"""
Motor principal de postulación.

Responsabilidades:
  - Orquestar el flujo completo: navegación -> deduplicación -> postulación -> logging
  - Aplicar rate limiting por portal para evitar detección
  - Reintentar ante errores transitorios (red, timeout)
  - Delegar a portales específicos (LinkedInPortal) o al motor genérico
  - Persistir resultados en SQLite y CSV

Flujo de alto nivel:
    run_bot(portal)
        L-- validar configuración
        L-- abrir browser con sesión persistente
        L-- navegar a url_busqueda
        L-- por cada página:
                L-- extraer offer_ids / offer_urls
                L-- por cada oferta:
                        L-- skip si ya en DB (deduplicación)
                        L-- rate_limiter.acquire()  <- bloquea si excede límite/hora
                        L-- with_retry(apply)       <- reintenta ante error de red
                        L-- save_application()
                        L-- _csv_log()
                L-- paginar si hay siguiente página
"""
import csv
import datetime
import json
import logging
import os
import random
import shutil
import socket
import sys
import time
from pathlib import Path

# El bot corre como subproceso separado (subprocess.Popen desde gui_server.py),
# por lo que no hay bucle asyncio activo al arrancar. ProactorEventLoop (default
# en Windows) es necesario para que Playwright pueda lanzar Chromium vía
# create_subprocess_exec. No se cambia la política.

log = logging.getLogger("applyjob.engine")

from playwright.sync_api import sync_playwright, Page

# ── Señal de parada desde gui_server ─────────────────────────────────────────
# gui_server.py escribe este archivo cuando el usuario hace clic en Detener.
# engine.py lo chequea en cada iteración del loop principal y cierra el browser
# limpiamente antes de salir — evita que el Chrome del usuario quede con tabs
# abiertas y que el proceso quede colgado en una espera de Playwright.
_STOP_SIGNAL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "STOP_SIGNAL"
)

def _should_stop() -> bool:
    """Retorna True si gui_server solicitó detener la ejecución."""
    return os.path.exists(_STOP_SIGNAL_PATH)

# ---------------------------------------------------------------------------
# Auto-detección del ejecutable de Chrome/Chromium
# ---------------------------------------------------------------------------
_CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Chromium\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]

def _find_chrome_executable() -> str | None:
    """Retorna la ruta al ejecutable de Chrome/Chromium si está en el sistema."""
    for path in _CHROME_CANDIDATES:
        if Path(path).exists():
            return path
    return None


_CDP_PORT = int(os.getenv("CDP_PORT", "9222"))


def _try_cdp_connect(pw, port: int = None):
    """Conecta a Chrome vía CDP si está disponible. Retorna (browser, ctx, page) o None."""
    port = port or _CDP_PORT
    try:
        # Verificar que el puerto está abierto
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.8)
        result = sock.connect_ex(("127.0.0.1", port))
        sock.close()
        if result != 0:
            return None

        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        # Usar el contexto existente del usuario (ya logueado)
        ctx  = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        log.info("[OK] CDP conectado al Chrome del usuario (puerto %d)", port)
        print(f"[CDP] Conectado al Chrome del usuario — sin perfil separado.")
        return browser, ctx, page
    except Exception as exc:
        log.debug("CDP no disponible (%s) — usando perfil separado.", exc)
        return None

from .config import SITE_CONFIG, USER_PROFILE, location_score, schedule_ok, experience_ok, practica_ok, topic_ok, topic_ok_it
from .state import already_applied, save_application
from .stealth_utils import (
    apply_stealth, human_delay, human_scroll, human_click,
    take_error_screenshot, random_user_agent, random_viewport,
    portal_action_delay, reading_pause, pre_form_pause,
    scroll_to_and_pause, human_type_field, human_click_element,
)
from .form_filler import fill_form
from .retry import with_retry, get_rate_limiter
from .validator import run_startup_validation

# log = logging.getLogger("applyjob.engine") (movido arriba)

BASE_DIR        = Path(__file__).parent.parent
SESSIONS_DIR    = BASE_DIR / "sessions"
LOGS_DIR        = BASE_DIR / "logs"
SCAN_QUEUE_PATH = BASE_DIR / "data" / "scan_queue.json"
QUICK_LINKS_PATH = BASE_DIR / "data" / "quick_links.json"
LOGS_DIR.mkdir(exist_ok=True)
SESSIONS_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Gestión de sesiones — solo se persisten si hubo al menos 1 postulación
# ---------------------------------------------------------------------------

def _discard_session(session_dir: str, portal_name: str) -> None:
    """Elimina el directorio de sesión (cookies, storage). Llamar cuando applied == 0."""
    try:
        if os.path.exists(session_dir):
            shutil.rmtree(session_dir, ignore_errors=True)
            print(f"[SESSION] ⚠ Sin postulaciones en {portal_name.upper()} — sesión descartada (privacidad).")
    except Exception as exc:
        log.debug("_discard_session error: %s", exc)

# Siempre descartar sesión al finalizar → siempre pedir login al próximo run.
# El usuario quiere control total del login en cada sesión.
MIN_APPLIES_TO_KEEP_SESSION = 1   # usado solo para logging de progreso

def _maybe_keep_session(session_dir: str, portal_name: str, applied: int) -> None:
    """Siempre descarta la sesión para que el próximo run pida login."""
    print(f"[SESSION] {portal_name.upper()} — sesion descartada. "
          f"La proxima vez pedira login ({applied} postulaciones en esta sesion).")
    _discard_session(session_dir, portal_name)


# ---------------------------------------------------------------------------
# Quick Links — todas las ofertas que pasan filtros, listas para postulación manual
# ---------------------------------------------------------------------------

def _save_quick_link(url: str, title: str, portal: str) -> None:
    """Guarda un link rápido en quick_links.json (sin duplicados)."""
    try:
        QUICK_LINKS_PATH.parent.mkdir(exist_ok=True)
        links: list = []
        if QUICK_LINKS_PATH.exists():
            try:
                links = json.load(open(QUICK_LINKS_PATH, encoding="utf-8"))
            except Exception:
                links = []
        # Deduplicar por URL
        if any(e.get("url") == url for e in links):
            return
        links.insert(0, {
            "url":       url,
            "title":     title,
            "portal":    portal,
            "saved_at":  _datetime_now(),
            "dismissed": False,
        })
        # Mantener máximo 100 links
        links = links[:100]
        with open(QUICK_LINKS_PATH, "w", encoding="utf-8") as f:
            json.dump(links, f, ensure_ascii=False, indent=2)
        log.info("  [quick-link] Guardado: %s | %s", portal, title[:60])
    except Exception as exc:
        log.debug("_save_quick_link error: %s", exc)


# ---------------------------------------------------------------------------
# Scan queue helpers — cola de ofertas pendientes de responder y aplicar
# ---------------------------------------------------------------------------

def _datetime_now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def _load_scan_queue() -> list:
    if not SCAN_QUEUE_PATH.exists():
        return []
    try:
        return json.load(open(SCAN_QUEUE_PATH, encoding="utf-8"))
    except Exception:
        return []


def _save_to_scan_queue(url: str, title: str, portal: str,
                        unanswered: list) -> None:
    existing_queue = _load_scan_queue()
    # Preservar failure_count si ya existía la entrada
    old_entry = next((e for e in existing_queue if e.get("url") == url), None)
    failure_count = old_entry.get("failure_count", 0) if old_entry else 0
    queue = [e for e in existing_queue if e.get("url") != url]
    queue.append({
        "url":           url,
        "title":         title,
        "portal":        portal,
        "unanswered":    unanswered,
        "scanned_at":    _datetime_now(),
        "failure_count": failure_count,
    })
    SCAN_QUEUE_PATH.parent.mkdir(exist_ok=True)
    with open(SCAN_QUEUE_PATH, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)


def _remove_from_scan_queue(url: str) -> None:
    queue = [e for e in _load_scan_queue() if e.get("url") != url]
    with open(SCAN_QUEUE_PATH, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)


def _increment_scan_queue_failure(url: str, max_failures: int = 2) -> bool:
    """
    Incrementa el contador de fallos de una entrada.
    Si alcanza max_failures → la elimina de la cola automáticamente.
    Retorna True si fue eliminada, False si solo se incrementó.
    """
    queue = _load_scan_queue()
    entry = next((e for e in queue if e.get("url") == url), None)
    if not entry:
        return False
    entry["failure_count"] = entry.get("failure_count", 0) + 1
    count = entry["failure_count"]
    if count >= max_failures:
        queue = [e for e in queue if e.get("url") != url]
        log.info("[PRUNE] Eliminada de cola tras %d fallos: %s", max_failures, url[:60])
        with open(SCAN_QUEUE_PATH, "w", encoding="utf-8") as f:
            json.dump(queue, f, ensure_ascii=False, indent=2)
        return True
    # Solo actualizar contador
    with open(SCAN_QUEUE_PATH, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)
    return False


def _prune_scan_queue(max_age_days: int = 5) -> int:
    """
    Elimina entradas de scan_queue más antiguas que max_age_days.
    Las ofertas vencen rápido — después de 5 días casi siempre están cerradas.
    Retorna la cantidad de entradas eliminadas.
    """
    import datetime as _dt
    queue = _load_scan_queue()
    cutoff = _dt.datetime.now() - _dt.timedelta(days=max_age_days)
    fresh   = []
    pruned  = 0
    for entry in queue:
        try:
            scanned = _dt.datetime.fromisoformat(entry.get("scanned_at", ""))
            if scanned < cutoff:
                pruned += 1
                log.info("[PRUNE] Expirada (>%dd): '%s'", max_age_days, entry.get("title", "")[:55])
                continue
        except Exception:
            pass  # Si no hay fecha → conservar
        fresh.append(entry)
    if pruned:
        with open(SCAN_QUEUE_PATH, "w", encoding="utf-8") as f:
            json.dump(fresh, f, ensure_ascii=False, indent=2)
        log.info("[PRUNE] %d entradas antiguas eliminadas de scan_queue.", pruned)
    return pruned


# ---------------------------------------------------------------------------
# CSV log (humano-legible, complementa el SQLite)
# ---------------------------------------------------------------------------

def _csv_log(portal: str, url: str, title: str, status: str, detail: str = "") -> None:
    """
    Escribe una fila en el CSV diario de postulaciones.

    El CSV es el log legible por humanos; la DB SQLite es para queries.
    Ambos se actualizan en cada postulación.

    Args:
        portal : nombre del portal
        url    : URL de la oferta
        title  : título del puesto
        status : resultado ("applied", "skipped_*", "error: ...")
        detail : información adicional opcional
    """
    today    = datetime.date.today().isoformat()
    log_file = LOGS_DIR / f"applied_{today}.csv"
    write_header = not log_file.exists()
    with open(log_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["timestamp", "portal", "title", "url", "status", "detail"])
        writer.writerow([
            datetime.datetime.now().isoformat(timespec="seconds"),
            portal, title, url, status, detail,
        ])


# ---------------------------------------------------------------------------
# Estrategias genéricas
# ---------------------------------------------------------------------------

def _apply_directa(page: Page, config: dict, profile: dict, job_title: str = "") -> str:
    """
    Estrategia para postulación directa: un click al botón y submit en la misma página.

    Flujo:
        click(selector_boton_aplicar) -> fill_form() -> click(submit)

    Args:
        page      : página de Playwright activa
        config    : SITE_CONFIG del portal
        profile   : USER_PROFILE (puede incluir _mode)
        job_title : título del puesto para detección contextual de perfil

    Returns:
        "applied" | "filled_no_submit" | "error: <mensaje>"
    """
    btn_sel = config["selector_boton_aplicar"]
    try:
        human_click(page, btn_sel)
        human_delay(1.2, 2.5)
        form_result = fill_form(page, profile, job_title=job_title)

        # Si quedaron preguntas sin respuesta -> NO enviar, saltar oferta
        if form_result.get("unanswered", 0) > 0:
            labels = form_result.get("unanswered_labels", [])
            log.warning("  [SKIP] Oferta saltada — %d pregunta(s) sin respuesta: %s",
                        len(labels), ", ".join(labels[:3]))
            print(f"  [SKIP] Postulacion cancelada — preguntas sin respuesta: {', '.join(labels[:3])}")
            return "skipped: preguntas_sin_respuesta"

        for submit_sel in [
            "button[type='submit']", "input[type='submit']",
            "button:has-text('Enviar')", "button:has-text('Submit')",
            "button:has-text('Apply')",  "button:has-text('Postular')",
        ]:
            try:
                if page.query_selector(submit_sel):
                    human_click(page, submit_sel)
                    human_delay(1.2, 2.0)
                    return "applied"
            except Exception:
                continue
        return "filled_no_submit"
    except Exception as exc:
        log.warning("_apply_directa falló: %s", exc)
        return f"error: {exc}"


def _apply_modal(page: Page, config: dict, profile: dict, job_title: str = "") -> str:
    """
    Estrategia para postulación modal: click abre un overlay,
    se llena dentro del modal y se avanza paso a paso.

    Flujo:
        click(selector_boton_aplicar) -> esperar modal -> fill_form() × N pasos -> submit

    Args:
        page      : página activa
        config    : SITE_CONFIG del portal
        profile   : USER_PROFILE (puede incluir _mode)
        job_title : título del puesto para detección contextual de perfil

    Returns:
        "applied" | "error: <mensaje>"
    """
    btn_sel = config["selector_boton_aplicar"]
    try:
        human_click(page, btn_sel)
        human_delay(1.2, 2.5)
        form_result = fill_form(page, profile, job_title=job_title)

        # Si quedaron preguntas sin respuesta en el primer paso -> cancelar
        if form_result.get("unanswered", 0) > 0:
            labels = form_result.get("unanswered_labels", [])
            log.warning("  [SKIP] Oferta saltada — %d pregunta(s) sin respuesta: %s",
                        len(labels), ", ".join(labels[:3]))
            print(f"  [SKIP] Postulacion cancelada — preguntas sin respuesta: {', '.join(labels[:3])}")
            return "skipped: preguntas_sin_respuesta"

        for _ in range(5):
            advanced = False
            for next_sel in [
                "button:has-text('Next')",      "button:has-text('Siguiente')",
                "button:has-text('Continue')",  "button:has-text('Continuar')",
                "button:has-text('Submit')",    "button:has-text('Enviar')",
                "button:has-text('Apply')",     "button:has-text('Postular')",
                "button[aria-label='Submit application']",
            ]:
                try:
                    btn = page.query_selector(next_sel)
                    if btn and btn.is_visible():
                        btn.click()
                        human_delay(1.0, 2.0)
                        step_result = fill_form(page, profile, job_title=job_title)
                        # Si en un paso intermedio hay preguntas sin respuesta -> cancelar
                        if step_result.get("unanswered", 0) > 0:
                            labels = step_result.get("unanswered_labels", [])
                            log.warning("  [SKIP] Paso intermedio con preguntas sin respuesta: %s",
                                        ", ".join(labels[:3]))
                            print(f"  [SKIP] Postulacion cancelada en paso intermedio: {', '.join(labels[:3])}")
                            return "skipped: preguntas_sin_respuesta"
                        advanced = True
                        break
                except Exception as exc:
                    log.debug("Boton '%s' no disponible: %s", next_sel, exc)
                    continue
            if not advanced:
                break
        return "applied"
    except Exception as exc:
        log.warning("_apply_modal fallo: %s", exc)
        return f"error: {exc}"


def _apply_externa(page: Page, config: dict, profile: dict = None) -> str:
    """
    Estrategia para postulación externa: el botón puede abrir nueva pestaña
    o navegar en la misma página hacia el formulario externo.

    Flujo:
      1. Si el botón tiene target="_blank" → expect_page (15 s)
      2. Si falla o no hay target → click normal + esperar navegación (20 s)
      3. En cualquier caso, intentar fill_form en la página resultante
      4. Volver a la página original

    Returns:
        "applied" | "external: <url>" | "error_externa: <mensaje>"
    """
    btn_sel = config["selector_boton_aplicar"]
    original_url = page.url
    try:
        btn = page.query_selector(btn_sel)
        if not btn or not btn.is_visible():
            return "skipped: no_button"

        target = (btn.get_attribute("target") or "").strip()
        opens_tab = target == "_blank"

        if opens_tab:
            # Intento con nueva pestaña (timeout reducido a 12 s)
            try:
                with page.context.expect_page(timeout=12_000) as np_info:
                    btn.click()
                new_page = np_info.value
                new_page.wait_for_load_state("domcontentloaded", timeout=15_000)
                external_url = new_page.url
                log.debug("Externa nueva pestaña: %s", external_url)
                # Intentar llenar formulario si hay campos
                if profile:
                    try:
                        fill_form(new_page, profile)
                    except Exception:
                        pass
                new_page.close()
                return f"external: {external_url}"
            except Exception as tab_exc:
                log.debug("expect_page falló (%s) — intentando misma pestaña", tab_exc)

        # Navegar en la misma página
        btn.click()
        try:
            page.wait_for_load_state("domcontentloaded", timeout=20_000)
        except Exception:
            pass
        human_delay(0.8, 1.5)
        nav_url = page.url

        # Si navegó a una nueva URL, intentar llenar formulario
        if nav_url != original_url:
            log.debug("Externa misma pestaña: %s", nav_url)
            # Detectar si navegó a login/authwall → no es una aplicación real
            _login_redirects = ["/login", "/authwall", "/signin", "/uas/", "/checkpoint"]
            if any(p in nav_url.lower() for p in _login_redirects):
                log.info("_apply_externa: redirigido a login: %s", nav_url[:60])
                try:
                    page.goto(original_url, wait_until="domcontentloaded", timeout=15_000)
                except Exception:
                    pass
                return "skipped: login_required"
            if profile:
                try:
                    fill_form(page, profile)
                except Exception:
                    pass
            # Volver a la página original
            try:
                page.go_back(wait_until="domcontentloaded", timeout=10_000)
            except Exception:
                try:
                    page.goto(original_url, wait_until="domcontentloaded", timeout=15_000)
                except Exception:
                    pass
            return f"external: {nav_url}"

        # Sin navegación — verificar si apareció un modal/overlay tras el click
        try:
            for modal_sel in [
                "div[role='dialog']", "[class*='modal'][style*='display']",
                "[class*='overlay']", "iframe[src*='apply']",
                "form[action*='apply']",
            ]:
                el = page.query_selector(modal_sel)
                if el and el.is_visible():
                    log.info("_apply_externa: modal detectado tras click (sin navegación)")
                    return "skipped: apply_via_modal"
        except Exception:
            pass

        # Intentar botón alternativo (a veces el selector principal no es el real)
        _ALT_BTN_SELS = [
            "a[href*='apply']:visible", "a[href*='postular']:visible",
            "button:has-text('Postular')", "button:has-text('Apply Now')",
            "a:has-text('Aplicar')", "a:has-text('Postular ahora')",
        ]
        for alt_sel in _ALT_BTN_SELS:
            try:
                alt_btn = page.query_selector(alt_sel)
                if alt_btn and alt_btn.is_visible():
                    alt_href = alt_btn.get_attribute("href") or ""
                    if alt_href and alt_href not in (original_url, "#", "javascript:void(0)"):
                        if not alt_href.startswith("http"):
                            base = "/".join(original_url.split("/")[:3])
                            alt_href = base + alt_href
                        log.info("_apply_externa: URL alternativa encontrada: %s", alt_href[:60])
                        return f"external: {alt_href}"
            except Exception:
                continue

        log.info("_apply_externa: sin navegación ni modal en %s", original_url[:60])
        return "skipped: no_navigation"
    except Exception as exc:
        log.warning("_apply_externa falló: %s", exc)
        return f"error_externa: {str(exc)[:80]}"


# ---------------------------------------------------------------------------
# Proceso genérico de una oferta
# ---------------------------------------------------------------------------

def _process_offer_generic(
    page: Page, offer_url: str, config: dict, profile: dict,
    portal: str, dry_run: bool,
) -> tuple[str, str]:
    """
    Navega a una URL de oferta y aplica la estrategia correspondiente al portal.

    Args:
        page     : página activa
        offer_url: URL de la oferta individual
        config   : SITE_CONFIG del portal
        profile  : USER_PROFILE
        portal   : nombre del portal para logs y capturas de pantalla
        dry_run  : si True, no postula realmente

    Returns:
        Tuple (title, status)
    """
    title = "unknown"
    try:
        def _navigate():
            page.goto(offer_url, wait_until="domcontentloaded", timeout=30_000)

        with_retry(_navigate, attempts=2, delay=3.0, portal=portal)
        human_delay(1.0, 2.0)

        # Detectar página de error (404, oferta eliminada, expirada)
        cur_url = page.url
        page_text = (page.text_content("body") or "").lower()
        _error_signals = [
            "/404", "404", "no encontramos", "no encontrada", "not found",
            "página no existe", "oferta no disponible", "oferta eliminada",
            "this job is no longer", "ya no está disponible", "expiró",
        ]
        if any(s in cur_url.lower() or s in page_text for s in _error_signals):
            log.warning("  [SKIP] Oferta no disponible (404/eliminada): %s", offer_url)
            print(f"  [FILTRO] Oferta eliminada o expirada — saltando: {offer_url}")
            return title, "skipped: oferta_eliminada"

        human_scroll(page, steps=1)

        title_sel = config.get("selector_titulo_oferta")
        if title_sel:
            try:
                title = (page.text_content(title_sel, timeout=3_000) or "").strip()[:80]
            except Exception as exc:
                log.debug("No se pudo leer título con '%s': %s", title_sel, exc)

        # Segundo chequeo usando el titulo real (practica + rubro no-IT)
        if not practica_ok(title) or not practica_ok(page_text[:300]):
            log.info("  [FILTRO/PRACTICA] Oferta descartada por practica en titulo: '%s'", title)
            print(f"  [FILTRO] Practica/pasantia detectada en pagina — saltando: {title}")
            return title, "skipped: practica"
        if not topic_ok(title):
            log.info("  [FILTRO/TOPIC] Oferta descartada por rubro no-IT en titulo: '%s'", title)
            print(f"  [FILTRO] Rubro no-IT detectado en pagina — saltando: {title}")
            return title, "skipped: rubro_no_it"

        if dry_run:
            log.info("  [dry_run] Llenando formulario para verificación visual...")
            tipo = config.get("tipo_postulacion", "directa")
            if tipo == "directa":
                # En dry_run, solo clickeamos el primer botón y llenamos, sin hacer submit
                try:
                    btn_sel = config.get("selector_boton_aplicar")
                    if btn_sel:
                        page.click(btn_sel)
                        human_delay(1.2, 2.0)
                        fill_form(page, profile, job_title=title)
                        human_delay(3.0, 5.0)
                except Exception as e:
                    log.debug("Error en dry_run fill: %s", e)
            return title, "dry_run"

        tipo = config.get("tipo_postulacion", "directa")
        if tipo == "directa":
            status = _apply_directa(page, config, profile, job_title=title)
        elif tipo == "modal":
            status = _apply_modal(page, config, profile, job_title=title)
        elif tipo == "externa":
            status = _apply_externa(page, config, profile=profile)
        else:
            status = f"unknown_type:{tipo}"

        return title, status

    except Exception as exc:
        screenshot = take_error_screenshot(page, portal, "offer_error")
        log.error("Error procesando '%s': %s | screenshot: %s", offer_url, exc, screenshot,
                  exc_info=True)
        return title, f"error: {exc}"


# ---------------------------------------------------------------------------
# Detección de login pendiente
# ---------------------------------------------------------------------------

# Selectores que indican que el portal pide login
_LOGIN_SIGNALS = {
    "linkedin": [
        "div.nav__button-secondary",
        "button[data-tracking-control-name='guest_homepage-basic_sign-in-button']",
        ".sign-in-form",
        "#session_key",
        # NO incluir "a[href*='/login']" — aparece en footer incluso logueado
    ],
    "laborum": [
        "#ingresarNavBar",
        "button:has-text('Ingresar')",
        "input#email",
        # NOTA: NO incluir "a[href*='/login']" — aparece en footer incluso estando logueado
    ],
    "indeed": [
        "a[href*='/account/login']",
        "button:has-text('Iniciar sesión')",
        "button:has-text('Sign in')",
        "div.desktop-sign-in-button",
        "a[data-gnav-element-name='SignIn']",
    ],
    "chiletrabajos": [
        "a:has-text('Ingresa a tu cuenta')",
        "a[href*='/login']",
        "a[href*='/ingresar']",
        "button:has-text('Ingresar')",
        "input[name='email']",
        "input[type='password']",
    ],
    "computrabajo": [
        # Solo señales de login que NO aparecen en footer cuando logueado
        "input[name='email'][placeholder*='mail']",
        "input[type='password']",
        "form[action*='login']",
        "form[action*='iniciar']",
    ],
    "getonyboard": [
        # Botón "Ingresa" — puede ser <a>, <button> o componente React
        "a:has-text('Ingresa')",
        "button:has-text('Ingresa')",
        "[class*='sign-in']:has-text('Ingresa')",
        "nav a:has-text('Ingresa')",
        "header a:has-text('Ingresa')",
        "a[href*='/auth/sign_in']",
        "a[href*='/auth/sign']",
        "a[href*='/login']",
        "input[name='user[email]']",
        "input[type='password']",
    ],
}

# Selectores que confirman que ya hay sesión activa
_LOGGED_IN_SIGNALS = {
    "chiletrabajos": [
        # Avatar / menú del usuario logueado en el header
        "a[href*='/candidato/perfil']",
        "a[href*='/mi-cuenta']",
        "a[href*='/postulaciones']",
        "a:has-text('Mi cuenta')",
        "a:has-text('Mis postulaciones')",
        "a:has-text('Mi perfil')",
        "div.user-menu, ul.user-menu",
        "span.username, span[class*='username']",
        # El botón "Ingresa a tu cuenta" DESAPARECE cuando hay sesión
        # — se verifica por ausencia en _is_logged_in con lógica extra
    ],
    "computrabajo": [
        # Nav del usuario logueado — visible en candidato.cl.computrabajo.com
        "a:has-text('Mi área')",
        "a:has-text('Mi currículum')",
        "a:has-text('Mis postulaciones')",
        "a:has-text('Mis alertas')",
        "a[href*='/candidato/']",
        "a[href*='/mis-postulaciones']",
        "a[href*='/mi-curriculum']",
    ],
    "linkedin": [
        ".global-nav__me-photo",
        "img.global-nav__me-photo",
        "[data-control-name='nav.settings']",
        ".feed-identity-module",
    ],
    "laborum": [
        # Selectores robustos para estado logueado en Laborum
        "[data-testid='header-user-menu']",
        "button:has-text('Mi Perfil')",
        "a[href*='/postulante/']",
        "img[class*='Avatar']",
        # Elementos visibles en el dashboard tras login
        "a:has-text('Ir a mis postulaciones')",
        "a:has-text('Ir a mi CV')",
        "a[href*='/postulante/cv']",
        "a[href*='/postulante/perfil']",
        "a[href*='/mi-perfil']",
        "[class*='userMenu']",
        "[class*='UserMenu']",
        "[class*='user-menu']",
        # Avatar genérico en nav (círculo del usuario top-right)
        "nav img[src*='avatar'], nav img[src*='profile'], nav img[src*='user']",
        "header [class*='avatar'], header [class*='Avatar']",
    ],
    "indeed": [
        "a[data-gnav-element-name='Account']",
        "div[data-testid='UserDropdown']",
        "button[aria-label*='cuenta']",
        "img[class*='avatarImage']",
        "#IA_AccountHamburger",
        "a[href*='/myjobs']",
    ],
    "getonyboard": [
        # Nav logueado en GetOnBoard — avatar o link al perfil del usuario
        "a[href*='/workers/me']",
        "a[href*='/profile']",
        "img[class*='avatar']",
        "img[class*='Avatar']",
        "div[class*='user-menu']",
        "div[class*='UserMenu']",
        "a:has-text('Mi perfil')",
        "a:has-text('Mis postulaciones')",
        "a:has-text('Ver perfil')",
    ],
}

# URLs de login por portal
_LOGIN_URLS = {
    "linkedin":      "https://www.linkedin.com/login",
    "laborum":       "https://www.laborum.cl/login",
    "indeed":        "https://cl.indeed.com/account/login",
    "chiletrabajos": "https://www.chiletrabajos.cl/candidato/login",
    "computrabajo":  "https://cl.computrabajo.com/candidato/login",
    "getonyboard":   "https://www.getonbrd.com/auth/sign_in",
}


def _wait_for_login_if_needed(page, portal_name: str, config: dict) -> None:
    """
    1. Si hay Cloudflare -> espera a que el usuario lo resuelva (imprime [CAPTCHA]).
    2. Si hay pantalla de login -> espera a que el usuario inicie sesión ([LOGIN_REQUERIDO]).
    3. Si ya hay sesión activa -> retorna inmediatamente.
    """
    if not config.get("requires_login"):
        return

    login_sels   = _LOGIN_SIGNALS.get(portal_name, [])
    session_sels = _LOGGED_IN_SIGNALS.get(portal_name, [])

    def _is_cloudflare() -> bool:
        try:
            title = page.title().lower()
            url   = page.url
            body  = page.evaluate("document.body?.innerText || ''") or ""
            return (
                "just a moment" in title or
                "un momento"    in title or
                "cf_chl"        in url   or
                "verifique que es un ser humano" in body.lower() or
                "verificación adicional"         in body.lower() or
                ("cloudflare" in body.lower() and len(body) < 3000)
            )
        except Exception:
            return False

    def _is_logged_in() -> bool:
        # 1. Selectores positivos de sesión activa
        for sel in session_sels:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    return True
            except Exception:
                pass
        # 2. Indeed: URL de jobs sin muro de login
        if portal_name == "indeed":
            try:
                cur = page.url
                if (any(k in cur for k in ("/jobs", "/myjobs", "/resume"))
                        and "login" not in cur and "authwall" not in cur
                        and "cf_chl" not in cur):
                    return True
            except Exception:
                pass
        # 3. Laborum: lógica combinada más robusta
        if portal_name == "laborum":
            try:
                cur = page.url
                # URL de área privada → siempre logueado
                if any(x in cur for x in ("/postulante/", "/mis-postulaciones", "/mi-perfil")):
                    return True
                # URL laborum.cl fuera del login con ausencia de botón Ingresar VISIBLE
                if "laborum.cl" in cur and "login" not in cur:
                    ingresar = page.query_selector("#ingresarNavBar")
                    if ingresar and ingresar.is_visible():
                        return False   # botón Ingresar visible → no logueado
                    # Verificar también texto del botón header
                    btn_ingresar = page.query_selector("button:has-text('Ingresar')")
                    if btn_ingresar and btn_ingresar.is_visible():
                        # Solo cuenta si está en el header/nav (no en footer)
                        try:
                            in_nav = page.evaluate(
                                "el => el.closest('nav,header') !== null",
                                btn_ingresar
                            )
                            if in_nav:
                                return False
                        except Exception:
                            pass
                    # Sin botón Ingresar visible en header → asumir logueado
                    return True
            except Exception:
                pass
        # 4. LinkedIn: URL feed o mynetwork → logueado
        if portal_name == "linkedin":
            try:
                cur = page.url
                if any(x in cur for x in ("/feed", "/mynetwork", "/jobs", "/in/")):
                    return True
            except Exception:
                pass
        # 5. ChileTrabajos: detectar por presencia de nav del candidato logueado
        if portal_name == "chiletrabajos":
            try:
                cur = page.url
                if "chiletrabajos.cl" in cur and "login" not in cur and "ingresar" not in cur:
                    # Primero: señales POSITIVAS de sesión activa
                    for pos_sel in [
                        "a:has-text('Mis postulaciones')",
                        "a:has-text('Mi cuenta')",
                        "a:has-text('Mi perfil')",
                        "a[href*='/postulaciones']",
                        "a[href*='/candidato/']",
                        "a[href*='/mi-cuenta']",
                    ]:
                        try:
                            el = page.query_selector(pos_sel)
                            if el and el.is_visible():
                                return True
                        except Exception:
                            pass
                    # Señal NEGATIVA: botón de ingreso todavía visible → no logueado
                    btn = page.query_selector("a:has-text('Ingresa a tu cuenta')")
                    if btn and btn.is_visible():
                        return False
                    # Sin señales claras → asumir logueado (evitar falsos negativos)
                    return True
            except Exception:
                pass
        # 6. Computrabajo: detectar por presencia de nav del área privada
        # OJO: el dominio es computrabajo.com (no .cl) — cl. es subdomain
        if portal_name == "computrabajo":
            try:
                cur = page.url
                if "computrabajo.com" in cur and "login" not in cur and "iniciar-sesion" not in cur:
                    # Señales POSITIVAS: nav del candidato logueado
                    for pos_sel in [
                        "a:has-text('Mi área')",
                        "a:has-text('Mi currículum')",
                        "a:has-text('Mis postulaciones')",
                        "a[href*='/candidato/']",
                    ]:
                        try:
                            el = page.query_selector(pos_sel)
                            if el and el.is_visible():
                                return True
                        except Exception:
                            pass
                    # Si ninguna señal positiva → no logueado
                    return False
            except Exception:
                pass
        return False

    def _needs_login() -> bool:
        if _is_logged_in():
            return False
        for sel in login_sels:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    return True
            except Exception:
                pass
        cur = page.url
        if "login" in cur or "authwall" in cur or "checkpoint" in cur:
            return True
        return False

    # -- Paso 1: resolver Cloudflare si está presente --------------------------
    if _is_cloudflare():
        print(f"\n[CAPTCHA] Cloudflare detectado en {portal_name.upper()}. Resuelve el desafio en el navegador.")
        log.warning("Cloudflare detectado — esperando resolución manual (max 3 min)...")
        deadline_cf = time.time() + 180
        last_cf_log = 0
        while time.time() < deadline_cf:
            time.sleep(2)
            if not _is_cloudflare():
                log.info("Cloudflare resuelto. Continuando.")
                print(f"\n[SESION_INICIADA] Cloudflare resuelto en {portal_name.upper()}.")
                human_delay(2.0, 3.0)
                break
            if time.time() - last_cf_log > 20:
                print(f"[CAPTCHA] Esperando resolución del desafío en {portal_name.upper()}...")
                last_cf_log = time.time()
        else:
            log.error("Tiempo agotado esperando Cloudflare.")
            return

    # -- Paso 2: esperar a que la página termine de cargar antes de verificar ---
    # Portales con requires_login necesitan que el DOM esté completo para
    # detectar correctamente si el botón de login o el avatar del usuario aparece.
    _PORTALS_FORCE_LOGIN = ("indeed", "laborum", "linkedin", "chiletrabajos", "computrabajo", "getonyboard")
    try:
        page.wait_for_load_state("domcontentloaded", timeout=5_000)
        page.wait_for_function("() => document.readyState === 'complete'", timeout=5_000)
    except Exception:
        pass

    # -- Paso 3: verificar si ya hay sesión activa -----------------------------
    if _is_logged_in():
        log.info("Sesión activa confirmada en %s.", portal_name)
        return

    # -- Paso 4: verificar si realmente necesita login -------------------------
    # Forzar verificación profunda en portales que siempre requieren cuenta
    needs = _needs_login()
    if not needs and portal_name not in _PORTALS_FORCE_LOGIN:
        return
    if not needs:
        # Esperar un poco más — la página puede seguir cargando elementos del header
        try:
            page.wait_for_timeout(2000)  # 2s para que React/SPA hidrate el nav
        except Exception:
            pass

        if _is_logged_in():
            return
        if not _needs_login():
            if portal_name not in _PORTALS_FORCE_LOGIN:
                # Sin señales claras y portal no forzado -> continuar
                log.info("Sin señales claras de login en %s — continuando.", portal_name)
                return
            # _PORTALS_FORCE_LOGIN: sin señales positivas NI negativas → ambiguo.
            # Para portales que SIEMPRE requieren cuenta (getonyboard, linkedin, etc.),
            # el silencio no es seguro — tratar como login requerido y esperar.
            log.warning("[SESION] %s: sin señales de sesión activa — asumiendo login necesario.",
                        portal_name)
            print(f"\n[SESION_CHECK] {portal_name.upper()}: no se detectó sesión activa. "
                  "Esperando login en el navegador...")

    # -- Hay que hacer login ---------------------------------------------------
    log.warning("LOGIN REQUERIDO en %s", portal_name)
    print(f"\n[LOGIN_REQUERIDO] {portal_name.upper()} — Inicia sesión en el navegador abierto.")

    # Navegar a login solo si la URL actual no es ya de login
    cur = page.url
    if portal_name in _LOGIN_URLS and "login" not in cur and "authwall" not in cur:
        try:
            page.goto(_LOGIN_URLS[portal_name], wait_until="domcontentloaded", timeout=15_000)
            human_delay(1.0, 2.0)
        except Exception as exc:
            log.debug("No se pudo navegar a login page: %s", exc)

    # --- Intento de Auto-Login si hay credenciales ---
    if portal_name == "laborum":
        email = USER_PROFILE.get("laborum_email")
        password = USER_PROFILE.get("laborum_password")
        if email and password:
            log.info("Intentando auto-login en Laborum...")
            try:
                page.fill("input#email", email)
                human_delay(0.5, 1.0)
                page.fill("input#password", password)
                human_delay(0.5, 1.2)
                page.click("button#ingresar")
                human_delay(3.0, 5.0)
            except Exception as exc:
                log.warning("Fallo auto-login Laborum: %s", exc)

    # Esperar hasta 10 minutos a que el usuario inicie sesión
    deadline  = time.time() + 600
    last_log  = 0
    prev_url  = page.url   # para detectar transición de URL login → no-login

    while time.time() < deadline:
        current_time = time.time()
        if current_time - last_log > 20:
            print(f"\n[LOGIN_REQUERIDO] Esperando login en {portal_name.upper()}. "
                  "Inicia sesión en el navegador abierto.")
            last_log = current_time

        time.sleep(3)
        try:
            cur = page.url

            # ── Detección por transición de URL (más rápida que selectores) ──
            # Si el bot estaba en la página de login y ahora NO lo está → logueado
            login_keywords = ("login", "authwall", "signin", "checkpoint", "uas/login")
            was_on_login = any(k in prev_url.lower() for k in login_keywords)
            now_off_login = not any(k in cur.lower() for k in login_keywords)
            if was_on_login and now_off_login and portal_name.split(".")[0] in cur.lower().replace("www.", ""):
                # Pequeña pausa para que React termine de hidratarse
                time.sleep(2.5)
                log.info("Transición URL login→no-login detectada. Asumiendo sesión activa.")
                print(f"\n[SESION_INICIADA] Login detectado en {portal_name.upper()} (cambio de URL).")
                try:
                    page.goto(config["url_busqueda"], wait_until="domcontentloaded", timeout=30_000)
                except Exception:
                    pass
                human_delay(2.0, 3.0)
                return

            prev_url = cur

            # ── Detección por selectores ─────────────────────────────────────
            if _is_logged_in():
                log.info("Sesión detectada por selector. Continuando...")
                print(f"\n[SESION_INICIADA] Login detectado en {portal_name.upper()}. Continuando.")
                try:
                    if config["url_busqueda"] not in cur:
                        page.goto(config["url_busqueda"], wait_until="domcontentloaded", timeout=30_000)
                except Exception:
                    pass
                human_delay(2.0, 3.0)
                return

        except Exception as exc:
            log.debug("Error verificando login: %s", exc)

    log.error("Tiempo de espera agotado (10 min). Abortando portal.")
    print(f"\n[FALLO] Tiempo de espera agotado en {portal_name.upper()}. "
          "Usa Detener y vuelve a intentarlo cuando tengas sesión abierta.")
    raise TimeoutError("Login no completado en el tiempo límite")


# ---------------------------------------------------------------------------
# run_bot — función principal pública
# ---------------------------------------------------------------------------

def _run_keyword_loop(
    page, browser, portal_name: str, config: dict, profile: dict,
    max_offers: int, dry_run: bool, rate_limiter, portal_handler,
    using_cdp: bool = False,
    session_verified: bool = False,
    deadline: float = 0.0,
) -> tuple[int, list[str]]:
    """
    Navega a config['url_busqueda'], extrae ofertas y postula.
    Reutilizable en run_bot (keyword única) y run_bot_multi_keywords (browser compartido).

    Retorna (aplicadas: int, títulos_vistos: list[str]).
    Los títulos vistos permiten al llamador extraer nuevas keywords dinámicamente.

    session_verified=True indica que el login ya fue verificado en esta ejecución
    (evita navegar a la home en cada keyword del loop multi-keyword).
    """
    # -- Pre-flight: verificar sesión en la HOME antes de ir a búsqueda -------
    # Solo corre UNA VEZ por portal (session_verified=False en la primera keyword).
    # Para portales que requieren login, navegar primero a la home permite que
    # las cookies carguen correctamente y que _wait_for_login_if_needed detecte
    # el estado real de sesión antes de entrar al listado de ofertas.
    _HOME_URLS = {
        # Ir directo al área del candidato — ya tiene señales de sesión claras
        "chiletrabajos": "https://www.chiletrabajos.cl",
        "computrabajo":  "https://candidato.cl.computrabajo.com",
        "laborum":       "https://www.laborum.cl",
        "indeed":        "https://cl.indeed.com",
        "getonyboard":   "https://www.getonbrd.com",
        "linkedin":      "https://www.linkedin.com/feed",  # pre-flight antes de buscar
    }
    if not session_verified and config.get("requires_login") and portal_name in _HOME_URLS:
        home_url = _HOME_URLS[portal_name]
        log.info("[PRE-FLIGHT] Verificando sesión en home de %s: %s", portal_name, home_url)
        print(f"\n[PRE-FLIGHT] Verificando sesión en {portal_name.upper()} antes de buscar...")
        try:
            page.goto(home_url, wait_until="domcontentloaded", timeout=20_000)
            human_delay(1.5, 2.5)
            _wait_for_login_if_needed(page, portal_name, config)
        except Exception as pf_err:
            log.warning("[PRE-FLIGHT] Error verificando sesión en home: %s", pf_err)

    log.info("Navegando a: %s", config["url_busqueda"])
    try:
        with_retry(
            lambda: page.goto(config["url_busqueda"], wait_until="domcontentloaded", timeout=30_000),
            attempts=2, delay=5.0, portal=portal_name,
        )
    except Exception as nav_err:
        log.error("No se pudo navegar a la URL de búsqueda: %s", nav_err)
        return 0

    human_delay(1.5, 2.5)
    # Segunda verificación sobre la URL de búsqueda (puede redirigir a login)
    _wait_for_login_if_needed(page, portal_name, config)

    applied      = 0   # postulaciones REALES (applied / external / filled)
    visited      = 0   # ofertas visitadas en total (safety cap para evitar bucle infinito)
    _seen_titles: list[str] = []   # títulos de ofertas vistas — para extracción dinámica de keywords
    _MAX_VISITS = max_offers * 6  # nunca visitar más de 6× la cuota — por si todo es skip
    # Statuses que cuentan como postulación real (definido una vez aquí para ambos paths)
    _REAL_APPLY = {"applied", "filled_no_submit", "external_apply", "dry_run"}
    page_num = 1
    current_listing_url = page.url

    while applied < max_offers and visited < _MAX_VISITS:

        # ── Señal de parada: el usuario hizo clic en Detener ──────────────
        if _should_stop():
            print(f"\n[STOP] Señal de parada detectada. Saliendo del loop de paginas.")
            sys.exit(0)

        # ── Límite de tiempo por keyword ──────────────────────────────────
        if deadline and time.time() > deadline:
            print(f"\n[TIEMPO_KW] Tiempo por keyword agotado ({applied} postuladas). Siguiente keyword.")
            break

        log.info("--- Página %d | aplicadas: %d/%d | rate: %d/%d restantes ---",
                 page_num, applied, max_offers,
                 rate_limiter.current_count, rate_limiter.max_actions)

        # Scroll con recuperación ante TargetClosedError
        try:
            human_scroll(page, steps=2)
        except Exception as scroll_err:
            log.warning("Scroll falló (%s). Intentando recuperar página...", scroll_err)
            recovered = False
            # Intento 1: reutilizar una página viva del browser
            try:
                live = [p for p in browser.pages if not p.is_closed()]
                if live:
                    page = live[-1]
                    human_delay(1.5, 2.5)
                    recovered = True
            except Exception:
                pass
            # Intento 2: abrir página nueva y re-navegar a listing
            if not recovered:
                try:
                    page = (browser.new_page() if not using_cdp
                            else browser.contexts[0].new_page())
                    if not using_cdp:
                        apply_stealth(page)
                    page.goto(config["url_busqueda"],
                              wait_until="domcontentloaded", timeout=25_000)
                    human_delay(3.0, 5.0)
                    _wait_for_login_if_needed(page, portal_name, config)
                    recovered = True
                    log.info("Página recreada y re-navegada a URL de búsqueda.")
                except Exception as rec_err:
                    log.error("No se pudo recuperar la página: %s", rec_err)
            if not recovered:
                log.error("Recuperación fallida. Abortando búsqueda.")
                break
            continue

        human_delay(0.8, 1.5)

        # -- Portal específico -------------------------------------------------
        if portal_handler:
            current_listing_url = page.url
            print(f"  [BUSCANDO] Buscando ofertas en {portal_name.upper()}...")
            try:
                offer_ids = portal_handler.get_offer_urls(page)
            except Exception as gou_err:
                # TargetClosedError u otro error crítico en get_offer_urls
                log.warning("get_offer_urls falló (%s). Intentando recuperar página...", gou_err)
                recovered = False
                try:
                    live = [p for p in browser.pages if not p.is_closed()]
                    if live:
                        page = live[-1]
                        human_delay(2.0, 3.0)
                        recovered = True
                except Exception:
                    pass
                if not recovered:
                    try:
                        page = (browser.new_page() if not using_cdp
                                else browser.contexts[0].new_page())
                        if not using_cdp:
                            apply_stealth(page)
                        page.goto(config["url_busqueda"],
                                  wait_until="domcontentloaded", timeout=25_000)
                        human_delay(3.0, 5.0)
                        _wait_for_login_if_needed(page, portal_name, config)
                        recovered = True
                    except Exception as rec_err:
                        log.error("No se pudo recuperar tras get_offer_urls: %s", rec_err)
                if not recovered:
                    break
                offer_ids = []  # break en la siguiente iteración

            if not offer_ids:
                log.warning("Sin ofertas detectadas con selector: %s", config["selector_oferta"])
                take_error_screenshot(page, portal_name, "no_offers")
                break

            log.info("  [OK] Ofertas encontradas: %d", len(offer_ids))

            for offer_id in offer_ids:
                if _should_stop():
                    print(f"\n[STOP] Señal de parada detectada entre ofertas. Saliendo.")
                    sys.exit(0)

                if deadline and time.time() > deadline:
                    print(f"\n[TIEMPO_KW] Tiempo por keyword agotado. Siguiente keyword.")
                    break

                if applied >= max_offers or visited >= _MAX_VISITS:
                    break

                offer_url = (portal_handler.get_job_url(page, offer_id)
                             if hasattr(portal_handler, "get_job_url")
                             else offer_id)

                if already_applied(offer_url):
                    log.debug("  [skip-db] %s", offer_url)
                    continue

                # Recuperar página si fue cerrada
                try:
                    _ = page.url
                except Exception:
                    log.warning("Página cerrada inesperadamente. Recreando...")
                    try:
                        page = browser.new_page()
                        apply_stealth(page)
                        try:
                            from playwright_stealth import Stealth
                            Stealth().apply_stealth_sync(page)
                        except Exception:
                            pass
                    except Exception as page_err:
                        log.error("No se pudo recrear la página: %s", page_err)
                        break

                log.info("  [ABRIENDO] Oferta %s...", offer_id)
                start_time = time.time()
                result = portal_handler.apply_to_offer(page, offer_id)
                elapsed = time.time() - start_time
                status, title = result if isinstance(result, tuple) else (result, "")

                visited += 1  # siempre cuenta visita
                # Recolectar título para extracción dinámica de keywords
                if title and title not in ("unknown", ""):
                    _seen_titles.append(title)

                if status == "applied":
                    print(f"  [ÉXITO] Postulación completada para: {title or offer_id}")
                elif status == "error: linkedin_blocked":
                    print(f"\n⛔  LinkedIn bloqueó el bot. Deteniendo LinkedIn por esta sesión.")
                    log.warning("LinkedIn bloqueado — abortando loop de LinkedIn.")
                    save_application(offer_url, portal_name, title, status)
                    _csv_log(portal_name, offer_url, title, status)
                    return applied, _seen_titles  # ← salir INMEDIATAMENTE del loop
                elif status.startswith("error"):
                    print(f"  [FALLO] Error en {title or offer_id}: {status}")

                log.info("  [OK] [%s] %s -> %s (Tiempo: %.1fs)", portal_name, title or offer_id, status, elapsed)
                save_application(offer_url, portal_name, title, status)
                _csv_log(portal_name, offer_url, title, status)

                # Guardar como quick link (todos los que pasan filtros)
                if title and title != "unknown":
                    _save_quick_link(offer_url, title, portal_name)

                # Contar solo postulaciones REALES (no skips ni errores)
                is_real = status in _REAL_APPLY or (isinstance(status, str) and status.startswith("external:"))
                if is_real:
                    applied += 1

                print(f"  [PROGRESO] Aplicadas {applied}/{max_offers} en {portal_name.upper()}")

                if status in {"applied", "filled_no_submit", "dry_run"}:
                    rate_limiter.acquire(portal_name)

                # LinkedIn necesita delays más largos para evitar detección
                if portal_name == "linkedin":
                    import random as _rnd
                    _delay = _rnd.uniform(8.0, 15.0)
                    log.debug("  [linkedin] Pausa anti-bot: %.1fs", _delay)
                    time.sleep(_delay)
                else:
                    human_delay(1.0, 2.0)

        # -- Motor genérico ----------------------------------------------------
        else:
            elements = page.query_selector_all(config["selector_oferta"])
            if not elements:
                log.warning("Sin ofertas detectadas con selector: %s", config["selector_oferta"])
                take_error_screenshot(page, portal_name, "no_offers")
                break

            log.info("Ofertas en página: %d", len(elements))
            print(f"  [BUSCANDO] Detectadas {len(elements)} ofertas en página {page_num}")

            # -- Extraer URLs + score por ubicación (Santiago RM — Maipú primeras) --
            loc_sel = config.get("selector_ubicacion")
            scored: list[tuple[int, str]] = []   # (score, url)

            skipped_sched    = 0
            skipped_geo      = 0
            skipped_exp      = 0
            skipped_practica = 0
            skipped_topic    = 0
            for el in elements:
                try:
                    href = el.get_attribute("href")
                    if not href:
                        a = el.query_selector("a[href]")
                        href = a.get_attribute("href") if a else None
                    if not href:
                        continue
                    if not href.startswith("http"):
                        base = "/".join(page.url.split("/")[:3])
                        href = base + href

                    # Leer texto completo del card (ubicación + título + snippet)
                    card_text = ""
                    try:
                        card_text = (el.text_content() or "")[:500]
                    except Exception:
                        pass

                    # -- Filtro 1: horario — descartar turno noche / finde -----
                    if not schedule_ok(card_text):
                        log.info("  [FILTRO/SCHED] Descartado por horario: %s",
                                 card_text[:80].strip().replace("\n", " "))
                        skipped_sched += 1
                        continue

                    # -- Filtro 2: experiencia — solo junior / sin experiencia --
                    if not experience_ok(card_text):
                        log.info("  [FILTRO/EXP] Descartado (senior/experiencia): %s",
                                 card_text[:80].strip().replace("\n", " "))
                        skipped_exp += 1
                        continue

                    # -- Filtro 3: practica/pasantia — nunca postular a practicas --
                    if not practica_ok(card_text):
                        log.info("  [FILTRO/PRACTICA] Descartado (practica/pasantia): %s",
                                 card_text[:80].strip().replace("\n", " "))
                        skipped_practica += 1
                        continue

                    # -- Filtro 4: rubro — solo IT/bodega, nada de marketing/salud/etc --
                    if not topic_ok(card_text):
                        log.info("  [FILTRO/TOPIC] Descartado (rubro ajeno a IT): %s",
                                 card_text[:80].strip().replace("\n", " "))
                        skipped_topic += 1
                        continue

                    # Intentar leer texto de ubicación del card
                    loc_text = ""
                    if loc_sel:
                        for sel in loc_sel.split(","):
                            try:
                                loc_el = el.query_selector(sel.strip())
                                if loc_el:
                                    loc_text = (loc_el.text_content() or "").strip()
                                    break
                            except Exception:
                                pass
                    if not loc_text:
                        loc_text = card_text[:300]

                    # Portales remotos internacionales: skip filtro geográfico
                    if config.get("remote_intl"):
                        score = 9  # tratarlos como remote siempre
                    else:
                        score = location_score(loc_text)
                    # Rechazar comunas lejanas (score 2 = _LOC_FAR: Vitacura, Las Condes, etc.)
                    if score == 2:
                        log.info("  [GEO] Rechazada por ubicación fuera de zona: '%s'", loc_text[:60])
                        skipped_geo += 1
                        continue
                    if loc_text and score != 5:
                        log.debug("  [GEO] score=%d loc='%s...' url=%s",
                                  score, loc_text[:40], href[:60])
                    scored.append((score, href))
                except Exception as exc:
                    log.debug("Error extrayendo href: %s", exc)
                    continue

            if skipped_sched:
                log.info("  %d ofertas descartadas por turno incompatible", skipped_sched)
                print(f"  [FILTRO] {skipped_sched} ofertas descartadas -> horario (noche/finde/rotativo)")
            if skipped_exp:
                log.info("  %d ofertas descartadas por nivel senior / experiencia requerida", skipped_exp)
                print(f"  [FILTRO] {skipped_exp} ofertas descartadas -> requieren experiencia/senior")
            if skipped_geo:
                log.info("  %d ofertas descartadas por zona fuera de Santiago/Maipú", skipped_geo)
                print(f"  [FILTRO] {skipped_geo} ofertas descartadas -> ubicacion fuera de zona")
            if skipped_practica:
                log.info("  %d ofertas descartadas por ser practica/pasantia", skipped_practica)
                print(f"  [FILTRO] {skipped_practica} ofertas descartadas -> practica/pasantia")
            if skipped_topic:
                log.info("  %d ofertas descartadas por rubro ajeno a IT", skipped_topic)
                print(f"  [FILTRO] {skipped_topic} ofertas descartadas -> rubro no-IT (marketing/salud/etc)")

            # Ordenar: mayor score primero (Maipú primeras, luego resto de Santiago)
            scored.sort(key=lambda x: x[0], reverse=True)

            # Loguear si hay reordenamiento visible
            if scored:
                top_score = scored[0][0]
                bot_score = scored[-1][0]
                if top_score != bot_score:
                    print(f"  [GEO] Ofertas Santiago ordenadas (Maipú primeras)"
                          f" (score {top_score}->{bot_score})")

            offer_urls = []
            seen = set()
            for _, url in scored:
                if url not in seen:
                    seen.add(url)
                    offer_urls.append(url)

            for url in offer_urls:
                if _should_stop():
                    print(f"\n[STOP] Señal de parada detectada entre URLs. Saliendo.")
                    sys.exit(0)

                if deadline and time.time() > deadline:
                    print(f"\n[TIEMPO_KW] Tiempo por keyword agotado. Siguiente keyword.")
                    break

                if applied >= max_offers or visited >= _MAX_VISITS:
                    break
                if already_applied(url):
                    log.debug("  [skip-db] %s", url)
                    continue

                print(f"  [ABRIENDO] Navegando a oferta: {url[:60]}...")
                title, status = _process_offer_generic(
                    page, url, config, profile, portal_name, dry_run
                )

                visited += 1  # siempre cuenta visita
                # Recolectar título para extracción dinámica de keywords
                if title and title not in ("unknown", ""):
                    _seen_titles.append(title)

                if status == "applied":
                    print(f"  [ÉXITO] Postulación completada para: {title or 'Sin Título'}")
                elif status.startswith("error"):
                    print(f"  [FALLO] Error en {title or 'Sin Título'}: {status}")

                log.info("  [OK] [%s] %s -> %s", portal_name, title, status)
                save_application(url, portal_name, title, status)
                _csv_log(portal_name, url, title, status)

                # Guardar quick link (todos los que pasan filtros)
                if title and title != "unknown":
                    _save_quick_link(url, title, portal_name)

                # Contar solo postulaciones REALES (no skips ni errores)
                is_real = status in _REAL_APPLY or (isinstance(status, str) and status.startswith("external:"))
                if is_real:
                    applied += 1

                print(f"  [PROGRESO] Aplicadas {applied}/{max_offers} en {portal_name.upper()}")

                if status in {"applied", "filled_no_submit", "dry_run"}:
                    rate_limiter.acquire(portal_name)

                human_delay(1.0, 2.0)
                try:
                    page.go_back(wait_until="domcontentloaded")
                    human_delay(0.8, 1.5)
                except Exception as exc:
                    log.warning("go_back falló: %s", exc)

        # -- Paginación --------------------------------------------------------
        next_sel = config.get("selector_siguiente_pagina")
        max_pages = config.get("max_pages", 3)   # máx 3 páginas por keyword, luego sigue con la siguiente
        if not next_sel or applied >= max_offers or page_num >= max_pages:
            if page_num >= max_pages:
                log.info("Límite de páginas alcanzado (%d). Fin.", max_pages)
            break

        if portal_handler:
            try:
                if page.url != current_listing_url:
                    page.goto(current_listing_url, wait_until="domcontentloaded", timeout=15_000)
                    human_delay(2.0, 3.0)
            except Exception:
                pass

        try:
            next_btn = page.query_selector(next_sel)
            if not next_btn or not next_btn.is_visible():
                log.info("Sin página siguiente. Fin.")
                break
            human_click(page, next_sel)
            human_delay(1.5, 2.5)
            page_num += 1
        except Exception as exc:
            log.warning("Error al paginar: %s", exc)
            break

    # Diagnóstico: si visitamos ofertas pero 0 fueron postulaciones reales, avisar
    if visited > 0 and applied == 0:
        print(f"  [AVISO] {visited} ofertas visitadas en {portal_name.upper()} pero 0 postulaciones reales."
              f" Causas: DB duplicada, filtros estrictos, o sesion expirada.")
        log.warning("  [AVISO] %d offers visitadas, 0 real_applied en %s", visited, portal_name)

    return applied, _seen_titles


def _scan_offer(page, offer_url: str, config: dict, profile: dict,
                portal: str) -> tuple:
    """
    Pasada 1: navega a oferta, abre formulario, escanea preguntas SIN enviar.
    Retorna (title, status, unanswered_labels).
    status: 'ready' | 'queued' | 'skipped' | 'error'

    Comportamiento por tipo de postulacion:
      - "directa" : click en boton (si existe) y escanear formulario en la misma pagina.
      - "modal"   : click en boton para abrir modal, esperar 1.5s, escanear modal.
      - "externa" : NO click (abriria nueva pestana). Escanear pagina actual y marcar
                    'queued' para que run_scan_pass lo guarde en la cola sin aplicar.
    """
    from .form_filler import scan_form
    title = "unknown"
    try:
        page.goto(offer_url, wait_until="domcontentloaded", timeout=25_000)
        human_delay(0.6, 1.2)

        page_text = (page.text_content("body") or "").lower()
        _err = ["404", "no encontramos", "not found", "oferta no disponible", "oferta eliminada"]
        if any(s in page_text for s in _err):
            return title, "skipped: eliminada", []

        title_sel = config.get("selector_titulo_oferta")
        if title_sel:
            try:
                title = (page.text_content(title_sel, timeout=2_000) or "").strip()[:80]
            except Exception:
                pass

        # Fallback: extraer título del slug de la URL si el selector falló
        if not title or title == "unknown":
            import re as _re
            slug = offer_url.rstrip("/").split("/")[-1]
            slug = _re.sub(r"[A-F0-9]{20,}.*$", "", slug, flags=_re.I)  # quitar hash al final
            slug = _re.sub(r"^oferta-de-trabajo-de-", "", slug)
            slug = _re.sub(r"-en-[a-z-]+$", "", slug)  # quitar "-en-ciudad"
            slug_title = slug.replace("-", " ").strip().title()
            if len(slug_title) > 6:
                title = slug_title[:80]

        # Filtrar por título Y por texto visible de la página (primeros 800 chars)
        page_snippet = (page.text_content("body") or "")[:800]
        if not practica_ok(title) or not practica_ok(page_snippet):
            return title, "skipped: filtro", []
        # topic_ok_it: rechazar si no hay señal IT en título o en descripción
        if not topic_ok_it(title) and not topic_ok_it(page_snippet):
            log.info("  [FILTRO/TOPIC-SCAN] Sin señal IT en pagina: '%s'", title[:55])
            return title, "skipped: filtro", []
        # topic_ok estándar: rechazar rubros explícitamente off-topic
        if not topic_ok(title) or not topic_ok(page_snippet):
            log.info("  [FILTRO/TOPIC-SCAN] Rubro off-topic en pagina: '%s'", title[:55])
            return title, "skipped: filtro", []

        tipo = config.get("tipo_postulacion", "directa")

        # Para portales externos (GetOnBoard): no hay formulario que escanear.
        # - Si es bodega → guardar como quick_link para postulación manual 1-click.
        # - Si es IT     → descartar (el usuario va directamente al portal).
        if tipo == "externa":
            if _is_bodega_job(title):
                log.info("  [SCAN-EXTERNA-BODEGA] %s — guardando como quick link", title[:55])
                return title, "bodega_quick_link", []
            else:
                log.info("  [SCAN-EXTERNA-IT] %s — descartado (externo, no bodega)", title[:55])
                return title, "skipped: externa_it", []

        # Para portales directa/modal: intentar abrir el formulario
        btn_sel = config.get("selector_boton_aplicar", "")
        if btn_sel:
            try:
                btn = page.query_selector(btn_sel)
                if btn and btn.is_visible():
                    btn.click()
                    # Esperar mas tiempo para modales (LinkedIn Easy Apply, Indeed)
                    if tipo == "modal":
                        page.wait_for_timeout(1500)
                    else:
                        human_delay(0.8, 1.5)
                else:
                    log.debug("  [SCAN] Boton aplicar no visible en %s — escaneando pagina tal cual", offer_url[:50])
            except Exception as btn_exc:
                log.debug("  [SCAN] Click en boton de aplicar fallo (%s) — escaneando pagina tal cual", btn_exc)

        result = scan_form(page, profile, job_title=title)

        # Para modales (LinkedIn Easy Apply): cerrar sin enviar para que go_back() funcione
        if tipo == "modal":
            for dismiss_sel in [
                "button[aria-label='Dismiss']",
                "button[aria-label='Cerrar']",
                "button[aria-label='Descartar']",
                "button[aria-label='Discard']",
            ]:
                try:
                    btn = page.query_selector(dismiss_sel)
                    if btn and btn.is_visible():
                        btn.click()
                        page.wait_for_timeout(600)
                        # Confirmar descarte si aparece dialogo
                        for confirm_sel in [
                            "button[data-control-name='discard_application_confirm_btn']",
                            "button:has-text('Discard')", "button:has-text('Descartar')",
                        ]:
                            cfm = page.query_selector(confirm_sel)
                            if cfm and cfm.is_visible():
                                cfm.click()
                                page.wait_for_timeout(400)
                                break
                        break
                except Exception:
                    pass

        if result["all_answered"]:
            log.info("  [SCAN-OK] %s (%d campos)", title[:55], result["answered_count"])
            return title, "ready", []
        else:
            log.info("  [SCAN-QUEUE] %s — %d sin respuesta: %s",
                     title[:45], len(result["unanswered"]),
                     ", ".join(result["unanswered"][:2]))
            return title, "queued", result["unanswered"]
    except Exception as exc:
        log.debug("_scan_offer error %s: %s", offer_url[:50], exc)
        return title, f"error: {str(exc)[:40]}", []


def _get_offer_urls_from_page(page, config: dict, limit: int) -> list:
    """Extrae URLs de ofertas de la pagina de resultados actual."""
    urls = []
    sel = config.get("selector_oferta", "a[href]")
    try:
        page.wait_for_selector(sel, timeout=8_000)
        elements = page.query_selector_all(sel)
        for el in elements:
            href = el.get_attribute("href") or ""
            if not href:
                try:
                    child_a = el.query_selector("a[href]")
                    if child_a:
                        href = child_a.get_attribute("href") or ""
                except Exception:
                    pass
            if not href:
                continue
            if not href.startswith("http"):
                base = "/".join(page.url.split("/")[:3])
                href = base + href
            if href not in urls:
                urls.append(href)
            if len(urls) >= limit * 2:
                break
    except Exception:
        pass
    return urls[:limit]


def run_scan_pass(portal_name: str, headless: bool = False) -> None:
    """
    Pasada 1: recorre todos los keywords, escanea formularios SIN postular.
    Ofertas listas -> aplica directo. Con preguntas -> guarda en scan_queue.json.

    Uso: python main.py --portal laborum --scan
    """
    from .config import KEYWORD_GROUPS, build_config_for_keyword
    from .keyword_optimizer import get_active_groups, process_keyword_result, extract_keywords_from_seen_titles

    if portal_name not in SITE_CONFIG:
        raise ValueError(f"Portal '{portal_name}' no encontrado.")

    run_startup_validation(portal_name, USER_PROFILE, SITE_CONFIG[portal_name])
    rate_limiter = get_rate_limiter(portal_name)
    session_dir  = str(SESSIONS_DIR / portal_name)
    Path(session_dir).mkdir(exist_ok=True)

    total_applied = 0
    total_queued  = 0
    total_skipped = 0

    log.info("=== PASADA 1 (SCAN) %s | %d keywords ===", portal_name.upper(), len(KEYWORD_GROUPS))
    print(f"\n[SCAN] Pasada 1 en {portal_name.upper()} — sin postular, solo recolectando\n")

    with sync_playwright() as pw:
        cdp = _try_cdp_connect(pw)
        if cdp:
            _, _, page = cdp
            using_cdp = True
        else:
            ctx = pw.chromium.launch_persistent_context(
                session_dir, headless=headless,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
                user_agent=random_user_agent(), viewport=random_viewport(),
                locale="es-CL", timezone_id="America/Santiago",
            )
            page = ctx.new_page()
            apply_stealth(page)
            using_cdp = False

        base_config = dict(SITE_CONFIG[portal_name])
        try:
            page.goto(base_config["url_busqueda"], wait_until="domcontentloaded", timeout=20_000)
            human_delay(1.5, 2.5)
            _wait_for_login_if_needed(page, portal_name, base_config)
        except Exception as e:
            log.warning("Error accediendo al portal en scan: %s", e)

        # Scan usa solo grupos marcados con scan=True (solo IT, excluye bodega)
        scan_groups_base = [g for g in KEYWORD_GROUPS if g.get("scan", True)]
        scan_groups = list(get_active_groups(scan_groups_base, portal_name))
        log.info("[SCAN] %d grupos IT para scan (bodega excluida, optimizer aplicado)", len(scan_groups))

        for group in scan_groups:
            keyword = group["keyword"]
            try:
                config = build_config_for_keyword(portal_name, keyword)
            except Exception:
                config = base_config

            max_o = config.get("max_offers_per_run", 10)
            print(f"  [SCAN] '{keyword}'", end=" ", flush=True)

            try:
                page.goto(config["url_busqueda"], wait_until="domcontentloaded", timeout=20_000)
                human_delay(0.8, 1.5)
            except Exception:
                print("ERROR nav")
                continue

            # --- Mismos filtros de card que el bot normal ---
            loc_sel = config.get("selector_ubicacion_oferta", "")
            scored: list = []
            skipped_f = 0
            raw_urls = _get_offer_urls_from_page(page, config, max_o * 3)

            # Leer card_text para cada elemento visible en la página
            try:
                card_elements = page.query_selector_all(config.get("selector_oferta", "a[href]"))
            except Exception:
                card_elements = []

            card_texts: dict = {}
            for el in card_elements:
                try:
                    href = el.get_attribute("href") or ""
                    if not href:
                        child_a = el.query_selector("a[href]")
                        if child_a:
                            href = child_a.get_attribute("href") or ""
                    if not href.startswith("http"):
                        base = "/".join(page.url.split("/")[:3])
                        href = base + href
                    card_texts[href] = (el.text_content() or "")[:500]
                except Exception:
                    pass

            skipped_geo = 0
            for url in raw_urls:
                card_text = card_texts.get(url, "")
                if not schedule_ok(card_text):
                    skipped_f += 1; continue
                if not experience_ok(card_text):
                    skipped_f += 1; continue
                if not practica_ok(card_text):
                    skipped_f += 1; continue
                # Scan estricto: topic_ok_it exige señal IT en el card
                if not topic_ok_it(card_text):
                    skipped_f += 1; continue
                loc_text = card_text
                score = location_score(loc_text)
                # Rechazar comunas fuera de Santiago / sin transporte (score 2)
                if score == 2:
                    skipped_geo += 1; continue
                scored.append((score, url))

            scored.sort(key=lambda x: x[0], reverse=True)
            urls = [u for _, u in scored][:max_o]
            if skipped_f:
                print(f"({skipped_f} filtradas)", end=" ", flush=True)
            if skipped_geo:
                print(f"({skipped_geo} fuera de zona)", end=" ", flush=True)
            # -------------------------------------------------

            scanned = 0
            for url in urls:
                if already_applied(url):
                    continue
                if scanned >= max_o:
                    break

                title, status, unanswered = _scan_offer(page, url, config, USER_PROFILE, portal_name)
                scanned += 1

                if status == "ready":
                    # Scan NUNCA postula — guarda en cola para que run_apply_queue lo procese
                    _save_to_scan_queue(url, title, portal_name, [])
                    total_queued += 1
                elif status == "bodega_quick_link":
                    # Oferta de bodega externa → panel verde (1-click manual)
                    _save_quick_link(url, title, portal_name)
                    print(f"  [🏭 BODEGA] Guardado para postulación manual: {title[:50]}")
                    total_queued += 1
                elif status == "queued":
                    _save_to_scan_queue(url, title, portal_name, unanswered)
                    total_queued += 1
                elif "skipped" in status or "error" in status:
                    total_skipped += 1

                human_delay(0.4, 0.8)
                try:
                    page.go_back(wait_until="domcontentloaded")
                    human_delay(0.3, 0.7)
                except Exception:
                    pass

            print(f"-> {scanned} escaneadas")

            # Extraer patrones de títulos vistos y generar nuevas keywords
            page_titles = [t for t in card_texts.values() if t]
            if page_titles:
                _existing = {g["keyword"].lower().strip() for g in scan_groups}
                for nk in extract_keywords_from_seen_titles(page_titles, portal_name, _existing):
                    scan_groups.append(nk)
                    print(f"  [KW_SCAN] Nueva combinación detectada: '{nk['keyword']}'")

            # Registrar resultado en optimizer: found = raw offers antes de filtros
            new_kws = process_keyword_result(keyword, portal_name, applied=0, found=len(raw_urls))
            if new_kws:
                scan_groups.extend(new_kws)
                print(f"  [KW_RETIRE] '{keyword}' retirada (0 ofertas) → {len(new_kws)} reemplazos")

        if not using_cdp:
            ctx.close()
            # Scan no postula → siempre descartar sesión (sin postulación = sin persistencia)
            _discard_session(session_dir, portal_name)

    queue_size = len(_load_scan_queue())
    print(f"\n[SCAN] Resultado pasada 1:")
    print(f"  Postuladas directo   : {total_applied}")
    print(f"  En cola (pendientes) : {total_queued}")
    print(f"  Saltadas (filtros)   : {total_skipped}")
    if queue_size:
        print(f"\n  Responde el panel naranja en http://localhost:5000 y corre:")
        print(f"  python main.py --portal {portal_name} --apply-queue")


def run_apply_queue(portal_name: str, headless: bool = False) -> None:
    """
    Pasada 2: aplica a ofertas en scan_queue.json cuyas preguntas ya estan respondidas.

    Uso: python main.py --portal laborum --apply-queue
    """
    from .form_filler import scan_form
    from .config import build_config_for_keyword

    # --- Limpieza automática ANTES de procesar ---
    # 1. Entradas expiradas (> 5 días → oferta casi siempre cerrada)
    pruned_age = _prune_scan_queue(max_age_days=5)
    if pruned_age:
        print(f"[APPLY-QUEUE] 🗑  {pruned_age} entradas expiradas (>5 días) eliminadas automáticamente.")

    raw_queue = [e for e in _load_scan_queue() if e.get("portal") == portal_name]

    # 2. Limpiar ítems no-IT antes de procesar (pueden venir de pasadas viejas sin filtros)
    queue = []
    discarded_topic = 0
    for entry in raw_queue:
        title = entry.get("title", "")
        if not topic_ok_it(title) and title:
            log.info("[APPLY-QUEUE] Descartado por topic (no IT): %s", title[:60])
            print(f"  ⏭  No-IT eliminada: {title[:60]}")
            _remove_from_scan_queue(entry["url"])
            discarded_topic += 1
        else:
            queue.append(entry)
    if discarded_topic:
        print(f"[APPLY-QUEUE] {discarded_topic} ítem(s) eliminados por no ser IT.")

    if not queue:
        print(f"[APPLY-QUEUE] Sin ofertas IT en cola para {portal_name.upper()}.")
        print(f"  Corre primero: python main.py --portal {portal_name} --scan")
        return

    print(f"\n[APPLY-QUEUE] {len(queue)} ofertas en cola para {portal_name.upper()}")
    rate_limiter = get_rate_limiter(portal_name)
    session_dir  = str(SESSIONS_DIR / portal_name)
    Path(session_dir).mkdir(exist_ok=True)

    applied       = 0
    still_pending = 0
    errors        = 0
    total_skipped = 0

    with sync_playwright() as pw:
        cdp = _try_cdp_connect(pw)
        if cdp:
            _, _, page = cdp
            using_cdp = True
        else:
            ctx = pw.chromium.launch_persistent_context(
                session_dir, headless=headless,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
                user_agent=random_user_agent(), viewport=random_viewport(),
                locale="es-CL", timezone_id="America/Santiago",
            )
            page = ctx.new_page()
            apply_stealth(page)
            using_cdp = False

        base_config = dict(SITE_CONFIG[portal_name])
        try:
            page.goto(base_config["url_busqueda"], wait_until="domcontentloaded", timeout=20_000)
            human_delay(1.5, 2.5)
            _wait_for_login_if_needed(page, portal_name, base_config)
        except Exception as e:
            log.warning("Error login apply-queue: %s", e)

        for entry in queue:
            url   = entry["url"]
            title = entry.get("title", "Sin titulo")

            if already_applied(url):
                _remove_from_scan_queue(url)
                continue

            try:
                config = build_config_for_keyword(portal_name, "desarrollador junior")
            except Exception:
                config = base_config

            print(f"  [APPLY-QUEUE] {title[:60]}", end=" ")

            try:
                # --- Verificación rápida antes de navegar ---
                # Detectar si la URL tiene señales de oferta muerta en la propia URL
                _dead_url_patterns = ["/404", "not-found", "oferta-eliminada", "expired", "job-closed"]
                if any(p in url.lower() for p in _dead_url_patterns):
                    print(f"-> [URL_MUERTA] Eliminada de cola")
                    _remove_from_scan_queue(url)
                    total_skipped += 1
                    continue

                page.goto(url, wait_until="domcontentloaded", timeout=25_000)
                human_delay(0.6, 1.2)

                # --- Verificar si la oferta sigue viva tras navegar ---
                cur_url  = page.url
                cur_text = (page.text_content("body") or "").lower()[:600]
                _dead_signals = [
                    "404", "no encontramos", "not found", "oferta no disponible",
                    "oferta eliminada", "this job is no longer", "ya no está disponible",
                    "ya no esta disponible", "oferta cerrada", "job has expired",
                ]
                _login_signals = ["/login", "/authwall", "/signin", "/checkpoint", "/uas/"]
                if any(s in cur_url.lower() for s in _login_signals):
                    print(f"-> [LOGIN_REQUERIDO] Sesión expirada — re-loguéate y vuelve a correr")
                    errors += 1
                    continue
                if any(s in cur_url.lower() or s in cur_text for s in _dead_signals):
                    print(f"-> [OFERTA_CERRADA] Eliminada de cola automáticamente")
                    _remove_from_scan_queue(url)
                    _csv_log(portal_name, url, title, "skipped: oferta_cerrada")
                    total_skipped += 1
                    continue

                tipo = config.get("tipo_postulacion", "directa")

                # Portales externos (GetOnBoard): ir directo a _apply_externa
                # No tiene sentido scan_form — el formulario está en sitio externo
                if tipo == "externa":
                    st = _apply_externa(page, config, profile=USER_PROFILE)
                    t2 = title
                    save_application(url, portal_name, t2, st)
                    _csv_log(portal_name, url, t2, st)
                    if st.startswith("external:") or st == "applied":
                        _remove_from_scan_queue(url)
                        applied += 1
                        print(f"-> ✅ {st[:70]}")
                        rate_limiter.acquire(portal_name)
                    elif st in ("skipped: no_navigation", "skipped: apply_via_modal", "skipped: login_required"):
                        removed = _increment_scan_queue_failure(url)
                        fc = 2 if removed else 1
                        reason = {"skipped: no_navigation": "sin navegación",
                                  "skipped: apply_via_modal": "requiere modal",
                                  "skipped: login_required": "requiere login"}.get(st, st)
                        if removed:
                            print(f"-> ⚠️  {reason} — eliminada de cola tras 2 fallos")
                        else:
                            print(f"-> ⚠️  {reason} (fallo {fc}/2)")
                        errors += 1
                    else:
                        errors += 1
                        print(f"-> {st[:70]}")
                    human_delay(0.5, 1.0)
                    continue

                # Portales directa/modal: re-escanear y verificar respuestas
                btn_sel = config.get("selector_boton_aplicar", "")
                if btn_sel:
                    try:
                        btn = page.query_selector(btn_sel)
                        if btn and btn.is_visible():
                            btn.click()
                            human_delay(0.8, 1.5)
                        else:
                            log.debug("[APPLY-QUEUE] Botón aplicar no visible: %s", url[:50])
                    except Exception:
                        pass

                sr = scan_form(page, USER_PROFILE, job_title=title)

                if not sr["all_answered"]:
                    still_pending += 1
                    q_labels = sr.get("unanswered", [])
                    print(f"-> ⏳ Pendiente ({len(q_labels)} preguntas sin respuesta): {', '.join(q_labels[:2])}")
                    _save_to_scan_queue(url, title, portal_name, q_labels)
                    try:
                        page.go_back(wait_until="domcontentloaded")
                    except Exception:
                        pass
                    continue

                # Todas respondidas — volver y aplicar con fill_form normal
                try:
                    page.go_back(wait_until="domcontentloaded")
                    human_delay(0.4, 0.8)
                except Exception:
                    pass

                r  = _process_offer_generic(page, url, config, USER_PROFILE, portal_name, dry_run=False)
                st = r[1] if isinstance(r, tuple) else r
                t2 = r[0] if isinstance(r, tuple) else title

                save_application(url, portal_name, t2, st)
                _csv_log(portal_name, url, t2, st)

                if st in {"applied", "filled_no_submit"}:
                    _remove_from_scan_queue(url)
                    applied += 1
                    print(f"-> ✅ Postulado!")
                    rate_limiter.acquire(portal_name)
                elif st in ("skipped: no_navigation", "skipped: oferta_eliminada"):
                    removed = _increment_scan_queue_failure(url)
                    reason = "oferta cerrada" if "eliminada" in st else "sin navegación"
                    if removed:
                        print(f"-> ⚠️  {reason} — eliminada de cola tras 2 fallos")
                    else:
                        print(f"-> ⚠️  {reason} (fallo 1/2)")
                    errors += 1
                else:
                    errors += 1
                    print(f"-> {st}")

            except Exception as exc:
                errors += 1
                log.warning("Error apply-queue %s: %s", url[:50], exc)
                # Registrar fallo — si es error persistente, eliminar de cola
                removed = _increment_scan_queue_failure(url)
                if removed:
                    print(f"-> ❌ Error — eliminada de cola tras 2 fallos ({str(exc)[:40]})")
                else:
                    print(f"-> ❌ Error (fallo 1/2): {str(exc)[:50]}")

        if not using_cdp:
            ctx.close()
            _maybe_keep_session(session_dir, portal_name, applied)

    remaining = len(_load_scan_queue())
    print(f"\n[APPLY-QUEUE] Resultado:")
    print(f"  ✅ Postuladas          : {applied}")
    print(f"  ⏳ Pendientes          : {still_pending}")
    print(f"  🗑  Cerradas/eliminadas : {total_skipped}")
    print(f"  ❌ Errores             : {errors}")
    if remaining:
        print(f"\n  Quedan en cola: {remaining} — responde el panel naranja y vuelve a correr --apply-queue")


# ── Límite de tiempo por portal y retry ─────────────────────────────────────
# MAX_PORTAL_MINUTES: tiempo máximo por sesión de portal (configurable via .env)
# RETRY_WAIT_INITIAL: si 0 postulaciones → esperar X min y reintentar
# RETRY_DECREMENT   : reducir la espera en cada reintento fallido
MAX_PORTAL_MINUTES   = int(os.getenv("MAX_PORTAL_MINUTES", "25"))
_RETRY_WAIT_INITIAL  = 15 * 60   # 15 min primer reintento
_RETRY_DECREMENT     = 5  * 60   # -5 min por cada reintento


def _retry_countdown(wait_secs: int, portal: str) -> None:
    """
    Cuenta regresiva visible en dashboard.
    Chequea señal de parada cada 2s; imprime mensaje cada 60s.
    """
    remaining = wait_secs
    since_last_print = 0
    while remaining > 0:
        if _should_stop():
            print(f"\n[STOP] Señal detectada durante espera de retry. Saliendo.")
            sys.exit(0)
        chunk = min(2, remaining)
        time.sleep(chunk)
        remaining -= chunk
        since_last_print += chunk
        if since_last_print >= 60:
            mins = remaining // 60
            secs = remaining % 60
            print(f"  [RETRY] {portal.upper()}: reintentando en {mins}m {secs:02d}s...", flush=True)
            since_last_print = 0
    # Imprimir también al inicio del countdown
    if wait_secs > 0:
        mins0 = wait_secs // 60
        print(f"  [RETRY] {portal.upper()}: esperando {mins0} min antes de reintentar...", flush=True)


def run_bot_multi_keywords(portal_name: str, dry_run: bool = False, headless: bool = False) -> None:
    """
    Abre el browser UNA VEZ y ejecuta una búsqueda por cada keyword del KEYWORD_GROUPS.
    Al compartir el contexto del browser, Cloudflare/login solo se resuelve una vez.

    Tiempo por keyword dinámico: ≤5→25 min, ≤10→20 min, ≤15→15 min, ≤20→10 min, >20→5 min.
    Si termina con 0 postulaciones → reintenta tras 15 min, luego 10 min, luego 5 min.
    """
    from .config import KEYWORD_GROUPS, build_config_for_keyword
    from .portals import PORTAL_REGISTRY
    from .keyword_optimizer import get_active_groups, process_keyword_result, extract_keywords_from_seen_titles

    if portal_name not in SITE_CONFIG:
        raise ValueError(f"Portal '{portal_name}' no encontrado.")

    # -- Verificar si el portal está habilitado --------------------------------
    if not SITE_CONFIG[portal_name].get("enabled", True):
        msg = (
            f"[STANDBY] Portal '{portal_name.upper()}' está en standby y no se ejecutará.\n"
            f"  Razón: Cloudflare Turnstile detecta Playwright Chromium en segundos.\n"
            f"  Para habilitarlo: establece INDEED_ENABLED=true en .env (cuando esté implementada\n"
            f"  la solución con patchright/camoufox o CDP verificado)."
        )
        log.warning(msg)
        print(f"\n[!] CAPTCHA DETECTADO EN {portal_name.upper()}... Por favor resuélvelo manualmente. (ver plan de implementación).")
        return

    # -- Verificar restricción temporal de LinkedIn ---------------------------
    if portal_name == "linkedin":
        _restr_path = BASE_DIR / "data" / "portal_restrictions.json"
        _restr = {}
        if _restr_path.exists():
            try:
                _restr = json.load(open(_restr_path, encoding="utf-8"))
            except Exception:
                pass
        _linkedin_restr = _restr.get("linkedin", {})
        if _linkedin_restr.get("restricted"):
            import datetime as _dt
            until_str = _linkedin_restr.get("until", "")
            try:
                until_dt = _dt.datetime.fromisoformat(until_str)
                if _dt.datetime.now() < until_dt:
                    until_fmt = until_dt.strftime("%d/%m/%Y %H:%M")
                    print(f"\n[LINKEDIN] ⛔ Cuenta restringida hasta {until_fmt}. "
                          "Saltando LinkedIn en esta sesión.")
                    log.warning("LinkedIn restringido hasta %s — saltando.", until_str)
                    return
                else:
                    # Restricción ya venció — limpiar
                    _linkedin_restr["restricted"] = False
                    with open(_restr_path, "w", encoding="utf-8") as _f:
                        json.dump(_restr, _f, ensure_ascii=False, indent=2)
                    print(f"\n[LINKEDIN] Restricción vencida — reanudando con cautela.")
            except Exception:
                pass

    run_startup_validation(portal_name, USER_PROFILE, SITE_CONFIG[portal_name])

    rate_limiter = get_rate_limiter(portal_name)
    chrome_exe   = _find_chrome_executable()
    if chrome_exe:
        log.info("Usando Chrome del sistema: %s", chrome_exe)
    else:
        log.info("Usando Chromium de Playwright")

    _PORTAL_LOCALE = {
        # Portales Chile — navegador en español para evitar detección
        "indeed":        ("es-CL", "America/Santiago"),
        "laborum":       ("es-CL", "America/Santiago"),
        "getonyboard":   ("es-CL", "America/Santiago"),
        "computrabajo":  ("es-CL", "America/Santiago"),
        "linkedin":      ("es-CL", "America/Santiago"),
        "chiletrabajos": ("es-CL", "America/Santiago"),
        # Portales remotos internacionales — navegador en inglés
        "weworkremotely": ("en-US", "America/New_York"),
        "remotive":       ("en-US", "America/New_York"),
        "remoteco":       ("en-US", "America/New_York"),
    }
    _locale, _tz = _PORTAL_LOCALE.get(portal_name, ("es-CL", "America/Santiago"))

    session_dir = str(SESSIONS_DIR / portal_name)
    Path(session_dir).mkdir(exist_ok=True)

    log.info("=== ApplyJob Bot (Multi-Keyword — browser compartido) ===")
    log.info("Portal: %s | grupos: %d", portal_name, len(KEYWORD_GROUPS))

    with sync_playwright() as pw:
        # -- Intentar CDP primero (Chrome del usuario ya abierto) -------------
        cdp_result = _try_cdp_connect(pw)
        using_cdp  = cdp_result is not None

        if using_cdp:
            browser_instance, browser_ctx, page = cdp_result
            log.info("Modo CDP — usando Chrome real del usuario (puerto %d)", _CDP_PORT)
            print(f"[CDP] [OK] Conectado a tu Chrome real — tus sesiones de Indeed/LinkedIn activas.")
        else:
            # -- Fallback: lanzar Chrome propio (sin sesiones reales) ---------
            # Prioridad: CDP (Chrome DevTools Protocol) para conectar a una sesión de Chrome abierta vía iniciar_bot.bat
            print(f"[AVISO] Chrome no está en modo debug (puerto {_CDP_PORT} cerrado).")
            print(f"[AVISO] Usa iniciar_bot.bat para abrir Chrome correctamente.")
            print(f"[AVISO] Lanzando Chromium propio — puede pedir login manualmente.")
            log.info("Modo fallback — lanzando Chromium propio")
            launch_kwargs = dict(
                user_data_dir = session_dir,
                headless      = headless,
                user_agent    = random_user_agent(),
                viewport      = random_viewport(),
                locale        = _locale,
                timezone_id   = _tz,
                args          = ["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            if chrome_exe:
                launch_kwargs["executable_path"] = chrome_exe

            browser_ctx = pw.chromium.launch_persistent_context(**launch_kwargs)
            page        = browser_ctx.new_page()
            browser_instance = None # No lo necesitamos en modo no-CDP
            apply_stealth(page)
            try:
                from playwright_stealth import Stealth
                Stealth().apply_stealth_sync(page)
                log.info("playwright-stealth activo")
            except (ImportError, Exception) as se:
                log.warning("playwright-stealth no disponible (%s) — usando stealth manual", se)

        total_applied     = 0
        _total_max_target = 0      # suma de max_offers de cada keyword (para PROGRESO_FINAL)
        _session_verified = False  # Se activa tras el primer pre-flight exitoso

        def _detect_linkedin_restriction(pg) -> str | None:
            """
            Detecta si LinkedIn ha restringido la cuenta.
            Retorna la fecha límite como string si está restringida, None si no.
            """
            try:
                body = pg.evaluate("document.body?.innerText?.slice(0, 800) || ''") or ""
                url  = pg.url
                restricted = (
                    "temporalmente restringido" in body.lower() or
                    "access to your account is temporarily restricted" in body.lower() or
                    "restricted" in url.lower() and "challenge" in url.lower()
                )
                if restricted:
                    # Intentar extraer fecha del texto
                    import re as _re
                    m = _re.search(r"(May|Jun|Jul|Aug|Jan|Feb|Mar|Apr|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}", body)
                    return m.group(0) if m else "próximamente"
                return None
            except Exception:
                return None

        # Obtener keywords activas (base - retiradas + generadas con éxito)
        active_groups = get_active_groups(KEYWORD_GROUPS, portal_name)
        log.info("[KW_OPTIMIZER] %d keywords activas (de %d base)",
                 len(active_groups), len(KEYWORD_GROUPS))
        print(f"\n[KEYWORDS] {len(active_groups)} keywords activas para {portal_name.upper()}")

        # ── Tiempo por keyword dinámico ────────────────────────────────────────
        # Más keywords → menos tiempo por keyword para que todas reciban atención.
        # Escala: ≤5→25 min, ≤10→20 min, ≤15→15 min, ≤20→10 min, >20→5 min
        _n_kw = len(active_groups) or 1
        if _n_kw <= 5:
            _per_kw_minutes = 25
        elif _n_kw <= 10:
            _per_kw_minutes = 20
        elif _n_kw <= 15:
            _per_kw_minutes = 15
        elif _n_kw <= 20:
            _per_kw_minutes = 10
        else:
            _per_kw_minutes = 5
        # Límite total de sesión = suma de budgets por keyword (mínimo 20 min)
        _session_max_minutes = max(20, _n_kw * _per_kw_minutes)
        print(f"[TIEMPO_KW] {_n_kw} keywords → {_per_kw_minutes} min/keyword "
              f"(máx sesión: {_session_max_minutes} min)")

        # ── Retry loop: si 0 postulaciones → esperar y reintentar ───────────────
        # Primer reintento: 15 min. Cada fallo siguiente resta 5 min (10→5→0→fin).
        _retry_wait  = _RETRY_WAIT_INITIAL
        _retry_count = 0

        while True:   # retry loop — sale con break en éxito o sin más reintentos

            # ── Chequeo de señal de parada (antes de cada intento) ────────────
            if _should_stop():
                print(f"\n[STOP] Señal de parada detectada. Cerrando {portal_name.upper()} limpiamente.")
                sys.exit(0)

            # Reiniciar contadores para cada intento
            total_applied     = 0
            _total_max_target = 0
            _session_verified = False   # re-verificar login en cada intento
            _kw_queue         = list(active_groups)
            _kw_index         = 0
            _portal_start     = time.time()

            if _retry_count > 0:
                print(f"\n[RETRY] {portal_name.upper()}: Intento #{_retry_count + 1} "
                      f"({_per_kw_minutes} min/keyword)\n")

            # ── Loop de keywords ──────────────────────────────────────────────
            while _kw_index < len(_kw_queue):

                # ── Chequeo de señal de parada (inicio de cada keyword) ───────
                if _should_stop():
                    print(f"\n[STOP] Señal de parada detectada en loop de keywords. "
                          f"Cerrando {portal_name.upper()} limpiamente.")
                    sys.exit(0)

                # Límite total de sesión (seguridad — en caso de keywords dinámicas)
                _elapsed = time.time() - _portal_start
                if _elapsed >= _session_max_minutes * 60:
                    print(f"\n[TIEMPO] {portal_name.upper()}: limite de sesión "
                          f"{_session_max_minutes} min alcanzado ({_elapsed/60:.1f} min).")
                    log.info("[TIEMPO] Limite sesion %d min alcanzado en %s",
                             _session_max_minutes, portal_name)
                    break

                group   = _kw_queue[_kw_index]
                _kw_index += 1
                keyword = group["keyword"]
                mode    = group["mode"]
                label   = group["label"]

                # Portales tech-only: saltar keywords de bodega/logística
                _TECH_ONLY_PORTALS = {"getonyboard", "weworkremotely", "remotive", "remoteco"}
                if portal_name in _TECH_ONLY_PORTALS and label.lower() == "bodega":
                    log.info("[%s] Saltando keyword bodega '%s' — portal tech-only.", portal_name, keyword)
                    print(f"  [SKIP] '{keyword}' omitido en {portal_name.upper()} (plataforma tech-only)")
                    continue

                config  = build_config_for_keyword(portal_name, keyword)
                profile = dict(USER_PROFILE)
                profile["_mode"] = mode
                max_offers = config.get("max_offers_per_run", 10)
                _total_max_target += max_offers

                PortalClass    = PORTAL_REGISTRY.get(portal_name)
                portal_handler = PortalClass(config, profile, dry_run) if PortalClass else None

                log.info("=== Busqueda atomica [%s] '%s' ===", label.upper(), keyword)
                print(f"\n[BUSQUEDA] [{label.upper()}] Buscando: '{keyword}' en {portal_name.upper()}")

                # Verificar / recuperar página
                page_needs_recreation = False
                try:
                    if page.is_closed():
                        page_needs_recreation = True
                    else:
                        page.evaluate("1")
                except Exception:
                    page_needs_recreation = True

                if page_needs_recreation:
                    log.warning("Pagina cerrada/no responde entre keywords. Recreando...")
                    try:
                        page = browser_ctx.new_page()
                        if not using_cdp:
                            apply_stealth(page)
                    except Exception as page_err:
                        log.error("No se pudo recrear la pagina: %s", page_err)
                        break

                # LinkedIn: verificar restricción antes de cada keyword
                if portal_name == "linkedin":
                    restr_until = _detect_linkedin_restriction(page)
                    if restr_until:
                        print(f"\n[LINKEDIN] Cuenta restringida (hasta {restr_until}). Saltando.")
                        log.warning("LinkedIn restringido: %s", restr_until)
                        _restr_path = BASE_DIR / "data" / "portal_restrictions.json"
                        _restr = {}
                        if _restr_path.exists():
                            try: _restr = json.load(open(_restr_path, encoding="utf-8"))
                            except Exception: pass
                        import datetime as _dt2
                        try:
                            _until_dt = _dt2.datetime.strptime(restr_until + " 23:59", "%B %d, %Y %H:%M")
                        except Exception:
                            _until_dt = _dt2.datetime.now() + _dt2.timedelta(days=2)
                        _restr["linkedin"] = {"restricted": True, "until": _until_dt.isoformat()}
                        _restr_path.parent.mkdir(exist_ok=True)
                        with open(_restr_path, "w", encoding="utf-8") as _f:
                            json.dump(_restr, _f, ensure_ascii=False, indent=2)
                        break

                _kw_deadline = time.time() + _per_kw_minutes * 60
                applied, seen_titles = _run_keyword_loop(
                    page, browser_ctx, portal_name, config, profile,
                    max_offers, dry_run, rate_limiter, portal_handler,
                    using_cdp=using_cdp,
                    session_verified=_session_verified,
                    deadline=_kw_deadline,
                )
                _session_verified = True
                total_applied += applied

                if applied == 0:
                    print(f"\n[AVISO] '{keyword}': 0 postulaciones — filtradas o ya en DB.")
                print(f"\n[PORTAL_FINALIZADO] --- KEYWORD '{keyword}': {applied} postulaciones ---")

                # Extracción dinámica de keywords desde títulos vistos
                if seen_titles:
                    _existing_kws = {g["keyword"].lower().strip() for g in _kw_queue}
                    scan_new_kws = extract_keywords_from_seen_titles(
                        seen_titles, portal_name, existing_keywords=_existing_kws
                    )
                    for nk in scan_new_kws:
                        _kw_queue.append(nk)

                # Registrar estadísticas y evaluar retiro
                new_kws = process_keyword_result(keyword, portal_name, applied, found=len(seen_titles) if seen_titles else 0)
                if new_kws:
                    for nk in new_kws:
                        _kw_queue.append(nk)
                    print(f"  [REEMPLAZOS] {len(new_kws)} nuevas keywords añadidas a la cola.")

                # Guardia de sesión muerta cada 5 keywords sin postulaciones
                _KEYWORDS_BEFORE_SESSION_CHECK = 5
                if (config.get("requires_login")
                        and total_applied == 0
                        and _kw_index % _KEYWORDS_BEFORE_SESSION_CHECK == 0
                        and _kw_index > 0):
                    print(f"\n[SESION_CHECK] {portal_name.upper()}: {_kw_index} keywords "
                          f"sin postulaciones — verificando sesion...")
                    log.warning("[SESION_CHECK] 0 aplicadas tras %d kws en %s",
                                _kw_index, portal_name)
                    try:
                        _HOME_URLS_CHECK = {
                            "chiletrabajos": "https://www.chiletrabajos.cl",
                            "computrabajo":  "https://candidato.cl.computrabajo.com",
                            "laborum":       "https://www.laborum.cl",
                            "indeed":        "https://cl.indeed.com",
                            "getonyboard":   "https://www.getonbrd.com",
                            "linkedin":      "https://www.linkedin.com/feed",
                        }
                        if portal_name in _HOME_URLS_CHECK:
                            page.goto(_HOME_URLS_CHECK[portal_name],
                                      wait_until="domcontentloaded", timeout=15_000)
                            human_delay(1.5, 2.0)
                            _wait_for_login_if_needed(page, portal_name, config)
                            _session_verified = False
                    except Exception as sc_err:
                        log.warning("[SESION_CHECK] Error re-verificando: %s", sc_err)

                # Pausa anti-Cloudflare entre keywords
                if _kw_index < len(_kw_queue):
                    _strict = portal_name in ("linkedin", "indeed")
                    _kw_pause = random.uniform(8.0, 14.0) if _strict else random.uniform(3.0, 6.0)
                    log.info("Pausa inter-keyword: %.0fs...", _kw_pause)
                    print(f"\n[PAUSA] Esperando {_kw_pause:.0f}s antes de la proxima busqueda...")
                    time.sleep(_kw_pause)
            # ── Fin loop de keywords ──────────────────────────────────────────

            # Progreso de este intento
            print(f"[PROGRESO_FINAL] Aplicadas {total_applied}/{_total_max_target} en {portal_name.upper()}")

            # ── Decisión de reintento ─────────────────────────────────────────
            if total_applied == 0 and _retry_wait > 0:
                wait_min = _retry_wait // 60
                next_wait = max((_retry_wait - _RETRY_DECREMENT) // 60, 0)
                print(f"\n[RETRY] {portal_name.upper()}: 0 postulaciones en esta tanda.")
                print(f"  Reintentando en {wait_min} min "
                      f"(siguiente espera: {next_wait} min | intento #{_retry_count + 2})")
                _retry_countdown(_retry_wait, portal_name)
                _retry_wait  -= _RETRY_DECREMENT
                _retry_count += 1
                continue   # volver al inicio del retry loop (nueva tanda)
            else:
                if total_applied > 0:
                    log.info("[RETRY] %s: %d postulaciones. Sin reintento necesario.",
                             portal_name, total_applied)
                else:
                    print(f"\n[RETRY] {portal_name.upper()}: 0 postulaciones. Sin mas reintentos.")
                break   # salir del retry loop

        # En modo CDP solo cerramos la pestaña, NO el browser del usuario
        if using_cdp:
            try:
                page.close()
                log.info("Pestaña del bot cerrada. Chrome del usuario sigue abierto.")
            except Exception:
                pass
        else:
            try:
                browser_ctx.close()
            except Exception as close_err:
                log.warning("browser_ctx.close() ignorado: %s", close_err)
            _maybe_keep_session(session_dir, portal_name, total_applied)

    log.info("=== Multi-Keyword Fin. Total aplicadas: %d | Rate: %d/%d ===",
             total_applied, rate_limiter.current_count, rate_limiter.max_actions)
    log.info("Logs CSV: %s", LOGS_DIR)

    # Emitir progreso final con flag finished=True para que el dashboard coloree
    print(f"[PROGRESO_FINAL] Aplicadas {total_applied}/{_total_max_target} en {portal_name.upper()}")

    if total_applied < MIN_APPLIES_TO_KEEP_SESSION:
        print(f"\n[SIN POSTULACIONES] {portal_name.upper()} termino con {total_applied}/{_total_max_target} postulaciones reales "
              f"(minimo requerido: {MIN_APPLIES_TO_KEEP_SESSION}).")
        print(f"  - Sesion DESCARTADA — la proxima vez pedira login de nuevo.")
        print(f"  - Causas posibles:")
        print(f"    * Todas las ofertas ya estan en la base de datos (ya postuladas antes)")
        print(f"    * Los filtros de experiencia/horario/zona las descartaron")
        print(f"    * La sesion expiro durante el recorrido")
        print(f"  - Revisa los logs en http://127.0.0.1:5000")
    else:
        print(f"\n[OK] {portal_name.upper()} — {total_applied}/{_total_max_target} postulaciones reales. "
              f"Sesion guardada.")


def run_bot(
    portal_name: str,
    dry_run: bool = False,
    headless: bool = False,
    config_override: dict = None,
    profile_mode: str = None,
    pw = None,
) -> int:
    """
    Ejecuta el bot para el portal especificado.

    Pasos:
      1. Validar configuración (USER_PROFILE + SITE_CONFIG)
      2. Abrir browser con sesión persistente (user_data_dir)
      3. Navegar a url_busqueda
      4. Por cada página: extraer ofertas -> deduplicar -> rate limit -> postular
      5. Paginar hasta max_offers o sin siguiente página

    Args:
        portal_name    : clave de SITE_CONFIG (ej. "linkedin", "indeed")
        dry_run        : navega y loguea pero NO postula
        headless       : corre sin ventana de browser
        config_override: dict de config alternativo (usado por run_bot_multi_keywords)
        profile_mode   : "it" | "bodega" — elige la personalidad del profile_kb.json

    Raises:
        ValueError    : si portal_name no existe en SITE_CONFIG
        ConfigError   : si USER_PROFILE o SITE_CONFIG están mal configurados
    """
    if portal_name not in SITE_CONFIG:
        available = ", ".join(SITE_CONFIG.keys())
        raise ValueError(f"Portal '{portal_name}' no encontrado. Disponibles: {available}")

    # -- Verificar si el portal está habilitado --------------------------------
    if not SITE_CONFIG[portal_name].get("enabled", True):
        log.warning("[STANDBY] Portal '%s' deshabilitado — saltando.", portal_name.upper())
        print(f"\n[STANDBY] {portal_name.upper()} está en standby y no se ejecutará.")
        return

    config = config_override if config_override is not None else SITE_CONFIG[portal_name]

    profile = dict(USER_PROFILE)
    if profile_mode:
        profile["_mode"] = profile_mode

    max_offers = config.get("max_offers_per_run", 10)

    # -- 1. Validar configuración ----------------------------------------------
    run_startup_validation(portal_name, USER_PROFILE, config)

    # -- 2. Cargar portal específico si existe ---------------------------------
    from .portals import PORTAL_REGISTRY
    PortalClass    = PORTAL_REGISTRY.get(portal_name)
    portal_handler = PortalClass(config, profile, dry_run) if PortalClass else None

    # -- 3. Rate limiter -------------------------------------------------------
    rate_limiter = get_rate_limiter(portal_name)

    session_dir = str(SESSIONS_DIR / portal_name)
    Path(session_dir).mkdir(exist_ok=True)

    log.info("=== ApplyJob Bot ===")
    log.info("Portal: %s | max: %d | dry_run: %s | motor: %s | rate_limit: %d/h",
             portal_name, max_offers, dry_run,
             PortalClass.__name__ if PortalClass else "genérico",
             rate_limiter.max_actions)

    chrome_exe = _find_chrome_executable()
    if chrome_exe:
        log.info("Usando Chrome del sistema: %s", chrome_exe)
    else:
        log.info("Usando Chromium de Playwright")

    # Ajustar locale / timezone según el portal — SIEMPRE Chile
    _PORTAL_LOCALE = {
        "indeed":         ("es-CL", "America/Santiago"),
        "laborum":        ("es-CL", "America/Santiago"),
        "getonyboard":    ("es-CL", "America/Santiago"),
        "computrabajo":   ("es-CL", "America/Santiago"),
        "linkedin":       ("es-CL", "America/Santiago"),
        "chiletrabajos":  ("es-CL", "America/Santiago"),
    }
    _locale, _tz = _PORTAL_LOCALE.get(portal_name, ("es-CL", "America/Santiago"))

    def _exec(pw_obj):
        # -- 4. Intentar CDP prioritario (Chrome del usuario ya abierto) --
        cdp_result = _try_cdp_connect(pw_obj)
        is_cdp = cdp_result is not None

        if is_cdp:
            browser_instance, browser_ctx, page = cdp_result
            log.info("Modo CDP — usando Chrome real del usuario (puerto %d)", _CDP_PORT)
        else:
            # Fallback: lanzar Chrome propio (sin sesiones reales)
            launch_kwargs = dict(
                user_data_dir = session_dir,
                headless      = headless,
                user_agent    = random_user_agent(),
                viewport      = random_viewport(),
                locale        = _locale,
                timezone_id   = _tz,
                args          = ["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            if chrome_exe:
                launch_kwargs["executable_path"] = chrome_exe

            browser_ctx = pw_obj.chromium.launch_persistent_context(**launch_kwargs)
            page = browser_ctx.new_page()
            browser_instance = None # No lo necesitamos en modo no-CDP
            apply_stealth(page)
            try:
                from playwright_stealth import Stealth
                Stealth().apply_stealth_sync(page)
            except:
                pass

        applied, _seen_titles_single = _run_keyword_loop(
            page, browser_ctx, portal_name, config, profile,
            max_offers, dry_run, rate_limiter, portal_handler,
            using_cdp=is_cdp,
        )

        if is_cdp:
            try: page.close()
            except: pass
        else:
            browser_ctx.close()
        
        print(f"\n[PORTAL_FINALIZADO] --- PORTAL {portal_name.upper()} COMPLETADO ---")
        log.info("=== Fin. Procesadas: %d | Rate usado: %d/%d ===",
                 applied, rate_limiter.current_count, rate_limiter.max_actions)
        log.info("Logs CSV: %s", LOGS_DIR)

        return applied

    if pw:
        return _exec(pw)
    else:
        with sync_playwright() as pw_instance:
            return _exec(pw_instance)

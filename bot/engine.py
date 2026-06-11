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
from bot.browser_discovery import select_browser_backend

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


# ── Relevance score ───────────────────────────────────────────────────────────
_RELEVANCE_THRESHOLD = float(os.getenv("RELEVANCE_THRESHOLD", "0.0"))
# 0.0 = desactivado (acepta todo). 0.3 = requiere que al menos 1 keyword
# aparezca en el título de la oferta. Se configura en .env.


def _relevance_score(title: str, keywords: list) -> float:
    """
    Fracción de keywords que aparecen (substring) en el título normalizado.
    Retorna 1.0 si keywords está vacío (sin filtro → aceptar todo).
    """
    if not keywords:
        return 1.0
    t = title.lower()
    for a, b in [("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),("ñ","n")]:
        t = t.replace(a, b)
    hits = sum(1 for kw in keywords if kw.lower() in t)
    return hits / len(keywords)

from .config import SITE_CONFIG, USER_PROFILE, location_score, location_min_score, mode_ok, schedule_ok, experience_ok, practica_ok, topic_ok, topic_ok_it, contract_ok
from .state import already_applied, save_application
from .stealth_utils import (
    apply_stealth, human_delay, human_scroll, human_click,
    take_error_screenshot, detect_captcha,
)
from .notifier import send_alert
from .form_filler import fill_form
from .retry import with_retry, get_rate_limiter, RateLimitExceeded
from .validator import run_startup_validation

# log = logging.getLogger("applyjob.engine") (movido arriba)

BASE_DIR        = Path(__file__).parent.parent
SESSIONS_DIR    = BASE_DIR / "sessions"
LOGS_DIR        = BASE_DIR / "logs"
SCAN_QUEUE_PATH = BASE_DIR / "data" / "scan_queue.json"
QUICK_LINKS_PATH = BASE_DIR / "data" / "quick_links.json"
ENABLE_QUICK_LINKS = os.getenv("ENABLE_QUICK_LINKS", "false").strip().lower() in ("1", "true", "yes")
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
            print(f"[SESSION] ⚠ Sin postulaciones en {portal_name.upper()} - sesion descartada (privacidad).")
    except Exception as exc:
        log.debug("_discard_session error: %s", exc)

MIN_APPLIES_TO_KEEP_SESSION = 0   # mantener sesión siempre (0 = nunca descartar por postulaciones)

def _maybe_keep_session(session_dir: str, portal_name: str, applied: int) -> None:
    """
    Mantiene la sesión guardada para el próximo run.
    Solo descarta si applied < 0 (indicador explícito de fallo de login).
    Con applied >= 0 (incluyendo 0 por filtros/no-ofertas), la sesión se preserva.
    """
    if applied < 0:
        # Señal explícita de fallo de autenticación → descartar
        _discard_session(session_dir, portal_name)
    else:
        print(f"[SESSION] {portal_name.upper()} - sesion preservada "
              f"({applied} postulaciones). Login válido para el próximo run.")


# ---------------------------------------------------------------------------
# Helper: detecta si un título de oferta corresponde a bodega/logística
# Se usa en scan mode para portales externos (GetOnBoard, Trabajando)
# ---------------------------------------------------------------------------
_BODEGA_SIGNALS = frozenset({
    "bodega", "bodeguero", "bodeguera",
    "operario bodega", "auxiliar bodega",
    "operario logistica", "auxiliar logistica",
    "logistica", "despacho", "picking", "recepcion",
    "almacen", "almacenamiento",
})

def _is_bodega_job(title: str) -> bool:
    """True si el título sugiere un cargo de bodega/logística."""
    if not title:
        return False
    low = title.lower()
    return any(w in low for w in _BODEGA_SIGNALS)


# ---------------------------------------------------------------------------
# Helper: filtro de "país no coincide" — pero NO bloquear híbrido/remoto
# ---------------------------------------------------------------------------
# GetOnBoard y portales internacionales muestran un aviso "Tu país no coincide
# con la ubicación del empleo" / "Este empleo solo acepta postulantes de
# <ciudad> (<país>)" cuando el perfil del usuario (Chile) no coincide con la
# ubicación de la oferta. Si el cargo es PRESENCIAL en otro país → imposible
# de ejercer, hay que saltarlo. Pero si la oferta es híbrida o remota, el
# país de residencia no es un impedimento real — no debe descartarse solo
# por ese aviso.
_COUNTRY_MISMATCH_SIGNALS = (
    "tu país no coincide", "tu pais no coincide",
    "no coincide con la ubicación del empleo", "no coincide con la ubicacion del empleo",
    "solo acepta postulantes de", "solo acepta postulantes",
    "this job is not available in your country", "not available in your location",
)
_HYBRID_REMOTE_SIGNALS = (
    "híbrido", "hibrido", "hybrid",
    "remoto", "remote", "teletrabajo", "trabajo remoto",
    "trabajo desde casa", "home office", "100% remoto", "full remote",
)

def _country_mismatch_blocks_apply(page_text: str) -> bool:
    """
    True si el texto de la página indica que el país del usuario no coincide
    con la ubicación de la oferta Y la modalidad NO es híbrida/remota
    (i.e. el cargo es presencial en otro país → debe saltarse).
    Si la oferta es híbrida/remota, retorna False (no bloquear).
    """
    low = (page_text or "").lower()
    if not any(s in low for s in _COUNTRY_MISMATCH_SIGNALS):
        return False
    if any(s in low for s in _HYBRID_REMOTE_SIGNALS):
        return False
    return True


# ---------------------------------------------------------------------------
# Quick Links — todas las ofertas que pasan filtros, listas para postulación manual
# ---------------------------------------------------------------------------

def _save_quick_link(url: str, title: str, portal: str) -> None:
    """Guarda un link rápido en quick_links.json (sin duplicados)."""
    if not ENABLE_QUICK_LINKS:
        return
    try:
        QUICK_LINKS_PATH.parent.mkdir(exist_ok=True)
        links: list = []
        if QUICK_LINKS_PATH.exists():
            try:
                with open(QUICK_LINKS_PATH, encoding="utf-8") as _f:
                    links = json.load(_f)
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
        with open(SCAN_QUEUE_PATH, encoding="utf-8") as _f:
            return json.load(_f)
    except Exception:
        return []


def _norm_queue_url(url: str) -> str:
    """Normaliza URLs para evitar duplicados por fragments (#lc=…) o trailing slashes."""
    return url.split("#")[0].rstrip("/")


def _save_to_scan_queue(url: str, title: str, portal: str,
                        unanswered: list) -> None:
    clean_url = _norm_queue_url(url)
    existing_queue = _load_scan_queue()
    # Preservar failure_count si ya existía la entrada (comparar por URL normalizada)
    old_entry = next((e for e in existing_queue if _norm_queue_url(e.get("url", "")) == clean_url), None)
    failure_count = old_entry.get("failure_count", 0) if old_entry else 0
    # Eliminar cualquier entrada previa con esa URL normalizada (incluye variantes de fragment)
    queue = [e for e in existing_queue if _norm_queue_url(e.get("url", "")) != clean_url]
    queue.append({
        "url":           clean_url,
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
    clean_url = _norm_queue_url(url)
    queue = [e for e in _load_scan_queue() if _norm_queue_url(e.get("url", "")) != clean_url]
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


def _handle_possible_captcha(page: Page, portal_name: str, timeout_s: int = 300) -> bool:
    """
    Si detecta un CAPTCHA en la página, toma screenshot para referencia/depuración
    y continúa de inmediato — NO espera resolución manual (nadie está mirando el
    navegador en tiempo real). Retorna True si se detectó un captcha (para que el
    caller pueda saltar la oferta/portal actual), False si no había.
    """
    try:
        if not detect_captcha(page):
            return False
    except Exception:
        return False

    # Tomar screenshot para referencia/depuración (no bloquea)
    try:
        take_error_screenshot(page, portal_name, context="captcha")
    except Exception:
        pass

    try:
        current_url = page.url or "[URL desconocida]"
    except Exception:
        current_url = "[URL desconocida]"
    log.warning("[CAPTCHA-SKIP] %s detectó verificacion humana (%s) — saltando sin esperar.",
                portal_name, current_url)
    print(f"[CAPTCHA-SKIP] {portal_name}: verificacion humana detectada — saltando sin esperar ({current_url})")
    return True


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

def _apply_directa(page: Page, config: dict, profile: dict, job_title: str = "") -> tuple:
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
        Tuple (status, unanswered_labels):
          status: "applied" | "filled_no_submit" | "skipped: preguntas_sin_respuesta" | "error: <msg>"
          unanswered_labels: list[str] de preguntas sin respuesta (vacío si no hay)
    """
    btn_sel = config["selector_boton_aplicar"]
    try:
        human_click(page, btn_sel)
        human_delay(1.2, 2.5)
        form_result = fill_form(page, profile, job_title=job_title)

        # Si quedaron preguntas sin respuesta → rellenar con cover_letter y continuar
        if form_result.get("unanswered", 0) > 0:
            labels = form_result.get("unanswered_labels", [])
            cover = profile.get("cover_letter", "") or profile.get("bodega_exp", "")
            log.warning("  [FORM] %d pregunta(s) sin respuesta — usando cover_letter: %s",
                        len(labels), ", ".join(labels[:3]))
            print(f"  [FORM] Rellenando {len(labels)} campo(s) pendiente(s) con carta de presentacion")
            if cover:
                try:
                    for ta in page.query_selector_all("textarea:visible, input[type='text']:visible"):
                        try:
                            if not (ta.evaluate("el => el.value") or "").strip():
                                ta.fill(cover[:500])
                        except Exception:
                            pass
                except Exception:
                    pass

        for submit_sel in [
            "button[type='submit']", "input[type='submit']",
            "button:has-text('Enviar postulación')", "button:has-text('Postularme')",
            "button:has-text('Enviar')", "button:has-text('Submit')",
            "button:has-text('Apply')", "button:has-text('Postular')",
            "button:has-text('Continuar')", "button:has-text('Siguiente')",
        ]:
            try:
                btn = page.query_selector(submit_sel)
                if btn and btn.is_visible() and btn.is_enabled():
                    human_click(page, submit_sel)
                    human_delay(1.2, 2.0)
                    return "applied", []
            except Exception:
                continue
        return "filled_no_submit", []
    except Exception as exc:
        log.warning("_apply_directa falló: %s", exc)
        return f"error: {exc}", []


def _apply_modal(page: Page, config: dict, profile: dict, job_title: str = "") -> tuple:
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
        Tuple (status, unanswered_labels):
          status: "applied" | "skipped: preguntas_sin_respuesta" | "error: <msg>"
          unanswered_labels: list[str] de preguntas sin respuesta (vacío si no hay)
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
            print(f"  [SKIP] Postulacion cancelada - preguntas sin respuesta: {', '.join(labels[:3])}")
            return "skipped: preguntas_sin_respuesta", labels

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
                            return "skipped: preguntas_sin_respuesta", labels
                        advanced = True
                        break
                except Exception as exc:
                    log.debug("Boton '%s' no disponible: %s", next_sel, exc)
                    continue
            if not advanced:
                break
        return "applied", []
    except Exception as exc:
        log.warning("_apply_modal fallo: %s", exc)
        return f"error: {exc}", []


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
            print(f"  [FILTRO] Oferta eliminada o expirada - saltando: {offer_url}")
            return title, "skipped: oferta_eliminada"

        if _country_mismatch_blocks_apply(page_text):
            log.info("  [SKIP] Pais no coincide y oferta es presencial: %s", offer_url)
            print(f"  [FILTRO] Presencial en otro pais (no hibrido/remoto) - saltando: {offer_url}")
            return title, "skipped: pais_no_coincide"

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
            print(f"  [FILTRO] Practica/pasantia detectada en pagina - saltando: {title}")
            return title, "skipped: practica"
        if not topic_ok(title):
            log.info("  [FILTRO/TOPIC] Oferta descartada por rubro no-IT en titulo: '%s'", title)
            print(f"  [FILTRO] Rubro no-IT detectado en pagina - saltando: {title}")
            return title, "skipped: rubro_no_it"
        if not contract_ok(page_text[:500]):
            log.info("  [FILTRO/CONTRACT] Oferta descartada por contrato temp/PT: '%s'", title)
            print(f"  [FILTRO] Contrato temporal/part-time - saltando: {title}")
            return title, "skipped: contract_temp"

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
        _unanswered_labels: list = []
        if tipo == "directa":
            status, _unanswered_labels = _apply_directa(page, config, profile, job_title=title)
        elif tipo == "modal":
            status, _unanswered_labels = _apply_modal(page, config, profile, job_title=title)
        elif tipo == "externa":
            status = _apply_externa(page, config, profile=profile)
        else:
            status = f"unknown_type:{tipo}"

        # ── Si hubo preguntas desconocidas: guardar en scan_queue para reintento ──
        # pending_questions.json ya tiene las preguntas guardadas (vía form_filler).
        # scan_queue.json permite que --apply-queue las reintente cuando el usuario
        # haya respondido las preguntas pendientes desde el dashboard.
        if status == "skipped: preguntas_sin_respuesta":
            try:
                _save_to_scan_queue(offer_url, title, portal, _unanswered_labels)
                log.info("  [SCAN_QUEUE] Oferta guardada para reintento (%d preguntas): %s",
                         len(_unanswered_labels), title[:55])
                print(f"  [SCAN_QUEUE] Guardada para reintento: {title[:50]}"
                      f" — responde las preguntas en el dashboard y usa 'Relanzar Cola'")
            except Exception as _sqe:
                log.debug("  [SCAN_QUEUE] No se pudo guardar en cola: %s", _sqe)

        return title, status

    except Exception as exc:
        screenshot = take_error_screenshot(page, portal, "offer_error")
        log.error("Error procesando '%s': %s | screenshot: %s", offer_url, exc, screenshot,
                  exc_info=True)
        return title, f"error: {exc}"


# ---------------------------------------------------------------------------
# Detección de login pendiente — fuente única en bot/session_config.py
# ---------------------------------------------------------------------------

from bot.session_config import (
    LOGGED_IN_SIGNALS   as _LOGGED_IN_SIGNALS,
    NOT_LOGGED_IN_SIGNALS as _LOGIN_SIGNALS,
    LOGIN_URLS          as _LOGIN_URLS,
    STEALTH_ARGS        as _STEALTH_ARGS,
    STEALTH_IGNORE_DEFAULT_ARGS as _STEALTH_IGNORE_DEFAULT_ARGS,
    STEALTH_INIT_SCRIPT as _STEALTH_INIT_SCRIPT,
    STEALTH_USER_AGENT  as _STEALTH_UA,
)
from bot.session_checker import is_logged_in_on_page as _is_logged_in_on_page

def _ensure_login(portal_name: str, session_dir: str) -> bool:
    """
    Verifica sesión guardada en playwright_state.json.
    Si existe, continúa. Si no, abre portal en CDP para login manual.
    
    Returns:
      True → sesión verificada (guardar o existente)
      False → saltear portal (usuario canceló o timeout)
    """
    if not SITE_CONFIG.get(portal_name, {}).get("requires_login", False):
        print(f"[SESION_VERIFICADA] {portal_name.upper()} - no requiere login.")
        return True

    state_file = Path(session_dir) / "playwright_state.json"

    # ── Fuente de verdad: el navegador CDP EN VIVO (perfil compartido) ──────
    #
    # ANTES esto se decidía leyendo playwright_state.json en disco — dos
    # variantes, ambas falsas en algún portal:
    #   a) shallow: "¿el archivo tiene cookies?" → sí, pero vencidas
    #      ("[SESION_VERIFICADA] 1131 cookies guardadas" + loop de login
    #      segundos después: "me pide compu pero si esta")
    #   b) check_session(): abre un browser headless AISLADO restaurando
    #      ESE archivo — pero el archivo puede estar desactualizado/sucio
    #      (ver infojobs: 1155 cookies de TODO el perfil compartido, solo
    #      47 propias, vencidas) mientras el navegador CDP que el bot
    #      realmente usa SÍ está logueado ahora mismo → falso "expirado".
    #
    # El navegador que importa es el que el bot usará para escanear/postular
    # — el CDP compartido. Lo abrimos, miramos su DOM real con
    # is_logged_in_on_page (misma función que usa _wait_for_login_if_needed
    # durante el run, cero divergencia), y si está logueado: verificado +
    # autosanamos el snapshot en disco (save_state_only) para que quede
    # sincronizado y check_session/badge del dashboard dejen de mentir.
    try:
        with sync_playwright() as _pw_chk:
            from bot.browser_discovery import select_browser_backend as _sbb
            _backend = _sbb(_pw_chk, session_dir, portal_name=portal_name, headless=False)
            if _backend:
                _pg = _backend.new_page()
                if _pg:
                    _verify_url = _LOGIN_URLS.get(portal_name) or SITE_CONFIG.get(portal_name, {}).get("url_busqueda", "")
                    try:
                        if _verify_url and "linkedin.com/login" not in (_pg.url or ""):
                            # Solo navegar si la pestaña no está ya en el portal —
                            # evita perder el estado de scans en curso.
                            from urllib.parse import urlparse as _up
                            _cur_host = _up(_pg.url or "").netloc.lower()
                            _tgt_host = _up(_verify_url).netloc.lower()
                            if _tgt_host and _tgt_host not in _cur_host:
                                _pg.goto(_verify_url, wait_until="domcontentloaded", timeout=15_000)
                                _pg.wait_for_timeout(2_000)
                    except Exception:
                        pass

                    if _is_logged_in_on_page(_pg, portal_name):
                        print(f"[SESION_VERIFICADA] {portal_name.upper()} - sesión activa en el navegador del bot.")
                        try:
                            _backend.save_state_only(state_file)
                        except Exception:
                            pass
                        _backend.close()
                        return True
                    else:
                        print(f"[SESION_NO_ACTIVA] {portal_name.upper()} - el navegador del bot no está logueado "
                              f"ahora mismo (se pedirá login manual).")
                _backend.close()
    except Exception as _exc:
        log.debug("[SESION_CHECK] %s: error chequeando navegador en vivo: %s", portal_name, _exc)

    # No se pudo confirmar sesión activa en vivo → pedir login manual
    print(f"\n[LOGIN_REQUERIDO] {portal_name.upper()} - sin sesion guardada. Abriendo navegador...")
    print(f"  → Inicia sesion en el navegador. El bot detectara el login automaticamente.")

    # Intentar abrir en CDP sin cerrar contexto
    try:
        with sync_playwright() as pw:
            from bot.browser_discovery import select_browser_backend
            backend = select_browser_backend(
                pw, session_dir, portal_name=portal_name, headless=False
            )
            if not backend:
                print(f"[ERROR] {portal_name.upper()} - no se pudo conectar a Chrome CDP.")
                return False

            page = backend.new_page()
            if not page:
                print(f"[ERROR] {portal_name.upper()} - no se pudo abrir pestana.")
                return False

            # Navegar a login
            login_url = _LOGIN_URLS.get(portal_name)
            if login_url:
                try:
                    page.goto(login_url, wait_until="domcontentloaded", timeout=15_000)
                except Exception:
                    pass

            # Esperar a que el usuario inicie sesión (máx 5 min)
            print(f"[ESPERANDO_LOGIN] {portal_name.upper()} - esperando login (5 min max)...")
            deadline   = time.time() + 300
            last_print = 0.0

            while time.time() < deadline:
                time.sleep(3)
                try:
                    if _is_logged_in_on_page(page, portal_name):
                        print(f"[SESION_INICIADA] {portal_name.upper()} - login detectado, guardando sesion...")
                        backend.save_state_only(state_file)
                        return True
                except Exception:
                    pass

                remaining = int(deadline - time.time())
                if time.time() - last_print >= 30:
                    print(f"[ESPERANDO_LOGIN] {portal_name.upper()} - {remaining}s restantes...")
                    last_print = time.time()

            print(f"[TIMEOUT_LOGIN] {portal_name.upper()} - 5 min sin login. Saltando portal.")
            return False

    except Exception as exc:
        log.warning("[ENSURE_LOGIN] Error en %s: %s", portal_name, exc)
        print(f"[ERROR] {portal_name.upper()} - error verificando sesion: {exc}")
        return False


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
                "403 forbidden" in body.lower() or
                "access denied" in body.lower() or
                "verifique que es un ser humano" in body.lower() or
                "verificación adicional"         in body.lower() or
                ("cloudflare" in body.lower() and len(body) < 3000)
            )
        except Exception:
            return False

    def _is_logged_in() -> bool:
        """
        Usa la función centralizada de session_checker para detectar logeo.
        """
        return _is_logged_in_on_page(page, portal_name)


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

    # -- Paso 1: si hay Cloudflare/verificación humana, NO esperar — nadie       --
    # -- está mirando el navegador en tiempo real. Loguear y seguir de inmediato.--
    if _is_cloudflare():
        log.warning("[CAPTCHA-SKIP] Cloudflare/verificacion detectada en %s — saltando sin esperar.", portal_name.upper())
        print(f"[CAPTCHA-SKIP] {portal_name.upper()}: Cloudflare/verificacion detectada — saltando sin esperar.")
        return

    # -- Paso 2: esperar a que la página termine de cargar antes de verificar ---
    # Portales con requires_login necesitan que el DOM esté completo para
    # detectar correctamente si el botón de login o el avatar del usuario aparece.
    # getonyboard: requires_login=True → verificar sesión LinkedIn guardada antes de postular.
    _PORTALS_FORCE_LOGIN = ("indeed", "linkedin", "chiletrabajos", "computrabajo", "getonyboard")
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
            print(f"\n[SESION_CHECK] {portal_name.upper()}: no se detecto sesion activa. "
                  "Esperando login en el navegador...")

    # -- Hay que hacer login ---------------------------------------------------
    log.warning("LOGIN REQUERIDO en %s", portal_name)
    print(f"\n[LOGIN_REQUERIDO] {portal_name.upper()} - Inicia sesion en el navegador abierto.")

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
        email    = USER_PROFILE.get("laborum_email") or USER_PROFILE.get("email", "")
        password = USER_PROFILE.get("laborum_password", "")
        if email and password:
            log.info("Intentando auto-login en Laborum con %s...", email[:20])
            print(f"[AUTO-LOGIN] Laborum - intentando con {email[:25]}...")
            try:
                # Esperar que el formulario de login esté listo (SPA React)
                try:
                    page.wait_for_selector(
                        "input[type='email'], input[name='email'], input[id*='email']",
                        timeout=8_000
                    )
                except Exception:
                    pass
                human_delay(0.5, 0.8)

                # Rellenar email — varios selectores por si cambia el HTML
                _email_sel = None
                for _s in ["input[type='email']", "input[name='email']",
                           "input[placeholder*='mail']", "input#email"]:
                    try:
                        _el = page.query_selector(_s)
                        if _el and _el.is_visible():
                            _email_sel = _s
                            break
                    except Exception:
                        pass
                if _email_sel:
                    page.fill(_email_sel, email)
                    human_delay(0.4, 0.8)
                else:
                    log.warning("  [laborum-login] campo email no encontrado")

                # Rellenar password
                _pass_sel = None
                for _s in ["input[type='password']", "input[name='password']",
                           "input[id*='password']", "input#password"]:
                    try:
                        _el = page.query_selector(_s)
                        if _el and _el.is_visible():
                            _pass_sel = _s
                            break
                    except Exception:
                        pass
                if _pass_sel:
                    page.fill(_pass_sel, password)
                    human_delay(0.4, 0.8)
                else:
                    log.warning("  [laborum-login] campo password no encontrado")

                # Hacer submit — varios selectores
                _submitted = False
                for _s in [
                    "button[type='submit']",
                    "button:has-text('Ingresar')",
                    "button:has-text('Iniciar sesión')",
                    "button#ingresar",
                    "input[type='submit']",
                ]:
                    try:
                        _el = page.query_selector(_s)
                        if _el and _el.is_visible() and _el.is_enabled():
                            _el.click()
                            human_delay(3.0, 5.0)
                            _submitted = True
                            log.info("  [laborum-login] submit con %r", _s)
                            break
                    except Exception:
                        pass
                if not _submitted:
                    log.warning("  [laborum-login] no se encontró botón submit")

            except Exception as exc:
                log.warning("Fallo auto-login Laborum: %s", exc)
        else:
            if not password:
                log.info("[laborum-login] Sin LABORUM_PASSWORD en .env — esperando login manual")
                print("\n[LABORUM] Sin contrasena configurada. Agrega LABORUM_PASSWORD al .env"
                      " para auto-login, o inicia sesión manualmente en el navegador.")

    # --- Auto-Login GetOnBoard vía LinkedIn OAuth ---
    # GetOnBoard no tiene login por email directo — el camino es LinkedIn OAuth.
    # Intentamos hacer clic en "Ingresa" → "Continuar con LinkedIn" automáticamente.
    # Si LinkedIn ya tiene sesión activa (CDP o sesión guardada), el OAuth completa
    # solo y el bot detecta el login por el bucle de espera abajo.
    if portal_name == "getonyboard":
        try:
            cur_url = page.url
            # Solo intentar si estamos en la página de login
            if "sign_in" in cur_url or "auth" in cur_url or "getonbrd.com" in cur_url:
                _gob_linkedin_btn = None
                # Intentar clic directo en el botón de LinkedIn si ya está visible
                for _sel in [
                    "a:has-text('Continuar con LinkedIn')",
                    "a[href*='linkedin']",
                    "button:has-text('LinkedIn')",
                    ".gb-btn--linkedin",
                ]:
                    try:
                        _el = page.query_selector(_sel)
                        if _el and _el.is_visible():
                            _gob_linkedin_btn = _el
                            break
                    except Exception:
                        pass

                if _gob_linkedin_btn:
                    log.info("[GOB_LOGIN] Haciendo clic en 'Continuar con LinkedIn'...")
                    print("[GOB_LOGIN] Auto-login GetOnBoard via LinkedIn...")
                    _gob_linkedin_btn.click()
                    human_delay(3.0, 5.0)
                else:
                    # Intentar clic en "Ingresa" primero para llegar a los botones OAuth
                    for _ingresa_sel in [
                        "a:has-text('Ingresa')",
                        "a[href*='/auth/sign_in']",
                        "a:has-text('Iniciar sesión')",
                    ]:
                        try:
                            _el = page.query_selector(_ingresa_sel)
                            if _el and _el.is_visible():
                                _el.click()
                                human_delay(2.0, 3.0)
                                # Ahora buscar el botón de LinkedIn
                                for _sel in [
                                    "a:has-text('Continuar con LinkedIn')",
                                    "a[href*='linkedin']",
                                    "button:has-text('LinkedIn')",
                                ]:
                                    try:
                                        _el2 = page.query_selector(_sel)
                                        if _el2 and _el2.is_visible():
                                            log.info("[GOB_LOGIN] Clic en LinkedIn OAuth...")
                                            _el2.click()
                                            human_delay(3.0, 5.0)
                                            break
                                    except Exception:
                                        pass
                                break
                        except Exception:
                            pass
        except Exception as _gob_exc:
            log.debug("[GOB_LOGIN] Error en auto-login: %s", _gob_exc)

    # ── Sesión no activa — esperar login en el browser ya abierto ────────────────
    # Estrategia dual:
    # A) El usuario loguea EN ESTE BROWSER → _is_logged_in() lo detecta por DOM
    # B) El usuario logueó en un BROWSER SEPARADO (🔑 Loguear) → las cookies en disco
    #    cambiaron; el bot recarga la página para cargar la nueva sesión
    _portal_display = portal_name.upper()
    _session_dir    = str(SESSIONS_DIR / portal_name)
    _ck_path        = Path(_session_dir) / "Default" / "Network" / "Cookies"
    _ck_size_start  = _ck_path.stat().st_size if _ck_path.exists() else 0
    _home_url_wf    = SITE_CONFIG.get(portal_name, {}).get("url_busqueda", "") or config.get("url_busqueda", "")

    # Máximo 5 intentos de 60s = 5 min por portal antes de saltarlo y pasar
    # al siguiente — mismo límite que _ensure_login (consistencia: "verificar
    # el login durante 5 min, sino cerrar ciclo y pasar con otro portal").
    # Configurable via MAX_LOGIN_WAIT_ATTEMPTS en .env.
    _MAX_ATTEMPTS = int(os.getenv("MAX_LOGIN_WAIT_ATTEMPTS", "5"))
    attempt = 0
    while not _should_stop() and attempt < _MAX_ATTEMPTS:
        attempt += 1
        print(f"\n[LOGIN_REQUERIDO] {_portal_display} - inicia sesion en el navegador. "
              f"(intento {attempt}/{_MAX_ATTEMPTS})")

        deadline_attempt = time.time() + 60
        while time.time() < deadline_attempt:
            if _should_stop():
                raise TimeoutError(f"{portal_name}: detenido.")
            time.sleep(3)

            # A) Detección DOM en el browser actual
            _detected = False
            try:
                _detected = _is_logged_in()
            except Exception:
                pass
            if _detected:
                log.info("[%s] Login detectado (DOM) tras %d intento(s).", portal_name, attempt)
                try:
                    print(f"[SESION_INICIADA] {_portal_display} - login detectado. Continuando.")
                except Exception:
                    pass
                return

            # B) Cookies en disco cambiaron (login via browser separado)
            try:
                _ck_size_now = _ck_path.stat().st_size if _ck_path.exists() else 0
                if _ck_size_now > _ck_size_start + 5000:  # creció >5KB = nuevas auth cookies
                    log.info("[%s] Cookies actualizadas en disco (%d→%d bytes) — recargando.",
                             portal_name, _ck_size_start, _ck_size_now)
                    try:
                        page.goto(_home_url_wf or _LOGIN_URLS.get(portal_name, ""),
                                  wait_until="domcontentloaded", timeout=15_000)
                        page.wait_for_timeout(3000)
                    except Exception:
                        pass
                    _ck_size_start = _ck_size_now
                    _detected2 = False
                    try:
                        _detected2 = _is_logged_in()
                    except Exception:
                        pass
                    if _detected2:
                        log.info("[%s] Login confirmado tras recarga de cookies.", portal_name)
                        try:
                            print(f"[SESION_INICIADA] {_portal_display} - sesion renovada. Continuando.")
                        except Exception:
                            pass
                        return
            except Exception:
                pass

        if not _should_stop() and attempt < _MAX_ATTEMPTS:
            print(f"[LOGIN_REQUERIDO] {_portal_display} - 60s sin login. Reintentando...")

    if attempt >= _MAX_ATTEMPTS and not _should_stop():
        log.warning("[%s] Max intentos de login (%d) agotados — saltando portal.", portal_name, _MAX_ATTEMPTS)
        print(f"\n[SESION_EXPIRADA] {_portal_display} - {_MAX_ATTEMPTS} intentos sin login. "
              f"Saltando portal. Usa 'Guardar sesion' en el dashboard para renovar.")
        raise TimeoutError(f"{portal_name}: sesion expirada, portal saltado.")

    raise TimeoutError(f"{portal_name}: detenido.")


# ---------------------------------------------------------------------------
# run_bot — función principal pública
# ---------------------------------------------------------------------------

def _run_keyword_loop(
    page, browser, portal_name: str, config: dict, profile: dict,
    max_offers: int, dry_run: bool, rate_limiter, portal_handler,
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
        print(f"\n[PRE-FLIGHT] Verificando sesion en {portal_name.upper()} antes de buscar...")
        try:
            page.goto(home_url, wait_until="domcontentloaded", timeout=20_000)
            human_delay(1.5, 2.5)
            _wait_for_login_if_needed(page, portal_name, config)
        except TimeoutError:
            raise   # sesion expirada → propagar al caller para saltar portal
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
        return 0, []   # BUGFIX: siempre retornar tuple (int, list)

    human_delay(1.5, 2.5)
    # Detectar CAPTCHA inmediatamente después de cargar la página de búsqueda
    try:
        _handle_possible_captcha(page, portal_name)
    except Exception:
        log.debug("_handle_possible_captcha falló (ignorado)")
    # Segunda verificación sobre la URL de búsqueda (puede redirigir a login)
    _wait_for_login_if_needed(page, portal_name, config)

    applied      = 0   # postulaciones totales contadas (directas + externas)
    _direct_applied  = 0   # formularios completados directamente (applied / filled_no_submit)
    _ext_applied     = 0   # links externos abiertos (external_apply) — usuario debe completarlos
    visited      = 0   # ofertas visitadas en total (safety cap para evitar bucle infinito)
    _seen_titles: list[str] = []   # títulos de ofertas vistas — para extracción dinámica de keywords
    _MAX_VISITS = max_offers * 6  # nunca visitar más de 6× la cuota — por si todo es skip
    # Statuses que cuentan como postulación real (definido una vez aquí para ambos paths)
    _REAL_APPLY  = {"applied", "filled_no_submit", "external_apply", "dry_run"}
    _DIRECT_APPLY = {"applied", "filled_no_submit", "dry_run"}
    page_num = 1
    current_listing_url = page.url

    while applied < max_offers and visited < _MAX_VISITS:

        # ── Señal de parada: el usuario hizo clic en Detener ──────────────
        if _should_stop():
            print(f"\n[STOP] Senal de parada detectada. Saliendo del loop de paginas.")
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
            # Intento 2: reusar página existente o abrir una nueva si no hay
            if not recovered:
                try:
                    _existing = browser.pages if hasattr(browser, "pages") else []
                    page = (_existing[0] if _existing else browser.new_page())
                    apply_stealth(page)
                    page.goto(config["url_busqueda"],
                              wait_until="domcontentloaded", timeout=25_000)
                    human_delay(3.0, 5.0)
                    _wait_for_login_if_needed(page, portal_name, config)
                    recovered = True
                    log.info("Página recreada y re-navegada a URL de búsqueda.")
                except TimeoutError:
                    raise   # sesion expirada → propagar
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
                        _existing2 = browser.pages if hasattr(browser, "pages") else []
                        page = (_existing2[0] if _existing2 else browser.new_page())
                        apply_stealth(page)
                        page.goto(config["url_busqueda"],
                                  wait_until="domcontentloaded", timeout=25_000)
                        human_delay(3.0, 5.0)
                        _wait_for_login_if_needed(page, portal_name, config)
                        recovered = True
                    except TimeoutError:
                        raise   # sesion expirada → propagar
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
                    print(f"\n[STOP] Senal de parada detectada entre ofertas. Saliendo.")
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

                # Recuperar página si fue cerrada — reusar existente, no abrir nueva
                try:
                    _ = page.url
                except Exception:
                    log.warning("Página cerrada inesperadamente. Recuperando...")
                    try:
                        _pgs = browser.pages if hasattr(browser, "pages") else []
                        page = _pgs[0] if _pgs else browser.new_page()
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
                    print(f"  [ÉXITO] Postulacion completada para: {title or offer_id}")
                elif status == "error: linkedin_blocked":
                    print(f"\n⛔  LinkedIn bloqueo el bot. Deteniendo LinkedIn por esta sesion.")
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
                is_real   = status in _REAL_APPLY or (isinstance(status, str) and status.startswith("external:"))
                is_direct = status in _DIRECT_APPLY or (isinstance(status, str) and status.startswith("external:"))
                is_ext    = status == "external_apply"
                if is_real:
                    applied += 1
                    if is_ext:
                        _ext_applied += 1
                    elif is_direct:
                        _direct_applied += 1

                # Mostrar conteo separado: directas vs externas (evitar confusión)
                if _ext_applied > 0 and _direct_applied == 0:
                    print(f"  [PROGRESO] Aplicadas {applied}/{max_offers} en {portal_name.upper()} "
                          f"({_ext_applied} externas — completar manualmente)")
                elif _ext_applied > 0:
                    print(f"  [PROGRESO] Aplicadas {applied}/{max_offers} en {portal_name.upper()} "
                          f"({_direct_applied} directas + {_ext_applied} externas)")
                else:
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
            print(f"  [BUSCANDO] Detectadas {len(elements)} ofertas en pagina {page_num}")

            # -- Extraer URLs + score por ubicación (Santiago RM — Maipú primeras) --
            loc_sel = config.get("selector_ubicacion")
            scored: list[tuple[int, str]] = []   # (score, url)

            skipped_sched    = 0
            skipped_geo      = 0
            skipped_exp      = 0
            skipped_practica = 0
            skipped_topic    = 0
            skipped_contract = 0
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

                    # -- Filtro 0: relevancia — descartar si no matchea keywords --
                    if _RELEVANCE_THRESHOLD > 0.0:
                        _kw_labels = [g.get("keyword", "") for g in
                                      config.get("_active_groups", [])]
                        _rscore = _relevance_score(card_text[:200], _kw_labels)
                        if _rscore < _RELEVANCE_THRESHOLD:
                            log.debug("  [RELEVANCIA] Score %.2f < %.2f — descartada",
                                      _rscore, _RELEVANCE_THRESHOLD)
                            continue

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
                    # Usar solo el inicio del card (título) — la descripción puede mencionar
                    # clientes o sectores no-IT de la empresa (ej. agencia de marketing contrata dev)
                    _card_title = card_text[:80].strip().replace("\n", " ")
                    if not topic_ok(_card_title):
                        log.info("  [FILTRO/TOPIC] Descartado (rubro ajeno a IT): %s", _card_title)
                        skipped_topic += 1
                        continue

                    # -- Filtro 5: contrato — descartar part-time/plazo fijo/freelance --
                    if not contract_ok(card_text):
                        log.info("  [FILTRO/CONTRACT] Descartado (contrato temporal/PT): %s",
                                 card_text[:80].strip().replace("\n", " "))
                        skipped_contract += 1
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
                        # Fallback: si no se extrajo loc_text, revisar slug de la URL
                        # (ej. "en-puerto-montt" en la URL de Computrabajo)
                        if score == 5 and not loc_text:
                            score = location_score(href.replace("-", " ").replace("_", " "))
                    # Rechazar según rango de distancia configurado (USER_LOCATION_RANGE)
                    if score < location_min_score():
                        log.info("  [GEO] Rechazada por ubicación fuera de zona: '%s'", (loc_text or href)[:60])
                        skipped_geo += 1
                        continue
                    # Rechazar según modalidad aceptada (USER_ACCEPTED_MODES)
                    if not mode_ok(loc_text):
                        log.info("  [GEO] Rechazada por modalidad no aceptada: '%s'", (loc_text or href)[:60])
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
            if skipped_contract:
                log.info("  %d ofertas descartadas por tipo de contrato", skipped_contract)
                print(f"  [FILTRO] {skipped_contract} ofertas descartadas -> contrato temporal/part-time")

            # Ordenar: mayor score primero (Maipú primeras, luego resto de Santiago)
            scored.sort(key=lambda x: x[0], reverse=True)

            # Loguear si hay reordenamiento visible
            if scored:
                top_score = scored[0][0]
                bot_score = scored[-1][0]
                if top_score != bot_score:
                    print(f"  [GEO] Ofertas Santiago ordenadas (Maipu primeras)"
                          f" (score {top_score}->{bot_score})")

            offer_urls = []
            seen = set()
            for _, url in scored:
                if url not in seen:
                    seen.add(url)
                    offer_urls.append(url)

            for _url_idx, url in enumerate(offer_urls):
                if _should_stop():
                    print(f"\n[STOP] Senal de parada detectada entre URLs. Saliendo.")
                    sys.exit(0)

                if deadline and time.time() > deadline:
                    print(f"\n[TIEMPO_KW] Tiempo por keyword agotado. Siguiente keyword.")
                    break

                if applied >= max_offers or visited >= _MAX_VISITS:
                    # Guardar ofertas sobrantes en quick_links para postulación rápida manual
                    _remaining = offer_urls[_url_idx:]
                    _queued_now = 0
                    for _rem_url in _remaining:
                        if not already_applied(_rem_url):
                            _save_quick_link(_rem_url, "", portal_name)
                            _queued_now += 1
                    if _queued_now:
                        print(f"  [QUICK] {_queued_now} ofertas guardadas en panel de postulacion rapida.")
                    break
                if already_applied(url):
                    log.debug("  [skip-db] %s", url)
                    continue

                print(f"  [ABRIENDO] Navegando a oferta: {url[:60]}...")
                title, status = _process_offer_generic(
                    page, url, config, profile, portal_name, dry_run
                )

                visited += 1  # siempre cuenta visita

                # Deduplicación cross-portal: saltar si ya se vio el mismo título
                # en otro portal durante los últimos 60 días
                if title and title not in ("unknown", ""):
                    try:
                        from .dedup import is_duplicate, mark_seen
                        if is_duplicate(title):
                            log.info("  [DEDUP] Oferta ya vista en otro portal: '%s'", title[:55])
                            print(f"  [DEDUP] Ya postulado en otro portal: {title[:55]}")
                            continue
                        mark_seen(title, portal=portal_name)
                    except Exception as _ded_err:
                        log.debug("[DEDUP] Error: %s", _ded_err)
                # Recolectar título para extracción dinámica de keywords
                if title and title not in ("unknown", ""):
                    _seen_titles.append(title)

                if status == "applied":
                    print(f"  [ÉXITO] Postulacion completada para: {title or 'Sin Titulo'}")
                elif status.startswith("error"):
                    print(f"  [FALLO] Error en {title or 'Sin Titulo'}: {status}")

                log.info("  [OK] [%s] %s -> %s", portal_name, title, status)
                save_application(url, portal_name, title, status)
                _csv_log(portal_name, url, title, status)

                # Guardar quick link (todos los que pasan filtros)
                if title and title != "unknown":
                    _save_quick_link(url, title, portal_name)

                # Contar solo postulaciones REALES (no skips ni errores)
                is_real   = status in _REAL_APPLY or (isinstance(status, str) and status.startswith("external:"))
                is_direct = status in _DIRECT_APPLY or (isinstance(status, str) and status.startswith("external:"))
                is_ext    = status == "external_apply"
                if is_real:
                    applied += 1
                    if is_ext:
                        _ext_applied += 1
                    elif is_direct:
                        _direct_applied += 1

                if _ext_applied > 0 and _direct_applied == 0:
                    print(f"  [PROGRESO] Aplicadas {applied}/{max_offers} en {portal_name.upper()} "
                          f"({_ext_applied} externas — completar manualmente)")
                elif _ext_applied > 0:
                    print(f"  [PROGRESO] Aplicadas {applied}/{max_offers} en {portal_name.upper()} "
                          f"({_direct_applied} directas + {_ext_applied} externas)")
                else:
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

    # -- Verificación final en "Mis postulaciones" --------------------------------
    if applied > 0:
        confirmed = _verify_portal_applications(page, portal_name, applied, _seen_titles)
        if confirmed < applied:
            print(f"  [VERIFICACION] ⚠ {portal_name.upper()}: bot contó {applied} postulaciones "
                  f"pero solo se confirman {confirmed} en el historial del portal.")
            log.warning("[VERIFY] %s: tracked=%d confirmed=%d — discrepancia detectada",
                        portal_name, applied, confirmed)
            applied = confirmed  # ajustar contador al valor verificado
        else:
            print(f"  [VERIFICACION] ✓ {portal_name.upper()}: {confirmed} postulaciones confirmadas "
                  f"en historial del portal.")

    return applied, _seen_titles


def _verify_portal_applications(page, portal_name: str, tracked: int,
                                applied_titles: list) -> int:
    """
    Navega a la página "Mis postulaciones" del portal y cuenta cuántas aplicaciones
    REALMENTE ENVIADAS hay visibles (excluye borradores y "por enviar").

    Retorna el número CONFIRMADO de postulaciones en el historial del portal.
    Si la navegación falla o no se puede parsear, retorna `tracked` (sin penalizar).
    """
    from .session_config import MY_APPLICATIONS_URLS, MY_APPLICATIONS_CARD_SELECTORS

    url = MY_APPLICATIONS_URLS.get(portal_name)
    sel = MY_APPLICATIONS_CARD_SELECTORS.get(portal_name)
    if not url or not sel:
        log.debug("[VERIFY] %s: sin URL de 'mis postulaciones' configurada", portal_name)
        return tracked

    try:
        print(f"  [VERIFICACION] Chequeando historial de postulaciones en {portal_name.upper()}...")
        page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        page.wait_for_timeout(2_500)

        # -- GetOnBoard: manejo especial por sección "Por enviar" vs "Enviadas" --
        if portal_name == "getonyboard":
            return _verify_gob_sent_applications(page, tracked)

        # -- Laborum: styled-components → no hay selector CSS estable, usar JS --
        if portal_name == "laborum":
            return _verify_laborum_sent_applications(page, tracked)

        # Contar tarjetas de postulación visibles en la primera página
        cards = page.query_selector_all(sel)
        count_on_page = len([c for c in cards if c.is_visible()])

        if count_on_page == 0:
            generic_cards = page.query_selector_all(
                "li[class*='appl'], div[class*='appl'], article[class*='appl'], "
                "div[class*='postul'], li[class*='postul']"
            )
            count_on_page = len([c for c in generic_cards if c.is_visible()])

        log.info("[VERIFY] %s: %d tarjetas visibles en mis-postulaciones (bot contó %d)",
                 portal_name, count_on_page, tracked)

        if count_on_page == 0:
            log.debug("[VERIFY] %s: 0 tarjetas — asumiendo tracked=%d correcto", portal_name, tracked)
            return tracked

        if count_on_page >= tracked:
            return tracked
        else:
            return count_on_page

    except Exception as exc:
        log.debug("[VERIFY] %s: error al verificar mis-postulaciones: %s", portal_name, exc)
        return tracked


def _verify_gob_sent_applications(page, tracked: int) -> int:
    """
    Verifica en https://www.getonbrd.com/applications cuántas postulaciones
    tienen estado ENVIADA (excluye INCOMPLETA y POR ENVIAR = borradores).

    Estructura DOM confirmada en vivo (2026-06-09):
      - Filas: tr.border-bottom.border-semi-transparent
      - Celda de estado: td.ja-status  → texto "ENVIADA" | "INCOMPLETA" | "POR ENVIAR"

    Retorna el número de filas con td.ja-status == "ENVIADA".
    """
    try:
        result = page.evaluate("""
            () => {
                const rows = document.querySelectorAll(
                    'tr.border-bottom.border-semi-transparent'
                );
                let enviadas = 0, incompletas = 0, porEnviar = 0;
                for (const row of rows) {
                    const statusCell = row.querySelector('td.ja-status');
                    if (!statusCell) continue;
                    const status = (statusCell.innerText || '').trim().toUpperCase();
                    if (status === 'ENVIADA')     enviadas++;
                    else if (status === 'INCOMPLETA') incompletas++;
                    else if (status.includes('ENVIAR')) porEnviar++;
                }
                return { enviadas, incompletas, porEnviar, total: rows.length };
            }
        """)

        enviadas   = result.get("enviadas", 0)
        incompletas = result.get("incompletas", 0)
        por_enviar = result.get("porEnviar", 0)
        total      = result.get("total", 0)

        print(f"  [VERIFICACION GOB] {enviadas} ENVIADAS · {incompletas} INCOMPLETAS · "
              f"{por_enviar} POR ENVIAR (de {total} filas totales)")
        log.info("[VERIFY] GOB: enviadas=%d incompletas=%d por_enviar=%d total=%d tracked=%d",
                 enviadas, incompletas, por_enviar, total, tracked)

        if total == 0:
            # Página no cargó correctamente — no penalizar
            log.debug("[VERIFY] GOB: 0 filas detectadas — asumiendo tracked=%d correcto", tracked)
            return tracked

        # Solo contar las ENVIADAS que coinciden con lo que el bot registró en esta sesión.
        # Si hay más ENVIADAS que lo tracked, es historial anterior → retornar tracked.
        return tracked if enviadas >= tracked else enviadas

    except Exception as exc:
        log.debug("[VERIFY] GOB: error en verificación: %s", exc)
        return tracked


def _verify_laborum_sent_applications(page, tracked: int) -> int:
    """
    Verifica en https://www.laborum.cl/postulantes/postulaciones cuántas
    postulaciones hay registradas.

    Laborum usa styled-components (clases sc-* dinámicas) — selector CSS no estable.
    Estructura DOM confirmada en vivo (2026-06-09):
      - Tarjetas individuales: div con 3 hijos y texto que incluye "Postulado el"
      - Estados visibles: "CV enviado", "CV leído"

    Retorna el total de tarjetas detectadas (todas son enviadas, no hay borradores).
    """
    try:
        # Scroll para activar lazy-load
        page.evaluate("window.scrollTo(0, 500)")
        page.wait_for_timeout(1_500)

        result = page.evaluate("""
            () => {
                const allDivs = Array.from(document.querySelectorAll('div'));
                const cards = allDivs.filter(el => {
                    const txt = (el.innerText || '').trim();
                    const lines = txt.split('\\n').filter(l => l.trim().length > 0);
                    return txt.includes('Postulado el') && lines.length >= 2 && lines.length <= 8
                           && el.childElementCount === 3;
                });
                return { total: cards.length };
            }
        """)

        total = result.get("total", 0)
        print(f"  [VERIFICACION LABORUM] {total} postulaciones confirmadas en historial")
        log.info("[VERIFY] LABORUM: total=%d tracked=%d", total, tracked)

        if total == 0:
            log.debug("[VERIFY] LABORUM: 0 tarjetas — asumiendo tracked=%d correcto", tracked)
            return tracked

        return tracked if total >= tracked else total

    except Exception as exc:
        log.debug("[VERIFY] LABORUM: error en verificación: %s", exc)
        return tracked


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
        # Detectar CAPTCHA al abrir una oferta
        try:
            _handle_possible_captcha(page, portal)
        except Exception:
            log.debug("_handle_possible_captcha falló al abrir oferta (ignorado)")

        page_text = (page.text_content("body") or "").lower()
        _err = ["404", "no encontramos", "not found", "oferta no disponible", "oferta eliminada"]
        if any(s in page_text for s in _err):
            return title, "skipped: eliminada", []

        if _country_mismatch_blocks_apply(page_text):
            log.info("  [SCAN-SKIP] Pais no coincide y oferta es presencial: %s", offer_url)
            return title, "skipped: pais_no_coincide", []

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

        # Para portales externos (GetOnBoard, Trabajando) y de redirect (InfoJobs):
        # Intentar seguir el link de aplicación para escanear el ATS externo.
        # Si el ATS es simple (Greenhouse, Lever, formularios genéricos) → escanear preguntas.
        # Si falla → guardar como quick_link para postulación manual.
        if tipo in ("externa", "external", "redirect"):
            btn_sel = config.get("selector_boton_aplicar", "")
            ext_unanswered = []
            scanned_ok = False

            if btn_sel:
                try:
                    btn = page.query_selector(btn_sel.split(",")[0].strip())
                    if btn and btn.is_visible():
                        opens_new_tab = (btn.get_attribute("target") or "").strip() == "_blank"
                        if opens_new_tab:
                            # Capturar nueva pestaña ATS externo
                            try:
                                with page.context.expect_page(timeout=8_000) as np_info:
                                    btn.click()
                                ext_page = np_info.value
                                ext_page.wait_for_load_state("domcontentloaded", timeout=15_000)
                                ext_url = ext_page.url
                                # Saltar ATSs complejos
                                _SKIP = ("workday", "taleo", "successfactors", "brassring",
                                         "icims", "oraclecloud", "myworkdayjobs",
                                         "linkedin.com", "indeed.com", "login", "signin", "register")
                                if not any(s in ext_url.lower() for s in _SKIP):
                                    from .form_filler import scan_form as _scan_form_ext
                                    ext_result = _scan_form_ext(ext_page, USER_PROFILE, job_title=title)
                                    ext_unanswered = ext_result.get("unanswered", [])
                                    scanned_ok = True
                                    log.info("  [SCAN-EXT] %d preguntas en ATS %s",
                                             len(ext_unanswered), ext_url[:50])
                                try:
                                    ext_page.close()
                                except Exception:
                                    pass
                            except Exception as tab_exc:
                                log.debug("  [SCAN-EXT] No se pudo abrir nueva pestaña: %s", tab_exc)
                        else:
                            # Click sin nueva pestaña — formulario inline o redirección
                            btn.click()
                            human_delay(1.0, 1.8)
                            try:
                                page.wait_for_load_state("domcontentloaded", timeout=8_000)
                            except Exception:
                                pass
                            from .form_filler import scan_form as _scan_form_ext
                            ext_result = _scan_form_ext(page, USER_PROFILE, job_title=title)
                            ext_unanswered = ext_result.get("unanswered", [])
                            scanned_ok = True
                except Exception as ext_exc:
                    log.debug("  [SCAN-EXTERNA] Error siguiendo botón: %s", ext_exc)

            if scanned_ok and ext_unanswered:
                log.info("  [SCAN-EXTERNA] Preguntas sin respuesta para '%s': %s",
                         title[:50], ext_unanswered[:3])
                return title, "queued", ext_unanswered

            # Sin preguntas abiertas O no se pudo escanear → quick_link
            if _is_bodega_job(title):
                log.info("  [SCAN-EXTERNA-BODEGA] %s — quick link", title[:55])
            else:
                log.info("  [SCAN-EXTERNA-IT] %s — quick link", title[:55])
            return title, "bodega_quick_link", []

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

        result = scan_form(page, profile, job_title=title, portal=portal, url=offer_url)

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
        page.wait_for_selector(sel, timeout=12_000)
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
    print(f"\n[SCAN] Pasada 1 en {portal_name.upper()} - sin postular, solo recolectando\n")

    # Pre-login: garantiza sesión antes de abrir el browser principal.
    if not _ensure_login(portal_name, session_dir):
        log.warning("[SCAN] Login cancelado para %s — detenido.", portal_name)
        return

    with sync_playwright() as pw:
        backend = select_browser_backend(
            pw,
            session_dir,
            headless=headless,
            user_agent=_STEALTH_UA,
            args=_STEALTH_ARGS + ["--disable-popup-blocking"],
            ignore_default_args=_STEALTH_IGNORE_DEFAULT_ARGS,
            locale="es-CL",
            timezone_id="America/Santiago",
            portal_name=portal_name,
        )
        if backend is None:
            log.warning("[SCAN] No se pudo inicializar backend de navegador")
            return

        page = backend.new_page()
        if page is None:
            backend.close()
            return

        page.add_init_script(_STEALTH_INIT_SCRIPT)
        apply_stealth(page)

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
                if not contract_ok(card_text):
                    skipped_f += 1; continue
                loc_text = card_text
                score = location_score(loc_text)
                # Rechazar según rango de distancia configurado (USER_LOCATION_RANGE)
                if score < location_min_score():
                    skipped_geo += 1; continue
                # Rechazar según modalidad aceptada (USER_ACCEPTED_MODES)
                if not mode_ok(loc_text):
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
                    print(f"  [🏭 BODEGA] Guardado para postulacion manual: {title[:50]}")
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
                    print(f"  [KW_SCAN] Nueva combinacion detectada: '{nk['keyword']}'")

            # Registrar resultado en optimizer: found = raw offers antes de filtros
            new_kws = process_keyword_result(keyword, portal_name, applied=0, found=len(raw_urls))
            if new_kws:
                scan_groups.extend(new_kws)
                print(f"  [KW_RETIRE] '{keyword}' retirada (0 ofertas) → {len(new_kws)} reemplazos")

        backend.close()
        # Portales con login: PRESERVAR la sesión (cookies válidas = recurso valioso)
        # Portales públicos (sin login): descartar (solo cache del browser, sin valor)
        if SITE_CONFIG.get(portal_name, {}).get("requires_login"):
            _maybe_keep_session(session_dir, portal_name, applied=0)
        else:
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
        print(f"[APPLY-QUEUE] 🗑  {pruned_age} entradas expiradas (>5 dias) eliminadas automaticamente.")

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
        print(f"[APPLY-QUEUE] {discarded_topic} item(s) eliminados por no ser IT.")

    if not queue:
        print(f"[APPLY-QUEUE] Sin ofertas IT en cola para {portal_name.upper()}.")
        print(f"  Corre primero: python main.py --portal {portal_name} --scan")
        return

    print(f"\n[APPLY-QUEUE] {len(queue)} ofertas en cola para {portal_name.upper()}")
    rate_limiter = get_rate_limiter(portal_name)
    session_dir  = str(SESSIONS_DIR / portal_name)
    Path(session_dir).mkdir(exist_ok=True)

    # Pre-login: garantiza sesión antes de abrir el browser.
    if not _ensure_login(portal_name, session_dir):
        log.warning("[LOGIN] Login cancelado para %s — detenido.", portal_name)
        return

    applied       = 0
    still_pending = 0
    errors        = 0
    total_skipped = 0

    _aq_max = SITE_CONFIG.get(portal_name, {}).get("max_offers_per_run", 5)
    _env_max_aq = os.getenv("USER_MAX_OFFERS", "").strip()
    if _env_max_aq.isdigit():
        _aq_max = min(_aq_max, int(_env_max_aq))

    with sync_playwright() as pw:
        backend = select_browser_backend(
            pw,
            session_dir,
            headless=headless,
            user_agent=_STEALTH_UA,
            args=_STEALTH_ARGS + ["--disable-popup-blocking"],
            ignore_default_args=_STEALTH_IGNORE_DEFAULT_ARGS,
            locale="es-CL",
            timezone_id="America/Santiago",
            portal_name=portal_name,
        )
        if backend is None:
            log.warning("[APPLY-QUEUE] No se pudo inicializar backend de navegador")
            return

        page = backend.new_page()
        if page is None:
            backend.close()
            return

        page.add_init_script(_STEALTH_INIT_SCRIPT)
        apply_stealth(page)

        base_config = dict(SITE_CONFIG[portal_name])
        try:
            page.goto(base_config["url_busqueda"], wait_until="domcontentloaded", timeout=20_000)
            human_delay(1.5, 2.5)
            _wait_for_login_if_needed(page, portal_name, base_config)
        except Exception as e:
            log.warning("Error login apply-queue: %s", e)

        for entry in queue:
            if applied >= _aq_max:
                remaining_in_queue = len(queue) - applied - errors - total_skipped - still_pending
                print(f"\n[APPLY-QUEUE] Limite {_aq_max} postulaciones alcanzado - "
                      f"{max(0, remaining_in_queue)} ofertas siguen en cola para el próximo run.")
                break

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
                    print(f"-> [LOGIN_REQUERIDO] Sesion expirada - re-logueate y vuelve a correr")
                    errors += 1
                    continue
                if any(s in cur_url.lower() or s in cur_text for s in _dead_signals):
                    print(f"-> [OFERTA_CERRADA] Eliminada de cola automaticamente")
                    _remove_from_scan_queue(url)
                    _csv_log(portal_name, url, title, "skipped: oferta_cerrada")
                    total_skipped += 1
                    continue

                if _country_mismatch_blocks_apply(cur_text):
                    print(f"-> [PAIS_NO_COINCIDE] Presencial en otro pais (no hibrido/remoto) - eliminada de cola")
                    _remove_from_scan_queue(url)
                    _csv_log(portal_name, url, title, "skipped: pais_no_coincide")
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
                            print(f"-> ⚠️  {reason} - eliminada de cola tras 2 fallos")
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
                        # Esperar hasta 4s a que el botón sea visible (carga dinámica)
                        page.wait_for_selector(btn_sel, timeout=4_000, state="visible")
                        btn = page.query_selector(btn_sel)
                        if btn and btn.is_visible():
                            btn.scroll_into_view_if_needed()
                            btn.click()
                            human_delay(0.8, 1.5)
                        else:
                            log.debug("[APPLY-QUEUE] Botón aplicar no visible: %s", url[:50])
                    except Exception:
                        log.debug("[APPLY-QUEUE] Botón aplicar no visible: %s", url[:50])

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
                        print(f"-> ⚠️  {reason} - eliminada de cola tras 2 fallos")
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
                    print(f"-> ❌ Error - eliminada de cola tras 2 fallos ({str(exc)[:40]})")
                else:
                    print(f"-> ❌ Error (fallo 1/2): {str(exc)[:50]}")

        backend.close()
        _maybe_keep_session(session_dir, portal_name, applied)

    remaining = len(_load_scan_queue())
    print(f"\n[APPLY-QUEUE] Resultado:")
    print(f"  ✅ Postuladas          : {applied}")
    print(f"  ⏳ Pendientes          : {still_pending}")
    print(f"  🗑  Cerradas/eliminadas : {total_skipped}")
    print(f"  ❌ Errores             : {errors}")
    if remaining:
        print(f"\n  Quedan en cola: {remaining} - responde el panel naranja y vuelve a correr --apply-queue")


# ── Límite de tiempo por portal ──────────────────────────────────────────────
# Si al terminar todas las keywords el portal tiene 0 postulaciones,
# se hace UN reintento obligatorio (espera _RETRY_WAIT_INITIAL segundos).
# Esto garantiza que el bot no "pasa de largo" sin haber postulado ni una vez.
# En modo no_retry (persistente) el reintento se omite — el orquestador externo
# ya maneja el ciclo entre portales.
MAX_PORTAL_MINUTES   = int(os.getenv("MAX_PORTAL_MINUTES", "25"))
_RETRY_WAIT_INITIAL  = 300   # 5 min de espera antes del reintento obligatorio
_RETRY_MAX_COUNT     = 1     # máximo 1 reintento (= 2 intentos en total)


def _retry_countdown(wait_secs: int, portal: str) -> None:
    """
    Cuenta regresiva visible en dashboard.
    Chequea señal de parada cada 2s; imprime mensaje cada 60s.
    """
    remaining = wait_secs
    since_last_print = 0
    while remaining > 0:
        if _should_stop():
            print(f"\n[STOP] Senal detectada durante espera de retry. Saliendo.")
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


def run_bot_multi_keywords(
    portal_name: str,
    dry_run: bool = False,
    headless: bool = False,
    no_retry: bool = False,
) -> int:
    """
    Abre el browser UNA VEZ y ejecuta una búsqueda por cada keyword del KEYWORD_GROUPS.
    Al compartir el contexto del browser, Cloudflare/login solo se resuelve una vez.

    no_retry=True: desactiva el retry interno (esperas de 15→10→5 min).
    Úsalo en modo persistente donde el loop externo maneja los reintentos
    pasando al siguiente portal en lugar de esperar.

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
        print(f"\n[STANDBY] {portal_name.upper()}: portal en standby por proteccion anti-bot — saltando sin esperar.")
        return

    # -- Verificar restricción temporal de LinkedIn ---------------------------
    if portal_name == "linkedin":
        _restr_path = BASE_DIR / "data" / "portal_restrictions.json"
        _restr = {}
        if _restr_path.exists():
            try:
                with open(_restr_path, encoding="utf-8") as _f:
                    _restr = json.load(_f)
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
                    print(f"\n[LINKEDIN] Restriccion vencida - reanudando con cautela.")
            except Exception:
                pass

    run_startup_validation(portal_name, USER_PROFILE, SITE_CONFIG[portal_name])

    rate_limiter = get_rate_limiter(portal_name)

    _PORTAL_LOCALE = {
        # Portales Chile — navegador en español para evitar detección
        "indeed":        ("es-CL", "America/Santiago"),
        "laborum":       ("es-CL", "America/Santiago"),
        "getonyboard":   ("es-CL", "America/Santiago"),
        "computrabajo":  ("es-CL", "America/Santiago"),
        "linkedin":      ("es-CL", "America/Santiago"),
        "chiletrabajos": ("es-CL", "America/Santiago"),
        "trabajando":    ("es-CL", "America/Santiago"),
        "infojobs":      ("es-CL", "America/Santiago"),
        # Portales remotos internacionales — navegador en inglés
        "weworkremotely": ("en-US", "America/New_York"),
        "remotive":       ("en-US", "America/New_York"),
        "remoteco":       ("en-US", "America/New_York"),
    }
    _locale, _tz = _PORTAL_LOCALE.get(portal_name, ("es-CL", "America/Santiago"))

    session_dir = str(SESSIONS_DIR / portal_name)
    Path(session_dir).mkdir(exist_ok=True)

    _run_start = time.time()   # para calcular duración al finalizar

    # ── Limpiar entradas stale del scan_queue (>5 días) para este portal ──────
    _sq_path = BASE_DIR / "data" / "scan_queue.json"
    if _sq_path.exists():
        try:
            with open(_sq_path, encoding="utf-8") as _sqf:
                _sq = json.load(_sqf)
            _cutoff = time.time() - 5 * 86400   # 5 días en segundos
            _sq_before = len(_sq)
            _sq = [e for e in _sq
                   if e.get("portal", "") != portal_name or e.get("ts", time.time()) >= _cutoff]
            _sq_portal = [e for e in _sq if e.get("portal", "") == portal_name]
            if len(_sq) != _sq_before:
                with open(_sq_path, "w", encoding="utf-8") as _sqf:
                    json.dump(_sq, _sqf, ensure_ascii=False, indent=2)
                log.info("[SCAN_QUEUE] %s: %d entradas stale eliminadas.",
                         portal_name, _sq_before - len(_sq))
            if _sq_portal:
                print(f"\n[SCAN_QUEUE] {portal_name.upper()}: {len(_sq_portal)} ofertas en cola "
                      f"de sesiones anteriores — se procesarán tras la búsqueda.")
        except Exception as _sqe:
            log.warning("[SCAN_QUEUE] Error limpiando cola: %s", _sqe)

    log.info("=== ApplyJob Bot (Multi-Keyword — browser compartido) ===")
    log.info("Portal: %s | grupos: %d", portal_name, len(KEYWORD_GROUPS))

    # Pre-login: garantiza sesión antes de abrir el browser principal.
    # Reintenta en ventanas de 60s hasta que el usuario loguee o detenga el bot.
    if not _ensure_login(portal_name, session_dir):
        log.warning("[LOGIN] Login cancelado para %s — detenido.", portal_name)
        return 0

    with sync_playwright() as pw:
        browser_backend = select_browser_backend(
            pw,
            session_dir,
            headless=headless,
            user_agent=_STEALTH_UA,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            ignore_default_args=_STEALTH_IGNORE_DEFAULT_ARGS,
            locale=_locale,
            timezone_id=_tz,
            portal_name=portal_name,
        )
        if browser_backend is None:
            log.warning("[BOT] No se pudo inicializar backend de navegador")
            return 0

        page = browser_backend.new_page()
        if page is None:
            browser_backend.close()
            return 0

        browser_ctx = browser_backend.context
        apply_stealth(page)
        try:
            from playwright_stealth import Stealth
            Stealth().apply_stealth_sync(page)
            log.info("playwright-stealth activo")
        except (ImportError, Exception) as se:
            log.warning("playwright-stealth no disponible (%s) — usando stealth manual", se)

        total_applied     = 0
        _total_max_target = 0      # suma de max_offers de cada keyword (para referencia interna)
        # Límite EFECTIVO de la sesión completa (global, no por keyword)
        _env_max_str = os.getenv("USER_MAX_OFFERS", "").strip()
        _effective_max = (int(_env_max_str) if _env_max_str.isdigit()
                          else SITE_CONFIG.get(portal_name, {}).get("max_offers_per_run", 5))

        # Límites por modo (IT / Bodega). Si no están definidos se usa _effective_max sin distinción.
        _env_max_it  = os.getenv("USER_MAX_IT", "").strip()
        _env_max_bod = os.getenv("USER_MAX_BODEGA", "").strip()
        _mode_max: dict[str, int] = {}
        if _env_max_it.isdigit():
            _mode_max["it"] = int(_env_max_it)
        if _env_max_bod.isdigit():
            _mode_max["bodega"] = int(_env_max_bod)
        # Si se define al menos uno, el total efectivo es la suma de los modos definidos
        if _mode_max:
            _effective_max = sum(_mode_max.values())
        _mode_applied: dict[str, int] = {}   # aplicadas por modo en esta sesión

        _session_verified = False  # Se activa tras el primer pre-flight exitoso
        _kw_count_for_stats = 0    # contador de keywords procesados en esta sesión
        _found_count_for_stats = 0 # contador de ofertas encontradas en esta sesión
        _portal_end_reason = "completed"   # motivo de fin del portal para session_stats

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

        # ── Intercalar IT y bodega para garantizar cobertura de ambos modos ──
        # Sin esto, con _effective_max=5 el bot siempre aplica en IT y nunca
        # llega a las keywords de bodega (que están al final del array).
        _it_groups     = [g for g in active_groups if g.get("mode") == "it"]
        _bodega_groups = [g for g in active_groups if g.get("mode") == "bodega"]
        _other_groups  = [g for g in active_groups if g.get("mode") not in ("it", "bodega")]
        if _it_groups and _bodega_groups:
            # Si no se definieron límites por modo: el máximo configurado
            # aplica COMPLETO a cada categoría (no se reparte 50/50).
            # "si son 5 entonces 5 IT y 5 bodega" → total real = 10.
            if not _mode_max:
                _mode_max["it"] = _effective_max
                _mode_max["bodega"] = _effective_max
                _effective_max = sum(_mode_max.values())
                print(
                    f"[MODO_BALANCE] Asignando {_mode_max['it']} IT y {_mode_max['bodega']} BODEGA "
                    f"(máximo independiente por categoría — total combinado {_effective_max})."
                )
            # Shuffle IT para rotar keywords cada ciclo (evita usar siempre los mismos 5)
            import random as _rnd
            _rnd.shuffle(_it_groups)
            # Intercalar: it[0], bodega[0], it[1], bodega[1], ...
            _interleaved = []
            for i in range(max(len(_it_groups), len(_bodega_groups))):
                if i < len(_it_groups):
                    _interleaved.append(_it_groups[i])
                if i < len(_bodega_groups):
                    _interleaved.append(_bodega_groups[i])
            active_groups = _interleaved + _other_groups
            print(f"[CICLO] Modo alternado (IT shuffled): {len(_it_groups)} IT + {len(_bodega_groups)} bodega intercalados")
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

        # ── Retry loop: reintento obligatorio si 0 postulaciones ────────────────
        # Si al terminar todas las keywords el portal tiene 0 postulaciones,
        # se hace UN reintento (espera _RETRY_WAIT_INITIAL segundos).
        # En modo no_retry (persistente) el reintento se omite.
        _retry_count = 0

        while True:   # retry loop — sale con break en éxito o sin más reintentos

            # ── Chequeo de señal de parada (antes de cada intento) ────────────
            if _should_stop():
                print(f"\n[STOP] Senal de parada detectada. Cerrando {portal_name.upper()} limpiamente.")
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
                    print(f"\n[STOP] Senal de parada detectada en loop de keywords. "
                          f"Cerrando {portal_name.upper()} limpiamente.")
                    sys.exit(0)

                # Límite total de sesión (seguridad — en caso de keywords dinámicas)
                _elapsed = time.time() - _portal_start
                if _elapsed >= _session_max_minutes * 60:
                    print(f"\n[TIEMPO] {portal_name.upper()}: limite de sesion "
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

                # Verificar límite por modo (IT / Bodega) si está configurado
                if mode in _mode_max:
                    _mode_done = _mode_applied.get(mode, 0)
                    _mode_limit = _mode_max[mode]
                    if _mode_done >= _mode_limit:
                        log.debug("[MODO] %s: límite %s alcanzado (%d/%d). Saltando keyword '%s'.",
                                  portal_name, mode.upper(), _mode_done, _mode_limit, keyword)
                        continue

                config  = build_config_for_keyword(portal_name, keyword)
                profile = dict(USER_PROFILE)
                profile["_mode"] = mode
                max_offers = config.get("max_offers_per_run", 10)
                _total_max_target += max_offers

                # Limitar por presupuesto restante del límite global de sesión
                _remaining_budget = max(0, _effective_max - total_applied)
                if _remaining_budget == 0:
                    print(f"  [LIMITE_PORTAL] Limite global {_effective_max} alcanzado. "
                          f"Saltando keyword '{keyword}'.")
                    break
                # Limitar también por presupuesto del modo
                if mode in _mode_max:
                    _remaining_mode = max(0, _mode_max[mode] - _mode_applied.get(mode, 0))
                    max_offers = min(max_offers, _remaining_mode)
                max_offers = min(max_offers, _remaining_budget)

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
                    log.warning("Pagina cerrada/no responde entre keywords. Reutilizando existente...")
                    try:
                        # Reusar primera página existente — no abrir nueva tab en Chrome
                        _existing_pages = browser_ctx.pages if hasattr(browser_ctx, "pages") else []
                        page = _existing_pages[0] if _existing_pages else browser_ctx.new_page()
                        apply_stealth(page)
                    except Exception as page_err:
                        log.error("No se pudo recuperar pagina: %s", page_err)
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
                            try:
                                with open(_restr_path, encoding="utf-8") as _f:
                                    _restr = json.load(_f)
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
                try:
                    applied, seen_titles = _run_keyword_loop(
                        page, browser_ctx, portal_name, config, profile,
                        max_offers, dry_run, rate_limiter, portal_handler,
                        session_verified=_session_verified,
                        deadline=_kw_deadline,
                    )
                except TimeoutError as _te:
                    log.error("[LOGIN_TIMEOUT] %s: %s", portal_name, _te)
                    # ── Sesión expirada en modo headless: abrir ventana visible para login ──
                    # Usamos el pw ya activo (no anidamos sync_playwright):
                    #   1. Cerrar contexto headless (libera lock del session_dir)
                    #   2. Abrir contexto NO-headless con el mismo pw → ventana visible
                    #   3. Esperar login via DOM (máx 5 min)
                    #   4. Cerrar ventana visible
                    #   5. Reabrir headless y reintentar keyword
                    if headless:
                        print(f"\n[SESION_NUEVA] {portal_name.upper()} - sesion expirada.")
                        print(f"[SESION_NUEVA] Abriendo navegador para LOGIN MANUAL en {portal_name.upper()}.")
                        print(f"[SESION_NUEVA] Inicia sesion en el navegador y el bot continuara automaticamente.")

                        # 1. Cerrar headless
                        try:
                            browser_ctx.close()
                        except Exception:
                            pass

                        # 2. Abrir visible con el mismo pw
                        _login_url = _LOGIN_URLS.get(portal_name,
                                                      SITE_CONFIG.get(portal_name, {}).get("url_busqueda", ""))
                        _login_ok  = False
                        try:
                            _vis_backend = select_browser_backend(
                                pw, session_dir,
                                headless=False,
                                user_agent=_STEALTH_UA,
                                args=_STEALTH_ARGS + ["--start-maximized"],
                                ignore_default_args=_STEALTH_IGNORE_DEFAULT_ARGS,
                                locale=_locale,
                                timezone_id=_tz,
            portal_name=portal_name,
                            )
                            if _vis_backend is None:
                                log.warning("[SESION_NUEVA] No se pudo inicializar backend para login manual.")
                                _portal_end_reason = "login_timeout"
                                break
                            _vis_ctx  = _vis_backend.context
                            _vis_page = _vis_backend.new_page()
                            if _vis_page is None:
                                _vis_backend.close()
                                _portal_end_reason = "login_timeout"
                                break
                            try:
                                _vis_page.goto(_login_url, wait_until="domcontentloaded", timeout=30_000)
                                _vis_page.wait_for_timeout(2000)
                            except Exception:
                                pass

                            # 3. Esperar login — detección multicapa cada 3s
                            # 5 min máx — mismo límite que _ensure_login y
                            # _wait_for_login_if_needed (cerrar ciclo y pasar
                            # al siguiente portal si no hay login a tiempo).
                            _login_deadline = time.time() + 300  # 5 min
                            _last_remind    = 0.0
                            while time.time() < _login_deadline:
                                time.sleep(3)
                                _found_session = False
                                try:
                                    _found_session = _is_logged_in_on_page(_vis_page, portal_name)
                                except Exception:
                                    pass
                                if _found_session:
                                    # Navegar a home del portal → fuerza flush de cookies a disco
                                    _flush_home = SITE_CONFIG.get(portal_name, {}).get("url_busqueda", "")
                                    if _flush_home:
                                        try:
                                            _vis_page.goto(_flush_home, wait_until="domcontentloaded", timeout=15_000)
                                        except Exception:
                                            pass
                                    _vis_page.wait_for_timeout(5000)   # margen para persist SQLite
                                    _login_ok = True
                                    break
                                if time.time() - _last_remind >= 30:
                                    _rem = int(_login_deadline - time.time())
                                    print(f"[SESION_NUEVA] Esperando login en {portal_name.upper()}..."
                                          f" ({_rem}s)")
                                    _last_remind = time.time()

                            # 4. Cerrar ventana visible
                            try:
                                _vis_ctx.close()
                            except Exception:
                                pass
                        except Exception as _vis_exc:
                            log.warning("[LOGIN_MANUAL] Error abriendo ventana visible: %s", _vis_exc)

                        if _login_ok:
                            # 5. Reabrir headless y reintentar keyword
                            print(f"[SESION_NUEVA] {portal_name.upper()} - sesion guardada. Relanzando headless...")
                            try:
                                from .stealth_utils import lock_session_ua, reset_session_ua
                                reset_session_ua()
                                _session_user_agent = lock_session_ua()
                            except Exception:
                                _session_user_agent = _STEALTH_UA
                            _rl_backend = select_browser_backend(
                                pw, session_dir,
                                headless=headless,
                                user_agent=_session_user_agent,
                                args=_STEALTH_ARGS,
                                ignore_default_args=_STEALTH_IGNORE_DEFAULT_ARGS,
                                locale=_locale,
                                timezone_id=_tz,
            portal_name=portal_name,
                            )
                            if _rl_backend is None:
                                log.warning("[SESION_NUEVA] No se pudo reinicializar backend.")
                                _portal_end_reason = "login_timeout"
                                break
                            browser_ctx = _rl_backend.context
                            page = browser_ctx.new_page()
                            apply_stealth(page)
                            _session_verified = False
                            _kw_index -= 1   # reintentar keyword actual
                            _portal_end_reason = "completed"
                            continue
                        else:
                            print(f"[SESION_NUEVA] {portal_name.upper()} - tiempo agotado sin login. Saltando.")
                            _portal_end_reason = "login_timeout"
                            break
                    else:
                        print(f"\n[LOGIN_TIMEOUT] {portal_name.upper()}: tiempo de login agotado. "
                              "Continuando con el siguiente portal.")
                        _portal_end_reason = "login_timeout"
                        break
                except RateLimitExceeded as _rle:
                    log.warning("[RATE_LIMIT] %s: límite alcanzado — saltando portal.", portal_name)
                    print(f"\n[RATE_LIMIT] {portal_name.upper()}: cuota de hora agotada "
                          f"({_rle.wait_secs/60:.0f} min para liberar). "
                          "Pasando al siguiente portal — se retomará en la próxima sesión.")
                    total_applied += applied
                    _portal_end_reason = "rate_limit"
                    break
                # _session_verified queda False → pre-flight verifica login antes de cada keyword
                total_applied += applied
                _mode_applied[mode] = _mode_applied.get(mode, 0) + applied
                _kw_count_for_stats  += 1
                _found_count_for_stats += len(seen_titles) if seen_titles else 0

                if applied == 0:
                    print(f"\n[AVISO] '{keyword}': 0 postulaciones - filtradas o ya en DB.")
                print(f"\n[PORTAL_FINALIZADO] --- KEYWORD '{keyword}': {applied} postulaciones ---")

                # Emitir progreso por modo y total
                _mode_progress = " | ".join(
                    f"{m.upper()} {_mode_applied.get(m,0)}/{_mode_max[m]}"
                    for m in _mode_max
                )
                _progress_str = f"{_mode_progress} | Total {total_applied}/{_effective_max}" if _mode_progress else f"Total {total_applied}/{_effective_max}"
                print(f"  [PROGRESO] {_progress_str} en {portal_name.upper()}")

                # Cortar keywords si ya alcanzamos el límite global de la sesión
                if total_applied >= _effective_max:
                    print(f"\n[LIMITE_PORTAL] {portal_name.upper()}: limite de {_effective_max} "
                          f"postulaciones alcanzado. Guardando keywords restantes en cola.")
                    log.info("[LIMITE_PORTAL] %s: %d/%d — deteniendo keywords.",
                             portal_name, total_applied, _effective_max)
                    break

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
                    print(f"  [REEMPLAZOS] {len(new_kws)} nuevas keywords anadidas a la cola.")

                # Guardia de sesión muerta cada 5 keywords sin postulaciones
                _KEYWORDS_BEFORE_SESSION_CHECK = 5
                if (config.get("requires_login")
                        and total_applied == 0
                        and _kw_index % _KEYWORDS_BEFORE_SESSION_CHECK == 0
                        and _kw_index > 0):
                    print(f"\n[SESION_CHECK] {portal_name.upper()}: {_kw_index} keywords "
                          f"sin postulaciones - verificando sesion...")
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
                    except TimeoutError:
                        raise   # sesion expirada → propagar para saltar portal
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
            print(f"[PROGRESO_FINAL] Aplicadas {total_applied}/{_effective_max} en {portal_name.upper()}")

            # ── Verificar si se debe reintentar ──────────────────────────────
            # Si el portal tiene AMBAS categorías activas (IT y bodega), el
            # mínimo para pasar al siguiente portal es 1 postulación de cada
            # una — no basta con total_applied > 0 (podría ser 2 IT y 0 bodega).
            # _mode_applied se acumula entre reintentos dentro del mismo portal.
            _both_modes_active = bool(_it_groups and _bodega_groups)
            if _both_modes_active:
                _it_done  = _mode_applied.get("it", 0)
                _bod_done = _mode_applied.get("bodega", 0)
                _min_ok   = _it_done >= 1 and _bod_done >= 1
            else:
                _it_done = _bod_done = 0
                _min_ok  = total_applied > 0

            if _min_ok:
                # Éxito: mínimo cumplido → salir normalmente
                if _both_modes_active:
                    log.info("[CICLO] %s: minimo cumplido (IT=%d, BODEGA=%d). Sesion completada.",
                             portal_name, _it_done, _bod_done)
                else:
                    log.info("[CICLO] %s: %d postulaciones. Sesión completada.", portal_name, total_applied)
                break

            # Mínimo NO alcanzado en este intento
            _falta_msg = (
                f"(faltan: {'IT ' if _it_done < 1 else ''}{'BODEGA' if _bod_done < 1 else ''} — "
                f"actual IT={_it_done}, BODEGA={_bod_done})"
                if _both_modes_active else "(0 postulaciones)"
            )
            if no_retry or _retry_count >= _RETRY_MAX_COUNT:
                # Modo persistente o ya agotamos reintentos → pasar al siguiente portal
                if _retry_count >= _RETRY_MAX_COUNT:
                    print(f"\n[CICLO] {portal_name.upper()}: minimo no alcanzado tras {_retry_count + 1} "
                          f"intentos {_falta_msg} — pasando al siguiente portal.")
                    log.warning("[CICLO] %s: minimo no alcanzado tras %d intentos %s — saltando.",
                                portal_name, _retry_count + 1, _falta_msg)
                else:
                    print(f"\n[CICLO] {portal_name.upper()}: minimo no alcanzado {_falta_msg} → siguiente portal.")
                break

            # Primer/siguiente intento sin alcanzar el mínimo → reintento obligatorio
            _retry_count += 1
            if _both_modes_active:
                print(
                    f"\n[REINTENTO] {portal_name.upper()}: minimo no alcanzado en intento #{_retry_count} "
                    f"{_falta_msg}.\n"
                    f"  → Reintento obligatorio en {_RETRY_WAIT_INITIAL // 60} min "
                    f"(se requiere minimo 1 postulacion IT y 1 BODEGA antes de pasar al siguiente portal).\n"
                )
            else:
                print(
                    f"\n[REINTENTO] {portal_name.upper()}: 0 postulaciones en intento #{_retry_count}.\n"
                    f"  → Reintento obligatorio en {_RETRY_WAIT_INITIAL // 60} min "
                    f"(se debe postular al menos una vez antes de pasar al siguiente portal).\n"
                )
            log.warning("[REINTENTO] %s: minimo no alcanzado %s — reintentando en %ds (intento %d/%d)",
                        portal_name, _falta_msg, _RETRY_WAIT_INITIAL, _retry_count, _RETRY_MAX_COUNT)
            _retry_countdown(_RETRY_WAIT_INITIAL, portal_name)
            # Continúa el while True → re-ejecuta el loop de keywords completo

        try:
            browser_ctx.close()
        except Exception as close_err:
            log.warning("browser_ctx.close() ignorado: %s", close_err)
        _maybe_keep_session(session_dir, portal_name, total_applied)

    log.info("=== Multi-Keyword Fin. Total aplicadas: %d | Rate: %d/%d ===",
             total_applied, rate_limiter.current_count, rate_limiter.max_actions)
    log.info("Logs CSV: %s", LOGS_DIR)

    # Emitir progreso final con flag finished=True para que el dashboard coloree
    # _effective_max puede quedar sin definir si el browser no llegó a la fase de keywords
    _eff = locals().get("_effective_max") or SITE_CONFIG.get(portal_name, {}).get("max_offers_per_run", 5)
    print(f"[PROGRESO_FINAL] Aplicadas {total_applied}/{_eff} en {portal_name.upper()}")

    # ── Notificación de fin de run ─────────────────────────────────────────────
    try:
        from .notifier import send_summary as _notify
        _notify(
            portals=[portal_name],
            applied=total_applied,
            external=0,
            filtered=0,
            errors=0,
            duration_s=time.time() - _run_start,
        )
    except Exception as _ne:
        log.debug("[NOTIFIER] Error enviando notificación: %s", _ne)

    # ── Devolver dict con stats del portal para que main.py las pase al SessionTracker ──
    return {
        "applied":    total_applied,
        "found":      _found_count_for_stats,
        "keywords":   _kw_count_for_stats,
        "end_reason": _portal_end_reason,
    }


def _keyword_cycle_report(portals: list, scan_base: list, cycle: int) -> None:
    """
    Imprime un resumen estadístico de keywords después de cada ciclo:
    - Top performers (score > 0.5)
    - Retiradas este ciclo (found=0 → eliminadas por optimizer)
    - Total activas restantes
    """
    from .keyword_optimizer import get_active_groups, get_keyword_score, _load_stats

    stats = _load_stats()
    print(f"\n[KEYWORDS ciclo {cycle}] Analisis estadistico:")

    for portal in portals:
        active = get_active_groups(scan_base, portal)
        n_active = len(active)

        # Retiradas: keywords con status=retired en este portal
        retired_this_cycle = [
            kw for kw, v in stats.items()
            if v.get("portals", {}).get(portal, {}).get("status") == "retired"
        ]

        # Top performers (score > 0.5 = encontraron algo)
        scored = sorted(
            [(g["keyword"], get_keyword_score(g["keyword"], portal)) for g in active],
            key=lambda x: x[1], reverse=True
        )
        top = [(kw, s) for kw, s in scored if s > 0.5][:5]
        zero = [(kw, s) for kw, s in scored if s <= 0.3]

        print(f"\n  {portal.upper()} - {n_active} keywords activas:")
        if top:
            print(f"    🏆 Top: " + " | ".join(f"{kw} ({s:.2f})" for kw, s in top))
        if zero:
            print(f"    ⚠️  Sin resultados: " + ", ".join(kw for kw, _ in zero[:5]))
        if retired_this_cycle:
            recent = retired_this_cycle[-5:]
            print(f"    🗑  Retiradas total: {len(retired_this_cycle)} "
                  f"(últimas: {', '.join(recent)})")
        if n_active == 0:
            print(f"    ❌ Sin keywords activas - este portal no se procesara mas.")


def run_scan_quick_links(headless: bool = False, max_links: int = 100) -> dict:
    """
    Escanea los quick_links.json guardados (ofertas externas de portales como GetOnBoard).

    Para cada link:
      1. Navega a la página de oferta
      2. Hace click en el botón de aplicar
      3. Sigue al ATS externo (nueva pestaña si aplica)
      4. Escanea el formulario con scan_form (sin enviar nada)
      5. Guarda preguntas sin respuesta en scan_queue.json y pending_questions.json

    Retorna resumen: {"scanned": N, "queued": N, "already_answered": N, "failed": N,
                      "top_questions": [...]}
    """
    from .form_filler import scan_form
    from .portals.getonyboard import _try_fill_external_ats as _placeholder  # noqa  force import

    if not ENABLE_QUICK_LINKS:
        print("[SCAN-QL] Quick links deshabilitados (ENABLE_QUICK_LINKS=false). No se procesará esta operación.")
        return {"scanned": 0, "queued": 0, "already_answered": 0, "failed": 0}

    if not QUICK_LINKS_PATH.exists():
        print("[SCAN-QL] No hay quick_links.json. Corre primero el bot para generar links.")
        return {}

    links = []
    try:
        with open(QUICK_LINKS_PATH, encoding="utf-8") as f:
            links = json.load(f)
    except Exception as e:
        print(f"[SCAN-QL] Error leyendo quick_links.json: {e}")
        return {}

    links = [l for l in links if not l.get("dismissed", False)][:max_links]
    print(f"\n[SCAN-QL] Escaneando {len(links)} quick links (sin postular)...\n")

    stats = {"scanned": 0, "queued": 0, "already_answered": 0, "failed": 0}
    all_unanswered: list[str] = []

    # ATSs complejos que requieren cuenta propia — saltar
    _SKIP_ATS = ("workday", "taleo", "successfactors", "brassring", "icims",
                 "oraclecloud", "myworkdayjobs", "linkedin.com", "indeed.com",
                 "login", "signin", "register", "signup")

    gob_session = str(SESSIONS_DIR / "getonyboard")
    Path(gob_session).mkdir(exist_ok=True)

    # Pre-login: garantiza sesión de GetOnBoard antes de abrir el browser.
    if not _ensure_login("getonyboard", gob_session):
        log.warning("[SCAN-QL] Login cancelado — detenido.")
        return stats

    with sync_playwright() as pw:
        _scan_backend = select_browser_backend(
            pw, gob_session,
            headless=headless,
            user_agent=_STEALTH_UA,
            args=_STEALTH_ARGS + ["--disable-popup-blocking"],
            ignore_default_args=_STEALTH_IGNORE_DEFAULT_ARGS,
            locale="es-CL",
            timezone_id="America/Santiago",
            portal_name="unknown",
        )
        if _scan_backend is None:
            log.warning("[SCAN-QL] No se pudo inicializar backend de navegador.")
            return stats
        ctx = _scan_backend.context
        page = ctx.new_page()
        if page is None:
            _scan_backend.close()
            return stats
        page.add_init_script(_STEALTH_INIT_SCRIPT)
        apply_stealth(page)

        for entry in links:
            offer_url = entry.get("url", "")
            title     = entry.get("title", "unknown")
            portal    = entry.get("portal", "unknown")

            if not offer_url:
                continue

            stats["scanned"] += 1
            print(f"  [{stats['scanned']:>3}/{len(links)}] {title[:55]}", end=" ", flush=True)

            try:
                page.goto(offer_url, wait_until="domcontentloaded", timeout=20_000)
                human_delay(0.7, 1.3)

                # Detectar botón de aplicar
                APPLY_SELS = [
                    "a#apply_bottom", "a#apply_bottom_short", "a.js-go-to-apply",
                    "a:has-text('Postular')", "a:has-text('Aplicar')",
                    "button:has-text('Postular')", "button:has-text('Apply')",
                ]
                btn = None
                for sel in APPLY_SELS:
                    try:
                        el = page.query_selector(sel)
                        if el and el.is_visible():
                            btn = el
                            break
                    except Exception:
                        pass

                if not btn:
                    print("(sin boton)")
                    stats["failed"] += 1
                    continue

                # Determinar si abre nueva pestaña
                opens_new_tab = (btn.get_attribute("target") or "").strip() == "_blank"
                ext_url = ""
                scanned_page = None

                if opens_new_tab:
                    try:
                        with page.context.expect_page(timeout=8_000) as np_info:
                            btn.click()
                        ext_page = np_info.value
                        ext_page.wait_for_load_state("domcontentloaded", timeout=15_000)
                        ext_url = ext_page.url

                        if any(s in ext_url.lower() for s in _SKIP_ATS):
                            print(f"(ATS complejo: {ext_url[:40]})")
                            try:
                                ext_page.close()
                            except Exception:
                                pass
                            stats["failed"] += 1
                            continue

                        scanned_page = ext_page
                    except Exception as tab_exc:
                        log.debug("  [SCAN-QL] No se pudo abrir pestaña: %s", tab_exc)
                        print("(error pestana)")
                        stats["failed"] += 1
                        continue
                else:
                    btn.click()
                    human_delay(1.0, 1.8)
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=8_000)
                    except Exception:
                        pass
                    ext_url = page.url
                    scanned_page = page

                # Escanear formulario en el ATS externo
                if scanned_page:
                    try:
                        form_result = scan_form(
                            scanned_page,
                            USER_PROFILE,
                            job_title=title,
                            portal=portal,
                            url=offer_url,
                        )
                        unanswered = form_result.get("unanswered", [])
                        answered   = form_result.get("all_answered", False)

                        if answered and not unanswered:
                            print("(ok - ya respondida)")
                            stats["already_answered"] += 1
                            _save_to_scan_queue(offer_url, title, portal, [])
                        elif unanswered:
                            print(f"({len(unanswered)} preguntas nuevas)")
                            stats["queued"] += 1
                            all_unanswered.extend(unanswered)
                            _save_to_scan_queue(offer_url, title, portal, unanswered)
                            # Guardar en pending_questions
                            for q in unanswered:
                                from .form_filler import _normalize as _fnorm, _save_pending_question
                                _save_pending_question(q, _fnorm(q), portal=portal, url=offer_url)
                        else:
                            print("(sin formulario)")
                            stats["failed"] += 1

                    except Exception as se:
                        print(f"(error scan: {str(se)[:30]})")
                        stats["failed"] += 1
                    finally:
                        if opens_new_tab and scanned_page != page:
                            try:
                                scanned_page.close()
                            except Exception:
                                pass

            except Exception as exc:
                print(f"(error: {str(exc)[:40]})")
                stats["failed"] += 1

            human_delay(0.3, 0.7)

        try:
            ctx.close()
        except Exception:
            pass

    # Calcular top preguntas
    from collections import Counter
    q_counter = Counter(all_unanswered)
    top_qs = [q for q, _ in q_counter.most_common(20)]
    stats["top_questions"] = top_qs

    # Mostrar resumen
    print(f"\n[SCAN-QL] Resultado:")
    print(f"  Escaneadas          : {stats['scanned']}")
    print(f"  Ya respondidas (OK) : {stats['already_answered']}")
    print(f"  En cola             : {stats['queued']}")
    print(f"  Sin formulario/error: {stats['failed']}")
    if top_qs:
        print(f"\n  TOP preguntas sin respuesta ({len(top_qs)}):")
        for i, q in enumerate(top_qs[:15], 1):
            cnt = q_counter[q]
            print(f"    {i:>2}. [{cnt}x] {q[:75]}")
        print(f"\n  Responde estas preguntas en data/qa_cache.json")
        print(f"  y corre: python main.py --apply-queue")

    return stats


def run_apply_quick_links(headless: bool = False, max_apply: int = 5) -> dict:
    """
    Aplica automáticamente a quick_links.json (ATS externos).
    Para cada link: navega, hace click en Apply, rellena el formulario con el perfil
    del usuario y envía — igual que fill_form pero en sitios externos.

    Solo procesa hasta max_apply links. Los restantes quedan para el siguiente run.
    """
    from .form_filler import scan_form, fill_form

    if not ENABLE_QUICK_LINKS:
        print("[APPLY-QL] Quick links deshabilitados (ENABLE_QUICK_LINKS=false). No se procesará esta operación.")
        return {"applied": 0, "pending": 0, "failed": 0}

    if not QUICK_LINKS_PATH.exists():
        print("[APPLY-QL] No hay quick_links.json.")
        return {"applied": 0, "pending": 0, "failed": 0}

    try:
        with open(QUICK_LINKS_PATH, encoding="utf-8") as f:
            all_links = json.load(f)
    except Exception as e:
        print(f"[APPLY-QL] Error leyendo quick_links.json: {e}")
        return {"applied": 0, "pending": 0, "failed": 0}

    # Filtrar: no incluir practicas/pasantias ni ya aplicadas
    from .config import practica_ok
    _auto_dismissed = 0
    for lnk in all_links:
        if lnk.get("dismissed"):
            continue
        url_low = lnk.get("url", "").lower()
        title_low = lnk.get("title", "").lower()
        if not practica_ok(url_low + " " + title_low):
            lnk["dismissed"] = True
            _auto_dismissed += 1
    if _auto_dismissed:
        try:
            with open(QUICK_LINKS_PATH, "w", encoding="utf-8") as _f:
                json.dump(all_links, _f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    links = [l for l in all_links if not l.get("dismissed", False)]
    print(f"\n[APPLY-QL] {len(links)} links pendientes ({_auto_dismissed} practica auto-descartadas) - procesando hasta {max_apply}...\n")

    _SKIP_ATS = ("workday", "taleo", "successfactors", "brassring", "icims",
                 "oraclecloud", "myworkdayjobs", "login", "signin", "register", "signup")
    _SUBMIT_SELS = (
        "button[type='submit'], input[type='submit'], "
        "button:has-text('Enviar'), button:has-text('Submit'), "
        "button:has-text('Apply'), button:has-text('Send application'), "
        "button:has-text('Postular'), button:has-text('Inscribirme'), "
        "button:has-text('Aplicar'), button:has-text('Solicitar')"
    )

    stats = {"applied": 0, "pending": 0, "failed": 0, "skipped_ats": 0}
    applied_urls: set[str] = set()

    # Sesion dinámica: usar la sesión del portal de origen si está disponible
    # (evita fallos por falta de login en portales que lo requieren)
    _PORTAL_SESSIONS = {
        "computrabajo": str(SESSIONS_DIR / "computrabajo"),
        "chiletrabajos": str(SESSIONS_DIR / "chiletrabajos"),
        "laborum": str(SESSIONS_DIR / "laborum"),
        "trabajando": str(SESSIONS_DIR / "trabajando"),
        "linkedin": str(SESSIONS_DIR / "linkedin"),
        "getonyboard": str(SESSIONS_DIR / "getonyboard"),
    }
    gob_session = str(SESSIONS_DIR / "getonyboard")
    Path(gob_session).mkdir(exist_ok=True)

    # Selectores de apply unificados para todos los portales soportados
    APPLY_SELS = [
        # GetOnBoard
        "a#apply_bottom", "a#apply_bottom_short", "a.js-go-to-apply",
        # Computrabajo
        "a.btn_postular", "button.btn_postular",
        "a[data-qa='btn-apply']", "button[data-qa='btn-apply']",
        "a:has-text('Postularme')", "button:has-text('Postularme')",
        # Genéricos
        "a:has-text('Postular')", "a:has-text('Aplicar')",
        "button:has-text('Postular')", "button:has-text('Apply')",
        "a:has-text('Apply')", "a:has-text('Solicitar')",
        "button:has-text('Inscribirme')", "a:has-text('Inscribirme')",
    ]
    _LOGIN_SIGNALS_QL = ("login", "signin", "ingresar", "iniciar sesion", "iniciar sesión",
                         "registrate", "registrar", "crear cuenta")

    with sync_playwright() as pw:
        # Agrupar links por portal para usar la sesión correcta
        links_by_portal: dict[str, list] = {}
        for lnk in links:
            p = lnk.get("portal", "getonyboard")
            links_by_portal.setdefault(p, []).append(lnk)

        for portal_key, portal_links in links_by_portal.items():
            if stats["applied"] >= max_apply:
                break

            # Usar sesión del portal si existe, sino GetOnBoard como fallback
            session_dir = _PORTAL_SESSIONS.get(portal_key, gob_session)
            Path(session_dir).mkdir(exist_ok=True)

            # Pre-login: garantiza sesión antes de abrir el browser del portal.
            if not _ensure_login(portal_key, session_dir):
                log.warning("[APPLY-QL] Login cancelado para %s — detenido.", portal_key)
                break

            _apply_backend = select_browser_backend(
                pw, session_dir,
                headless=headless,
                user_agent=_STEALTH_UA,
                args=_STEALTH_ARGS + ["--disable-popup-blocking"],
                ignore_default_args=_STEALTH_IGNORE_DEFAULT_ARGS,
                locale="es-CL",
                timezone_id="America/Santiago",
            portal_name=portal_key,
            )
            if _apply_backend is None:
                log.warning("[APPLY-QL] No se pudo inicializar backend para %s.", portal_key)
                continue
            ctx = _apply_backend.context
            page = ctx.new_page()
            if page is None:
                _apply_backend.close()
                continue
            page.add_init_script(_STEALTH_INIT_SCRIPT)
            apply_stealth(page)

            for entry in portal_links:
                if stats["applied"] >= max_apply:
                    break

                offer_url = entry.get("url", "")
                title     = entry.get("title", "Sin título")
                portal    = entry.get("portal", "unknown")

                if not offer_url or already_applied(offer_url):
                    _dismiss_quick_link(offer_url)
                    continue

                print(f"  [{stats['applied']+1}/{max_apply}] [{portal.upper()}] {title[:50]}", end=" ", flush=True)

                try:
                    page.goto(offer_url, wait_until="domcontentloaded", timeout=20_000)
                    human_delay(1.0, 1.8)

                    # Detectar redirección a login → skip sin contar como fallo
                    cur_url = page.url.lower()
                    cur_text = ""
                    try:
                        cur_text = (page.evaluate("() => document.body?.innerText?.slice(0,200) || ''") or "").lower()
                    except Exception:
                        pass
                    if any(s in cur_url for s in _LOGIN_SIGNALS_QL) or any(s in cur_text for s in _LOGIN_SIGNALS_QL):
                        print(f"(sin sesion - requiere login en {portal})")
                        stats["failed"] += 1
                        continue

                    # Esperar carga dinámica del botón (SPA portals)
                    for _sel in APPLY_SELS[:4]:  # solo los más probables para el wait
                        try:
                            page.wait_for_selector(_sel, timeout=3_000)
                            break
                        except Exception:
                            pass

                    btn = None
                    for sel in APPLY_SELS:
                        try:
                            el = page.query_selector(sel)
                            if el and el.is_visible():
                                btn = el
                                break
                        except Exception:
                            pass

                    if not btn:
                        print("(sin boton apply)")
                        stats["failed"] += 1
                        continue

                    opens_new_tab = (btn.get_attribute("target") or "").strip() == "_blank"
                    apply_page = None

                    if opens_new_tab:
                        try:
                            with page.context.expect_page(timeout=8_000) as np_info:
                                btn.click()
                            apply_page = np_info.value
                            apply_page.wait_for_load_state("domcontentloaded", timeout=15_000)
                            ext_url = apply_page.url
                            if any(s in ext_url.lower() for s in _SKIP_ATS):
                                print(f"(ATS complejo: {ext_url[:35]})")
                                stats["skipped_ats"] += 1
                                try:
                                    apply_page.close()
                                except Exception:
                                    pass
                                continue
                        except Exception as exc:
                            print(f"(error nueva pestana: {str(exc)[:30]})")
                            stats["failed"] += 1
                            continue
                    else:
                        btn.click()
                        human_delay(1.2, 2.0)
                        try:
                            page.wait_for_load_state("domcontentloaded", timeout=8_000)
                        except Exception:
                            pass
                        apply_page = page

                    if not apply_page:
                        stats["failed"] += 1
                        continue

                    # Verificar que las preguntas tienen respuesta
                    sr = scan_form(apply_page, USER_PROFILE, job_title=title)
                    if not sr.get("all_answered", False):
                        q_labels = sr.get("unanswered", [])
                        print(f"(⏳ {len(q_labels)} preguntas sin respuesta)")
                        _save_to_scan_queue(offer_url, title, portal, q_labels)
                        stats["pending"] += 1
                        if opens_new_tab and apply_page != page:
                            try:
                                apply_page.close()
                            except Exception:
                                pass
                        continue

                    # Rellenar formulario
                    fill_form(apply_page, USER_PROFILE, job_title=title)
                    human_delay(0.8, 1.5)

                    # Buscar y hacer click en Submit
                    submitted = False
                    for sel in _SUBMIT_SELS.split(","):
                        sel = sel.strip()
                        try:
                            sbtn = apply_page.query_selector(sel)
                            if sbtn and sbtn.is_visible() and sbtn.is_enabled():
                                sbtn.scroll_into_view_if_needed()
                                sbtn.click()
                                human_delay(1.5, 2.5)
                                submitted = True
                                break
                        except Exception:
                            continue

                    if submitted:
                        save_application(offer_url, portal, title, "applied")
                        _csv_log(portal, offer_url, title, "applied")
                        _dismiss_quick_link(offer_url)
                        applied_urls.add(offer_url)
                        stats["applied"] += 1
                        print("-> ✅ Postulado!")
                        rate_limiter_inst = get_rate_limiter(portal)
                        rate_limiter_inst.acquire(portal)
                    else:
                        print("(sin boton submit)")
                        stats["failed"] += 1

                except Exception as exc:
                    print(f"(error: {str(exc)[:50]})")
                    stats["failed"] += 1
                finally:
                    if opens_new_tab and apply_page and apply_page != page:
                        try:
                            apply_page.close()
                        except Exception:
                            pass

                    human_delay(1.0, 2.0)

            # Cerrar contexto de este portal antes de abrir el del siguiente
            try:
                ctx.close()
            except Exception:
                pass

    remaining = len([l for l in links if not l.get("dismissed") and l.get("url") not in applied_urls])
    print(f"\n[APPLY-QL] Resultado: {stats['applied']} aplicadas, "
          f"{stats['pending']} pendientes, {stats['failed']} fallidas. "
          f"{remaining} en cola para el próximo run.")
    return stats


def _dismiss_quick_link(url: str) -> None:
    """Marca un quick link como dismissed en quick_links.json."""
    if not QUICK_LINKS_PATH.exists():
        return
    try:
        with open(QUICK_LINKS_PATH, encoding="utf-8") as f:
            links = json.load(f)
        for l in links:
            if l.get("url") == url:
                l["dismissed"] = True
        with open(QUICK_LINKS_PATH, "w", encoding="utf-8") as f:
            json.dump(links, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.debug("_dismiss_quick_link error: %s", e)


def run_persistent_session(
    portals: list[str],
    dry_run: bool = False,
    headless: bool = False,
    min_per_portal: int = 1,
    max_cycles: int = 10,
) -> dict:
    """
    Loop persistente de postulaciones:
    - Cicla por los portales seleccionados hasta que TODOS tengan ≥ min_per_portal postulaciones.
    - Si un portal da 0 → pasa al siguiente SIN esperar (no_retry=True).
    - Después de cada ciclo completo:
        · Las keywords con found=0 ya fueron retiradas automáticamente por el optimizer.
        · Se reporta estado por portal.
        · Si no quedan keywords activas → para.
    - Para cuando todos los portales alcanzan el mínimo, se detecta señal STOP
      o se agotan los ciclos máximos.
    """
    from .config import KEYWORD_GROUPS
    from .keyword_optimizer import get_active_groups

    applied_per_portal: dict[str, int] = {p: 0 for p in portals}
    scan_base = [g for g in KEYWORD_GROUPS if g.get("scan", True)]

    print(f"\n{'='*55}")
    print(f"[PERSISTENTE] Iniciando sesion persistente")
    print(f"  Portales : {', '.join(p.upper() for p in portals)}")
    print(f"  Minimo   : {min_per_portal} postulacion/portal para parar")
    print(f"  Max ciclos: {max_cycles}")
    print(f"{'='*55}\n")

    for cycle in range(1, max_cycles + 1):
        if _should_stop():
            print("[PERSISTENTE] Senal de parada detectada. Saliendo.")
            break

        # Portales que aún no alcanzan el mínimo
        pending = [p for p in portals if applied_per_portal[p] < min_per_portal]
        if not pending:
            break   # todos tienen el mínimo → salir

        print(f"\n{'─'*55}")
        print(f"[CICLO {cycle}/{max_cycles}] Portales pendientes: {', '.join(p.upper() for p in pending)}")
        completados = [p for p in portals if p not in pending]
        if completados:
            print(f"  ✅ Ya listos: {', '.join(p.upper() for p in completados)}")
        print(f"{'─'*55}")

        any_keywords_left = False
        for portal in pending:
            if _should_stop():
                break

            # Verificar que el portal tiene keywords activas antes de lanzar el browser
            active_kws = get_active_groups(scan_base, portal)
            if not active_kws:
                print(f"  [CICLO {cycle}] {portal.upper()}: sin keywords activas - saltando.")
                continue
            any_keywords_left = True

            print(f"\n  [CICLO {cycle}] ▶ {portal.upper()} - {len(active_kws)} keywords activas")
            try:
                _res = run_bot_multi_keywords(
                    portal_name=portal,
                    dry_run=dry_run,
                    headless=headless,
                    no_retry=True,
                )
                portal_applied = _res.get("applied", 0) if isinstance(_res, dict) else (_res or 0)
            except SystemExit:
                print(f"  [CICLO {cycle}] {portal.upper()}: detenido por senal.")
                break
            except Exception as exc:
                log.error("[PERSISTENTE] Error en portal %s ciclo %d: %s", portal, cycle, exc)
                print(f"  [CICLO {cycle}] {portal.upper()}: error - {exc}")
                portal_applied = 0

            applied_per_portal[portal] += portal_applied
            emoji = "✅" if applied_per_portal[portal] >= min_per_portal else "⏳"
            print(f"  {emoji} {portal.upper()}: +{portal_applied or 0} esta vuelta "
                  f"(acumulado: {applied_per_portal[portal]}/{min_per_portal})")

        if not any_keywords_left:
            print("\n[PERSISTENTE] Sin keywords activas en ningun portal pendiente. Fin.")
            break

        # ── Resumen al final del ciclo ────────────────────────────────────────
        still_pnd = [p for p in portals if applied_per_portal[p] < min_per_portal]
        print(f"\n[CICLO {cycle}] Resumen de portales:")
        for p in portals:
            cnt = applied_per_portal[p]
            estado = "✅ LISTO" if cnt >= min_per_portal else f"⏳ {cnt}/{min_per_portal}"
            print(f"  {p.upper():<18} {estado}")

        # ── Reporte estadístico de keywords post-ciclo ────────────────────────
        _keyword_cycle_report(portals, scan_base, cycle)

        if not still_pnd:
            print(f"\n[PERSISTENTE] ✅ Todos los portales tienen ≥{min_per_portal} postulacion. Fin.")
            break

        if _should_stop():
            break

    # Resumen final
    print(f"\n{'='*55}")
    print("[PERSISTENTE] Sesion terminada:")
    total = sum(applied_per_portal.values())
    for p, cnt in applied_per_portal.items():
        estado = "✅" if cnt >= min_per_portal else "⚠️ "
        print(f"  {estado} {p.upper():<18} {cnt} postulaciones")
    print(f"  TOTAL: {total} postulaciones")
    print(f"{'='*55}\n")

    # ── Notificación de sesión persistente finalizada ──────────────────────────
    try:
        from .notifier import send_summary as _notify
        _notify(
            portals=list(applied_per_portal.keys()),
            applied=total,
            external=0,
            filtered=0,
            errors=0,
        )
    except Exception as _ne:
        log.debug("[NOTIFIER] Error enviando notificación: %s", _ne)

    return applied_per_portal


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
        print(f"\n[STANDBY] {portal_name.upper()} esta en standby y no se ejecutara.")
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

    # -- Pre-login: garantiza sesión antes de abrir el browser principal ----------
    # Reintenta en ventanas de 60s hasta que el usuario loguee o se detenga el bot.
    if not _ensure_login(portal_name, session_dir):
        log.warning("[LOGIN] Login cancelado para %s — detenido.", portal_name)
        return 0

    log.info("=== ApplyJob Bot ===")
    log.info("Portal: %s | max: %d | dry_run: %s | motor: %s | rate_limit: %d/h",
             portal_name, max_offers, dry_run,
             PortalClass.__name__ if PortalClass else "genérico",
             rate_limiter.max_actions)

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
        backend = select_browser_backend(
            pw_obj,
            session_dir,
            headless=headless,
            user_agent=_STEALTH_UA,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            ignore_default_args=_STEALTH_IGNORE_DEFAULT_ARGS,
            locale=_locale,
            timezone_id=_tz,
            portal_name=portal_name,
        )
        if backend is None:
            log.warning("No se pudo inicializar backend de navegador")
            return 0

        page = backend.new_page()
        if page is None:
            backend.close()
            return 0

        browser_ctx = backend.context
        apply_stealth(page)
        try:
            from playwright_stealth import Stealth
            Stealth().apply_stealth_sync(page)
        except Exception:
            pass

        applied, _seen_titles_single = _run_keyword_loop(
            page, browser_ctx, portal_name, config, profile,
            max_offers, dry_run, rate_limiter, portal_handler,
        )

        backend.close()
        
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

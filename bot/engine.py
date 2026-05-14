"""
Motor principal de postulación.

Responsabilidades:
  - Orquestar el flujo completo: navegación → deduplicación → postulación → logging
  - Aplicar rate limiting por portal para evitar detección
  - Reintentar ante errores transitorios (red, timeout)
  - Delegar a portales específicos (LinkedInPortal) o al motor genérico
  - Persistir resultados en SQLite y CSV

Flujo de alto nivel:
    run_bot(portal)
        └── validar configuración
        └── abrir browser con sesión persistente
        └── navegar a url_busqueda
        └── por cada página:
                └── extraer offer_ids / offer_urls
                └── por cada oferta:
                        └── skip si ya en DB (deduplicación)
                        └── rate_limiter.acquire()  ← bloquea si excede límite/hora
                        └── with_retry(apply)       ← reintenta ante error de red
                        └── save_application()
                        └── _csv_log()
                └── paginar si hay siguiente página
"""
import csv
import datetime
import logging
import os
import random
import socket
import time
from pathlib import Path

log = logging.getLogger("applyjob.engine")

from playwright.sync_api import sync_playwright, Page

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
        log.info("✓ CDP conectado al Chrome del usuario (puerto %d)", port)
        print(f"[CDP] Conectado al Chrome del usuario — sin perfil separado.")
        return browser, ctx, page
    except Exception as exc:
        log.debug("CDP no disponible (%s) — usando perfil separado.", exc)
        return None

from .config import SITE_CONFIG, USER_PROFILE, location_score, schedule_ok
from .state import already_applied, save_application
from .stealth_utils import (
    apply_stealth, human_delay, human_scroll, human_click,
    take_error_screenshot, random_user_agent, random_viewport,
)
from .form_filler import fill_form
from .retry import with_retry, get_rate_limiter
from .validator import run_startup_validation

# log = logging.getLogger("applyjob.engine") (movido arriba)

BASE_DIR     = Path(__file__).parent.parent
SESSIONS_DIR = BASE_DIR / "sessions"
LOGS_DIR     = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)
SESSIONS_DIR.mkdir(exist_ok=True)


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
        click(selector_boton_aplicar) → fill_form() → click(submit)

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
        fill_form(page, profile, job_title=job_title)
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
        click(selector_boton_aplicar) → esperar modal → fill_form() × N pasos → submit

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
        fill_form(page, profile, job_title=job_title)
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
                        fill_form(page, profile, job_title=job_title)
                        advanced = True
                        break
                except Exception as exc:
                    log.debug("Botón '%s' no disponible: %s", next_sel, exc)
                    continue
            if not advanced:
                break
        return "applied"
    except Exception as exc:
        log.warning("_apply_modal falló: %s", exc)
        return f"error: {exc}"


def _apply_externa(page: Page, config: dict) -> str:
    """
    Estrategia para postulación externa: click abre una nueva pestaña
    en un sitio de terceros. Se registra la URL y se cierra la pestaña.

    No se intenta rellenar formularios externos (muy variable y riesgoso).

    Args:
        page  : página activa
        config: SITE_CONFIG del portal

    Returns:
        "external: <url>" | "error_externa: <mensaje>"
    """
    btn_sel = config["selector_boton_aplicar"]
    try:
        with page.context.expect_page() as new_page_info:
            human_click(page, btn_sel)
        new_page = new_page_info.value
        new_page.wait_for_load_state("domcontentloaded")
        external_url = new_page.url
        new_page.close()
        log.debug("Redirección externa: %s", external_url)
        return f"external: {external_url}"
    except Exception as exc:
        log.warning("_apply_externa falló: %s", exc)
        return f"error_externa: {exc}"


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
        human_scroll(page, steps=1)

        title_sel = config.get("selector_titulo_oferta")
        if title_sel:
            try:
                title = (page.text_content(title_sel, timeout=3_000) or "").strip()[:80]
            except Exception as exc:
                log.debug("No se pudo leer título con '%s': %s", title_sel, exc)

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
            status = _apply_externa(page, config)
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
        "a[href*='/login']",
    ],
    "laborum": [
        "#ingresarNavBar",
        "button:has-text('Ingresar')",
        "input#email",
        "a[href*='/login']",
    ],
    "indeed": [
        "a[href*='/account/login']",
        "button:has-text('Iniciar sesión')",
        "button:has-text('Sign in')",
        "div.desktop-sign-in-button",
        "a[data-gnav-element-name='SignIn']",
    ],
}

# Selectores que confirman que ya hay sesión activa
_LOGGED_IN_SIGNALS = {
    "linkedin": [
        ".global-nav__me-photo",
        "img.global-nav__me-photo",
        "[data-control-name='nav.settings']",
        ".feed-identity-module",
    ],
    "laborum": [
        "[data-testid='header-user-menu']",
        "button:has-text('Mi Perfil')",
        "a[href*='/postulante/']",
        "img[class*='Avatar']",
    ],
    "indeed": [
        "a[data-gnav-element-name='Account']",
        "div[data-testid='UserDropdown']",
        "button[aria-label*='cuenta']",
        "img[class*='avatarImage']",
        "#IA_AccountHamburger",
        "a[href*='/myjobs']",
    ],
}

# URLs de login por portal
_LOGIN_URLS = {
    "linkedin": "https://www.linkedin.com/login",
    "laborum":  "https://www.laborum.cl/login",
    "indeed":   "https://cl.indeed.com/account/login",
}


def _wait_for_login_if_needed(page, portal_name: str, config: dict) -> None:
    """
    1. Si hay Cloudflare → espera a que el usuario lo resuelva (imprime [CAPTCHA]).
    2. Si hay pantalla de login → espera a que el usuario inicie sesión ([LOGIN_REQUERIDO]).
    3. Si ya hay sesión activa → retorna inmediatamente.
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
        for sel in session_sels:
            try:
                if page.query_selector(sel):
                    return True
            except Exception:
                pass
        # Indeed: URL de jobs sin muro de login
        if portal_name == "indeed":
            try:
                cur = page.url
                if (any(k in cur for k in ("/jobs", "/myjobs", "/resume"))
                        and "login" not in cur and "authwall" not in cur
                        and "cf_chl" not in cur):
                    return True
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

    # ── Paso 1: resolver Cloudflare si está presente ──────────────────────────
    if _is_cloudflare():
        print(f"\n[CAPTCHA] Cloudflare detectado en {portal_name.upper()}. Resuelve el desafio en el navegador.")
        log.warning("Cloudflare detectado — esperando resolución manual (max 3 min)...")
        deadline_cf = time.time() + 180
        last_cf_log = 0
        while time.time() < deadline_cf:
            time.sleep(2)
            if not _is_cloudflare():
                log.info("Cloudflare resuelto. Continuando.")
                print(f"\n[SESIÓN_INICIADA] Cloudflare resuelto en {portal_name.upper()}.")
                human_delay(2.0, 3.0)
                break
            if time.time() - last_cf_log > 20:
                print(f"[CAPTCHA] Esperando resolución del desafío en {portal_name.upper()}...")
                last_cf_log = time.time()
        else:
            log.error("Tiempo agotado esperando Cloudflare.")
            return

    # ── Paso 2: verificar si ya hay sesión activa ─────────────────────────────
    if _is_logged_in():
        log.info("Sesión activa confirmada en %s.", portal_name)
        return

    # ── Paso 3: verificar si realmente necesita login ─────────────────────────
    # Solo forzar login si hay señales explícitas O es indeed/laborum/linkedin
    needs = _needs_login()
    if not needs and portal_name not in ("indeed", "laborum", "linkedin"):
        return
    if not needs:
        # Para indeed/laborum: esperar un poco más antes de forzar login
        # (la página puede estar cargando aún) - USAR WAIT INTELIGENTE
        try:
            page.wait_for_function("() => document.readyState === 'complete'", timeout=2000)
        except:
            pass
            
        if _is_logged_in():
            return
        if not _needs_login():
            # Si no hay señales claras de login y no hay CF → continuar igual
            log.info("Sin señales claras de login en %s — continuando.", portal_name)
            return

    # ── Hay que hacer login ───────────────────────────────────────────────────
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

    # Esperar hasta 20 minutos a que el usuario inicie sesión
    deadline = time.time() + 1200
    last_log = 0

    while time.time() < deadline:
        current_time = time.time()
        if current_time - last_log > 15:
            print(f"\n[LOGIN_REQUERIDO] Esperando login en {portal_name.upper()}. Inicia sesión en el navegador abierto.")
            last_log = current_time
            
        time.sleep(3)
        try:
            if _is_logged_in():
                log.info("Sesión detectada. Continuando...")
                print(f"\n[SESIÓN_INICIADA] Login detectado en {portal_name.upper()}. Continuando automáticamente.")
                page.goto(config["url_busqueda"], wait_until="domcontentloaded", timeout=30_000)
                human_delay(2.0, 3.0)
                return
            cur = page.url
            indeed_ok = (portal_name == "indeed" and
                         any(k in cur for k in ("/jobs", "/myjobs", "/resume", "/account")) and
                         "login" not in cur and "authwall" not in cur)
            generic_ok = "/feed" in cur or "/profile" in cur
            if indeed_ok or generic_ok:
                log.info("Login detectado por URL. Continuando...")
                print(f"\n[SESIÓN_INICIADA] Login detectado en {portal_name.upper()}. Continuando automáticamente.")
                if config["url_busqueda"] not in cur:
                    page.goto(config["url_busqueda"], wait_until="domcontentloaded", timeout=30_000)
                human_delay(2.0, 3.0)
                return
        except Exception as exc:
            log.debug("Error verificando login: %s", exc)

    log.error("Tiempo de espera agotado (20 min). Cerrando.")
    print(f"\n[FALLO] Tiempo de espera agotado en {portal_name.upper()}.")
    raise TimeoutError("Login no completado en el tiempo límite")


# ---------------------------------------------------------------------------
# run_bot — función principal pública
# ---------------------------------------------------------------------------

def _run_keyword_loop(
    page, browser, portal_name: str, config: dict, profile: dict,
    max_offers: int, dry_run: bool, rate_limiter, portal_handler,
    using_cdp: bool = False,
) -> int:
    """
    Navega a config['url_busqueda'], extrae ofertas y postula.
    Reutilizable en run_bot (keyword única) y run_bot_multi_keywords (browser compartido).
    Retorna el número de postulaciones completadas.
    """
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
    _wait_for_login_if_needed(page, portal_name, config)

    applied  = 0
    page_num = 1
    current_listing_url = page.url

    while applied < max_offers:
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

        # ── Portal específico ─────────────────────────────────────────────────
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

            log.info("  ✓ Ofertas encontradas: %d", len(offer_ids))

            for offer_id in offer_ids:
                if applied >= max_offers:
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

                if status == "applied":
                    print(f"  [ÉXITO] Postulación completada para: {title or offer_id}")
                elif status.startswith("error"):
                    print(f"  [FALLO] Error en {title or offer_id}: {status}")

                log.info("  ✓ [%s] %s → %s (Tiempo: %.1fs)", portal_name, title or offer_id, status, elapsed)
                save_application(offer_url, portal_name, title, status)
                _csv_log(portal_name, offer_url, title, status)
                applied += 1

                print(f"  [PROGRESO] Aplicadas {applied}/{max_offers} en {portal_name.upper()}")

                _RATE_COUNTED = {"applied", "filled_no_submit", "dry_run"}
                if status in _RATE_COUNTED:
                    rate_limiter.acquire(portal_name)

                human_delay(1.0, 2.0)

        # ── Motor genérico ────────────────────────────────────────────────────
        else:
            elements = page.query_selector_all(config["selector_oferta"])
            if not elements:
                log.warning("Sin ofertas detectadas con selector: %s", config["selector_oferta"])
                take_error_screenshot(page, portal_name, "no_offers")
                break

            log.info("Ofertas en página: %d", len(elements))
            print(f"  [BUSCANDO] Detectadas {len(elements)} ofertas en página {page_num}")

            # ── Extraer URLs + score por ubicación (prioridad Maipú) ─────────────
            loc_sel = config.get("selector_ubicacion")
            scored: list[tuple[int, str]] = []   # (score, url)

            skipped_sched = 0
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

                    # ── Filtro de horario: descartar turno noche / finde ──────
                    if not schedule_ok(card_text):
                        log.info("  [GEO/SCHED] Descartado por horario: %s",
                                 card_text[:80].strip().replace("\n", " "))
                        skipped_sched += 1
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

                    score = location_score(loc_text)
                    if loc_text and score != 5:
                        log.debug("  [GEO] score=%d loc='%s...' url=%s",
                                  score, loc_text[:40], href[:60])
                    scored.append((score, href))
                except Exception as exc:
                    log.debug("Error extrayendo href: %s", exc)
                    continue

            if skipped_sched:
                log.info("  %d ofertas descartadas por turno incompatible", skipped_sched)
                print(f"  [FILTRO] {skipped_sched} ofertas descartadas por horario (noche/finde/rotativo)")

            # Ordenar: mayor score primero (Maipú y cercanas al inicio)
            scored.sort(key=lambda x: x[0], reverse=True)

            # Loguear si hay reordenamiento visible
            if scored:
                top_score = scored[0][0]
                bot_score = scored[-1][0]
                if top_score != bot_score:
                    print(f"  [GEO] Ofertas ordenadas por cercanía a Maipú"
                          f" (score {top_score}→{bot_score})")

            offer_urls = []
            seen = set()
            for _, url in scored:
                if url not in seen:
                    seen.add(url)
                    offer_urls.append(url)

            for url in offer_urls:
                if applied >= max_offers:
                    break
                if already_applied(url):
                    log.debug("  [skip-db] %s", url)
                    continue

                print(f"  [ABRIENDO] Navegando a oferta: {url[:60]}...")
                title, status = _process_offer_generic(
                    page, url, config, profile, portal_name, dry_run
                )

                if status == "applied":
                    print(f"  [ÉXITO] Postulación completada para: {title or 'Sin Título'}")
                elif status.startswith("error"):
                    print(f"  [FALLO] Error en {title or 'Sin Título'}: {status}")

                log.info("  ✓ [%s] %s → %s", portal_name, title, status)
                save_application(url, portal_name, title, status)
                _csv_log(portal_name, url, title, status)
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

        # ── Paginación ────────────────────────────────────────────────────────
        next_sel = config.get("selector_siguiente_pagina")
        max_pages = config.get("max_pages", 5)   # límite anti-bucle infinito
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

    return applied


def run_bot_multi_keywords(portal_name: str, dry_run: bool = False, headless: bool = False) -> None:
    """
    Abre el browser UNA VEZ y ejecuta una búsqueda por cada keyword del KEYWORD_GROUPS.
    Al compartir el contexto del browser, Cloudflare/login solo se resuelve una vez.
    """
    from .config import KEYWORD_GROUPS, build_config_for_keyword
    from .portals import PORTAL_REGISTRY

    if portal_name not in SITE_CONFIG:
        raise ValueError(f"Portal '{portal_name}' no encontrado.")

    # ── Verificar si el portal está habilitado ────────────────────────────────
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

    run_startup_validation(portal_name, USER_PROFILE, SITE_CONFIG[portal_name])

    rate_limiter = get_rate_limiter(portal_name)
    chrome_exe   = _find_chrome_executable()
    if chrome_exe:
        log.info("Usando Chrome del sistema: %s", chrome_exe)
    else:
        log.info("Usando Chromium de Playwright")

    _PORTAL_LOCALE = {
        "indeed": ("es-CL", "America/Santiago"), "laborum": ("es-CL", "America/Santiago"),
        "getonyboard": ("es-CL", "America/Santiago"), "computrabajo": ("es-CL", "America/Santiago"),
        "linkedin": ("es-CL", "America/Santiago"), "chiletrabajos": ("es-CL", "America/Santiago"),
    }
    _locale, _tz = _PORTAL_LOCALE.get(portal_name, ("es-CL", "America/Santiago"))

    session_dir = str(SESSIONS_DIR / portal_name)
    Path(session_dir).mkdir(exist_ok=True)

    log.info("=== ApplyJob Bot (Multi-Keyword — browser compartido) ===")
    log.info("Portal: %s | grupos: %d", portal_name, len(KEYWORD_GROUPS))

    with sync_playwright() as pw:
        # ── Intentar CDP primero (Chrome del usuario ya abierto) ─────────────
        cdp_result = _try_cdp_connect(pw)
        using_cdp  = cdp_result is not None

        if using_cdp:
            browser, _ctx, page = cdp_result
            log.info("Modo CDP — usando Chrome real del usuario (puerto %d)", _CDP_PORT)
            print(f"[CDP] ✓ Conectado a tu Chrome real — tus sesiones de Indeed/LinkedIn activas.")
        else:
            # ── Fallback: lanzar Chrome propio (sin sesiones reales) ─────────
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

            browser = pw.chromium.launch_persistent_context(**launch_kwargs)
            page    = browser.new_page()
            apply_stealth(page)
            try:
                from playwright_stealth import Stealth
                Stealth().apply_stealth_sync(page)
                log.info("playwright-stealth activo")
            except (ImportError, Exception) as se:
                log.warning("playwright-stealth no disponible (%s) — usando stealth manual", se)

        total_applied = 0

        for group in KEYWORD_GROUPS:
            keyword = group["keyword"]
            mode    = group["mode"]
            label   = group["label"]

            # GetOnBoard es plataforma 100% tech — no tiene ofertas de bodega/logística.
            # Saltamos esos grupos para evitar búsquedas vacías.
            if portal_name == "getonyboard" and label.lower() == "bodega":
                log.info("[gob] Saltando keyword bodega '%s' — GetOnBoard es tech-only.", keyword)
                print(f"  [SKIP] '{keyword}' omitido en GetOnBoard (plataforma tech-only)")
                continue

            config  = build_config_for_keyword(portal_name, keyword)
            profile = dict(USER_PROFILE)
            profile["_mode"] = mode
            max_offers = config.get("max_offers_per_run", 10)

            PortalClass    = PORTAL_REGISTRY.get(portal_name)
            portal_handler = PortalClass(config, profile, dry_run) if PortalClass else None

            log.info("=== Búsqueda atómica [%s] '%s' ===", label.upper(), keyword)
            print(f"\n[BUSQUEDA] [{label.upper()}] Buscando: '{keyword}' en {portal_name.upper()}")

            # Verificar / recuperar página — usar is_closed() y evaluate("1") en lugar
            # de page.url (que puede devolver URL cacheada aunque el target esté muerto)
            page_needs_recreation = False
            try:
                if page.is_closed():
                    page_needs_recreation = True
                else:
                    page.evaluate("1")   # ping real al contexto del browser
            except Exception:
                page_needs_recreation = True

            if page_needs_recreation:
                log.warning("Página cerrada/no responde entre keywords. Recreando...")
                try:
                    page = browser.new_page() if not using_cdp else _ctx.new_page()
                    if not using_cdp:
                        apply_stealth(page)
                except Exception as page_err:
                    log.error("No se pudo recrear la página: %s", page_err)
                    break

            applied = _run_keyword_loop(
                page, browser, portal_name, config, profile,
                max_offers, dry_run, rate_limiter, portal_handler,
                using_cdp=using_cdp,
            )
            total_applied += applied

            print(f"\n[PORTAL_FINALIZADO] --- KEYWORD '{keyword}': {applied} postulaciones ---")

            # ── Pausa anti-Cloudflare entre keywords ──────────────────────────
            # Indeed detecta navegación rápida entre búsquedas distintas y
            # dispara Cloudflare. Un delay de 12-20s simula comportamiento humano.
            if group is not KEYWORD_GROUPS[-1]:
                # Portales con anti-bot agresivo (LinkedIn, Indeed) necesitan pausa más larga
                _strict = portal_name in ("linkedin", "indeed")
                _kw_pause = random.uniform(8.0, 14.0) if _strict else random.uniform(3.0, 6.0)
                log.info("Pausa inter-keyword: %.0fs...", _kw_pause)
                print(f"\n[PAUSA] Esperando {_kw_pause:.0f}s antes de la próxima búsqueda...")
                time.sleep(_kw_pause)

        # En modo CDP solo cerramos la pestaña, NO el browser del usuario
        if using_cdp:
            try:
                page.close()
                log.info("Pestaña del bot cerrada. Chrome del usuario sigue abierto.")
            except Exception:
                pass
        else:
            try:
                browser.close()
            except Exception as close_err:
                log.warning("browser.close() ignorado (ya cerrado): %s", close_err)

    log.info("=== Multi-Keyword Fin. Total aplicadas: %d | Rate: %d/%d ===",
             total_applied, rate_limiter.current_count, rate_limiter.max_actions)
    log.info("Logs CSV: %s", LOGS_DIR)


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
      4. Por cada página: extraer ofertas → deduplicar → rate limit → postular
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

    # ── Verificar si el portal está habilitado ────────────────────────────────
    if not SITE_CONFIG[portal_name].get("enabled", True):
        log.warning("[STANDBY] Portal '%s' deshabilitado — saltando.", portal_name.upper())
        print(f"\n[STANDBY] {portal_name.upper()} está en standby y no se ejecutará.")
        return

    config = config_override if config_override is not None else SITE_CONFIG[portal_name]

    profile = dict(USER_PROFILE)
    if profile_mode:
        profile["_mode"] = profile_mode

    max_offers = config.get("max_offers_per_run", 10)

    # ── 1. Validar configuración ──────────────────────────────────────────────
    run_startup_validation(portal_name, USER_PROFILE, config)

    # ── 2. Cargar portal específico si existe ─────────────────────────────────
    from .portals import PORTAL_REGISTRY
    PortalClass    = PORTAL_REGISTRY.get(portal_name)
    portal_handler = PortalClass(config, profile, dry_run) if PortalClass else None

    # ── 3. Rate limiter ───────────────────────────────────────────────────────
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
        # ── 4. Intentar CDP prioritario (Chrome del usuario ya abierto) ──
        cdp_result = _try_cdp_connect(pw_obj)
        is_cdp = cdp_result is not None

        if is_cdp:
            browser_instance, _ctx, page = cdp_result
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

            browser_instance = pw_obj.chromium.launch_persistent_context(**launch_kwargs)
            page = browser_instance.new_page()
            apply_stealth(page)
            try:
                from playwright_stealth import Stealth
                Stealth().apply_stealth_sync(page)
            except:
                pass

        applied = _run_keyword_loop(
            page, browser_instance, portal_name, config, profile,
            max_offers, dry_run, rate_limiter, portal_handler,
            using_cdp=is_cdp,
        )

        if is_cdp:
            try: page.close()
            except: pass
        else:
            browser_instance.close()
        
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

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
from pathlib import Path

from playwright.sync_api import sync_playwright, Page

from .config import SITE_CONFIG, USER_PROFILE
from .state import already_applied, save_application
from .stealth_utils import (
    apply_stealth, human_delay, human_scroll, human_click,
    take_error_screenshot, random_user_agent, random_viewport,
)
from .form_filler import fill_form
from .retry import with_retry, get_rate_limiter
from .validator import run_startup_validation

log = logging.getLogger("applyjob.engine")

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

def _apply_directa(page: Page, config: dict, profile: dict) -> str:
    """
    Estrategia para postulación directa: un click al botón y submit en la misma página.

    Flujo:
        click(selector_boton_aplicar) → fill_form() → click(submit)

    Args:
        page   : página de Playwright activa
        config : SITE_CONFIG del portal
        profile: USER_PROFILE

    Returns:
        "applied" | "filled_no_submit" | "error: <mensaje>"
    """
    btn_sel = config["selector_boton_aplicar"]
    try:
        human_click(page, btn_sel)
        human_delay(2.0, 4.0)
        fill_form(page, profile)
        for submit_sel in [
            "button[type='submit']", "input[type='submit']",
            "button:has-text('Enviar')", "button:has-text('Submit')",
            "button:has-text('Apply')",  "button:has-text('Postular')",
        ]:
            try:
                if page.query_selector(submit_sel):
                    human_click(page, submit_sel)
                    human_delay(2.0, 3.0)
                    return "applied"
            except Exception:
                continue
        return "filled_no_submit"
    except Exception as exc:
        log.warning("_apply_directa falló: %s", exc)
        return f"error: {exc}"


def _apply_modal(page: Page, config: dict, profile: dict) -> str:
    """
    Estrategia para postulación modal: click abre un overlay,
    se llena dentro del modal y se avanza paso a paso.

    Flujo:
        click(selector_boton_aplicar) → esperar modal → fill_form() × N pasos → submit

    Args:
        page   : página activa
        config : SITE_CONFIG del portal
        profile: USER_PROFILE

    Returns:
        "applied" | "error: <mensaje>"
    """
    btn_sel = config["selector_boton_aplicar"]
    try:
        human_click(page, btn_sel)
        human_delay(2.0, 4.0)
        fill_form(page, profile)
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
                        human_delay(1.5, 3.0)
                        fill_form(page, profile)
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
        portal   : nombre del portal (para logs y screenshots)
        dry_run  : si True, no postula realmente

    Returns:
        Tuple (title, status)
    """
    title = "unknown"
    try:
        def _navigate():
            page.goto(offer_url, wait_until="domcontentloaded", timeout=30_000)

        with_retry(_navigate, attempts=2, delay=5.0, portal=portal)
        human_delay(2.0, 4.0)
        human_scroll(page, steps=2)

        title_sel = config.get("selector_titulo_oferta")
        if title_sel:
            try:
                title = (page.text_content(title_sel, timeout=3_000) or "").strip()[:80]
            except Exception as exc:
                log.debug("No se pudo leer título con '%s': %s", title_sel, exc)

        if dry_run:
            return title, "dry_run"

        tipo = config.get("tipo_postulacion", "directa")
        if tipo == "directa":
            status = _apply_directa(page, config, profile)
        elif tipo == "modal":
            status = _apply_modal(page, config, profile)
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
# run_bot — función principal pública
# ---------------------------------------------------------------------------

def run_bot(portal_name: str, dry_run: bool = False, headless: bool = False) -> None:
    """
    Ejecuta el bot para el portal especificado.

    Pasos:
      1. Validar configuración (USER_PROFILE + SITE_CONFIG)
      2. Abrir browser con sesión persistente (user_data_dir)
      3. Navegar a url_busqueda
      4. Por cada página: extraer ofertas → deduplicar → rate limit → postular
      5. Paginar hasta max_offers o sin siguiente página

    Args:
        portal_name: clave de SITE_CONFIG (ej. "linkedin", "indeed")
        dry_run    : navega y loguea pero NO postula
        headless   : corre sin ventana de browser

    Raises:
        ValueError    : si portal_name no existe en SITE_CONFIG
        ConfigError   : si USER_PROFILE o SITE_CONFIG están mal configurados
    """
    if portal_name not in SITE_CONFIG:
        available = ", ".join(SITE_CONFIG.keys())
        raise ValueError(f"Portal '{portal_name}' no encontrado. Disponibles: {available}")

    config     = SITE_CONFIG[portal_name]
    profile    = USER_PROFILE
    max_offers = config.get("max_offers_per_run", 10)

    # ── 1. Validar configuración ──────────────────────────────────────────────
    run_startup_validation(portal_name, profile, config)

    # ── 2. Cargar portal específico si existe ─────────────────────────────────
    from .portals import PORTAL_REGISTRY
    PortalClass    = PORTAL_REGISTRY.get(portal_name)
    portal_handler = PortalClass(config, profile) if PortalClass else None

    # ── 3. Rate limiter ───────────────────────────────────────────────────────
    rate_limiter = get_rate_limiter(portal_name)

    session_dir = str(SESSIONS_DIR / portal_name)
    Path(session_dir).mkdir(exist_ok=True)

    log.info("=== ApplyJob Bot ===")
    log.info("Portal: %s | max: %d | dry_run: %s | motor: %s | rate_limit: %d/h",
             portal_name, max_offers, dry_run,
             PortalClass.__name__ if PortalClass else "genérico",
             rate_limiter.max_actions)

    with sync_playwright() as pw:
        browser = pw.chromium.launch_persistent_context(
            user_data_dir = session_dir,
            headless      = headless,
            user_agent    = random_user_agent(),
            viewport      = random_viewport(),
            locale        = "es-AR",
            timezone_id   = "America/Argentina/Buenos_Aires",
            args          = ["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )

        page = browser.new_page()
        apply_stealth(page)

        try:
            from playwright_stealth import Stealth
            Stealth().apply_stealth_sync(page)
            log.info("playwright-stealth activo")
        except (ImportError, Exception) as _se:
            log.warning("playwright-stealth no disponible (%s) — usando stealth manual", _se)

        log.info("Navegando a: %s", config["url_busqueda"])
        with_retry(
            lambda: page.goto(config["url_busqueda"], wait_until="domcontentloaded", timeout=30_000),
            attempts=2, delay=5.0, portal=portal_name,
        )
        human_delay(3.0, 5.0)

        applied  = 0
        page_num = 1

        while applied < max_offers:
            log.info("--- Página %d | aplicadas: %d/%d | rate: %d/%d restantes ---",
                     page_num, applied, max_offers,
                     rate_limiter.current_count, rate_limiter.max_actions)
            human_scroll(page, steps=3)
            human_delay(1.5, 3.0)

            # ── Portal específico (ej. LinkedIn) ──────────────────────────────
            if portal_handler:
                offer_ids = portal_handler.get_offer_urls(page)
                if not offer_ids:
                    log.warning("Sin ofertas detectadas con selector: %s",
                                config["selector_oferta"])
                    take_error_screenshot(page, portal_name, "no_offers")
                    break

                log.info("Ofertas en página: %d", len(offer_ids))

                for offer_id in offer_ids:
                    if applied >= max_offers:
                        break

                    offer_url = (portal_handler.get_job_url(page, offer_id)
                                 if hasattr(portal_handler, "get_job_url")
                                 else offer_id)

                    if already_applied(offer_url):
                        log.debug("  [skip-db] %s", offer_url)
                        continue

                    if dry_run:
                        save_application(offer_url, portal_name, "", "dry_run")
                        _csv_log(portal_name, offer_url, "", "dry_run")
                        applied += 1
                        log.info("  [dry_run] %s", offer_url)
                        continue

                    # Rate limiting ANTES de postular
                    rate_limiter.acquire(portal_name)

                    result = portal_handler.apply_to_offer(page, offer_id)
                    status, title = result if isinstance(result, tuple) else (result, "")

                    log.info("  ✓ [%s] %s → %s", portal_name, title or offer_id, status)
                    save_application(offer_url, portal_name, title, status)
                    _csv_log(portal_name, offer_url, title, status)
                    applied += 1
                    human_delay(3.0, 6.0)

            # ── Motor genérico ────────────────────────────────────────────────
            else:
                elements = page.query_selector_all(config["selector_oferta"])
                if not elements:
                    log.warning("Sin ofertas detectadas con selector: %s",
                                config["selector_oferta"])
                    take_error_screenshot(page, portal_name, "no_offers")
                    break

                log.info("Ofertas en página: %d", len(elements))

                offer_urls: list[str] = []
                for el in elements:
                    try:
                        href = el.get_attribute("href")
                        if not href:
                            a = el.query_selector("a[href]")
                            href = a.get_attribute("href") if a else None
                        if href:
                            if not href.startswith("http"):
                                base = "/".join(page.url.split("/")[:3])
                                href = base + href
                            if href not in offer_urls:
                                offer_urls.append(href)
                    except Exception as exc:
                        log.debug("Error extrayendo href: %s", exc)
                        continue

                for url in offer_urls:
                    if applied >= max_offers:
                        break
                    if already_applied(url):
                        log.debug("  [skip-db] %s", url)
                        continue

                    # Rate limiting ANTES de postular
                    rate_limiter.acquire(portal_name)

                    title, status = _process_offer_generic(
                        page, url, config, profile, portal_name, dry_run
                    )
                    log.info("  ✓ [%s] %s → %s", portal_name, title, status)
                    save_application(url, portal_name, title, status)
                    _csv_log(portal_name, url, title, status)
                    applied += 1
                    human_delay(3.0, 6.0)
                    try:
                        page.go_back(wait_until="domcontentloaded")
                        human_delay(2.0, 4.0)
                    except Exception as exc:
                        log.warning("go_back falló: %s", exc)

            # ── Paginación ────────────────────────────────────────────────────
            next_sel = config.get("selector_siguiente_pagina")
            if not next_sel or applied >= max_offers:
                break
            try:
                next_btn = page.query_selector(next_sel)
                if not next_btn or not next_btn.is_visible():
                    log.info("Sin página siguiente. Fin.")
                    break
                human_click(page, next_sel)
                human_delay(3.0, 5.0)
                page_num += 1
            except Exception as exc:
                log.warning("Error al paginar: %s", exc)
                break

        browser.close()

    log.info("=== Fin. Procesadas: %d | Rate usado: %d/%d ===",
             applied, rate_limiter.current_count, rate_limiter.max_actions)
    log.info("Logs CSV: %s", LOGS_DIR)

"""
Motor principal de postulación.
Orquesta navegación, deduplicación, portal-specific logic y logging.
"""
import csv
import datetime
import logging
from pathlib import Path

from playwright.sync_api import sync_playwright, Page

from .config import SITE_CONFIG, USER_PROFILE
from .state import already_applied, save_application
from .stealth_utils import (
    apply_stealth, human_delay, human_scroll,
    take_error_screenshot, random_user_agent, random_viewport,
)
from .form_filler import fill_form

log = logging.getLogger("applyjob")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

BASE_DIR     = Path(__file__).parent.parent
SESSIONS_DIR = BASE_DIR / "sessions"
LOGS_DIR     = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)
SESSIONS_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# CSV log (humano-legible, complementa el SQLite)
# ---------------------------------------------------------------------------
def _csv_log(portal: str, url: str, title: str, status: str, detail: str = "") -> None:
    today = datetime.date.today().isoformat()
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
# Estrategias genéricas (para portales sin clase específica)
# ---------------------------------------------------------------------------
def _apply_directa(page: Page, config: dict, profile: dict) -> str:
    btn_sel = config["selector_boton_aplicar"]
    try:
        from .stealth_utils import human_click
        human_click(page, btn_sel)
        human_delay(2.0, 4.0)
        fill_form(page, profile)
        for submit_sel in [
            "button[type='submit']", "input[type='submit']",
            "button:has-text('Enviar')", "button:has-text('Submit')",
            "button:has-text('Apply')", "button:has-text('Postular')",
        ]:
            try:
                if page.query_selector(submit_sel):
                    human_click(page, submit_sel)
                    human_delay(2.0, 3.0)
                    return "applied"
            except Exception:
                continue
        return "filled_no_submit"
    except Exception as e:
        return f"error: {e}"


def _apply_modal(page: Page, config: dict, profile: dict) -> str:
    from .stealth_utils import human_click
    btn_sel = config["selector_boton_aplicar"]
    try:
        human_click(page, btn_sel)
        human_delay(2.0, 4.0)
        fill_form(page, profile)
        for _ in range(5):
            for next_sel in [
                "button:has-text('Next')", "button:has-text('Siguiente')",
                "button:has-text('Continue')", "button:has-text('Continuar')",
                "button:has-text('Submit')", "button:has-text('Enviar')",
                "button:has-text('Apply')", "button:has-text('Postular')",
                "button[aria-label='Submit application']",
            ]:
                try:
                    btn = page.query_selector(next_sel)
                    if btn and btn.is_visible():
                        btn.click()
                        human_delay(1.5, 3.0)
                        fill_form(page, profile)
                        break
                except Exception:
                    continue
        return "applied"
    except Exception as e:
        return f"error: {e}"


def _apply_externa(page: Page, config: dict) -> str:
    from .stealth_utils import human_click
    btn_sel = config["selector_boton_aplicar"]
    try:
        with page.context.expect_page() as new_page_info:
            human_click(page, btn_sel)
        new_page = new_page_info.value
        new_page.wait_for_load_state("domcontentloaded")
        external_url = new_page.url
        new_page.close()
        return f"external: {external_url}"
    except Exception as e:
        return f"error_externa: {e}"


# ---------------------------------------------------------------------------
# Procesar oferta — modo genérico
# ---------------------------------------------------------------------------
def _process_offer_generic(
    page: Page, offer_url: str, config: dict, profile: dict,
    portal: str, dry_run: bool
) -> tuple[str, str]:
    """Retorna (title, status)."""
    title = "unknown"
    try:
        page.goto(offer_url, wait_until="domcontentloaded", timeout=30_000)
        human_delay(2.0, 4.0)
        human_scroll(page, steps=2)

        title_sel = config.get("selector_titulo_oferta")
        if title_sel:
            try:
                title = (page.text_content(title_sel, timeout=3_000) or "").strip()[:80]
            except Exception:
                pass

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

    except Exception as e:
        screenshot = take_error_screenshot(page, portal, "offer_error")
        log.error("  Error en oferta %s: %s | screenshot: %s", offer_url, e, screenshot)
        return title, f"error: {e}"


# ---------------------------------------------------------------------------
# run_bot — función principal pública
# ---------------------------------------------------------------------------
def run_bot(portal_name: str, dry_run: bool = False, headless: bool = False) -> None:
    """
    Ejecuta el bot para el portal especificado.

    Args:
        portal_name : clave de SITE_CONFIG (ej. "linkedin", "indeed")
        dry_run     : navega y loguea pero NO postula
        headless    : corre sin ventana de browser
    """
    if portal_name not in SITE_CONFIG:
        available = ", ".join(SITE_CONFIG.keys())
        raise ValueError(f"Portal '{portal_name}' no encontrado. Disponibles: {available}")

    config     = SITE_CONFIG[portal_name]
    profile    = USER_PROFILE
    max_offers = config.get("max_offers_per_run", 10)

    # Cargar portal específico si existe
    from .portals import PORTAL_REGISTRY
    PortalClass = PORTAL_REGISTRY.get(portal_name)
    portal_handler = PortalClass(config, profile) if PortalClass else None

    session_dir = str(SESSIONS_DIR / portal_name)
    Path(session_dir).mkdir(exist_ok=True)

    log.info("=== ApplyJob Bot ===")
    log.info("Portal: %s | max: %d | dry_run: %s | específico: %s",
             portal_name, max_offers, dry_run, PortalClass is not None)

    with sync_playwright() as pw:
        browser = pw.chromium.launch_persistent_context(
            user_data_dir=session_dir,
            headless=headless,
            user_agent=random_user_agent(),
            viewport=random_viewport(),
            locale="es-AR",
            timezone_id="America/Argentina/Buenos_Aires",
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )

        page = browser.new_page()
        apply_stealth(page)

        try:
            from playwright_stealth import stealth_sync
            stealth_sync(page)
            log.info("playwright-stealth activo")
        except ImportError:
            log.warning("playwright-stealth no instalado — usando stealth manual")

        log.info("Navegando a: %s", config["url_busqueda"])
        page.goto(config["url_busqueda"], wait_until="domcontentloaded", timeout=30_000)
        human_delay(3.0, 5.0)

        applied = 0
        page_num = 1

        while applied < max_offers:
            log.info("--- Página %d ---", page_num)
            human_scroll(page, steps=3)
            human_delay(1.5, 3.0)

            # ── Portal específico (ej. LinkedIn) ──────────────────────────
            if portal_handler:
                offer_ids = portal_handler.get_offer_urls(page)
                if not offer_ids:
                    log.warning("Sin ofertas con selector: %s", config["selector_oferta"])
                    take_error_screenshot(page, portal_name, "no_offers")
                    break

                log.info("Ofertas encontradas: %d", len(offer_ids))

                for offer_id in offer_ids:
                    if applied >= max_offers:
                        break

                    offer_url = portal_handler.get_job_url(page, offer_id) \
                        if hasattr(portal_handler, "get_job_url") else offer_id

                    if already_applied(offer_url):
                        log.info("  [skip] ya procesado: %s", offer_url)
                        continue

                    if dry_run:
                        save_application(offer_url, portal_name, "", "dry_run")
                        _csv_log(portal_name, offer_url, "", "dry_run")
                        applied += 1
                        continue

                    title  = ""
                    status = portal_handler.apply_to_offer(page, offer_id)
                    log.info("  status: %s", status)

                    save_application(offer_url, portal_name, title, status)
                    _csv_log(portal_name, offer_url, title, status)
                    applied += 1
                    human_delay(3.0, 6.0)

            # ── Motor genérico ─────────────────────────────────────────────
            else:
                elements = page.query_selector_all(config["selector_oferta"])
                if not elements:
                    log.warning("Sin ofertas con selector: %s", config["selector_oferta"])
                    take_error_screenshot(page, portal_name, "no_offers")
                    break

                log.info("Ofertas encontradas: %d", len(elements))

                offer_urls = []
                for el in elements:
                    try:
                        href = el.get_attribute("href")
                        if not href:
                            a = el.query_selector("a[href]")
                            href = a.get_attribute("href") if a else None
                        if href:
                            if not href.startswith("http"):
                                base = page.url.split("/")[0] + "//" + page.url.split("/")[2]
                                href = base + href
                            if href not in offer_urls:
                                offer_urls.append(href)
                    except Exception:
                        continue

                for url in offer_urls:
                    if applied >= max_offers:
                        break
                    if already_applied(url):
                        log.info("  [skip] ya procesado: %s", url)
                        continue

                    title, status = _process_offer_generic(
                        page, url, config, profile, portal_name, dry_run
                    )
                    log.info("  [%s] %s → %s", portal_name, title, status)

                    save_application(url, portal_name, title, status)
                    _csv_log(portal_name, url, title, status)
                    applied += 1
                    human_delay(3.0, 6.0)
                    page.go_back(wait_until="domcontentloaded")
                    human_delay(2.0, 4.0)

            # ── Paginación ─────────────────────────────────────────────────
            next_sel = config.get("selector_siguiente_pagina")
            if not next_sel or applied >= max_offers:
                break
            try:
                from .stealth_utils import human_click
                next_btn = page.query_selector(next_sel)
                if not next_btn or not next_btn.is_visible():
                    log.info("Sin página siguiente. Fin.")
                    break
                human_click(page, next_sel)
                human_delay(3.0, 5.0)
                page_num += 1
            except Exception as e:
                log.warning("Error paginando: %s", e)
                break

        browser.close()

    log.info("=== Fin. Postulaciones procesadas: %d ===", applied)
    log.info("Logs: %s", LOGS_DIR)

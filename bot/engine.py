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
from .notifier import notifier

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

    # Cargar portal específico si existe, sino usar GenericPortal
    from .portals import PORTAL_REGISTRY
    from .portals.base import GenericPortal
    PortalClass = PORTAL_REGISTRY.get(portal_name, GenericPortal)
    portal_handler = PortalClass(config, profile)

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

# ── Bucle principal de ofertas ──────────────────────────────────────
        while applied < max_offers:
            log.info("--- Página %d ---", page_num)
            human_scroll(page, steps=3)
            human_delay(1.5, 3.0)

            # Usar el handler (específico o genérico) para obtener las ofertas
            offer_ids = portal_handler.get_offer_urls(page)
            if not offer_ids:
                log.warning("Sin ofertas con selector: %s", config["selector_oferta"])
                take_error_screenshot(page, portal_name, "no_offers")
                break

            log.info("Ofertas encontradas: %d", len(offer_ids))

            for offer_id in offer_ids:
                if applied >= max_offers:
                    break

                try:
                    # Obtener URL canónica para deduplicación y logs
                    offer_url = portal_handler.get_job_url(page, offer_id)
                    
                    if already_applied(offer_url):
                        log.info("  [skip] ya procesado: %s", offer_url)
                        continue

                    if dry_run:
                        log.info("  [dry_run] %s", offer_url)
                        save_application(offer_url, portal_name, "", "dry_run")
                        _csv_log(portal_name, offer_url, "", "dry_run")
                        applied += 1
                        continue

                    # Postular
                    log.info("  Procesando: %s", offer_url)
                    status = portal_handler.apply_to_offer(page, offer_id)
                    log.info("  status: %s", status)

                    # Extraer título si es posible
                    title = ""
                    
                    save_application(offer_url, portal_name, title, status)
                    _csv_log(portal_name, offer_url, title, status)
                    applied += 1
                    human_delay(3.0, 6.0)

                    # Si es un portal que navega (no modal), volver atrás
                    if not getattr(portal_handler, "is_modal", False) and portal_name != "linkedin":
                        if page.url != config["url_busqueda"] and not "search" in page.url:
                            page.go_back(wait_until="domcontentloaded")
                            human_delay(2.0, 4.0)
                except Exception as e:
                    log.error("Error procesando oferta: %s", e)
                    continue

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
        
        # Notificación final del portal
        total_p = page_num * 10 # Estimado o real
        notifier.send_summary(portal_name, applied, 0, applied) # Por ahora enviamos lo aplicado

    log.info("=== Fin. Postulaciones procesadas: %d ===", applied)
    log.info("Logs: %s", LOGS_DIR)

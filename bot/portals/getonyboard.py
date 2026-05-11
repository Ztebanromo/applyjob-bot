"""
Portal GetOnBoard — postulación externa.

GetOnBoard requiere cuenta propia para postular.
Este handler navega a cada oferta, registra la URL de postulación
y hace click en "Postular" para abrir el formulario.
Si el usuario no tiene cuenta, se registra como external_apply.

Flujo:
  1. get_offer_urls: extrae hrefs de a.gb-results-list__item
  2. apply_to_offer: navega al job, extrae título, intenta click en "Postular"
     - Si abre en la misma pestaña → registra external_apply
     - Si abre en nueva pestaña → registra external_apply con URL
"""
import logging

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from .base import BasePortal
from ..stealth_utils import human_delay, micro_delay, take_error_screenshot

log = logging.getLogger("applyjob.getonyboard")

SEL = {
    "card":        "a.gb-results-list__item",
    "apply_btn":   "a#apply_bottom, a#apply_bottom_short, a.js-go-to-apply",
    "job_title":   "h1.gb-landing-cover__title, h1[class*='title'], h1",
}


class GetOnBoardPortal(BasePortal):

    def get_offer_urls(self, page: Page) -> list[str]:
        """Extrae todos los hrefs de las tarjetas de oferta."""
        seen: set[str] = set()
        urls: list[str] = []
        try:
            cards = page.query_selector_all(SEL["card"])
            log.debug("GetOnBoardPortal: %d cards encontradas", len(cards))
            for card in cards:
                try:
                    href = card.get_attribute("href") or ""
                    if not href:
                        continue
                    if not href.startswith("http"):
                        href = "https://www.getonbrd.com" + href
                    if href not in seen:
                        seen.add(href)
                        urls.append(href)
                except Exception as exc:
                    log.debug("Error extrayendo href de card: %s", exc)
        except Exception as exc:
            log.warning("GetOnBoardPortal.get_offer_urls error: %s", exc)
        return urls

    def apply_to_offer(self, page: Page, offer_url: str) -> tuple[str, str]:
        """
        Navega a la oferta y hace click en "Postular".
        Registra el resultado como external_apply (GetOnBoard requiere cuenta).
        """
        title = "unknown"

        try:
            page.goto(offer_url, wait_until="domcontentloaded", timeout=30_000)
            human_delay(2.0, 3.5)

            # Extraer título
            try:
                for sel in SEL["job_title"].split(","):
                    el = page.query_selector(sel.strip())
                    if el:
                        title = (el.text_content() or "").strip()[:80]
                        break
            except Exception:
                pass

            # Buscar y clickear botón "Postular"
            apply_url = offer_url  # fallback
            try:
                for sel_part in SEL["apply_btn"].split(","):
                    sel_part = sel_part.strip()
                    btn = page.query_selector(sel_part)
                    if btn and btn.is_visible():
                        apply_href = btn.get_attribute("href") or ""
                        if apply_href:
                            if not apply_href.startswith("http"):
                                apply_href = "https://www.getonbrd.com" + apply_href
                            apply_url = apply_href
                        log.debug("  [gob] Botón Postular encontrado: %s", apply_url[:60])

                        # Intentar click — puede abrir en misma pestaña o nueva
                        try:
                            with page.context.expect_page(timeout=4_000) as new_pg:
                                btn.click()
                            new_page = new_pg.value
                            new_page.wait_for_load_state("domcontentloaded", timeout=10_000)
                            apply_url = new_page.url
                            new_page.close()
                        except PlaywrightTimeout:
                            # Abrió en la misma pestaña — tomar la URL actual
                            human_delay(2.0, 3.0)
                            apply_url = page.url
                        break
            except Exception as exc:
                log.debug("  [gob] Error clickeando Postular: %s", exc)

            log.info("  [gob] external_apply: %s", apply_url[:70])
            return "external_apply", title

        except Exception as exc:
            log.warning("  [gob] Error navegando a %s: %s", offer_url[:60], exc)
            return f"error: {exc}", title

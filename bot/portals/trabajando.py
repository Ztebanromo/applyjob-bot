"""
Portal: Trabajando.com (Chile)
Tipo: external — redirige al formulario del empleador.
URL: https://www.trabajando.com

No requiere login para ver ofertas; el botón de postulación redirige
al sitio del empleador o al formulario de Trabajando.
"""
from playwright.sync_api import Page
from bot.portals.base import BasePortal
from bot.stealth_utils import human_delay


class TrabajandoPortal(BasePortal):

    def apply_to_offer(self, page: Page, offer_url: str) -> str:
        try:
            page.goto(offer_url, timeout=25_000, wait_until="domcontentloaded")
            human_delay(0.8, 1.8)

            # Intentar varios selectores de botón de postulación
            apply_btn = (
                page.query_selector("a:has-text('Postular')") or
                page.query_selector("button:has-text('Postular')") or
                page.query_selector("a:has-text('Aplicar')") or
                page.query_selector("a.btn-postular") or
                page.query_selector("button.btn-postular")
            )
            if not apply_btn:
                return "skipped_no_apply_button"

            if self.dry_run:
                return "dry_run"

            apply_btn.click()
            human_delay(1.0, 2.5)

            # Si redirigió a página externa del empleador
            if "trabajando.com" not in page.url:
                return "external:trabajando"

            return "applied"

        except Exception as e:
            return f"error: {str(e)[:80]}"

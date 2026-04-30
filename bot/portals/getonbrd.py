"""
Portal específico de GetOnBrd.
"""
import logging
from playwright.sync_api import Page

from .base import BasePortal
from ..stealth_utils import human_delay, human_click

log = logging.getLogger("applyjob.getonbrd")

class GetOnBrdPortal(BasePortal):

    def apply_to_offer(self, page: Page, offer_url: str) -> str:
        """Flujo para GetOnBrd."""
        try:
            page.goto(offer_url, wait_until="domcontentloaded", timeout=30_000)
            human_delay(2.0, 4.0)

            # GetOnBrd suele tener botones que llevan a un formulario interno o externo
            btn = page.query_selector("a.btn-primary[href*='apply'], button.apply-btn")
            if not btn:
                return "skipped_no_apply_button"

            href = btn.get_attribute("href")
            if href and not href.startswith("#"):
                # Es un link externo
                return f"external: {href}"

            # Si es un botón que abre algo interno
            human_click(page, "a.btn-primary[href*='apply']")
            human_delay(2.0, 4.0)
            
            # Aquí se podría añadir fill_form si hay campos internos
            return "applied_check_manually"

        except Exception as e:
            return f"error: {e}"

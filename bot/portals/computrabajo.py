"""
Portal específico de Computrabajo.
"""
import logging
from playwright.sync_api import Page

from .base import BasePortal
from ..stealth_utils import human_delay, human_click, human_scroll
from ..form_filler import fill_form

log = logging.getLogger("applyjob.computrabajo")

SEL = {
    "apply_btn":    "a.btn_postular, button.postular, #btnPostular",
    "submit_btn":   "button[type='submit'], .btn_enviar",
    "success":      ".postulacion_exitosa, .message_success",
}

class ComputrabajoPortal(BasePortal):

    def apply_to_offer(self, page: Page, offer_url: str) -> str:
        """Flujo para Computrabajo."""
        try:
            page.goto(offer_url, wait_until="domcontentloaded", timeout=30_000)
            human_delay(2.0, 4.0)

            apply_btn = page.query_selector(SEL["apply_btn"])
            if not apply_btn:
                # Quizás ya postulamos o requiere login
                if "ya te postulaste" in page.content().lower():
                    return "skipped_already_applied"
                return "error: apply_button_not_found"

            human_click(page, SEL["apply_btn"])
            human_delay(2.0, 4.0)

            # Llenar posibles preguntas o CV
            fill_form(page, self.profile)
            
            # Intentar enviar
            submit = page.query_selector(SEL["submit_btn"])
            if submit and submit.is_visible():
                submit.click()
                human_delay(2.0, 4.0)
                return "applied"

            return "applied_direct" # A veces el primer click ya postula si el perfil está completo

        except Exception as e:
            return f"error: {e}"

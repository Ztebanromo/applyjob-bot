"""
Portal específico de Indeed Apply.
"""
import logging
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from .base import BasePortal
from ..stealth_utils import human_delay, human_click, human_scroll, take_error_screenshot, micro_delay
from ..form_filler import fill_form

log = logging.getLogger("applyjob.indeed")

# Selectores Indeed (comunes para Indeed Apply)
SEL = {
    "job_card":          "div.job_seen_beacon",
    "apply_btn":         "button#indeedApplyButton, span.indeed-apply-button",
    "modal_iframe":      "iframe[title*='Indeed Apply'], iframe[id*='indeed-apply']",
    "next_btn":          "button:has-text('Continue'), button:has-text('Continuar'), button.ia-continueButton",
    "submit_btn":        "button:has-text('Submit application'), button:has-text('Enviar postulación')",
    "close_modal":       "button[aria-label='Close'], button[aria-label='Cerrar']",
    "job_title":         "h2.jobsearch-JobInfoHeader-title",
}

class IndeedPortal(BasePortal):

    def get_offer_urls(self, page: Page) -> list[str]:
        """Extrae URLs o identificadores de las ofertas en la lista."""
        cards = page.query_selector_all(SEL["job_card"])
        urls = []
        for card in cards:
            try:
                # Indeed a veces requiere click en la card para ver detalles
                # Intentamos extraer el link directo si existe
                link = card.query_selector("a[id*='job_']")
                if link:
                    href = link.get_attribute("href")
                    if href:
                        if not href.startswith("http"):
                            href = "https://www.indeed.com" + href
                        urls.append(href)
            except Exception:
                continue
        return urls

    def apply_to_offer(self, page: Page, offer_url: str) -> str:
        """Flujo para Indeed Apply."""
        try:
            page.goto(offer_url, wait_until="domcontentloaded", timeout=30_000)
            human_delay(2.0, 4.0)
            
            # 1. Detectar botón de Indeed Apply
            apply_btn = page.query_selector(SEL["apply_btn"])
            if not apply_btn:
                return "skipped_no_indeed_apply"

            # 2. Click para abrir modal (a veces abre nueva ventana o iframe)
            human_click(page, SEL["apply_btn"])
            human_delay(3.0, 5.0)

            # Indeed Apply suele abrirse en un iframe o en una nueva pestaña
            # Intentamos detectar si hay un iframe
            iframe_el = page.query_selector(SEL["modal_iframe"])
            if iframe_el:
                frame = iframe_el.content_frame()
                if frame:
                    return self._process_apply_frame(frame)
            
            # Si no es iframe, podría ser la página principal (si redirigió)
            return self._process_apply_frame(page)

        except Exception as e:
            log.error("Error en Indeed: %s", e)
            return f"error: {e}"

    def _process_apply_frame(self, frame) -> str:
        """Procesa los pasos dentro del iframe/página de postulación."""
        max_steps = 10
        for step in range(max_steps):
            try:
                # Llenar formulario en el paso actual
                fill_form(frame, self.profile)
                human_delay(1.0, 2.0)

                # Intentar enviar
                submit = frame.query_selector(SEL["submit_btn"])
                if submit and submit.is_visible():
                    submit.click()
                    human_delay(3.0, 5.0)
                    return "applied"

                # Intentar continuar
                next_btn = frame.query_selector(SEL["next_btn"])
                if next_btn and next_btn.is_visible():
                    next_btn.click()
                    human_delay(2.0, 4.0)
                else:
                    # Si no hay ni next ni submit, quizás terminó o hay un error
                    break
            except Exception as e:
                log.warning("Paso %d falló: %s", step, e)
                break
        
        return "error: steps_unfinished"

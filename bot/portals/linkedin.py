"""
Portal específico de LinkedIn Easy Apply.

Flujo real de LinkedIn:
  1. Página de búsqueda → lista de jobs en panel izquierdo
  2. Click en card → panel derecho muestra detalles (no navega)
  3. Click "Easy Apply" → modal multi-step se abre
  4. Cada step: fill form → Next → hasta Submit application
  5. Detectar: step counter, preguntas de screening, dropdowns

Casos especiales manejados:
  - Jobs sin Easy Apply (solo "Apply") → skip
  - Modal con >6 pasos → skip (demasiado complejo)
  - CAPTCHA / "Verify you're human" → pausa y screenshot
  - "Already applied" banner → skip
"""
import logging
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from .base import BasePortal
from ..stealth_utils import human_delay, human_click, human_scroll, take_error_screenshot, micro_delay
from ..form_filler import fill_form

log = logging.getLogger("applyjob.linkedin")

# Selectores LinkedIn (actualizados a 2024)
SEL = {
    "job_card":          "li.jobs-search-results__list-item",
    "job_card_link":     "a.job-card-list__title",
    "easy_apply_btn":    "button.jobs-apply-button--top-card, button[aria-label*='Easy Apply']",
    "external_apply":    "button.jobs-apply-button--top-card:not([aria-label*='Easy Apply'])",
    "already_applied":   "span.artdeco-inline-feedback__message",
    "modal":             "div.jobs-easy-apply-modal",
    "modal_title":       "h3.jobs-easy-apply-modal__title",
    "step_indicator":    "span.t-14.t-black--light",        # "Step X of Y"
    "next_btn":          "button[aria-label='Continue to next step']",
    "review_btn":        "button[aria-label='Review your application']",
    "submit_btn":        "button[aria-label='Submit application']",
    "close_modal":       "button[aria-label='Dismiss'], button[aria-label='Cerrar']",
    "job_title_panel":   "h1.job-details-jobs-unified-top-card__job-title",
    "captcha_check":     "div.challenge-dialog, iframe[title*='security']",
    "discard_btn":       "button[data-control-name='discard_application_confirm_btn']",
    "dismiss_discard":   "button[aria-label='Dismiss']",
}

MAX_MODAL_STEPS = 6


class LinkedInPortal(BasePortal):

    def get_offer_urls(self, page: Page) -> list[str]:
        """
        LinkedIn no usa href por oferta — los jobs se cargan en panel.
        Retorna los índices de las cards para procesarlas en orden.
        Usamos una lista de 'data-job-id' como identificador único.
        """
        cards = page.query_selector_all(SEL["job_card"])
        job_ids = []
        for card in cards:
            try:
                job_id = card.get_attribute("data-job-id") or card.get_attribute("data-occludable-job-id")
                if job_id and job_id not in job_ids:
                    job_ids.append(job_id)
            except Exception:
                continue
        return job_ids

    def _click_job_card(self, page: Page, job_id: str) -> str:
        """Hace click en la card del job y retorna el título del panel."""
        card_sel = f"li[data-job-id='{job_id}'], li[data-occludable-job-id='{job_id}']"
        try:
            card = page.wait_for_selector(card_sel, timeout=5_000)
            card.scroll_into_view_if_needed()
            micro_delay()
            card.click()
            human_delay(2.0, 3.5)
            # Esperar a que el panel derecho cargue
            page.wait_for_selector(SEL["easy_apply_btn"], timeout=8_000)
            try:
                title = page.text_content(SEL["job_title_panel"], timeout=3_000) or ""
                return title.strip()[:80]
            except Exception:
                return f"job_{job_id}"
        except PlaywrightTimeout:
            return ""

    def _is_already_applied(self, page: Page) -> bool:
        try:
            feedback = page.query_selector(SEL["already_applied"])
            if feedback:
                text = feedback.text_content() or ""
                return "applied" in text.lower() or "postulaste" in text.lower()
        except Exception:
            pass
        return False

    def _has_easy_apply(self, page: Page) -> bool:
        try:
            btn = page.query_selector(SEL["easy_apply_btn"])
            return btn is not None and btn.is_visible()
        except Exception:
            return False

    def _detect_step_count(self, page: Page) -> tuple[int, int]:
        """Intenta leer 'Step X of Y' del modal. Retorna (current, total)."""
        try:
            indicators = page.query_selector_all(SEL["step_indicator"])
            for el in indicators:
                text = el.text_content() or ""
                import re
                m = re.search(r"(\d+)\s+(?:of|de)\s+(\d+)", text, re.IGNORECASE)
                if m:
                    return int(m.group(1)), int(m.group(2))
        except Exception:
            pass
        return 1, 1

    def _fill_modal_step(self, page: Page) -> None:
        """Llena los campos del step actual del modal."""
        fill_form(page, self.profile)
        self._handle_dropdowns(page)

    def _handle_dropdowns(self, page: Page) -> None:
        """
        Intenta responder selects de screening comunes:
        experiencia laboral, autorización, idioma.
        """
        try:
            selects = page.query_selector_all("select")
            for sel_el in selects:
                if not sel_el.is_visible():
                    continue
                options = sel_el.query_selector_all("option")
                option_values = [o.get_attribute("value") or "" for o in options]
                current = sel_el.input_value()
                if current and current not in ("", "Select an option", "Selecciona una opción"):
                    continue
                # Preferir "Yes" / "Sí" / primer valor no vacío
                for opt_val in option_values:
                    lower = opt_val.lower()
                    if lower in ("yes", "si", "sí", "true", "1", "authorized", "no", "n/a"):
                        sel_el.select_option(opt_val)
                        micro_delay()
                        break
                    if opt_val and opt_val != "Select an option":
                        sel_el.select_option(opt_val)
                        micro_delay()
                        break
        except Exception:
            pass

    def _advance_modal(self, page: Page) -> bool:
        """
        Intenta avanzar al siguiente paso o enviar.
        Retorna True si el modal sigue abierto, False si se cerró (submit exitoso).
        """
        for btn_sel in [SEL["submit_btn"], SEL["review_btn"], SEL["next_btn"]]:
            try:
                btn = page.query_selector(btn_sel)
                if btn and btn.is_visible() and btn.is_enabled():
                    btn.click()
                    human_delay(2.0, 3.5)
                    if btn_sel == SEL["submit_btn"]:
                        return False  # aplicación enviada
                    return True
            except Exception:
                continue
        return True

    def _close_modal_safely(self, page: Page) -> None:
        """Cierra el modal sin enviar, descartando el borrador."""
        try:
            close = page.query_selector(SEL["close_modal"])
            if close and close.is_visible():
                close.click()
                human_delay(1.0, 2.0)
            # Confirmar descarte si aparece diálogo
            discard = page.query_selector(SEL["discard_btn"])
            if discard and discard.is_visible():
                discard.click()
                human_delay(1.0, 2.0)
        except Exception:
            pass

    def apply_to_offer(self, page: Page, job_id: str) -> str:
        """
        Flujo completo para una card de LinkedIn identificada por job_id.
        """
        # 1. Click en la card → cargar panel
        title = self._click_job_card(page, job_id)
        if not title:
            return "error: card_not_found"

        log.info("  [LinkedIn] %s", title)

        # 2. Verificar si ya postulé
        if self._is_already_applied(page):
            log.info("  → ya postulado, skip")
            return "skipped_already_applied"

        # 3. Verificar Easy Apply disponible
        if not self._has_easy_apply(page):
            log.info("  → sin Easy Apply, skip")
            return "skipped_no_easy_apply"

        # 4. Abrir modal
        try:
            human_click(page, SEL["easy_apply_btn"])
            human_delay(2.0, 3.5)
            page.wait_for_selector(SEL["modal"], timeout=8_000)
        except PlaywrightTimeout:
            screenshot = take_error_screenshot(page, "linkedin", f"modal_open_{job_id}")
            log.warning("  Modal no abrió. Screenshot: %s", screenshot)
            return "error: modal_timeout"

        # 5. Detectar CAPTCHA
        if page.query_selector(SEL["captcha_check"]):
            self._close_modal_safely(page)
            return "skipped_captcha"

        # 6. Loop de pasos del modal
        step = 0
        while step < MAX_MODAL_STEPS:
            step += 1
            current_step, total_steps = self._detect_step_count(page)
            log.info("  Paso %d/%d", current_step, total_steps)

            if total_steps > MAX_MODAL_STEPS:
                log.info("  Modal muy largo (%d pasos), skip", total_steps)
                self._close_modal_safely(page)
                return f"skipped_complex_{total_steps}_steps"

            self._fill_modal_step(page)
            human_delay(1.0, 2.0)

            modal_still_open = self._advance_modal(page)
            if not modal_still_open:
                log.info("  ✓ Aplicación enviada")
                return "applied"

            # Verificar si el modal se cerró inesperadamente
            if not page.query_selector(SEL["modal"]):
                return "applied"

        # Si salimos del loop sin submit
        self._close_modal_safely(page)
        return "error: max_steps_exceeded"

    def get_job_url(self, page: Page, job_id: str) -> str:
        """Construye la URL canónica de LinkedIn para logs."""
        return f"https://www.linkedin.com/jobs/view/{job_id}/"

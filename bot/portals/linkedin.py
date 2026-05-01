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
import re  # BUG FIX: import a nivel de módulo, no dentro de función
import logging
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from .base import BasePortal
from ..stealth_utils import human_delay, human_click, take_error_screenshot, micro_delay
from ..form_filler import fill_form

log = logging.getLogger("applyjob.linkedin")

# Selectores LinkedIn (actualizados a 2024)
SEL = {
    "job_card":          "li.jobs-search-results__list-item",
    "job_card_link":     "a.job-card-list__title",
    "easy_apply_btn":    "button.jobs-apply-button--top-card, button[aria-label*='Easy Apply']",
    "already_applied":   "span.artdeco-inline-feedback__message",
    "modal":             "div.jobs-easy-apply-modal",
    "modal_title":       "h3.jobs-easy-apply-modal__title",
    "step_indicator":    "span.t-14.t-black--light",
    "next_btn":          "button[aria-label='Continue to next step']",
    "review_btn":        "button[aria-label='Review your application']",
    "submit_btn":        "button[aria-label='Submit application']",
    "close_modal":       "button[aria-label='Dismiss'], button[aria-label='Cerrar']",
    "job_title_panel":   "h1.job-details-jobs-unified-top-card__job-title",
    "captcha_check":     "div.challenge-dialog, iframe[title*='security']",
    "discard_btn":       "button[data-control-name='discard_application_confirm_btn']",
}

MAX_MODAL_STEPS = 6

# Valores que indican respuesta afirmativa en dropdowns de screening
YES_VALUES = {"yes", "si", "sí", "true", "1", "authorized", "yes, i am authorized",
              "i am authorized", "sí, estoy autorizado"}

# Valores a evitar seleccionar en dropdowns (respuestas negativas)
NO_VALUES = {"no", "false", "0", "not authorized", "no estoy autorizado",
             "select an option", "selecciona una opción", ""}


class LinkedInPortal(BasePortal):

    def get_offer_urls(self, page: Page) -> list[str]:
        """
        LinkedIn carga jobs en panel lateral — no navega a URLs individuales.
        Retorna lista de job_ids (data-job-id) de las cards visibles.
        """
        cards = page.query_selector_all(SEL["job_card"])
        job_ids = []
        for card in cards:
            try:
                job_id = (card.get_attribute("data-job-id")
                          or card.get_attribute("data-occludable-job-id"))
                if job_id and job_id not in job_ids:
                    job_ids.append(job_id)
            except Exception:
                continue
        return job_ids

    def get_job_url(self, page: Page, job_id: str) -> str:
        """URL canónica de LinkedIn para deduplicación en SQLite."""
        return f"https://www.linkedin.com/jobs/view/{job_id}/"

    def _click_job_card(self, page: Page, job_id: str) -> str:
        """
        Hace click en la card y retorna el título del panel derecho.
        Retorna '' si la card no se encuentra o no carga.
        """
        card_sel = (f"li[data-job-id='{job_id}'], "
                    f"li[data-occludable-job-id='{job_id}']")
        try:
            card = page.wait_for_selector(card_sel, timeout=5_000)
            card.scroll_into_view_if_needed()
            micro_delay()
            card.click()
            human_delay(2.0, 3.5)
            # Esperar panel derecho — cualquiera de los dos botones
            page.wait_for_selector(
                f"{SEL['easy_apply_btn']}, button.jobs-apply-button--top-card",
                timeout=8_000,
            )
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
                text = (feedback.text_content() or "").lower()
                return "applied" in text or "postulaste" in text
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
        """Lee 'Step X of Y' del modal. Retorna (current, total)."""
        try:
            for el in page.query_selector_all(SEL["step_indicator"]):
                text = el.text_content() or ""
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
        Responde selects de screening:
        1. Prefiere valores afirmativos explícitos (yes/sí/authorized)
        2. Si no hay afirmativo, elige el primer valor que NO sea negativo
        3. Nunca selecciona valores de la lista NO_VALUES

        BUG FIX anterior: el código viejo seleccionaba el primer valor no vacío
        aunque fuera "No" o "Not authorized".
        """
        try:
            selects = page.query_selector_all("select")
            for sel_el in selects:
                if not sel_el.is_visible():
                    continue

                options = sel_el.query_selector_all("option")
                option_values = [o.get_attribute("value") or "" for o in options]

                # No sobreescribir si ya tiene selección válida
                current = (sel_el.input_value() or "").strip()
                if current and current.lower() not in NO_VALUES:
                    continue

                # Paso 1: buscar valor afirmativo explícito
                chosen = None
                for val in option_values:
                    if val.lower() in YES_VALUES:
                        chosen = val
                        break

                # Paso 2: primer valor que no sea negativo ni vacío
                if not chosen:
                    for val in option_values:
                        if val and val.lower() not in NO_VALUES:
                            chosen = val
                            break

                if chosen:
                    sel_el.select_option(chosen)
                    micro_delay()

        except Exception:
            pass

    def _advance_modal(self, page: Page) -> bool:
        """
        Avanza al siguiente paso o envía la aplicación.
        Retorna True si el modal sigue abierto, False si se cerró (submit exitoso).
        """
        for btn_sel in [SEL["submit_btn"], SEL["review_btn"], SEL["next_btn"]]:
            try:
                btn = page.query_selector(btn_sel)
                if btn and btn.is_visible() and btn.is_enabled():
                    btn.click()
                    human_delay(2.0, 3.5)
                    return btn_sel != SEL["submit_btn"]  # False si fue submit
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
            discard = page.query_selector(SEL["discard_btn"])
            if discard and discard.is_visible():
                discard.click()
                human_delay(1.0, 2.0)
        except Exception:
            pass

    def apply_to_offer(self, page: Page, job_id: str) -> tuple[str, str]:
        """
        Flujo completo para una card identificada por job_id.

        BUG FIX: ahora retorna (status, title) en vez de solo status,
        para que engine.py pueda guardar el título en los logs.

        Returns:
            tuple (status, title)
            status: 'applied' | 'skipped_*' | 'error: ...'
            title:  título del job o '' si no se pudo extraer
        """
        title = self._click_job_card(page, job_id)
        if not title:
            return "error: card_not_loaded", ""

        log.info("  [LinkedIn] %s", title)

        if self._is_already_applied(page):
            log.info("  → ya postulado, skip")
            return "skipped_already_applied", title

        if not self._has_easy_apply(page):
            log.info("  → sin Easy Apply, skip")
            return "skipped_no_easy_apply", title

        # Abrir modal
        try:
            human_click(page, SEL["easy_apply_btn"])
            human_delay(2.0, 3.5)
            page.wait_for_selector(SEL["modal"], timeout=8_000)
        except PlaywrightTimeout:
            screenshot = take_error_screenshot(page, "linkedin", f"modal_{job_id}")
            log.warning("  Modal no abrió. Screenshot: %s", screenshot)
            return "error: modal_timeout", title

        # Detectar CAPTCHA antes de empezar
        if page.query_selector(SEL["captcha_check"]):
            self._close_modal_safely(page)
            return "skipped_captcha", title

        # Loop de pasos del modal
        step = 0
        while step < MAX_MODAL_STEPS:
            step += 1
            current_step, total_steps = self._detect_step_count(page)
            log.info("  Paso %d/%d", current_step, total_steps)

            if total_steps > MAX_MODAL_STEPS:
                log.info("  Modal muy largo (%d pasos), skip", total_steps)
                self._close_modal_safely(page)
                return f"skipped_complex_{total_steps}_steps", title

            self._fill_modal_step(page)
            human_delay(1.0, 2.0)

            modal_still_open = self._advance_modal(page)
            if not modal_still_open:
                log.info("  ✓ Aplicación enviada")
                return "applied", title

            if not page.query_selector(SEL["modal"]):
                return "applied", title

        self._close_modal_safely(page)
        return "error: max_steps_exceeded", title

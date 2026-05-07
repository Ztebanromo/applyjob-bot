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
    "job_card":          "li[data-occludable-job-id], li.scaffold-layout__list-item",
    "job_card_link":     "a.job-card-list__title--link, a.job-card-container__link",
    "easy_apply_btn":    ("button.jobs-apply-button--top-card, "
                          "button[aria-label*='Easy Apply'], "
                          "button[aria-label*='Solicitud sencilla'], "
                          "button[aria-label*='Solicitud'], "
                          "button[aria-label*='Apply']"),
    "already_applied":   "span.artdeco-inline-feedback__message",
    "modal":             "div.jobs-easy-apply-modal, div[data-test-modal-id='easy-apply-modal'], div.artdeco-modal--layer-default, div[role='dialog']",
    "modal_title":       "h3.jobs-easy-apply-modal__title, h2.t-bold",
    "step_indicator":    "span.t-14.t-black--light",
    "next_btn":          ("button[aria-label='Continue to next step'], "
                         "button[aria-label='Siguiente paso'], "
                         "button[aria-label='Continue to next step']"),
    "review_btn":        ("button[aria-label='Review your application'], "
                         "button[aria-label='Revisar tu solicitud'], "
                         "button[aria-label='Revisar solicitud']"),
    "submit_btn":        ("button[aria-label='Submit application'], "
                         "button[aria-label='Enviar solicitud'], "
                         "button[aria-label='Enviar']"),
    "close_modal":       ("button[aria-label='Dismiss'], button[aria-label='Cerrar'], "
                          "button[aria-label='Descartar']"),
    "job_title_panel":   "h1.job-details-jobs-unified-top-card__job-title, h1.t-24",
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

    def _dismiss_auth_popup(self, page: Page) -> None:
        """
        Cierra el popup 'Inicia sesión para ver más empleos' si está presente.
        LinkedIn lo muestra como overlay sin redirigir — el bot cree que está
        logueado pero no puede ver las cards debajo.
        """
        POPUP_CLOSE_SELS = [
            "button[aria-label='Descartar']",
            "button[aria-label='Dismiss']",
            "button.modal__dismiss",
            "button.contextual-sign-in-modal__modal-dismiss",
            "button[data-tracking-control-name='guest_homepage-basic_sign-in-modal__core-auth-dismiss-button']",
            # Cierre genérico de modales de autenticación
            "div.base-sign-in-modal button.contextual-sign-in-modal__modal-dismiss",
        ]
        try:
            for sel in POPUP_CLOSE_SELS:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    btn.click()
                    human_delay(0.5, 1.0)
                    log.debug("Popup de login cerrado con selector: %s", sel)
                    return
            # Si ningún botón cerró, intentar con Escape
            if page.query_selector("div.contextual-sign-in-modal, div.base-sign-in-modal"):
                page.keyboard.press("Escape")
                human_delay(0.5, 1.0)
                log.debug("Popup cerrado con Escape")
        except Exception as exc:
            log.debug("_dismiss_auth_popup: %s", exc)

    def get_offer_urls(self, page: Page) -> list[str]:
        """
        LinkedIn carga jobs en panel lateral — no navega a URLs individuales.
        Retorna lista de job_ids (data-job-id) de las cards visibles.

        Antes de leer las cards:
          1. Espera que cargue la lista (hasta 8 seg)
          2. Descarta cualquier popup de autenticación overlay
        """
        # Esperar que las cards estén en el DOM
        try:
            page.wait_for_selector(SEL["job_card"], timeout=8_000)
        except PlaywrightTimeout:
            # Puede que haya un popup bloqueando — intentar cerrarlo
            self._dismiss_auth_popup(page)
            human_delay(1.5, 2.5)
            # Segundo intento
            try:
                page.wait_for_selector(SEL["job_card"], timeout=5_000)
            except PlaywrightTimeout:
                pass  # get_offer_urls retornará lista vacía

        # Descartar popup si está presente (aunque hayamos encontrado cards)
        self._dismiss_auth_popup(page)

        cards = page.query_selector_all(SEL["job_card"])
        job_ids = []
        for card in cards:
            try:
                job_id = (card.get_attribute("data-job-id")
                          or card.get_attribute("data-occludable-job-id"))
                if job_id and job_id not in job_ids:
                    job_ids.append(job_id)
            except Exception as exc:
                log.debug("Error leyendo job_id de card: %s", exc)
                continue
        return job_ids

    def get_job_url(self, page: Page, job_id: str) -> str:
        """URL canónica de LinkedIn para deduplicación en SQLite."""
        return f"https://www.linkedin.com/jobs/view/{job_id}/"

    def _click_job_card(self, page: Page, job_id: str) -> str:
        """
        Hace click en la card y retorna el título del panel derecho.
        Retorna '' si la card no se encuentra o no carga.

        Estrategia multinivel (LinkedIn virtualiza el DOM):
          1. Intenta por li[data-occludable-job-id] con scroll
          2. Si no lo encuentra, intenta por el link href /jobs/view/{job_id}
          3. Espera cualquier botón de aplicar (Easy Apply o Apply normal)
        """
        # Selector 1: li por atributo de job_id
        card_sel = (f"li[data-job-id='{job_id}'], "
                    f"li[data-occludable-job-id='{job_id}']")
        # Selector 2: link href (más estable, no se virtualiza)
        link_sel = f"a[href*='/jobs/view/{job_id}']"
        # Selector 3: cualquier botón de aplicar en el panel derecho
        apply_any = (
            f"{SEL['easy_apply_btn']}, "
            "button.jobs-apply-button--top-card, "
            "button[aria-label*='Apply'], "
            "button[aria-label*='Aplicar'], "
            "button[aria-label*='Solicitud']"
        )

        def _wait_panel_and_title() -> str:
            """Espera que el panel derecho cargue y retorna el título."""
            try:
                page.wait_for_selector(apply_any, timeout=10_000)
            except PlaywrightTimeout:
                pass  # Puede no tener Easy Apply — lo detecta _has_easy_apply
            try:
                title = page.text_content(SEL["job_title_panel"], timeout=4_000) or ""
                return title.strip()[:80] or f"job_{job_id}"
            except Exception:
                return f"job_{job_id}"

        # ── Intento 1: li por atributo ──
        try:
            card = page.wait_for_selector(card_sel, timeout=5_000)
            card.scroll_into_view_if_needed()
            micro_delay()
            card.click()
            human_delay(1.5, 2.5)
            return _wait_panel_and_title()
        except PlaywrightTimeout:
            pass  # card virtualizada — intentar por link

        # ── Intento 2: link href ──
        try:
            link = page.wait_for_selector(link_sel, timeout=4_000)
            link.scroll_into_view_if_needed()
            micro_delay()
            link.click()
            human_delay(1.5, 2.5)
            return _wait_panel_and_title()
        except PlaywrightTimeout:
            return ""

    def _is_already_applied(self, page: Page) -> bool:
        """
        Detecta si el job ya fue solicitado.
        LinkedIn puede mostrar esto de varias formas:
          - Span con "applied" / "postulaste"
          - Texto "Solicitado" en el panel derecho (estado post-postulación)
          - Botón "Ver solicitud" (visible cuando ya se aplicó)
        """
        try:
            feedback = page.query_selector(SEL["already_applied"])
            if feedback:
                text = (feedback.text_content() or "").lower()
                if "applied" in text or "postulaste" in text:
                    return True
        except Exception as exc:
            log.debug("_is_already_applied feedback error: %s", exc)

        # Chequeo adicional: buscar "Solicitado" o "Ver solicitud" en el panel derecho
        try:
            page_text = page.evaluate("""
                () => {
                    // Buscar en el panel derecho (mitad derecha del viewport)
                    const vw = window.innerWidth;
                    const midX = vw / 2;
                    const allEls = Array.from(document.querySelectorAll(
                        'span, p, div.jobs-details-top-card__apply-info, ' +
                        'div.jobs-unified-top-card__apply-container, ' +
                        'div.jobs-details-top-card__apply-info, ' +
                        'button[aria-label*="solicitud"], a[href*="detail/applied"]'
                    ));
                    for (const el of allEls) {
                        const rect = el.getBoundingClientRect();
                        if (rect.left < midX - 50) continue;  // solo panel derecho
                        const txt = (el.textContent || '').toLowerCase();
                        if (txt.includes('solicitado') || txt.includes('ver solicitud') ||
                            txt.includes('already applied') || txt.includes('application submitted')) {
                            return txt.substring(0, 60);
                        }
                    }
                    return '';
                }
            """)
            if page_text:
                log.debug("  _is_already_applied: found '%s'", page_text[:50])
                return True
        except Exception as exc:
            log.debug("_is_already_applied JS error: %s", exc)

        return False

    def _has_easy_apply(self, page: Page) -> bool:
        """
        Espera hasta 6s al botón 'Solicitud sencilla' / 'Easy Apply'.
        LinkedIn renderiza el botón vía React después de DOMContentLoaded,
        por eso no basta con query_selector inmediato.
        """
        try:
            page.wait_for_selector(SEL["easy_apply_btn"], timeout=6_000)
            btn = page.query_selector(SEL["easy_apply_btn"])
            return btn is not None and btn.is_visible()
        except PlaywrightTimeout:
            return False
        except Exception as exc:
            log.debug("_has_easy_apply error: %s", exc)
            return False

    def _detect_step_count(self, page: Page) -> tuple[int, int]:
        """Lee 'Step X of Y' del modal. Retorna (current, total)."""
        try:
            for el in page.query_selector_all(SEL["step_indicator"]):
                text = el.text_content() or ""
                m = re.search(r"(\d+)\s+(?:of|de)\s+(\d+)", text, re.IGNORECASE)
                if m:
                    return int(m.group(1)), int(m.group(2))
        except Exception as exc:
            log.debug("_detect_step_count error: %s", exc)
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

        except Exception as exc:
            log.debug("_handle_dropdowns error: %s", exc)

    def _advance_modal(self, page: Page) -> bool:
        """
        Avanza al siguiente paso o envía la aplicación.
        Retorna True si el modal sigue abierto, False si se cerró (submit exitoso).

        Usa JS para buscar dentro del modal/dialog cualquier botón actionable,
        priorizando por texto: Enviar > Revisar > Siguiente > primario genérico.
        """
        result = page.evaluate("""
            () => {
                // Localizar el modal
                const modal = document.querySelector(
                    'div.jobs-easy-apply-modal, div[role="dialog"], ' +
                    'div.artdeco-modal--layer-default'
                );
                if (!modal) return {found: false, text: 'no_modal'};

                const isVisible = b => !b.disabled && b.offsetParent !== null && b.getBoundingClientRect().width > 0;
                const getText   = b => (b.textContent || '').trim().toLowerCase() + '|' + (b.getAttribute('aria-label') || '').toLowerCase();

                // ── Prioridad 1: botón primario en el FOOTER del modal ──
                // Este es el botón de acción principal (Enviar / Siguiente / Revisar).
                // Es más confiable que buscar por texto porque siempre está en el footer.
                const footer = modal.querySelector(
                    'footer, div.jobs-easy-apply-modal__footer, div[class*="footer"]'
                );
                if (footer) {
                    const primary = Array.from(footer.querySelectorAll('button'))
                        .find(b => b.classList.contains('artdeco-button--primary') && isVisible(b));
                    if (primary) {
                        const txt = primary.textContent.trim();
                        const lbl = (primary.getAttribute('aria-label') || '').toLowerCase();
                        const combined = txt.toLowerCase() + '|' + lbl;
                        const isSubmit = combined.includes('enviar') || combined.includes('submit') || combined.includes('send');
                        primary.click();
                        return {found: true, text: txt, isSubmit};
                    }
                }

                // ── Prioridad 2: cualquier botón visible con texto enviar/submit ──
                const btns = Array.from(modal.querySelectorAll('button')).filter(isVisible);

                let target = btns.find(b => {
                    const t = getText(b);
                    return t.includes('enviar') || t.includes('submit') || t.includes('send');
                });
                if (target) {
                    target.click();
                    return {found: true, text: target.textContent.trim(), isSubmit: true};
                }

                // ── Prioridad 3: revisar / review ──
                target = btns.find(b => {
                    const t = getText(b);
                    return t.includes('revisar') || t.includes('review');
                });
                if (target) {
                    target.click();
                    return {found: true, text: target.textContent.trim(), isSubmit: false};
                }

                // ── Prioridad 4: siguiente / next / continue ──
                target = btns.find(b => {
                    const t = getText(b);
                    return t.includes('siguiente') || t.includes('next') ||
                           t.includes('continuar') || t.includes('continue');
                });
                if (target) {
                    target.click();
                    return {found: true, text: target.textContent.trim(), isSubmit: false};
                }

                // ── Prioridad 5: cualquier artdeco-button--primary fuera del footer ──
                target = btns.find(b => b.classList.contains('artdeco-button--primary'));
                if (target) {
                    const txt = getText(target);
                    const isSubmit = txt.includes('enviar') || txt.includes('submit');
                    target.click();
                    return {found: true, text: target.textContent.trim(), isSubmit};
                }

                // Debug: listar todos los botones visibles
                const debug = btns.map(b =>
                    '"' + (b.textContent || '').trim().substring(0, 25) + '"'
                ).join(', ');
                return {found: false, text: debug || 'sin botones'};
            }
        """)

        if result and result.get("found"):
            btn_text = (result.get("text") or "").strip()
            is_submit = result.get("isSubmit", False)
            log.info("  _advance_modal: '%s' (submit=%s)", btn_text[:40], is_submit)
            human_delay(2.0, 3.5)
            return not is_submit   # False = modal cerrado (enviado), True = sigue abierto

        log.warning("  _advance_modal: sin botón. Botones presentes: %s",
                    (result or {}).get("text", "n/a")[:120])
        return True  # Modal sigue abierto, no se pudo avanzar

    def _dismiss_post_apply_dialog(self, page: Page) -> None:
        """
        Cierra el popup de confirmación 'Solicitud enviada' que LinkedIn muestra
        después de enviar una Easy Apply. Busca el botón 'Hecho'/'Done'/'Dismiss'.
        """
        POST_APPLY_SELS = [
            "button[aria-label='Hecho']",
            "button[aria-label='Done']",
            "button[aria-label='Dismiss']",
            "button[aria-label='Cerrar']",
        ]
        try:
            for sel in POST_APPLY_SELS:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    btn.click()
                    micro_delay()
                    log.debug("_dismiss_post_apply_dialog: cerrado con %s", sel)
                    return
            # Fallback: buscar por texto "Hecho" o "Done" en cualquier botón visible
            page.evaluate("""
                () => {
                    const btns = Array.from(document.querySelectorAll('button'));
                    for (const b of btns) {
                        const txt = (b.textContent || '').trim().toLowerCase();
                        if ((txt === 'hecho' || txt === 'done') && b.offsetParent !== null) {
                            b.click(); return true;
                        }
                    }
                    return false;
                }
            """)
            micro_delay()
        except Exception as exc:
            log.debug("_dismiss_post_apply_dialog: %s", exc)

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
        except Exception as exc:
            log.debug("_close_modal_safely error: %s", exc)

    def _click_easy_apply(self, page: Page) -> bool:
        """
        Hace click en el botón "Solicitud sencilla" del PANEL DERECHO.

        Estrategia: buscar el botón por posición (mitad derecha del viewport)
        y por texto/aria-label. Esto garantiza que clickeamos el botón del
        panel de detalle y no algún elemento del panel izquierdo.

        Returns True si el modal abrió, False si no.
        """
        def _modal_visible() -> bool:
            try:
                page.wait_for_selector(SEL["modal"], timeout=4_000)
                return True
            except PlaywrightTimeout:
                return False

        # ── Intento 1: JS click en el botón del panel derecho (mitad derecha) ──
        # Busca el botón "Solicitud sencilla" posicionado en la mitad derecha
        # del viewport para evitar clicar elementos del panel izquierdo.
        try:
            clicked = page.evaluate("""
                () => {
                    const vw = window.innerWidth;
                    const midX = vw / 2;
                    const btns = Array.from(document.querySelectorAll('button'));
                    const target = btns.find(b => {
                        const rect = b.getBoundingClientRect();
                        const txt  = (b.textContent || '').trim().toLowerCase();
                        const lbl  = (b.getAttribute('aria-label') || '').toLowerCase();
                        const isEA = txt.includes('solicitud sencilla') ||
                                     txt.includes('easy apply') ||
                                     lbl.includes('solicitud sencilla') ||
                                     lbl.includes('easy apply');
                        const inRightPanel = rect.left > midX - 100;  // en panel derecho
                        return isEA && inRightPanel && !b.disabled &&
                               rect.width > 0 && rect.height > 0;
                    });
                    if (target) {
                        target.scrollIntoView({behavior:'instant', block:'center'});
                        target.click();
                        return true;
                    }
                    return false;
                }
            """)
            if clicked:
                human_delay(2.0, 3.0)
                if _modal_visible():
                    return True
        except Exception:
            pass

        # ── Intento 2: Playwright locator con texto visible ──
        try:
            loc = page.get_by_role("button", name="Solicitud sencilla").first
            loc.scroll_into_view_if_needed()
            micro_delay()
            loc.click()
            human_delay(2.0, 3.0)
            if _modal_visible():
                return True
        except Exception:
            pass

        # ── Intento 3: focus + Enter por teclado (bypasa pointer-events) ──
        try:
            btn = page.query_selector(SEL["easy_apply_btn"])
            if btn:
                page.evaluate("(b) => b.focus()", btn)
                micro_delay()
                page.keyboard.press("Return")
                human_delay(2.0, 3.0)
                if _modal_visible():
                    return True
        except Exception:
            pass

        return False

    def apply_to_offer(self, page: Page, job_id: str) -> tuple[str, str]:
        """
        Flujo completo para un job_id de LinkedIn.

        Estrategia: navegar a la URL de búsqueda con parámetro currentJobId.
        Esto hace que LinkedIn cargue el panel de detalle sin salir de la
        búsqueda — la misma URL base que ya funciona, solo cambia el job
        seleccionado. Evita el bloqueo de navegación directa a /jobs/view/{id}/.

        Returns:
            tuple (status, title)
            status: 'applied' | 'skipped_*' | 'error: ...'
            title:  título del job o '' si no se pudo extraer
        """
        # ── Sin navegación: permanecemos en la search page todo el tiempo ──
        # Si por alguna razón salimos de la búsqueda, volvemos.
        if "linkedin.com/jobs/search" not in page.url:
            try:
                page.goto(self.config["url_busqueda"],
                          wait_until="domcontentloaded", timeout=20_000)
                human_delay(2.0, 3.0)
            except PlaywrightTimeout:
                return "error: card_not_loaded", ""

        # Detectar redirect a login / authwall
        cur = page.url
        if any(x in cur for x in ("/login", "/checkpoint", "/authwall", "/uas/", "/signup")):
            log.warning("  Redirigido a auth: %s", cur)
            return "error: login_required", ""

        # Click en la card del panel izquierdo para cargar el job en el panel derecho.
        # Intentamos en orden: selector de li, link href, JS scroll+click.
        card_sel = (f"li[data-occludable-job-id='{job_id}'], "
                    f"li[data-job-id='{job_id}']")
        link_sel = f"a[href*='/jobs/view/{job_id}']"
        card_clicked = False

        for sel in [card_sel, link_sel]:
            try:
                el = page.wait_for_selector(sel, timeout=4_000)
                if el:
                    el.scroll_into_view_if_needed()
                    micro_delay()
                    el.click()
                    human_delay(1.5, 2.5)
                    card_clicked = True
                    break
            except PlaywrightTimeout:
                continue

        if not card_clicked:
            # Intento JS: scroll en la lista y click
            clicked = page.evaluate(f"""
                () => {{
                    const el = document.querySelector(
                        "li[data-occludable-job-id='{job_id}'], " +
                        "li[data-job-id='{job_id}'], " +
                        "a[href*='/jobs/view/{job_id}']"
                    );
                    if (el) {{
                        el.scrollIntoView({{behavior: 'instant', block: 'center'}});
                        el.click();
                        return true;
                    }}
                    return false;
                }}
            """)
            if clicked:
                human_delay(1.5, 2.5)
                card_clicked = True

        if not card_clicked:
            # ── Fallback final: navegar via currentJobId URL param ──
            # LinkedIn virtualiza cards fuera del viewport — si ningún selector
            # encuentra el elemento, forzamos la carga del job via URL param.
            try:
                search_base = self.config["url_busqueda"]
                job_url = f"{search_base}&currentJobId={job_id}"
                page.goto(job_url, wait_until="domcontentloaded", timeout=20_000)
                human_delay(2.0, 3.0)
                # Verificar que cargó el job correcto
                page.wait_for_function(
                    f"() => window.location.href.includes('{job_id}')",
                    timeout=6_000,
                )
                card_clicked = True
                log.debug("  card fallback: currentJobId URL para %s", job_id)
            except Exception as exc:
                log.debug("  card fallback URL falló: %s", exc)
                return "error: card_not_loaded", ""

        # Esperar que el URL se actualice con currentJobId=job_id
        # Confirma que LinkedIn reconoció el click y actualizó el panel derecho.
        try:
            page.wait_for_function(
                f"() => window.location.href.includes('{job_id}')",
                timeout=6_000,
            )
        except Exception:
            # URL no cambió → el click no registró correctamente.
            # Intento forzado: click en el link href directamente
            try:
                link = page.query_selector(f"a[href*='/jobs/view/{job_id}']")
                if link:
                    link.click(force=True)
                    human_delay(1.5, 2.5)
                    page.wait_for_function(
                        f"() => window.location.href.includes('{job_id}')",
                        timeout=5_000,
                    )
            except Exception:
                log.debug("  Panel no confirmó job %s (URL no actualizó)", job_id)
                return "error: card_not_loaded", ""

        # Extraer título del panel derecho — ahora sí muestra nuestro job
        title = ""
        try:
            page.wait_for_selector(SEL["job_title_panel"], timeout=6_000)
            title = (page.text_content(SEL["job_title_panel"]) or "").strip()[:80]
        except Exception:
            pass

        if not title:
            title = f"job_{job_id}"

        log.info("  [LinkedIn] %s", title)

        if self._is_already_applied(page):
            log.info("  → ya postulado, skip")
            return "skipped_already_applied", title

        if not self._has_easy_apply(page):
            log.info("  → sin Easy Apply, skip")
            return "skipped_no_easy_apply", title

        # Abrir modal con estrategia multinivel (evita scaffold-layout)
        modal_opened = self._click_easy_apply(page)

        if not modal_opened:
            # Esperar un poco más — puede que el modal tarde
            try:
                page.wait_for_selector(SEL["modal"], timeout=5_000)
                modal_opened = True
            except PlaywrightTimeout:
                pass

        if not modal_opened:
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
                human_delay(1.0, 2.0)
                self._dismiss_post_apply_dialog(page)
                return "applied", title

            if not page.query_selector(SEL["modal"]):
                human_delay(1.0, 2.0)
                self._dismiss_post_apply_dialog(page)
                return "applied", title

        self._close_modal_safely(page)
        return "error: max_steps_exceeded", title

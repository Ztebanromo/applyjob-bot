"""
Portal específico para Indeed Chile.

Flujo real de cl.indeed.com:
  1. Página de búsqueda → lista de tarjetas de trabajo
  2. Click en tarjeta → panel derecho muestra detalles (no navega a nueva página)
  3. Si es "Solicitud sencilla de Indeed" → botón indeedApplyButton → overlay de aplicación
  4. Si es "Aplicar en el sitio" → link externo → se registra como external_skipped
  5. Paginación: a[data-testid='pagination-page-next']

Observaciones:
  - Navegar directamente a /viewjob?jk=... puede activar Cloudflare
  - El flujo correcto es SIEMPRE desde la página de búsqueda (tiene cookies/sesión)
  - data-jk identifica de forma única cada oferta
"""
import logging
import time

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from .base import BasePortal
from ..stealth_utils import human_delay, micro_delay, take_error_screenshot
from ..form_filler import fill_form

log = logging.getLogger("applyjob.indeed")

# ── Selectores ──────────────────────────────────────────────────────────────
SEL = {
    # Cards de oferta en la lista de búsqueda
    "card":              "div.job_seen_beacon",
    "card_title_link":   "h2.jobTitle a[data-jk], a.jcs-JobTitle[data-jk]",

    # Panel de detalle (lado derecho) — prueba varios contenedores
    "detail_panel":      (
        "#viewJobSSRRoot, "
        ".jobsearch-ViewJobLayout--embedded, "
        "#mosaic-afterAd, "
        ".jobsearch-RightPane, "
        "#jobDescriptionText, "
        ".jobDetailSectionWrapper"
    ),

    # Botón "Solicitud sencilla de Indeed" (Easy Apply)
    "easy_apply_btn":    (
        "button#indeedApplyButton, "
        "span.indeed-apply-button button, "
        ".ia-IndeedApplyButton, "
        "button[class*='indeedApply'], "
        "button[data-testid='indeedApplyButton']"
    ),

    # Botón / link de "Aplicar en el sitio del empleador" (externo)
    "external_apply":    (
        "a.sl_apply_button, "
        "a[data-testid='external-apply-link'], "
        "a[href*='clk?jk='][target='_blank']"
    ),

    # Overlay / modal de Easy Apply
    "apply_overlay":     "div.ia-ApplyFormTop, div[class*='ia-'], #ia-container",

    # Inputs dentro del overlay de Easy Apply
    "overlay_next":      (
        "button[type='submit'], "
        "button:has-text('Continue'), button:has-text('Next'), "
        "button:has-text('Submit'), button:has-text('Aplicar')"
    ),

    # Confirmación de aplicación enviada
    "apply_success":     (
        "div.ia-PostApply, "
        "div[class*='PostApply'], "
        "h1:has-text('application was sent'), "
        "div:has-text('Tu solicitud fue enviada'), "
        "div:has-text('application submitted')"
    ),

    # Título de la oferta en el panel de detalle
    "job_title":         "h2.jobsearch-JobInfoHeader-title, h1.jobsearch-JobInfoHeader-title",
}

PANEL_WAIT_MS = 8_000   # ms a esperar por el panel de detalle
OVERLAY_WAIT_MS = 5_000  # ms a esperar por el overlay de Easy Apply


class IndeedPortal(BasePortal):
    """
    Maneja Indeed Chile usando el flujo de panel lateral.
    No navega a /viewjob directamente para evitar Cloudflare.
    """

    def _wait_cloudflare(self, page: Page, timeout_s: int = 120) -> bool:
        """
        Detecta el desafío de Cloudflare y espera hasta que el usuario lo resuelva.
        Retorna True si se resolvió, False si expiró el timeout.
        """
        import time as _time

        def _is_cloudflare() -> bool:
            try:
                txt = page.evaluate("document.body.innerText") or ""
                return "Verificación adicional requerida" in txt or \
                       "Verifique que es un ser humano" in txt or \
                       "cf_chl" in page.url or \
                       "cloudflare" in txt.lower()
            except Exception:
                return False

        def _has_jobs() -> bool:
            try:
                return len(page.query_selector_all(SEL["card"])) > 0
            except Exception:
                return False

        if not _is_cloudflare():
            return True  # sin desafío

        log.warning("=" * 60)
        log.warning("CLOUDFLARE CHALLENGE detectado en Indeed")
        log.warning("Por favor, marca la casilla 'Verifique que es un ser humano'")
        log.warning("en el navegador abierto. El bot esperará hasta %d segundos...", timeout_s)
        log.warning("=" * 60)

        deadline = _time.time() + timeout_s
        while _time.time() < deadline:
            _time.sleep(3)
            if not _is_cloudflare():
                log.info("✓ Cloudflare resuelto — continuando...")
                human_delay(2.0, 3.0)
                return True
            if _has_jobs():
                log.info("✓ Jobs visibles — Cloudflare superado")
                return True

        log.error("Timeout esperando Cloudflare. Abortando Indeed.")
        return False

    def get_offer_urls(self, page: Page) -> list[str]:
        """
        Extrae job keys (data-jk) de todas las tarjetas en la página actual.
        Retorna una lista de job keys únicos (no URLs completas).
        """
        # Verificar y esperar si hay desafío de Cloudflare
        if not self._wait_cloudflare(page):
            return []

        seen = set()
        keys = []
        try:
            cards = page.query_selector_all(SEL["card"])
            log.debug("IndeedPortal: %d cards encontradas", len(cards))
            for card in cards:
                try:
                    link = card.query_selector(SEL["card_title_link"])
                    if not link:
                        # Fallback: buscar cualquier a[data-jk] dentro del card
                        link = card.query_selector("a[data-jk]")
                    if not link:
                        continue
                    jk = link.get_attribute("data-jk") or ""
                    if jk and jk not in seen:
                        seen.add(jk)
                        keys.append(jk)
                except Exception as exc:
                    log.debug("Error extrayendo jk de card: %s", exc)
                    continue
        except Exception as exc:
            log.warning("get_offer_urls error: %s", exc)
        return keys

    def get_job_url(self, page: Page, offer_id: str) -> str:
        """Retorna una URL canónica para deduplicación en la DB."""
        return f"https://cl.indeed.com/viewjob?jk={offer_id}"

    def apply_to_offer(self, page: Page, offer_id: str) -> tuple[str, str]:
        """
        1. Hace click en la tarjeta del job para abrir el panel de detalle.
        2. Detecta si es Easy Apply o externo.
        3. Para Easy Apply: rellena el overlay y envía.
        4. Para externo: registra como external_skipped.

        Returns:
            (status, title)
        """
        title = "unknown"

        # ── 1. Encontrar la tarjeta y hacer click ──────────────────────────
        card = self._find_card(page, offer_id)
        if not card:
            log.warning("  [indeed] No se encontró card para jk=%s — intentando scroll", offer_id)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            human_delay(1.5, 2.5)
            card = self._find_card(page, offer_id)
            if not card:
                take_error_screenshot(page, "indeed", f"no_card_{offer_id}")
                return "error: card_not_found", title

        try:
            # Hacer scroll al card y clickearlo
            card.scroll_into_view_if_needed()
            micro_delay()
            card.click()
            human_delay(2.0, 3.5)
        except Exception as exc:
            log.warning("  [indeed] Click en card falló: %s", exc)
            return f"error: click_card {exc}", title

        # ── 2. Esperar panel de detalle ────────────────────────────────────
        panel_loaded = self._wait_for_panel(page)
        if not panel_loaded:
            log.warning("  [indeed] Panel de detalle no cargó para jk=%s", offer_id)
            take_error_screenshot(page, "indeed", f"no_panel_{offer_id}")
            return "error: panel_timeout", title

        # Extraer título desde el panel
        try:
            title_el = page.query_selector(SEL["job_title"])
            if title_el:
                title = (title_el.text_content() or "").strip()[:80]
        except Exception:
            pass

        log.debug("  [indeed] Panel cargado | título: %s", title)

        # ── 3. Detectar tipo de aplicación ────────────────────────────────
        easy_btn = self._find_visible(page, SEL["easy_apply_btn"])

        if easy_btn:
            return self._apply_easy(page, easy_btn, title)
        else:
            # Verificar si hay botón externo
            ext_btn = self._find_visible(page, SEL["external_apply"])
            if ext_btn:
                ext_url = ext_btn.get_attribute("href") or "unknown"
                log.info("  [indeed] Postulación externa: %s", ext_url[:60])
                return "external_skipped", title
            else:
                # No hay botón de ningún tipo visible
                take_error_screenshot(page, "indeed", f"no_apply_btn_{offer_id}")
                return "error: no_apply_button", title

    # ────────────────────────────────────────────────────────────────────────
    # Helpers privados
    # ────────────────────────────────────────────────────────────────────────

    def _find_card(self, page: Page, jk: str):
        """Busca el div.job_seen_beacon que contiene el link con data-jk=jk."""
        try:
            link = page.query_selector(f"a[data-jk='{jk}']")
            if not link:
                return None
            # Subir hasta div.job_seen_beacon
            card = link.evaluate_handle("""
                el => {
                    let cur = el;
                    for (let i = 0; i < 8; i++) {
                        if (!cur) return null;
                        if (cur.classList && cur.classList.contains('job_seen_beacon')) return cur;
                        cur = cur.parentElement;
                    }
                    return el;  // fallback: retornar el link mismo
                }
            """)
            return card.as_element()
        except Exception as exc:
            log.debug("_find_card error: %s", exc)
            return None

    def _wait_for_panel(self, page: Page) -> bool:
        """Espera a que el panel de detalle esté visible."""
        try:
            page.wait_for_selector(SEL["detail_panel"], timeout=PANEL_WAIT_MS)
            return True
        except PlaywrightTimeout:
            return False

    def _find_visible(self, page: Page, selector: str):
        """Retorna el primer elemento visible que coincide con selector, o None."""
        try:
            for sel_part in selector.split(","):
                sel_part = sel_part.strip()
                try:
                    els = page.query_selector_all(sel_part)
                    for el in els:
                        if el.is_visible():
                            return el
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def _apply_easy(self, page: Page, apply_btn, title: str) -> tuple[str, str]:
        """
        Hace click en el botón de Easy Apply de Indeed y rellena el overlay.
        """
        try:
            apply_btn.click()
            human_delay(2.0, 3.5)
        except Exception as exc:
            log.warning("  [indeed] Click en Easy Apply falló: %s", exc)
            return f"error: easy_apply_click {exc}", title

        # Verificar que el overlay se abrió
        overlay = self._wait_for_overlay(page)
        if not overlay:
            log.warning("  [indeed] Overlay de Easy Apply no apareció")
            return "error: overlay_timeout", title

        # Rellenar y avanzar pasos (máximo 5)
        for step in range(5):
            human_delay(1.0, 2.0)
            fill_form(page, self.profile)

            # Buscar botón de avance / submit
            advanced = False
            for sel in SEL["overlay_next"].split(","):
                sel = sel.strip()
                try:
                    btn = page.query_selector(sel)
                    if btn and btn.is_visible() and btn.is_enabled():
                        btn.click()
                        human_delay(1.5, 2.5)
                        advanced = True
                        break
                except Exception:
                    continue

            if not advanced:
                log.debug("  [indeed] No se encontró botón de avance en step %d", step)
                break

            # Verificar si ya mostró confirmación
            if self._check_success(page):
                log.info("  [indeed] ✓ Aplicación enviada (step %d)", step)
                return "applied", title

        # Verificación final de éxito
        if self._check_success(page):
            return "applied", title

        take_error_screenshot(page, "indeed", "apply_incomplete")
        return "filled_no_submit", title

    def _wait_for_overlay(self, page: Page) -> bool:
        try:
            page.wait_for_selector(SEL["apply_overlay"], timeout=OVERLAY_WAIT_MS)
            return True
        except PlaywrightTimeout:
            # Intentar con selector genérico
            try:
                page.wait_for_selector("div[class*='ia-']", timeout=3_000)
                return True
            except PlaywrightTimeout:
                return False

    def _check_success(self, page: Page) -> bool:
        try:
            for sel_part in SEL["apply_success"].split(","):
                sel_part = sel_part.strip()
                try:
                    el = page.query_selector(sel_part)
                    if el and el.is_visible():
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

"""
Portal: InfoJobs (www.infojobs.net)
Tipo: redirect — abre formulario del empleador o modal InfoJobs.
requires_login: True (requiere cuenta InfoJobs para inscribirse).
"""
import logging

from playwright.sync_api import Page
from bot.portals.base import BasePortal
from bot.stealth_utils import human_delay
from ..config import practica_ok
from ..form_filler import scan_form

log = logging.getLogger("applyjob.infojobs")

_APPLY_SELS = [
    "a.btn-apply",
    "button.btn-apply",
    "a[data-testid='btn-apply']",
    "button[data-testid='btn-apply']",
    "a[data-testid='apply-button']",
    "button[data-testid='apply-button']",
    "button:has-text('Inscribirme')",
    "a:has-text('Inscribirme')",
    "button:has-text('Inscríbete')",
    "a:has-text('Inscríbete')",
    "button:has-text('Postularme')",
    "a:has-text('Postularme')",
    "button:has-text('Postúlate')",
    "a:has-text('Postúlate')",
    "button:has-text('Aplicar')",
    "a:has-text('Aplicar')",
    "[class*='applyButton']",
    "[class*='apply-btn']",
    "[class*='btn-inscribirse']",
]

_OFFER_LINK_SELS = [
    "a[href*='/empleos/oferta/']",
    "a[href*='/oferta-trabajo/']",
    "a[href*='/trabajo/']",
    "li.ij-OfferCardContent a",
    "article.ij-OfferCard a",
    "div[data-testid='offer-list-item'] a",
    "li[data-testid='offer-list-item'] a",
    "div[class*='OfferCard'] a",
    "div[class*='offer-card'] a",
]


class InfoJobsCLPortal(BasePortal):

    def get_offer_urls(self, page: Page) -> list:
        """Extrae URLs de ofertas de la página de resultados de InfoJobs Chile."""
        urls = []
        # Intentar wait con cada selector hasta que alguno aparezca
        found = False
        for sel in _OFFER_LINK_SELS:
            try:
                page.wait_for_selector(sel, timeout=5_000)
                found = True
                break
            except Exception:
                pass

        if not found:
            # Último recurso: esperar networkidle
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass

        for sel in _OFFER_LINK_SELS:
            try:
                els = page.query_selector_all(sel)
                for el in els:
                    href = el.get_attribute("href") or ""
                    if not href:
                        continue
                    if not href.startswith("http"):
                        base = "/".join(page.url.split("/")[:3])
                        href = base + href
                    if href not in urls and "infojobs" in href:
                        urls.append(href)
            except Exception:
                pass
            if len(urls) >= 30:
                break

        return urls[:30]

    def apply_to_offer(self, page: Page, offer_url: str) -> tuple[str, str]:
        try:
            page.goto(offer_url, timeout=25_000, wait_until="domcontentloaded")
            human_delay(1.0, 2.0)

            # Buscar botón de inscripción
            btn = None
            for sel in _APPLY_SELS:
                try:
                    el = page.query_selector(sel)
                    if el and el.is_visible():
                        btn = el
                        break
                except Exception:
                    pass

            # Fallback por texto dentro de botones o enlaces visibles
            if not btn:
                try:
                    for el in page.query_selector_all("button,a"):
                        try:
                            if not el.is_visible():
                                continue
                            text = (el.text_content() or "").strip()
                            if not text:
                                continue
                            if any(text.startswith(t) for t in [
                                'Inscribirme', 'Inscríbete', 'Postularme',
                                'Postúlate', 'Aplicar', 'Postularme', 'Publicar'
                            ]):
                                btn = el
                                break
                        except Exception:
                            continue
                except Exception:
                    pass

            title = ""
            try:
                el = page.query_selector("h1, .offer-title, .header-title")
                if el:
                    title = (el.text_content() or "").strip()[:100]
            except Exception:
                pass

            page_body = page.text_content("body") or ""
            if not practica_ok(title + " " + page_body):
                log.info("  [infojobs] Descartada (práctica/pasantía): '%s'", title[:80])
                return "skipped_practica", title

            if not btn:
                return "skipped_no_apply_button", title

            if self.dry_run:
                return "dry_run", title

            btn.click()
            human_delay(1.5, 3.0)
            try:
                page.wait_for_timeout(2_500)
            except Exception:
                pass

            # Si redirigió fuera de InfoJobs
            if "infojobs" not in page.url:
                # Escanear el ATS externo para guardar preguntas en cache/pending
                try:
                    ext_result = scan_form(page, self.profile, job_title=title,
                                           portal="infojobs", url=offer_url)
                    if ext_result.get("unanswered"):
                        log.info("  [infojobs] Preguntas externas guardadas: %d", len(ext_result["unanswered"]))
                except Exception:
                    pass
                return "external_apply", title

            # Mensaje de inscripción exitosa
            for success_sel in [
                "div:has-text('inscripción')",
                "p:has-text('postulación enviada')",
                "h2:has-text('inscrito')",
                "[class*='success']:has-text('inscrip')",
                "[data-testid='apply-success']",
                "div:has-text('Inscrito')",
                "div:has-text('Te has inscrito')",
                "p:has-text('Inscripción realizada')",
                "p:has-text('Tu postulación se ha enviado')",
            ]:
                try:
                    el = page.query_selector(success_sel)
                    if el and el.is_visible():
                        return "applied", title
                except Exception:
                    pass

            # Fallback de texto visible en la página
            try:
                page_text = (page.text_content('body') or '').lower()
                if any(sub in page_text for sub in [
                    'inscripción enviada', 'postulación enviada',
                    'te has inscrito', 'ya estás inscrito', 'ya te postulaste',
                    'inscripción realizada', 'postulado correctamente',
                    'oferta guardada']):
                    return "applied", title
                if btn and hasattr(btn, 'is_enabled'):
                    try:
                        if not btn.is_enabled():
                            # El botón quedó deshabilitado tras enviar la aplicación.
                            return "applied", title
                    except Exception:
                        pass
                if btn and hasattr(btn, 'text_content'):
                    text = (btn.text_content() or '').strip().lower()
                    if any(t in text for t in ['inscrito', 'postulado', 'solicitado']):
                        return "applied", title
            except Exception:
                pass

            return "external_apply", title

        except Exception as e:
            return f"error: {str(e)[:80]}", title

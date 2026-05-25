"""
Portal: InfoJobs Chile (cl.infojobs.net)
Tipo: redirect — abre formulario del empleador o modal InfoJobs.
requires_login: True (requiere cuenta InfoJobs para inscribirse).

Si no hay sesión, el bot emitirá login_required y esperará.
"""
from playwright.sync_api import Page
from bot.portals.base import BasePortal
from bot.stealth_utils import human_delay


class InfoJobsCLPortal(BasePortal):

    def apply_to_offer(self, page: Page, offer_url: str) -> str:
        try:
            page.goto(offer_url, timeout=25_000, wait_until="domcontentloaded")
            human_delay(0.8, 2.0)

            # Botón principal de inscripción (varios selectores posibles)
            btn = (
                page.query_selector("a.btn-apply") or
                page.query_selector("button:has-text('Inscribirme')") or
                page.query_selector("a:has-text('Inscribirme')") or
                page.query_selector("button:has-text('Postularme')") or
                page.query_selector("a:has-text('Postularme')")
            )
            if not btn:
                return "skipped_no_apply_button"

            if self.dry_run:
                return "dry_run"

            btn.click()
            human_delay(1.0, 2.5)

            # Si redirigió a sitio externo del empleador
            if "infojobs" not in page.url:
                return "external:infojobs"

            # Detectar mensaje de inscripción exitosa
            success = (
                page.query_selector("div:has-text('inscripción')") or
                page.query_selector("p:has-text('postulación enviada')") or
                page.query_selector("h2:has-text('inscrito')")
            )
            if success:
                return "applied"

            return "external:infojobs"

        except Exception as e:
            return f"error: {str(e)[:80]}"

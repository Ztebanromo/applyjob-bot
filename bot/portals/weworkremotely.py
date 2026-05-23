"""
Portal We Work Remotely (weworkremotely.com)

El portal de trabajo remoto más grande del mundo (~200k visitas/mes).
Postulación SIEMPRE externa: el botón Apply lleva al ATS/sitio de la empresa.
Sin login requerido para navegar.

Flujo:
  1. get_offer_urls: extrae hrefs de section.jobs filtrando por experiencia/práctica
  2. apply_to_offer: navega a la oferta, extrae título, aplica filtros, retorna external_apply
"""
import logging
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from .base import BasePortal
from ..stealth_utils import human_delay, take_error_screenshot
from ..config import experience_ok, practica_ok, topic_ok

log = logging.getLogger("applyjob.weworkremotely")

SEL = {
    "card":      "section.jobs li a[href*='/remote-jobs/']",
    "job_title": "h2.listing-header-container, h2[class*='listing'], h1",
    "apply_btn": (
        "a.button:has-text('Apply'), "
        "a:has-text('Apply for this Job'), "
        "a:has-text('Apply Now'), "
        "a[href*='apply']:not([href='#'])"
    ),
    "description": "div.listing-container, .listing-content, .job-description",
}


class WeWorkRemotelyPortal(BasePortal):

    def get_offer_urls(self, page: Page) -> list[str]:
        seen, urls = set(), []
        try:
            try:
                page.wait_for_selector("section.jobs", timeout=12_000)
            except PlaywrightTimeout:
                log.warning("WeWorkRemotely: timeout esperando section.jobs")
                return []

            cards = page.query_selector_all(SEL["card"])
            log.debug("WeWorkRemotely: %d cards encontradas en %s", len(cards), page.url[:60])

            skipped = 0
            for card in cards:
                href = card.get_attribute("href") or ""
                if not href:
                    continue
                if not href.startswith("http"):
                    href = "https://weworkremotely.com" + href

                card_text = (card.text_content() or "")[:300]

                if not experience_ok(card_text):
                    skipped += 1
                    continue
                if not practica_ok(card_text):
                    skipped += 1
                    continue

                if href not in seen:
                    seen.add(href)
                    urls.append(href)

            log.info("  [wwr] %d incluidas | %d descartadas", len(urls), skipped)
        except Exception as exc:
            log.warning("WeWorkRemotelyPortal.get_offer_urls error: %s", exc)
        return urls

    def apply_to_offer(self, page: Page, offer_url: str) -> tuple[str, str]:
        title = "unknown"
        try:
            page.goto(offer_url, wait_until="domcontentloaded", timeout=25_000)
            human_delay(0.7, 1.3)

            # Detectar 404 / oferta eliminada
            cur_url = page.url
            page_text = (page.text_content("body") or "").lower()[:400]
            if ("404" in cur_url or "not-found" in cur_url
                    or "job listing is no longer" in page_text
                    or "this job has expired" in page_text):
                log.info("  [wwr] Oferta expirada/eliminada: %s", offer_url[:60])
                return "skipped_404", title

            # Extraer título
            for sel in SEL["job_title"].split(","):
                el = page.query_selector(sel.strip())
                if el:
                    raw = (el.text_content() or "").strip()
                    first = next((ln.strip() for ln in raw.splitlines() if ln.strip()), raw)
                    if first:
                        title = first[:80]
                        break

            # Filtros en descripción
            desc_el = page.query_selector(SEL["description"])
            desc = (desc_el.text_content() or "")[:600] if desc_el else ""
            full = title + " " + desc

            if not experience_ok(full):
                return "skipped_experience", title
            if not practica_ok(full):
                return "skipped_practica", title

            if self.dry_run:
                log.info("  [wwr] dry_run — registrando sin click")
                return "dry_run", title

            # Obtener URL de postulación (puede ser externa al ATS de la empresa)
            apply_url = offer_url
            for sel in SEL["apply_btn"].split(","):
                btn = page.query_selector(sel.strip())
                if btn and btn.is_visible():
                    href = btn.get_attribute("href") or ""
                    if href and href.startswith("http"):
                        apply_url = href
                    break

            log.info("  [wwr] external_apply -> %s | '%s'", apply_url[:70], title)
            return "external_apply", title

        except Exception as exc:
            log.warning("  [wwr] Error navegando a %s: %s", offer_url[:60], exc)
            return f"error: {exc}", title

"""
Portal Remote.co (remote.co)

Curado por humanos, alta calidad, acepta LATAM explícitamente.
WordPress + WP Job Manager — HTML estático muy estable.
Postulación SIEMPRE externa. Sin login requerido.

Flujo:
  1. get_offer_urls: extrae hrefs de .job_listing
  2. apply_to_offer: navega, verifica región (descarta USA-only), filtra, retorna external_apply
"""
import logging
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from .base import BasePortal
from ..stealth_utils import human_delay
from ..config import experience_ok, practica_ok

log = logging.getLogger("applyjob.remoteco")

# Restricciones de ubicación que excluyen LATAM (texto visible en la oferta)
_LATAM_BLOCKED = frozenset({
    "usa only", "us only", "united states only", "u.s. only",
    "canada only", "uk only", "europe only", "eu only",
    "australia only", "uk/eu only", "north america only",
})

SEL = {
    "card":      ".job_listing",
    "job_title": "h1.job_title, h1",
    "apply_btn": "a.application_button, a:has-text('Apply For Job'), a:has-text('Apply Now')",
    "location":  ".location, .job-location, span[class*='location']",
}


class RemoteCoPortal(BasePortal):

    def get_offer_urls(self, page: Page) -> list[str]:
        seen, urls = set(), []
        try:
            try:
                page.wait_for_selector(SEL["card"], timeout=12_000)
            except PlaywrightTimeout:
                log.warning("Remote.co: timeout esperando .job_listing")
                return []

            # Extraer links de cada card
            listings = page.query_selector_all(f"{SEL['card']} a[href]")
            if not listings:
                listings = page.query_selector_all("a[href*='remote.co/remote-jobs/']")

            log.debug("Remote.co: %d listings encontrados", len(listings))

            for a in listings:
                href = a.get_attribute("href") or ""
                if "remote.co/remote-jobs/" not in href:
                    continue
                # Excluir URL de categoría
                if href.rstrip("/") in (
                    "https://remote.co/remote-jobs",
                    "https://remote.co/remote-jobs/developer",
                    "https://remote.co/remote-jobs/programmer",
                ):
                    continue
                if href not in seen:
                    seen.add(href)
                    urls.append(href)

            log.info("  [remoteco] %d ofertas encontradas", len(urls))
        except Exception as exc:
            log.warning("RemoteCoPortal.get_offer_urls error: %s", exc)
        return urls

    def apply_to_offer(self, page: Page, offer_url: str) -> tuple[str, str]:
        title = "unknown"
        try:
            page.goto(offer_url, wait_until="domcontentloaded", timeout=25_000)
            human_delay(0.7, 1.3)

            # 404 / closed
            cur_url = page.url
            page_text = (page.text_content("body") or "").lower()[:500]
            if ("404" in cur_url or "not-found" in cur_url
                    or "job has been filled" in page_text
                    or "listing is no longer" in page_text):
                return "skipped_404", title

            # Título
            el = page.query_selector(SEL["job_title"])
            if el:
                title = (el.text_content() or "").strip()[:80]

            # Verificar restricción de región — descartar si es USA-only
            loc_el = page.query_selector(SEL["location"])
            if loc_el:
                loc = (loc_el.text_content() or "").lower().strip()
                if any(restr in loc for restr in _LATAM_BLOCKED):
                    log.info("  [remoteco] Descartada (región '%s'): '%s'", loc[:25], title)
                    return "skipped_location", title

            # Filtros de experiencia y práctica
            body = page.evaluate("() => document.body?.innerText?.slice(0, 800) || ''") or ""
            full = title + " " + body
            if not experience_ok(full):
                return "skipped_experience", title
            if not practica_ok(full):
                return "skipped_practica", title

            if self.dry_run:
                return "dry_run", title

            log.info("  [remoteco] external_apply | '%s'", title)
            return "external_apply", title

        except Exception as exc:
            log.warning("  [remoteco] Error: %s | %s", offer_url[:60], exc)
            return f"error: {exc}", title

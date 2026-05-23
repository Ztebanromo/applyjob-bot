"""
Portal Remotive (remotive.com)

Directorio curado 100% remoto, muy fuerte en IT.
Renderizado Next.js SSR — selectores estables.
Postulación SIEMPRE externa. Sin login requerido.

Flujo:
  1. get_offer_urls: espera networkidle (SPA) y extrae hrefs de li[data-id]
  2. apply_to_offer: navega, filtra, retorna external_apply
"""
import logging
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from .base import BasePortal
from ..stealth_utils import human_delay
from ..config import experience_ok, practica_ok

log = logging.getLogger("applyjob.remotive")

# URLs de categoría que NO son ofertas individuales (excluir)
_CATEGORY_URLS = frozenset({
    "https://remotive.com/remote-jobs",
    "https://remotive.com/remote-jobs/software-dev",
    "https://remotive.com/remote-jobs/devops-sysadmin",
    "https://remotive.com/remote-jobs/qa",
    "https://remotive.com/remote-jobs/data",
})


class RemotivePortal(BasePortal):

    def get_offer_urls(self, page: Page) -> list[str]:
        seen, urls = set(), []
        try:
            # Next.js SSR pero tiene hidratación cliente — esperar networkidle
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass  # continuar con lo que haya cargado

            # Selectores en orden de preferencia
            for sel in [
                "li[data-id] a[href*='/remote-jobs/']",
                "a[href*='/remote-jobs/software-dev/']",
                "a[href*='/remote-jobs/devops']",
                "a[href*='/remote-jobs/qa/']",
            ]:
                cards = page.query_selector_all(sel)
                if cards:
                    break

            if not cards:
                # Fallback genérico
                cards = page.query_selector_all("a[href*='/remote-jobs/']")

            log.debug("Remotive: %d cards encontradas en %s", len(cards), page.url[:60])

            skipped = 0
            for a in cards:
                href = a.get_attribute("href") or ""
                if not href.startswith("http"):
                    href = "https://remotive.com" + href
                if href.rstrip("/") in _CATEGORY_URLS:
                    continue
                if "/remote-jobs/" not in href:
                    continue

                card_text = (a.text_content() or "")[:300]
                if not experience_ok(card_text):
                    skipped += 1
                    continue
                if not practica_ok(card_text):
                    skipped += 1
                    continue

                if href not in seen:
                    seen.add(href)
                    urls.append(href)

            log.info("  [remotive] %d incluidas | %d descartadas", len(urls), skipped)
        except Exception as exc:
            log.warning("RemotivePortal.get_offer_urls error: %s", exc)
        return urls

    def apply_to_offer(self, page: Page, offer_url: str) -> tuple[str, str]:
        title = "unknown"
        try:
            page.goto(offer_url, wait_until="domcontentloaded", timeout=25_000)
            human_delay(0.7, 1.3)

            # 404 / removed
            cur_url = page.url
            page_text = (page.text_content("body") or "").lower()[:400]
            if ("404" in cur_url or "not-found" in cur_url
                    or "job is no longer" in page_text
                    or "position has been filled" in page_text):
                return "skipped_404", title

            # Título
            el = page.query_selector("h1")
            if el:
                title = (el.text_content() or "").strip()[:80]

            # Filtros
            body = page.evaluate("() => document.body?.innerText?.slice(0, 800) || ''") or ""
            full = title + " " + body
            if not experience_ok(full):
                return "skipped_experience", title
            if not practica_ok(full):
                return "skipped_practica", title

            if self.dry_run:
                return "dry_run", title

            log.info("  [remotive] external_apply | '%s'", title)
            return "external_apply", title

        except Exception as exc:
            log.warning("  [remotive] Error: %s | %s", offer_url[:60], exc)
            return f"error: {exc}", title

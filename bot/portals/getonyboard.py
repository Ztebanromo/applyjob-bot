"""
Portal GetOnBoard — postulación externa.

GetOnBoard usa URLs tipo slug (/jobs-{slug}) para búsquedas reales.
El parámetro ?q= y los filtros de seniority en URL son ignorados por el SPA.

Flujo:
  1. get_offer_urls: extrae hrefs de a.gb-results-list__item en la página slug
     - Filtra cards con keywords de nivel senior en el título (senior, sr., lead…)
     - Filtra por horario (schedule_ok)
  2. apply_to_offer: navega al job, extrae título, registra external_apply
     - Detecta 404 y retorna "skipped_404"
     - Aplica filtro de horario en la descripción
"""
import logging
import re as _re

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from .base import BasePortal
from ..stealth_utils import human_delay, take_error_screenshot
from ..config import schedule_ok, experience_ok, practica_ok, topic_ok

log = logging.getLogger("applyjob.getonyboard")

SEL = {
    "card":      "a.gb-results-list__item",
    "apply_btn": "a#apply_bottom, a#apply_bottom_short, a.js-go-to-apply",
    "job_title": "h1.gb-landing-cover__title, h1[class*='title'], h1",
}

# Palabras que CONFIRMAN nivel junior/entry -> siempre incluir (nunca filtrar)
_JUNIOR_WORDS = {
    "junior", "jr.", " jr ", "trainee", "practicante", "práctica", "practica",
    "egresado", "recién titulado", "recien titulado",
    "entry level", "sin experiencia", "no experience",
}

# Palabras largas (substring seguro — no aparecen dentro de otras palabras comunes)
_SENIOR_SUBSTRINGS = {
    "senior", "semi senior", "semi-senior",
    "tech lead", "líder", "lider",
    "arquitecto", "architect",
    "jefe de", "gerente", "director de",
    "manager", "head of",
}

# Palabras cortas -> requieren word-boundary para no falsar ("cto" dentro de "proyecto")
_SENIOR_WORDS_EXACT = {"sr", "ssr", "lead", "cto", "cio", "cpo", "vp"}


def _is_senior(title: str) -> bool:
    """
    True si el título indica nivel senior/directivo.
    - Primero verifica palabras junior -> retorna False inmediatamente.
    - Luego verifica substrings seguros (palabras largas).
    - Por último, word-boundary para abreviaciones cortas (cto, cio, sr…).
    """
    tl = title.lower()
    # Junior explícito -> nunca filtrar
    for w in _JUNIOR_WORDS:
        if w in tl:
            return False
    # Substrings seguros (palabras suficientemente largas para no dar falsos positivos)
    for w in _SENIOR_SUBSTRINGS:
        if w in tl:
            return True
    # Abreviaciones cortas: exigir word-boundary
    for w in _SENIOR_WORDS_EXACT:
        if _re.search(r'\b' + _re.escape(w) + r'\b', tl):
            return True
    return False


class GetOnBoardPortal(BasePortal):

    def get_offer_urls(self, page: Page) -> list[str]:
        """
        Extrae hrefs de tarjetas de oferta filtrando:
          - Títulos con nivel senior/directivo
          - Turnos incompatibles (schedule_ok)
        """
        seen: set[str] = set()
        urls: list[str] = []
        skipped_senior   = 0
        skipped_schedule = 0

        try:
            # Esperar a que carguen las tarjetas (slug pages son SSR -> rápido)
            try:
                page.wait_for_selector(SEL["card"], timeout=10_000)
            except PlaywrightTimeout:
                log.warning("GetOnBoard: timeout esperando tarjetas — URL puede ser incorrecta")

            cards = page.query_selector_all(SEL["card"])
            log.debug("GetOnBoardPortal: %d cards encontradas en %s", len(cards), page.url[:60])

            for card in cards:
                try:
                    href = card.get_attribute("href") or ""
                    if not href:
                        continue
                    if not href.startswith("http"):
                        href = "https://www.getonbrd.com" + href

                    # Texto completo del card para filtros
                    card_text = (card.text_content() or "")[:500]

                    # Extraer título del card (primera línea no vacía)
                    card_title = next(
                        (ln.strip() for ln in card_text.splitlines() if ln.strip()), ""
                    )

                    # Filtro senior
                    if _is_senior(card_title):
                        log.debug("  [gob] Descartado (senior): %s", card_title[:60])
                        skipped_senior += 1
                        continue

                    # Filtro horario
                    if not schedule_ok(card_text):
                        log.info("  [gob] Descartado (horario): %s", card_title[:60])
                        skipped_schedule += 1
                        continue

                    # Filtro experiencia
                    if not experience_ok(card_text):
                        log.info("  [gob] Descartado (senior/exp): %s", card_title[:60])
                        skipped_schedule += 1
                        continue

                    if href not in seen:
                        seen.add(href)
                        urls.append(href)

                except Exception as exc:
                    log.debug("Error extrayendo href de card: %s", exc)

            log.info(
                "  [gob] %d incluidas | %d senior descartadas | %d horario descartadas",
                len(urls), skipped_senior, skipped_schedule,
            )

        except Exception as exc:
            log.warning("GetOnBoardPortal.get_offer_urls error: %s", exc)

        return urls

    def apply_to_offer(self, page: Page, offer_url: str) -> tuple[str, str]:
        """
        Navega a la oferta, extrae título y registra como external_apply.
        GetOnBoard siempre requiere cuenta propia — solo registramos la URL de postulación.
        """
        title = "unknown"

        try:
            page.goto(offer_url, wait_until="domcontentloaded", timeout=25_000)
            human_delay(0.7, 1.2)

            # Detectar 404 / página no encontrada
            try:
                not_found = page.query_selector("h1, h2, [class*='error'], [class*='404']")
                if not_found:
                    txt = (not_found.text_content() or "").lower()
                    if any(x in txt for x in ("no encontramos", "not found", "404", "no existe")):
                        log.info("  [gob] 404 detectado: %s", offer_url[:60])
                        return "skipped_404", title
            except Exception:
                pass

            # Extraer título (primera línea del h1 — el resto es empresa/ciudad)
            try:
                for sel in SEL["job_title"].split(","):
                    el = page.query_selector(sel.strip())
                    if el:
                        raw = (el.text_content() or "").strip()
                        first_line = next(
                            (ln.strip() for ln in raw.splitlines() if ln.strip()), raw
                        )
                        if first_line:
                            title = first_line[:80]
                            break
            except Exception:
                pass

            # Filtro senior en el título de la oferta (segunda capa)
            if _is_senior(title):
                log.info("  [gob] Descartada (senior en detalle): '%s'", title)
                return "skipped_senior", title

            # Filtro horario en la descripción
            try:
                desc_text = page.evaluate(
                    "() => {"
                    "  const d = document.querySelector('.gb-job-detail__description,"
                    "    [class*=\"description\"], .gb-landing-cover__description');"
                    "  return d ? d.innerText : document.body?.innerText?.slice(0,800) || '';"
                    "}"
                ) or ""
                full_text = title + " " + desc_text
                if not practica_ok(full_text):
                    log.info("  [gob] Descartada (práctica/pasantía): '%s'", title)
                    return "skipped_practica", title
                if not schedule_ok(full_text):
                    log.info("  [gob] Descartada (horario): '%s'", title)
                    return "skipped_schedule", title
                if not experience_ok(full_text):
                    log.info("  [gob] Descartada (senior/experiencia): '%s'", title)
                    print(f"  [FILTRO] Descartada por nivel/experiencia: {title}")
                    return "skipped_experience", title
                if not topic_ok(full_text):
                    log.info("  [gob] Descartada (fuera de rubro IT/bodega): '%s'", title)
                    return "skipped_topic", title
            except Exception:
                pass

            # Dry-run
            if self.dry_run:
                log.info("  [gob] dry_run — registrando sin click")
                return "dry_run", title

            # Obtener URL de postulación
            apply_url = offer_url
            try:
                for sel_part in SEL["apply_btn"].split(","):
                    btn = page.query_selector(sel_part.strip())
                    if btn and btn.is_visible():
                        apply_href = btn.get_attribute("href") or ""
                        if apply_href:
                            if not apply_href.startswith("http"):
                                apply_href = "https://www.getonbrd.com" + apply_href
                            apply_url = apply_href
                        break
            except Exception as exc:
                log.debug("  [gob] No se encontró botón Postular: %s", exc)

            log.info("  [gob] external_apply -> %s | '%s'", apply_url[:70], title)
            return "external_apply", title

        except Exception as exc:
            log.warning("  [gob] Error navegando a %s: %s", offer_url[:60], exc)
            return f"error: {exc}", title

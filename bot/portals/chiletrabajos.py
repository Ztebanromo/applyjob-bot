"""
Portal ChileTrabajos (chiletrabajos.cl).

Flujo real:
  1. Búsqueda en /empleos?q=... -> lista de div.job-item (30 por página)
  2. Cada card tiene a.font-weight-bold[href*='/trabajo/'] -> URL de detalle
  3. En detalle -> a.postular[href*='/trabajo/postular/{id}']
  4. Click en Postular -> puede redirigir a:
     a. Formulario interno de chiletrabajos (/trabajo/postular/{id})
     b. Login si no hay sesión -> esperar login manual
     c. ATS externo del empleador
  5. Paginación: a[rel='next']

Observaciones:
  - ChileTrabajos requiere cuenta gratuita para postular.
  - Primera vez: el bot espera login manual hasta 5 minutos.
  - Las sesiones se guardan en sessions/chiletrabajos/.
  - Filtro TI por título — los resultados de búsqueda incluyen todo tipo de trabajos.
"""
import logging
import re
import time

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from .base import BasePortal
from ..stealth_utils import human_delay, micro_delay, take_error_screenshot
from ..form_filler import fill_form
from ..config import schedule_ok, experience_ok, practica_ok, topic_ok

log = logging.getLogger("applyjob.chiletrabajos")

BASE_URL = "https://www.chiletrabajos.cl"

SEL = {
    # Cards en el listado de búsqueda
    "card":         "div.job-item",
    "card_link":    "a.font-weight-bold[href*='/trabajo/']",

    # Título en la página de detalle
    "job_title":    "h1.job-title, h1[class*='title'], h1",

    # Botón Postular en el detalle
    "apply_btn":    (
        "a.postular, "
        "a[href*='/trabajo/postular/'], "
        "a:has-text('Postular'), "
        "button:has-text('Postular'), "
        "a[class*='postular']"
    ),

    # Señales de login requerido
    "login_signal": "input[name='email'], input[type='password'], form[action*='login']",

    # Formulario interno de postulación
    "apply_form":   "form[action*='postular'], div.postulacion-form, div[class*='apply']",

    # Submit
    "form_submit": (
        "button[type='submit'], "
        "input[type='submit'], "
        "button:has-text('Enviar'), "
        "button:has-text('Postular'), "
        "a:has-text('Confirmar')"
    ),

    # Éxito
    "success_signal": (
        "div:has-text('postulación enviada'), "
        "div:has-text('Te has postulado'), "
        "div:has-text('Postulación exitosa'), "
        "div:has-text('postulaste exitosamente'), "
        "div.alert-success, "
        "div[class*='success']"
    ),

    # Paginación
    "next_page":    "a[rel='next'], a[data-ci-pagination-page]",
}

# Palabras TI — suficientemente específicas para no dar falsos positivos
_TECH_WORDS = {
    # Roles TI (específicos)
    "desarrollador", "programador", "developer", "software engineer",
    "fullstack", "full stack", "frontend", "front-end", "backend", "back-end",
    "devops", "ingeniero en", "ingeniero de sistemas", "ingeniero ti",
    # Lenguajes / stacks (únicos en contexto TI)
    "python", "javascript", "typescript", "react", "angular", "node.js",
    "php", "ruby", "golang", "kotlin", "swift", ".net", "java developer",
    # Especialidades TI (compuestas para evitar falsos positivos)
    "analista programador", "analista de sistemas", "analista ti", "analista bi",
    "soporte ti", "soporte técnico ti", "soporte técnico en ti",
    "técnico ti", "técnico en informática", "técnico en telecomunicaciones",
    "data engineer", "data scientist", "machine learning", "inteligencia artificial",
    "qa engineer", "qa analyst", "tester", "testing",
    "cloud", "aws", "azure", "gcp", "ciberseguridad", "seguridad informática",
    "infraestructura ti", "redes y telecomunicaciones",
    # Bodega/Logística — términos compuestos para precisión
    "operario bodega", "operario de bodega",
    "auxiliar bodega", "auxiliar de bodega",
    "ayudante bodega", "ayudante de bodega",
    "bodeguero", "jefe de bodega",
    "operador logístico", "operador logistico",
    "auxiliar logístico", "auxiliar logistico",
    "logística y distribución",
    # Niveles entrada
    "trainee", "junior", "jr.", "practicante",
    "recién titulado", "recien titulado", "egresado",
}

# Palabras que indican nivel senior/dirección — se excluyen
_SENIOR_WORDS = {
    "senior", " sr.", " sr ", "semi senior", "ssr", "semi-senior",
    "jefe de proyecto", "gerente", "director de", "lead ", "tech lead", "líder de",
    "principal", "arquitecto de", "cto", "vp de", "head of",
    "manager", "coordinador", "supervisor de",
}

DETAIL_TIMEOUT = 25_000


class ChileTrabajosPortal(BasePortal):

    _login_done: bool = False

    def _title_is_tech(self, title: str) -> bool:
        """True si el título parece ser un puesto TI."""
        tl = title.lower()
        return any(w in tl for w in _TECH_WORDS)

    def _title_is_senior(self, title: str) -> bool:
        """True si el título indica nivel senior/directivo (se excluye)."""
        tl = title.lower()
        return any(w in tl for w in _SENIOR_WORDS)

    def _title_ok(self, title: str) -> bool:
        """
        True si el puesto debe ser postulado:
        - Es tech (o no tiene título -> se incluye por si acaso)
        - NO es senior/directivo
        """
        if not title:
            return True   # sin título -> incluir por defecto
        return self._title_is_tech(title) and not self._title_is_senior(title)

    def _wait_for_login(self, page: Page, timeout_s: int = 300) -> bool:
        def _on_login() -> bool:
            try:
                url = page.url
                return "login" in url.lower() or "iniciar" in url.lower() or "signin" in url.lower()
            except Exception:
                return False

        def _logged_in() -> bool:
            try:
                url = page.url
                return "chiletrabajos.cl" in url and "login" not in url.lower()
            except Exception:
                return False

        if not _on_login():
            return True

        log.warning("ChileTrabajos requiere inicio de sesión. Esperando hasta %ds...", timeout_s)
        print(f"\n[LOGIN_REQUERIDO] Inicia sesión en ChileTrabajos en el navegador abierto.\n")

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            time.sleep(3)
            if _logged_in():
                log.info("Sesión de ChileTrabajos detectada. Continuando.")
                ChileTrabajosPortal._login_done = True
                human_delay(1.0, 1.5)
                return True

        log.error("Tiempo de espera agotado esperando login en ChileTrabajos.")
        return False

    def get_offer_urls(self, page: Page) -> list[str]:
        seen: set[str] = set()
        urls: list[str] = []

        try:
            try:
                page.wait_for_selector(SEL["card"], timeout=10_000)
            except PlaywrightTimeout:
                log.warning("ChileTrabajosPortal: tiempo de espera agotado esperando tarjetas de oferta.")

            cards = page.query_selector_all(SEL["card"])
            log.debug("ChileTrabajos: %d cards encontradas", len(cards))

            for card in cards:
                try:
                    link = card.query_selector(SEL["card_link"])
                    if not link:
                        link = card.query_selector("a[href*='/trabajo/']")
                    if not link:
                        continue

                    href = link.get_attribute("href") or ""
                    if not href:
                        continue
                    if not href.startswith("http"):
                        href = BASE_URL + href

                    # Solo URLs de detalle de oferta (no de búsqueda)
                    if "/trabajo/" not in href or "/postular/" in href:
                        continue

                    # Filtrar: TI + no senior
                    title = ""
                    try:
                        title = (link.text_content() or "").strip()
                    except Exception:
                        pass

                    # Filtro de horario en el card
                    card_text = ""
                    try:
                        card_text = (card.text_content() or "")[:500]
                    except Exception:
                        pass
                    # Filtro 1: horario (lunes a viernes AM)
                    if not schedule_ok(card_text):
                        log.info("  [ct2] Descartado (horario): %s", card_text[:80].strip())
                        continue

                    # Filtro 2: experiencia (solo junior / sin experiencia)
                    if not experience_ok(title + " " + card_text):
                        log.info("  [ct2] Descartado (senior/exp): %s", title[:60])
                        continue

                    if self._title_ok(title):
                        if href not in seen:
                            seen.add(href)
                            urls.append(href)
                            log.debug("  [ct2] incluida: %s", title[:60])
                    else:
                        log.debug("  [ct2] filtrada (senior/no-tech): %s", title[:60])

                except Exception as exc:
                    log.debug("Error extrayendo card: %s", exc)
                    continue

        except Exception as exc:
            log.warning("ChileTrabajosPortal.get_offer_urls error: %s", exc)

        log.debug("ChileTrabajos: %d URLs extraídas", len(urls))
        return urls

    def apply_to_offer(self, page: Page, offer_url: str) -> tuple[str, str]:
        title = "unknown"

        try:
            # -- 1. Navegar al detalle -------------------------------------
            page.goto(offer_url, wait_until="domcontentloaded", timeout=DETAIL_TIMEOUT)
            human_delay(0.8, 1.5)

            # -- 2. Extraer título -----------------------------------------
            try:
                for sel in SEL["job_title"].split(","):
                    el = page.query_selector(sel.strip())
                    if el:
                        title = (el.text_content() or "").strip()[:80]
                        if title:
                            break
            except Exception:
                pass

            # -- 2b. Filtros sobre descripción real -------------------------
            try:
                desc_text = page.evaluate(
                    "() => {"
                    "  const d = document.querySelector("
                    "    'div.job-description, div[class*=\"description\"],"
                    "     section.description, article');"
                    "  return d ? d.innerText : document.body?.innerText?.slice(0,1000) || '';"
                    "}"
                ) or ""
                full_text = title + " " + desc_text
                if not practica_ok(full_text):
                    log.info("  [ct2] Descartada (práctica/pasantía): '%s'", title)
                    return "skipped_practica", title
                if not schedule_ok(full_text):
                    log.info("  [ct2] Descartada (horario): '%s'", title)
                    return "skipped_schedule", title
                if not experience_ok(full_text):
                    log.info("  [ct2] Descartada (senior/experiencia): '%s'", title)
                    return "skipped_experience", title
                if not topic_ok(full_text):
                    log.info("  [ct2] Descartada (fuera de rubro IT/bodega): '%s'", title)
                    return "skipped_topic", title
            except Exception:
                pass

            # -- 3. Buscar botón Postular ----------------------------------
            apply_btn = self._find_visible(page, SEL["apply_btn"])
            if not apply_btn:
                page.evaluate("window.scrollTo(0, 400)")
                human_delay(0.5, 0.8)
                apply_btn = self._find_visible(page, SEL["apply_btn"])

            if not apply_btn:
                log.warning("  [ct2] Botón Postular no encontrado: %s", offer_url[:60])
                take_error_screenshot(page, "chiletrabajos", "no_apply_btn")
                return "error: no_apply_button", title

            # -- 4. Dry-run ------------------------------------------------
            if self.dry_run:
                log.info("  [ct2] dry_run — no se hace click en Postular")
                return "dry_run", title

            # -- 5. Click en Postular --------------------------------------
            try:
                apply_btn.scroll_into_view_if_needed()
                micro_delay()
                apply_btn.click()
                human_delay(1.2, 2.0)
            except Exception as exc:
                return f"error: click_apply {exc}", title

            current_url = page.url

            # -- 6. Detectar resultado -------------------------------------

            # Login requerido
            if "login" in current_url.lower() or "iniciar" in current_url.lower():
                if not ChileTrabajosPortal._login_done:
                    login_ok = self._wait_for_login(page, timeout_s=300)
                    if not login_ok:
                        return "skipped_login_required", title
                    # Reintentar desde la URL de postulación directa
                    postular_url = offer_url.replace("/trabajo/", "/trabajo/postular/")
                    # Extraer ID del slug: /trabajo/slug-123456 -> /trabajo/postular/123456
                    m = re.search(r'-(\d+)$', offer_url.rstrip('/'))
                    if m:
                        postular_url = f"{BASE_URL}/trabajo/postular/{m.group(1)}"
                    page.goto(postular_url, wait_until="domcontentloaded", timeout=DETAIL_TIMEOUT)
                    human_delay(1.0, 1.5)
                    current_url = page.url
                else:
                    ChileTrabajosPortal._login_done = False
                    return "skipped_login_required", title

            # Éxito directo
            if self._check_success(page):
                log.info("  [ct2] [OK] Postulación exitosa directa")
                return "applied", title

            # Formulario interno de chiletrabajos
            if "chiletrabajos.cl" in current_url and ("postular" in current_url or self._is_visible(page, SEL["apply_form"])):
                return self._fill_apply_form(page, title)

            # ATS externo real
            if "chiletrabajos.cl" not in current_url:
                log.info("  [ct2] ATS externo: %s", current_url[:60])
                return "external_apply", title

            # Esperar y verificar
            human_delay(2.0, 3.0)
            if self._check_success(page):
                return "applied", title

            return self._fill_apply_form(page, title)

        except Exception as exc:
            log.error("  [ct2] Error: %s", exc)
            try:
                take_error_screenshot(page, "chiletrabajos", "exception")
            except Exception:
                pass
            return f"error: {type(exc).__name__}", title

    def _fill_apply_form(self, page: Page, title: str) -> tuple[str, str]:
        """Rellena y envía el formulario de postulación interno de ChileTrabajos."""
        human_delay(0.8, 1.2)

        if self._check_success(page):
            return "applied", title

        try:
            fill_form(page, self.profile)
        except Exception as exc:
            log.debug("  [ct2] fill_form error: %s", exc)

        # Adjuntar CV
        try:
            cv_input = page.query_selector("input[type='file']")
            cv_path = self.profile.get("cv_path", "")
            if cv_input and cv_path:
                import os
                if os.path.exists(cv_path):
                    cv_input.set_input_files(cv_path)
                    log.debug("  [ct2] CV adjuntado")
        except Exception:
            pass

        human_delay(0.5, 0.8)

        # Submit
        for sel in SEL["form_submit"].split(","):
            sel = sel.strip()
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible() and btn.is_enabled():
                    btn.click()
                    human_delay(1.2, 2.0)
                    break
            except Exception:
                continue

        if self._check_success(page):
            log.info("  [ct2] [OK] Formulario enviado")
            return "applied", title

        log.info("  [ct2] Formulario procesado (sin confirmación visual)")
        return "applied", title

    def _find_visible(self, page: Page, selector: str):
        for sel_part in selector.split(","):
            sel_part = sel_part.strip()
            try:
                for el in page.query_selector_all(sel_part):
                    if el.is_visible():
                        return el
            except Exception:
                continue
        return None

    def _is_visible(self, page: Page, selector: str) -> bool:
        return self._find_visible(page, selector) is not None

    def _check_success(self, page: Page) -> bool:
        return self._is_visible(page, SEL["success_signal"])

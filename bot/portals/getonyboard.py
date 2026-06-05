"""
Portal GetOnBoard — postulación con seguimiento a ATS externo.

GetOnBoard usa URLs tipo slug (/jobs-{slug}) para búsquedas reales.
El parámetro ?q= y los filtros de seniority en URL son ignorados por el SPA.

Flujo:
  1. get_offer_urls: extrae hrefs de /empleos/ o /jobs/ filtrando seniors
  2. apply_to_offer:
     a) Detecta botón Postular
     b) Si abre nueva pestaña (ATS externo) → intenta fill_form en esa pestaña
        - Si el ATS es simple (Greenhouse, Lever, BambooHR, genérico) → llena y envía → "applied"
        - Si falla (login requerido, Workday, etc.) → "external_apply"
     c) Si no abre pestaña → detecta formulario rápido en la misma página → llena → "applied"
     d) Si no hay botón → "external_apply"
"""
import logging
import re as _re

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from .base import BasePortal
from ..stealth_utils import human_delay, take_error_screenshot
from ..config import schedule_ok, experience_ok, practica_ok, topic_ok

log = logging.getLogger("applyjob.getonyboard")

SEL = {
    # GetOnBoard renovó su CSS — el selector antiguo (a.gb-results-list__item) ya no funciona.
    # Las tarjetas actuales son enlaces con hrefs /empleos/ (castellano) o /jobs/ (inglés).
    "card":      "a[href*='/empleos/'], a[href*='getonbrd.com/jobs/']",
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


def _try_fill_external_ats(page, profile: dict, title: str) -> str:
    """
    Intenta llenar y enviar un formulario de ATS externo (Greenhouse, Lever, BambooHR,
    formularios genéricos de empresa, etc.) en la nueva pestaña que abrió GetOnBoard.

    Retorna:
        "applied"  — formulario enviado con éxito
        "failed"   — ATS no soportado, requiere login, o sin formulario

    Portales omitidos:
        - Login/registro requerido (workday, taleo, successfactors, brassring)
        - LinkedIn/Indeed (sesión separada)
        - Páginas de empresa sin formulario
    """
    from ..form_filler import fill_form
    from ..stealth_utils import human_delay as _hd

    # ── ATSs que requieren cuenta propia — no intentar ──────────────────────
    _SKIP_DOMAINS = (
        "workday", "taleo", "successfactors", "brassring", "icims",
        "oraclecloud", "myworkdayjobs",
        "linkedin.com", "indeed.com",
    )
    # ── Indicadores de pantalla de login/registro ────────────────────────────
    _LOGIN_PATHS = ("login", "signin", "sign-in", "register", "signup", "auth/", "/account")

    try:
        url = page.url
        url_low = url.lower()

        # Saltar ATSs complejos o pantallas de login
        if any(d in url_low for d in _SKIP_DOMAINS):
            log.info("  [ats] ATS no soportado/complejo: %s", url[:60])
            return "failed"
        if any(p in url_low for p in _LOGIN_PATHS):
            log.info("  [ats] Pantalla de login detectada: %s", url[:60])
            return "failed"

        # Detectar si hay algún formulario en la página
        _hd(1.0, 1.8)
        page_text = (page.text_content("body") or "").lower()
        # Saltar páginas de error o sin formulario
        if any(x in page_text for x in ("404", "not found", "page not found", "oferta no disponible")):
            return "failed"

        # Verificar que hay inputs o textareas (formulario real)
        inputs = page.query_selector_all("input:not([type='hidden']):visible, textarea:visible, select:visible")
        if len(inputs) < 2:
            log.info("  [ats] Sin formulario detectable en %s", url[:60])
            return "failed"

        # Usar fill_form para llenar todos los campos conocidos
        form_result = fill_form(page, profile, job_title=title, portal="getonyboard", url=apply_url)
        answered    = (form_result.get("text_fields", 0)
                       + form_result.get("radio_answers", 0)
                       + (1 if form_result.get("file_uploaded") else 0))
        unanswered  = form_result.get("unanswered", 0)

        if answered == 0 and unanswered == 0:
            log.info("  [ats] Formulario no respondido (sin campos reconocidos): %s", url[:60])
            return "failed"

        # Fallback cover_letter para textareas vacíos
        if unanswered > 0:
            cover = profile.get("cover_letter", "") or profile.get("bodega_exp", "")
            if cover:
                for ta in page.query_selector_all("textarea:visible"):
                    try:
                        if not (ta.evaluate("el => el.value") or "").strip():
                            ta.fill(cover[:500])
                    except Exception:
                        pass

        # Intentar submit
        _SUBMIT = [
            "button[type='submit']:visible",
            "input[type='submit']:visible",
            "button:has-text('Submit Application'):visible",
            "button:has-text('Submit'):visible",
            "button:has-text('Apply'):visible",
            "button:has-text('Postular'):visible",
            "button:has-text('Postularme'):visible",
            "button:has-text('Enviar postulación'):visible",
            "button:has-text('Enviar'):visible",
            "button:has-text('Send'):visible",
            "button:has-text('Continuar'):visible",
            "button:has-text('Continue'):visible",
        ]
        for sel in _SUBMIT:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible() and btn.is_enabled():
                    btn.click()
                    _hd(2.0, 3.0)
                    log.info("  [ats] Enviado con selector %s en %s", sel, url[:60])
                    return "applied"
            except Exception:
                pass

        log.info("  [ats] Formulario llenado pero sin botón submit: %s", url[:60])
        return "failed"

    except Exception as exc:
        log.warning("  [ats] Error en fill_external_ats: %s", exc)
        return "failed"


def _try_fill_gob_quick_apply(page, profile: dict, title: str) -> bool:
    """
    Detecta y rellena el formulario de Postulación Rápida de GetOnBoard.

    GetOnBoard muestra un formulario inline/modal con preguntas como:
      - ¿Por qué te interesa trabajar aquí?
      - Pretensión de renta
      - Disponibilidad
      - Preguntas técnicas opcionales

    Retorna True si el formulario fue enviado con éxito, False si no se detectó.
    """
    from ..form_filler import fill_form
    from ..stealth_utils import human_delay as _hd

    try:
        # Selectores de formulario quick-apply en GetOnBoard
        FORM_SELECTORS = [
            "form.gb-application-form",
            "form[class*='application']",
            "form[class*='apply']",
            ".gb-application-form",
            "[class*='quick-apply'] form",
            "form:has(textarea):visible",
            # Modal overlay con formulario
            ".gb-modal form",
            "[role='dialog'] form",
        ]

        form_found = False
        for sel in FORM_SELECTORS:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    form_found = True
                    log.info("  [gob-quick] Formulario detectado con selector: %s", sel)
                    break
            except Exception:
                pass

        # Segunda detección: buscar textarea visible que no sea de búsqueda
        if not form_found:
            try:
                textareas = page.query_selector_all("textarea:visible")
                # Filtrar textareas de búsqueda / header
                real_ta = [ta for ta in textareas
                           if not (ta.get_attribute("placeholder") or "").lower().startswith("busca")]
                if real_ta:
                    form_found = True
                    log.info("  [gob-quick] Textarea visible detectada — probable formulario quick-apply")
            except Exception:
                pass

        if not form_found:
            return False

        # Usar fill_form del motor genérico
        form_result = fill_form(page, profile, job_title=title, portal="getonyboard", url=page.url)
        answered   = form_result.get("answered", 0)
        unanswered = form_result.get("unanswered", 0)
        log.info("  [gob-quick] fill_form: %d respondidas, %d sin respuesta", answered, unanswered)

        # Si hay campos sin respuesta, usar cover_letter como fallback
        if unanswered > 0:
            cover = profile.get("cover_letter", "") or profile.get("bodega_exp", "")
            if cover:
                try:
                    for ta in page.query_selector_all("textarea:visible"):
                        val = (ta.evaluate("el => el.value") or "").strip()
                        if not val:
                            ta.fill(cover[:500])
                            log.debug("  [gob-quick] cover_letter aplicado a textarea vacío")
                except Exception:
                    pass

        # Intentar submit
        SUBMIT_SELECTORS = [
            "button[type='submit']:visible",
            "input[type='submit']:visible",
            "button:has-text('Enviar postulación'):visible",
            "button:has-text('Postularme'):visible",
            "button:has-text('Postular'):visible",
            "button:has-text('Enviar'):visible",
            "button:has-text('Submit'):visible",
            "button:has-text('Apply'):visible",
        ]
        for submit_sel in SUBMIT_SELECTORS:
            try:
                btn = page.query_selector(submit_sel)
                if btn and btn.is_visible() and btn.is_enabled():
                    btn.click()
                    _hd(1.5, 2.5)
                    log.info("  [gob-quick] Formulario enviado con selector: %s", submit_sel)
                    return True
            except Exception:
                pass

        log.info("  [gob-quick] Formulario detectado pero no se encontró botón submit")
        return False

    except Exception as exc:
        log.warning("  [gob-quick] Error en fill_gob_quick_apply: %s", exc)
        return False


def _is_gob_multistep_url(url: str) -> bool:
    """True si la URL corresponde al flujo multi-paso de GetOnBoard (/applications/{id}/edit)."""
    return "/applications/" in url and "/edit" in url


def _is_gob_applications_list_url(url: str) -> bool:
    """True si la URL corresponde al dashboard de aplicaciones de GetOnBoard."""
    lower = url.lower()
    return "/applications" in lower and "/edit" not in lower


def _absolute_gob_url(page: Page, href: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return "https://www.getonbrd.com" + href
    base = "/".join(page.url.split("/")[:3])
    return base + "/" + href.lstrip("/")


def _find_gob_application_links(page: Page) -> list[str]:
    urls: list[str] = []
    for anchor in page.query_selector_all("a[href*='/applications/']"):
        try:
            href = (anchor.get_attribute("href") or "").strip()
            if not href:
                continue
            if "/edit" in href.lower():
                abs_url = _absolute_gob_url(page, href)
                if abs_url not in urls:
                    urls.append(abs_url)
        except Exception:
            continue
    return urls


def _get_gob_step(page) -> str:
    """
    Detecta en qué paso del flujo está.
    Retorna: 'experiencia' | 'info_basica' | 'preguntas' | 'preview' | 'unknown'
    """
    url = page.url.lower()
    # Por URL
    if "step=preview"    in url: return "preview"
    if "step=questions"  in url: return "preguntas"
    if "step=basic_data" in url: return "info_basica"
    if "step=basic"      in url: return "experiencia"
    # Por texto visible en la página (GetOnBoard puede usar rutas sin query param)
    try:
        body = page.evaluate("document.body?.innerText?.slice(0, 300) || ''") or ""
        body_low = body.lower()
        if "vista previa de tu postulacion" in body_low or "enviar postulacion ahora" in body_low:
            return "preview"
        if "aun no ha sido enviada" in body_low or "todavia no ha sido enviada" in body_low:
            return "preview"
    except Exception:
        pass
    # Por indicador de paso activo en la página
    try:
        active = page.query_selector(
            "li.active, [class*='active'] span, [aria-current='step'], "
            ".gb-breadcrumb__item--active, .step--active"
        )
        if active:
            txt = (active.text_content() or "").lower()
            if "previa"    in txt or "preview" in txt: return "preview"
            if "pregunta"  in txt or "question" in txt: return "preguntas"
            if "b" in txt and "sica" in txt:            return "info_basica"
            if "experiencia" in txt:                    return "experiencia"
    except Exception:
        pass
    return "unknown"


def _navigate_gob_multistep(page, profile: dict, title: str) -> bool:
    """
    Navega el flujo multi-paso de GetOnBoard (4 pasos):
      1. Experiencia  → fill + Siguiente
      2. Info básica  → fill + Siguiente
      3. Preguntas    → fill QA cache + cover_letter + Siguiente
      4. Vista previa → click Postular

    Retorna True si la postulación fue enviada, False si falló.
    """
    from ..form_filler import fill_form
    from ..stealth_utils import human_delay as _hd

    max_steps = 6
    cover = profile.get("cover_letter", "") or ""

    for step_n in range(max_steps):
        current_url = page.url
        step = _get_gob_step(page)
        log.info("  [gob-multi] Paso %d: %s | %s", step_n + 1, step, current_url[:60])
        print(f"  [GOB] Paso {step_n + 1}: {step}")

        # ── Paso 4: Vista previa → enviar ─────────────────────────────────────
        if step == "preview":
            for sel in [
                "button:has-text('Enviar postulación ahora'):visible",
                "a:has-text('Enviar postulación ahora'):visible",
                "button:has-text('Enviar postulación'):visible",
                "a:has-text('Enviar postulación'):visible",
                "button:has-text('Postular'):visible",
                "button:has-text('Enviar mi postulación'):visible",
                "button:has-text('Submit'):visible",
                "input[type='submit']:visible",
                "button[type='submit']:visible",
            ]:
                try:
                    btn = page.query_selector(sel)
                    if btn and btn.is_visible() and btn.is_enabled():
                        btn.click()
                        _hd(2.0, 3.5)
                        log.info("  [gob-multi] Postulación enviada en preview: '%s'", title)
                        print(f"  [GOB] ✓ Postulado: {title[:60]}")
                        return True
                except Exception:
                    pass
            log.warning("  [gob-multi] En preview pero sin botón de envío visible")
            return False

        # ── Pasos 1-3: llenar campos ──────────────────────────────────────────
        _hd(0.8, 1.5)

        # Detectar si el formulario requiere inglés
        _requires_en = False
        try:
            body_txt = (page.evaluate("document.body?.innerText?.slice(0,500) || ''") or "").lower()
            _requires_en = "ingles" in body_txt or "in english" in body_txt or "inglés" in body_txt
        except Exception:
            pass

        # fill_form usa el QA cache para preguntas y los datos del perfil para fields estándar
        try:
            fill_form(page, profile, job_title=title)
        except Exception as fe:
            log.debug("  [gob-multi] fill_form error: %s", fe)

        # Salary USD — campo numérico vacío
        try:
            sal_inp = page.query_selector("input[type='number']:visible, input[placeholder*='USD' i]:visible, input[placeholder*='salary' i]:visible")
            if sal_inp:
                val = (sal_inp.evaluate("el => el.value") or "").strip()
                if not val or val == "0":
                    sal_inp.fill("800")
                    log.debug("  [gob-multi] salary USD rellenado: 800")
        except Exception:
            pass

        # Checkbox "Certifico que tengo residencia legal en Chile"
        try:
            chk = page.query_selector("input[type='checkbox']:not(:checked):visible")
            if chk:
                chk.click()
                log.debug("  [gob-multi] checkbox residencia marcado")
        except Exception:
            pass

        # Textareas vacíos → cover_letter en el idioma requerido
        _cover_text = (
            "I am a recently graduated Programmer Analyst from INACAP (2024) with practical experience "
            "in enterprise systems SAP WM, WMS, and RF Terminal. I handle Python for scripting and automation, "
            "SQL for database queries, JavaScript/HTML/CSS for web development, and Git for version control. "
            "I combine a solid technical foundation with real understanding of logistics and retail business flows. "
            "Available immediately for a formal IT position."
        ) if _requires_en else cover

        if _cover_text:
            try:
                for ta in page.query_selector_all("textarea:visible"):
                    val = (ta.evaluate("el => el.value") or "").strip()
                    if not val:
                        ta.fill(_cover_text[:1000])
                        log.debug("  [gob-multi] cover (%s) aplicado a textarea", "EN" if _requires_en else "ES")
            except Exception:
                pass

        # ── Detectar errores de validación antes de avanzar ──────────────────
        try:
            err = page.query_selector(
                "[class*='error']:visible, [class*='invalid']:visible, "
                ".field_with_errors:visible, [aria-invalid='true']:visible"
            )
            if err:
                log.warning("  [gob-multi] Validación fallida en step %s: %s", step, err.text_content()[:60])
        except Exception:
            pass

        # ── Click en "Siguiente" y verificar avance ───────────────────────────
        SIGUIENTE_SELS = [
            "button:has-text('Siguiente'):visible",
            "a:has-text('Siguiente'):visible",
            "button:has-text('Next'):visible",
            "button:has-text('Continuar'):visible",
            "input[type='submit']:visible",
        ]
        advanced = False
        for sel in SIGUIENTE_SELS:
            try:
                btn = page.query_selector(sel)
                if not (btn and btn.is_visible()):
                    continue
                # Esperar a que esté habilitado (puede estar disabled durante validación)
                for _ in range(5):
                    if btn.is_enabled():
                        break
                    _hd(0.4, 0.6)
                if not btn.is_enabled():
                    log.warning("  [gob-multi] Botón Siguiente deshabilitado — ¿validación pendiente?")
                    continue
                btn.click()
                _hd(1.5, 2.5)
                # Esperar cambio de URL o de contenido (SPA puede no recargar)
                try:
                    page.wait_for_function(
                        f"() => window.location.href !== {repr(current_url)}",
                        timeout=8_000
                    )
                except Exception:
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=5_000)
                    except Exception:
                        pass
                new_url = page.url
                if new_url != current_url:
                    log.info("  [gob-multi] Avanzó: %s → %s", current_url[-30:], new_url[-30:])
                    advanced = True
                else:
                    log.warning("  [gob-multi] URL no cambió tras Siguiente — ¿validación?")
                break
            except Exception as ce:
                log.debug("  [gob-multi] Click error (%s): %s", sel, ce)

        if not advanced:
            log.warning("  [gob-multi] No se pudo avanzar desde paso %s", step)
            break

    log.warning("  [gob-multi] Flujo no completado para '%s'", title)
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

            # Si llegamos al dashboard de aplicaciones, procesar aplicaciones incompletas
            if _is_gob_applications_list_url(offer_url):
                app_links = _find_gob_application_links(page)
                if not app_links:
                    log.info("  [gob] Dashboard de aplicaciones sin enlaces editables: %s", offer_url[:70])
                    return "skipped_no_applications", title

                applied_any = False
                for link in app_links:
                    try:
                        page.goto(link, wait_until="domcontentloaded", timeout=20_000)
                        human_delay(0.7, 1.2)

                        if _is_gob_multistep_url(page.url):
                            log.info("  [gob] Procesando aplicación GetOnBoard: %s", page.url[:70])
                            if _navigate_gob_multistep(page, self.profile, title):
                                applied_any = True
                                break
                            continue

                        if _try_fill_gob_quick_apply(page, self.profile, title):
                            applied_any = True
                            break
                    except Exception as exc:
                        log.warning("  [gob] Error procesando enlace de aplicación: %s", exc)
                        continue

                status = "applied" if applied_any else "pending_saved"
                return status, title

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
                # Revisar experiencia solo en los primeros 500 chars de descripción
                # para evitar falsos positivos en texto largo de ofertas genéricas
                exp_text = title + " " + desc_text[:500]
                if not experience_ok(exp_text):
                    log.info("  [gob] Descartada (senior/experiencia): '%s'", title)
                    print(f"  [FILTRO] Descartada por nivel/experiencia: {title}")
                    return "skipped_experience", title
                if not topic_ok(title):   # solo el título — descripción puede mencionar sectores no-IT
                    log.info("  [gob] Descartada (fuera de rubro IT/bodega): '%s'", title)
                    return "skipped_topic", title
            except Exception:
                pass

            # Dry-run
            if self.dry_run:
                log.info("  [gob] dry_run — registrando sin click")
                return "dry_run", title

            # ── Buscar botón Postular y hacer click real ─────────────────────
            apply_url = offer_url
            btn_found = None
            try:
                for sel_part in SEL["apply_btn"].split(","):
                    el = page.query_selector(sel_part.strip())
                    if el and el.is_visible():
                        apply_href = el.get_attribute("href") or ""
                        if apply_href:
                            if not apply_href.startswith("http"):
                                apply_href = "https://www.getonbrd.com" + apply_href
                            apply_url = apply_href
                        btn_found = el
                        break
            except Exception as exc:
                log.debug("  [gob] No se encontró botón Postular: %s", exc)

            if not btn_found:
                log.info("  [gob] Sin botón Postular visible — external_apply: %s", apply_url[:70])
                print(f"  [GOB] Sin botón Postular — registrando URL: {apply_url[:70]}")
                return "external_apply", title

            # Intentar click: el botón puede abrir nueva pestaña (target=_blank) o navegar
            try:
                opens_new_tab = (btn_found.get_attribute("target") or "").strip() == "_blank"

                if opens_new_tab:
                    # Capturar la nueva pestaña antes de hacer click
                    with page.context.expect_page(timeout=10_000) as new_page_info:
                        btn_found.click()
                    new_page = new_page_info.value
                    new_page.wait_for_load_state("domcontentloaded", timeout=20_000)
                    apply_url = new_page.url
                    log.info("  [gob] Nueva pestaña → %s | '%s'", apply_url[:70], title)
                    print(f"  [GOB] Postulación abierta: {apply_url[:60]}")

                    if not self.dry_run:
                        # ── Flujo multi-paso de GetOnBoard ─────────────────────
                        if _is_gob_multistep_url(apply_url):
                            log.info("  [gob] Flujo multi-paso detectado: %s", apply_url[:70])
                            print(f"  [GOB] Flujo multi-paso — navegando pasos: {title[:60]}")
                            multistep_ok = _navigate_gob_multistep(new_page, self.profile, title)
                            try:
                                new_page.close()
                            except Exception:
                                pass
                            if multistep_ok:
                                print(f"  [GOB] ✓ Postulación multi-paso enviada: {title[:60]}")
                                return "applied", title
                            return "external_apply", title

                        # ── ATS externo genérico ────────────────────────────────
                        ats_result = _try_fill_external_ats(new_page, self.profile, title)
                        if ats_result == "applied":
                            log.info("  [gob] ATS externo completado: '%s'", title)
                            print(f"  [GOB] ✓ Formulario ATS enviado: {title[:60]}")
                            try:
                                new_page.close()
                            except Exception:
                                pass
                            return "applied", title

                    try:
                        new_page.close()
                    except Exception:
                        pass
                else:
                    btn_found.click()
                    # Esperar un momento para que cargue formulario (AJAX) o navegue
                    human_delay(1.2, 2.0)
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=10_000)
                    except PlaywrightTimeout:
                        pass
                    apply_url = page.url

                    if "getonbrd.com" in apply_url:
                        # ── Flujo multi-paso en mismo tab ───────────────────────
                        if _is_gob_multistep_url(apply_url):
                            log.info("  [gob] Flujo multi-paso (mismo tab): %s", apply_url[:70])
                            multistep_ok = _navigate_gob_multistep(page, self.profile, title)
                            if multistep_ok:
                                print(f"  [GOB] ✓ Postulación multi-paso enviada: {title[:60]}")
                                return "applied", title

                        # ── Postulación Rápida ──────────────────────────────────
                        quick_applied = _try_fill_gob_quick_apply(page, self.profile, title)
                        if quick_applied:
                            log.info("  [gob] Postulación Rápida completada: '%s'", title)
                            print(f"  [GOB] ✓ Postulación Rápida enviada: {title[:60]}")
                            return "applied", title

                    log.info("  [gob] Navegó a: %s | '%s'", apply_url[:70], title)
                    print(f"  [GOB] Postulación abierta: {apply_url[:70]}")

            except PlaywrightTimeout:
                log.warning("  [gob] Timeout esperando navegación post-click — usando href directo")
            except Exception as click_err:
                log.warning("  [gob] Error en click Postular (%s) — usando href directo", click_err)

            log.info("  [gob] external_apply -> %s | '%s'", apply_url[:70], title)
            return "external_apply", title

        except Exception as exc:
            log.warning("  [gob] Error navegando a %s: %s", offer_url[:60], exc)
            return f"error: {exc}", title

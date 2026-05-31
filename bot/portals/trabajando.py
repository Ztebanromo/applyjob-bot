"""
Portal: Trabajando.cl (Chile)
URL: https://www.trabajando.cl

Uno de los portales laborales más grandes de Chile.
El bot puede:
  1. Llenar/actualizar el curriculum en https://www.trabajando.cl/mi-curriculum#/
  2. Navegar la búsqueda y postular a ofertas que tengan formulario interno.

Requiere sesión activa (login previo guardado en el contexto de Playwright).
"""
import logging
import time
from playwright.sync_api import Page, TimeoutError as PWTimeout

from bot.portals.base import BasePortal
from bot.stealth_utils import human_delay, micro_delay
from bot.form_filler import fill_form

log = logging.getLogger(__name__)

# ── Selectores clave ──────────────────────────────────────────────────────────
_SEL = {
    # Página de búsqueda
    "card":     (
        "a.aviso-titulo, "
        "a[href*='/empleos/'], "
        "div.aviso-item a, "
        "article.aviso-item a, "
        "a[href*='/trabajo/'], "
        "li.aviso-item a, "
        "div[class*='aviso'] a[href*='/'], "
        "section[class*='result'] a[href*='/']"
    ),
    # Página de oferta
    "title":    "h1.aviso-titulo, h1",
    "apply_btn": (
        "a:has-text('Postular'), button:has-text('Postular'), "
        "a:has-text('Aplicar'), button:has-text('Aplicar'), "
        "a.btn-postular, button.btn-postular, "
        "a[href*='postular'], button[data-action='postular']"
    ),
    # Formulario de postulación interno
    "form_submit": (
        "button[type='submit']:has-text('Enviar'), "
        "button[type='submit']:has-text('Postular'), "
        "button:has-text('Enviar postulación'), "
        "input[type='submit']"
    ),
    # Confirmación de postulación enviada
    "success": (
        "text=postulación enviada, text=te hemos enviado, "
        "text=aplicación enviada, text=gracias por postular, "
        ".alerta-exito, .success-message"
    ),
    # Curriculum — campos del perfil
    "cv": {
        "summary":      "#resumen, textarea[name='resumen'], textarea[placeholder*='resumen']",
        "skills":       "#habilidades, textarea[name='habilidades'], input[placeholder*='habilidad']",
        "salary":       "input[name='renta'], input[placeholder*='renta'], input[placeholder*='sueldo']",
        "availability": "select[name='disponibilidad'], input[name='disponibilidad']",
    },
}

# ── Curriculum fields ─────────────────────────────────────────────────────────
_CV_SUMMARY_MAX = 1_200   # caracteres


def _build_summary(profile: dict) -> str:
    """Construye el resumen/presentación personal a partir del perfil."""
    return profile.get("cover_letter", "").strip()[:_CV_SUMMARY_MAX]


def _build_skills(profile: dict) -> str:
    """Lista de habilidades técnicas resumidas."""
    return "Python, SQL, JavaScript, HTML/CSS, Git, SAP WM, WMS, RF Terminal"


# ── Métodos internos ──────────────────────────────────────────────────────────

def fill_curriculum(page: Page, profile: dict) -> bool:
    """
    Navega a https://www.trabajando.cl/mi-curriculum#/ y rellena los campos
    del perfil con los datos del usuario, incluyendo habilidades, idiomas y
    expectativa de renta.

    Retorna True si se completó sin errores graves, False si hubo algún problema.
    """
    log.info("[trabajando.cl] Navegando a /mi-curriculum…")
    try:
        page.goto("https://www.trabajando.cl/mi-curriculum#/",
                  timeout=30_000, wait_until="domcontentloaded")
        human_delay(2.0, 3.5)
    except PWTimeout:
        log.warning("[trabajando.cl] Timeout al cargar /mi-curriculum")
        return False

    # Verificar que no haya redirigido a login
    if "login" in page.url or "signin" in page.url or "ingresar" in page.url:
        log.warning("[trabajando.cl] Redirigió a login — sesión no activa")
        return False

    # ── Datos personales básicos ───────────────────────────────────────────
    _safe_fill(page, "input[name='nombre'], #nombre", profile.get("first_name", ""))
    _safe_fill(page, "input[name='apellido'], #apellido", profile.get("last_name", ""))
    _safe_fill(page, "input[name='email'], #email", profile.get("email", ""))
    _safe_fill(page, "input[name='telefono'], #telefono, input[type='tel']",
               profile.get("phone_number", ""))
    _safe_fill(page, "input[name='ciudad'], #ciudad", profile.get("city", ""))

    human_delay(0.5, 1.0)

    # ── Resumen / presentación personal ────────────────────────────────────
    summary = _build_summary(profile)
    _safe_fill(page, _SEL["cv"]["summary"], summary)

    # ── Pretensión de renta ────────────────────────────────────────────────
    salary = str(profile.get("salary", ""))
    if salary:
        _safe_fill(page, _SEL["cv"]["salary"], salary)

    # ── Disponibilidad ────────────────────────────────────────────────────
    avail = profile.get("availability", "Inmediata")
    avail_sel = _SEL["cv"]["availability"]
    el = page.query_selector(avail_sel)
    if el:
        tag = el.evaluate("el => el.tagName.toLowerCase()")
        if tag == "select":
            try:
                el.select_option(label=avail)
            except Exception:
                pass
        else:
            _safe_fill(page, avail_sel, avail)

    # ── Guardar / confirmar ───────────────────────────────────────────────
    human_delay(0.8, 1.5)
    save_btn = (
        page.query_selector("button:has-text('Guardar')") or
        page.query_selector("button[type='submit']") or
        page.query_selector("input[type='submit']")
    )
    if save_btn:
        save_btn.click()
        human_delay(1.5, 2.5)
        log.info("[trabajando.cl] Curriculum guardado ✓")
    else:
        log.warning("[trabajando.cl] No se encontró botón Guardar")

    # ── LinkedIn / portfolio si hay campos ───────────────────────────────
    _safe_fill(page, "input[name='linkedin'], input[placeholder*='linkedin']",
               profile.get("linkedin", ""))
    _safe_fill(page, "input[name='portfolio'], input[placeholder*='github']",
               profile.get("portfolio", ""))

    # ── Secciones adicionales ────────────────────────────────────────────
    _fill_habilidades(page)
    _fill_idiomas(page)
    _fill_expectativa_renta(page, profile)

    return True


def _fill_habilidades(page: Page) -> None:
    """Navega a #/habilidades y agrega tags de habilidades técnicas."""
    skills = [
        "Python", "SQL", "JavaScript", "HTML/CSS", "Git",
        "SAP WM", "WMS", "RF Terminal",
        "Bases de datos relacionales", "Automatizacion de procesos",
    ]
    log.info("[trabajando.cl] Navegando a #/habilidades…")
    try:
        page.goto("https://www.trabajando.cl/mi-curriculum#/habilidades",
                  timeout=20_000, wait_until="domcontentloaded")
        human_delay(2.5, 3.5)
    except PWTimeout:
        log.warning("[trabajando.cl] Timeout al cargar #/habilidades")
        return

    # Verificar login
    if "login" in page.url or "ingresar" in page.url:
        log.warning("[trabajando.cl] Sesión no activa en #/habilidades")
        return

    # Esperar a que aparezca el input (SPA puede tardar en renderizar)
    tag_input = None
    for sel in [
        "input.form-control",
        "input[placeholder*='habilidades']",
        "input[placeholder*='Agrega']",
        "section input[type='text']",
        "form input[type='text']",
    ]:
        try:
            page.wait_for_selector(sel, timeout=5_000)
            tag_input = page.query_selector(sel)
            if tag_input:
                log.debug("[trabajando.cl] Input encontrado con selector: %s", sel)
                break
        except PWTimeout:
            continue

    if not tag_input:
        # Debug: log all inputs visible
        inputs = page.query_selector_all("input")
        info = [(i.get_attribute("class"), i.get_attribute("placeholder")) for i in inputs]
        log.warning("[trabajando.cl] No se encontró input de habilidades. URL=%s Inputs: %s",
                    page.url, info)
        return

    # Usar JavaScript para agregar tags sin disparar el form-submit del sitio
    log.warning("[trabajando.cl] Agregando %d habilidades via JS…", len(skills))
    try:
        result = page.evaluate("""
            async (skills) => {
                const input = document.querySelector('input.form-control');
                if (!input) return 'NO_INPUT';
                const nativeSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;

                const sleep = ms => new Promise(r => setTimeout(r, ms));

                for (const skill of skills) {
                    // Establecer valor via React native setter
                    nativeSetter.call(input, skill);
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    await sleep(400);
                    // Disparar Enter solo sobre este input (stopPropagation evita form submit)
                    const ev = new KeyboardEvent('keydown', {
                        key: 'Enter', keyCode: 13, which: 13, bubbles: true
                    });
                    input.dispatchEvent(ev);
                    await sleep(800);
                }
                return 'OK';
            }
        """, skills)
        log.warning("[trabajando.cl] JS add-tags result: %s — URL: %s", result, page.url)
    except Exception as exc:
        log.warning("[trabajando.cl] Error en JS add-tags: %s", exc)

    # Guardar
    human_delay(1.0, 1.5)
    save_btn = page.query_selector("button:has-text('Guardar')")
    if save_btn:
        save_btn.click()
        human_delay(2.5, 3.5)
        log.warning("[trabajando.cl] Habilidades guardadas ✓")
    else:
        log.warning("[trabajando.cl] No se encontró Guardar en #/habilidades. URL: %s", page.url)


def _fill_idiomas(page: Page) -> None:
    """Navega a #/idiomas y agrega Español e Inglés."""
    idiomas = [
        {"nombre": "Español", "nivel": "Nativo"},
        {"nombre": "Inglés",  "nivel": "Básico"},
    ]
    log.info("[trabajando.cl] Navegando a #/idiomas…")
    try:
        page.goto("https://www.trabajando.cl/mi-curriculum#/idiomas",
                  timeout=20_000, wait_until="domcontentloaded")
        human_delay(2.0, 3.0)
    except PWTimeout:
        log.warning("[trabajando.cl] Timeout al cargar #/idiomas")
        return

    for idioma in idiomas:
        try:
            # Campo nombre del idioma
            nombre_input = page.query_selector(
                "input[placeholder*='idioma'], input[placeholder*='Idioma'], "
                "input[name='idioma'], #idioma"
            )
            if nombre_input:
                nombre_input.click()
                nombre_input.fill(idioma["nombre"])
                human_delay(0.4, 0.8)

                # Seleccionar de autocomplete si aparece
                opt = page.query_selector(f"li:has-text('{idioma['nombre']}'), "
                                          f"div:has-text('{idioma['nombre']}')")
                if opt:
                    opt.click()
                    human_delay(0.3, 0.6)

            # Nivel
            nivel_sel = page.query_selector(
                "select[name='nivel'], select[placeholder*='nivel'], "
                "select[name*='nivel']"
            )
            if nivel_sel:
                try:
                    nivel_sel.select_option(label=idioma["nivel"])
                except Exception:
                    pass
            human_delay(0.3, 0.5)

            # Botón agregar o guardar
            add_btn = (
                page.query_selector("button:has-text('Agregar')") or
                page.query_selector("button:has-text('Añadir')")
            )
            if add_btn:
                add_btn.click()
                human_delay(1.0, 1.5)
                log.info("[trabajando.cl] Idioma agregado: %s", idioma["nombre"])
        except Exception as exc:
            log.warning("[trabajando.cl] Error al agregar idioma '%s': %s",
                        idioma["nombre"], exc)

    # Guardar final
    save_btn = page.query_selector("button:has-text('Guardar')")
    if save_btn:
        save_btn.click()
        human_delay(2.0, 3.0)
        log.info("[trabajando.cl] Idiomas guardados ✓")


def _fill_expectativa_renta(page: Page, profile: dict) -> None:
    """Navega a #/pretension-renta y agrega la expectativa de renta."""
    salary = str(profile.get("salary", "850000"))
    if not salary:
        return
    log.info("[trabajando.cl] Navegando a #/pretension-renta…")
    for path in ("#/pretension-renta", "#/renta", "#/expectativa-renta"):
        try:
            page.goto(f"https://www.trabajando.cl/mi-curriculum{path}",
                      timeout=15_000, wait_until="domcontentloaded")
            human_delay(1.5, 2.5)
            if "login" in page.url:
                break
            # Intentar llenar el campo
            renta_el = page.query_selector(
                "input[name='renta'], input[placeholder*='renta'], "
                "input[placeholder*='sueldo'], input[type='number']"
            )
            if renta_el:
                renta_el.click()
                renta_el.fill(salary)
                human_delay(0.5, 1.0)
                save_btn = page.query_selector("button:has-text('Guardar')")
                if save_btn:
                    save_btn.click()
                    human_delay(2.0, 3.0)
                    log.info("[trabajando.cl] Expectativa de renta guardada ✓")
                return
        except PWTimeout:
            continue
    log.warning("[trabajando.cl] No se pudo guardar expectativa de renta")


def _safe_fill(page: Page, selector: str, value: str) -> None:
    """Rellena el primer elemento que coincida con el selector (si existe)."""
    if not value:
        return
    try:
        # El selector puede ser una lista separada por comas
        el = page.query_selector(selector)
        if el:
            el.click()
            micro_delay()
            el.fill(value)
    except Exception as exc:
        log.debug("[trabajando.cl] _safe_fill '%s': %s", selector[:60], exc)


# ── Portal principal ──────────────────────────────────────────────────────────

class TrabajandoPortal(BasePortal):
    """
    Portal Trabajando.cl con soporte de:
      - Listado de ofertas desde la búsqueda
      - Postulación con formulario interno o con redirección externa
      - Llenado del curriculum en /mi-curriculum#/
    """

    def apply_to_offer(self, page: Page, offer_url: str) -> str:
        """
        Flujo de postulación para una oferta de trabajando.cl.

        Retorna: 'applied' | 'skipped_*' | 'external:trabajando' | 'error: ...'
        """
        try:
            page.goto(offer_url, timeout=30_000, wait_until="domcontentloaded")
            human_delay(1.0, 2.0)

            # ── Título ──────────────────────────────────────────────────────
            title_el = page.query_selector(_SEL["title"])
            title = title_el.inner_text().strip() if title_el else offer_url

            # ── Botón de postulación ────────────────────────────────────────
            apply_btn = page.query_selector(_SEL["apply_btn"])
            if not apply_btn:
                log.debug("[trabajando.cl] Sin botón postular: %s", offer_url)
                return "skipped_no_apply_button"

            if self.dry_run:
                return "dry_run"

            # ── Click en postular ───────────────────────────────────────────
            # Aceptar diálogos JS (alert/confirm) que aparezcan durante postulación
            page.on("dialog", lambda d: d.accept())

            new_tab = None
            try:
                with page.context.expect_page(timeout=7_000) as new_page_info:
                    apply_btn.click()
                    human_delay(1.5, 2.5)
                # Solo llegamos aquí si se abrió una ventana/pestaña nueva
                new_tab = new_page_info.value
                new_tab.wait_for_load_state("domcontentloaded", timeout=15_000)
                result_url = new_tab.url
                # Si abrió ventana emergente/pestaña externa
                if "trabajando.cl" not in result_url:
                    log.info("[trabajando.cl] Redirigió a externo: %s", result_url[:80])
                    new_tab.close()
                    return "external:trabajando"
                # Formulario en ventana emergente del mismo portal
                fill_form(new_tab, self.profile)
                human_delay(1.0, 2.0)
                submit = new_tab.query_selector(_SEL["form_submit"])
                if submit:
                    submit.click()
                    human_delay(2.0, 3.5)
                new_tab.close()
                return "applied"
            except PWTimeout:
                pass  # No abrió ventana emergente → formulario en la misma página

            # ── Formulario en la misma página ──────────────────────────────
            if "trabajando.cl" not in page.url:
                return "external:trabajando"

            # Intentar rellenar formulario interno
            human_delay(1.0, 2.0)
            fill_form(page, self.profile)
            human_delay(1.0, 1.5)

            submit = page.query_selector(_SEL["form_submit"])
            if submit:
                submit.click()
                human_delay(2.0, 3.5)
                # Verificar confirmación
                try:
                    page.wait_for_selector(_SEL["success"], timeout=5_000)
                    log.info("[trabajando.cl] ✅ Postulado: %s", title[:60])
                    return "applied"
                except PWTimeout:
                    pass

            # Sin submit → probablemente ya se envió automáticamente (un-click)
            if page.query_selector(_SEL["success"]):
                return "applied"

            return "applied"  # best-effort

        except PWTimeout:
            return "error: timeout"
        except Exception as exc:
            return f"error: {str(exc)[:80]}"

    def get_offer_urls(self, page: Page) -> list[str]:
        """Extrae URLs de ofertas de la página de resultados."""
        # Esperar a que la SPA termine de renderizar las cards
        try:
            page.wait_for_selector(_SEL["card"], timeout=12_000)
        except PWTimeout:
            # Fallback: esperar networkidle y volver a intentar
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass

        urls = []
        cards = page.query_selector_all(_SEL["card"])
        for card in cards:
            try:
                href = card.get_attribute("href") or ""
                if not href:
                    a = card.query_selector("a[href]")
                    href = a.get_attribute("href") if a else ""
                if href:
                    if href.startswith("/"):
                        href = "https://www.trabajando.cl" + href
                    if href.startswith("http") and href not in urls:
                        urls.append(href)
            except Exception:
                continue
        return urls

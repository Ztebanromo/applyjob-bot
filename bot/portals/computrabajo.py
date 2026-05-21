"""
Portal Computrabajo Chile (computrabajo.cl).

Flujo real de Computrabajo:
  1. Página de búsqueda -> lista de ofertas (article.box_offer)
  2. Click en oferta -> navega a página de detalle del empleo
  3. En detalle -> botón "Postularme" o "Postular"
     a. Click -> puede abrir formulario interno (nombre, email, CV, carta)
     b. O redirige al ATS externo del empleador
  4. Si hay formulario interno -> fill_form() + submit
  5. Si es externo -> registrar como external_apply

Observaciones:
  - Computrabajo no requiere cuenta para ver ofertas, pero SÍ para postular.
  - Al hacer click en "Postularme" sin sesión, muestra modal de login/registro.
  - Flujo sin cuenta: rellena datos como "invitado" en algunos casos.
  - La paginación usa `a[title='Siguiente']` o parámetros en la URL (?p=2).
  - Las ofertas pueden ser internas (formulario Computrabajo) o externas (ATS).
"""
import time
import logging
import re as _re

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from .base import BasePortal
from ..stealth_utils import human_delay, micro_delay, take_error_screenshot
from ..form_filler import fill_form
from ..config import schedule_ok, experience_ok, practica_ok, topic_ok

log = logging.getLogger("applyjob.computrabajo")

# -- Selectores ---------------------------------------------------------------
SEL = {
    # Cards de oferta en el listado de búsqueda
    # article.box_offer puede tener clases extra: "sel", "outstanding"
    "card":             "article.box_offer",
    "card_link":        "a.js-o-link, article.box_offer h2 a, article.box_offer a[href*='/ofertas-de-trabajo/']",

    # Título del puesto en la página de detalle
    "job_title":        "h1.title_offer, h1[class*='title'], h1",

    # Botón principal de postulación en la página de detalle
    "apply_btn": (
        "a.btn_postular, "
        "button.postular, "
        "a:has-text('Postularme'), "
        "a:has-text('Postular'), "
        "button:has-text('Postularme'), "
        "button:has-text('Postular'), "
        "a[class*='postul'], "
        "button[class*='postul']"
    ),

    # Página / modal de login (redirige a secure.computrabajo.com)
    "login_signal": (
        "input[type='email'], "
        "input[type='password'], "
        "form[action*='login'], "
        "form[action*='Login'], "
        "button:has-text('Iniciar sesión'), "
        "button:has-text('Ingresar')"
    ),

    # Modal de login que aparece si no hay sesión
    "login_modal": (
        "div.modal-login, "
        "div[class*='modal'][class*='login'], "
        "div#modal-registro, "
        "div.modal-register"
    ),

    # Formulario interno de postulación (sin ATS externo)
    "apply_form": (
        "form[action*='postula'], "
        "form[id*='postula'], "
        "div.form-postulacion, "
        "div[class*='apply-form']"
    ),

    # Campos del formulario interno
    "form_name":        "input[name='name'], input[name='nombre'], input[placeholder*='nombre']",
    "form_email":       "input[name='email'], input[type='email']",
    "form_phone":       "input[name='phone'], input[name='telefono'], input[placeholder*='teléfono']",
    "form_cv":          "input[type='file'][name*='cv'], input[type='file'][name*='curriculum']",
    "form_cover":       "textarea[name*='carta'], textarea[name*='cover'], textarea[placeholder*='carta']",

    # Submit del formulario
    "form_submit": (
        "button[type='submit'], "
        "input[type='submit'], "
        "button:has-text('Enviar'), "
        "button:has-text('Postular'), "
        "button:has-text('Aplicar')"
    ),

    # Confirmación de éxito
    "success_signal": (
        "div:has-text('postulación enviada'), "
        "div:has-text('Te has postulado'), "
        "div:has-text('Postulación exitosa'), "
        "h2:has-text('¡Listo!'), "
        "div:has-text('Tu CV fue enviado'), "
        "div.alert-success, "
        "div[class*='success']"
    ),

    # Paginación
    "next_page":        "a[title='Siguiente'], a[rel='next'], a.next",
}

# Palabras en título que indican oferta TI (para filtrar irrelevantes)
_TECH_WORDS = {
    "desarrollador", "programador", "developer", "software",
    "fullstack", "frontend", "backend", "devops",
    "python", "java", "react", "angular", "node",
    "analista", "sistemas", "informática", "ti ", " ti,",
    "soporte ti", "data", "base de datos", "qa ", "tester",
    "cloud", "aws", "azure", "seguridad informática",
    # Logística / Bodega
    "bodega", "logística", "logistica", "operario", "operador", "picking",
}

MAX_FORM_STEPS = 3      # máximo pasos de formulario interno
DETAIL_TIMEOUT = 25_000  # ms para cargar página de detalle


class ComputrabajoPortal(BasePortal):
    """
    Controlador para Computrabajo Chile.

    Estrategia de postulación:
      1. Primera oferta: detectar si hay sesión; si no, esperar login manual.
      2. Extraer URLs del listado con get_offer_urls().
      3. Para cada oferta: navegar, detectar tipo (interna/externa), postular.
      4. Formularios internos -> fill_form() + submit.
      5. Postulaciones externas -> registrar como external_apply.
    """

    _login_done: bool = False  # flag de sesión — solo pide login una vez por run

    def _wait_for_login(self, page: Page, timeout_s: int = 300) -> bool:
        """
        Detecta si el bot fue redirigido a la página de login de Computrabajo
        y espera hasta que el usuario inicie sesión manualmente.
        Retorna True si el login se completó antes del timeout.
        """
        import time as _t

        def _on_login_page() -> bool:
            try:
                url = page.url
                return "login" in url.lower() or "account" in url.lower() or "secure.computrabajo" in url
            except Exception:
                return False

        def _logged_in() -> bool:
            try:
                url = page.url
                return "computrabajo.com" in url and "login" not in url.lower() and "account" not in url.lower()
            except Exception:
                return False

        if not _on_login_page():
            return True  # ya hay sesión

        log.warning("=" * 60)
        log.warning("COMPUTRABAJO requiere inicio de sesión.")
        log.warning("Por favor, inicia sesión en la ventana de Chrome.")
        log.warning("El bot esperará hasta %d segundos...", timeout_s)
        log.warning("=" * 60)

        deadline = _t.time() + timeout_s
        while _t.time() < deadline:
            _t.sleep(3)
            if _logged_in():
                log.info("Sesión de Computrabajo detectada. Continuando.")
                ComputrabajoPortal._login_done = True
                human_delay(2.0, 3.0)
                return True

        log.error("Tiempo de espera agotado esperando login en Computrabajo. Abortando.")
        return False

    def _title_is_tech(self, title: str) -> bool:
        """Filtra si el título corresponde a un puesto de TI."""
        tl = title.lower()
        return any(w in tl for w in _TECH_WORDS)

    def get_offer_urls(self, page: Page) -> list[str]:
        """
        Extrae URLs de ofertas de la página de búsqueda actual.
        Soporta múltiples selectores como fallback.
        """
        seen: set[str] = set()
        urls: list[str] = []

        try:
            # Esperar a que carguen las cards
            try:
                page.wait_for_selector(SEL["card"], timeout=10_000)
            except PlaywrightTimeout:
                log.warning("ComputrabajoPortal: tiempo de espera agotado esperando tarjetas de oferta. Se intentará continuar.")

            # Estrategia 1: links dentro de las cards
            cards = page.query_selector_all(SEL["card"])
            log.debug("Computrabajo: %d cards encontradas", len(cards))

            for card in cards:
                try:
                    # Intentar con el selector específico primero
                    link = card.query_selector(SEL["card_link"].split(",")[0].strip())
                    if not link:
                        link = card.query_selector("a[href]")
                    if not link:
                        continue

                    href = link.get_attribute("href") or ""
                    if not href:
                        continue
                    if not href.startswith("http"):
                        href = "https://cl.computrabajo.com" + href

                    # Filtrar por URL que parezca oferta de empleo
                    if "/ofertas-de-trabajo/" not in href and "/empleo-" not in href:
                        continue

                    # Extraer título para filtrar solo TI
                    title = ""
                    try:
                        title_el = card.query_selector("h2, h3, .title_offer, [class*='title']")
                        if title_el:
                            title = (title_el.text_content() or "").strip()
                    except Exception:
                        pass

                    # Texto completo del card para filtros
                    card_text = ""
                    try:
                        card_text = (card.text_content() or "")[:500]
                    except Exception:
                        pass

                    # Filtro 1: horario (lunes a viernes AM — no noche/finde)
                    if not schedule_ok(card_text):
                        log.info("  [ct] Descartado (horario): %s", card_text[:80].strip())
                        continue

                    # Filtro 2: experiencia (solo junior / sin experiencia)
                    check_text = (title + " " + card_text).strip()
                    if not experience_ok(check_text):
                        log.info("  [ct] Descartado (senior/exp): %s", (title or card_text[:60]).strip())
                        continue

                    # Si no hay título o es de TI, incluir
                    if not title or self._title_is_tech(title):
                        if href not in seen:
                            seen.add(href)
                            urls.append(href)

                except Exception as exc:
                    log.debug("Error extrayendo URL de card: %s", exc)
                    continue

            # Estrategia 2 (fallback): todos los links de la página que sean ofertas
            if not urls:
                log.warning("Computrabajo: fallback a links generales de la página")
                all_links = page.query_selector_all("a[href*='/ofertas-de-trabajo/']")
                for link in all_links:
                    try:
                        href = link.get_attribute("href") or ""
                        if href and href not in seen:
                            if not href.startswith("http"):
                                href = "https://cl.computrabajo.com" + href
                            seen.add(href)
                            urls.append(href)
                    except Exception:
                        continue

        except Exception as exc:
            log.warning("ComputrabajoPortal.get_offer_urls error: %s", exc)

        log.debug("Computrabajo: %d URLs de ofertas extraídas", len(urls))
        return urls

    def apply_to_offer(self, page: Page, offer_url: str) -> tuple[str, str]:
        """
        Flujo completo de postulación para una oferta de Computrabajo.

        Returns:
            (status, title) donde status es: applied | external_apply |
            skipped_login_required | error: {msg}
        """
        title = "unknown"

        try:
            # -- 1. Navegar a la oferta ------------------------------------
            log.debug("  [ct] Navegando a: %s", offer_url[:70])
            page.goto(offer_url, wait_until="domcontentloaded", timeout=DETAIL_TIMEOUT)
            human_delay(2.0, 3.5)

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

            log.debug("  [ct] Título: %s", title)

            # -- 2b. Filtros sobre descripción real -------------------------
            try:
                desc_text = page.evaluate(
                    "() => {"
                    "  const d = document.querySelector("
                    "    'div.offer_description, div[class*=\"description\"],"
                    "     section.offer_description, article');"
                    "  return d ? d.innerText : document.body?.innerText?.slice(0,1000) || '';"
                    "}"
                ) or ""
                full_text = title + " " + desc_text
                if not practica_ok(full_text):
                    log.info("  [ct] Descartada (práctica/pasantía): '%s'", title)
                    return "skipped_practica", title
                if not schedule_ok(full_text):
                    log.info("  [ct] Descartada (horario): '%s'", title)
                    return "skipped_schedule", title
                if not experience_ok(full_text):
                    log.info("  [ct] Descartada (senior/experiencia): '%s'", title)
                    return "skipped_experience", title
                if not topic_ok(full_text):
                    log.info("  [ct] Descartada (fuera de rubro IT/bodega): '%s'", title)
                    return "skipped_topic", title
            except Exception:
                pass

            # -- 3. Buscar botón de postulación ----------------------------
            apply_btn = self._find_visible(page, SEL["apply_btn"])
            if not apply_btn:
                # Intentar scroll hacia abajo — el botón puede estar off-screen
                page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.4)")
                human_delay(1.0, 1.5)
                apply_btn = self._find_visible(page, SEL["apply_btn"])

            if not apply_btn:
                log.warning("  [ct] Botón de postulación no encontrado en %s", offer_url[:60])
                take_error_screenshot(page, "computrabajo", "no_apply_btn")
                return "error: no_apply_button", title

            # -- 4. Click en postular --------------------------------------
            if self.dry_run:
                log.info("  [ct] dry_run — no se hace click en Postular")
                return "dry_run", title

            try:
                apply_btn.scroll_into_view_if_needed()
                micro_delay()
                apply_btn.click()
                human_delay(2.5, 4.0)
            except Exception as exc:
                log.warning("  [ct] Click en botón falló: %s", exc)
                return f"error: click_apply {exc}", title

            # -- 5. Detectar resultado del click ---------------------------

            # 5a. ¿Redirigió al login de Computrabajo?
            if "login" in page.url.lower() or "account" in page.url.lower() or "secure.computrabajo" in page.url:
                if not ComputrabajoPortal._login_done:
                    # Primera vez: esperar login manual del usuario
                    login_ok = self._wait_for_login(page, timeout_s=300)
                    if not login_ok:
                        return "skipped_login_required", title
                    # Volver a la oferta y reintentar
                    page.goto(offer_url, wait_until="domcontentloaded", timeout=DETAIL_TIMEOUT)
                    human_delay(2.0, 3.0)
                    apply_btn2 = self._find_visible(page, SEL["apply_btn"])
                    if apply_btn2:
                        apply_btn2.click()
                        human_delay(2.5, 4.0)
                    else:
                        return "error: no_apply_button_after_login", title
                else:
                    # Ya debería tener sesión — algo falló
                    log.warning("  [ct] Redirigido a login con sesión activa — sesión expirada?")
                    ComputrabajoPortal._login_done = False
                    return "skipped_login_required", title

            # 5b. ¿Ya mostró éxito directo?
            if self._check_success(page):
                log.info("  [ct] [OK] Postulación exitosa directa")
                return "applied", title

            current_url = page.url

            # 5c. ¿Redirigió al formulario interno de candidato.cl.computrabajo.com?
            #     postapply = formulario con CV adjunto
            #     kq        = formulario rápido (Quick Apply)
            if "candidato.cl.computrabajo.com" in current_url:
                log.info("  [ct] Formulario interno Computrabajo: %s", current_url[:70])
                return self._fill_candidato_form(page, title)

            # 5d. ¿Hay formulario interno en la misma página?
            if self._is_visible(page, SEL["apply_form"]):
                return self._fill_internal_form(page, title, offer_url)

            # 5e. ¿Se redirigió a dominio externo real (ATS de tercero)?
            if "computrabajo" not in current_url:
                log.info("  [ct] ATS externo: %s", current_url[:70])
                return "external_apply", title

            # 5f. Esperar un poco más y verificar éxito
            human_delay(2.0, 3.0)
            if self._check_success(page):
                return "applied", title

            # 5g. Formulario genérico con form_filler
            return self._try_generic_form(page, title, offer_url)

        except Exception as exc:
            log.error("  [ct] Error en apply_to_offer: %s", exc)
            try:
                take_error_screenshot(page, "computrabajo", "exception")
            except Exception:
                pass
            return f"error: {type(exc).__name__}", title

    # -- Helpers privados -----------------------------------------------------

    def _fill_candidato_form(self, page: Page, title: str) -> tuple[str, str]:
        """
        Maneja candidato.cl.computrabajo.com/candidate/postapply y /candidate/kq.
        Estos son los formularios internos de Computrabajo cuando el usuario ya
        tiene sesión iniciada. Rellena datos faltantes y hace submit.
        """
        human_delay(2.0, 3.0)

        # Verificar éxito inmediato (postapply puede confirmar al instante)
        if self._check_success(page):
            log.info("  [ct] [OK] Postulación enviada (postapply inmediato)")
            return "applied", title

        # Rellenar con form_filler genérico
        try:
            fill_form(page, self.profile)
        except Exception as exc:
            log.debug("  [ct] fill_form en candidato form: %s", exc)

        # Adjuntar CV si hay input file
        try:
            cv_input = page.query_selector("input[type='file']")
            cv_path = self.profile.get("cv_path", "")
            if cv_input and cv_path:
                import os
                if os.path.exists(cv_path):
                    cv_input.set_input_files(cv_path)
                    log.debug("  [ct] CV adjuntado en candidato form")
        except Exception as exc:
            log.debug("  [ct] CV upload error: %s", exc)

        human_delay(1.0, 2.0)

        # Buscar y hacer click en submit
        submitted = False
        for sel in SEL["form_submit"].split(","):
            sel = sel.strip()
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible() and btn.is_enabled():
                    btn.click()
                    human_delay(2.5, 4.0)
                    submitted = True
                    log.debug("  [ct] Submit clickeado en candidato form")
                    break
            except Exception:
                continue

        if not submitted:
            # Algunos formularios kq se auto-envían sin botón explícito
            log.debug("  [ct] Sin botón submit visible — asumiendo auto-submit")

        # Verificar éxito post-submit
        human_delay(1.5, 2.5)
        if self._check_success(page):
            log.info("  [ct] [OK] Postulación confirmada en candidato form")
            return "applied", title

        # Sin confirmación visual pero el formulario se procesó
        log.info("  [ct] Formulario candidato procesado (sin confirmación visual)")
        return "applied", title

    def _fill_internal_form(self, page: Page, title: str, offer_url: str) -> tuple[str, str]:
        """
        Rellena el formulario interno de postulación de Computrabajo.
        Intenta hasta MAX_FORM_STEPS pasos.
        """
        log.info("  [ct] Formulario interno detectado — rellenando")
        profile = self.profile

        for step in range(MAX_FORM_STEPS):
            human_delay(1.0, 2.0)

            filled = 0

            # Nombre
            try:
                el = page.query_selector(SEL["form_name"])
                if el and el.is_visible() and not el.input_value():
                    el.fill(profile.get("full_name", ""))
                    filled += 1
            except Exception:
                pass

            # Email
            try:
                el = page.query_selector(SEL["form_email"])
                if el and el.is_visible() and not el.input_value():
                    el.fill(profile.get("email", ""))
                    filled += 1
            except Exception:
                pass

            # Teléfono
            try:
                el = page.query_selector(SEL["form_phone"])
                if el and el.is_visible() and not el.input_value():
                    el.fill(profile.get("phone", ""))
                    filled += 1
            except Exception:
                pass

            # CV
            try:
                cv_input = page.query_selector(SEL["form_cv"])
                cv_path = profile.get("cv_path", "")
                if cv_input and cv_path:
                    import os
                    if os.path.exists(cv_path):
                        cv_input.set_input_files(cv_path)
                        filled += 1
                        log.debug("  [ct] CV adjuntado")
            except Exception as exc:
                log.debug("  [ct] No se pudo adjuntar CV: %s", exc)

            # Carta de presentación
            try:
                el = page.query_selector(SEL["form_cover"])
                if el and el.is_visible() and not el.input_value():
                    el.fill(profile.get("cover_letter", ""))
                    filled += 1
            except Exception:
                pass

            # Usar fill_form genérico para campos restantes
            try:
                fill_form(page, profile)
            except Exception as exc:
                log.debug("  [ct] fill_form error en step %d: %s", step, exc)

            log.debug("  [ct] Formulario paso %d — %d campos rellenados", step + 1, filled)

            # Buscar botón de submit/siguiente
            submitted = False
            for sel in SEL["form_submit"].split(","):
                sel = sel.strip()
                try:
                    btn = page.query_selector(sel)
                    if btn and btn.is_visible() and btn.is_enabled():
                        btn.click()
                        human_delay(2.0, 3.5)
                        submitted = True
                        break
                except Exception:
                    continue

            if not submitted:
                log.debug("  [ct] No se encontró botón de submit en paso %d", step + 1)
                break

            if self._check_success(page):
                log.info("  [ct] [OK] Formulario enviado exitosamente (paso %d)", step + 1)
                return "applied", title

        # Verificación final
        if self._check_success(page):
            return "applied", title

        take_error_screenshot(page, "computrabajo", "form_incomplete")
        log.warning("  [ct] Formulario no completado — marcando como applied (sin confirmación visual)")
        return "applied", title  # Optimista — el form se intentó rellenar y enviar

    def _try_generic_form(self, page: Page, title: str, offer_url: str) -> tuple[str, str]:
        """
        Intento con fill_form genérico cuando no se detecta estructura específica.
        """
        try:
            fill_form(page, self.profile)
            human_delay(1.0, 2.0)

            # Buscar cualquier botón de submit
            for sel in SEL["form_submit"].split(","):
                sel = sel.strip()
                try:
                    btn = page.query_selector(sel)
                    if btn and btn.is_visible() and btn.is_enabled():
                        btn.click()
                        human_delay(2.0, 3.0)
                        if self._check_success(page):
                            return "applied", title
                        break
                except Exception:
                    continue
        except Exception as exc:
            log.debug("  [ct] _try_generic_form error: %s", exc)

        # Si llegamos aquí no sabemos si se envió o no
        log.warning("  [ct] No se pudo confirmar el envío — marcando external_apply")
        return "external_apply", title

    def _find_visible(self, page: Page, selector: str):
        """Retorna el primer elemento visible del selector compuesto, o None."""
        for sel_part in selector.split(","):
            sel_part = sel_part.strip()
            try:
                els = page.query_selector_all(sel_part)
                for el in els:
                    if el.is_visible():
                        return el
            except Exception:
                continue
        return None

    def _is_visible(self, page: Page, selector: str) -> bool:
        """Retorna True si algún elemento del selector es visible."""
        return self._find_visible(page, selector) is not None

    def _check_success(self, page: Page) -> bool:
        """Verifica señales visuales de postulación exitosa."""
        return self._is_visible(page, SEL["success_signal"])

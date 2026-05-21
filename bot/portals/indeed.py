"""
Portal específico para Indeed Chile.

Flujo real de cl.indeed.com:
  1. Página de búsqueda -> lista de tarjetas de trabajo
  2. Click en tarjeta -> panel derecho muestra detalles (no navega a nueva página)
  3. Si es "Solicitud sencilla de Indeed" -> botón indeedApplyButton -> overlay de aplicación
  4. Si es "Aplicar en el sitio" -> link externo -> se registra como external_skipped
  5. Paginación: a[data-testid='pagination-page-next']

Observaciones:
  - Navegar directamente a /viewjob?jk=... puede activar Cloudflare
  - El flujo correcto es SIEMPRE desde la página de búsqueda (tiene cookies/sesión)
  - data-jk identifica de forma única cada oferta
"""
import logging
import time
import random

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from .base import BasePortal
from ..stealth_utils import human_delay, micro_delay, take_error_screenshot
from ..form_filler import fill_form
from ..config import schedule_ok, experience_ok, practica_ok, topic_ok

log = logging.getLogger("applyjob.indeed")

# -- Selectores --------------------------------------------------------------
SEL = {
    # Cards de oferta en la lista de búsqueda
    "card":              "div.job_seen_beacon",
    "card_title_link":   "h2.jobTitle a[data-jk], a.jcs-JobTitle[data-jk]",

    # Panel de detalle (lado derecho) — prueba varios contenedores
    "detail_panel":      (
        "#viewJobSSRRoot, "
        ".jobsearch-ViewJobLayout--embedded, "
        "#mosaic-afterAd, "
        ".jobsearch-RightPane, "
        "#jobDescriptionText, "
        ".jobDetailSectionWrapper"
    ),

    # Botón "Solicitud sencilla de Indeed" (Easy Apply)
    "easy_apply_btn":    (
        "button#indeedApplyButton, "
        "span.indeed-apply-button button, "
        ".ia-IndeedApplyButton, "
        "button[class*='indeedApply'], "
        "button[data-testid='indeedApplyButton']"
    ),

    # Botón / link de "Aplicar en el sitio del empleador" (externo)
    # Indeed Chile puede renderizar cualquiera de estos selectores según la versión
    "external_apply":    (
        "a.sl_apply_button, "
        "a[data-testid='external-apply-link'], "
        "a[href*='clk?jk='][target='_blank'], "
        "a[href*='/rc/clk'][target='_blank'], "
        "a[href*='indeed.com/applystart'], "
        "a[data-testid='applyButton'], "
        "a[class*='apply'][href], "
        "a[id*='apply'], "
        "button[data-testid='applyButton'], "
        "a[target='_blank'][href*='apply']"
    ),

    # Overlay / modal de Easy Apply
    "apply_overlay":     "div.ia-ApplyFormTop, div[class*='ia-'], #ia-container",

    # Inputs dentro del overlay de Easy Apply
    "overlay_next":      (
        "button[type='submit'], "
        "button:has-text('Continue'), button:has-text('Next'), "
        "button:has-text('Submit'), button:has-text('Aplicar')"
    ),

    # Confirmación de aplicación enviada
    "apply_success":     (
        "div.ia-PostApply, "
        "div[class*='PostApply'], "
        "h1:has-text('application was sent'), "
        "div:has-text('Tu solicitud fue enviada'), "
        "div:has-text('application submitted')"
    ),

    # Título de la oferta en el panel de detalle
    "job_title":         "h2.jobsearch-JobInfoHeader-title, h1.jobsearch-JobInfoHeader-title",
}

PANEL_WAIT_MS = 12_000   # ms a esperar por el panel de detalle
OVERLAY_WAIT_MS = 5_000  # ms a esperar por el overlay de Easy Apply


class IndeedPortal(BasePortal):
    """
    Maneja Indeed Chile usando el flujo de panel lateral.
    No navega a /viewjob directamente para evitar Cloudflare.
    """

    def _wait_cloudflare(self, page: Page, timeout_s: int = 150) -> bool:
        """
        Detecta el desafío de Cloudflare y espera hasta que el usuario lo resuelva.
        Retorna True si se resolvió, False si expiró el timeout.
        """
        import time as _time

        def _is_cloudflare() -> bool:
            try:
                # Buscar señales de Cloudflare en el título o cuerpo
                title = page.title().lower()
                txt = page.evaluate("document.body.innerText") or ""
                return "verificación adicional" in txt.lower() or \
                       "verifique que es un ser humano" in txt.lower() or \
                       "cf_chl" in page.url or \
                       "just a moment" in title or \
                       "cloudflare" in txt.lower()
            except Exception:
                return False

        def _has_jobs() -> bool:
            try:
                return len(page.query_selector_all(SEL["card"])) > 0
            except Exception:
                return False

        if not _is_cloudflare():
            return True  # sin desafío

        log.warning("=" * 60)
        log.warning("CLOUDFLARE CHALLENGE detectado en Indeed")
        # SEÑAL PARA EL DASHBOARD
        print("\n[CAPTCHA] Verificación humana requerida. Resuelve el CAPTCHA en el navegador abierto.")
        
        log.warning("Por favor, marca la casilla 'Verifique que es un ser humano'")
        log.warning("en el navegador abierto. El bot esperará hasta %d segundos...", timeout_s)
        log.warning("=" * 60)

        deadline = _time.time() + timeout_s
        last_notif = 0
        while _time.time() < deadline:
            _time.sleep(3)
            
            # Notificar cada 15s al dashboard para mantener la alerta viva
            if _time.time() - last_notif > 15:
                print("[CAPTCHA] Esperando resolución manual.")
                last_notif = _time.time()

            if not _is_cloudflare() or _has_jobs():
                log.info("Cloudflare resuelto. Continuando.")
                print("\n[SESION_INICIADA] Verificación de Indeed resuelta. Continuando.")
                human_delay(2.0, 3.0)
                return True

        log.error("Tiempo de espera agotado esperando Cloudflare. Abortando Indeed.")
        print("\n[FALLO] Indeed: tiempo de espera agotado esperando la resolución del CAPTCHA.")
        return False


    def get_offer_urls(self, page: Page) -> list[str]:
        """
        Extrae job keys (data-jk) de todas las tarjetas en la página actual.
        Retorna una lista de job keys únicos (no URLs completas).
        """
        # Verificar y esperar si hay desafío de Cloudflare
        if not self._wait_cloudflare(page):
            return []

        seen = set()
        keys = []
        try:
            # Verificar que la página sigue activa tras CF
            try:
                _ = page.url
            except Exception:
                log.warning("  [indeed] Contexto destruido post-CF. Recargando URL...")
                try:
                    page.reload(wait_until="domcontentloaded", timeout=20_000)
                    human_delay(3.0, 5.0)
                except Exception as reload_err:
                    log.error("  [indeed] No se pudo recargar: %s", reload_err)
                    return []

            # Scroll inicial para simular lectura humana y evadir Cloudflare
            from ..stealth_utils import human_scroll
            human_scroll(page, steps=random.randint(2, 4))
            human_delay(1.5, 3.0)

            cards = page.query_selector_all(SEL["card"])
            log.debug("IndeedPortal: %d cards encontradas", len(cards))
            skipped_schedule = 0
            for card in cards:
                try:
                    link = card.query_selector(SEL["card_title_link"])
                    if not link:
                        link = card.query_selector("a[data-jk]")
                    if not link:
                        continue
                    jk = link.get_attribute("data-jk") or ""
                    if not jk or jk in seen:
                        continue

                    # Leer texto del card (título + snippet)
                    try:
                        card_text = (card.text_content() or "")
                    except Exception:
                        card_text = ""

                    # Filtro de horario: descartar turno noche/finde/rotativo
                    if not schedule_ok(card_text):
                        log.info("  [indeed] Descartado por horario: %s",
                                 card_text[:80].strip().replace("\n", " "))
                        skipped_schedule += 1
                        continue

                    # Filtro de experiencia: descartar senior / 2+ años
                    if not experience_ok(card_text):
                        log.info("  [indeed] Descartado (senior/exp): %s",
                                 card_text[:80].strip().replace("\n", " "))
                        skipped_schedule += 1
                        continue

                    seen.add(jk)
                    keys.append(jk)
                except Exception as exc:
                    log.debug("Error extrayendo jk de card: %s", exc)
                    continue
            if skipped_schedule:
                log.info("  [indeed] %d ofertas descartadas por horario (noche/rotativo/fin de semana)", skipped_schedule)
                print(f"  [FILTRO] {skipped_schedule} ofertas descartadas por turno incompatible (noche/finde)")
        except Exception as exc:
            log.warning("get_offer_urls error: %s", exc)
        return keys

    def get_job_url(self, page: Page, offer_id: str) -> str:
        """Retorna una URL canónica para deduplicación en la DB."""
        return f"https://cl.indeed.com/viewjob?jk={offer_id}"

    def apply_to_offer(self, page: Page, offer_id: str) -> tuple[str, str]:
        """
        1. Hace click en la tarjeta del job para abrir el panel de detalle.
        2. Detecta si es Easy Apply o externo.
        3. Para Easy Apply: rellena el overlay y envía.
        4. Para externo: registra como external_skipped.

        Returns:
            (status, title)
        """
        title = "unknown"

        # -- 1. Click en la tarjeta -----------------------------------------
        # Retorna (clicked, used_direct_navigation)
        clicked, used_direct = self._click_card_robust(page, offer_id)
        if not clicked:
            return "error: card_not_found", title

        human_delay(2.0, 3.5)

        # -- 2. Esperar contenido -------------------------------------------
        # Si usamos Capa 3 (navegación directa a /viewjob), la página es
        # standalone: no hay panel lateral, el contenido está en la página completa.
        # Los selectores del panel lateral no existen ahí -> usar selectores standalone.
        if used_direct:
            panel_loaded = self._wait_for_standalone(page)
            if not panel_loaded:
                log.warning("  [indeed] Standalone page no cargó para jk=%s", offer_id)
                take_error_screenshot(page, "indeed", f"no_standalone_{offer_id}")
                return "error: panel_timeout", title
        else:
            panel_loaded = self._wait_for_panel(page)
            if not panel_loaded:
                log.warning("  [indeed] Panel no cargó — scroll suave y reintento")
                try:
                    page.evaluate("window.scrollBy(0, 200)")
                    human_delay(1.5, 2.5)
                    panel_loaded = self._wait_for_panel(page)
                except Exception:
                    pass
            if not panel_loaded:
                log.warning("  [indeed] Panel de detalle no cargó para jk=%s", offer_id)
                take_error_screenshot(page, "indeed", f"no_panel_{offer_id}")
                return "error: panel_timeout", title

        # Extraer título (funciona igual en panel y standalone)
        try:
            for title_sel in [
                SEL["job_title"],
                "h1.jobsearch-JobInfoHeader-title",
                "h1[class*='jobTitle']",
                "h1",
            ]:
                title_el = page.query_selector(title_sel)
                if title_el:
                    t = (title_el.text_content() or "").strip()[:80]
                    if t:
                        title = t
                        break
        except Exception:
            pass

        log.debug("  [indeed] Panel cargado | título: %s", title)

        # -- 2b. Filtro de horario en descripción completa ------------------
        try:
            desc_text = page.evaluate(
                "() => {"
                "  const p = document.querySelector('#jobDescriptionText, .jobsearch-jobDescriptionText, [id*=\"jobDescription\"]');"
                "  return p ? p.innerText : '';"
                "}"
            ) or ""
        except Exception:
            desc_text = ""

        full_text = title + " " + desc_text
        if not practica_ok(full_text):
            log.info("  [indeed] Descartada (práctica/pasantía): '%s'", title)
            return "skipped_practica", title
        if not schedule_ok(full_text):
            log.info("  [indeed] Oferta descartada por horario: '%s'", title)
            print(f"  [FILTRO] Descartada por turno incompatible: {title}")
            return "skipped_schedule", title
        if not experience_ok(full_text):
            log.info("  [indeed] Oferta descartada (senior/experiencia): '%s'", title)
            print(f"  [FILTRO] Descartada por nivel/experiencia: {title}")
            return "skipped_experience", title
        if not topic_ok(full_text):
            log.info("  [indeed] Descartada (fuera de rubro IT/bodega): '%s'", title)
            return "skipped_topic", title

        # -- 3. Detectar tipo de aplicación --------------------------------
        # Primero verificar si el Easy Apply está dentro de un iframe
        easy_btn = self._find_easy_apply_in_iframe(page)
        if easy_btn == "iframe_applied":
            return "applied", title   # ya se aplicó dentro del iframe
        # Si no hay iframe, buscar botón en la página principal
        if easy_btn is None:
            easy_btn = self._find_visible(page, SEL["easy_apply_btn"])

        if easy_btn:
            return self._apply_easy(page, easy_btn, title)
        else:
            # Verificar si hay botón externo (CSS selectors)
            ext_btn = self._find_visible(page, SEL["external_apply"])
            if ext_btn:
                ext_url = ext_btn.get_attribute("href") or "unknown"
                log.info("  [indeed] Postulación externa: %s", ext_url[:60])
                return "external_skipped", title

            # Fallback JS: buscar cualquier elemento con texto "Aplicar" o "Apply"
            # que apunte fuera de indeed o tenga href (para external jobs)
            ext_url = self._find_external_apply_js(page)
            if ext_url is not None:
                log.info("  [indeed] Postulación externa (JS fallback): %s", str(ext_url)[:60])
                return "external_skipped", title

            # Último recurso: si hay texto "Aplicar en el sitio" visible en el panel,
            # es un trabajo externo aunque no podamos encontrar el botón exacto
            panel_text = ""
            try:
                panel_text = page.evaluate(
                    "() => { "
                    "  const selectors = ["
                    "    '#viewJobSSRRoot', '.jobsearch-RightPane', '#mosaic-afterAd',"
                    "    '.jobDetailSectionWrapper', '#jobDescriptionText',"
                    "    '[data-testid=\"jobsearch-JobComponent-description\"]',"
                    "    '.job-details-jobs-unified-top-card__job-insight'"
                    "  ];"
                    "  for (const s of selectors) {"
                    "    const el = document.querySelector(s);"
                    "    if (el && el.innerText) return el.innerText.toLowerCase();"
                    "  }"
                    "  return document.body?.innerText?.toLowerCase()?.slice(0, 800) || '';"
                    "}"
                ) or ""
            except Exception:
                pass

            external_phrases = [
                "aplicar en el sitio", "apply on company site",
                "apply on employer site", "aplicar en empresa",
                "ver oferta completa", "ver en sitio",
                "apply externally", "solicitar empleo",
            ]
            if any(ph in panel_text for ph in external_phrases):
                log.info("  [indeed] Postulación externa detectada por texto del panel")
                return "external_skipped", title

            # No hay botón de ningún tipo visible — loguear panel para diagnóstico
            take_error_screenshot(page, "indeed", f"no_apply_btn_{offer_id}")
            log.warning(
                "  [indeed] no_apply_button para jk=%s | título='%s' | panel_text (300c): %s",
                offer_id, title,
                (panel_text[:300] if panel_text else "(panel vacío — selector no encontró nada)")
            )
            # Intentar leer el texto completo de la página como último recurso
            try:
                full_body = page.evaluate("() => document.body?.innerText?.slice(0, 500) || ''") or ""
                if any(ph in full_body.lower() for ph in [
                    "aplicar", "apply", "postular", "solicitar",
                    "aplicar en el sitio", "employer site"
                ]):
                    log.warning("  [indeed] Texto de aplicación encontrado en body — marcando external_skipped")
                    return "external_skipped", title
            except Exception:
                pass
            return "error: no_apply_button", title

    # ------------------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------------------

    def _click_card_robust(self, page: Page, jk: str) -> tuple[bool, bool]:
        """
        Hace click en el card de Indeed de forma robusta contra virtual scrolling.
        Retorna (clicked, used_direct_navigation).

        Indeed elimina del DOM los cards que salen del viewport (virtual list).
        Estrategia de 3 capas:
          1. Scroll al top del contenedor -> re-query -> click JS (sin scroll_into_view).
          2. Scroll progresivo 400px × 6 buscando el card en el DOM.
          3. Volver a la URL de búsqueda y reintentar desde cero.
          NO navegamos a /viewjob directamente porque esa URL dispara Cloudflare
          y el panel lateral no existe en la vista standalone.
        """
        RESULTS_CONTAINER = "#mosaic-provider-jobcards, .jobsearch-ResultsList, ul.css-zu9cdh"

        # Guardar URL de búsqueda para poder volver
        try:
            listing_url = page.url
        except Exception:
            listing_url = ""

        # -- Capa 1: scroll al inicio del contenedor y re-query -------------
        try:
            page.evaluate(f"""
                () => {{
                    const c = document.querySelector('{RESULTS_CONTAINER}');
                    if (c) c.scrollTop = 0;
                    window.scrollTo(0, 0);
                }}
            """)
            human_delay(0.8, 1.5)
        except Exception:
            pass

        link = page.query_selector(f"a[data-jk='{jk}']")
        if link:
            try:
                # force=True: omite scroll_into_view pero dispara eventos reales
                # que React procesa (a diferencia de el.click() JS puro)
                link.click(force=True, timeout=5_000)
                log.debug("  [indeed] Click (force) en jk=%s (capa 1)", jk)
                return True, False
            except Exception as exc:
                log.debug("  [indeed] force click falló, intentando JS: %s", exc)
                try:
                    page.evaluate("el => el.click()", link)
                    log.debug("  [indeed] Click JS en jk=%s (capa 1 fallback)", jk)
                    return True, False
                except Exception:
                    pass

        # -- Capa 2: scroll progresivo buscando el card ---------------------
        log.debug("  [indeed] Card jk=%s no visible — scroll progresivo", jk)
        for step in range(8):
            try:
                page.evaluate(f"""
                    () => {{
                        const c = document.querySelector('{RESULTS_CONTAINER}');
                        if (c) c.scrollTop += 400;
                        else window.scrollBy(0, 400);
                    }}
                """)
                human_delay(0.4, 0.8)
                link = page.query_selector(f"a[data-jk='{jk}']")
                if link:
                    try:
                        link.click(force=True, timeout=5_000)
                        log.debug("  [indeed] Click (force) en jk=%s (capa 2, step %d)", jk, step)
                        return True, False
                    except Exception:
                        try:
                            page.evaluate("el => el.click()", link)
                            log.debug("  [indeed] Click JS en jk=%s (capa 2 fallback, step %d)", jk, step)
                            return True, False
                        except Exception:
                            pass
            except Exception:
                break

        # -- Capa 3: volver a la listing URL y reintentar --------------------
        # Más seguro que navegar a /viewjob directamente (evita Cloudflare).
        if listing_url and "indeed.com" in listing_url:
            log.warning("  [indeed] Card jk=%s no hallado — recargando página de búsqueda", jk)
            try:
                page.goto(listing_url, wait_until="domcontentloaded", timeout=20_000)
                human_delay(2.5, 4.0)
                page.evaluate("window.scrollTo(0, 0)")
                human_delay(0.8, 1.5)
                link = page.query_selector(f"a[data-jk='{jk}']")
                if link:
                    link.click(force=True, timeout=5_000)
                    log.debug("  [indeed] Click (force) en jk=%s (capa 3 — reload)", jk)
                    return True, False
                # Scroll progresivo una vez más
                for step in range(6):
                    page.evaluate("window.scrollBy(0, 400)")
                    human_delay(0.4, 0.7)
                    link = page.query_selector(f"a[data-jk='{jk}']")
                    if link:
                        link.click(force=True, timeout=5_000)
                        log.debug("  [indeed] Click (force) en jk=%s (capa 3 reload+scroll %d)", jk, step)
                        return True, False
            except Exception as exc:
                log.warning("  [indeed] Capa 3 reload falló: %s", exc)

        take_error_screenshot(page, "indeed", f"no_card_{jk}")
        log.warning("  [indeed] jk=%s no encontrado en ninguna capa — skip", jk)
        return False, False

    def _find_easy_apply_in_iframe(self, page: Page):
        """
        Indeed a veces renderiza el botón Easy Apply dentro de un iframe embebido.
        - Retorna el ElementHandle del botón si lo encuentra en iframe (listo para click).
        - Retorna "iframe_applied" si se detecta que ya se procesó.
        - Retorna None si no hay iframe o no hay botón Easy Apply en él.
        """
        try:
            iframe_sel = (
                "#indeed-ia-iframe, "
                "iframe[name*='ia-'], "
                "iframe[src*='indeed'][src*='apply'], "
                "iframe[title*='apply' i], "
                "iframe[title*='solicitud' i]"
            )
            iframe_el = page.query_selector(iframe_sel)
            if not iframe_el:
                return None

            frame = iframe_el.content_frame()
            if not frame:
                return None

            log.debug("  [indeed] Iframe Easy Apply detectado — buscando botón dentro")
            btn = None
            for sel in SEL["easy_apply_btn"].split(","):
                sel = sel.strip()
                try:
                    candidates = frame.query_selector_all(sel)
                    for c in candidates:
                        if c.is_visible():
                            btn = c
                            break
                except Exception:
                    continue
                if btn:
                    break

            if btn:
                log.info("  [indeed] Easy Apply encontrado DENTRO del iframe")
                return btn   # devolver el botón para que _apply_easy lo use

            return None
        except Exception as exc:
            log.debug("_find_easy_apply_in_iframe error: %s", exc)
            return None

    def _find_external_apply_js(self, page: Page):
        """
        Busca en el DOM un enlace que apunte CLARAMENTE a una aplicación externa.
        Solo retorna href si hay un link inequívoco fuera de indeed.com (clk, applystart, etc).
        NO cuenta botones de navegación genéricos (evita falsos positivos).
        """
        try:
            result = page.evaluate("""
                () => {
                    // Palabras clave de aplicación (exactas, no substrings genéricos)
                    const applyWords = ['aplicar', 'apply now', 'postular', 'solicitar empleo', 'apply on'];
                    // Hrefs/paths que indican cuenta/navegación — ignorar
                    const navPaths = ['/account', '/login', '/home', '/settings',
                                      '/messages', '/notifications', '/resume',
                                      '/auth', '/signup', 'signin', 'register'];

                    const allLinks = Array.from(document.querySelectorAll('a[href]'));
                    for (const el of allLinks) {
                        const txt = (el.innerText || el.textContent || '').toLowerCase().trim();
                        const href = el.href || '';

                        // Excluir si está dentro de nav, header, footer o menú lateral
                        if (el.closest('nav, header, footer, [role="navigation"], [class*="nav"]')) continue;

                        // Excluir hrefs de cuenta/login/navegación
                        if (navPaths.some(p => href.toLowerCase().includes(p))) continue;

                        // Solo considerar si el texto coincide con aplicación
                        const matchesApply = applyWords.some(w => txt.includes(w));
                        if (!matchesApply) continue;

                        // Link definitivamente externo: /clk, applystart, o dominio ajeno
                        if (href.includes('/clk') || href.includes('applystart') ||
                            href.includes('/rc/clk') || href.includes('apply?jk=')) {
                            return href;
                        }
                        if (!href.includes('indeed.com') && href.startsWith('http')) {
                            return href;
                        }
                    }
                    return null;
                }
            """)
            return result
        except Exception as exc:
            log.debug("_find_external_apply_js error: %s", exc)
            return None

    def _find_card(self, page: Page, jk: str):
        """Busca el div.job_seen_beacon que contiene el link con data-jk=jk."""
        try:
            link = page.query_selector(f"a[data-jk='{jk}']")
            if not link:
                return None
            # Subir hasta div.job_seen_beacon
            card = link.evaluate_handle("""
                el => {
                    let cur = el;
                    for (let i = 0; i < 8; i++) {
                        if (!cur) return null;
                        if (cur.classList && cur.classList.contains('job_seen_beacon')) return cur;
                        cur = cur.parentElement;
                    }
                    return el;  // fallback: retornar el link mismo
                }
            """)
            return card.as_element()
        except Exception as exc:
            log.debug("_find_card error: %s", exc)
            return None

    def _wait_for_panel(self, page: Page) -> bool:
        """
        Espera a que el panel de detalle de Indeed esté visible.

        Estrategia de 3 niveles:
        1. URL: Indeed agrega `vjk=<jk>` cuando el panel abre -> señal más confiable.
        2. Selectores CSS conocidos del panel.
        3. JS: buscar cualquier elemento con texto sustancial de descripción.
        """
        # Nivel 1: detectar cambio de URL (vjk parameter)
        try:
            page.wait_for_url("*vjk=*", timeout=6_000)
            # URL cambió -> panel está cargando, esperar un poco
            human_delay(0.8, 1.5)
        except PlaywrightTimeout:
            pass  # No todas las versiones de Indeed usan vjk en la URL

        # Nivel 2: esperar selectores CSS conocidos
        try:
            page.wait_for_selector(SEL["detail_panel"], timeout=PANEL_WAIT_MS)
            return True
        except PlaywrightTimeout:
            pass

        # Nivel 3: detectar contenido de descripción vía JS (por si los selectores cambiaron)
        try:
            result = page.evaluate("""
                () => {
                    const candidates = [
                        '#jobDescriptionText', '.jobsearch-jobDescriptionText',
                        '.jobsearch-RightPane', '#mosaic-afterAd',
                        '[class*="jobDetails"]', '[class*="JobDetails"]',
                        '[data-testid*="jobDetails"]', '[data-testid="jobsearch-RightPane"]',
                        '#viewJobSSRRoot', '.jobsearch-ViewJobLayout',
                        '[class*="rightPane"]', '[class*="RightPane"]'
                    ];
                    for (const s of candidates) {
                        try {
                            const el = document.querySelector(s);
                            if (el && el.innerText && el.innerText.trim().length > 80) return true;
                        } catch(e) {}
                    }
                    return false;
                }
            """)
            if result:
                log.debug("  [indeed] Panel detectado vía JS fallback")
                return True
        except Exception:
            pass

        return False

    def _find_visible(self, page: Page, selector: str):
        """Retorna el primer elemento visible que coincide con selector, o None."""
        try:
            for sel_part in selector.split(","):
                sel_part = sel_part.strip()
                try:
                    els = page.query_selector_all(sel_part)
                    for el in els:
                        if el.is_visible():
                            return el
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def _apply_easy(self, page: Page, apply_btn, title: str) -> tuple[str, str]:
        """
        Hace click en el botón de Easy Apply de Indeed y rellena el overlay.
        """
        try:
            apply_btn.click()
            human_delay(2.0, 3.5)
        except Exception as exc:
            log.warning("  [indeed] Click en Easy Apply falló: %s", exc)
            return f"error: easy_apply_click {exc}", title

        # Verificar que el overlay se abrió
        overlay = self._wait_for_overlay(page)
        if not overlay:
            log.warning("  [indeed] Overlay de Easy Apply no apareció")
            return "error: overlay_timeout", title

        # Rellenar y avanzar pasos (máximo 5)
        for step in range(5):
            human_delay(1.0, 2.0)
            fill_form(page, self.profile)

            # Buscar botón de avance / submit
            advanced = False
            for sel in SEL["overlay_next"].split(","):
                sel = sel.strip()
                try:
                    btn = page.query_selector(sel)
                    if btn and btn.is_visible() and btn.is_enabled():
                        btn.click()
                        human_delay(1.5, 2.5)
                        advanced = True
                        break
                except Exception:
                    continue

            if not advanced:
                log.debug("  [indeed] No se encontró botón de avance en step %d", step)
                break

            # Verificar si ya mostró confirmación
            if self._check_success(page):
                log.info("  [indeed] [OK] Aplicación enviada (step %d)", step)
                return "applied", title

        # Verificación final de éxito
        if self._check_success(page):
            return "applied", title

        take_error_screenshot(page, "indeed", "apply_incomplete")
        return "filled_no_submit", title

    def _wait_for_overlay(self, page: Page) -> bool:
        try:
            page.wait_for_selector(SEL["apply_overlay"], timeout=OVERLAY_WAIT_MS)
            return True
        except PlaywrightTimeout:
            # Intentar con selector genérico
            try:
                page.wait_for_selector("div[class*='ia-']", timeout=3_000)
                return True
            except PlaywrightTimeout:
                return False

    def _wait_for_standalone(self, page: Page) -> bool:
        """
        Espera a que una página standalone de Indeed (/viewjob?jk=...) cargue
        su contenido principal. A diferencia del panel lateral, aquí el job detail
        ocupa toda la página.

        Nota: Capa 3 de _click_card_robust ya NO navega a /viewjob directamente,
        por lo que este método rara vez se llama. Se mantiene como salvaguarda.
        """
        standalone_selectors = (
            "#viewJobSSRRoot, "
            "#jobDescriptionText, "
            ".jobsearch-JobComponent, "
            ".jobsearch-ViewJobLayout, "
            "div[class*='JobComponent'], "
            "h1.jobsearch-JobInfoHeader-title, "
            "h1[class*='jobTitle']"
        )
        try:
            page.wait_for_selector(standalone_selectors, timeout=PANEL_WAIT_MS)
            return True
        except PlaywrightTimeout:
            # Segundo intento: esperar que desaparezca el loader
            try:
                page.wait_for_load_state("networkidle", timeout=5_000)
                # Verificar que hay algo útil en la página
                el = page.query_selector("#jobDescriptionText, h1")
                return el is not None
            except Exception:
                return False

    def _check_success(self, page: Page) -> bool:
        try:
            for sel_part in SEL["apply_success"].split(","):
                sel_part = sel_part.strip()
                try:
                    el = page.query_selector(sel_part)
                    if el and el.is_visible():
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

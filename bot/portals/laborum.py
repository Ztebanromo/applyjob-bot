"""
Portal Laborum Chile (plataforma Navent / React SPA).

Este módulo implementa la lógica específica para el portal Laborum.cl, incluyendo
la extracción de ofertas mediante su API interna y el proceso de postulación
automatizada en su interfaz SPA (Single Page Application).

Arquitectura del Portal:
    - Framework: React.
    - Renderizado: Cliente (SPA), requiere tiempos de espera para hidratación.
    - API: /api/avisos/searchV2 (retorna JSON con ofertas).
    - Selectores: Clases dinámicas (sc-xxxx), se priorizan selectores por texto y atributos.

Autenticación y Sesiones Reales:
    - Este bot utiliza el perfil real del usuario. No se recomiendan cuentas de prueba.
    - Las sesiones se almacenan de forma persistente en `sessions/laborum`.
    - Si se proporcionan `LABORUM_EMAIL` y `LABORUM_PASSWORD` en el archivo `.env`,
      el motor intentará realizar un inicio de sesión automático.
    - En caso de no haber credenciales o fallar el auto-login, el bot pausará la
      ejecución y esperará hasta 5 minutos a que el usuario inicie sesión manualmente
      en la ventana del navegador.
"""

import json
import time
import logging
import re as _re
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Set, Optional, Dict

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout, ElementHandle

from .base import BasePortal
from ..stealth_utils import human_delay, take_error_screenshot
from ..form_filler import fill_form
from ..config import schedule_ok

# Ruta del archivo de caché persistente de preguntas y respuestas
_QA_CACHE_PATH = Path(__file__).parent.parent.parent / "data" / "qa_cache.json"

# Ruta de preguntas pendientes de respuesta del usuario
_PENDING_QUESTIONS_PATH = Path(__file__).parent.parent.parent / "data" / "pending_questions.json"


def _save_pending_question(question_text: str, offer_url: str = "") -> None:
    """
    Guarda una pregunta desconocida en pending_questions.json para que
    el usuario la responda desde el dashboard HTML.
    """
    try:
        _PENDING_QUESTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing: list = []
        if _PENDING_QUESTIONS_PATH.exists():
            with open(_PENDING_QUESTIONS_PATH, encoding="utf-8") as f:
                existing = json.load(f)

        # No duplicar la misma pregunta
        norm = question_text.strip().lower()[:200]
        if any(e.get("norm", "") == norm for e in existing):
            return

        existing.append({
            "question":  question_text.strip(),
            "norm":      norm,
            "portal":    "laborum",
            "offer_url": offer_url,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "answered":  False,
            "answer":    "",
        })
        with open(_PENDING_QUESTIONS_PATH, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logging.getLogger("applyjob.laborum").debug("Error guardando pregunta pendiente: %s", exc)

# Configuración del Logger específico para este portal
log = logging.getLogger("applyjob.laborum")

# Diccionario de selectores CSS y patrones de búsqueda
# Se utilizan selectores de Playwright (como :has-text) para evitar la fragilidad
# de las clases dinámicas de React/Styled Components.
SEL = {
    # Selector de las tarjetas de empleo en el listado
    "card": "a[href*='/empleos/'][class*='sc-']",
    
    # Selector del botón principal de postulación en la página de detalle
    # Laborum cambió de "Postularme" → "Postulación rápida" (mayo 2026)
    "apply_btn": (
        "button:has-text('Postulación rápida'), "
        "button:has-text('Postularme'), "
        "button:has-text('Postular'), "
        "a:has-text('Postulación rápida'), "
        "a:has-text('Postularme'), "
        "button[class*='sc-enLHqu']"
    ),
    
    # Título del puesto en la página de detalle
    "job_title": "h1",
    
    # Señales que indican que el usuario no está logueado
    "login_signal": (
        "input[type='email'], "
        "input[type='password'], "
        "[class*='login'], "
        "#ingresarNavBar, "
        "button:has-text('Ingresar')"
    ),
    
    # Señales de presencia de formularios de preguntas/filtros
    "form_signal": "form, textarea, input[type='text'], .sc-fLcnxK",
    
    # Mensajes de éxito tras postular
    "success_signal": (
        "div:has-text('postulación enviada'), "
        "div:has-text('aplicación enviada'), "
        "div:has-text('Te postulaste'), "
        "div:has-text('inscripción enviada'), "
        "h2:has-text('¡Ya te postulaste!')"
    ),
}


class LaborumPortal(BasePortal):
    """
    Controlador especializado para el portal Laborum.cl.
    
    Maneja la extracción híbrida de ofertas (API + DOM) y el flujo de postulación
    directa o con formularios intermedios.
    """

    # Keywords dinámicas del .env
    from ..config import CLEAN_KEYWORDS
    SEARCH_KEYWORD: str = CLEAN_KEYWORDS
    API_MAX_PAGES: int = 15   # 750 items totales escaneados por run

    # Palabras en el TÍTULO que indican trabajo de TI (substring seguro — son largas)
    _TITLE_IT_WORDS: Set[str] = {
        # Roles de desarrollo
        "desarrollador", "programador", "developer", "programmer",
        "software engineer", "software developer",
        "fullstack", "full stack", "frontend", "front end", "backend", "back end",
        # Lenguajes/frameworks (suficientemente únicos como substring)
        "python", "angular", "nodejs", "node.js", "devops",
        "flutter", "kotlin", "docker", "kubernetes", "typescript",
        # Clouds y plataformas
        "azure", "salesforce", "dynamics 365", "power bi", "powerbi",
        # Datos e IA
        "machine learning", "deep learning", "data engineer",
        "data scientist", "data science", "inteligencia artificial",
        "pipeline de datos", "ingeniero de datos",
        # Roles TI multi-palabra (safe como substring)
        "soporte ti", "soporte it", "soporte técnico ti",
        "analista programador", "analista de sistemas", "analista bi",
        "ingeniero de software", "ingeniero en sistemas",
        "infraestructura ti", "infraestructura it",
        "seguridad informática", "ciberseguridad",
        "redes y telecomunicaciones",
        # QA / Testing
        "qa engineer", "qa analyst", "quality assurance", "tester", "testing",
        # Prácticas TI
        "práctica en ti", "práctica ti", "práctica informática",
        "practicante ti", "pasantía ti",
        "trainee developer", "trainee software", "trainee ti",
    }
    # Palabras CORTAS — match por word-boundary (evitan falsos positivos de substring)
    _TITLE_IT_WORDS_EXACT: Set[str] = {
        "react", "java", "android", "ios", "cloud", "sql", "sap", "aws",
        "erp", "crm", "bi", "ti", "it",
    }

    def __init__(self, config: dict, profile: dict, dry_run: bool = False):
        """
        Inicializa el portal de Laborum.

        Args:
            config (dict): Configuración del sitio desde SITE_CONFIG.
            profile (dict): Perfil del usuario para rellenar formularios.
            dry_run (bool): Si es True, no enviará las postulaciones.
        """
        super().__init__(config, profile, dry_run)
        # Registro de URLs procesadas en la sesión para evitar bucles de paginación
        self._returned_urls: Set[str] = set()
        # Caché persistente de preguntas → respuestas (cargado desde disco)
        self._qa_cache: Dict[str, str] = self._load_qa_cache()

    # -----------------------------------------------------------------------
    # Q&A Cache — persistencia de preguntas de screening
    # -----------------------------------------------------------------------

    @staticmethod
    def _normalize_question(text: str) -> str:
        """
        Normaliza el texto de una pregunta para usarlo como clave de caché.
        Elimina espacios extra, signos de puntuación al inicio/fin y pasa a lowercase.
        Se trunca a 200 caracteres para evitar claves gigantes.
        """
        normalized = _re.sub(r"\s+", " ", text.strip().lower())
        normalized = normalized.strip("?¿.,:;!")
        return normalized[:200]

    @staticmethod
    def _load_qa_cache() -> Dict[str, str]:
        """
        Carga la caché desde `data/qa_cache.json`.
        Si el archivo no existe, retorna un dict vacío (primera ejecución).
        """
        try:
            if _QA_CACHE_PATH.exists():
                with open(_QA_CACHE_PATH, encoding="utf-8") as f:
                    data = json.load(f)
                log.info("Q&A cache cargada: %d respuestas guardadas", len(data))
                return data
        except Exception as exc:
            log.warning("No se pudo cargar qa_cache.json: %s", exc)
        return {}

    def _save_qa_cache(self) -> None:
        """
        Persiste la caché actual en `data/qa_cache.json`.
        Crea el directorio `data/` si no existe.
        """
        try:
            _QA_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_QA_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(self._qa_cache, f, ensure_ascii=False, indent=2)
            log.debug("Q&A cache guardada: %d entradas", len(self._qa_cache))
        except Exception as exc:
            log.warning("No se pudo guardar qa_cache.json: %s", exc)

    def _get_pending_answer(self, question_text: str) -> Optional[str]:
        """
        Busca si el usuario ya respondió esta pregunta en pending_questions.json.
        Retorna la respuesta si existe y está marcada como answered=True.
        """
        try:
            if not _PENDING_QUESTIONS_PATH.exists():
                return None
            with open(_PENDING_QUESTIONS_PATH, encoding="utf-8") as f:
                data = json.load(f)
            norm = self._normalize_question(question_text)
            for entry in data:
                if not entry.get("answered") or not entry.get("answer"):
                    continue
                if entry.get("norm", "") == norm or norm in entry.get("norm", ""):
                    return entry["answer"]
        except Exception:
            pass
        return None

    def _get_cached_answer(self, question_text: str) -> Optional[str]:
        """
        Busca una respuesta guardada para la pregunta dada.
        Primero busca coincidencia exacta (normalizada), luego partial match
        (la pregunta normalizada está contenida en una clave guardada, o viceversa).
        """
        key = self._normalize_question(question_text)
        if not key:
            return None
        # Coincidencia exacta
        if key in self._qa_cache:
            log.debug("  [qa_cache] HIT exacto: %r", key[:60])
            return self._qa_cache[key]
        # Coincidencia parcial: la clave guardada empieza con nuestra pregunta o viceversa
        for cached_key, cached_answer in self._qa_cache.items():
            if key in cached_key or cached_key in key:
                log.debug("  [qa_cache] HIT parcial: %r → %r", key[:40], cached_key[:40])
                return cached_answer
        return None

    def _cache_answer(self, question_text: str, answer: str) -> None:
        """
        Guarda un nuevo par pregunta→respuesta en la caché y la persiste en disco.
        No sobreescribe si ya existe una respuesta para esa pregunta.
        """
        key = self._normalize_question(question_text)
        if not key:
            return
        if key not in self._qa_cache:
            self._qa_cache[key] = answer
            log.info("  [qa_cache] NUEVA pregunta guardada: %r", key[:60])
            self._save_qa_cache()

    def _goto_networkidle(self, page: Page, url: str) -> bool:
        """
        Navega a una URL esperando a que la red esté inactiva (networkidle).
        
        Laborum es una SPA pesada, por lo que se añade un delay extra tras el idle
        para permitir que React termine de renderizar los componentes dinámicos.

        Args:
            page (Page): Instancia de la página de Playwright.
            url (str): URL de destino.

        Returns:
            bool: True si la navegación fue exitosa, False en caso contrario.
        """
        try:
            log.debug("Navegando a %s (networkidle)", url[:60])
            page.goto(url, wait_until="networkidle", timeout=25_000)
            time.sleep(1.5)  # Tiempo de gracia para React
            return True
        except PlaywrightTimeout:
            log.warning("Timeout networkidle en %s, intentando fallback domcontentloaded", url[:60])
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=15_000)
                time.sleep(3)
                return True
            except Exception as exc:
                log.error("Fallo total de navegación en %s: %s", url[:60], exc)
                return False

    def _fetch_job_urls_via_api(self, page: Page, keyword: str, max_pages: int = 3) -> List[str]:
        """
        Extrae URLs de ofertas utilizando la API interna de búsqueda de Laborum.
        
        Este método ejecuta un script en el contexto del navegador para aprovechar
        las cookies de sesión y evitar bloqueos de CORS.

        Args:
            page (Page): Instancia de la página activa.
            keyword (str): Palabra clave de búsqueda.
            max_pages (int): Número máximo de páginas de la API a consultar.

        Returns:
            List[str]: Lista de URLs completas de las ofertas encontradas.
        """
        urls: List[str] = []
        seen_ids: Set[str] = set()
        page_size = 50  # Cantidad de avisos por llamada (máximo permitido por la API)

        for page_num in range(max_pages):
            log.debug("Consultando API Laborum: página %d", page_num)
            try:
                # Se inyecta el fetch directamente en la consola del navegador
                result = page.evaluate(f"""
                async () => {{
                    try {{
                        const resp = await fetch(
                            '/api/avisos/searchV2?pageSize={page_size}&page={page_num}&sort=RELEVANTES',
                            {{
                                method: 'POST',
                                credentials: 'include',
                                headers: {{
                                    'Accept': 'application/json, text/plain, */*',
                                    'Content-Type': 'application/json',
                                    'x-site-id': 'BMCL',
                                }},
                                body: JSON.stringify({{
                                    "filtros": [],
                                    "palabraClave": "{keyword}"
                                }})
                            }}
                        );
                        if (!resp.ok) return {{ error: resp.status, items: [] }};
                        const data = await resp.json();
                        return {{
                            total: data.total || 0,
                            items: (data.content || []).map(j => ({{
                                id: String(j.id || ''),
                                title: j.titulo || '',
                                url: j.url || j.postulacionUrl || j.link || j.slug || '',
                                titulo_slug: (j.titulo || '').toLowerCase()
                                    .normalize('NFD').replace(/[̀-ͯ]/g, '')
                                    .replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, ''),
                            }})),
                        }};
                    }} catch(e) {{
                        return {{ error: String(e), items: [] }};
                    }}
                }}
                """)

                if 'error' in result:
                    log.warning("Error en respuesta de API Laborum (p%d): %s", page_num, result['error'])
                    break

                items = result.get('items', [])
                if not items:
                    log.debug("No se encontraron más ítems en la página %d de la API", page_num)
                    break

                for item in items:
                    jid = item.get('id', '').strip()
                    title = item.get('title', '').strip()
                    if not jid or jid in seen_ids:
                        continue

                    # Filtro de seguridad: el título debe ser de TI
                    if self._title_is_tech(title):
                        seen_ids.add(jid)
                        # Preferir URL directa de la API; si no, construir con slug + id
                        raw_url = item.get('url', '').strip()
                        if raw_url:
                            if not raw_url.startswith('http'):
                                raw_url = 'https://www.laborum.cl' + raw_url
                            urls.append(raw_url)
                        else:
                            # Construir URL con slug del título para evitar 404
                            titulo_slug = item.get('titulo_slug', '').strip()
                            if titulo_slug:
                                urls.append(f"https://www.laborum.cl/empleos/{titulo_slug}-{jid}.html")
                            else:
                                urls.append(f"https://www.laborum.cl/empleos/oferta-{jid}.html")

                # Si recibimos menos del pageSize, es la última página
                if len(items) < page_size:
                    break

            except Exception as exc:
                # TargetClosedError → re-raise para que el engine recupere la página
                if "TargetClosedError" in type(exc).__name__ or "Target page" in str(exc):
                    log.warning("Página cerrada durante API fetch (página %d) — re-lanzando", page_num)
                    raise
                log.error("Error crítico consultando API en página %d: %s", page_num, exc)
                break

        return urls

    def get_offer_urls(self, page: Page) -> List[str]:
        """
        Recopila URLs de ofertas de empleo usando multi-keyword API search.

        Estrategia:
          1. Itera sobre SEARCH_KEYWORDS lanzando una búsqueda API por cada uno.
          2. Acumula y deduplica resultados hasta tener ≥ TARGET_JOBS únicos.
          3. Si la API falla completamente, cae a scraping del DOM.

        Returns:
            List[str]: URLs únicas no procesadas en esta sesión.
        """
        # ── API scan: un solo keyword, muchas páginas ─────────────────────────
        # NOTA: Omitimos la espera de tarjetas DOM — Laborum detecta bots en ~5s
        # y puede cerrar la página. Ir directo a la API es más rápido y seguro.
        # La API devuelve el mismo pool ordenado por relevancia; escanear más páginas
        # nos da acceso a los jobs tech dispersos entre los no-tech.
        api_urls = self._fetch_job_urls_via_api(page, self.SEARCH_KEYWORD,
                                                max_pages=self.API_MAX_PAGES)
        if api_urls:
            fresh = self._deduplicate_session(api_urls)
            log.info("API: %d tech jobs encontrados -> %d frescos para procesar",
                     len(api_urls), len(fresh))
            return fresh

        # ── Fallback DOM ──────────────────────────────────────────────────────
        log.info("API sin resultados, extrayendo desde el DOM...")
        dom_urls: List[str] = []
        try:
            for card in page.query_selector_all(SEL["card"]):
                href = card.get_attribute("href") or ""
                if not href.startswith("http"):
                    href = "https://www.laborum.cl" + href
                if self._is_tech_job(href.lower()):
                    dom_urls.append(href)
        except Exception as exc:
            log.warning("Error DOM scraping: %s", exc)

        return self._deduplicate_session(dom_urls)

    def _deduplicate_session(self, urls: List[str]) -> List[str]:
        """
        Filtra URLs que ya han sido procesadas en la ejecución actual del bot.

        Args:
            urls (List[str]): Lista de URLs a filtrar.

        Returns:
            List[str]: Lista de URLs nuevas.
        """
        fresh = [u for u in urls if u not in self._returned_urls]
        self._returned_urls.update(fresh)
        return fresh

    def apply_to_offer(self, page: Page, offer_url: str) -> Tuple[str, str]:
        """
        Realiza el proceso de postulación para una oferta específica.
        
        Pasos:
            1. Navega a la oferta.
            2. Extrae el título del puesto.
            3. Detecta y hace clic en el botón 'Postularme'.
            4. Gestiona flujos secundarios (Login, Formularios).
            5. Verifica el éxito de la operación.

        Args:
            page (Page): Instancia de la página.
            offer_url (str): URL de la oferta.

        Returns:
            Tuple[str, str]: (Estado resultante, Título de la oferta).
        """
        title = "unknown"
        try:
            if not self._goto_networkidle(page, offer_url):
                return "error: navegación fallida", title

            # Detectar 404 (URL cambiada o oferta eliminada)
            current_url = page.url
            if "/404" in current_url or "not-found" in current_url:
                log.info("  [laborum] 404 detectado: %s", offer_url[:60])
                return "skipped_404", title
            try:
                page_text = (page.evaluate("() => document.body?.innerText?.slice(0,300) || ''") or "").lower()
                if any(x in page_text for x in ("no encontramos la página", "página no encontrada", "error 404")):
                    log.info("  [laborum] 404 detectado por texto: %s", offer_url[:60])
                    return "skipped_404", title
            except Exception:
                pass

            # Extracción del título para el reporte
            title_el = page.query_selector(SEL["job_title"])
            if title_el:
                title = (title_el.text_content() or "").strip()[:80]

            # Filtro de horario: leer descripción visible y descartar si es noche/finde
            try:
                desc_text = page.evaluate(
                    "() => { const d = document.querySelector('[class*=\"description\"], [class*=\"Description\"], "
                    "#job-description, [data-testid*=\"description\"]'); "
                    "return d ? d.innerText : document.body?.innerText?.slice(0, 800) || ''; }"
                ) or ""
                if not schedule_ok(title + " " + desc_text):
                    log.info("  [laborum] Descartada por turno incompatible: '%s'", title)
                    print(f"  [FILTRO] Descartada por turno incompatible: {title}")
                    return "skipped_schedule", title
            except Exception:
                pass

            # Esperar que React hidrate el botón de postulación (SPA)
            try:
                page.wait_for_selector(SEL["apply_btn"], timeout=8_000)
            except Exception:
                pass  # seguimos — puede que no exista

            # Localizar el botón de postulación
            apply_btn = None
            for selector in SEL["apply_btn"].split(","):
                try:
                    btn = page.query_selector(selector.strip())
                    if btn and btn.is_visible():
                        apply_btn = btn
                        break
                except Exception:
                    continue

            if not apply_btn:
                # Scroll hacia abajo por si el botón está fuera de viewport
                page.evaluate("window.scrollTo(0, 400)")
                time.sleep(1)
                for selector in SEL["apply_btn"].split(","):
                    try:
                        btn = page.query_selector(selector.strip())
                        if btn and btn.is_visible():
                            apply_btn = btn
                            break
                    except Exception:
                        continue

            if not apply_btn:
                # Sin botón nativo: oferta cerrada o solo informativa → no es un error
                log.info("Sin botón de postulación en %s (oferta cerrada o externa)", offer_url[:60])
                take_error_screenshot(page, "laborum", "no_apply_btn")
                return "skipped_no_apply", title

            # Clic humano y espera de transición SPA
            apply_btn.click()
            human_delay(1.0, 2.0)

            # --- Manejo de Estados Post-Click ---
            
            # Caso A: Solicita login
            if self._check_is_login_page(page):
                log.info("Requiere login manual para continuar")
                return "external_apply", title

            # Caso B: Formulario multi-paso de Laborum
            # Paso 1: Salary form  →  Paso 2: Screening questions
            # El bot itera hasta 3 pasos, rellenando y enviando cada uno.
            MAX_FORM_STEPS = 3
            form_submitted = False
            for step in range(MAX_FORM_STEPS):
                if not self._check_has_form(page):
                    break

                log.info("Formulario paso %d — iniciando auto-llenado", step + 1)

                # Relleno específico Laborum (salary + preguntas abiertas)
                n = self._fill_laborum_screening(page)
                # Relleno genérico (dropdowns, radios, etc.)
                fill_form(page, self.profile)
                log.debug("  [form] step %d: %d campos Laborum + genérico", step + 1, n)

                # En dry_run pausamos para inspección visual
                if self.dry_run:
                    log.info("  [dry_run] Paso %d completado. Pausa visual...", step + 1)
                    human_delay(4.0, 6.0)
                    return "dry_run", title

                # Buscar y pulsar el botón de submit de ESTE paso
                # Orden de prioridad: "Responder" > type=submit > textos genéricos
                submit_selectors = [
                    "button:has-text('Responder')",
                    "button:has-text('Enviar postulación')",
                    "button[type='submit']:has-text('Postularme')",
                    "button[type='submit']",
                    "button:has-text('Postularme')",
                    "button:has-text('Enviar')",
                    "button:has-text('Postular')",
                ]
                submitted_step = False
                for sub_sel in submit_selectors:
                    try:
                        sub_btn = page.query_selector(sub_sel)
                        if sub_btn and sub_btn.is_visible() and sub_btn.is_enabled():
                            sub_btn.click()
                            human_delay(1.5, 2.5)
                            submitted_step = True
                            form_submitted = True
                            log.debug("  [form] paso %d enviado con: %r", step + 1, sub_sel)
                            break
                    except Exception:
                        continue
                if not submitted_step:
                    log.warning("  [form] no se encontró botón de submit en paso %d", step + 1)
                    break

            # Verificar si se logró el éxito
            if self._check_success(page):
                log.info("Postulación exitosa confirmada")
                return "applied", title

            # Si enviamos el formulario pero no vemos confirmación explícita,
            # probablemente sí se aplicó (Laborum no siempre muestra el mensaje)
            if form_submitted:
                log.info("Formulario enviado — sin confirmación visual, marcando como applied")
                return "applied", title

            # Sin form, sin éxito → registrar como external
            return "external_apply", title

        except Exception as exc:
            log.error("Error aplicando a %s: %s", offer_url, exc)
            take_error_screenshot(page, "laborum", "exception")
            return f"error: {str(exc)[:50]}", title

    # --- Llenado de Formulario Específico de Laborum ---

    # Palabras que indican que la pregunta es sobre sueldo/renta
    _SALARY_WORDS = {
        "renta", "salario", "sueldo", "pretension", "pretensión",
        "remuneracion", "remuneración", "cuánto quieres ganar",
    }

    def _fill_laborum_screening(self, page: Page) -> int:
        """
        Rellena los campos de screening específicos de Laborum:
        1. input[name='salarioPretendido'] — campo de sueldo bruto
        2. textarea[name*='pregunta']      — preguntas abiertas de la empresa

        Estrategia de respuesta para textareas (en orden de prioridad):
          a) Busca en el Q&A cache persistente (pregunta → respuesta guardada)
          b) Si la pregunta es de sueldo → usa profile["salary"]
          c) Fallback → usa profile["cover_letter"]
          La respuesta elegida se guarda en cache para futuros runs.

        Returns:
            int: número de campos rellenados.
        """
        filled = 0
        profile = self.profile

        # ── 1. Campo de salario bruto ────────────────────────────────────────
        sal_input = page.query_selector("input[name='salarioPretendido']")
        if sal_input and sal_input.is_visible():
            try:
                if not (sal_input.evaluate("el => el.value") or "").strip():
                    sal_input.click()
                    sal_input.fill("850000")
                    filled += 1
                    log.debug("  [form] salarioPretendido → 850000")
            except Exception as exc:
                log.debug("  [form] salarioPretendido error: %s", exc)

        # ── 2. Textareas de preguntas de screening ───────────────────────────
        for ta in page.query_selector_all("textarea[name*='pregunta']"):
            if not ta.is_visible():
                continue
            try:
                # Saltar si ya tiene contenido
                current_val = ta.evaluate("el => el.value") or ""
                if current_val.strip():
                    continue

                # Detectar el texto de la pregunta (label encima del textarea)
                question_text = ta.evaluate("""
                    el => {
                        let cur = el.parentElement;
                        for (let j = 0; j < 7; j++) {
                            if (!cur) break;
                            let sib = cur.previousElementSibling;
                            while (sib) {
                                const txt = sib.textContent.trim();
                                if (txt.length > 4) return txt;
                                sib = sib.previousElementSibling;
                            }
                            const pTxt = cur.textContent.trim();
                            if (pTxt.length > 4 && pTxt.length < 400) return pTxt;
                            cur = cur.parentElement;
                        }
                        return '';
                    }
                """) or ""
                question_lc = question_text.lower()

                # ── Elegir respuesta (prioridad: cache > salary-detect > PENDIENTE) ──
                # Primero verificar si la pregunta pendiente ya tiene respuesta del usuario
                pending_answer = self._get_pending_answer(question_text)

                # a) Caché persistente (respuesta ya guardada)
                cached = self._get_cached_answer(question_text)
                if cached:
                    value = cached
                    answer_source = "cache"
                # b) Respuesta del usuario desde pending_questions.json
                elif pending_answer:
                    value = pending_answer
                    answer_source = "pending_answered"
                    self._cache_answer(question_text, value)  # promover a cache
                # c) Pregunta de sueldo detectada por keywords
                elif any(w in question_lc for w in self._SALARY_WORDS):
                    value = profile.get("salary", "850.000")
                    answer_source = "salary"
                    self._cache_answer(question_text, value)
                # d) Pregunta desconocida → NO rellenar, guardar como pendiente
                else:
                    _save_pending_question(question_text)
                    log.warning(
                        "  [form] PREGUNTA PENDIENTE (sin respuesta): %r — dejando vacío",
                        question_text[:80],
                    )
                    print(f"\n[PREGUNTA_PENDIENTE] {question_text[:120]}\n", flush=True)
                    continue  # no rellenar este campo

                ta.click()
                ta.fill(value)
                filled += 1
                log.debug("  [form] pregunta=%r → [%s]", question_text[:60], answer_source)

            except Exception as exc:
                log.debug("  [form] textarea error: %s", exc)

        return filled

    # --- Métodos de Verificación y Estado ---

    def _check_is_login_page(self, page: Page) -> bool:
        """Verifica si la página actual es un formulario de login."""
        for sig in SEL["login_signal"].split(","):
            try:
                el = page.query_selector(sig.strip())
                if el and el.is_visible():
                    return True
            except Exception:
                continue
        return False

    def _check_has_form(self, page: Page) -> bool:
        """Verifica si hay un formulario de preguntas activo."""
        try:
            el = page.query_selector(SEL["form_signal"])
            return bool(el and el.is_visible())
        except Exception:
            return False

    def _check_success(self, page: Page) -> bool:
        """Busca señales visuales de postulación exitosa."""
        for sig in SEL["success_signal"].split(","):
            try:
                el = page.query_selector(sig.strip())
                if el and el.is_visible():
                    return True
            except Exception:
                continue
        return False

    # Palabras que EXCLUYEN un título aunque contenga palabras IT
    # (evita "Analista Contable SAP", "Médico Soporte", etc.)
    _TITLE_EXCLUDE: Set[str] = {
        "contable", "contador", "contabilidad", "cobranzas",
        "remuneraciones", "recursos humanos", "rrhh",
        "médico", "enfermera", "tens", "dental", "salud",
        "administrativo", "secretaria", "recepcionista",
        "vendedor", "vendedora", "ventas", "comercial",
        "chofer",
    }

    def _title_is_tech(self, title: str) -> bool:
        """
        Determina si un título de oferta es de TI.
        1. Descarta si contiene palabras de exclusión
        2. Acepta si contiene palabras IT largas (substring)
        3. Acepta si contiene palabras IT cortas (word-boundary)
        """
        title_lc = title.lower()

        # Paso 0: exclusión explícita (contabilidad, RRHH, salud, etc.)
        if any(w in title_lc for w in self._TITLE_EXCLUDE):
            return False

        # Paso 1: substring para frases y palabras largas
        if any(w in title_lc for w in self._TITLE_IT_WORDS):
            return True

        # Paso 2: word-boundary para palabras cortas ambiguas
        for word in self._TITLE_IT_WORDS_EXACT:
            if _re.search(r'\b' + _re.escape(word) + r'\b', title_lc):
                return True
        return False

    def _is_tech_job(self, slug: str) -> bool:
        """Determina si una URL slug de oferta es de TI (fallback DOM)."""
        return self._title_is_tech(slug.replace("-", " ").replace(".html", ""))

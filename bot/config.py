import os
import copy
from urllib.parse import quote_plus
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
load_dotenv()

# --- Configuración dinámica de Keywords ---
# Limpiamos keywords para evitar redundancias con los filtros automáticos de seniority
RAW_KEYWORDS = os.getenv("USER_KEYWORDS", "it dev desarrollador bodega").replace("'", "").replace('"', "")
# Quitamos "sin experiencia" y frases largas de la URL principal para no "marear" al buscador
CLEAN_KEYWORDS = RAW_KEYWORDS.lower().replace("sin experiencia", "").replace("lunes a viernes am", "").replace(",", " ").strip()
ENCODED_KEYWORDS = quote_plus(CLEAN_KEYWORDS.replace("  ", " ").replace(" ", ", "))
# Para portales que usan guiones (ej: computrabajo, laborum)
DASH_KEYWORDS = CLEAN_KEYWORDS.replace("  ", " ").replace(" ", "-")

# ---------------------------------------------------------------------------
# Perfil del usuario — se usa para autocompletar formularios
# ---------------------------------------------------------------------------
USER_PROFILE = {
    # ── Datos personales — vienen del .env o del wizard (--setup / subir CV) ──
    # NO hay valores hardcodeados. Configura tu .env antes de correr el bot.
    "full_name":        os.getenv("USER_FULL_NAME", ""),
    "first_name":       os.getenv("USER_FIRST_NAME", ""),
    "last_name":        os.getenv("USER_LAST_NAME", ""),
    "email":            os.getenv("USER_EMAIL", ""),
    "phone":            os.getenv("USER_PHONE", ""),
    "phone_number":     os.getenv("USER_PHONE_NUMBER", ""),
    "country_code":     os.getenv("USER_COUNTRY_CODE", "+56"),
    "country":          os.getenv("USER_COUNTRY", "Chile"),
    "city":             os.getenv("USER_CITY", ""),
    "linkedin":         os.getenv("USER_LINKEDIN", ""),
    "portfolio":        os.getenv("USER_PORTFOLIO", ""),
    "cv_path":          os.getenv("USER_CV_PATH", ""),
    "salary":           os.getenv("USER_SALARY", ""),
    "years_exp":        os.getenv("USER_YEARS_EXP", "0"),
    "cover_letter":     os.getenv("USER_COVER_LETTER", ""),
    "availability":     os.getenv("USER_AVAILABILITY", "Inmediata"),
    "english_level":    os.getenv("USER_ENGLISH_LEVEL", ""),
    "work_mode":        os.getenv("USER_WORK_MODE", "Sí"),
    "laborum_email":    os.getenv("LABORUM_EMAIL", os.getenv("USER_EMAIL", "")),
    "laborum_password": os.getenv("LABORUM_PASSWORD", ""),
    # Campos de screening — también desde .env
    "education":        os.getenv("USER_EDUCATION", ""),
    "contact_info":     os.getenv("USER_CONTACT_INFO", ""),
    "excel_level":      os.getenv("USER_EXCEL_LEVEL", ""),
    "bodega_exp":       os.getenv("USER_BODEGA_EXP", ""),
}

# ---------------------------------------------------------------------------
# Configuración de portales
# ---------------------------------------------------------------------------
SITE_CONFIG = {
    "linkedin": {
        "url_busqueda": (
            f"https://www.linkedin.com/jobs/search/?keywords={ENCODED_KEYWORDS}&location=Santiago%2C+Regi%C3%B3n+Metropolitana%2C+Chile&f_AL=true&f_E=2%2C3"
        ),
        "selector_oferta":          "li[data-occludable-job-id]",
        "selector_boton_aplicar":   "button.jobs-apply-button",
        "selector_siguiente_pagina": "button[aria-label='Ver más empleos']",
        "selector_titulo_oferta":   "h1.job-details-jobs-unified-top-card__job-title",
        "tipo_postulacion":         "modal",
        "max_offers_per_run":       10,
        "requires_login":           True,
    },
    "indeed": {
        # -- STANDBY ---------------------------------------------------------
        # Indeed usa Cloudflare Turnstile que detecta Playwright Chromium
        # en ~3 segundos. Requiere Chrome real con CDP (iniciar_bot.bat).
        # Pendiente: migrar a patchright / camoufox para bypass de CF.
        # Cambiar INDEED_ENABLED=true en .env cuando esté listo.
        "enabled": os.getenv("INDEED_ENABLED", "false").lower() == "true",
        # --------------------------------------------------------------------
        "url_busqueda": (
            f"https://cl.indeed.com/jobs?q={ENCODED_KEYWORDS}"
            f"&l=Santiago%2C+Regi%C3%B3n+Metropolitana"
            f"&radius=25&explvl=entry_level&sort=date"
        ),
        "selector_oferta":           "div.job_seen_beacon",
        "selector_ubicacion":        "[data-testid='text-location'], .companyLocation",
        "selector_boton_aplicar":    "button#indeedApplyButton",
        "selector_siguiente_pagina": "a[data-testid='pagination-page-next']",
        "selector_titulo_oferta":    "h2.jobTitle",
        "tipo_postulacion":          "modal",
        "max_offers_per_run":        10,
        "requires_login":            True,
    },
    "computrabajo": {
        "url_busqueda": (
            f"https://cl.computrabajo.com/trabajo-de-{DASH_KEYWORDS}-sin-experiencia"
        ),
        "selector_oferta":           "article.box_offer",
        "selector_ubicacion":        "p.fs13, .p_ubic, span.fs13",
        "selector_boton_aplicar":    "a.btn_postular, a:has-text('Postularme'), a:has-text('Postular'), button:has-text('Postularme')",
        "selector_siguiente_pagina": "a[title='Siguiente'], a[rel='next'], a[class*='next']",
        "selector_titulo_oferta":    "h1.title_offer, h1[class*='title'], h1",
        "tipo_postulacion":          "directa",
        "max_offers_per_run":        20,
        "requires_login":            True,
    },
    "getonyboard": {
        # GetOnBoard usa URLs tipo slug: /jobs-{keyword}
        # El parámetro ?q= es ignorado por el SPA — no sirve para filtrar.
        # Los filtros de seniority tampoco funcionan vía URL; se aplican en el bot.
        # URL por defecto para búsqueda manual / --portal getonyboard sin --multi-keyword:
        "url_busqueda": "https://www.getonbrd.com/jobs-desarrollador-junior",
        "selector_oferta":           "a.gb-results-list__item",
        "selector_boton_aplicar":    "a#apply_bottom, a#apply_bottom_short, a.js-go-to-apply",
        "selector_siguiente_pagina": None,
        "selector_titulo_oferta":    "h1.gb-landing-cover__title, h1[class*='title'], h1",
        "tipo_postulacion":          "externa",
        "max_offers_per_run":        15,
        "requires_login":            True,   # Siempre verificar sesión antes de buscar
    },
    "chiletrabajos": {
        # ChileTrabajos requiere cuenta gratuita para postular.
        # La sesión se guarda en sessions/chiletrabajos/ automáticamente.
        "url_busqueda": (
            f"https://www.chiletrabajos.cl/empleos?q={ENCODED_KEYWORDS}"
            f"&experiencia=sin-experiencia"
        ),
        "selector_oferta":           "div.job-item",
        "selector_ubicacion":        ".job-location, .location, span[class*='location']",
        "selector_boton_aplicar":    "a.postular, a[href*='/trabajo/postular/'], a:has-text('Postular'), button:has-text('Postular')",
        "selector_siguiente_pagina": "a[rel='next'], a[data-ci-pagination-page]",
        "selector_titulo_oferta":    "h1.job-title, h1[class*='title'], h1",
        "tipo_postulacion":          "directa",
        "max_offers_per_run":        20,
        "requires_login":            True,
    },
    "laborum": {
        # Laborum usa API interna (/api/avisos/searchV2) para extraer ofertas.
        # La URL solo sirve para inicializar cookies de sesión — NO usar /busqueda?q=
        # ya que Laborum redirige esa ruta a 404. Usar la home directamente.
        "url_busqueda": "https://www.laborum.cl",
        "selector_oferta":           "a[href*='/empleos/'][class*='sc-']",
        "selector_ubicacion":        "span[class*='location'], span[class*='Location'], p[class*='location']",
        "selector_boton_aplicar":    "button:has-text('Postularme'), button:has-text('Postular'), button:has-text('Postulación rápida')",
        "selector_siguiente_pagina": "a[aria-label*='iguiente'], button[aria-label*='iguiente']",
        "selector_titulo_oferta":    "h1",
        "tipo_postulacion":          "directa",
        "max_offers_per_run":        15,
        "requires_login":            True,
    },

    # ── Portales remotos internacionales ──────────────────────────────────────
    # Sin login, postulación siempre externa al ATS de la empresa.
    # Filtros de horario/zona NO aplican (son 100% remote worldwide).

    "weworkremotely": {
        # El portal de trabajo remoto más grande del mundo (~200k visitas/mes).
        # HTML estático (no SPA) — selectores muy estables.
        # Categoría IT: /categories/remote-programming-jobs
        # Búsqueda: /remote-jobs/search?term=<keyword>
        "url_busqueda":              "https://weworkremotely.com/remote-jobs/search?term=junior+developer",
        "selector_oferta":           "section.jobs li a[href*='/remote-jobs/']",
        "selector_boton_aplicar":    "a.button:has-text('Apply'), a:has-text('Apply for this Job')",
        "selector_siguiente_pagina": None,
        "selector_titulo_oferta":    "h2.listing-header-container, h2[class*='listing'], h1",
        "tipo_postulacion":          "externa",
        "max_offers_per_run":        20,
        "requires_login":            False,
        "remote_intl":               True,   # skip filtros de zona/horario Chile
        "lang":                      "en",
    },
    "remotive": {
        # Directorio curado 100% remoto, fuerte en IT/Dev/QA.
        # Next.js SSR — espera networkidle antes de extraer cards.
        # Categorías: /remote-jobs/software-dev  /remote-jobs/qa  /remote-jobs/devops-sysadmin
        "url_busqueda":              "https://remotive.com/remote-jobs/software-dev?query=junior+developer",
        "selector_oferta":           "li[data-id] a, a[href*='/remote-jobs/software-dev/']",
        "selector_boton_aplicar":    "a:has-text('Apply for this job'), a:has-text('Apply Now')",
        "selector_siguiente_pagina": None,   # scroll infinito en SPA — una sola página
        "selector_titulo_oferta":    "h1",
        "tipo_postulacion":          "externa",
        "max_offers_per_run":        20,
        "requires_login":            False,
        "remote_intl":               True,
        "lang":                      "en",
    },
    "remoteco": {
        # Curado por humanos, alta calidad, acepta LATAM explícitamente.
        # WordPress + WP Job Manager — HTML muy estable.
        # Marcado por región: "Worldwide" / "Latin America" / "USA Only" (se filtra USA-only).
        "url_busqueda":              "https://remote.co/remote-jobs/search/?search_keywords=junior+developer",
        "selector_oferta":           ".job_listing",
        "selector_boton_aplicar":    "a.application_button, a:has-text('Apply For Job')",
        "selector_siguiente_pagina": "a.next, a[rel='next']",
        "selector_titulo_oferta":    "h1.job_title, h1",
        "tipo_postulacion":          "externa",
        "max_offers_per_run":        15,
        "requires_login":            False,
        "remote_intl":               True,
        "lang":                      "en",
    },
    "trabajando": {
        # Trabajando.com — uno de los portales laborales más grandes de Chile.
        # Postulación externa: redirige al formulario del empleador.
        "url_busqueda": (
            f"https://www.trabajando.com/trabajo/{DASH_KEYWORDS}/"
            "?pais=chile&orden=fecha"
        ),
        "selector_oferta":           "div.item-trabajo a.item-title, li.job-card a",
        "selector_boton_aplicar":    "a:has-text('Postular'), button:has-text('Postular')",
        "selector_siguiente_pagina": "a[rel='next'], a.next-page",
        "selector_titulo_oferta":    "h1.title-offer, h1",
        "tipo_postulacion":          "external",
        "max_offers_per_run":        15,
        "requires_login":            False,
    },
    "infojobs": {
        # InfoJobs Chile — portal con ofertas formales, requiere cuenta.
        # Tipo redirect: modal de InfoJobs o redirige al empleador.
        "url_busqueda": (
            f"https://cl.infojobs.net/ofertas-trabajo/trabajo_{DASH_KEYWORDS}/"
            "?sortBy=PUBLICATION_DATE"
        ),
        "selector_oferta":           "li.ij-OfferCardContent, article.ij-OfferCard",
        "selector_boton_aplicar":    "a.btn-apply, button:has-text('Inscribirme')",
        "selector_siguiente_pagina": "a[data-testid='pagination-next'], a[rel='next']",
        "selector_titulo_oferta":    "h1.ij-OfferDetailHeader-title, h1",
        "tipo_postulacion":          "redirect",
        "max_offers_per_run":        15,
        "requires_login":            True,
    },
}

# Respetar USER_MAX_OFFERS del .env como límite global de postulaciones por búsqueda
_env_max = os.getenv("USER_MAX_OFFERS", "")
if _env_max.strip().isdigit():
    _global_max = int(_env_max.strip())
    for _p in SITE_CONFIG.values():
        _p["max_offers_per_run"] = min(_p.get("max_offers_per_run", 10), _global_max)

# ---------------------------------------------------------------------------
# Filtro de horario — SOLO AM / Lunes a Viernes
# ---------------------------------------------------------------------------
# Señales que CONFIRMAN turno AM/L-V -> nunca descartar
SCHEDULE_WHITELIST = frozenset({
    "lunes a viernes", "lunes-viernes", "l a v", "l-v",
    "horario am", "turno am", "turno mañana", "turno diurno",
    "jornada diurna", "horario diurno",
    "08:00", "08:30", "09:00",         # hora de inicio AM típica
    "horario de oficina", "horario normal",
})

# Señales que indican turno incompatible -> oferta descartada
SCHEDULE_BLACKLIST = frozenset({
    # Noche
    "turno noche", "nocturno", "nocturna", "nocturnos", "nocturnas",
    "guardia nocturna", "guardia noche", "turno noche a mañana",
    # Tarde / PM (excluir explícitamente turnos vespertinos)
    "turno tarde", "jornada tarde", "horario tarde",
    "turno pm", "horario pm",
    "turno tarde-noche", "tarde-noche",
    "vespertino", "vespertina",
    # Turnos rotativos
    "turno rotativo", "turnos rotativos", "rotativo", "rotativos",
    "rotativa", "rotativas", "turno variable", "turnos variables",
    "turno cambiante", "turno split",
    # Fines de semana
    "fines de semana", "fin de semana",
    "sábados y domingos", "sabados y domingos",
    "sábado y domingo", "sabado y domingo",
    "sábados", "domingos",
    # 24/7
    "24x7", "24/7",
    # Códigos de turnos nocturnos Chile
    "turno c", "turno d",
})


def schedule_ok(text: str) -> bool:
    """
    Retorna False si el texto de la oferta indica turno noche, rotativo
    o fin de semana. True en cualquier otro caso (neutral o AM confirmado).

    Se llama con el título + descripción de la oferta.
    """
    if not text:
        return True
    low = text.lower()
    # Si hay señal de whitelist, nunca descartar
    for phrase in SCHEDULE_WHITELIST:
        if phrase in low:
            return True
    # Si hay señal de blacklist, descartar
    for phrase in SCHEDULE_BLACKLIST:
        if phrase in low:
            return False
    return True   # sin señales -> incluir


# ---------------------------------------------------------------------------
# Prioridad geográfica — Santiago RM (Maipú + Pudahuel primeras)
# ---------------------------------------------------------------------------

# Tier 1 · score 10 · Maipú + Pudahuel + adyacentes inmediatos (< 5 km)
_LOC_T1 = frozenset({
    "maipú", "maipu",
    "pudahuel",
    "cerrillos",
    "lo prado",
    "bustos",
})

# Tier 2 · score 7 · Comunas anexas accesibles por metro/Alameda (5-15 km)
_LOC_T2 = frozenset({
    "estación central", "estacion central",
    "pedro aguirre cerda",
    "san miguel",
    "lo espejo",
    "la cisterna",
    "cerro navia",
    "quinta normal",
    "santiago centro",
})

# Tier R · score 9 · Remoto / Híbrido — sin traslado, siempre bienvenido
_LOC_REMOTE = frozenset({
    "remoto", "teletrabajo", "remote", "home office", "homeoffice",
    "trabajo desde casa", "trabajo a distancia", "híbrido", "hibrido",
})

# Tier 3 · score 4 · Dentro de Santiago RM pero lejos de Maipú (15-40 km)
# Accesibles por metro pero con traslado largo — se incluyen al final de la cola
_LOC_DISTANT_RM = frozenset({
    # Sur RM periférico (metro o bus frecuente)
    "san bernardo", "calera de tango", "padre hurtado",
    "peñaflor", "penalflor", "talagante", "el monte",
    "buin", "paine",
    # Oriente / nororiente — accesibles por Línea 1, 4 o 5
    "las condes", "lo barnechea", "la reina", "vitacura",
    "providencia", "ñuñoa", "nunoa",
    # Norte — Línea 2 o 3 (accesibles con trasbordo)
    "huechuraba", "recoleta", "independencia", "conchalí", "conchali",
    "quilicura", "renca",
    # Sur / suroriente — Línea 5 o 4
    "la florida", "puente alto",
    "san joaquín", "san joaquin", "macul", "peñalolén", "penalolen",
    "la granja", "el bosque",
})

# Tier FAR · score 2 · Fuera del Gran Santiago o sin transporte directo → RECHAZADAS
_LOC_FAR = frozenset({
    # Fuera del Gran Santiago
    "til til", "colina", "lampa", "melipilla",
    # Comunas sin metro y muy alejadas
    "la pintana", "san ramón", "san ramon",
    # Regiones fuera de Santiago RM
    "valparaíso", "valparaiso", "viña del mar", "concepción", "concepcion",
    "antofagasta", "iquique", "temuco", "puerto montt",
})


def location_score(text: str) -> int:
    """
    Retorna score según proximidad a Maipú dentro de Santiago RM.
      10 = Maipú / Pudahuel / adyacentes (< 5 km)
       9 = Remoto / Híbrido
       7 = Comunas anexas accesibles por metro (5-15 km)
       5 = Santiago genérico / sin info -> neutro
       4 = Dentro de RM pero lejos (15-40 km) -> incluido al final
       2 = Comunas lejanas o mal conectadas -> RECHAZADAS en engine.py
    """
    if not text:
        return 5
    low = text.lower()
    for place in _LOC_T1:
        if place in low:
            return 10
    for place in _LOC_REMOTE:
        if place in low:
            return 9
    for place in _LOC_T2:
        if place in low:
            return 7
    for place in _LOC_DISTANT_RM:
        if place in low:
            return 4
    for place in _LOC_FAR:
        if place in low:
            return 2
    # Santiago genérico o RM sin especificar -> neutro
    if any(k in low for k in ("metropolitana", "región", "rm", "santiago")):
        return 5
    return 5   # sin info -> neutro


# ---------------------------------------------------------------------------
# Filtro de experiencia — rechaza ofertas senior o que exigen años de experiencia
# ---------------------------------------------------------------------------
# Palabras que CONFIRMAN junior / 0-1 año -> nunca filtrar
_EXP_WHITELIST = frozenset({
    # Nivel explícito
    "junior", "jr.", " jr ", "trainee", "practicante", "práctica", "practica",
    "egresado", "recién titulado", "recien titulado", "recién egresado", "recien egresado",
    # Sin experiencia
    "sin experiencia", "no se requiere experiencia", "no experience",
    "sin exp requerida", "sin exp", "0 años de experiencia",
    "primer empleo", "primera experiencia",
    # Hasta 1 año — aceptable según criterio del usuario
    "entry level", "entry-level",
    "0 a 1 año", "0 a 1 años", "hasta 1 año", "hasta un año",
    "1 año de experiencia", "un año de experiencia",
    "1 a 2 años",   # el mínimo es 1 -> aplica con 1 año
    "deseable experiencia",  # no obligatoria
    "no excluyente",         # experiencia "no excluyente" = sin exp OK
})

# Substrings largos que indican nivel senior/directivo (seguros — no aparecen en jr)
_SENIOR_SUBSTRINGS = frozenset({
    "senior", "semi senior", "semi-senior", "semisenior",
    "tech lead", "líder técnico", "lider tecnico",
    "arquitecto de software", "architect",
    "jefe de proyecto", "jefe de área", "jefe de area",
    "gerente", "director de", "director de tecnología",
    "manager", "head of", "vp de",
    "con experiencia comprobada", "experiencia comprobable",
})

# Abreviaciones cortas — requieren word-boundary para no dar falsos positivos
_SENIOR_WORDS_EXACT = frozenset({"sr", "ssr", "lead", "cto", "cio", "cpo"})

# Patrones de años de experiencia que SUPERAN lo aceptable
import re as _re
_EXP_YEARS_PATTERN = _re.compile(
    # "3 años de experiencia" / "2+ años de experiencia" / "5 o más años"
    r'\b([2-9]|\d{2,})\s*(?:\+|o más|más de)?\s*años?\s*(?:de\s+)?experiencia'
    r'|'
    # "5+ años" / "3+ años" — el + solo ya implica experiencia requerida
    r'\b([2-9]|\d{2,})\s*\+\s*años?',
    _re.IGNORECASE,
)


def experience_ok(text: str) -> bool:
    """
    Retorna False si la oferta claramente requiere experiencia senior o años > 1.
    Retorna True si es junior/sin experiencia, o si no hay señal clara (beneficio de la duda).

    Reglas (en orden):
      1. Si hay palabra de whitelist (junior, sin experiencia…) -> True siempre.
      2. Si hay patrón "2+ años de experiencia" -> False.
      3. Si hay substring senior/directivo -> False.
      4. Si hay abreviación exacta (sr, ssr, lead…) con word-boundary -> False.
      5. Sin señales -> True (beneficio de la duda).
    """
    if not text:
        return True
    low = text.lower()

    # 1. Whitelist: junior/sin-experiencia confirma -> nunca rechazar
    for phrase in _EXP_WHITELIST:
        if phrase in low:
            return True

    # 2. Patrón de años excesivos
    if _EXP_YEARS_PATTERN.search(text):
        return False

    # 3. Substrings senior largos
    for w in _SENIOR_SUBSTRINGS:
        if w in low:
            return False

    # 4. Abreviaciones cortas con word-boundary
    for w in _SENIOR_WORDS_EXACT:
        if _re.search(r'\b' + _re.escape(w) + r'\b', low):
            return False

    return True  # sin señales -> incluir


# Patrones de practica/pasantia — si aparecen en titulo/card, saltar oferta
_PRACTICA_SUBSTRINGS = [
    # formas con "practicante/s"
    "practicante", "practicantes",
    # formas con "practica" como sustantivo
    "practica profesional", "practicas profesionales",
    "practica curricular", "practicas curriculares",
    "practica laboral", "periodo de practica",
    "alumno en practica", "alumno(a) en practica", "alumno/a en practica",
    "estudiante en practica",
    # pasantia
    "pasantia", "pasante", "pasantes",
    # ingles
    "internship", "intern ",
]


def practica_ok(text: str) -> bool:
    """
    Retorna False si la oferta es claramente una practica/pasantia.
    Retorna True si no hay senales de practica (incluir).

    El bot nunca debe postular a practicas, solo a empleos reales.
    """
    if not text:
        return True
    low = text.lower()
    # Normalizar: quitar tildes basicas para comparar
    low = (low.replace("a", "a").replace("e", "e")
              .replace("i", "i").replace("o", "o").replace("u", "u")
              .replace("\xe1", "a").replace("\xe9", "e").replace("\xed", "i")
              .replace("\xf3", "o").replace("\xfa", "u"))
    for phrase in _PRACTICA_SUBSTRINGS:
        if phrase in low:
            return False
    return True


# ---------------------------------------------------------------------------
# Filtro de contrato — rechaza part-time, plazo fijo, freelance
# El usuario quiere trabajo FIJO (contrato indefinido / jornada completa)
# ---------------------------------------------------------------------------
_CONTRACT_BLACKLIST = frozenset({
    # Contratos temporales / a plazo
    "a plazo fijo", "contrato a plazo", "plazo determinado",
    "contrato temporal", "trabajo temporal",
    "por temporada", "por campaña",
    # Tiempo parcial
    "part time", "part-time", "media jornada", "medio tiempo",
    "jornada parcial", "horas semanales",
    # Freelance / por proyecto
    "freelance", "por proyecto", "por obra",
    # Honorarios (Chile: trabajo independiente sin contrato)
    "honorarios",
})


def contract_ok(text: str) -> bool:
    """
    Retorna False si la oferta es claramente temporal, part-time o freelance.
    True si no hay señal (beneficio de la duda → incluir).
    """
    if not text:
        return True
    low = text.lower()
    for phrase in _CONTRACT_BLACKLIST:
        if phrase in low:
            return False
    return True


# Categorias/rubros que NO son IT — el bot solo busca trabajo IT/bodega
_OFF_TOPIC_SUBSTRINGS = [
    # RRHH / Recursos Humanos
    "rrhh", "recursos humanos", "recursos humano",
    "reclutamiento", "seleccion de personal", "selección de personal",
    "remuneraciones", "payroll", "gestion de personas",
    "jefe de rrhh", "analista de rrhh", "asistente de rrhh",
    "asistente rrhh", "coordinador de rrhh", "coordinadora de rrhh",
    "depto rrhh", "departamento rrhh",
    "bienestar laboral", "clima organizacional",
    # Contabilidad / Finanzas operativas
    "contabilidad", "contador", "contadora", "contable",
    "auditor", "auditoria", "finanzas corporativas", "tesorero",
    "facturacion", "facturación", "cobranza", "cuentas por pagar",
    "cuentas por cobrar", "conciliacion bancaria", "impuestos",
    "tributario", "tributaria", "balance", "estados financieros",
    # Administración general (no IT)
    "asistente administrativo", "asistente administrativa",
    "asistente de gerencia", "asistente ejecutivo", "asistente ejecutiva",
    "secretaria", "secretario ejecutivo", "recepcionista",
    "asistente legal", "asistente juridico",
    # Ventas / Comercial
    "ejecutivo de ventas", "ejecutiva de ventas",
    "vendedor", "vendedora", "asesor comercial", "asesora comercial",
    "ejecutivo comercial", "ejecutiva comercial",
    "agente de ventas", "promotor de ventas",
    # Marketing y publicidad
    "marketing", "community manager", "redes sociales", "social media",
    "publicidad", "diseñador grafico", "disenador grafico", "contenido digital",
    "paid media", "seo ", "sem ", "e-commerce manager",
    # Salud
    "enfermera", "enfermero", "tecnico en enfermeria", "auxiliar de enfermeria",
    "kinesiologo", "medico", "dentista", "farmaceutico", "nutricionista",
    "psicologo", "psicologa", "terapeuta", "fonoaudiologo",
    # Educacion
    "profesor", "profesora", "docente", "educador", "educadora", "pedagogia",
    # Construccion / Arquitectura
    "arquitecto", "arquitecta", "construccion civil", "ingeniero civil",
    "maestro mayor", "capataz", "jefe de obra",
    # Derecho
    "abogado", "abogada", "derecho laboral", "paralegal", "notario",
    # Comunicaciones / Medios
    "periodista", "comunicaciones", "locutor", "redactor",
    # Gastronomia
    "gastronomia", "chef ", "cocinero", "cocinera", "cocina",
    "garzón", "garzon", "bartender", "barista",
    # Transporte (excluir choferes, no bodega logística)
    "chofer de camion", "chofer camion", "conductor de camion",
    "taxista", "uber", "delivery conductor",
    # Prevención de riesgos / Seguridad laboral
    "prevencionista", "prevención de riesgo", "prevencion de riesgo",
    "experto en prevencion", "experto en prevención",
    "higiene y seguridad", "seguridad y salud", "salud ocupacional",
    "seguridad industrial", "seguridad laboral",
    "asesor de seguridad", "asesora de seguridad",
    "coordinador de seguridad", "coordinadora de seguridad",
    "jefe de prevenci", "encargado de prevenci",
    # Minería / Operaciones industriales (no IT)
    "operador de planta", "operador de maquinaria",
    "operador minero", "minero", "pirquinero",
    "mantencion industrial", "mantención industrial",
    "electricista industrial", "mecanico industrial",
    "soldador", "tornero", "fresador",
    # Retail / Atención al cliente (no IT)
    "cajero", "cajera", "promotor", "promotora",
    "reponedor", "reponedora", "repositor",
    "atención al cliente", "atencion al cliente",
    "servicio al cliente", "call center", "teleoperador",
    # Limpieza / Aseo
    "auxiliar de aseo", "auxiliar aseo", "aseo y ornato",
    "camarera", "camarero", "mucama",
    "conserje",
    # Seguridad privada (guardia, no TI)
    "guardia de seguridad", "guardia privado", "vigilante",
    "guardia nocturno",
    # Agricultura / Campo
    "agricultor", "temporero", "cosecha",
    "operario agricola", "operario agrícola",
    # Servicios personales
    "peluquero", "peluquera", "esteticista", "manicurista",
    "masajista", "cosmetologa",
]


def topic_ok(text: str) -> bool:
    """
    Retorna False si la oferta es de un rubro ajeno a IT/bodega.
    Retorna True si no hay senales de rubro no-IT (incluir).
    """
    if not text:
        return True
    low = (text.lower()
           .replace("\xe1", "a").replace("\xe9", "e").replace("\xed", "i")
           .replace("\xf3", "o").replace("\xfa", "u").replace("\xf1", "n"))
    for phrase in _OFF_TOPIC_SUBSTRINGS:
        if phrase in low:
            return False
    return True


# Palabras que confirman que la oferta es del área IT/tech
_IT_SIGNALS = frozenset({
    # Roles
    "desarrollador", "developer", "programador", "ingeniero de software",
    "analista programador", "analista de sistemas", "analista ti",
    "analista de datos", "analista bi", "analista sap", "analista erp",
    "analista funcional",
    "soporte tecnico", "soporte ti", "help desk", "mesa de ayuda",
    "tecnico informatica", "tecnico en informatica", "tecnico ti",
    "qa ", "tester", "quality assurance",
    "devops", "sre", "cloud",
    "egresado informatica", "egresado ti", "egresado sistemas",
    # Tecnologías en el título
    "python", "java ", "javascript", "sql", "php", "react",
    "angular", "node", ".net", "c#", "html", "css",
    "backend", "frontend", "fullstack", "full stack",
    "software", "sistemas", "informatica", "informatico",
    "ti ", " ti,", "(ti)", "tecnologia", "tecnologias",
    "web ", "app ", "mobile", "base de datos",
    "git", "linux", "docker", "aws", "azure",
    "erp", "sap", "wms",
})


def topic_ok_it(text: str) -> bool:
    """
    Versión estricta para scan: exige que el card/título contenga al menos
    una señal IT. Usa en run_scan_pass para evitar colar cargos genéricos
    como 'Asistente' o 'Analista' sin contexto tecnológico.

    Retorna True si hay señal IT, False si el texto es genérico/sin contexto IT.
    Si text está vacío, se beneficia la duda (True).
    """
    if not text:
        return True
    # Primero pasar por el filtro de exclusión estándar
    if not topic_ok(text):
        return False
    low = (text.lower()
           .replace("\xe1", "a").replace("\xe9", "e").replace("\xed", "i")
           .replace("\xf3", "o").replace("\xfa", "u").replace("\xf1", "n"))
    for signal in _IT_SIGNALS:
        if signal in low:
            return True
    # Sin señal IT confirmada -> rechazar en modo scan estricto
    return False


# ---------------------------------------------------------------------------
# Grupos de keywords para búsqueda atómica por cargo
# Cada grupo lanza una búsqueda independiente con su propio perfil de respuesta
# Definido DESPUÉS de SITE_CONFIG para que build_config_for_keyword pueda referenciarlo
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Generador de combinaciones — base × modificador de experiencia
# "sin experiencia" se añade siempre en la URL via build_config_for_keyword
# ---------------------------------------------------------------------------
def _gen_it(label: str, bases: list, mods=("junior", "sin experiencia"), scan=True):
    """Genera combinaciones base × modificador. Solo IT, solo junior/sin experiencia."""
    rows = []
    for base in bases:
        for mod in mods:
            rows.append({"label": label, "keyword": f"{base} {mod}", "mode": "it", "scan": scan})
    return rows


def _gen_bodega(label: str, bases: list, mods=("sin experiencia",), scan=True):
    """Genera combinaciones para bodega/logística. Modifier: sin experiencia."""
    rows = []
    for base in bases:
        for mod in mods:
            rows.append({"label": label, "keyword": f"{base} {mod}", "mode": "bodega", "scan": scan})
    return rows


KEYWORD_GROUPS = (
    # ── Desarrollo general ────────────────────────────────────────────────────
    _gen_it("Desarrollo", [
        "desarrollador", "programador", "developer",
        "ingeniero de software", "software developer",
    ]) +
    # ── Stack / Lenguajes ─────────────────────────────────────────────────────
    _gen_it("Stack", [
        "python", "javascript", "sql",
        "desarrollador web", "desarrollador backend",
        "desarrollador frontend", "desarrollador fullstack",
        "desarrollador react", "desarrollador node",
    ]) +
    # ── Analista ─────────────────────────────────────────────────────────────
    _gen_it("Analista", [
        "analista programador", "analista de sistemas",
        "analista funcional", "analista SAP", "analista ERP",
        "ingeniero en sistemas",
    ]) +
    # ── Datos / BI ────────────────────────────────────────────────────────────
    _gen_it("Datos", [
        "analista de datos", "analista BI", "data analyst",
        "analista de informacion",
    ]) +
    # ── Soporte / Helpdesk ────────────────────────────────────────────────────
    _gen_it("Soporte", [
        "soporte tecnico", "soporte TI", "help desk",
        "mesa de ayuda", "tecnico informatica",
    ]) +
    # ── QA / Testing ─────────────────────────────────────────────────────────
    _gen_it("QA", [
        "QA", "tester", "quality assurance",
    ]) +
    # ── Egresados — el modificador ES la identidad ────────────────────────────
    [
        {"label": "Egresado", "keyword": "egresado informatica",          "mode": "it", "scan": True},
        {"label": "Egresado", "keyword": "egresado TI",                   "mode": "it", "scan": True},
        {"label": "Egresado", "keyword": "egresado analista programador", "mode": "it", "scan": True},
        {"label": "Egresado", "keyword": "recien egresado sistemas",      "mode": "it", "scan": True},
        {"label": "Egresado", "keyword": "recien egresado informatica",   "mode": "it", "scan": True},
    ] +
    # ── Operario / Bodega / Logística — turno am, lunes a viernes, sin exp ────
    # Filtros de horario (schedule_ok) y contrato (contract_ok) se aplican en engine
    _gen_bodega("Bodega", [
        "operario bodega",
        "auxiliar bodega",
        "bodeguero",
        "operario logistica",
        "auxiliar logistica",
    ])
)


# ---------------------------------------------------------------------------
# GetOnBoard — mapa keyword -> slug URL
# GetOnBoard ignora el parámetro ?q= (SPA client-side). Las búsquedas reales
# usan URLs tipo /jobs-{slug} que sí devuelven resultados filtrados por el portal.
# Probado: cada slug devuelve ~100 ofertas reales del sector.
# ---------------------------------------------------------------------------
_GOB_SLUG_MAP: dict[str, str] = {
    # Desarrollo / Programación
    "desarrollador junior":           "jobs-desarrollador-junior",
    "developer junior":               "jobs-developer-junior",
    "programador junior":             "jobs-programador-junior",
    "analista programador junior":    "jobs-analista-programador",
    "analista de sistemas junior":    "jobs-analista-sistemas",
    "ingeniero en sistemas junior":   "jobs-ingeniero-sistemas",
    # Stack / Tecnología
    "python junior":                  "jobs-python-developer-junior",
    "sql junior":                     "jobs-sql-junior",
    "desarrollador web junior":       "jobs-desarrollador-web-junior",
    "desarrollador backend junior":   "jobs-backend-developer-junior",
    "desarrollador fullstack junior": "jobs-fullstack-developer-junior",
    "javascript junior":              "jobs-javascript-developer-junior",
    # Soporte / Helpdesk
    "soporte tecnico junior":         "jobs-soporte-tecnico-junior",
    "soporte ti junior":              "jobs-soporte-ti",
    "help desk junior":               "jobs-help-desk",
    "mesa de ayuda junior":           "jobs-mesa-de-ayuda",
    "tecnico informatica junior":     "jobs-tecnico-informatica",
    # Datos / BI
    "analista de datos junior":       "jobs-analista-datos",
    "analista bi junior":             "jobs-analista-bi",
    # Especialidades
    "analista sap junior":            "jobs-analista-sap",
    "analista erp junior":            "jobs-analista-erp",
    "analista funcional junior":      "jobs-analista-funcional",
    # QA / Testing
    "qa junior":                      "jobs-qa-junior",
    "tester junior":                  "jobs-tester-junior",
    # Egresados / Entrada
    "egresado informatica":           "jobs-desarrollador-junior",
    "egresado ti":                    "jobs-desarrollador-junior",
}

def _gob_slug_url(keyword: str) -> str:
    """Convierte un keyword a la URL slug correcta de GetOnBoard."""
    kw_low = keyword.strip().lower()
    if kw_low in _GOB_SLUG_MAP:
        slug = _GOB_SLUG_MAP[kw_low]
    else:
        # Fallback genérico: guiones en lugar de espacios
        slug = "jobs-" + kw_low.replace(" ", "-")
    return f"https://www.getonbrd.com/{slug}"


# ---------------------------------------------------------------------------
# Conversor de keywords Spanish → English para portales internacionales
# ---------------------------------------------------------------------------
_ES_TO_EN_KW: dict[str, str] = {
    # Desarrollo general
    "desarrollador":            "developer",
    "programador":              "programmer",
    "developer":                "developer",
    "ingeniero de software":    "software engineer",
    "software developer":       "software developer",
    # Stack / lenguajes
    "python":                   "python",
    "javascript":               "javascript",
    "sql":                      "sql",
    "desarrollador web":        "web developer",
    "desarrollador backend":    "backend developer",
    "desarrollador frontend":   "frontend developer",
    "desarrollador fullstack":  "fullstack developer",
    "desarrollador react":      "react developer",
    "desarrollador node":       "node.js developer",
    # Analista
    "analista programador":     "software developer",
    "analista de sistemas":     "systems analyst",
    "analista funcional":       "functional analyst",
    "analista sap":             "sap analyst",
    "analista erp":             "erp analyst",
    "ingeniero en sistemas":    "systems engineer",
    # Datos
    "analista de datos":        "data analyst",
    "analista bi":              "bi analyst",
    "data analyst":             "data analyst",
    # Soporte
    "soporte tecnico":          "technical support",
    "soporte ti":               "it support",
    "help desk":                "help desk",
    "mesa de ayuda":            "help desk",
    "tecnico informatica":      "it technician",
    # QA
    "qa":                       "qa engineer",
    "tester":                   "qa tester",
    "quality assurance":        "quality assurance",
    # Egresados → entry level
    "egresado informatica":     "junior developer",
    "egresado ti":              "junior it",
    "egresado analista programador": "junior software developer",
    "recien egresado sistemas": "entry level developer",
    "recien egresado informatica": "entry level developer",
}

def _to_en_keyword(keyword: str) -> str:
    """
    Convierte un keyword en español a su equivalente en inglés
    para portales internacionales (We Work Remotely, Remotive, Remote.co).
    Mantiene 'junior' / 'entry level' al final.
    """
    kw_low = keyword.strip().lower()
    # Buscar la coincidencia más larga primero
    best_en = None
    for es_term, en_term in sorted(_ES_TO_EN_KW.items(), key=lambda x: -len(x[0])):
        if es_term in kw_low:
            best_en = en_term
            break
    if best_en is None:
        # Fallback: limpiar término y agregar junior
        base = kw_low.replace("junior", "").replace("trainee", "").replace("sin experiencia", "").strip()
        best_en = base if base else "developer"
    # Agregar nivel de experiencia
    level = "junior" if "junior" in kw_low or "trainee" in kw_low else "entry level"
    # Evitar duplicados ("junior developer junior")
    if level in best_en.lower():
        return best_en
    return f"{best_en} {level}"


def build_config_for_keyword(portal_key: str, keyword: str) -> dict:
    """
    Devuelve una copia de SITE_CONFIG[portal_key] con la URL reconstruida
    para un keyword específico. Agrega 'junior sin experiencia' automáticamente.
    """
    config = copy.deepcopy(SITE_CONFIG[portal_key])
    kw_encoded = quote_plus(keyword)
    kw_dash    = keyword.replace(" ", "-")
    kw_low    = keyword.lower()
    has_junior = "junior" in kw_low
    is_bodega  = any(w in kw_low for w in ("bodega", "logistica", "operario", "auxiliar"))

    # Indeed: no duplicar "junior" si ya está en el keyword
    if is_bodega:
        exp_indeed = "+sin+experiencia"
    elif has_junior:
        exp_indeed = "+sin+experiencia"          # junior ya está en el keyword
    else:
        exp_indeed = "+junior+sin+experiencia"

    exp_suffix_ct      = "-sin-experiencia"
    exp_suffix_laborum = "-sin-experiencia" if is_bodega else "-seniority-junior-sin-experiencia"
    exp_ct_query       = "sin+experiencia"  if is_bodega else "junior+sin+experiencia"

    # Portales internacionales: keyword en inglés
    en_kw         = _to_en_keyword(keyword)
    en_kw_encoded = quote_plus(en_kw)

    url_map = {
        "indeed":        f"https://cl.indeed.com/jobs?q={kw_encoded}{exp_indeed}&l=Santiago%2C+Regi%C3%B3n+Metropolitana&radius=25&explvl=entry_level&sort=date",
        "computrabajo":  f"https://cl.computrabajo.com/trabajo-de-{kw_dash}{exp_suffix_ct}",
        # Laborum: solo necesita llegar a laborum.cl con sesión válida.
        # /busqueda?q= devuelve 404 — usar la home siempre.
        "laborum":       "https://www.laborum.cl",
        # ChileTrabajos: URL de búsqueda directa por keyword
        "chiletrabajos": f"https://www.chiletrabajos.cl/empleos?q={kw_encoded}&experiencia=sin-experiencia",
        # GetOnBoard: usa URLs tipo slug (/jobs-{slug}), no soporta ?q= ni seniority en URL.
        "getonyboard":   _gob_slug_url(keyword),
        "linkedin":      f"https://www.linkedin.com/jobs/search/?keywords={kw_encoded}&location=Santiago%2C+Regi%C3%B3n+Metropolitana%2C+Chile&f_AL=true&f_E=2%2C3",
        # Portales remotos internacionales — usar keyword en inglés
        "weworkremotely": f"https://weworkremotely.com/remote-jobs/search?term={en_kw_encoded}",
        "remotive":       f"https://remotive.com/remote-jobs/software-dev?query={en_kw_encoded}",
        "remoteco":       f"https://remote.co/remote-jobs/search/?search_keywords={en_kw_encoded}",
    }
    if portal_key in url_map:
        config["url_busqueda"] = url_map[portal_key]
    config["_keyword"] = keyword
    return config

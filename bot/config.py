import os
import copy
import unicodedata
from urllib.parse import quote_plus
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
load_dotenv()

# Keywords del usuario — limpiados para URLs y codificación
RAW_KEYWORDS      = os.getenv("USER_KEYWORDS", "it dev desarrollador bodega").replace("'", "").replace('"', "")
CLEAN_KEYWORDS    = RAW_KEYWORDS.lower().replace("sin experiencia", "").replace("lunes a viernes am", "").replace(",", " ").strip()
ENCODED_KEYWORDS  = quote_plus(CLEAN_KEYWORDS.replace("  ", " ").replace(" ", ", "))
DASH_KEYWORDS     = CLEAN_KEYWORDS.replace("  ", " ").replace(" ", "-")
# URL base (máx 4 palabras) — slug largo causa HTTP 400 en computrabajo/infojobs
SHORT_DASH_KEYWORDS = "-".join(CLEAN_KEYWORDS.split()[:4]) or DASH_KEYWORDS

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
    "salary":           os.getenv("USER_SALARY", "850000"),
    "years_exp":        os.getenv("USER_YEARS_EXP", "0"),
    "cover_letter":     os.getenv("USER_COVER_LETTER", ""),
    "availability":     os.getenv("USER_AVAILABILITY", "Inmediata"),
    "english_level":    os.getenv("USER_ENGLISH_LEVEL",
                            "Básico técnico. Leo documentación en inglés sin problemas; "
                            "comunicación oral limitada."),
    "work_mode":        os.getenv("USER_WORK_MODE", "Sí"),
    "laborum_email":    os.getenv("LABORUM_EMAIL", os.getenv("USER_EMAIL", "")),
    "laborum_password": os.getenv("LABORUM_PASSWORD", ""),
    # Campos de screening — también desde .env; defaults con datos reales del perfil
    "education":        os.getenv("USER_EDUCATION",
                            "Técnico de Nivel Superior en Analista Programador — "
                            "INACAP (egresado 2024). Formación en programación, bases de datos, "
                            "redes y desarrollo web."),
    "contact_info":     os.getenv("USER_CONTACT_INFO",
                            f"{os.getenv('USER_PHONE','')} | {os.getenv('USER_EMAIL','')}"),
    "excel_level":      os.getenv("USER_EXCEL_LEVEL",
                            "Excel nivel básico-intermedio: tablas, BUSCARV, fórmulas "
                            "condicionales (SI, SUMAR.SI). Score 6/10."),
    "bodega_exp":       os.getenv("USER_BODEGA_EXP",
                            "Exposición a operaciones de bodega: SAP WM, WMS, terminales RF, "
                            "picking, despacho y recepción de mercadería. Entorno STL Internacional."),
}

# ---------------------------------------------------------------------------
# Configuración de portales
# ---------------------------------------------------------------------------
SITE_CONFIG = {
    "linkedin": {
        "url_busqueda": (
            f"https://www.linkedin.com/jobs/search/?keywords={ENCODED_KEYWORDS}&location=Santiago%2C+Regi%C3%B3n+Metropolitana%2C+Chile&f_AL=true&sortBy=DD&f_TPR=r604800"
        ),
        "selector_oferta":          "li[data-occludable-job-id]",
        "selector_boton_aplicar":   "button.jobs-apply-button",
        "selector_siguiente_pagina": "button[aria-label='Ver más empleos']",
        "selector_titulo_oferta":   "h1.job-details-jobs-unified-top-card__job-title",
        "tipo_postulacion":         "modal",
        "max_offers_per_run":       30,
        "max_pages":                4,
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
        # ?ordenar=2 = más recientes primero → evita ver siempre las mismas ofertas ya aplicadas
        "url_busqueda": (
            f"https://cl.computrabajo.com/trabajo-de-{SHORT_DASH_KEYWORDS}?ordenar=2"
        ),
        "selector_oferta":           "article.box_offer",
        "selector_ubicacion":        "p.fs13, .p_ubic, span.fs13",
        "selector_boton_aplicar":    "a.btn_postular, button.btn_postular, a:has-text('Postularme'), a:has-text('Postular'), button:has-text('Postularme'), button:has-text('Postular'), button:has-text('Inscribirme'), a:has-text('Inscribirme'), button:has-text('Aplicar'), [data-qa='btn-apply']",
        "selector_siguiente_pagina": "a[title='Siguiente'], a[rel='next'], a[class*='next']",
        "selector_titulo_oferta":    "h1.title_offer, h1[class*='title'], h1",
        "tipo_postulacion":          "directa",
        "max_offers_per_run":        30,
        "max_pages":                 6,
        "requires_login":            True,
    },
    "getonyboard": {
        # GetOnBoard usa URLs tipo slug: /jobs-{keyword}
        # El parámetro ?q= es ignorado por el SPA — no sirve para filtrar.
        # Los filtros de seniority tampoco funcionan vía URL; se aplican en el bot.
        # URL por defecto para búsqueda manual / --portal getonyboard sin --multi-keyword:
        "url_busqueda": "https://www.getonbrd.com/jobs-desarrollador-junior",
        # GetOnBoard permite navegar ofertas SIN login (es un portal público).
        # tipo_postulacion="externa" → solo registra URLs, nunca envía formularios,
        # por lo que NO se necesita sesión para escanear. Login solo sería necesario
        # para postular directamente en el sitio (que el bot NO hace).
        # Selector actualizado: GetOnBoard usa hrefs /empleos/ para jobs en castellano
        # y /jobs/ para jobs en inglés. El selector antiguo (a.gb-results-list__item)
        # dejó de funcionar cuando renovaron su CSS.
        "selector_oferta":           "a[href*='/empleos/'], a[href*='getonbrd.com/jobs/']",
        "selector_boton_aplicar":    "a#apply_bottom, a#apply_bottom_short, a.js-go-to-apply",
        "selector_siguiente_pagina": None,
        "selector_titulo_oferta":    "h1.gb-landing-cover__title, h1[class*='title'], h1",
        "tipo_postulacion":          "externa",
        "max_offers_per_run":        30,
        "max_pages":                 3,
        "requires_login":            True,
        "enabled":                   True,
    },
    "chiletrabajos": {
        # ChileTrabajos requiere cuenta gratuita para postular.
        # La sesión se guarda en sessions/chiletrabajos/ automáticamente.
        # &ordenar=recientes = más recientes primero
        "url_busqueda": (
            f"https://www.chiletrabajos.cl/empleos?q={ENCODED_KEYWORDS}"
            f"&region=13&ordenar=recientes"
        ),
        "selector_oferta":           "div.job-item",
        "selector_ubicacion":        ".job-location, .location, span[class*='location']",
        "selector_boton_aplicar":    "a.postular, a[href*='/trabajo/postular/'], a:has-text('Postular'), button:has-text('Postular')",
        "selector_siguiente_pagina": "a[rel='next'], a[data-ci-pagination-page]",
        "selector_titulo_oferta":    "h1.job-title, h1[class*='title'], h1",
        "tipo_postulacion":          "directa",
        "max_offers_per_run":        50,
        "max_pages":                 8,
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
        "max_offers_per_run":        30,
        "max_pages":                 4,
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
        "max_offers_per_run":        30,
        "requires_login":            False,
        "remote_intl":               True,
        "lang":                      "en",
    },
    "remotive": {
        # Directorio curado 100% remoto, fuerte en IT/Dev/QA.
        # URL 2025: /remote-jobs/software-development (slug cambió de software-dev)
        # Selectores actualizados: li.tw-cursor-pointer + a.remotive-url-visit
        "url_busqueda":              "https://remotive.com/remote-jobs/software-development",
        "selector_oferta":           "li.tw-cursor-pointer a.remotive-url-visit, a[class*='remotive-url-visit']",
        "selector_boton_aplicar":    "a:has-text('Apply for this job'), a:has-text('Apply Now'), a[class*='apply']",
        "selector_siguiente_pagina": None,   # scroll infinito en SPA — una sola página
        "selector_titulo_oferta":    "h1",
        "tipo_postulacion":          "externa",
        "max_offers_per_run":        30,
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
        "max_offers_per_run":        30,
        "max_pages":                 3,
        "requires_login":            False,
        "remote_intl":               True,
        "lang":                      "en",
    },
    "trabajando": {
        # Trabajando.cl — portal laboral chileno.
        # URL de búsqueda: /trabajo-empleo/{keyword-slug}
        # Links reales de ofertas: /trabajo/{id-slug} (confirmado en vivo 2026-06-09)
        "url_busqueda": (
            f"https://www.trabajando.cl/trabajo-empleo/{SHORT_DASH_KEYWORDS}"
        ),
        "selector_oferta":           "a[href*='/trabajo-empleo/'][href*='/trabajo/']",
        "selector_boton_aplicar":    "a:has-text('Postular'), button:has-text('Postular'), a.btn-postular",
        "selector_siguiente_pagina": "a[rel='next'], a[aria-label='Siguiente'], li.next a",
        "selector_titulo_oferta":    "h1",
        "tipo_postulacion":          "form",
        "max_offers_per_run":        30,
        "max_pages":                 4,
        "requires_login":            True,
        "curriculum_url":            "https://www.trabajando.cl/mi-curriculum#/",
    },
    "infojobs": {
        # InfoJobs Chile — portal con ofertas formales, requiere cuenta.
        # Tipo redirect: modal de InfoJobs o redirige al empleador.
        "url_busqueda": (
            f"https://www.infojobs.net/trabajo/?q={SHORT_DASH_KEYWORDS}"
            "&sortBy=PUBLICATION_DATE"
        ),
        "selector_oferta": (
            # Tarjetas modernas InfoJobs Chile (2024-2025)
            "li.ij-OfferCardContent, "
            "article.ij-OfferCard, "
            "div[data-testid='offer-list-item'], "
            "li[data-testid='offer-list-item'], "
            "article[data-testid='offer-card'], "
            "div.IFCard, "
            "li.IFCard, "
            "div[class*='OfferCard'], "
            "li[class*='offer-item'], "
            "div[class*='offer-card'], "
            "a[href*='/empleos/oferta/']"
        ),
        "selector_boton_aplicar":    "a.btn-apply, button:has-text('Inscribirme')",
        "selector_siguiente_pagina": "a[data-testid='pagination-next'], a[rel='next'], li.next a",
        "selector_titulo_oferta":    "h1.ij-OfferDetailHeader-title, h1",
        "tipo_postulacion":          "redirect",
        "max_offers_per_run":        30,
        "max_pages":                 4,
        "requires_login":            True,
        "enabled":                   True,
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
    "noche/", "(noche)", " noche -", " noche,",   # título: "Bodega Noche/Ciudad"
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
    # "sábados" solo ELIMINADO — "lunes a sábados" = jornada normal 5.5 días
    # Solo rechazar si aparece junto a "domingos" o en contexto de fin de semana
    "domingos",
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

# Tier FAR · score 2 · Fuera del Gran Santiago, regiones u otro país → RECHAZADAS
# (salvo que el texto indique remoto/híbrido — _LOC_REMOTE se evalúa ANTES y gana)
_LOC_FAR = frozenset({
    # Fuera del Gran Santiago (dentro de RM pero sin transporte directo)
    "til til", "colina", "lampa", "melipilla",
    # Regiones — Norte
    "calama", "antofagasta", "iquique", "arica", "tocopilla",
    "atacama", "copiapó", "copiapo", "vallenar",
    "coquimbo", "la serena", "ovalle", "illapel",
    # Regiones — Valparaíso
    "valparaíso", "valparaiso", "viña del mar", "villa alemana",
    "quillota", "los andes", "san antonio", "san felipe",
    # Regiones — O'Higgins / Sur
    "rancagua", "san fernando", "pichilemu",
    "talca", "curicó", "curico", "linares", "cauquenes",
    "chillán", "chillan", "san carlos",
    "concepción", "concepcion", "talcahuano", "los angeles",
    "coronel", "lota", "arauco",
    # Regiones — Sur
    "temuco", "villarrica", "pucón", "pucon", "angol",
    "valdivia", "osorno", "la unión", "la union",
    "puerto montt", "puerto varas", "castro", "ancud",
    "coyhaique", "chile chico",
    "punta arenas", "puerto natales",
    # Perú/Argentina/exterior — ciudades
    "lima", "bogotá", "bogota", "buenos aires", "monterrey",
    "córdoba", "cordoba", "rosario", "mendoza", "medellín", "medellin",
    "cali", "guayaquil", "quito", "montevideo", "asunción", "asuncion",
    "ciudad de méxico", "ciudad de mexico", "cdmx", "guadalajara",
    "são paulo", "sao paulo", "rio de janeiro", "madrid", "barcelona",
    "miami", "panamá", "panama city",
    # Países — cualquier mención de país distinto a Chile sin "remoto" → fuera
    "perú", "peru", "argentina", "colombia", "ecuador", "uruguay",
    "paraguay", "venezuela", "bolivia", "brasil", "brazil",
    "méxico", "mexico", "españa", "espana", "estados unidos", "usa",
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
    # Rangos con "a" (espacio)
    "1 a 2 años", "1 a 2 anos",
    "2 a 3 años", "2 a 3 anos",
    "3 a 4 años", "3 a 4 anos",
    "3 a 5 años", "3 a 5 anos",   # usuario tiene 6 años exp total → puede aplicar
    "4 a 5 años", "4 a 5 anos",
    # Rangos con guión (ej: "3-5 años") — sin whitelist el regex detecta el "5" solo
    "1-2 años", "1-2 anos", "1-3 años", "1-3 anos",
    "2-3 años", "2-3 anos", "2-4 años", "2-4 anos",
    "3-4 años", "3-4 anos", "3-5 años", "3-5 anos",
    "4-5 años", "4-5 anos",
    # Hasta N años
    "hasta 2 años", "hasta 2 anos", "máximo 2 años", "maximo 2 anos",
    "hasta 3 años", "hasta 3 anos",
    "hasta 4 años", "hasta 4 anos",
    "deseable experiencia",
    "no excluyente",
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
    # Umbral: 5+ años → rechazar. 1-4 = aceptable (usuario tiene 6 años exp total)
    # a\xf1os = años con tilde; anos = sin tilde (ambas formas en portales CL)
    r'\b([5-9]|\d{2,})\s*(?:\+|o\s+m[aá]s|m[aá]s\s+de)?\s*a(?:ñ|n)os?\b'
    r'|'
    r'\b([5-9]|\d{2,})\s*\+\s*a(?:ñ|n)os?\b',
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
    "practica profesional", "práctica profesional", "practicas profesionales", "prácticas profesionales",
    "practica curricular", "práctica curricular", "practicas curriculares", "prácticas curriculares",
    "practica laboral", "práctica laboral", "periodo de practica", "periodo de práctica",
    "alumno en practica", "alumno(a) en practica", "alumno/a en practica",
    "estudiante en practica",
    # pasantia
    "pasantia", "pasantias", "pasantías", "pasante", "pasantes",
    # ingles
    "internship", "interns", "intern ",
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
    # Normalizar acentos/diacríticos para que las variantes se detecten siempre
    low = unicodedata.normalize("NFKD", low)
    low = "".join(ch for ch in low if not unicodedata.combining(ch))
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
    "jornada parcial",
    # "horas semanales" ELIMINADO — "44 horas semanales" es jornada completa estándar en Chile
    # Freelance / por proyecto estricto (evitar falso positivo en "proyecto TI")
    "freelance", "contrato por proyecto", "por obra",
    # "honorarios" ELIMINADO — muchas empresas TI en Chile contratan bajo honorarios (boleta)
    # "por proyecto" ELIMINADO — "proyecto de transformación digital" daba falso positivo
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
    # Licencias de conducir requeridas (clase B/D = vehículos/transporte)
    "licencia clase b", "licencia clase d", "licencia b", "licencia d",
    "licencia conducir clase b", "licencia conducir clase d",
    "con licencia clase b", "con licencia clase d",
    "profesional de bodega con licencia",
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
    "anfitrion", "anfitrión", "anfitriona",   # host de retail/eventos
    # Ventas sin "de ventas" explícito
    "ejecutivo venta", "ejecutiva venta",
    "agente de seguros", "ejecutivo seguros", "asesor de seguros",
    "asesor seguros", "ejecutivo en seguros",
    # Limpieza / Aseo
    "auxiliar de aseo", "auxiliar aseo", "aseo y ornato",
    "camarera", "camarero", "mucama",
    "conserje",
    # Seguridad privada (guardia, no TI)
    "guardia de seguridad", "guardia privado", "vigilante",
    "guardia nocturno",
    "guardia ",   # "Guardia Colina", "Guardia Express", etc. (con espacio para no cortar palabras)
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
def _it(label: str, keyword: str) -> dict:
    return {"label": label, "keyword": keyword, "mode": "it", "scan": True}


def _bodega(keyword: str) -> dict:
    return {"label": "Bodega", "keyword": keyword, "mode": "bodega", "scan": True}


# ---------------------------------------------------------------------------
# Keywords — lean set optimizado para postulación en Chile (25 keywords)
# Un solo modificador por base: "junior" para IT, "sin experiencia" para bodega
# ---------------------------------------------------------------------------
KEYWORD_GROUPS = [
    # ── Desarrollo / Programación ─────────────────────────────────────────────
    _it("Desarrollo",  "desarrollador junior"),
    _it("Desarrollo",  "programador junior"),
    _it("Desarrollo",  "desarrollador web junior"),
    _it("Desarrollo",  "desarrollador fullstack junior"),
    _it("Desarrollo",  "desarrollador backend junior"),

    # ── Stack técnico (CV: Python, SQL, JavaScript) ───────────────────────────
    _it("Stack",       "python junior"),
    _it("Stack",       "javascript junior"),
    _it("Stack",       "sql junior"),

    # ── Analista — título exacto del CV ──────────────────────────────────────
    _it("Analista",    "analista programador junior"),
    _it("Analista",    "analista de sistemas junior"),
    _it("Analista",    "analista SAP junior"),        # CV: SAP WM
    _it("Analista",    "analista WMS junior"),        # CV: WMS en STL/Ripley

    # ── Datos ─────────────────────────────────────────────────────────────────
    _it("Datos",       "analista de datos junior"),
    _it("Datos",       "data analyst junior"),

    # ── Soporte — cargo más ofertado entry-level IT en Chile ──────────────────
    _it("Soporte",     "soporte tecnico junior"),
    _it("Soporte",     "help desk junior"),
    _it("Soporte",     "soporte TI junior"),

    # ── QA / Testing ─────────────────────────────────────────────────────────
    _it("QA",          "tester junior"),
    _it("QA",          "QA junior"),

    # ── Egresados ─────────────────────────────────────────────────────────────
    _it("Egresado",    "egresado informatica"),
    _it("Egresado",    "egresado analista programador"),
    _it("Egresado",    "egresado TI"),

    # ── Bodega / Logística (CV: STL, Natura, Total Tools, Ripley) ────────────
    _bodega("auxiliar bodega sin experiencia"),
    _bodega("bodeguero sin experiencia"),
    _bodega("auxiliar picking sin experiencia"),
    _bodega("auxiliar logistica sin experiencia"),
    _bodega("operario bodega sin experiencia"),
    _bodega("bodeguero"),
    _bodega("auxiliar de bodega"),
    _bodega("operario logistica"),
    _bodega("auxiliar de despacho"),
    _bodega("receptor de mercaderia"),
    _bodega("operario picking packing"),
    _bodega("auxiliar de almacen"),
    _bodega("peon de bodega"),
    _bodega("ayudante de bodega"),
    _bodega("auxiliar logistica bodega"),

    # ── Términos más buscados en portales chilenos (sin "junior" — más resultados) ──
    {"keyword": "tecnico informatico",     "label": "Analista", "mode": "it"},
    {"keyword": "programador",             "label": "Desarrollo", "mode": "it"},
    {"keyword": "soporte tecnico",         "label": "Soporte", "mode": "it"},
    {"keyword": "help desk",               "label": "Soporte", "mode": "it"},
    {"keyword": "desarrollador",           "label": "Desarrollo", "mode": "it"},
    {"keyword": "analista programador",    "label": "Analista", "mode": "it"},
    {"keyword": "practicante informatica", "label": "Egresado", "mode": "it"},
    {"keyword": "trainee TI",              "label": "Egresado", "mode": "it"},
    {"keyword": "operador de sistemas",    "label": "Soporte", "mode": "it"},
    {"keyword": "digitador",               "label": "Soporte", "mode": "it"},
]


# ---------------------------------------------------------------------------
# GetOnBoard — mapa keyword -> slug URL
# GetOnBoard ignora el parámetro ?q= (SPA client-side). Las búsquedas reales
# usan URLs tipo /jobs-{slug} que sí devuelven resultados filtrados por el portal.
# Probado: cada slug devuelve ~100 ofertas reales del sector.
# ---------------------------------------------------------------------------
_GOB_SLUG_MAP: dict[str, str] = {
    # Desarrollo
    "desarrollador junior":           "jobs-desarrollador-junior",
    "programador junior":             "jobs-programador-junior",
    "desarrollador web junior":       "jobs-desarrollador-web-junior",
    "desarrollador fullstack junior": "jobs-fullstack-developer-junior",
    "desarrollador backend junior":   "jobs-backend-developer-junior",
    # Stack
    "python junior":                  "jobs-python-developer-junior",
    "javascript junior":              "jobs-javascript-developer-junior",
    "sql junior":                     "jobs-sql-junior",
    # Analista
    "analista programador junior":    "jobs-analista-programador",
    "analista de sistemas junior":    "jobs-analista-sistemas",
    "analista sap junior":            "jobs-analista-sap",
    "analista wms junior":            "jobs-analista-sap",   # slug más cercano en GOB
    # Datos
    "analista de datos junior":       "jobs-analista-datos",
    "data analyst junior":            "jobs-data-analyst",
    # Soporte
    "soporte tecnico junior":         "jobs-soporte-tecnico-junior",
    "help desk junior":               "jobs-help-desk",
    "soporte ti junior":              "jobs-soporte-ti",
    # QA
    "tester junior":                  "jobs-tester-junior",
    "qa junior":                      "jobs-qa-junior",
    # Egresados
    "egresado informatica":           "jobs-desarrollador-junior",
    "egresado analista programador":  "jobs-analista-programador",
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

    has_sin_exp        = "sin experiencia" in kw_low
    exp_suffix_ct      = "" if has_sin_exp else "-sin-experiencia"   # evitar doble sin-experiencia
    exp_suffix_laborum = "-sin-experiencia" if is_bodega else "-seniority-junior-sin-experiencia"
    exp_ct_query       = "sin+experiencia"  if is_bodega else "junior+sin+experiencia"

    # Portales internacionales: keyword en inglés
    en_kw         = _to_en_keyword(keyword)
    en_kw_encoded = quote_plus(en_kw)

    url_map = {
        "indeed":        f"https://cl.indeed.com/jobs?q={kw_encoded}{exp_indeed}&l=Santiago%2C+Regi%C3%B3n+Metropolitana&radius=25&explvl=entry_level&sort=date",
        # ordenar=2 (recientes) | provincia=Santiago
        # wt=1 eliminado — puede disparar Cloudflare WAF en Playwright
        "computrabajo":  f"https://cl.computrabajo.com/trabajo-de-{kw_dash}{exp_suffix_ct}-en-santiago-de-chile?ordenar=2",
        # Laborum: home con sesión válida — la API recibe Region=13 en filtros
        "laborum":       "https://www.laborum.cl",
        # ChileTrabajos: región 13 (RM) + jornada completa (jc=1) + horario AM (h=1)
        "chiletrabajos": f"https://www.chiletrabajos.cl/empleos?q={kw_encoded}&region=13&ordenar=recientes&jornada=1",
        # GetOnBoard: usa URLs tipo slug (/jobs-{slug}), no soporta ?q= ni seniority en URL.
        "getonyboard":   _gob_slug_url(keyword),
        "linkedin":      f"https://www.linkedin.com/jobs/search/?keywords={kw_encoded}&location=Santiago%2C+Regi%C3%B3n+Metropolitana%2C+Chile&f_AL=true&sortBy=DD&f_TPR=r604800",
        # Trabajando.cl: nueva URL slug /trabajo-empleo/{keyword-slug}
        "trabajando":    f"https://www.trabajando.cl/trabajo-empleo/{kw_dash}",
        # InfoJobs Chile: búsqueda por keyword ordenada por fecha
        "infojobs":      f"https://www.infojobs.net/trabajo/?q={kw_encoded}&sortBy=PUBLICATION_DATE",
        # Portales remotos internacionales — usar keyword en inglés
        "weworkremotely": f"https://weworkremotely.com/remote-jobs/search?term={en_kw_encoded}",
        "remotive":       f"https://remotive.com/remote-jobs/software-development",
        "remoteco":       f"https://remote.co/remote-jobs/search/?search_keywords={en_kw_encoded}",
    }
    if portal_key in url_map:
        config["url_busqueda"] = url_map[portal_key]
    config["_keyword"] = keyword
    return config

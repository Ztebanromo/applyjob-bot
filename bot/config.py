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
    "full_name":     os.getenv("USER_FULL_NAME", "Ignacio Romo"),
    "first_name":    os.getenv("USER_FIRST_NAME", "Ignacio"),
    "last_name":     os.getenv("USER_LAST_NAME", "Romo"),
    "email":         os.getenv("USER_EMAIL", "ygnacio1698@gmail.com"),
    "phone":         os.getenv("USER_PHONE", "+56 9 3420 0859"),
    "phone_number":  os.getenv("USER_PHONE_NUMBER", "934200859"),
    "country_code":  os.getenv("USER_COUNTRY_CODE", "+56"),
    "country":       os.getenv("USER_COUNTRY", "Chile"),
    "city":          os.getenv("USER_CITY", "Maipú, Región Metropolitana de Santiago, Chile"),
    "linkedin":      os.getenv("USER_LINKEDIN", "https://www.linkedin.com/in/ignacio-romo-dev"),
    "portfolio":     os.getenv("USER_PORTFOLIO", "https://github.com/Ztebanromo"),
    "cv_path":       os.getenv("USER_CV_PATH", "C:/Users/ygnac/OneDrive/Documentos/cv-ignacio-romo.pdf"),
    "salary":        os.getenv("USER_SALARY", "850000"),
    "years_exp":     os.getenv("USER_YEARS_EXP", "0"),
    "cover_letter":  os.getenv("USER_COVER_LETTER", (
        "Soy Analista Programador egresado de INACAP con 4 años de exposición directa a sistemas empresariales "
        "(SAP modulo WM, WMS, terminales RF) en empresas de alta demanda como STL Internacional, Natura y Ripley. "
        "Cuento con conocimientos en Python, SQL y desarrollo web. Orientado a automatización e integridad de datos, "
        "busco mi primera oportunidad formal en TI para aportar desde el primer día."
    )),
    "availability":  os.getenv("USER_AVAILABILITY", "Inmediata"),
    "english_level": os.getenv("USER_ENGLISH_LEVEL", "Básico técnico"),
    "work_mode":     os.getenv("USER_WORK_MODE", "Sí"),
    "laborum_email": os.getenv("LABORUM_EMAIL", os.getenv("USER_EMAIL")),
    "laborum_password": os.getenv("LABORUM_PASSWORD", ""),
}

# ---------------------------------------------------------------------------
# Configuración de portales
# ---------------------------------------------------------------------------
SITE_CONFIG = {
    "linkedin": {
        "url_busqueda": (
            f"https://www.linkedin.com/jobs/search/?keywords={ENCODED_KEYWORDS}&location=Chile&f_AL=true&f_E=2%2C3"
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
        # ── STANDBY ─────────────────────────────────────────────────────────
        # Indeed usa Cloudflare Turnstile que detecta Playwright Chromium
        # en ~3 segundos. Requiere Chrome real con CDP (iniciar_bot.bat).
        # Pendiente: migrar a patchright / camoufox para bypass de CF.
        # Cambiar INDEED_ENABLED=true en .env cuando esté listo.
        "enabled": os.getenv("INDEED_ENABLED", "false").lower() == "true",
        # ────────────────────────────────────────────────────────────────────
        "url_busqueda": (
            f"https://cl.indeed.com/jobs?q={ENCODED_KEYWORDS}"
            f"&l=Maip%C3%BA%2C+Regi%C3%B3n+Metropolitana"
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
        "requires_login":            False,
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
        "requires_login":            False,
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
        "requires_login":            False,
    },
    "laborum": {
        # Laborum usa API interna (/api/avisos/searchV2) para extraer ofertas.
        # La URL de búsqueda solo sirve para inicializar la sesión en el navegador.
        "url_busqueda": "https://www.laborum.cl/busqueda",
        "selector_oferta":           "a[href*='/empleos/'][class*='sc-']",
        "selector_ubicacion":        "span[class*='location'], span[class*='Location'], p[class*='location']",
        "selector_boton_aplicar":    "button:has-text('Postularme'), button:has-text('Postular'), button:has-text('Postulación rápida')",
        "selector_siguiente_pagina": "a[aria-label*='iguiente'], button[aria-label*='iguiente']",
        "selector_titulo_oferta":    "h1",
        "tipo_postulacion":          "directa",
        "max_offers_per_run":        15,
        "requires_login":            True,
    },
}

# ---------------------------------------------------------------------------
# Filtro de horario — solo AM / Lunes a Viernes
# ---------------------------------------------------------------------------
# Palabras que indican turno incompatible → oferta descartada
SCHEDULE_BLACKLIST = frozenset({
    "turno noche", "nocturno", "nocturna", "nocturnos", "nocturnas",
    "turno rotativo", "turnos rotativos", "turno rotativo",
    "rotativo", "rotativos", "rotativa", "rotativas",
    "fines de semana", "fin de semana",
    "sábados y domingos", "sabados y domingos",
    "sábado y domingo",  "sabado y domingo",
    "sábados", "domingos",
    "24x7", "24/7",
    "guardia nocturna", "guardia noche",
    "turno tarde-noche", "tarde-noche",
    "turno noche a mañana",
    "vespertino", "vespertina",
    "turno c", "turno d",        # nombres comunes de turnos noche en Chile
})

# Palabras que CONFIRMAN turno AM / L-V (oferta prioritaria, nunca se filtra)
SCHEDULE_WHITELIST = frozenset({
    "lunes a viernes", "lunes-viernes", "l a v", "l-v",
    "horario am", "turno am", "turno mañana", "turno diurno",
    "jornada diurna", "horario diurno",
    "08:00", "08:30", "09:00",   # inicio de turno AM típico
    "jornada completa",           # generalmente L-V en oficina
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
    return True   # sin señales → incluir


# ---------------------------------------------------------------------------
# Prioridad geográfica — comunas cercanas a Maipú
# ---------------------------------------------------------------------------
# Tier 1 · < 5 km — adyacentes directos
_LOC_T1 = frozenset({
    "maipú", "maipu", "cerrillos", "pudahuel", "lo prado", "bustos",
})
# Tier 2 · 5-20 km — bien conectados por metro / Alameda
_LOC_T2 = frozenset({
    "estación central", "estacion central",
    "pedro aguirre cerda", "san miguel", "lo espejo", "la cisterna",
    "cerro navia", "quinta normal", "santiago", "santiago centro",
    "san bernardo", "calera de tango", "padre hurtado",
    "peñaflor", "penalflor", "talagante", "el monte",
})
# Tier R · Remoto — sin traslado, siempre bienvenido
_LOC_REMOTE = frozenset({
    "remoto", "teletrabajo", "remote", "home office", "homeoffice",
    "trabajo desde casa", "trabajo a distancia", "híbrido", "hibrido",
})
# Tier 4 · Lejos o mal conectados — al final de la cola
_LOC_FAR = frozenset({
    "vitacura", "las condes", "lo barnechea", "la reina",
    "huechuraba", "recoleta", "independencia", "conchalí", "conchali",
    "quilicura", "renca", "la florida", "puente alto", "la pintana",
    "san joaquín", "san joaquin", "macul", "peñalolén", "penalolen",
    "la granja", "el bosque", "ñuñoa", "nunoa", "providencia",
    "til til", "colina", "lampa", "melipilla", "buin", "paine",
})


def location_score(text: str) -> int:
    """
    Retorna 0-10 según proximidad a Maipú.
    10 = adyacente, 9 = remoto, 7 = cercanía media, 5 = neutro/RM, 2 = lejos.
    Se llama con el texto completo del card de oferta.
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
    for place in _LOC_FAR:
        if place in low:
            return 2
    # RM genérico / Santiago sin especificar comuna → neutro
    if any(k in low for k in ("metropolitana", "región", "rm", "santiago")):
        return 5
    return 5   # sin info → neutro


# ---------------------------------------------------------------------------
# Grupos de keywords para búsqueda atómica por cargo
# Cada grupo lanza una búsqueda independiente con su propio perfil de respuesta
# Definido DESPUÉS de SITE_CONFIG para que build_config_for_keyword pueda referenciarlo
# ---------------------------------------------------------------------------
KEYWORD_GROUPS = [
    {"label": "IT",     "keyword": "desarrollador junior",        "mode": "it"},
    {"label": "IT",     "keyword": "analista programador junior",  "mode": "it"},
    {"label": "IT",     "keyword": "python junior",               "mode": "it"},
    {"label": "IT",     "keyword": "soporte tecnico junior",      "mode": "it"},
    {"label": "Bodega", "keyword": "operario bodega",             "mode": "bodega"},
    {"label": "Bodega", "keyword": "auxiliar bodega",             "mode": "bodega"},
    {"label": "Bodega", "keyword": "auxiliar logistica",          "mode": "bodega"},
]


# ---------------------------------------------------------------------------
# GetOnBoard — mapa keyword → slug URL
# GetOnBoard ignora el parámetro ?q= (SPA client-side). Las búsquedas reales
# usan URLs tipo /jobs-{slug} que sí devuelven resultados filtrados por el portal.
# Probado: cada slug devuelve ~100 ofertas reales del sector.
# ---------------------------------------------------------------------------
_GOB_SLUG_MAP: dict[str, str] = {
    "desarrollador junior":        "jobs-desarrollador-junior",
    "analista programador junior": "jobs-analista-programador",
    "python junior":               "jobs-python-developer-junior",
    "soporte tecnico junior":      "jobs-soporte-tecnico-junior",
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

    url_map = {
        "indeed":        f"https://cl.indeed.com/jobs?q={kw_encoded}{exp_indeed}&l=Maip%C3%BA%2C+Regi%C3%B3n+Metropolitana&radius=25&explvl=entry_level&sort=date",
        "computrabajo":  f"https://cl.computrabajo.com/trabajo-de-{kw_dash}{exp_suffix_ct}",
        # Laborum: URL secundaria — el portal usa la API interna directamente.
        # Solo necesita llegar a laborum.cl con sesión válida.
        "laborum":       f"https://www.laborum.cl/busqueda?q={kw_encoded}",
        # ChileTrabajos: URL de búsqueda directa por keyword
        "chiletrabajos": f"https://www.chiletrabajos.cl/empleos?q={kw_encoded}&experiencia=sin-experiencia",
        # GetOnBoard: usa URLs tipo slug (/jobs-{slug}), no soporta ?q= ni seniority en URL.
        # El mapa cubre exactamente los keywords IT del KEYWORD_GROUPS.
        # El filtro de seniority se aplica en el bot (getonyboard.py) leyendo el texto del card.
        "getonyboard":   _gob_slug_url(keyword),
        "linkedin":      f"https://www.linkedin.com/jobs/search/?keywords={kw_encoded}&location=Chile&f_AL=true&f_E=2%2C3",
    }
    if portal_key in url_map:
        config["url_busqueda"] = url_map[portal_key]
    return config

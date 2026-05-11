import os
from urllib.parse import quote_plus
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
load_dotenv()

# --- Configuración dinámica de Keywords ---
RAW_KEYWORDS = os.getenv("USER_KEYWORDS", "Desarrollador Programador")
ENCODED_KEYWORDS = quote_plus(RAW_KEYWORDS)
# Para portales que usan guiones (ej: laborum)
DASH_KEYWORDS = RAW_KEYWORDS.replace(" ", "-").lower()

# ---------------------------------------------------------------------------
# Perfil del usuario — se usa para autocompletar formularios
# ---------------------------------------------------------------------------
USER_PROFILE = {
    "full_name":     os.getenv("USER_FULL_NAME", "Ignacio Romo"),
    "first_name":    os.getenv("USER_FIRST_NAME", "Ignacio"),
    "last_name":     os.getenv("USER_LAST_NAME", "Romo"),
    "email":         os.getenv("USER_EMAIL", "ygnacio1698@gmail.com"),
    "phone":         os.getenv("USER_PHONE", "+56 934200859"),
    "phone_number":  os.getenv("USER_PHONE_NUMBER", "934200859"),
    "country_code":  os.getenv("USER_COUNTRY_CODE", "+56"),
    "country":       os.getenv("USER_COUNTRY", "Chile"),
    "city":          os.getenv("USER_CITY", "Región Metropolitana de Santiago, Chile"),
    "linkedin":      os.getenv("USER_LINKEDIN", "https://www.linkedin.com/in/ignacio-romo-dev"),
    "portfolio":     os.getenv("USER_PORTFOLIO", "https://github.com/Ztebanromo"),
    "cv_path":       os.getenv("USER_CV_PATH", "C:/Users/ygnac/Downloads/files/cv-ignacio-romo.pdf"),
    "salary":        os.getenv("USER_SALARY", "850.000"),
    "years_exp":     os.getenv("USER_YEARS_EXP", "0"),
    "cover_letter":  os.getenv("USER_COVER_LETTER", (
        "Soy Analista Programador recien egresado de INACAP, sin experiencia formal previa en TI. Cuento con conocimientos basicos en Python, SQL y desarrollo web adquiridos durante mi formacion. Tuve exposicion directa a sistemas empresariales como SAP y WMS en roles no TI. Busco mi primera oportunidad en el area donde pueda aprender y aportar desde el primer dia."
    )),
    "availability":  os.getenv("USER_AVAILABILITY", "Inmediata"),
    "english_level": os.getenv("USER_ENGLISH_LEVEL", "Básico"),
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
        "max_offers_per_run":       20,
        "requires_login":           True,
    },
    "indeed": {
        "url_busqueda": (
            f"https://cl.indeed.com/jobs?q={ENCODED_KEYWORDS}&l=Chile&explvl=entry_level&sort=date"
        ),
        "selector_oferta":          "div.job_seen_beacon",
        "selector_boton_aplicar":   "button#indeedApplyButton",
        "selector_siguiente_pagina": "a[data-testid='pagination-page-next']",
        "selector_titulo_oferta":   "h2.jobTitle",
        "tipo_postulacion":         "modal",
        "max_offers_per_run":       20,
        "requires_login":           False,
    },
    "computrabajo": {
        "url_busqueda": (
            f"https://cl.computrabajo.com/trabajo-de-{DASH_KEYWORDS}-sin-experiencia"
        ),
        "selector_oferta":           "article.box_offer",
        "selector_boton_aplicar":    "a.btn_postular, a:has-text('Postularme'), a:has-text('Postular'), button:has-text('Postularme')",
        "selector_siguiente_pagina": "a[title='Siguiente'], a[rel='next'], a[class*='next']",
        "selector_titulo_oferta":    "h1.title_offer, h1[class*='title'], h1",
        "tipo_postulacion":          "directa",
        "max_offers_per_run":        20,
        "requires_login":            False,
    },
    "getonyboard": {
        "url_busqueda": (
            f"https://www.getonbrd.com/search/jobs?q={ENCODED_KEYWORDS}&seniority[]=no_experience&seniority[]=junior"
        ),
        "selector_oferta":          "a.gb-results-list__item",
        "selector_boton_aplicar":   "a#apply_bottom, a.js-go-to-apply",
        "selector_siguiente_pagina": None,
        "selector_titulo_oferta":   "h1.gb-landing-cover__title",
        "tipo_postulacion":         "externa",
        "max_offers_per_run":       20,
        "requires_login":           False,
    },
    "chiletrabajos": {
        "url_busqueda": (
            f"https://www.chiletrabajos.cl/trabajos/{ENCODED_KEYWORDS}?2=junior+sin+experiencia"
        ),
        "selector_oferta":           "div.job-item",
        "selector_boton_aplicar":    "a.postular, a[href*='/trabajo/postular/'], a:has-text('Postular'), button:has-text('Postular')",
        "selector_siguiente_pagina": "a[rel='next'], a[data-ci-pagination-page]",
        "selector_titulo_oferta":    "h1.job-title, h1[class*='title'], h1",
        "tipo_postulacion":          "directa",
        "max_offers_per_run":        20,
        "requires_login":            False,
    },
    "laborum": {
        "url_busqueda": (
            f"https://www.laborum.cl/empleos-busqueda-{DASH_KEYWORDS}-seniority-junior-sin-experiencia.html"
        ),
        "selector_oferta":          "a[href*='/empleos/'][class*='sc-']",
        "selector_boton_aplicar":   "button:has-text('Postularme'), button:has-text('Postular')",
        "selector_siguiente_pagina": "a[aria-label*='iguiente'], button[aria-label*='iguiente']",
        "selector_titulo_oferta":   "h1",
        "tipo_postulacion":         "directa",
        "max_offers_per_run":       20,
        "requires_login":           True,
    },
}



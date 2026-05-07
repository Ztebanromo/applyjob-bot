import os
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
load_dotenv()

# ---------------------------------------------------------------------------
# Perfil del usuario — se usa para autocompletar formularios
# ---------------------------------------------------------------------------
USER_PROFILE = {
    "full_name":     os.getenv("USER_FULL_NAME", "Ignacio Romo"),
    "first_name":    os.getenv("USER_FIRST_NAME", "Ignacio"),
    "last_name":     os.getenv("USER_LAST_NAME", "Romo"),
    "email":         os.getenv("USER_EMAIL", "ygnacio1698@gmail.com"),
    "phone":         os.getenv("USER_PHONE", "+56 9 3420 0859"),
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
}

# ---------------------------------------------------------------------------
# Configuración de portales
# ---------------------------------------------------------------------------
# Para agregar un portal nuevo copia cualquier entrada y ajusta los selectores.
# Usa DevTools (F12 → Inspector) para obtener los selectores CSS correctos.
#
# Campos obligatorios:
#   url_busqueda          — URL de búsqueda con filtros ya aplicados
#   selector_oferta       — Selector del contenedor de cada oferta en la lista
#   selector_boton_aplicar — Selector del botón principal de postulación
#   tipo_postulacion      — "directa" | "modal" | "externa"
#
# Campos opcionales:
#   selector_siguiente_pagina — para paginación automática
#   selector_titulo_oferta    — para extraer el título en los logs
#   max_offers_per_run        — límite de postulaciones en una ejecución
#   requires_login            — True si la sesión debe estar iniciada antes
# ---------------------------------------------------------------------------
SITE_CONFIG = {
    "linkedin": {
        "url_busqueda": (
            "https://www.linkedin.com/jobs/search/"
            "?keywords=Desarrollador+Programador&location=Santiago%2C+Región+Metropolitana%2C+Chile"
            "&f_AL=true"   # Easy Apply (Solicitud sencilla) solamente
            "&f_E=2"       # Entry Level únicamente — sin experiencia previa
            # Keywords amplias IT: captura Desarrollador Junior, Programador Jr,
            # Analista Programador, Dev Backend/Frontend, etc.
        ),
        "selector_oferta":          "li[data-occludable-job-id]",
        "selector_boton_aplicar":   "button.jobs-apply-button",
        "selector_siguiente_pagina": "button[aria-label='Ver más empleos']",
        "selector_titulo_oferta":   "h1.job-details-jobs-unified-top-card__job-title",
        "tipo_postulacion":         "modal",
        "max_offers_per_run":       15,
        "requires_login":           True,
    },
    "indeed": {
        "url_busqueda": (
            "https://cl.indeed.com/jobs?q=Desarrollador+Programador+Junior&l=Santiago%2C+Regi%C3%B3n+Metropolitana"
        ),
        "selector_oferta":          "div.job_seen_beacon",
        "selector_boton_aplicar":   "button#indeedApplyButton, a.indeed-apply-button",
        "selector_siguiente_pagina": "a[data-testid='pagination-page-next']",
        "selector_titulo_oferta":   "h2.jobsearch-JobInfoHeader-title",
        "tipo_postulacion":         "modal",
        "max_offers_per_run":       10,
        "requires_login":           False,
    },
    "computrabajo": {
        "url_busqueda": (
            "https://www.computrabajo.com.ar/trabajo-de-desarrollador-python"
        ),
        "selector_oferta":          "article.box_offer",
        "selector_boton_aplicar":   "a.btn_postular, button.postular",
        "selector_siguiente_pagina": "a[title='Siguiente']",
        "selector_titulo_oferta":   "h1.title_offer",
        "tipo_postulacion":         "directa",
        "max_offers_per_run":       20,
        "requires_login":           False,
    },
    "getonyboard": {
        "url_busqueda": (
            "https://www.getonbrd.com/jobs/programming?tag=python&remote=true"
        ),
        "selector_oferta":          "a.gb-results-list__item",
        "selector_boton_aplicar":   "a.btn-primary[href*='apply'], button.apply-btn",
        "selector_siguiente_pagina": None,
        "selector_titulo_oferta":   "h1.gb-landing-cover__title",
        "tipo_postulacion":         "externa",
        "max_offers_per_run":       10,
        "requires_login":           False,
    },
}

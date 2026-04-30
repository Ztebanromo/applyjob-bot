"""
Configuración central del bot.
Edita USER_PROFILE con tus datos y agrega portales en SITE_CONFIG.
"""

# ---------------------------------------------------------------------------
# Perfil del usuario — se usa para autocompletar formularios
# ---------------------------------------------------------------------------
USER_PROFILE = {
    "full_name":   "Tu Nombre Completo",
    "first_name":  "Tu Nombre",
    "last_name":   "Tu Apellido",
    "email":       "tuemail@gmail.com",
    "phone":       "+1234567890",
    "city":        "Ciudad, País",
    "linkedin":    "https://linkedin.com/in/tu-perfil",
    "portfolio":   "https://tu-portfolio.com",
    "cv_path":     "C:/Users/TuUsuario/Documents/CV.pdf",  # ruta absoluta al PDF
    "salary":      "3000",
    "years_exp":   "3",
    "cover_letter": (
        "Estoy muy interesado en esta posición y creo que mi experiencia "
        "encaja perfectamente con los requerimientos del rol."
    ),
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
            "?keywords=Python+Developer&location=Remote&f_AL=true"
            # f_AL=true = Easy Apply solamente
        ),
        "selector_oferta":          "li.jobs-search-results__list-item",
        "selector_boton_aplicar":   "button.jobs-apply-button",
        "selector_siguiente_pagina": "button[aria-label='Ver más empleos']",
        "selector_titulo_oferta":   "h1.job-details-jobs-unified-top-card__job-title",
        "tipo_postulacion":         "modal",
        "max_offers_per_run":       15,
        "requires_login":           True,
    },
    "indeed": {
        "url_busqueda": (
            "https://www.indeed.com/jobs?q=Python+Developer&l=Remote"
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

import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIG_FILE = DATA_DIR / "user_config.json"

# ---------------------------------------------------------------------------
# Perfil del usuario — Valores por defecto
# ---------------------------------------------------------------------------
DEFAULT_PROFILE = {
    "full_name":   "Tu Nombre Completo",
    "first_name":  "Tu Nombre",
    "last_name":   "Tu Apellido",
    "email":       "tuemail@gmail.com",
    "phone":       "+1234567890",
    "city":        "Ciudad, País",
    "linkedin":    "https://linkedin.com/in/tu-perfil",
    "portfolio":   "https://tu-portfolio.com",
    "cv_path":     "C:/Users/TuUsuario/Documents/CV.pdf",
    "salary":      "3000",
    "years_exp":   "3",
    "cover_letter": "Estoy interesado en esta posición...",
}

def load_user_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"profile": DEFAULT_PROFILE, "search": {}}

_config = load_user_config()
USER_PROFILE = _config.get("profile", DEFAULT_PROFILE)
SEARCH_CONFIG = _config.get("search", {})

# ---------------------------------------------------------------------------
# Configuración de portales
# ---------------------------------------------------------------------------
kw = SEARCH_CONFIG.get("keywords", "Python Developer").replace(" ", "+")
loc = SEARCH_CONFIG.get("location", "Remote").replace(" ", "+")

SITE_CONFIG = {
    "linkedin": {
        "url_busqueda": f"https://www.linkedin.com/jobs/search/?keywords={kw}&location={loc}&f_AL=true",
        "selector_oferta":          "li.jobs-search-results__list-item",
        "selector_boton_aplicar":   "button.jobs-apply-button",
        "selector_siguiente_pagina": "button[aria-label='Ver más empleos']",
        "selector_titulo_oferta":   "h1.job-details-jobs-unified-top-card__job-title",
        "tipo_postulacion":         "modal",
        "max_offers_per_run":       15,
        "requires_login":           True,
    },
    "indeed": {
        "url_busqueda": f"https://www.indeed.com/jobs?q={kw}&l={loc}",
        "selector_oferta":          "div.job_seen_beacon",
        "selector_boton_aplicar":   "button#indeedApplyButton, a.indeed-apply-button",
        "selector_siguiente_pagina": "a[data-testid='pagination-page-next']",
        "selector_titulo_oferta":   "h2.jobsearch-JobInfoHeader-title",
        "tipo_postulacion":         "modal",
        "max_offers_per_run":       10,
        "requires_login":           False,
    },
    "computrabajo": {
        "url_busqueda": f"https://www.computrabajo.com.ar/trabajo-de-{kw.lower().replace('+', '-')}",
        "selector_oferta":          "article.box_offer",
        "selector_boton_aplicar":   "a.btn_postular, button.postular",
        "selector_siguiente_pagina": "a[title='Siguiente']",
        "selector_titulo_oferta":   "h1.title_offer",
        "tipo_postulacion":         "directa",
        "max_offers_per_run":       20,
        "requires_login":           False,
    },
    "getonyboard": {
        "url_busqueda": f"https://www.getonbrd.com/empleos-{kw.lower().replace('+', '-')}",
        "selector_oferta":          "a.gb-results-list__item",
        "selector_boton_aplicar":   "a.btn-primary[href*='apply'], button.apply-btn",
        "selector_siguiente_pagina": None,
        "selector_titulo_oferta":   "h1.gb-landing-cover__title",
        "tipo_postulacion":         "externa",
        "max_offers_per_run":       10,
        "requires_login":           False,
    },
}

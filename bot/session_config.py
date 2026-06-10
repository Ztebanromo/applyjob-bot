"""
session_config.py — Única fuente de verdad para detección de sesión por portal.

Importado por engine.py, gui_server.py y session_checker.py.
NUNCA definir selectores de sesión en otro lugar.
"""
from __future__ import annotations

# URLs de "Mis postulaciones" por portal — usadas para verificar que el envío
# realmente quedó registrado en el historial del portal.
MY_APPLICATIONS_URLS: dict[str, str] = {
    # "applied-jobs/" da 404 ("Esta página no existe") — confirmado en vivo
    # 2026-06-09. La URL correcta es "saved-jobs" filtrado por cardType=APPLIED
    # (pestaña "Solicitados" en Mis anuncios de empleo).
    "linkedin":      "https://www.linkedin.com/my-items/saved-jobs/?cardType=APPLIED",
    "computrabajo":  "https://candidato.cl.computrabajo.com/candidate/match/",  # confirmado en vivo (10 tarjetas, slash final)
    "laborum":       "https://www.laborum.cl/postulantes/postulaciones",      # confirmado
    "trabajando":    "https://www.trabajando.cl/mis-postulaciones",              # confirmado
    # confirmado — dgv es un token de sesion que puede expirar/cambiar
    "infojobs":      "https://www.infojobs.net/candidate/applications/list.xhtml?dgv=1500261436412201670",
    "chiletrabajos": "https://www.chiletrabajos.cl/dashboard/postulaciones",  # confirmado
    "getonyboard":   "https://www.getonbrd.com/applications",
    "indeed":        "https://cl.indeed.com/my-jobs",
}

# Selectores de las tarjetas de postulación en la página "mis postulaciones".
# El bot los usa para contar cuántas aplicaciones recientes hay y confirmar envío.
MY_APPLICATIONS_CARD_SELECTORS: dict[str, str] = {
    "linkedin":      "li.job-card-container, li[class*='applied-jobs'], div[class*='job-card']",
    "computrabajo":  "div.box.dFlex.hover",  # confirmado en vivo (10 tarjetas detectadas)
    "laborum":       "div.application-item, article[class*='application'], li[class*='postulacion'], div[class*='postulation']",
    "trabajando":    "div[class*='postulation'], article[class*='application'], li.apply-item",
    "infojobs":      "div.application-card, li[class*='application'], div[class*='offerItem']",
    "chiletrabajos": "div.accordion-item",
    "getonyboard":   "div.application, a[href*='/applications/'], div[class*='Application'], li[class*='application']",
    "indeed":        "div[class*='applied-job'], li.job-card, div[class*='jobcard']",
}

# URL a la que navegar para verificar si la sesión sigue activa.
VERIFY_URLS: dict[str, str] = {
    "linkedin":      "https://www.linkedin.com/feed",
    "computrabajo":  "https://cl.computrabajo.com/",
    "laborum":       "https://www.laborum.cl/",
    "trabajando":    "https://www.trabajando.cl/",
    "infojobs":      "https://www.infojobs.net/",
    "chiletrabajos": "https://www.chiletrabajos.cl/",
    "getonyboard":   "https://www.getonbrd.com",
    "indeed":        "https://cl.indeed.com/account/login",
}

# URL para abrir el browser de login manual.
# LinkedIn: homepage en vez de /login para evitar bloqueo anti-bot.
LOGIN_URLS: dict[str, str] = {
    "linkedin":      "https://www.linkedin.com/login",
    "computrabajo":  "https://cl.computrabajo.com",
    "laborum":       "https://www.laborum.cl",
    "trabajando":    "https://www.trabajando.cl",
    "infojobs":      "https://www.infojobs.net",
    "chiletrabajos": "https://www.chiletrabajos.cl",
    "getonyboard":   "https://www.getonbrd.com/auth/sign_in",
    "indeed":        "https://cl.indeed.com/account/login",
}

# Selectores que indican sesión ACTIVA (visibles solo cuando autenticado).
# Criterio: is_visible() == True cuando el usuario está logueado.
# Priorizar elementos en el header principal, NO dentro de dropdowns colapsados.
LOGGED_IN_SIGNALS: dict[str, list[str]] = {
    "linkedin": [
        "img[class*='EntityPhoto']",
        "img.global-nav__me-photo",
        "div.global-nav__me-photo",
        "img[class*='global-nav__me']",
        "[data-control-name='nav.settings']",
        "a[data-tracking-control-name*='nav_settings']",
    ],
    "computrabajo": [
        # Confirmado en vivo (DOM dump 2026-06-07): el header muestra
        # <div class="info_user" data-info-user=""><span>{Nombre}</span>...
        # SOLO cuando hay sesión activa — visible y único. Los selectores
        # viejos ([class*='img_user'], a[href*='/candidato']) SÍ existen en
        # el DOM pero ocultos (menú móvil colapsado / cajas promo de la home),
        # is_visible() siempre daba False → falsos "no logueado" en loop
        # ("me pide compu pero si esta" — el usuario SÍ tenía sesión activa).
        "[data-info-user]",
        ".info_user",
        "div.info_user span",
    ],
    "laborum": [
        "[class*='userAvatar']",
        "[class*='UserAvatar']",
        "img[alt*='avatar' i]",
        "[class*='user-menu']",
        "[class*='UserMenu']",
        "a[href*='/postulantes']",
    ],
    "trabajando": [
        "[class*='header-logged']",
        "[class*='logged-menu']",
        "[class*='profile'][class*='position-relative']",
        "div.menuLogueadoMovil",
        "[class*='menuLogueado']",
        "a[href*='/mi-cv']",
    ],
    "infojobs": [
        "[class*='ij-HeaderDesktop-navbar-avatar']",
        "a[href*='my-infojobs']",
        "a[href*='/candidate/applications']",
        "a[href*='/candidate/cv']",
        "[class*='UserMenu']",
        "[class*='navbar-user']",
    ],
    "chiletrabajos": [
        # Confirmado con DOM dump: div.logged presente cuando autenticado
        "div.logged",
        "[class*='user-profile']",
        "a[href*='/dashboard']",
        "a[href*='logout']",
        "a[href*='chtlogin/logout']",
    ],
    "getonyboard": [
        # body.dashboard = clase solo presente cuando logueado
        "body.dashboard",
        # Avatar div visible en header (no en dropdown)
        "[data-placeholder-avatar]",
        "a[href*='/webpros/logout']",
    ],
    "indeed": [
        "a[data-gnav-element-name='Account']",
        "div[data-testid='UserDropdown']",
        "img[class*='avatarImage']",
    ],
}

# Selectores que indican pantalla de LOGIN (sesión NO activa).
# Si cualquiera es visible → sesión expirada/inexistente.
NOT_LOGGED_IN_SIGNALS: dict[str, list[str]] = {
    "linkedin": [
        "#session_key",
        ".sign-in-form",
        "div.nav__button-secondary",
        "button[data-tracking-control-name='guest_homepage-basic_sign-in-button']",
        "input[name='session_key']",
    ],
    "computrabajo": [
        "input[name='email'][placeholder*='mail']",
        "form[action*='login']",
        "form[action*='iniciar']",
        "button:has-text('Iniciar sesión')",
    ],
    "laborum": [
        "#ingresarNavBar",
        "input[type='password']",
        "form[id*='login']",
    ],
    "trabajando": [
        # Nuxt SPA: este div solo aparece cuando NO está logueado
        "div.menuNoLogueadoMovil",
        "a[id='ingresarATuCuenta']",
        "a[href='/ingresa-a-tu-cuenta']",
        "input[type='password']",
    ],
    "infojobs": [
        "button:has-text('Entrar')",
        "button:has-text('Iniciar sesión')",
        "input[type='password']",
        "form[action*='login']",
    ],
    "chiletrabajos": [
        "a:has-text('Ingresa a tu cuenta')",
        "input[name='email']",
        "input[type='password']",
    ],
    "getonyboard": [
        "a:has-text('Ingresa')",
        "button:has-text('Ingresa')",
        "a[href*='/auth/sign_in']",
    ],
    "indeed": [
        "input[type='password']",
        "form[action*='/account/login']",
        "div.desktop-sign-in-button",
    ],
}

# Keywords en la URL que indican redirección a login
LOGIN_URL_KEYWORDS: list[str] = [
    "login", "signin", "sign-in", "account/login",
    "candidato/login", "iniciar-sesion", "auth",
    "ingresa-a-tu-cuenta", "authwall",
]

# User-Agent para browsers Playwright (headless y visible)
# Usar Chrome 131 (versión actual 2025) para evitar detección por UA desactualizado
STEALTH_USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Args de stealth para Playwright
STEALTH_ARGS: list[str] = [
    "--no-sandbox",
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--disable-notifications",
    "--disable-dev-shm-usage",
    "--disable-features=IsolateOrigins,site-per-process",
    "--flag-switches-begin",
    "--flag-switches-end",
]

STEALTH_IGNORE_DEFAULT_ARGS: list[str] = ["--enable-automation"]

STEALTH_INIT_SCRIPT: str = (
    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
    "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});"
    "Object.defineProperty(navigator,'languages',{get:()=>['es-CL','es','en']});"
)

# Portales que requieren sesión para postular
PORTALS_REQUIRE_LOGIN: set[str] = {
    "linkedin", "computrabajo", "laborum", "trabajando",
    "infojobs", "chiletrabajos", "getonyboard",
}

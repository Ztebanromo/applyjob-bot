"""
session_config.py — Única fuente de verdad para detección de sesión por portal.

Importado por engine.py, gui_server.py y session_checker.py.
NUNCA definir selectores de sesión en otro lugar.
"""
from __future__ import annotations

# URL a la que navegar para verificar si la sesión sigue activa.
VERIFY_URLS: dict[str, str] = {
    "linkedin":      "https://www.linkedin.com/feed",
    "computrabajo":  "https://cl.computrabajo.com/",
    "laborum":       "https://www.laborum.cl/",
    "trabajando":    "https://www.trabajando.cl/mi-cuenta",
    "infojobs":      "https://www.infojobs.net/candidato/mi-perfil",
    "chiletrabajos": "https://www.chiletrabajos.cl/",
    "getonyboard":   "https://www.getonbrd.com",
    "indeed":        "https://cl.indeed.com/account/login",
}

# URL para abrir el browser de login manual.
# LinkedIn: homepage en vez de /login para evitar bloqueo anti-bot.
LOGIN_URLS: dict[str, str] = {
    "linkedin":      "https://www.linkedin.com/",
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
        "img.global-nav__me-photo",
        "div.global-nav__me-photo",
        "img[class*='global-nav__me']",
        "[data-control-name='nav.settings']",
        "a[data-tracking-control-name*='nav_settings']",
    ],
    "computrabajo": [
        "[class*='img_user']",
        "[class*='HeaderUser']",
        "[class*='header-user']",
        "a[href*='/candidato']",
        "a[title*='Mi cuenta' i]",
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
        # Nuxt SPA: solo aparece cuando está logueado
        "div.menuLogueadoMovil",
        "[class*='menuLogueado']",
        "a[href*='/mi-cv']",
        "a[href*='/mi-cuenta']",
        "[class*='user-menu']",
        "[data-testid*='user']",
    ],
    "infojobs": [
        "[class*='UserMenu']",
        "[class*='navbar-user']",
        "[data-testid='user-menu']",
        "a[href*='/candidato/mis-candidaturas']",
        "a[href*='/candidato/mi-perfil']",
        "img[alt*='avatar' i]",
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
STEALTH_USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Args de stealth para Playwright
STEALTH_ARGS: list[str] = [
    "--no-sandbox",
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--disable-notifications",
    "--disable-dev-shm-usage",
]

STEALTH_IGNORE_DEFAULT_ARGS: list[str] = ["--enable-automation"]

STEALTH_INIT_SCRIPT: str = (
    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
)

# Portales que requieren sesión para postular
PORTALS_REQUIRE_LOGIN: set[str] = {
    "linkedin", "computrabajo", "laborum", "trabajando",
    "infojobs", "chiletrabajos", "getonyboard",
}

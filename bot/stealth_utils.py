"""
Utilidades de evasión y simulación de comportamiento humano.

Filosofía: cada portal tiene su propio "ritmo". LinkedIn es el más
vigilado → tiempos más largos. Portales chilenos locales → tiempos medios.

Módulos exportados:
  - apply_stealth(page)          — inyecta JS anti-detección
  - human_delay(min, max)        — pausa aleatoria entre acciones
  - micro_delay()                — pausa muy corta (keystroke)
  - human_scroll(page, steps)    — scroll gradual
  - human_click(page, selector)  — click con offset + hover previo
  - human_type(page, sel, text)  — typing con velocidad variable
  - human_type_field(page, el, text, portal) — typing robusto sobre ElementHandle
  - reading_pause(char_count)    — simula tiempo de lectura
  - pre_form_pause(portal)       — pausa antes de interactuar con formulario
  - scroll_to_and_pause(page, el)  — hace scroll al elemento y espera
  - portal_action_delay(portal)  — delay entre acciones según portal
  - take_error_screenshot(page, portal, context)
  - random_user_agent()
  - random_viewport()
"""
import datetime
import random
import time
from pathlib import Path

from playwright.sync_api import Page, ElementHandle


# ---------------------------------------------------------------------------
# Perfiles de timing por portal
# ---------------------------------------------------------------------------
# Cada perfil define:
#   min_action / max_action  → pausa entre acciones (segundos)
#   min_key / max_key        → tiempo entre teclas (segundos)
#   typo_rate                → prob. de cometer un typo y corregirlo
#   scroll_steps             → pasos de scroll al cargar página
#   read_wpm                 → palabras por minuto de "lectura"
PORTAL_TIMING: dict[str, dict] = {
    "linkedin": {
        "min_action": 1.8, "max_action": 4.0,
        "min_key": 0.07,   "max_key": 0.25,
        "typo_rate": 0.06,
        "scroll_steps": 3,
        "read_wpm": 200,
    },
    "computrabajo": {
        "min_action": 0.9, "max_action": 2.2,
        "min_key": 0.04,   "max_key": 0.14,
        "typo_rate": 0.02,
        "scroll_steps": 2,
        "read_wpm": 350,
    },
    "laborum": {
        "min_action": 0.8, "max_action": 2.0,
        "min_key": 0.04,   "max_key": 0.13,
        "typo_rate": 0.02,
        "scroll_steps": 2,
        "read_wpm": 350,
    },
    "chiletrabajo": {
        "min_action": 0.7, "max_action": 1.8,
        "min_key": 0.03,   "max_key": 0.11,
        "typo_rate": 0.01,
        "scroll_steps": 1,
        "read_wpm": 400,
    },
    "getonboard": {
        "min_action": 0.9, "max_action": 2.0,
        "min_key": 0.04,   "max_key": 0.14,
        "typo_rate": 0.02,
        "scroll_steps": 2,
        "read_wpm": 350,
    },
    "default": {
        "min_action": 0.8, "max_action": 2.0,
        "min_key": 0.04,   "max_key": 0.14,
        "typo_rate": 0.02,
        "scroll_steps": 2,
        "read_wpm": 350,
    },
}

# Letras adyacentes en teclado QWERTY para simular typos realistas
_ADJACENT: dict[str, str] = {
    "a": "sqwz", "b": "vghn", "c": "xdfv", "d": "sfgxce", "e": "wrsd",
    "f": "dgtrce", "g": "fhtyuv", "h": "gjyubn", "i": "uojk", "j": "hkuimn",
    "k": "jlion", "l": "kop", "m": "njk", "n": "bmhj", "o": "iplk",
    "p": "ol", "q": "wa", "r": "etdf", "s": "adwezx", "t": "rfgy",
    "u": "yihj", "v": "cfgb", "w": "qase", "x": "zsdc", "y": "tghu",
    "z": "asx",
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
]


# ---------------------------------------------------------------------------
# Helpers base
# ---------------------------------------------------------------------------

def random_user_agent() -> str:
    return random.choice(USER_AGENTS)


def random_viewport() -> dict:
    return random.choice(VIEWPORTS)


def _portal_profile(portal: str) -> dict:
    """Retorna el perfil de timing para el portal dado."""
    key = (portal or "").lower().split(".")[0]
    return PORTAL_TIMING.get(key, PORTAL_TIMING["default"])


def human_delay(min_s: float = 1.0, max_s: float = 2.5) -> None:
    """Pausa aleatoria para simular tiempo de reacción humana."""
    time.sleep(random.uniform(min_s, max_s))


def micro_delay() -> None:
    """Pausa muy corta entre micro-acciones (keystroke)."""
    time.sleep(random.uniform(0.05, 0.15))


def portal_action_delay(portal: str = "") -> None:
    """Pausa entre acciones según el nivel de riesgo del portal."""
    p = _portal_profile(portal)
    time.sleep(random.uniform(p["min_action"], p["max_action"]))


def reading_pause(char_count: int = 300, portal: str = "") -> None:
    """
    Simula el tiempo que tarda un humano en leer N caracteres.
    Basado en WPM del portal (~5 chars/word).
    """
    p = _portal_profile(portal)
    words = max(char_count / 5, 10)
    seconds = (words / p["read_wpm"]) * 60
    # Agregar jitter natural ±20%
    jitter = random.uniform(0.8, 1.2)
    pause = min(seconds * jitter, 8.0)  # cap a 8s
    time.sleep(max(pause, 0.5))


def pre_form_pause(portal: str = "") -> None:
    """
    Pausa antes de empezar a rellenar un formulario.
    Simula que el humano 'lee' el formulario antes de escribir.
    """
    p = _portal_profile(portal)
    base = random.uniform(p["min_action"] * 1.2, p["max_action"] * 1.5)
    time.sleep(min(base, 5.0))


# ---------------------------------------------------------------------------
# Scroll
# ---------------------------------------------------------------------------

def human_scroll(page: Page, steps: int = 2, portal: str = "") -> None:
    """Scroll gradual simulando lectura de página."""
    p = _portal_profile(portal)
    steps = max(steps, p.get("scroll_steps", 2))
    for _ in range(steps):
        delta = random.randint(250, 550)
        page.mouse.wheel(0, delta)
        time.sleep(random.uniform(0.25, 0.6))
    # Pequeña pausa al final como si el usuario leyera el final
    time.sleep(random.uniform(0.2, 0.5))


def scroll_to_and_pause(page: Page, element: ElementHandle, portal: str = "") -> None:
    """
    Hace scroll suave hasta el elemento y espera que sea visible.
    Luego pausa un momento como si el usuario lo estuviera leyendo.
    """
    try:
        element.scroll_into_view_if_needed(timeout=3000)
        time.sleep(random.uniform(0.3, 0.8))
        # Scroll suave adicional para que no quede justo en el borde
        page.mouse.wheel(0, random.randint(-80, 80))
        time.sleep(random.uniform(0.15, 0.35))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Click humano
# ---------------------------------------------------------------------------

def human_click(page: Page, selector: str, timeout: int = 10_000,
                portal: str = "") -> None:
    """
    Click con:
    - Hover previo moviendo el mouse desde posición aleatoria
    - Offset aleatorio dentro del elemento
    - Pausa mínima entre hover y click
    """
    try:
        element = page.wait_for_selector(selector, timeout=timeout)
        if not element:
            return
        _click_element(page, element, portal)
    except Exception:
        pass


def _click_element(page: Page, element: ElementHandle, portal: str = "") -> None:
    """Click humano sobre un ElementHandle ya localizado."""
    try:
        box = element.bounding_box()
        if box:
            # Destino: punto aleatorio dentro del elemento (evitando bordes)
            tx = box["x"] + box["width"]  * random.uniform(0.25, 0.75)
            ty = box["y"] + box["height"] * random.uniform(0.25, 0.75)

            # Origen: posición actual del mouse o un punto cercano aleatorio
            sx = tx + random.randint(-120, 120)
            sy = ty + random.randint(-80,  80)

            # Movimiento gradual en 6-12 pasos
            steps = random.randint(6, 12)
            page.mouse.move(sx, sy)
            time.sleep(random.uniform(0.05, 0.12))
            page.mouse.move(tx, ty, steps=steps)
            time.sleep(random.uniform(0.06, 0.18))
            page.mouse.click(tx, ty)
        else:
            element.click()
    except Exception:
        try:
            element.click()
        except Exception:
            pass


def human_click_element(page: Page, element: ElementHandle, portal: str = "") -> None:
    """Versión pública de _click_element para uso externo."""
    _click_element(page, element, portal)


# ---------------------------------------------------------------------------
# Typing humano
# ---------------------------------------------------------------------------

def _make_typo(char: str) -> str | None:
    """Retorna un carácter adyacente para simular typo, o None si no hay."""
    c = char.lower()
    adj = _ADJACENT.get(c)
    if adj:
        return random.choice(adj)
    return None


def human_type(page: Page, selector: str, text: str, portal: str = "") -> None:
    """
    Escribe en un campo (selector CSS) con velocidad variable por carácter.
    Compatible con la firma original (no rompe llamadas existentes).
    """
    try:
        element = page.wait_for_selector(selector, timeout=8000)
        if element:
            human_type_field(page, element, text, portal)
    except Exception:
        pass


def human_type_field(page: Page, element: ElementHandle, text: str,
                     portal: str = "") -> None:
    """
    Escribe texto en un ElementHandle con comportamiento humano:
    - Hace click en el campo primero (con hover)
    - Limpia el contenido existente
    - Escribe con velocidad variable por tecla
    - Ocasionalmente comete un typo y lo corrige (Backspace)
    - Pausa larga ocasional (0.3-0.8s) simulando que el usuario piensa
    """
    p = _portal_profile(portal)
    min_k  = p["min_key"]
    max_k  = p["max_key"]
    t_rate = p["typo_rate"]

    try:
        _click_element(page, element, portal)
        time.sleep(random.uniform(0.1, 0.25))

        # Limpiar campo (Ctrl+A → Delete)
        page.keyboard.press("Control+a")
        time.sleep(random.uniform(0.05, 0.1))
        page.keyboard.press("Delete")
        time.sleep(random.uniform(0.05, 0.1))

        for i, char in enumerate(text):
            # Pausa larga ocasional (simula que el usuario piensa)
            if i > 0 and random.random() < 0.03:
                time.sleep(random.uniform(0.35, 0.85))

            # Typo simulado (solo en letras minúsculas)
            if char.isalpha() and random.random() < t_rate:
                typo = _make_typo(char)
                if typo:
                    page.keyboard.type(typo)
                    time.sleep(random.uniform(min_k, max_k * 1.5))
                    page.keyboard.press("Backspace")
                    time.sleep(random.uniform(0.08, 0.18))

            page.keyboard.type(char)
            time.sleep(random.uniform(min_k, max_k))

    except Exception:
        # Fallback silencioso: fill directo
        try:
            element.fill(text)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Stealth JS injection
# ---------------------------------------------------------------------------

def apply_stealth(page: Page) -> None:
    """Inyecta scripts para ocultar huellas de automatización de Playwright."""
    page.add_init_script("""
        // Ocultar webdriver
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

        // Plugins realistas
        Object.defineProperty(navigator, 'plugins', {
            get: () => [
                { name: 'Chrome PDF Viewer',  filename: 'internal-pdf-viewer',
                  description: 'Portable Document Format' },
                { name: 'Chrome PDF Plugin',  filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai',
                  description: 'Portable Document Format' },
                { name: 'Native Client',      filename: 'internal-nacl-plugin',
                  description: '' },
            ]
        });

        // Idioma y plataforma
        Object.defineProperty(navigator, 'languages', { get: () => ['es-CL', 'es', 'en-US', 'en'] });
        Object.defineProperty(navigator, 'platform',  { get: () => 'Win32' });

        // Chrome runtime (evita detección por ausencia)
        window.chrome = {
            runtime:    { onMessage: { addListener: () => {} } },
            loadTimes:  function() { return {}; },
            csi:        function() { return {}; },
            app:        { isInstalled: false }
        };

        // WebGL vendor realista
        const _getParam = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(p) {
            if (p === 37445) return 'Google Inc. (Intel)';
            if (p === 37446) return 'ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)';
            return _getParam.apply(this, arguments);
        };

        // Ocultar automatización en Permissions API
        const _query = window.navigator.permissions && window.navigator.permissions.query;
        if (_query) {
            window.navigator.permissions.query = (params) => {
                if (params.name === 'notifications') {
                    return Promise.resolve({ state: Notification.permission });
                }
                return _query(params);
            };
        }

        // Simular hardware concurrency y deviceMemory normales
        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
        try { Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 }); } catch(_){}
    """)


# ---------------------------------------------------------------------------
# Screenshot de errores
# ---------------------------------------------------------------------------

def take_error_screenshot(page: Page, portal: str, context: str = "") -> Path:
    """Guarda un screenshot en errors/ y retorna la ruta."""
    errors_dir = Path(__file__).parent.parent / "errors"
    errors_dir.mkdir(exist_ok=True)
    ts    = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    label = f"{portal}_{context}_{ts}" if context else f"{portal}_{ts}"
    path  = errors_dir / f"{label}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
    except Exception:
        pass
    return path

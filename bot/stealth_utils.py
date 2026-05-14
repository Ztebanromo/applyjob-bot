"""Utilidades de evasión y simulación de comportamiento humano."""
import datetime
import random
import time
from pathlib import Path

from playwright.sync_api import Page


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
]


def random_user_agent() -> str:
    return random.choice(USER_AGENTS)


def random_viewport() -> dict:
    return random.choice(VIEWPORTS)


def human_delay(min_s: float = 1.0, max_s: float = 2.5) -> None:
    """Pausa aleatoria para simular tiempo de reacción humana."""
    time.sleep(random.uniform(min_s, max_s))


def micro_delay() -> None:
    """Pausa muy corta entre micro-acciones."""
    time.sleep(random.uniform(0.05, 0.15))


def human_scroll(page: Page, steps: int = 2) -> None:
    """Scroll gradual simulando lectura."""
    for _ in range(steps):
        page.mouse.wheel(0, random.randint(300, 600))
        time.sleep(random.uniform(0.3, 0.7))


def human_click(page: Page, selector: str, timeout: int = 10_000) -> None:
    """Click con offset aleatorio para evadir detección."""
    try:
        element = page.wait_for_selector(selector, timeout=timeout)
        if not element:
            return
        box = element.bounding_box()
        if box:
            x = box["x"] + box["width"]  * random.uniform(0.2, 0.8)
            y = box["y"] + box["height"] * random.uniform(0.2, 0.8)
            page.mouse.move(x, y, steps=8)
            micro_delay()
            page.mouse.click(x, y)
        else:
            element.click()
    except Exception:
        pass


def human_type(page: Page, selector: str, text: str) -> None:
    """Escribe texto con velocidad variable por carácter."""
    try:
        page.click(selector)
        micro_delay()
        for char in text:
            page.keyboard.type(char)
            if random.random() < 0.04:
                time.sleep(random.uniform(0.2, 0.5))
            else:
                time.sleep(random.uniform(0.04, 0.12))
    except Exception:
        pass


def apply_stealth(page: Page) -> None:
    """Inyecta scripts para ocultar huellas de automatización."""
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', {
            get: () => [
                { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer' },
                { name: 'YouTube Plug-in',   filename: 'youtube-plugin' }
            ]
        });
        Object.defineProperty(navigator, 'languages', { get: () => ['es-CL', 'es', 'en-US'] });
        Object.defineProperty(navigator, 'platform',  { get: () => 'Win32' });
        window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){}, app: {} };
        const _getParam = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(p) {
            if (p === 37445) return 'Intel Open Source Technology Center';
            if (p === 37446) return 'Mesa DRI Intel(R) HD Graphics 520 (Skylake GT2)';
            return _getParam.apply(this, arguments);
        };
    """)


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

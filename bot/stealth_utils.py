"""
Utilidades de evasión y simulación de comportamiento humano.
"""
import random
import time
from pathlib import Path
from playwright.sync_api import Page


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
    {"width": 1280, "height": 800},
]


def random_user_agent() -> str:
    return random.choice(USER_AGENTS)


def random_viewport() -> dict:
    return random.choice(VIEWPORTS)


def human_delay(min_s: float = 2.0, max_s: float = 5.0) -> None:
    """Pausa aleatoria para simular tiempo de lectura/reacción humana."""
    time.sleep(random.uniform(min_s, max_s))


def micro_delay() -> None:
    """Pausa muy corta entre teclas o micro-acciones."""
    time.sleep(random.uniform(0.05, 0.2))


def human_scroll(page: Page, steps: int = 3) -> None:
    """Scroll gradual hacia abajo simulando lectura."""
    for _ in range(steps):
        amount = random.randint(200, 500)
        page.mouse.wheel(0, amount)
        time.sleep(random.uniform(0.3, 0.8))


def human_click(page: Page, selector: str, timeout: int = 10_000) -> None:
    """
    Mueve el mouse hacia el elemento con una pequeña variación de offset
    antes de hacer click, en lugar de un click directo.
    """
    element = page.wait_for_selector(selector, timeout=timeout)
    box = element.bounding_box()
    if box:
        # Offset aleatorio dentro del elemento
        x = box["x"] + box["width"] * random.uniform(0.2, 0.8)
        y = box["y"] + box["height"] * random.uniform(0.2, 0.8)
        page.mouse.move(x, y)
        micro_delay()
        page.mouse.click(x, y)
    else:
        element.click()


def human_type(page: Page, selector: str, text: str) -> None:
    """Escribe texto con velocidad variable por carácter."""
    page.click(selector)
    micro_delay()
    for char in text:
        page.keyboard.type(char)
        micro_delay()


def apply_stealth(page: Page) -> None:
    """
    Inyecta scripts para ocultar propiedades que delatan a Playwright.
    Complementa playwright-stealth cuando está disponible.
    """
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
        Object.defineProperty(navigator, 'languages', {
            get: () => ['es-AR', 'es', 'en-US', 'en']
        });
        window.chrome = { runtime: {} };
    """)


def take_error_screenshot(page: Page, portal: str, context: str = "") -> Path:
    """Guarda un screenshot en errors/ y retorna la ruta."""
    import datetime
    errors_dir = Path(__file__).parent.parent / "errors"
    errors_dir.mkdir(exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    label = f"{portal}_{context}_{ts}" if context else f"{portal}_{ts}"
    path = errors_dir / f"{label}.png"
    page.screenshot(path=str(path), full_page=True)
    return path

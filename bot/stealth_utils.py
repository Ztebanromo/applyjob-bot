"""
Utilidades de evasión y simulación de comportamiento humano.
"""
import random
import time
from pathlib import Path
from playwright.sync_api import Page


def random_user_agent():
    agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0"
    ]
    return random.choice(agents)


def random_viewport():
    viewports = [
        {"width": 1920, "height": 1080},
        {"width": 1366, "height": 768},
        {"width": 1440, "height": 900},
        {"width": 1536, "height": 864}
    ]
    return random.choice(viewports)


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
    Aplica técnicas avanzadas de sigilo inyectando scripts de JS 
    antes de que la página cargue sus propios scripts.
    """
    # Script para falsificar Canvas, WebGL y Plugins
    stealth_script = """
    (function() {
        // 1. Ruido en Canvas (evita fingerprinting exacto)
        const originalGetImageData = CanvasRenderingContext2D.prototype.getImageData;
        CanvasRenderingContext2D.prototype.getImageData = function(x, y, w, h) {
            const imageData = originalGetImageData.apply(this, arguments);
            for (let i = 0; i < imageData.data.length; i += 4) {
                imageData.data[i] = imageData.data[i] + (Math.random() > 0.5 ? 1 : -1);
            }
            return imageData;
        };

        // Ruido en toDataURL para firmas digitales
        const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function() {
            const context = this.getContext('2d');
            if (context) {
                context.fillRect(Math.random(), Math.random(), 1, 1);
            }
            return originalToDataURL.apply(this, arguments);
        };

        // 2. Falsificar WebGL (ocultar SwiftShader/Headless)
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {
            if (parameter === 37445) return 'Intel Inc.';
            if (parameter === 37446) return 'Intel(R) Iris(R) Xe Graphics';
            return getParameter.apply(this, arguments);
        };

        // 3. Falsificar Permisos (evitar estado 'prompt' infinito)
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );

        // 3. Falsificar Plugins
        Object.defineProperty(navigator, 'plugins', {
            get: () => [
                { name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: 'Google Chrome PDF' },
                { name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer', description: 'Microsoft Edge PDF' }
            ],
        });

        // 4. Ocultar WebDriver
        Object.defineProperty(navigator, 'webdriver', { get: () => false });
        
        // 5. Soporte para chrome.runtime
        window.chrome = { runtime: {} };
    })();
    """
    page.add_init_script(stealth_script)
    
    # Headers adicionales aleatorios
    page.set_extra_http_headers({
        "Accept-Language": random.choice(["es-ES,es;q=0.9", "en-US,en;q=0.8", "es-AR,es;q=0.9"]),
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1" # Do Not Track
    })


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

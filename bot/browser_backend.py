from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

try:
    from playwright.sync_api import Browser, BrowserContext, Page, Playwright
except ImportError:  # pragma: no cover
    Browser = object
    BrowserContext = object
    Page = object
    Playwright = object

log = logging.getLogger(__name__)


class BrowserBackend:
    name = "generic"

    def __init__(
        self,
        pw: Playwright,
        session_dir: str | Path,
        *,
        headless: bool = False,
        user_agent: str | None = None,
        args: list[str] | None = None,
        ignore_default_args: list[str] | None = None,
        locale: str = "es-CL",
        timezone_id: str = "America/Santiago",
        executable_path: str | None = None,
    ):
        self.pw = pw
        self.session_dir = Path(session_dir)
        self.headless = headless
        self.user_agent = user_agent
        self.args = args or []
        self.ignore_default_args = ignore_default_args or []
        self.locale = locale
        self.timezone_id = timezone_id
        self.executable_path = executable_path
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None

    def connect(self) -> bool:
        raise NotImplementedError

    def is_connected(self) -> bool:
        return self.context is not None

    def new_page(self) -> Page | None:
        if not self.connect():
            return None
        try:
            return self.context.new_page()  # type: ignore[attr-defined]
        except Exception as exc:
            log.warning("[BACKEND] No se pudo abrir nueva pestaña: %s", exc)
            return None

    def open_url(
        self,
        url: str,
        *,
        wait_until: str = "domcontentloaded",
        timeout: int = 20_000,
    ) -> Page | None:
        page = self.new_page()
        if page is None:
            return None
        try:
            page.goto(url, wait_until=wait_until, timeout=timeout)
            return page
        except Exception as exc:
            log.warning("[BACKEND] Error navegando a %s: %s", url, exc)
            try:
                page.close()
            except Exception:
                pass
            return None

    def save_storage_state(self, path: str | Path) -> int:
        if not self.context:
            return 0
        try:
            state = self.context.storage_state(path=str(path))  # type: ignore[attr-defined]
            return len(state.get("cookies", []))
        except Exception as exc:
            log.warning("[BACKEND] No se pudo guardar storage_state: %s", exc)
            return 0

    def get_cookies(self) -> list[dict[str, Any]]:
        if not self.context:
            return []
        try:
            return self.context.cookies()  # type: ignore[attr-defined]
        except Exception as exc:
            log.warning("[BACKEND] No se pudieron leer cookies: %s", exc)
            return []

    def close(self) -> None:
        if self.context:
            try:
                self.context.close()  # type: ignore[attr-defined]
            except Exception:
                pass
        self.context = None
        self.browser = None


class ChromiumCDPBackend(BrowserBackend):
    name = "cdp"
    CDP_URL = "http://127.0.0.1:9222"

    @classmethod
    def is_port_open(cls, port: int = 9222, timeout: float = 1.0) -> bool:
        import socket

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        result = s.connect_ex(("127.0.0.1", port))
        s.close()
        return result == 0

    def connect(self) -> bool:
        if self.context is not None:
            return True
        if not self.is_port_open():
            return False
        try:
            self.browser = self.pw.chromium.connect_over_cdp(self.CDP_URL)
            self.context = (
                self.browser.contexts[0]
                if getattr(self.browser, "contexts", None)
                else self.browser.new_context()
            )
            return self.context is not None
        except Exception as exc:
            log.warning("[CDP] No se pudo conectar a Chrome real: %s", exc)
            self.browser = None
            self.context = None
            return False

    def new_page(self) -> "Page | None":
        """
        Reutiliza una pestaña existente si está disponible en lugar de abrir una nueva.
        Solo abre pestaña nueva si todas las existentes están en uso activo.
        """
        if not self.connect():
            return None
        try:
            pages = self.context.pages  # type: ignore[attr-defined]
            # Preferir una pestaña en about:blank (pestaña "libre")
            for p in pages:
                try:
                    if p.url in ("about:blank", "chrome://newtab/", ""):
                        return p
                except Exception:
                    pass
            # Si hay solo 1 pestaña abierta, reutilizarla directamente
            if len(pages) == 1:
                return pages[0]
            # Más de 1 pestaña abierta → abrir nueva para no interferir con el usuario
            return self.context.new_page()  # type: ignore[attr-defined]
        except Exception as exc:
            log.warning("[CDP] No se pudo obtener/crear pestaña: %s", exc)
            return None

    def close(self) -> None:
        # En modo CDP NO cerramos el contexto — es el Chrome del usuario
        self.context = None
        self.browser = None


class ChromiumLaunchBackend(BrowserBackend):
    name = "chromium"

    def connect(self) -> bool:
        if self.context is not None:
            return True
        try:
            self.session_dir.mkdir(parents=True, exist_ok=True)
            kwargs = {
                "headless": self.headless,
                "args": self.args,
                "ignore_default_args": self.ignore_default_args,
                "locale": self.locale,
                "timezone_id": self.timezone_id,
            }
            if self.user_agent:
                kwargs["user_agent"] = self.user_agent
            if self.executable_path:
                kwargs["executable_path"] = self.executable_path
            self.context = self.pw.chromium.launch_persistent_context(
                str(self.session_dir), **kwargs
            )
            return self.context is not None
        except Exception as exc:
            log.warning("[CHROMIUM] No se pudo lanzar Chromium: %s", exc)
            self.context = None
            return False

    def new_page(self) -> "Page | None":
        """
        Reutiliza la primera página existente del contexto.
        launch_persistent_context ya abre 1 página al iniciarse —
        no abrir otra innecesariamente.
        """
        if not self.connect():
            return None
        try:
            pages = self.context.pages  # type: ignore[attr-defined]
            if pages:
                return pages[0]  # reutilizar la página ya abierta
            return self.context.new_page()  # type: ignore[attr-defined]
        except Exception as exc:
            log.warning("[CHROMIUM] No se pudo obtener página: %s", exc)
            return None


class FirefoxBackend(BrowserBackend):
    name = "firefox"

    def connect(self) -> bool:
        if self.context is not None:
            return True
        try:
            self.session_dir.mkdir(parents=True, exist_ok=True)
            kwargs = {
                "headless": self.headless,
                "args": self.args,
                "ignore_default_args": self.ignore_default_args,
                "locale": self.locale,
                "timezone_id": self.timezone_id,
            }
            if self.user_agent:
                kwargs["user_agent"] = self.user_agent
            self.context = self.pw.firefox.launch_persistent_context(
                str(self.session_dir), **kwargs
            )
            return self.context is not None
        except Exception as exc:
            log.warning("[FIREFOX] No se pudo lanzar Firefox: %s", exc)
            self.context = None
            return False


class WebKitBackend(BrowserBackend):
    name = "webkit"

    def connect(self) -> bool:
        if self.context is not None:
            return True
        try:
            self.session_dir.mkdir(parents=True, exist_ok=True)
            kwargs = {
                "headless": self.headless,
                "args": self.args,
                "ignore_default_args": self.ignore_default_args,
                "locale": self.locale,
                "timezone_id": self.timezone_id,
            }
            if self.user_agent:
                kwargs["user_agent"] = self.user_agent
            self.context = self.pw.webkit.launch_persistent_context(
                str(self.session_dir), **kwargs
            )
            return self.context is not None
        except Exception as exc:
            log.warning("[WEBKIT] No se pudo lanzar WebKit: %s", exc)
            self.context = None
            return False

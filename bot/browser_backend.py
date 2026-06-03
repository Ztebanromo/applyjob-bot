"""
browser_backend.py — backends de browser para el bot.

CDPTabBackend   : usa el Chrome del usuario (donde está el dashboard).
                  UNA pestaña por portal, nunca la cierra entre ciclos.
ChromiumLaunchBackend : Chromium aislado como fallback (una sola página).
"""
from __future__ import annotations

import logging
import socket
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

CDP_URL  = "http://127.0.0.1:9222"
CDP_PORT = 9222


def _cdp_port_open(timeout: float = 0.5) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    ok = s.connect_ex(("127.0.0.1", CDP_PORT)) == 0
    s.close()
    return ok


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
        portal_name: str = "",
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
        self.portal_name = portal_name
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None

    def connect(self) -> bool:
        raise NotImplementedError

    def is_connected(self) -> bool:
        return self.context is not None

    def new_page(self) -> "Page | None":
        raise NotImplementedError

    def save_storage_state(self, path: str | Path) -> int:
        if not self.context:
            return 0
        try:
            state = self.context.storage_state(path=str(path))  # type: ignore[attr-defined]
            return len(state.get("cookies", []))
        except Exception as exc:
            log.warning("[BACKEND] save_storage_state: %s", exc)
            return 0

    def get_cookies(self) -> list[dict[str, Any]]:
        if not self.context:
            return []
        try:
            return self.context.cookies()  # type: ignore[attr-defined]
        except Exception as exc:
            log.warning("[BACKEND] get_cookies: %s", exc)
            return []

    def close(self) -> None:
        if self.context:
            try:
                self.context.close()  # type: ignore[attr-defined]
            except Exception:
                pass
        self.context = None
        self.browser = None


class CDPTabBackend(BrowserBackend):
    """
    Conecta al Chrome del usuario (donde está el dashboard en localhost:5000)
    y gestiona UNA pestaña por portal.

    La pestaña se reutiliza en cada ciclo — nunca se cierra entre runs.
    Requiere que Chrome esté abierto con --remote-debugging-port=9222
    (ejecutar chrome_debug.bat antes de iniciar el bot).
    """
    name = "cdp"

    def connect(self) -> bool:
        if self.context is not None:
            try:
                _ = self.context.pages  # ping
                return True
            except Exception:
                self.context = None
                self.browser = None

        if not _cdp_port_open():
            return False
        try:
            self.browser = self.pw.chromium.connect_over_cdp(CDP_URL)
            ctxs = getattr(self.browser, "contexts", [])
            self.context = ctxs[0] if ctxs else None
            if self.context is None:
                log.warning("[CDP] Sin contexto disponible en Chrome")
                return False
            log.info("[CDP] Conectado al Chrome del usuario (dashboard)")
            return True
        except Exception as exc:
            log.warning("[CDP] No se pudo conectar: %s", exc)
            self.browser = None
            self.context = None
            return False

    def new_page(self) -> "Page | None":
        """
        Devuelve LA pestaña de este portal (una sola por portal).
        Busca por dominio en las pestañas abiertas → reutiliza si existe.
        Si no existe, abre una nueva y la registra.
        """
        if not self.connect():
            return None

        pages = []
        try:
            pages = list(self.context.pages)  # type: ignore[attr-defined]
        except Exception:
            return None

        # Buscar pestaña existente por dominio del portal
        if self.portal_name:
            from bot.session_config import VERIFY_URLS
            verify_url = VERIFY_URLS.get(self.portal_name, "")
            if verify_url:
                domain = verify_url.split("/")[2]  # e.g. "www.laborum.cl"
                for p in pages:
                    try:
                        if domain in p.url:
                            log.debug("[CDP] %s: reutilizando pestaña %s", self.portal_name, p.url[:60])
                            return p
                    except Exception:
                        pass

        # Buscar pestaña vacía (about:blank o nueva pestaña)
        for p in pages:
            try:
                if p.url in ("about:blank", "chrome://newtab/", ""):
                    return p
            except Exception:
                pass

        # Abrir nueva pestaña y dejarla abierta para próximos ciclos
        try:
            page = self.context.new_page()  # type: ignore[attr-defined]
            log.info("[CDP] %s: nueva pestaña abierta", self.portal_name or "?")
            return page
        except Exception as exc:
            log.warning("[CDP] No se pudo abrir pestaña: %s", exc)
            return None

    def close(self) -> None:
        # NO cerrar el contexto ni las pestañas — son del Chrome del usuario
        # Solo desconectar la referencia local
        self.context = None
        self.browser = None


class ChromiumLaunchBackend(BrowserBackend):
    """
    Fallback: lanza un Chromium aislado con perfil persistente.
    Reutiliza la primera página existente del contexto (1 página por sesión).
    """
    name = "chromium"

    def connect(self) -> bool:
        if self.context is not None:
            return True
        try:
            self.session_dir.mkdir(parents=True, exist_ok=True)
            kwargs: dict = {
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
            log.warning("[CHROMIUM] No se pudo lanzar: %s", exc)
            self.context = None
            return False

    def new_page(self) -> "Page | None":
        """Una sola página por sesión — reutiliza la existente."""
        if not self.connect():
            return None
        try:
            pages = self.context.pages  # type: ignore[attr-defined]
            return pages[0] if pages else self.context.new_page()  # type: ignore[attr-defined]
        except Exception as exc:
            log.warning("[CHROMIUM] No se pudo obtener página: %s", exc)
            return None

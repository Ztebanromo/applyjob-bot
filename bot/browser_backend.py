"""
browser_backend.py — backend de browser del bot.

CDPTabBackend: usa el Chrome del usuario via CDP (:9222).
               Una pestaña por portal, reutilizada entre ciclos.
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

    def new_page(self) -> "Page | None":
        raise NotImplementedError

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
        """
        Al terminar el portal: navega la pestaña a about:blank para dejarla
        disponible para el próximo portal sin acumular URLs de trabajo.
        NO cierra la pestaña — es el Chrome del usuario.
        """
        if self.context:
            try:
                pages = list(self.context.pages)
                if self.portal_name:
                    from bot.session_config import VERIFY_URLS
                    domain = VERIFY_URLS.get(self.portal_name, "").split("/")[2] if VERIFY_URLS.get(self.portal_name) else ""
                    for p in pages:
                        try:
                            if domain and domain in p.url:
                                p.goto("about:blank", wait_until="commit", timeout=3_000)
                                break
                        except Exception:
                            pass
            except Exception:
                pass
        self.context = None
        self.browser = None

    def save_state_only(self, filepath: str | Path) -> bool:
        """
        Guarda el storage_state (cookies + localStorage) SIN cerrar contexto,
        filtrado al dominio del portal.

        El contexto CDP es el perfil COMPARTIDO del bot (todo lo que ha
        navegado: ad-tech, claude.ai, youtube, otros portales...). Guardar
        ese storage_state completo produce JSONs de miles de cookies ajenas
        al portal — ruido, riesgo de privacidad y nada que ayude a verificar
        la sesión real. Filtramos a cookies/origins cuyo dominio pertenece al
        portal, así playwright_state.json refleja SOLO la sesión relevante.
        """
        if not self.context:
            return False
        try:
            import json
            from pathlib import Path

            filepath = Path(filepath)
            filepath.parent.mkdir(parents=True, exist_ok=True)

            state = self.context.storage_state()
            domain_keys = self._portal_domain_keys()

            cookies = state.get("cookies", [])
            origins = state.get("origins", [])
            if domain_keys:
                cookies = [
                    c for c in cookies
                    if any(k in (c.get("domain") or "").lower() for k in domain_keys)
                ]
                origins = [
                    o for o in origins
                    if any(k in (o.get("origin") or "").lower() for k in domain_keys)
                ]

            filtered = {"cookies": cookies, "origins": origins}
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(filtered, f, ensure_ascii=False, indent=2)

            log.info(
                "[CDP] save_state_only: %d cookies (%d origins) guardadas en %s (filtrado: %s)",
                len(cookies), len(origins), filepath.name, domain_keys or "sin filtro",
            )
            return True
        except Exception as exc:
            log.warning("[CDP] save_state_only: error guardando estado: %s", exc)
            return False

    def _portal_domain_keys(self) -> list[str]:
        """
        Deriva fragmentos de dominio (ej. 'linkedin.com') a partir de
        VERIFY_URLS/LOGIN_URLS del portal, para filtrar cookies/origins.
        """
        if not self.portal_name:
            return []
        try:
            from bot.session_config import VERIFY_URLS, LOGIN_URLS
        except Exception:
            return []

        keys: set[str] = set()
        for table in (VERIFY_URLS, LOGIN_URLS):
            url = table.get(self.portal_name, "")
            if not url:
                continue
            try:
                host = url.split("/")[2].lower()
            except Exception:
                continue
            parts = host.split(".")
            if len(parts) >= 2:
                keys.add(".".join(parts[-2:]))  # ej. "linkedin.com"
            keys.add(host)
        return sorted(keys)

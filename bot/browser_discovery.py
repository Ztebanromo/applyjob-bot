"""
browser_discovery.py — selección de backend de browser.

Orden de prioridad:
  1. CDPTabBackend   → Chrome del usuario (una pestaña por portal)
  2. ChromiumLaunchBackend → Chromium aislado (fallback)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from bot.browser_backend import BrowserBackend, CDPTabBackend, ChromiumLaunchBackend, _cdp_port_open

log = logging.getLogger(__name__)


def find_chrome_executable() -> str | None:
    candidates = [
        r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe",
        r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe",
        r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    ]
    for c in candidates:
        p = Path(os.path.expandvars(c))
        if p.exists():
            return str(p)
    return None


def select_browser_backend(
    pw,
    session_dir: str | Path,
    *,
    headless: bool = False,
    user_agent: str | None = None,
    args: list[str] | None = None,
    ignore_default_args: list[str] | None = None,
    locale: str = "es-CL",
    timezone_id: str = "America/Santiago",
    portal_name: str = "",
    prefer_cdp: bool = True,
) -> BrowserBackend | None:
    """
    Selecciona el mejor backend disponible.

    Con Chrome abierto (chrome_debug.bat):
        → CDPTabBackend: una pestaña en el Chrome del usuario por portal

    Sin Chrome con debug port:
        → ChromiumLaunchBackend: Chromium aislado (fallback)
    """
    if pw is None:
        return None

    common = dict(
        headless=headless,
        user_agent=user_agent,
        args=args,
        ignore_default_args=ignore_default_args,
        locale=locale,
        timezone_id=timezone_id,
        portal_name=portal_name,
    )

    # 1. CDP — Chrome del usuario (donde está el dashboard)
    if prefer_cdp and _cdp_port_open():
        backend = CDPTabBackend(pw, session_dir, **common)
        if backend.connect():
            log.info("[BACKEND] Chrome del usuario via CDP (portal=%s)", portal_name or "?")
            return backend
        log.debug("[BACKEND] CDP disponible pero conexión falló")

    # 2. Chromium aislado — fallback
    chrome_exe = find_chrome_executable()
    backend = ChromiumLaunchBackend(
        pw, session_dir, executable_path=chrome_exe, **common
    )
    if backend.connect():
        log.info("[BACKEND] Chromium (%s, portal=%s)", chrome_exe or "playwright", portal_name or "?")
        return backend

    log.warning("[BACKEND] No se pudo inicializar ningún backend")
    return None

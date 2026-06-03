from __future__ import annotations

import logging
import os
from pathlib import Path

from bot.browser_backend import BrowserBackend, ChromiumLaunchBackend

log = logging.getLogger(__name__)


def _expand(candidate: str) -> Path:
    return Path(os.path.expandvars(candidate))


def find_chrome_executable() -> str | None:
    candidates = [
        r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe",
        r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe",
        r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    ]
    for path in (_expand(c) for c in candidates):
        if path.exists():
            return str(path)
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
    prefer_cdp: bool = False,   # ignorado — solo Chromium
) -> BrowserBackend | None:
    if pw is None:
        return None

    chrome_exe = find_chrome_executable()
    backend = ChromiumLaunchBackend(
        pw,
        session_dir,
        headless=headless,
        user_agent=user_agent,
        args=args,
        ignore_default_args=ignore_default_args,
        locale=locale,
        timezone_id=timezone_id,
        executable_path=chrome_exe,
    )
    if backend.connect():
        log.info("[BACKEND] Chromium (%s)", chrome_exe or "playwright chromium")
        return backend

    log.warning("[BACKEND] No se pudo inicializar Chromium")
    return None

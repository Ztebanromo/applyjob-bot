"""
browser_discovery.py — selección de backend de browser.

Orden de prioridad:
  1. CDPTabBackend   → Chrome del usuario (una pestaña por portal)
  2. Auto-lanzar Chrome con debug port si no está corriendo
  (sin fallback a Chromium aislado — portales como Computrabajo bloquean Playwright)
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

from bot.browser_backend import BrowserBackend, CDPTabBackend, _cdp_port_open

log = logging.getLogger(__name__)


def _launch_chrome_debug() -> bool:
    """
    Lanza Chrome con --remote-debugging-port=9222 usando el perfil dedicado del bot.
    Retorna True si el puerto quedó abierto en los próximos 8 segundos.
    """
    chrome_exe = find_chrome_executable()
    if not chrome_exe:
        log.warning("[BROWSER] Chrome no encontrado — no se puede lanzar automáticamente.")
        return False

    bot_profile = os.path.join(os.environ.get("LOCALAPPDATA", ""), "ApplyJobBot", "ChromeProfile")
    Path(bot_profile).mkdir(parents=True, exist_ok=True)

    cmd = [
        chrome_exe,
        "--remote-debugging-port=9222",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={bot_profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "http://127.0.0.1:5000/",
    ]
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log.info("[BROWSER] Chrome lanzado con CDP en puerto 9222...")
    except Exception as exc:
        log.warning("[BROWSER] Error lanzando Chrome: %s", exc)
        return False

    # Esperar hasta 8 segundos a que el puerto esté disponible
    for _ in range(16):
        time.sleep(0.5)
        if _cdp_port_open():
            log.info("[BROWSER] Chrome CDP listo.")
            return True

    log.warning("[BROWSER] Chrome lanzado pero CDP no respondió en 8s.")
    return False


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
) -> BrowserBackend | None:
    """Conecta al Chrome del usuario via CDP. Auto-lanza Chrome si no está corriendo."""
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

    if not _cdp_port_open():
        log.info("[BACKEND] Chrome no detectado — lanzando automaticamente...")
        print("[CHROME] Lanzando Chrome con CDP + dashboard... (primera vez puede tardar 8s)", flush=True)
        _launch_chrome_debug()

    if _cdp_port_open():
        backend = CDPTabBackend(pw, session_dir, **common)
        if backend.connect():
            log.info("[BACKEND] Chrome del usuario via CDP (portal=%s)", portal_name or "?")
            return backend
        log.debug("[BACKEND] CDP disponible pero conexion fallo")

    log.warning("[BACKEND] CDP no disponible; Chrome no responde en :9222")
    return None

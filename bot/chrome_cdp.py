"""
chrome_cdp.py — Conexión al Chrome real del usuario vía CDP.

El bot opera directamente en el Chrome del usuario:
- Todas las sesiones existentes están disponibles (LinkedIn, portales, etc.)
- No necesita re-login en ningún portal
- Usa el fingerprint real del browser del usuario (anti-bot evasión)

USO:
  1. Ejecutar chrome_debug.bat (lanza Chrome con --remote-debugging-port=9222)
  2. El bot se conecta automáticamente al detectar el puerto abierto

SEGURIDAD:
  - El puerto CDP solo escucha en 127.0.0.1 (solo tu máquina)
  - No hay acceso externo posible
  - El bot solo crea nuevas pestañas — no modifica sesiones existentes
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Page

log = logging.getLogger(__name__)

CDP_PORT = 9222
CDP_URL  = f"http://127.0.0.1:{CDP_PORT}"

# Estado global de la conexión (thread-safe con lock)
_lock       = threading.Lock()
_pw         = None   # instancia de sync_playwright
_browser    = None   # Browser conectado vía CDP
_connected  = False


def is_port_open(port: int = CDP_PORT, timeout: float = 1.0) -> bool:
    """True si hay algo escuchando en 127.0.0.1:<port>."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    result = s.connect_ex(("127.0.0.1", port))
    s.close()
    return result == 0


def is_connected() -> bool:
    """True si hay una conexión CDP activa y funcionando."""
    global _browser, _connected
    with _lock:
        if not _connected or _browser is None:
            return False
        try:
            _ = _browser.contexts  # ping
            return True
        except Exception:
            _connected = False
            _browser   = None
            return False


def connect(timeout_s: float = 5.0) -> bool:
    """
    Conecta al Chrome con debug habilitado en 127.0.0.1:9222.

    Retorna True si la conexión fue exitosa.
    Reutiliza la conexión si ya está activa.
    """
    global _pw, _browser, _connected

    if is_connected():
        return True

    if not is_port_open():
        log.info("[CDP] Puerto %d no disponible — Chrome no está en modo debug.", CDP_PORT)
        return False

    with _lock:
        try:
            from playwright.sync_api import sync_playwright

            if _pw is None:
                _pw = sync_playwright().start()

            _browser   = _pw.chromium.connect_over_cdp(CDP_URL)
            _connected = True
            log.info("[CDP] Conectado al Chrome real en %s", CDP_URL)
            return True

        except Exception as exc:
            log.warning("[CDP] Error conectando: %s", exc)
            _browser   = None
            _connected = False
            return False


def disconnect() -> None:
    """Cierra la conexión CDP (no cierra Chrome)."""
    global _pw, _browser, _connected
    with _lock:
        try:
            if _browser:
                _browser.close()
        except Exception:
            pass
        try:
            if _pw:
                _pw.stop()
        except Exception:
            pass
        _browser   = None
        _pw        = None
        _connected = False
    log.info("[CDP] Desconectado.")


def get_context() -> "BrowserContext | None":
    """
    Retorna el primer contexto del Chrome (con todas las sesiones del usuario).
    Si no hay conexión activa, retorna None.
    """
    global _browser
    if not is_connected():
        return None
    try:
        contexts = _browser.contexts
        if contexts:
            return contexts[0]
        # Sin contextos → crear uno nuevo
        return _browser.new_context()
    except Exception as exc:
        log.warning("[CDP] Error obteniendo contexto: %s", exc)
        return None


def new_page() -> "Page | None":
    """
    Abre una nueva pestaña en el Chrome del usuario.
    Retorna None si no hay conexión activa.
    """
    ctx = get_context()
    if ctx is None:
        return None
    try:
        return ctx.new_page()
    except Exception as exc:
        log.warning("[CDP] Error abriendo pestaña: %s", exc)
        return None


def _check_session_cdp_impl(portal: str) -> str:
    """
    Implementación interna — corre en thread propio con conexión CDP fresca.
    Abre UNA pestaña por portal y la mantiene abierta para reutilizar.
    Las segundas verificaciones navegan en la pestaña existente (no abren una nueva).
    """
    from playwright.sync_api import sync_playwright
    from bot.session_config import (
        VERIFY_URLS, LOGGED_IN_SIGNALS, NOT_LOGGED_IN_SIGNALS,
        LOGIN_URL_KEYWORDS,
    )

    verify_url = VERIFY_URLS.get(portal)
    if not verify_url:
        return "ok"

    def _do_check(page) -> str:
        try:
            page.goto(verify_url, wait_until="domcontentloaded", timeout=20_000)
            page.wait_for_timeout(2000)
        except Exception:
            pass
        current_url = page.url.lower()
        if any(kw in current_url for kw in LOGIN_URL_KEYWORDS):
            return "expired"
        for sel in NOT_LOGGED_IN_SIGNALS.get(portal, []):
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    return "expired"
            except Exception:
                pass
        for sel in LOGGED_IN_SIGNALS.get(portal, []):
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    return "ok"
            except Exception:
                pass
        return "expired"

    try:
        with sync_playwright() as _pw:
            try:
                _browser = _pw.chromium.connect_over_cdp(CDP_URL)
            except Exception as exc:
                log.debug("[CDP] connect_over_cdp falló para %s: %s", portal, exc)
                return "no_connection"

            ctx = _browser.contexts[0] if getattr(_browser, "contexts", None) else None
            if ctx is None:
                return "no_connection"

            # Buscar pestaña existente de este portal (por URL de dominio)
            portal_domain = verify_url.split("/")[2]  # e.g. "www.laborum.cl"
            existing_page = None
            for p in ctx.pages:
                try:
                    if portal_domain in p.url:
                        existing_page = p
                        break
                except Exception:
                    pass

            if existing_page:
                log.debug("[CDP] %s: reutilizando pestaña existente (%s)", portal, existing_page.url[:50])
                return _do_check(existing_page)

            # Abrir nueva pestaña y dejarla abierta
            page = ctx.new_page()
            if page is None:
                return "error"
            log.debug("[CDP] %s: abriendo pestaña nueva (queda abierta)", portal)
            return _do_check(page)
            # NO cerramos la página → queda abierta para próximas verificaciones

    except Exception as exc:
        log.warning("[CDP] Error verificando %s: %s", portal, exc)
        return "error"


def check_session_cdp(portal: str) -> str:
    """
    Verifica sesión del portal via CDP.
    Retorna: 'ok' | 'expired' | 'no_connection' | 'error'

    Corre en un thread separado para aislar el event loop de asyncio
    y evitar conflicto con sync_playwright del motor principal.
    """
    import concurrent.futures

    if not is_port_open():
        return "no_connection"

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_check_session_cdp_impl, portal)
            return future.result(timeout=25)
    except concurrent.futures.TimeoutError:
        log.warning("[CDP] Timeout verificando %s", portal)
        return "error"
    except Exception as exc:
        log.warning("[CDP] Error en thread CDP para %s: %s", portal, exc)
        return "error"


def get_status() -> dict:
    """
    Retorna el estado actual de la conexión CDP.
    Usado por el endpoint /api/cdp-status.
    """
    port_open  = is_port_open()
    connected  = is_connected()
    n_contexts = 0
    n_pages    = 0

    if connected and _browser:
        try:
            contexts   = _browser.contexts
            n_contexts = len(contexts)
            n_pages    = sum(len(ctx.pages) for ctx in contexts)
        except Exception:
            pass

    return {
        "port_open":  port_open,
        "connected":  connected,
        "cdp_url":    CDP_URL,
        "contexts":   n_contexts,
        "pages":      n_pages,
        "mode":       "chrome_real" if connected else ("port_ready" if port_open else "playwright"),
    }


def save_all_sessions() -> dict[str, int]:
    """
    Si CDP está conectado, guarda playwright_state.json de todos los portales.
    Llama a import_all_from_cdp usando la conexión activa.
    Retorna dict {portal: n_cookies} — vacío si CDP no está conectado.
    """
    if not is_connected():
        log.debug("[CDP] save_all_sessions: sin conexión activa.")
        return {}
    try:
        from bot.session_importer import import_all_from_cdp
        results = import_all_from_cdp()
        saved = {p: n for p, n in results.items() if n > 0}
        if saved:
            log.info("[CDP] Auto-save: %d portales guardados (%s).",
                     len(saved), ", ".join(saved))
        return results
    except Exception as exc:
        log.warning("[CDP] Error en save_all_sessions: %s", exc)
        return {}

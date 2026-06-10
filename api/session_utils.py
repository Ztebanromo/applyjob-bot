"""api/session_utils.py — Estado de sesiones (cookies) por portal."""
from __future__ import annotations

import os
import threading

from bot.session_config import PORTALS_REQUIRE_LOGIN
from bot.session_checker import check_session as _check_session_unified

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SESSIONS_DIR = os.path.join(_BASE_DIR, 'sessions')
_DATA_DIR = os.path.join(_BASE_DIR, 'data')
_RESTRICTIONS_PATH = os.path.join(_DATA_DIR, 'portal_restrictions.json')

# Evita verificaciones de sesión concurrentes (cada una abre browsers headless)
class _SessionVerifyState:
    lock = threading.Lock()
    running = False


_session_verify_state = _SessionVerifyState()

_KNOWN_PORTALS = [
    # Portales Chile
    'chiletrabajos', 'laborum', 'getonyboard', 'computrabajo', 'linkedin',
    'trabajando', 'infojobs',
    # Portales remotos internacionales (sin login, postulación externa)
    'weworkremotely', 'remotive', 'remoteco',
]

_COOKIES_MIN_BYTES = 25_000   # mismo umbral que engine.py — SQLite vacia pesa 20480B

_PORTALS_REQUIRE_LOGIN = PORTALS_REQUIRE_LOGIN


def _validate_portals(raw) -> list:
    """
    Filtra una lista de portales recibida del cliente contra la whitelist.
    Retorna solo los portales válidos. Nunca lanza excepción.
    """
    if not isinstance(raw, list):
        return []
    return [p for p in raw if isinstance(p, str) and p in _KNOWN_PORTALS]


def _portal_cookies_ok(portal: str) -> bool:
    """True si el portal tiene cookies reales guardadas. Delega a session_checker."""
    from bot.session_checker import has_real_cookies
    session_dir = os.path.join(_SESSIONS_DIR, portal)
    return has_real_cookies(session_dir)


def get_session_status() -> dict:
    """Detecta qué portales tienen sesión válida (archivo Cookies >= 35 KB)."""
    status = {}
    for portal in _KNOWN_PORTALS:
        status[portal] = _portal_cookies_ok(portal)
    return status


def _verify_session_headless(portal: str) -> str:
    """
    Wrapper sobre session_checker.check_session().
    Retorna: 'ok' | 'expired' | 'no_cookies' | 'error'
    """
    session_dir = os.path.join(_SESSIONS_DIR, portal)
    result = _check_session_unified(portal, session_dir)
    return result.value


def _clear_session_auth() -> None:
    """
    Borra datos de login de cada portal al iniciar el servidor.
    Playwright guarda el perfil en user_data_dir con esta estructura real:
      <portal>/                        ← raíz (algunos archivos aquí)
        Login Data, Web Data, LOCK…
        Default/
          Login Data, Web Data…
          Network/
            Cookies, Cookies-journal   ← cookies reales
          Local Storage/
          Session Storage/
          IndexedDB/
          Sessions/
    Solo se eliminan archivos de auth; se preservan model files y caché de Chromium.
    """
    import shutil as _shutil
    from pathlib import Path as _Path

    # Rutas relativas a cada portal_dir que contienen auth
    # Formato: ("archivo_o_dir", es_dir)
    AUTH_TARGETS_ROOT = [
        ("Login Data", False), ("Login Data-journal", False),
        ("Login Data For Account", False), ("Login Data For Account-journal", False),
        ("Web Data", False), ("Web Data-journal", False),
        ("Local Storage", True), ("Session Storage", True),
        ("IndexedDB", True), ("LOCK", False),
    ]
    AUTH_TARGETS_DEFAULT = [
        ("Login Data", False), ("Login Data-journal", False),
        ("Login Data For Account", False), ("Login Data For Account-journal", False),
        ("Web Data", False), ("Web Data-journal", False),
        ("Local Storage", True), ("Session Storage", True),
        ("IndexedDB", True), ("Sessions", True),
        ("SharedStorage", False),
    ]
    AUTH_TARGETS_DEFAULT_NETWORK = [
        ("Cookies", False), ("Cookies-journal", False),
        ("Trust Tokens", False), ("Trust Tokens-journal", False),
    ]

    sessions_dir = _Path(_BASE_DIR) / "sessions"
    if not sessions_dir.exists():
        return

    portals_cleared = set()

    def _rm(path: _Path, is_dir: bool) -> bool:
        if not path.exists():
            return False
        try:
            if is_dir:
                _shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
            return True
        except Exception:
            return False

    for portal_dir in sessions_dir.iterdir():
        if not portal_dir.is_dir():
            continue
        hit = False
        # Raíz del perfil
        for name, is_dir in AUTH_TARGETS_ROOT:
            if _rm(portal_dir / name, is_dir):
                hit = True
        # Default/
        default = portal_dir / "Default"
        for name, is_dir in AUTH_TARGETS_DEFAULT:
            if _rm(default / name, is_dir):
                hit = True
        # Default/Network/
        net = default / "Network"
        for name, is_dir in AUTH_TARGETS_DEFAULT_NETWORK:
            if _rm(net / name, is_dir):
                hit = True
        if hit:
            portals_cleared.add(portal_dir.name)

    if portals_cleared:
        print(f"[SESSION] Login limpiado en {len(portals_cleared)} portal(es): "
              + ", ".join(sorted(portals_cleared)))
    else:
        print("[SESSION] No había datos de login previos.")

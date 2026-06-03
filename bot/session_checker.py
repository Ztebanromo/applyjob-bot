"""
session_checker.py — Verificación de sesión headless, portal-agnóstica.

Uso:
    from bot.session_checker import check_session, SessionResult

    result = check_session("laborum", "sessions/laborum")
    if result == SessionResult.OK:
        ...
"""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import tempfile
import time
from enum import Enum
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None  # type: ignore[assignment]

log = logging.getLogger(__name__)


class SessionResult(str, Enum):
    OK         = "ok"
    EXPIRED    = "expired"
    NO_COOKIES = "no_cookies"
    ERROR      = "error"


def has_real_cookies(session_dir: str | Path) -> bool:
    """
    True si el directorio de sesión tiene cookies reales (COUNT > 0 en SQLite).
    No abre ningún browser — solo lee el archivo.
    """
    cookies_path = Path(session_dir) / "Default" / "Network" / "Cookies"
    if not cookies_path.exists():
        return False
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name
        shutil.copy2(cookies_path, tmp_path)
        conn = sqlite3.connect(tmp_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='cookies'"
        )
        if not cursor.fetchone():
            conn.close()
            return False
        cursor.execute("SELECT COUNT(*) FROM cookies")
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0
    except Exception as exc:
        log.debug("[COOKIE_CHECK] Error leyendo cookies de %s: %s", session_dir, exc)
        try:
            return cookies_path.stat().st_size > 20480
        except OSError:
            return False
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def check_session(
    portal: str,
    session_dir: str | Path,
    *,
    timeout_ms: int = 25_000,
    extra_wait_ms: int = 2500,
) -> SessionResult:
    """
    Verifica si la sesión del portal está activa.

    Proceso:
      1. Sin cookies reales → NO_COOKIES (sin abrir browser)
      2. playwright_state.json reciente (< 4h) → OK (shortcut)
      3. Abre browser headless, navega a VERIFY_URLS[portal]
      4. Chequea URL, señales negativas y positivas
      5. Cierra y retorna SessionResult

    Args:
        portal:        Nombre del portal (debe existir en session_config)
        session_dir:   Ruta al directorio de sesión Playwright del portal
        timeout_ms:    Timeout de navegación (ms)
        extra_wait_ms: Espera extra para render JS en SPAs

    Returns:
        SessionResult enum value
    """
    from bot.session_config import (
        VERIFY_URLS,
        LOGGED_IN_SIGNALS,
        NOT_LOGGED_IN_SIGNALS,
        LOGIN_URL_KEYWORDS,
        STEALTH_USER_AGENT,
        STEALTH_ARGS,
        STEALTH_IGNORE_DEFAULT_ARGS,
        STEALTH_INIT_SCRIPT,
    )

    session_dir = Path(session_dir)


    # Paso 1: atajo rápido sin browser
    if not has_real_cookies(session_dir):
        return SessionResult.NO_COOKIES

    # Paso 2: playwright_state.json reciente → confiar
    state_file = session_dir / "playwright_state.json"
    try:
        if state_file.exists():
            age_h = (time.time() - state_file.stat().st_mtime) / 3600
            if age_h < 4:
                log.debug(
                    "[SESSION_CHECK] %s: playwright_state.json %.1fh → OK", portal, age_h
                )
                return SessionResult.OK
    except Exception:
        pass

    verify_url = VERIFY_URLS.get(portal)
    if not verify_url:
        return SessionResult.OK  # sin URL → asumir ok si cookies existen

    pos_sels = LOGGED_IN_SIGNALS.get(portal, [])
    neg_sels = NOT_LOGGED_IN_SIGNALS.get(portal, ["input[type='password']"])

    try:
        if sync_playwright is None:
            log.warning("[SESSION_CHECK] Playwright no disponible.")
            return SessionResult.ERROR

        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                str(session_dir),
                headless            = True,
                user_agent          = STEALTH_USER_AGENT,
                args                = STEALTH_ARGS,
                ignore_default_args = STEALTH_IGNORE_DEFAULT_ARGS,
                viewport            = {"width": 1280, "height": 800},
                locale              = "es-CL",
                timezone_id         = "America/Santiago",
            )
            pg = ctx.new_page()
            pg.add_init_script(STEALTH_INIT_SCRIPT)
            try:
                try:
                    pg.goto(verify_url, wait_until="networkidle", timeout=timeout_ms)
                except Exception:
                    try:
                        pg.goto(verify_url, wait_until="domcontentloaded", timeout=timeout_ms)
                        pg.wait_for_timeout(extra_wait_ms)
                    except Exception:
                        pass

                pg.wait_for_timeout(extra_wait_ms)

                # Chequeo 1: URL con keyword de login → expirada
                current_url = pg.url.lower()
                if any(kw in current_url for kw in LOGIN_URL_KEYWORDS):
                    log.debug("[SESSION_CHECK] %s: login redirect: %s", portal, current_url)
                    return SessionResult.EXPIRED

                # Chequeo 2: señales negativas visibles → expirada
                for sel in neg_sels:
                    try:
                        el = pg.query_selector(sel)
                        if el and el.is_visible():
                            log.debug("[SESSION_CHECK] %s: neg selector: %s", portal, sel)
                            return SessionResult.EXPIRED
                    except Exception:
                        pass

                # Chequeo 3: señales positivas → ok
                if pos_sels:
                    for sel in pos_sels:
                        try:
                            el = pg.query_selector(sel)
                            if el and el.is_visible():
                                return SessionResult.OK
                        except Exception:
                            pass

                    # Segunda vuelta con wait (para SPAs lentas)
                    for sel in pos_sels[:3]:
                        try:
                            pg.wait_for_selector(sel, timeout=4_000)
                            return SessionResult.OK
                        except Exception:
                            pass

                    log.debug(
                        "[SESSION_CHECK] %s: sin señal positiva. Tried: %s",
                        portal, pos_sels,
                    )
                    return SessionResult.EXPIRED
                else:
                    return SessionResult.OK

            finally:
                try:
                    ctx.close()
                except Exception:
                    pass

    except Exception as exc:
        log.warning("[SESSION_CHECK] Error verificando %s: %s", portal, exc)
        return SessionResult.ERROR


def is_logged_in_on_page(page, portal: str) -> bool:
    """
    Verifica si un usuario está logueado en una página de Playwright.
    
    Lógica de 3 capas:
      1. Señal negativa: si hay form de login visible → NO logueado
      2. Señal positiva: si hay avatar/menú de usuario visible → logueado
      3. Fallback: URL en dominio correcto y no es ruta de login → asumir logueado
    
    Args:
        page: Objeto Page de Playwright
        portal: Nombre del portal (ej: "linkedin", "laborum")
    
    Returns:
        bool: True si está logueado, False en caso contrario
    """
    from bot.session_config import (
        LOGGED_IN_SIGNALS, NOT_LOGGED_IN_SIGNALS, LOGIN_URL_KEYWORDS
    )
    from urllib.parse import urlparse
    
    try:
        current_url = page.url or ""
    except Exception:
        return False
    
    # Selectores negativos FUERTES — solo en páginas de login real
    _strong_neg = [
        "input[type='password']",
        "input[name='email'][placeholder*='mail']",
        "form[action*='login']",
        "form[action*='iniciar']",
        "form[action*='signin']",
        "#session_key",
        ".sign-in-form",
    ]
    # Negativos adicionales por portal
    _portal_neg = NOT_LOGGED_IN_SIGNALS.get(portal, [])
    
    # 1. Señal negativa → NO logueado
    for sel in _strong_neg + _portal_neg:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                return False
        except Exception:
            pass
    
    # 2. Señal positiva → logueado
    session_sels = LOGGED_IN_SIGNALS.get(portal, [])
    for sel in session_sels:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                return True
        except Exception:
            pass
    
    # 3. Fallback: URL check
    _portal_domains = {
        "linkedin":      "linkedin.com",
        "computrabajo":  "computrabajo.com",
        "laborum":       "laborum.cl",
        "trabajando":    "trabajando.cl",
        "infojobs":      "infojobs.net",
        "chiletrabajos": "chiletrabajos.cl",
        "getonyboard":   "getonbrd.com",
        "indeed":        "indeed.com",
    }
    expected_domain = _portal_domains.get(portal, "")
    try:
        cur_domain = urlparse(current_url).netloc.lower()
    except Exception:
        cur_domain = ""
    
# ChileTrabajos usa la homepage como URL de login, así que no podemos
    # asumir sesión activa solo por dominio si no hay señales explícitas.
    if portal == "chiletrabajos":
        return False

    if (current_url and expected_domain
            and expected_domain in cur_domain
            and not any(k in current_url.lower() for k in LOGIN_URL_KEYWORDS)):
        return True
    
    return False


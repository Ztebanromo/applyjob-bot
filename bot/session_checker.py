"""
session_checker.py — Verificación de sesión headless, portal-agnóstica.

Uso:
    from bot.session_checker import check_session, SessionResult

    result = check_session("laborum", "sessions/laborum")
    if result == SessionResult.OK:
        ...
"""
from __future__ import annotations

import json
import logging
import os
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
    True si el directorio de sesión tiene cookies reales en playwright_state.json.
    No abre ningún browser — solo lee el archivo.
    """
    session_dir = Path(session_dir)
    state_file = session_dir / "playwright_state.json"
    
    if not state_file.exists():
        return False
    
    try:
        state_data = json.loads(state_file.read_text(encoding="utf-8"))
        cookies = state_data.get("cookies", [])
        if isinstance(cookies, list) and len(cookies) > 0:
            return True
    except Exception as exc:
        log.debug("[COOKIE_CHECK] Error leyendo %s: %s", state_file, exc)
    
    return False


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

    # Paso 2: no confiar solo en el state_file reciente.
    # Siempre verificar headless si existe VERIFY_URLS para el portal.
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
            # IMPORTANTE: NO usar launch_persistent_context(session_dir).
            # Esa API trata session_dir como perfil NATIVO de Chrome (carpeta
            # Default/ + Cookies SQLite) — pero _ensure_login guarda la sesión
            # como storage_state de Playwright en playwright_state.json (vía
            # CDP, desde el perfil compartido del bot). Son dos formatos que
            # nunca se comunican: el perfil nativo en session_dir queda vacío
            # (o solo acumula cookies de tracking de visitas headless previas),
            # y el storage_state real jamás se carga → falsos "expired".
            # Fix: lanzar browser limpio y restaurar la sesión real con
            # new_context(storage_state=...) — la forma correcta de Playwright
            # de inyectar cookies guardadas.
            browser = pw.chromium.launch(
                headless            = True,
                args                = STEALTH_ARGS,
                ignore_default_args = STEALTH_IGNORE_DEFAULT_ARGS,
            )
            state_file = session_dir / "playwright_state.json"
            ctx_kwargs = dict(
                user_agent  = STEALTH_USER_AGENT,
                viewport    = {"width": 1280, "height": 800},
                locale      = "es-CL",
                timezone_id = "America/Santiago",
            )
            if state_file.exists():
                ctx_kwargs["storage_state"] = str(state_file)
            ctx = browser.new_context(**ctx_kwargs)
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
                    if is_logged_in_on_page(pg, portal):
                        log.debug("[SESSION_CHECK] %s: no pos_sels configured, fallback is_logged_in_on_page → OK", portal)
                        return SessionResult.OK
                    return SessionResult.EXPIRED

            finally:
                try:
                    ctx.close()
                except Exception:
                    pass
                try:
                    browser.close()
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
    from bot.session_config import LOGGED_IN_SIGNALS, NOT_LOGGED_IN_SIGNALS

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
    
    # 3. Sin señal positiva ni negativa → asumir NO logueado.
    #
    # Antes había un fallback que asumía "logueado" si el dominio coincidía
    # y la URL no contenía palabras clave de login. Eso producía falsos
    # positivos graves: LOGIN_URLS para linkedin/trabajando/infojobs apunta
    # a la homepage (no a /login), así que el dominio siempre coincide y la
    # URL nunca contiene "login" — el bot daba la sesión por iniciada en
    # segundos sin que nadie escribiera credenciales, guardando cookies
    # "fantasma" que la verificación real (check_session) marca 'expired'.
    #
    # Todos los portales ya tienen LOGGED_IN_SIGNALS bien definidos (capa 2),
    # así que el default seguro es "no logueado" — preferible pedir login de
    # más a que el bot crea (falsamente) que ya tiene sesión activa.
    return False


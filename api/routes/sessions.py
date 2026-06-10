"""api/routes/sessions.py — Estado y gestión de sesiones (cookies) por portal."""
from __future__ import annotations

import json
import logging
import os

from flask import Blueprint, jsonify, request

from api.app import socketio
from api.session_utils import (
    _KNOWN_PORTALS, _PORTALS_REQUIRE_LOGIN, _RESTRICTIONS_PATH,
    _portal_cookies_ok, _verify_session_headless, _clear_session_auth,
    _session_verify_state,
)

_log = logging.getLogger("applyjob.server")

bp = Blueprint('sessions', __name__)


@bp.route('/api/known_portals')
def api_known_portals():
    """Devuelve la lista de portales conocidos y si requieren login."""
    try:
        from bot.session_config import PORTALS_REQUIRE_LOGIN
        ports = []
        for p in _KNOWN_PORTALS:
            ports.append({'name': p, 'requires_login': p in PORTALS_REQUIRE_LOGIN})
        return jsonify({'ok': True, 'portals': ports})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500


@bp.route('/api/focus-portal-tab', methods=['POST'])
def api_focus_portal_tab():
    """
    Trae al frente — DENTRO de la misma ventana del bot — la pestaña del
    portal indicado, usando el protocolo CDP (puerto 9222). Así el usuario
    no tiene que buscar manualmente dónde está el captcha/login: un click
    y Chrome cambia a esa pestaña.
    """
    try:
        import urllib.request
        data   = request.get_json() or {}
        portal = (data.get('portal') or '').strip().lower()
        if not portal:
            return jsonify({'ok': False, 'error': 'portal requerido'}), 400

        from bot.session_config import VERIFY_URLS
        verify_url = VERIFY_URLS.get(portal, '')
        domain = verify_url.split('/')[2] if verify_url and '://' in verify_url else ''

        with urllib.request.urlopen('http://127.0.0.1:9222/json', timeout=3) as r:
            tabs = json.loads(r.read().decode('utf-8'))

        target = None
        for t in tabs:
            if t.get('type') != 'page':
                continue
            url = t.get('url', '')
            if domain and domain in url:
                target = t
                break
        if target is None:
            return jsonify({'ok': False, 'error': f'No se encontro pestaña abierta para {portal}'}), 404

        target_id = target.get('id')
        with urllib.request.urlopen(f'http://127.0.0.1:9222/json/activate/{target_id}', timeout=3) as r2:
            r2.read()

        return jsonify({'ok': True, 'portal': portal, 'url': target.get('url', '')})
    except Exception as exc:
        _log.warning("/api/focus-portal-tab error: %s", exc)
        return jsonify({'ok': False, 'error': str(exc)}), 500


@bp.route('/api/check-sessions', methods=['POST'])
def api_check_sessions():
    """
    Verifica sesiones de portales via browser headless.
    Body JSON: { "portals": ["linkedin", "laborum"] }  — vacío = todos los que requieren login.
    Retorna: { portal: "ok"|"expired"|"no_cookies"|"error" }
    """
    with _session_verify_state.lock:
        if _session_verify_state.running:
            return jsonify({"error": "Verificación ya en curso"}), 429
        _session_verify_state.running = True

    try:
        requested = (request.json or {}).get("portals") or list(_PORTALS_REQUIRE_LOGIN)
        portals_to_check = [p for p in requested if p in _PORTALS_REQUIRE_LOGIN]

        results = {}

        for portal in portals_to_check:
            results[portal] = "no_cookies" if not _portal_cookies_ok(portal) else "checking"

        socketio.emit('session_check_progress', results, namespace='/bot')

        # Solo portales que tienen cookies reales requieren verificación headless
        needs_headless = [p for p in portals_to_check if results[p] == "checking"]

        if needs_headless:
            # Verificación paralela — hasta 3 browsers simultáneos
            from concurrent.futures import ThreadPoolExecutor, as_completed

            def _check_one(portal):
                r = _verify_session_headless(portal)
                socketio.emit('session_check_progress', {portal: r}, namespace='/bot')
                return portal, r

            with ThreadPoolExecutor(max_workers=3) as ex:
                futures = {ex.submit(_check_one, p): p for p in needs_headless}
                for fut in as_completed(futures):
                    try:
                        portal, result = fut.result()
                        results[portal] = result
                    except Exception:
                        portal = futures[fut]
                        results[portal] = "error"
                        socketio.emit('session_check_progress', {portal: "error"}, namespace='/bot')

        # Emitir estado final basado en resultados verificados, no solo en la existencia de cookies.
        socketio.emit('session_status', {p: results[p] == 'ok' for p in results}, namespace='/bot')
        return jsonify(results)
    finally:
        with _session_verify_state.lock:
            _session_verify_state.running = False


@bp.route('/api/import-chrome-cookies', methods=['POST'])
def api_import_chrome_cookies():
    """Intenta importar sesiones desde Chrome (CDP) a `sessions/<portal>/playwright_state.json`.

    Body JSON: { "portals": ["laborum","chiletrabajos"] }  — si vacío, importa todos.
    Retorna: { portal: 'imported'|'no_cookies'|'error' }
    """
    data = request.json or {}
    portals = data.get('portals')
    try:
        from bot.session_importer import import_all_from_cdp
        # Intentar la URL por defecto (9222)
        res = import_all_from_cdp(cdp_url=os.getenv('CDP_URL', 'http://127.0.0.1:9222'), portals=portals)
        return jsonify(res)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route('/api/import-all-sessions', methods=['POST'])
def api_import_all_sessions():
    """CDP eliminado — importación de sesiones no disponible."""
    return jsonify({'ok': False, 'msg': 'CDP no disponible'}), 503


@bp.route('/api/portal-restrictions')
def api_portal_restrictions():
    """Devuelve el estado de restricciones por portal."""
    try:
        if not os.path.exists(_RESTRICTIONS_PATH):
            return jsonify({})
        with open(_RESTRICTIONS_PATH, encoding='utf-8') as f:
            return jsonify(json.load(f))
    except Exception as e:
        return jsonify({"error": str(e)})


@bp.route('/api/reset-sessions', methods=['POST'])
def api_reset_sessions():
    """Borra cookies de todos los portales para forzar re-login en el próximo run."""
    _clear_session_auth()
    return jsonify({'ok': True, 'msg': 'Sesiones borradas — el bot pedirá login en cada portal'})

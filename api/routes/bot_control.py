"""api/routes/bot_control.py — Control de ejecución del bot (scan / apply / stop / reset)."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time

from flask import Blueprint, jsonify, request

from api.app import socketio
from api.state import state
from api.config_utils import _make_child_env
from api.process_utils import STOP_SIGNAL_PATH, PORTAL_TIMEOUT_S, MASTER_TIMEOUT_S
from api.qa_utils import _SCAN_QUEUE_PATH, _QUICK_LINKS_PATH, _PENDING_Q_PATH, _DATA_DIR
from api.session_utils import _RESTRICTIONS_PATH, get_session_status, _clear_session_auth
from api.verification import _verify_postulations
from api.bot_runner import _resolve_portals, _stream_process, _start_watchdog, _do_stop

_log = logging.getLogger("applyjob.server")

bp = Blueprint('bot_control', __name__)


@bp.route('/api/bot-state')
def api_bot_state():
    """Estado mínimo del bot para sincronización al reconectar el socket."""
    scan_live  = state.scan_process  is not None and state.scan_process.poll()  is None
    apply_live = state.apply_process is not None and state.apply_process.poll() is None
    return jsonify({
        "running":       state.is_active,
        "scan_active":   state.scan_active,
        "apply_active":  state.apply_active,
        "process_active": scan_live or apply_live,
    })


@bp.route('/verify_postulations', methods=['POST'])
def verify_postulations():
    """Verifica si las postulaciones aplicadas hoy aparecen en Mis postulaciones."""
    try:
        data = request.get_json() or {}
        portals = data.get('portals')
        summary = _verify_postulations(portals)
        return jsonify({'ok': True, 'summary': summary})
    except Exception as exc:
        _log.warning("/verify_postulations error: %s", exc)
        return jsonify({'ok': False, 'error': str(exc)}), 500


@bp.route('/api/scan', methods=['POST'])
def api_scan():
    """Escanea ofertas sin postular — corre independiente del proceso de apply."""
    if state.scan_active:
        return jsonify({'ok': False, 'msg': 'Ya hay un scan en curso.'})
    data    = request.json or {}
    portals = _resolve_portals(data)
    label   = portals or 'todos los portales'

    def _run():
        run_id = state.start_scan()
        socketio.emit('bot_status', state.get_status() | {"status": "scan_started"}, namespace='/bot')
        try:
            cmd = [sys.executable, "-u", "main.py", "--scan"]
            if portals:
                cmd += ["--portal", portals]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding='utf-8', errors='replace', bufsize=1,
                                    env=_make_child_env())
            state.set_scan_process(proc)
            _start_watchdog(proc, PORTAL_TIMEOUT_S, f"scan-{portals or 'all'}")
            def _finish():
                if state.finish_scan(run_id):
                    socketio.emit('bot_status', state.get_status() | {"status": "scan_finished"}, namespace='/bot')
            _stream_process(proc, "\n[SCAN] Escaneo completado.\n", _finish)
        except Exception as e:
            state.add_log(f"\n[SCAN] Error: {e}\n")
            state.finish_scan(run_id)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'ok': True, 'msg': f'Scan iniciado para {label}'})


# /api/postular eliminado — el frontend usa socket.emit('start_master') directamente


@bp.route('/api/apply_queue', methods=['POST'])
def api_apply_queue():
    """Aplica a ofertas en cola (scan previo). Independiente del scan."""
    if state.apply_active:
        return jsonify({'ok': False, 'msg': 'Ya hay una postulación en curso.'})
    data    = request.json or {}
    portals = _resolve_portals(data)
    label   = portals or 'todos los portales'

    def _run():
        run_id = state.start_apply()
        socketio.emit('bot_status', state.get_status() | {"status": "started"}, namespace='/bot')
        try:
            cmd = [sys.executable, "-u", "main.py", "--apply-queue"]
            if portals:
                cmd += ["--portal", portals]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding='utf-8', errors='replace', bufsize=1,
                                    env=_make_child_env())
            state.set_apply_process(proc)
            _start_watchdog(proc, PORTAL_TIMEOUT_S, f"apply-queue-{portals or 'all'}")
            def _finish():
                _verify_postulations(portals)
                if state.finish_apply(run_id):
                    socketio.emit('bot_status', state.get_status() | {"status": "finished"}, namespace='/bot')
                    from bot.state import get_stats as _get_stats
                    _s = _get_stats()
                    _by_p = _s.get("by_portal", {})
                    _applied_total = sum(v.get("applied", 0) for v in _by_p.values())
                    _ext_total     = sum(
                        sum(cnt for k, cnt in v.items() if k.startswith("external"))
                        for v in _by_p.values()
                    )
                    _err_total = sum(
                        sum(cnt for k, cnt in v.items() if k.startswith("error"))
                        for v in _by_p.values()
                    )
                    socketio.emit('run_summary', {
                        'total_applied':  state.stats.get('applied', _applied_total),
                        'total_external': _ext_total,
                        'total_filtered': 0,
                        'total_errors':   _err_total,
                        'verified_applied': state.stats.get('verified_applied', 0),
                    }, namespace='/bot')
            _stream_process(proc, "\n[APPLY-QUEUE] Completado.\n", _finish)
        except Exception as e:
            state.add_log(f"\n[APPLY-QUEUE] Error: {e}\n")
            state.finish_apply(run_id)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'ok': True, 'msg': f'Apply-queue iniciado para {label}'})


@bp.route('/api/reset-all', methods=['POST'])
def api_reset_all():
    """
    Limpia todo el estado persistente:
    - Cola de scan (scan_queue.json)
    - Quick links (quick_links.json)
    - Restricciones de portales (portal_restrictions.json)
    - Stats del optimizer de keywords (keyword_stats.json)
    - Señal de stop (STOP_SIGNAL) si quedó colgada
    No toca: .env, sessions/, uploads/, logs/, ni la DB de postulaciones.
    """
    if state.is_active:
        return jsonify({'ok': False, 'msg': 'Detén el bot antes de limpiar.'}), 409

    cleared = []
    errors  = []

    _files = [
        (_SCAN_QUEUE_PATH,   'Cola de scan'),
        (_QUICK_LINKS_PATH,  'Quick links'),
        (_RESTRICTIONS_PATH, 'Restricciones de portales'),
        (STOP_SIGNAL_PATH,   'Señal de stop'),
        (os.path.join(_DATA_DIR, 'keyword_stats.json'), 'Stats de keywords'),
    ]
    for path, label in _files:
        try:
            if os.path.exists(path):
                os.remove(path)
                cleared.append(label)
        except Exception as e:
            errors.append(f'{label}: {e}')

    state.clear_logs()
    socketio.emit('bot_status', state.get_status() | {'status': 'reset'}, namespace='/bot')
    return jsonify({'ok': True, 'cleared': cleared, 'errors': errors})


@bp.route('/api/limpiar-todo', methods=['POST'])
def api_limpiar_todo():
    """
    Limpieza total: borra sesiones, cola de scan, quick links,
    restricciones, stats, señal de stop y logs.
    No toca: .env, uploads/, ni la DB de postulaciones.
    """
    if state.is_active:
        return jsonify({'ok': False, 'msg': 'Detén el bot antes de limpiar.'}), 409

    cleared = []
    errors  = []

    # 1. Sesiones (cookies de todos los portales)
    try:
        _clear_session_auth()
        cleared.append('Sesiones (cookies)')
    except Exception as e:
        errors.append(f'Sesiones: {e}')

    # 2. Archivos de estado persistente
    _files = [
        (_SCAN_QUEUE_PATH,   'Cola de scan'),
        (_QUICK_LINKS_PATH,  'Quick links'),
        (_RESTRICTIONS_PATH, 'Restricciones de portales'),
        (STOP_SIGNAL_PATH,   'Señal de stop'),
        (os.path.join(_DATA_DIR, 'keyword_stats.json'), 'Stats de keywords'),
        (_PENDING_Q_PATH,    'Preguntas pendientes'),
    ]
    for path, label in _files:
        try:
            if os.path.exists(path):
                os.remove(path)
                cleared.append(label)
        except Exception as e:
            errors.append(f'{label}: {e}')

    # 3. Limpiar logs en memoria
    state.clear_logs()

    socketio.emit('bot_status', state.get_status() | {'status': 'reset'}, namespace='/bot')
    socketio.emit('session_status', get_session_status(), namespace='/bot')
    return jsonify({'ok': True, 'cleared': cleared, 'errors': errors})


@bp.route('/api/stop', methods=['POST'])
def api_stop():
    """Endpoint HTTP de stop — fallback por si el SocketIO no llega."""
    killed = _do_stop()
    return jsonify({'ok': True, 'killed': killed})


@bp.route('/api/login-portals', methods=['POST'])
def api_login_portals():
    """Abre browser visible para login manual en los portales indicados."""
    data    = request.get_json(force=True) or {}
    portals = data.get('portals', [])
    if not portals:
        return jsonify({'ok': False, 'error': 'Sin portales'}), 400

    # Bloquear SCAN/POSTULAR antes del thread — evita race condition
    with state.lock:
        state.apply_active = True

    def _run_login():
        # Detener bot activo dentro del thread — no bloquea el HTTP handler
        if state.scan_process and state.scan_process.poll() is None:
            state.stop_process()
            time.sleep(1.5)

        try:
            cmd = [sys.executable, "-u", "login_portals.py"] + portals
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace',
                bufsize=1,
                env=_make_child_env(),
            )
            with state.lock:
                state.apply_process = proc
            socketio.emit('bot_status', {'status': 'login_started', 'portals': portals}, namespace='/bot')
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    state.add_log(line)
            proc.wait()
        except Exception as e:
            state.add_log(f"[LOGIN] Error: {e}")
        finally:
            with state.lock:
                state.apply_active  = False
                state.apply_process = None
            socketio.emit('bot_status', {'status': 'login_finished'}, namespace='/bot')
            socketio.emit('session_status', get_session_status(), namespace='/bot')

    t = threading.Thread(target=_run_login, daemon=True)
    t.start()
    return jsonify({'ok': True})

"""api/sockets.py — Handlers SocketIO del namespace /bot."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time

from flask_socketio import emit

from api.app import socketio
from api.state import state
from api.config_utils import _make_child_env, clean_form_value, _SENSITIVE_ENV_KEYS
from api.process_utils import PORTAL_TIMEOUT_S, MASTER_TIMEOUT_S
from api.qa_utils import _SCAN_QUEUE_PATH
from api.session_utils import _KNOWN_PORTALS, _validate_portals, get_session_status
from api.verification import _verify_postulations
from api.bot_runner import _start_watchdog, _stream_process, _do_stop

_log = logging.getLogger("applyjob.server")


@socketio.on('connect', namespace='/bot')
def handle_connect():
    print("Cliente conectado al canal /bot")
    emit('bot_status', state.get_status())
    emit('session_status', get_session_status())


@socketio.on('start_master', namespace='/bot')
def handle_start(data):
    """Botón Postular → postulación directa independiente (no necesita scan previo)."""
    _log.info("start_master recibido: portals=%s apply_active=%s", data.get('portals', []), state.apply_active)
    if state.apply_active:
        # Verificar si el proceso aún vive — si murió, limpiar el estado colgado
        with state.lock:
            proc = state.apply_process
            if proc is None or proc.poll() is not None:
                _log.warning("apply_active=True pero proceso muerto — limpiando estado.")
                state.apply_active  = False
                state.apply_process = None
            else:
                state.add_log("\n[SISTEMA] ⚠️ Ya hay una postulación activa. Detén el bot antes de iniciar otra.\n")
                emit('bot_status', state.get_status() | {"status": "already_running"})
                return
    portals = _validate_portals(data.get('portals', []))
    if not portals:
        # Sin portales seleccionados → usar todos los habilitados por defecto
        portals = [p for p in _KNOWN_PORTALS if p != 'indeed']
        state.add_log("\n[SISTEMA] Sin portales seleccionados — usando todos los disponibles.\n")
    runtime_env = {
        key: clean_form_value(value)
        for key, value in (data.get('profile') or {}).items()
        if key in _SENSITIVE_ENV_KEYS and clean_form_value(value)
    }
    if data.get('keywords'):
        runtime_env['USER_KEYWORDS'] = clean_form_value(data.get('keywords'))
    if data.get('max_offers'):
        runtime_env['USER_MAX_OFFERS'] = clean_form_value(data.get('max_offers'))

    persistent = bool(data.get('persistent', True))  # persistente por defecto
    try:
        min_per = max(1, min(50, int(str(data.get('min_per_portal', 1)).strip())))
    except (ValueError, TypeError):
        min_per = 1

    def _run():
        run_id = state.start_apply()
        socketio.emit('bot_status', state.get_status() | {"status": "started"}, namespace='/bot')

        def _final_finish():
            # Resumen de sesión con conteo final
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
            state.add_log(
                f"\n[POSTULAR] ✅ Sesión completada — "
                f"{_applied_total} postuladas · {_ext_total} externas · {_err_total} errores\n"
            )
            # Enviar notificación por email/webhook si está configurado
            try:
                from bot.notifier import send_summary as _send_summary
                _send_summary(
                    portals=portals,
                    applied=_applied_total,
                    external=_ext_total,
                    filtered=0,
                    errors=_err_total,
                )
            except Exception as _ne:
                _log.debug("[NOTIFIER] Error enviando resumen: %s", _ne)

            # ── Paso 3: Verificar en "Mis postulaciones" que se enviaron ──────
            if _applied_total > 0:
                _verify_postulations(portals)

            if state.finish_apply(run_id):
                socketio.emit('bot_status', state.get_status() | {"status": "finished"}, namespace='/bot')
                socketio.emit('session_status', get_session_status(), namespace='/bot')

            socketio.emit('run_summary', {
                'total_applied':  state.stats.get('applied', _applied_total),
                'total_external': _ext_total,
                'total_filtered': 0,
                'total_errors':   _err_total,
                'verified_applied': state.stats.get('verified_applied', 0),
            }, namespace='/bot')

        try:
            # ── Paso 1: Búsqueda multi-keyword / persistente ─────────────────
            if persistent:
                cmd = [sys.executable, "-u", "main.py",
                       "--persistent", "--headless",
                       "--portal", ",".join(portals),
                       "--min-per-portal", str(min_per)]
            else:
                cmd = [sys.executable, "-u", "main.py", "--multi-keyword", "--headless",
                       "--portal", ",".join(portals)]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding='utf-8', errors='replace', bufsize=1,
                                    env=_make_child_env(runtime_env))
            state.set_apply_process(proc)
            _start_watchdog(proc, MASTER_TIMEOUT_S, f"master-{','.join(portals)}")
            # _stream_process verifica stop_requested cada 200ms — garantiza stop responsivo
            _stream_process(proc, "\n[POSTULAR] Búsqueda con keywords completada.\n", lambda: None)

            # ── Paso 2: Procesar cola de scan acumulado ───────────────────────
            if not state.stop_requested:
                try:
                    if os.path.exists(_SCAN_QUEUE_PATH):
                        with open(_SCAN_QUEUE_PATH, encoding='utf-8') as _qf:
                            _queue = json.load(_qf)
                        if _queue:
                            state.add_log(
                                f"\n[APPLY-QUEUE] ⏳ {len(_queue)} ofertas en cola "
                                f"— procesando ahora...\n"
                            )
                            cmd2 = [sys.executable, "-u", "main.py", "--apply-queue", "--headless",
                                    "--portal", ",".join(portals)]
                            proc2 = subprocess.Popen(
                                cmd2, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', errors='replace', bufsize=1,
                                env=_make_child_env(runtime_env)
                            )
                            state.set_apply_process(proc2)
                            _start_watchdog(proc2, PORTAL_TIMEOUT_S,
                                            f"apply-queue-{','.join(portals)}")
                            _stream_process(proc2, "\n[APPLY-QUEUE] Cola procesada.\n",
                                            lambda: None)
                except Exception as _qe:
                    state.add_log(f"\n[APPLY-QUEUE] Error procesando cola: {_qe}\n")
        except Exception as e:
            state.add_log(f"\n[POSTULAR] Error crítico: {e}\n")
        finally:
            _final_finish()

    threading.Thread(target=_run, daemon=True).start()
    socketio.emit('bot_status', state.get_status() | {"status": "starting"}, namespace='/bot')


@socketio.on('stop_master', namespace='/bot')
def handle_stop():
    _do_stop()

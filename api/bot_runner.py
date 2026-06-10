"""api/bot_runner.py — Helpers de ejecución de subprocesos del bot.

Contiene la lógica compartida para lanzar, transmitir y detener los
subprocesos `main.py` (scan / apply / postular maestro), así como el
hilo legado `run_bot_thread` usado por el scheduler.
"""
from __future__ import annotations

import json
import logging
import os
import queue as _queue
import subprocess
import sys
import threading
import time

from api.app import socketio
from api.state import state
from api.session_utils import _KNOWN_PORTALS, get_session_status
from api.process_utils import _write_stop_signal, _kill_chromium_children
from api.verification import _verify_postulations

_log = logging.getLogger("applyjob.server")


def _resolve_portals(data: dict) -> str | None:
    """Convierte el campo 'portals' (array) o 'portal' (string) en un string
    separado por comas para pasarlo como --portal a main.py.
    Si no se indica ninguno, retorna None (main.py usará _ALL_PORTALS).
    SECURITY: valida cada nombre contra la whitelist de portales conocidos."""
    _VALID = set(_KNOWN_PORTALS) | {'indeed'}
    portals = data.get('portals')  # array del frontend
    if portals and isinstance(portals, list) and portals:
        safe = [p.strip().lower() for p in portals if p.strip().lower() in _VALID]
        return ','.join(safe) if safe else None
    single = data.get('portal', '').strip().lower()
    return single if single in _VALID else None


def _stream_process(proc, finish_log: str, finish_cb, stop_check=None):
    """
    Lee stdout sin bloquear el thread principal.
    Usa un reader-thread + queue para que el check de stop_requested
    corra cada 200ms sin importar si el proceso está imprimiendo o no.
    """
    q = _queue.Queue()

    def _reader():
        try:
            for line in iter(proc.stdout.readline, ""):
                q.put(line)
        except Exception:
            pass
        finally:
            q.put(None)          # sentinel — proceso terminó

    threading.Thread(target=_reader, daemon=True).start()

    _stop = stop_check or (lambda: state.stop_requested)

    while True:
        if _stop():
            state._kill_proc(proc)
            # Vaciar la queue para no dejar el reader-thread colgado
            while True:
                try: q.get_nowait()
                except Exception: break
            break
        try:
            line = q.get(timeout=0.2)
            if line is None:        # proceso terminó normalmente
                break
            if line:
                state.add_log(line)
        except Exception:
            if proc.poll() is not None:   # proceso ya murió
                # Drenar lo que quede
                while True:
                    try:
                        line = q.get_nowait()
                        if line and line is not None:
                            state.add_log(line)
                    except Exception:
                        break
                break

    try:
        proc.stdout.close()
    except Exception:
        pass
    try:
        proc.wait(timeout=3)
    except Exception:
        pass
    # Limpiar procesos Chromium zombie que el subprocess pudo haber dejado
    if proc.pid:
        _kill_chromium_children(proc.pid)
    state.add_log(finish_log)
    finish_cb()


def _start_watchdog(process: subprocess.Popen, timeout_s: int, label: str) -> threading.Thread:
    """
    Lanza un thread daemon que mata `process` si no termina en timeout_s segundos.
    """
    def _watch():
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if process.poll() is not None:
                return          # proceso ya terminó — watchdog no necesario
            time.sleep(5)
        # Timeout agotado
        if process.poll() is None:
            _log.warning("[WATCHDOG] %s excedió %ds — matando PID %d",
                         label, timeout_s, process.pid)
            try:
                state.add_log(f"\n[WATCHDOG] ⏱ Timeout {timeout_s}s en {label} — forzando cierre.\n")
            except Exception:
                pass
            _write_stop_signal()
            time.sleep(3)
            if process.poll() is None:
                try:
                    process.kill()
                except Exception:
                    pass

    t = threading.Thread(target=_watch, daemon=True, name=f"watchdog-{label}")
    t.start()
    return t


def _do_stop():
    """Lógica compartida de stop (usada por HTTP y SocketIO)."""
    killed = state.stop_process()
    state.add_log("\n[SISTEMA] Deteniendo ejecución a solicitud del usuario.\n")

    def _emit_stopped():
        time.sleep(0.8)
        # Limpiar stop_requested para que el próximo run no quede pegado
        with state.lock:
            state.stop_requested = False
        socketio.emit('bot_status', state.get_status() | {"status": "stopped"}, namespace='/bot')

    threading.Thread(target=_emit_stopped, daemon=True).start()
    return killed


def run_bot_thread(portals, runtime_env=None):
    """Hilo legado usado por el scheduler para disparar una ejecución completa."""
    from api.config_utils import _make_child_env

    run_id = state.start_run()

    socketio.emit('bot_status', state.get_status() | {"status": "started"}, namespace='/bot')

    try:
        # Unificar portales en una sola llamada para máxima velocidad
        portal_str = ",".join(portals)
        state.add_log(f"\n[SISTEMA] Iniciando ejecución unificada: {portal_str}\n")

        cmd = [sys.executable, "-u", "main.py", "--portal", portal_str, "--multi-keyword"]
        child_env = _make_child_env(runtime_env)

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1,
                env=child_env
            )
            state.set_process(process)
            _stream_process(process, "", lambda: None)
        except Exception as e:
            state.add_log(f"\n[FALLO] Error crítico en ejecución: {str(e)}\n")
    finally:
        state.add_log("\n[SISTEMA] Ejecución maestra finalizada.\n")
        if state.finish_run(run_id):
            socketio.emit('bot_status', state.get_status() | {"status": "finished"}, namespace='/bot')
            socketio.emit('session_status', get_session_status(), namespace='/bot')
            # Emitir resumen post-run
            socketio.emit('run_summary', {
                'total_applied':  state.stats.get('applied',  0),
                'total_external': state.stats.get('external', 0),
                'total_filtered': state.stats.get('filtered', 0),
                'total_errors':   state.stats.get('errors',   0),
            }, namespace='/bot')

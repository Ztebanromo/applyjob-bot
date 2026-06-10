"""gui_server.py — Entrypoint del dashboard ApplyJob Bot.

La lógica vive en el paquete `api/`:
  - api/app.py           instancias compartidas Flask + SocketIO
  - api/auth.py          HTTP Basic Auth global
  - api/state.py         BotState (estado en memoria)
  - api/verification.py  verificación de postulaciones reales
  - api/bot_runner.py     helpers de ejecución de subprocesos
  - api/sockets.py        handlers SocketIO (/bot)
  - api/routes/*          blueprints REST por dominio
"""
import atexit
import logging as _logging
import os
import threading
import time

from dotenv import load_dotenv

from api.app import app, socketio
from api.process_utils import _write_stop_signal, _clear_stop_signal
from api.session_utils import _KNOWN_PORTALS
from api.state import state
from api.bot_runner import run_bot_thread

import api.auth        # noqa: F401 — registra _global_auth (before_request)
import api.sockets      # noqa: F401 — registra handlers SocketIO /bot
from api.routes import register_blueprints

_log = _logging.getLogger("applyjob.server")

register_blueprints()

# Limpiar señal residual al iniciar (por si el servidor se reinició abruptamente)
_clear_stop_signal()

# NOTA: scan_queue.json NO se limpia al arrancar.
# Las ofertas con preguntas desconocidas se guardan entre sesiones para que
# --apply-queue las reintente una vez el usuario haya respondido las preguntas.
# El bot aplica poda automática por antigüedad (5 días) en run_apply_queue.


# ── Scheduler ─────────────────────────────────────────────────────────────────
# Lee SCHEDULE_PORTALS y SCHEDULE_TIMES del .env y dispara el bot
# automáticamente a las horas configuradas si no hay una ejecución activa.
#
# SCHEDULE_PORTALS=linkedin,computrabajo
# SCHEDULE_TIMES=09:00,14:00,18:00
#
# El scheduler corre en un thread daemon — se inicia al arrancar el servidor y
# muere cuando el proceso principal termina. No persiste entre reinicios.
# Si el bot ya está corriendo a la hora programada, la dispara se omite silenciosamente.

def _scheduler_thread():
    """Thread daemon que dispara el bot a las horas configuradas.
    Re-lee SCHEDULE_PORTALS y SCHEDULE_TIMES en cada ciclo para reflejar
    cambios en .env sin necesidad de reiniciar el servidor.
    """
    import datetime as _dt

    _last_fired: set[tuple] = set()   # (date, HH, MM) disparados hoy

    while True:
        try:
            # Re-leer configuración en cada ciclo (soporta cambios en .env en caliente)
            load_dotenv(override=True)
            _SCHEDULE_PORTALS_RAW = os.getenv("SCHEDULE_PORTALS", "").strip()
            _SCHEDULE_TIMES_RAW   = os.getenv("SCHEDULE_TIMES",   "").strip()

            if not _SCHEDULE_PORTALS_RAW or not _SCHEDULE_TIMES_RAW:
                time.sleep(30)
                continue

            # Parsear portales
            _VALID_SCHED = set(_KNOWN_PORTALS) | {'indeed'}
            sched_portals = [p.strip().lower() for p in _SCHEDULE_PORTALS_RAW.split(",")
                             if p.strip().lower() in _VALID_SCHED]
            if not sched_portals:
                time.sleep(30)
                continue

            # Parsear horarios HH:MM
            sched_times: list[tuple[int, int]] = []
            for t in _SCHEDULE_TIMES_RAW.split(","):
                t = t.strip()
                try:
                    h, m = t.split(":")
                    sched_times.append((int(h), int(m)))
                except (ValueError, AttributeError):
                    pass

            if not sched_times:
                time.sleep(30)
                continue

            now = _dt.datetime.now()
            today = now.date()

            for h, m in sched_times:
                key = (today, h, m)
                if key in _last_fired:
                    continue   # ya se disparó hoy a esta hora

                # Ventana de ±30 s (mitad del intervalo de sleep) — evita doble disparo
                target = now.replace(hour=h, minute=m, second=0, microsecond=0)
                diff   = abs((now - target).total_seconds())
                if diff <= 30:
                    if state.is_active:
                        _log.info("[SCHEDULER] %02d:%02d — bot ya activo, omitiendo.",
                                  h, m)
                        _last_fired.add(key)  # marcar igual para no reintentar este minuto
                    else:
                        _log.info("[SCHEDULER] Disparando bot programado %02d:%02d — %s",
                                  h, m, ", ".join(sched_portals))
                        print(f"\n[SCHEDULER] ⏰ {h:02d}:{m:02d} — lanzando bot: "
                              f"{', '.join(p.upper() for p in sched_portals)}")
                        state.add_log(
                            f"\n[SCHEDULER] ⏰ {h:02d}:{m:02d} — ejecución programada iniciada.\n"
                        )
                        threading.Thread(
                            target=run_bot_thread,
                            args=(sched_portals,),
                            daemon=True,
                        ).start()
                        _last_fired.add(key)  # marcar solo tras lanzar el thread

            # Limpiar disparos de días anteriores (evitar que el set crezca)
            _last_fired = {k for k in _last_fired if k[0] == today}

        except Exception as _se:
            _log.warning("[SCHEDULER] Error en ciclo: %s", _se)

        time.sleep(30)   # comprobar cada 30 segundos


# Iniciar scheduler al arrancar el módulo (funciona tanto en __main__ como importado)
threading.Thread(target=_scheduler_thread, daemon=True, name="scheduler").start()


def _on_exit():
    """Limpieza al cerrar el servidor (Ctrl+C o kill)."""
    _log.info("[SHUTDOWN] Servidor cerrándose — limpiando procesos...")
    _write_stop_signal()
    for proc_attr in ('apply_process', 'scan_process'):
        proc = getattr(state, proc_attr, None)
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    _clear_stop_signal()
    # Las sesiones SE PRESERVAN entre reinicios del servidor.
    # El usuario puede borrarlas manualmente con el botón 🗝 Re-login.
    _log.info("[SHUTDOWN] Limpieza completada. Sesiones preservadas.")


atexit.register(_on_exit)


def _open_unified_browser(port: int) -> None:
    """
    Lanza el navegador único del bot (Chrome con CDP + perfil dedicado) apuntando
    directo al dashboard, para que TODO — dashboard, logins, captchas — ocurra
    en la misma ventana. No hace nada si ya hay un Chrome con CDP corriendo
    (evita ventanas duplicadas).
    """
    from bot.browser_backend import _cdp_port_open
    from bot.browser_discovery import _launch_chrome_debug

    if _cdp_port_open():
        _log.info("[BROWSER] Chrome con CDP ya esta corriendo — no se abre otra ventana.")
        return

    # Esperar a que el server Flask este listo antes de navegar
    import urllib.request
    url = f"http://127.0.0.1:{port}/"
    for _ in range(40):
        try:
            urllib.request.urlopen(url, timeout=0.5)
            break
        except Exception:
            time.sleep(0.25)

    print("[CHROME] Abriendo navegador unico del bot (dashboard + portales)...", flush=True)
    _launch_chrome_debug()


if __name__ == '__main__':
    port = 5000
    print(f"\n--- ApplyJob Bot API Server ---")
    print(f"URL: http://127.0.0.1:{port}")
    print(f"------------------------------\n")
    threading.Thread(target=_open_unified_browser, args=(port,), daemon=True).start()
    socketio.run(app, host='127.0.0.1', port=port, debug=True, use_reloader=False)

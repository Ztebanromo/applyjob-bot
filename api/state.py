"""api/state.py — Estado global del bot (BotState) y singleton compartido."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time as _t

from api.app import socketio
from api.process_utils import _write_stop_signal, _clear_stop_signal
from api.session_utils import _KNOWN_PORTALS, get_session_status


class BotState:
    def __init__(self):
        self.scan_process  = None   # subproceso de scan (independiente)
        self.apply_process = None   # subproceso de postulación (independiente)
        self.logs = []
        self.lock = threading.RLock()
        self.stop_requested = False
        self.scan_active  = False
        self.apply_active = False
        self.quick_links_active = False
        self.stats = {"applied": 0, "external": 0, "filtered": 0, "errors": 0, "no_nav": 0, "total": 0, "verified_applied": 0}
        self.current_portal = None
        self.intervention = None
        self.scan_run_id  = 0
        self.apply_run_id = 0

    @property
    def is_active(self):
        return self.scan_active or self.apply_active or self.quick_links_active

    @property
    def process(self):
        """Compatibilidad con código legado."""
        return self.apply_process or self.scan_process

    def add_log(self, message):
        try:
            print(f"[BOT] {message.strip()}", flush=True)
        except UnicodeEncodeError:
            enc = (sys.stdout.encoding or "ascii")
            print(f"[BOT] {message.strip()}".encode(enc, "replace").decode(enc), flush=True)
        with self.lock:
            self.logs.append(message)
            if len(self.logs) > 2000:
                self.logs.pop(0)
        # Persistir en logs/bot_YYYY-MM-DD.log
        try:
            import datetime as _dt
            _logs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
            os.makedirs(_logs_dir, exist_ok=True)
            _log_file = os.path.join(_logs_dir, f"bot_{_dt.date.today()}.log")
            with open(_log_file, "a", encoding="utf-8") as _f:
                _f.write(f"[{_dt.datetime.now().strftime('%H:%M:%S')}] {message.rstrip()}\n")
        except Exception:
            pass

        # Notificar a los clientes vía SocketIO
        socketio.emit('new_log', {"message": message}, namespace='/bot')

        # Detectar portal activo para actualizar la UI
        if "[PORTAL_ACTIVO]" in message:
            match = re.search(r"PORTAL: (\w+)", message, re.IGNORECASE)
            if match:
                self.current_portal = match.group(1).lower()
                self.intervention = None
                socketio.emit('portal_change', {"portal": self.current_portal}, namespace='/bot')

        # Actualizar contadores y emitir eventos específicos
        msg = message.strip()
        portal = self.current_portal or ""

        # --- Postulación exitosa ---
        if ("[ÉXITO]" in message or "[EXITO]" in message
                or "Postulación completada" in message
                or "-> ✅ Postulado" in message):
            with self.lock:
                self.stats["applied"] += 1
                self.stats["total"]   += 1
            title_m = re.search(r"(?:Postulación completada para|para):\s*(.+)", message)
            title = title_m.group(1).strip() if title_m else msg[:60]
            socketio.emit('update_stats', self.stats, namespace='/bot')
            socketio.emit('job_applied', {"portal": portal, "title": title, "status": "success"}, namespace='/bot')

        # --- Postulación externa registrada ---
        elif ("external_apply" in message or "-> ✅ external:" in message
              or ("[OK]" in message and "external:" in message)):
            with self.lock:
                self.stats["external"] += 1
                self.stats["total"]    += 1
            socketio.emit('update_stats', self.stats, namespace='/bot')

        # --- Sin navegación / no_navigation ---
        elif ("no_navigation" in message or "sin navegación" in message
              or "no navigation" in message.lower()):
            with self.lock:
                self.stats["no_nav"] += 1
                self.stats["total"]  += 1
            socketio.emit('update_stats', self.stats, namespace='/bot')

        # --- Filtrada / descartada ---
        elif ("[FILTRO]" in message or "skipped" in message.lower()
              or "Descartad" in message or "OFERTA_CERRADA" in message
              or "URL_MUERTA" in message or "filtrada" in message.lower()):
            with self.lock:
                self.stats["filtered"] += 1
                self.stats["total"]    += 1
            socketio.emit('update_stats', self.stats, namespace='/bot')

        # --- Error real ---
        elif ("[FALLO]" in message or "-> ❌" in message
              or re.search(r"\[OK\].*error:", message, re.IGNORECASE)):
            with self.lock:
                self.stats["errors"] += 1
                self.stats["total"]  += 1
            socketio.emit('update_stats', self.stats, namespace='/bot')
            socketio.emit('job_applied', {"portal": portal, "title": msg[:60], "status": "error"}, namespace='/bot')
        elif "[CAPTCHA-SKIP]" in message:
            # El bot ya NO espera resolución manual — solo informa que saltó la
            # oferta/portal por verificación humana. Sin modal, sin intervención.
            portal = self._portal_from_message(message) or self.current_portal or ""
            socketio.emit('captcha_skipped', {"message": message.strip(), "portal": portal}, namespace='/bot')
        elif "[SESION_NUEVA]" in message:
            # Navegador abierto para login manual antes de la sesion headless
            portal = self._portal_from_message(message) or self.current_portal or ""
            with self.lock:
                self.intervention = {"type": "login", "portal": portal, "message": message.strip()}
            socketio.emit('login_required', {
                "portal":  portal,
                "message": message.strip(),
                "manual":  True,
            }, namespace='/bot')
        elif "[SESION_INICIADA]" in message:
            with self.lock:
                self.intervention = None
            socketio.emit('login_resolved', {"message": message.strip()}, namespace='/bot')
            socketio.emit('session_status', get_session_status(), namespace='/bot')
        elif "[LOGIN_REQUERIDO]" in message:
            portal = self._portal_from_message(message) or self.current_portal or ""
            with self.lock:
                self.intervention = {"type": "login", "portal": portal, "message": message.strip()}
            socketio.emit('login_required', {"portal": portal, "message": message.strip()}, namespace='/bot')
        elif "[PREGUNTA_PENDIENTE]" in message:
            # Extraer texto de la pregunta del log y emitir evento al dashboard
            q_match = re.search(r'\[PREGUNTA_PENDIENTE\]\s*(.+)', message)
            question_text = q_match.group(1).strip() if q_match else message.strip()
            socketio.emit('pending_question', {"question": question_text}, namespace='/bot')

        elif "[BUSQUEDA]" in message:
            kw_match = re.search(r"Buscando: '(.+)' en", message)
            if kw_match:
                socketio.emit('keyword_update', {"keyword": kw_match.group(1)}, namespace='/bot')
        elif "[PROGRESO]" in message:
            prog_match = re.search(r"Aplicadas (\d+)/(\d+) en (\w+)", message)
            if prog_match:
                socketio.emit('portal_progress', {
                    "portal":   prog_match.group(3).lower(),
                    "applied":  int(prog_match.group(1)),
                    "max":      int(prog_match.group(2)),
                    "finished": False,
                }, namespace='/bot')
        elif "[PROGRESO_FINAL]" in message:
            prog_match = re.search(r"Aplicadas (\d+)/(\d+) en (\w+)", message)
            if prog_match:
                socketio.emit('portal_progress', {
                    "portal":   prog_match.group(3).lower(),
                    "applied":  int(prog_match.group(1)),
                    "max":      int(prog_match.group(2)),
                    "finished": True,
                }, namespace='/bot')

    def _portal_from_message(self, message):
        text = message.lower()
        for portal in _KNOWN_PORTALS:
            if portal in text:
                return portal
        return None

    def clear_logs(self):
        with self.lock:
            self.logs = []
            self.stats = {"applied": 0, "external": 0, "filtered": 0, "errors": 0, "no_nav": 0, "total": 0, "verified_applied": 0}
        socketio.emit('update_stats', self.stats, namespace='/bot')

    def set_process(self, proc):
        """Compatibilidad legado — asigna al proceso de apply."""
        with self.lock:
            self.apply_process = proc

    def set_scan_process(self, proc):
        with self.lock:
            self.scan_process = proc

    def set_apply_process(self, proc):
        with self.lock:
            self.apply_process = proc

    def get_status(self):
        with self.lock:
            scan_live  = self.scan_process  is not None and self.scan_process.poll()  is None
            apply_live = self.apply_process is not None and self.apply_process.poll() is None
            return {
                "running":       self.is_active,
                "scan_active":   self.scan_active,
                "apply_active":  self.apply_active,
                "process_active": scan_live or apply_live,
                "logs":          list(self.logs),
                "stats":         dict(self.stats),
                "current_portal": self.current_portal,
                "intervention":  self.intervention,
                "run_id":        self.scan_run_id + self.apply_run_id,
            }

    def _kill_proc(self, proc):
        """
        Mata el proceso y todos sus hijos.
        1. Escribe archivo señal → engine.py cierra browser Playwright limpiamente
        2. Espera hasta 2s para shutdown limpio
        3. Si aún vive: taskkill /F /T + terminate + kill (forzado)
        4. Limpia el archivo señal
        """
        _write_stop_signal()
        pid = getattr(proc, 'pid', None)

        # Dar 2 segundos para que engine.py detecte la señal y cierre solo
        for _ in range(10):            # 10 x 200ms = 2s
            if proc.poll() is not None:
                break
            _t.sleep(0.2)

        # Si todavía vive → kill forzado
        if proc.poll() is None:
            if pid:
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(pid)],
                        capture_output=True, timeout=5
                    )
                except Exception:
                    pass
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                _t.sleep(0.3)
                if proc.poll() is None:
                    proc.kill()
            except Exception:
                pass

        _clear_stop_signal()

    def stop_process(self):
        procs = []
        with self.lock:
            self.stop_requested = True
            for p in [self.scan_process, self.apply_process]:
                if p and p.poll() is None:
                    procs.append(p)
            self.scan_process  = None
            self.apply_process = None
            self.scan_active   = False
            self.apply_active  = False
            self.intervention  = None
        for p in procs:
            self._kill_proc(p)
        return bool(procs)

    # ── Scan ──────────────────────────────────────────────────────────────────
    def start_scan(self):
        with self.lock:
            self.scan_run_id += 1
            self.scan_active = True
            self.stop_requested = False
            run_id = self.scan_run_id
        return run_id

    def finish_scan(self, run_id):
        with self.lock:
            if run_id != self.scan_run_id:
                return False
            self.scan_active  = False
            self.scan_process = None
            return True

    # ── Apply ─────────────────────────────────────────────────────────────────
    def start_apply(self):
        with self.lock:
            self.apply_run_id += 1
            self.apply_active = True
            self.stop_requested = False
            self.current_portal = None
            self.intervention = None
            run_id = self.apply_run_id
        self.clear_logs()
        return run_id

    def finish_apply(self, run_id):
        with self.lock:
            if run_id != self.apply_run_id:
                return False
            self.apply_active  = False
            self.apply_process = None
            self.current_portal = None
            self.intervention  = None
            return True

    # ── Legado (usado por run_bot_thread) ─────────────────────────────────────
    def start_run(self):
        return self.start_apply()

    def finish_run(self, run_id):
        return self.finish_apply(run_id)


state = BotState()

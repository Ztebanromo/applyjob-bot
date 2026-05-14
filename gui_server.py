import json
import os
import subprocess
import threading
import time
import sys
import secrets
import re
import tempfile
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
from dotenv import load_dotenv

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', secrets.token_hex(24))

# Inicializar SocketIO con hilos (modo más compatible en Windows sin eventlet/gevent)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Asegurar directorios
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Estado global thread-safe
class BotState:
    def __init__(self):
        self.process = None
        self.logs = []
        self.lock = threading.RLock()
        self.stop_requested = False
        self.is_active = False
        self.stats = {"applied": 0, "errors": 0, "total": 0}
        self.current_portal = None
        self.intervention = None
        self.run_id = 0

    def add_log(self, message):
        print(f"[BOT] {message.strip()}", flush=True)
        with self.lock:
            self.logs.append(message)
            if len(self.logs) > 2000:
                self.logs.pop(0)
        
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
        if "[EXITO]" in message or "Postulación completada" in message:
            with self.lock:
                self.stats["applied"] += 1
            # Extraer portal del mensaje de progreso o del portal actual
            portal = self.current_portal or ""
            title_match = re.search(r"Postulación completada para: (.+)", message)
            title = title_match.group(1).strip() if title_match else ""
            socketio.emit('update_stats', self.stats, namespace='/bot')
            socketio.emit('job_applied', {"portal": portal, "title": title, "status": "success"}, namespace='/bot')
        elif "[FALLO]" in message or "Error en" in message:
            with self.lock:
                self.stats["errors"] += 1
            portal = self.current_portal or ""
            socketio.emit('update_stats', self.stats, namespace='/bot')
            socketio.emit('job_applied', {"portal": portal, "title": message.strip(), "status": "error"}, namespace='/bot')
        elif "[CAPTCHA]" in message or "CAPTCHA DETECTADO" in message.upper():
            portal = self._portal_from_message(message) or self.current_portal or ""
            with self.lock:
                self.intervention = {"type": "captcha", "portal": portal, "message": message.strip()}
            socketio.emit('captcha_required', {"message": "Verificación humana requerida en el navegador", "portal": portal}, namespace='/bot')
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
                    "portal": prog_match.group(3).lower(),
                    "applied": int(prog_match.group(1)),
                    "max": int(prog_match.group(2)),
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
            self.stats = {"applied": 0, "errors": 0, "total": 0}
        socketio.emit('update_stats', self.stats, namespace='/bot')

    def set_process(self, proc):
        with self.lock:
            self.process = proc

    def get_status(self):
        with self.lock:
            return {
                "running": self.is_active,
                "process_active": self.process is not None and self.process.poll() is None,
                "logs": list(self.logs),
                "stats": dict(self.stats),
                "current_portal": self.current_portal,
                "intervention": self.intervention,
                "run_id": self.run_id,
            }

    def stop_process(self):
        process = None
        with self.lock:
            self.stop_requested = True
            if self.process and self.process.poll() is None:
                process = self.process
            self.process = None
            self.is_active = False
            self.intervention = None
        if process:
            try:
                process.kill()
            except Exception:
                pass
            return True
        return False

    def start_run(self):
        with self.lock:
            self.run_id += 1
            self.is_active = True
            self.stop_requested = False
            self.current_portal = None
            self.intervention = None
            run_id = self.run_id
        self.clear_logs()
        return run_id

    def finish_run(self, run_id):
        with self.lock:
            if run_id != self.run_id:
                return False
            self.is_active = False
            self.process = None
            self.current_portal = None
            self.intervention = None
            return True

state = BotState()

_SESSIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sessions')
_KNOWN_PORTALS = ['chiletrabajos', 'laborum', 'getonyboard', 'computrabajo', 'linkedin', 'indeed']
_PERSISTED_ENV_KEYS = {
    'USER_KEYWORDS',
    'USER_MAX_OFFERS',
}
_SENSITIVE_ENV_KEYS = {
    'USER_FULL_NAME',
    'USER_FIRST_NAME',
    'USER_LAST_NAME',
    'USER_EMAIL',
    'USER_PHONE',
    'USER_PHONE_NUMBER',
    'USER_COUNTRY_CODE',
    'USER_COUNTRY',
    'USER_CITY',
    'USER_LINKEDIN',
    'USER_PORTFOLIO',
    'USER_CV_PATH',
    'USER_SALARY',
    'USER_YEARS_EXP',
    'USER_AVAILABILITY',
    'USER_ENGLISH_LEVEL',
    'USER_WORK_MODE',
    'USER_COVER_LETTER',
    'LABORUM_EMAIL',
    'LABORUM_PASSWORD',
}


def update_env_values(env_path: str, updates: dict, remove_keys=None) -> None:
    """Actualiza .env sin reemplazar el archivo por un temporal.

    En Windows + OneDrive, el rename atómico que usa python-dotenv puede fallar
    con PermissionError aunque el archivo sea escribible.
    """
    existing_lines = []
    seen = set()
    remove_keys = set(remove_keys or [])

    if os.path.exists(env_path):
        with open(env_path, 'r', encoding='utf-8') as f:
            existing_lines = f.readlines()

    with open(env_path, 'w', encoding='utf-8', newline='') as f:
        for line in existing_lines:
            stripped = line.strip()
            if not stripped or stripped.startswith('#') or '=' not in line:
                f.write(line)
                continue

            key, _, _ = line.partition('=')
            key = key.strip()
            if key in remove_keys:
                continue
            if key in updates:
                f.write(f"{key}={updates[key]}\n")
                seen.add(key)
            else:
                f.write(line)

        for key, value in updates.items():
            if key not in seen:
                f.write(f"{key}={value}\n")


def clean_form_value(value: str) -> str:
    value = str(value or '').strip()
    for token in ("\\'", '\\"', "\\", "'", '"'):
        value = value.replace(token, '')
    return re.sub(r'\s+', ' ', value).strip()


def get_session_status() -> dict:
    """Detecta qué portales tienen sesión guardada (carpeta sessions/<portal> no vacía)."""
    status = {}
    for portal in _KNOWN_PORTALS:
        portal_dir = os.path.join(_SESSIONS_DIR, portal)
        has_session = False
        if os.path.exists(portal_dir):
            try:
                has_session = any(True for _ in os.scandir(portal_dir))
            except OSError:
                pass
        status[portal] = has_session
    return status


def run_bot_thread(portals, runtime_env=None):
    run_id = state.start_run()
    
    socketio.emit('bot_status', state.get_status() | {"status": "started"}, namespace='/bot')
    
    try:
        # Unificar portales en una sola llamada para máxima velocidad
        portal_str = ",".join(portals)
        state.add_log(f"\n[SISTEMA] Iniciando ejecución unificada: {portal_str}\n")
        
        cmd = [sys.executable, "-u", "main.py", "--portal", portal_str, "--multi-keyword"]
        child_env = os.environ.copy()
        child_env['PYTHONUTF8']       = '1'
        child_env['PYTHONIOENCODING'] = 'utf-8'
        child_env['PYTHONLEGACYWINDOWSSTDIO'] = '0'
        if runtime_env:
            child_env.update(runtime_env)

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
            
            for line in iter(process.stdout.readline, ""):
                if line:
                    state.add_log(line)
                if state.stop_requested:
                    try: process.kill()
                    except: pass
                    break
            
            process.stdout.close()
            rc = process.wait()
            
            if rc != 0 and not state.stop_requested:
                state.add_log(f"\n[FALLO] La ejecución unificada terminó con error (código {rc}).\n")
            
        except Exception as e:
            state.add_log(f"\n[FALLO] Error crítico en ejecución: {str(e)}\n")
    finally:
        state.add_log("\n[SISTEMA] Ejecución maestra finalizada.\n")
        if state.finish_run(run_id):
            socketio.emit('bot_status', state.get_status() | {"status": "finished"}, namespace='/bot')
            socketio.emit('session_status', get_session_status(), namespace='/bot')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/config')
def api_config():
    """Devuelve las variables del .env como JSON para que el dashboard las cargue vía fetch."""
    env_data = {}
    if os.path.exists('.env'):
        with open('.env', 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    key, _, val = line.partition('=')
                    key = key.strip()
                    if key in _PERSISTED_ENV_KEYS:
                        env_data[key] = val.strip()
    return jsonify(env_data)

@app.route('/api/parse_cv', methods=['POST'])
def api_parse_cv():
    """Recibe un CV, extrae campos y borra el archivo temporal al terminar."""
    tmp_path = None
    try:
        cv_file = request.files.get('cv_file')
        if not cv_file or cv_file.filename == '':
            return jsonify({"status": "error", "message": "No se recibió archivo"})

        ext = os.path.splitext(cv_file.filename)[1].lower()
        if ext not in ('.pdf', '.docx', '.doc'):
            return jsonify({"status": "error", "message": f"Formato no soportado: {ext}. Usa PDF o DOCX."})

        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp_path = tmp.name
            cv_file.save(tmp)

        from bot.cv_parser import parse_cv
        fields = parse_cv(tmp_path)

        return jsonify({"status": "success", "fields": fields, "filename": cv_file.filename})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


@app.route('/save_config', methods=['POST'])
def save_config():
    try:
        data = request.form.to_dict()
        
        env_path = os.path.abspath('.env')
        if not os.path.exists(env_path):
            with open(env_path, 'w') as f: f.write("")

        updates = {}
        
        if 'MAX_OFFERS' in data:
            updates['USER_MAX_OFFERS'] = clean_form_value(data.pop('MAX_OFFERS'))

        for key, val in data.items():
            if key in _PERSISTED_ENV_KEYS and val.strip():
                updates[key] = clean_form_value(val)

        update_env_values(env_path, updates, remove_keys=_SENSITIVE_ENV_KEYS)
        
        return jsonify({"status": "success", "message": "Configuración operativa guardada. El CV y los datos personales no se guardan."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/stats')
def api_stats():
    """
    Devuelve estadísticas globales y por portal desde la DB SQLite.
    El dashboard las carga al iniciar para mostrar el historial acumulado.
    """
    try:
        from bot.state import get_stats
        stats = get_stats()
        by_portal = stats.get("by_portal", {})

        # Agregar by_status desde by_portal (get_stats no lo devuelve directamente)
        by_status: dict = {}
        for portal_data in by_portal.values():
            for status, cnt in portal_data.items():
                by_status[status] = by_status.get(status, 0) + cnt

        applied = by_status.get("applied", 0)
        errors  = sum(v for k, v in by_status.items() if "error" in k.lower())
        skipped = sum(v for k, v in by_status.items()
                      if k.startswith("skipped") or k.startswith("external") or k == "dry_run")

        return jsonify({
            "total":     stats.get("total", 0),
            "applied":   applied,
            "errors":    errors,
            "skipped":   skipped,
            "by_portal": by_portal,
            "by_status": by_status,
        })
    except Exception as e:
        return jsonify({"total": 0, "applied": 0, "errors": 0, "skipped": 0,
                        "by_portal": {}, "by_status": {}, "error": str(e)})


_PENDING_Q_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'pending_questions.json')

@app.route('/api/pending-questions')
def api_pending_questions():
    """Devuelve la lista de preguntas pendientes de respuesta del usuario."""
    try:
        if not os.path.exists(_PENDING_Q_PATH):
            return jsonify([])
        with open(_PENDING_Q_PATH, encoding='utf-8') as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/pending-questions/answer', methods=['POST'])
def api_answer_question():
    """Guarda la respuesta del usuario para una pregunta pendiente."""
    try:
        payload = request.get_json(force=True)
        norm    = (payload.get('norm') or '').strip().lower()[:200]
        answer  = (payload.get('answer') or '').strip()
        if not norm or not answer:
            return jsonify({"ok": False, "error": "norm y answer son requeridos"}), 400

        existing = []
        if os.path.exists(_PENDING_Q_PATH):
            with open(_PENDING_Q_PATH, encoding='utf-8') as f:
                existing = json.load(f)

        updated = False
        for entry in existing:
            if entry.get('norm', '') == norm:
                entry['answer']   = answer
                entry['answered'] = True
                updated = True
                break

        if not updated:
            return jsonify({"ok": False, "error": "Pregunta no encontrada"}), 404

        os.makedirs(os.path.dirname(_PENDING_Q_PATH), exist_ok=True)
        with open(_PENDING_Q_PATH, 'w', encoding='utf-8') as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@socketio.on('connect', namespace='/bot')
def handle_connect():
    print("Cliente conectado al canal /bot")
    emit('bot_status', state.get_status())
    emit('session_status', get_session_status())

@socketio.on('start_master', namespace='/bot')
def handle_start(data):
    print(f"[DEBUG] Solicitud de inicio recibida: {data}")
    status = state.get_status()
    if not status["running"]:
        portals = [p for p in data.get('portals', []) if p in _KNOWN_PORTALS]
        if not portals:
            emit('bot_status', state.get_status() | {"status": "idle"})
            return
        runtime_env = {
            key: clean_form_value(value)
            for key, value in (data.get('profile') or {}).items()
            if key in _SENSITIVE_ENV_KEYS and clean_form_value(value)
        }
        if data.get('keywords'):
            runtime_env['USER_KEYWORDS'] = clean_form_value(data.get('keywords'))
        if data.get('max_offers'):
            runtime_env['USER_MAX_OFFERS'] = clean_form_value(data.get('max_offers'))
        print(f"[DEBUG] Lanzando hilo para portales: {portals}")
        socketio.start_background_task(run_bot_thread, portals, runtime_env)
        socketio.emit('bot_status', {"status": "starting", "running": True, "stats": state.stats}, namespace='/bot')
    else:
        print("[DEBUG] El bot ya está en ejecución; se ignora la solicitud.")

@socketio.on('stop_master', namespace='/bot')
def handle_stop():
    state.stop_process()
    state.add_log("\n[SISTEMA] Deteniendo ejecución a solicitud del usuario.\n")
    socketio.emit('bot_status', state.get_status() | {"status": "stopped"}, namespace='/bot')

if __name__ == '__main__':
    port = 5000
    print(f"\n--- Iniciando modo maestro (producción) ---")
    print(f"URL: http://127.0.0.1:{port}")
    print(f"Servidor: SocketIO (Auto-detect)")
    print(f"-------------------------------------------\n")
    
    # socketio.run detectará automáticamente eventlet si está instalado
    app.jinja_env.auto_reload = True
    socketio.run(app, host='127.0.0.1', port=port, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)

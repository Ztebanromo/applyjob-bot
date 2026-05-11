import os
import subprocess
import threading
import time
import sys
from flask import Flask, render_template, request, jsonify
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max

# Asegurar directorios
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Estado global thread-safe
class BotState:
    def __init__(self):
        self.process = None
        self.logs = []
        self.lock = threading.Lock()
        self.stop_requested = False
        self.is_active = False  # Indica si el loop maestro (hilo) está corriendo

    def add_log(self, message):
        with self.lock:
            self.logs.append(message)
            if len(self.logs) > 3000:
                self.logs.pop(0)

    def clear_logs(self):
        with self.lock:
            self.logs = []

    def set_process(self, proc):
        with self.lock:
            self.process = proc

    def get_status(self):
        with self.lock:
            return {
                "running": self.is_active,
                "process_active": self.process is not None and self.process.poll() is None,
                "logs": list(self.logs)
            }

    def stop_process(self):
        with self.lock:
            self.stop_requested = True
            if self.process and self.process.poll() is None:
                try:
                    self.process.kill()  # Usar kill() para asegurar terminación en Windows
                except:
                    pass
                self.process = None
                return True
        return False

state = BotState()

def run_bot_thread(portals):
    with state.lock:
        state.is_active = True
        state.clear_logs()
        state.stop_requested = False
    
    try:
        for portal in portals:
            if state.stop_requested: break
            
            state.add_log(f"\n[PORTAL_ACTIVO] >>> INICIANDO PORTAL: {portal.upper()} <<<\n")
            
            cmd = [sys.executable, "main.py", "--portal", portal]
            
            try:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True
                )
                state.set_process(process)
                
                # Leer salida en tiempo real
                for line in iter(process.stdout.readline, ""):
                    if line:
                        state.add_log(line)
                    if state.stop_requested:
                        try: process.kill()
                        except: pass
                        break
                
                process.stdout.close()
                rc = process.wait()
                
                if state.stop_requested:
                    state.add_log(f"\n🛑 PORTAL {portal.upper()} INTERRUMPIDO\n")
                    break
                
                if rc != 0:
                    state.add_log(f"\n[FALLO] El proceso para {portal} terminó con error (code {rc})\n")
                else:
                    state.add_log(f"\n[PORTAL_FINALIZADO] --- PORTAL {portal.upper()} COMPLETADO ---\n")

            except Exception as e:
                state.add_log(f"\n[FALLO] ERROR EJECUTANDO PORTAL {portal}: {str(e)}\n")
    finally:
        state.add_log("\n>>> EJECUCIÓN MAESTRA FINALIZADA <<<\n")
        with state.lock:
            state.is_active = False
            state.set_process(None)


@app.route('/')
def index():
    env_data = {}
    if os.path.exists('.env'):
        with open('.env', 'r', encoding='utf-8') as f:
            for line in f:
                if '=' in line:
                    parts = line.strip().split('=', 1)
                    if len(parts) == 2:
                        env_data[parts[0]] = parts[1]
    return render_template('index.html', env=env_data)

@app.route('/save_config', methods=['POST'])
def save_config():
    try:
        data = request.form.to_dict()
        cv_file = request.files.get('cv_file')
        if cv_file and cv_file.filename != '':
            filename = secure_filename(cv_file.filename)
            cv_path = os.path.abspath(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            cv_file.save(cv_path)
            data['USER_CV_PATH'] = cv_path
        
        with open('.env', 'w', encoding='utf-8') as f:
            if 'MAX_OFFERS' in data:
                data['USER_MAX_OFFERS'] = data.pop('MAX_OFFERS')
            for key, val in data.items():
                if val: f.write(f"{key}={val}\n")
        
        return jsonify({"status": "success", "message": "Configuración guardada correctamente"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/start_bot', methods=['POST'])
def start_bot():
    status = state.get_status()
    if not status["running"]:
        portals = request.json.get('portals', [])
        thread = threading.Thread(target=run_bot_thread, args=(portals,))
        thread.start()
        return jsonify({"status": "started"})
    return jsonify({"status": "busy"})

@app.route('/stop_bot', methods=['POST'])
def stop_bot():
    state.stop_process()
    state.add_log("\n🛑 DETENIENDO EJECUCIÓN (SOLICITADO POR USUARIO)\n")
    return jsonify({"status": "stopped"})

@app.route('/logs')
def get_logs():
    return jsonify(state.get_status())

if __name__ == '__main__':
    print("Iniciando Master Mode Dashboard en http://127.0.0.1:5000")
    app.run(debug=True, port=5000, use_reloader=False)

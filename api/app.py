"""api/app.py — Instancia única de Flask + SocketIO compartida por toda la API."""
from __future__ import annotations

import os
import secrets

from flask import Flask
from flask_socketio import SocketIO

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = Flask(
    __name__,
    template_folder=os.path.join(_BASE_DIR, 'templates'),
    static_folder=os.path.join(_BASE_DIR, 'static'),
)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', secrets.token_hex(24))

# Inicializar SocketIO con hilos (modo más compatible en Windows sin eventlet/gevent)
# SECURITY: CORS restringido a localhost — nunca permitir orígenes externos
socketio = SocketIO(
    app,
    cors_allowed_origins=["http://127.0.0.1:5000", "http://localhost:5000"],
    async_mode='threading',
)

# Asegurar directorios
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

"""api/routes/config.py — Configuración (.env), keywords y CV."""
from __future__ import annotations

import logging
import os

from flask import Blueprint, jsonify, request
from dotenv import load_dotenv

from api.app import app
from api.config_utils import (
    _PERSISTED_ENV_KEYS, _PUBLIC_ENV_KEYS,
    update_env_values, clean_form_value,
)
from api.process_utils import _RateLimiter

_log = logging.getLogger("applyjob.server")

bp = Blueprint('config', __name__)

_config_rate_limiter = _RateLimiter(max_calls=10, window_s=60)


@bp.route('/api/keywords')
def api_keywords():
    """Devuelve KEYWORD_GROUPS desde config.py para que el dashboard los muestre como chips."""
    from bot.config import KEYWORD_GROUPS
    return jsonify(KEYWORD_GROUPS)


@bp.route('/api/config')
def api_config():
    """Devuelve las variables del .env como JSON para que el dashboard las cargue vía fetch.
    SECURITY: solo devuelve _PUBLIC_ENV_KEYS — nunca contraseñas ni SECRET_KEY."""
    env_data = {}
    if os.path.exists('.env'):
        with open('.env', 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    key, _, val = line.partition('=')
                    key = key.strip()
                    if key in _PUBLIC_ENV_KEYS:
                        env_data[key] = val.strip()
    return jsonify(env_data)


@bp.route('/api/location_ranges')
def api_location_ranges():
    """Devuelve, para la comuna del usuario (USER_CITY), las comunas que caen
    dentro de cada rango de distancia del slider (<=15km, <=40km)."""
    from bot.config import _COMUNA_COORDS, _haversine_km, user_comuna_coords, _find_comuna

    city = request.args.get('city', '') or os.getenv('USER_CITY', '')
    user_name, user_coords = _find_comuna(city)
    if not user_coords:
        user_name, user_coords = "maipú", _COMUNA_COORDS["maipú"]

    cercano, rm = [], []
    seen_coords = {user_coords}
    for name, coords in _COMUNA_COORDS.items():
        if coords in seen_coords:
            continue
        seen_coords.add(coords)
        dist = _haversine_km(user_coords, coords)
        if dist <= 15:
            cercano.append(name)
        elif dist <= 40:
            rm.append(name)

    return jsonify({
        "user_comuna": user_name,
        "cercano": sorted(set(cercano)),
        "rm": sorted(set(rm)),
    })


@bp.route('/api/parse_cv', methods=['POST'])
def api_parse_cv():
    """Recibe un CV, lo guarda en uploads/, extrae campos y actualiza USER_CV_PATH en .env."""
    try:
        cv_file = request.files.get('cv_file')
        if not cv_file or cv_file.filename == '':
            return jsonify({"status": "error", "message": "No se recibió archivo"})

        ext = os.path.splitext(cv_file.filename)[1].lower()
        if ext not in ('.pdf', '.docx', '.doc'):
            return jsonify({"status": "error", "message": f"Formato no soportado: {ext}. Usa PDF o DOCX."})

        # Guardar permanentemente en uploads/ (el bot lo usa al postular)
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        safe_name = "cv" + ext
        permanent_path = os.path.abspath(os.path.join(app.config['UPLOAD_FOLDER'], safe_name))
        cv_file.save(permanent_path)

        # Parsear el CV desde la copia permanente
        from bot.cv_parser import parse_cv
        fields = parse_cv(permanent_path)

        # Persistir la ruta en .env para que el bot siempre la encuentre
        env_path = os.path.abspath('.env')
        if not os.path.exists(env_path):
            open(env_path, 'w', encoding='utf-8').close()
        update_env_values(env_path, {'USER_CV_PATH': permanent_path})

        return jsonify({
            "status": "success",
            "fields": fields,
            "filename": cv_file.filename,
            "cv_path": os.path.basename(permanent_path),  # SECURITY: solo basename, no ruta absoluta
        })
    except Exception as e:
        _log.error("parse_cv error: %s", e)
        return jsonify({"status": "error", "message": "Error al procesar el CV. Intenta de nuevo."})


@bp.route('/save_config', methods=['POST'])
def save_config():
    try:
        _config_rate_limiter.check()
    except RuntimeError as e:
        return jsonify({'ok': False, 'error': str(e)}), 429
    try:
        data = request.form.to_dict()

        env_path = os.path.abspath('.env')
        if not os.path.exists(env_path):
            with open(env_path, 'w', encoding='utf-8') as f:
                f.write("")

        updates = {}

        if 'MAX_OFFERS' in data:
            updates['USER_MAX_OFFERS'] = clean_form_value(data.pop('MAX_OFFERS'))

        for key, val in data.items():
            if key in _PERSISTED_ENV_KEYS and val.strip():
                updates[key] = clean_form_value(val)

        # Guardar sin borrar ningún campo — todos se persisten en .env
        update_env_values(env_path, updates)
        load_dotenv(override=True)
        os.environ.update({k: v for k, v in updates.items() if v})

        return jsonify({"status": "success", "message": "Configuracion guardada correctamente."})
    except Exception as e:
        _log.error("save_config error: %s", e)
        return jsonify({"status": "error", "message": "Error al guardar configuración."})

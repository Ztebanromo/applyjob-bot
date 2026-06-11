"""api/config_utils.py — Manejo de .env y constantes de configuración del dashboard."""
from __future__ import annotations

import os
import re
import threading

_env_write_lock = threading.Lock()  # protege escrituras concurrentes a .env

# Indeed excluido: bloqueado por Cloudflare Turnstile
_PERSISTED_ENV_KEYS = {
    'USER_KEYWORDS',
    'USER_MAX_OFFERS',
    'USER_CV_PATH',
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
    'USER_SALARY',
    'USER_YEARS_EXP',
    'USER_AVAILABILITY',
    'USER_ENGLISH_LEVEL',
    'USER_WORK_MODE',
    'USER_LOCATION_RANGE',
    'USER_ACCEPTED_MODES',
    'USER_COVER_LETTER',
    'LABORUM_EMAIL',
    'LABORUM_PASSWORD',
}
# SECURITY: keys que NUNCA se devuelven al browser vía /api/config
_SECRET_ENV_KEYS = {'LABORUM_PASSWORD', 'SECRET_KEY', 'SMTP_PASS'}
# Keys públicas = persistidas - secretas
_PUBLIC_ENV_KEYS = _PERSISTED_ENV_KEYS - _SECRET_ENV_KEYS
# Campos que se pasan al proceso del bot como env vars en tiempo de ejecución
_SENSITIVE_ENV_KEYS = _PERSISTED_ENV_KEYS


def update_env_values(env_path: str, updates: dict, remove_keys=None) -> None:
    """Actualiza .env sin reemplazar el archivo por un temporal.

    Thread-safe: usa _env_write_lock para evitar corrupción por escrituras concurrentes.
    En Windows + OneDrive, el rename atómico que usa python-dotenv puede fallar
    con PermissionError aunque el archivo sea escribible.
    """
    with _env_write_lock:
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
    # Solo quitar comillas al inicio/final — nunca tocar barras invertidas (rutas Windows)
    value = value.strip("'\"")
    return re.sub(r'\s+', ' ', value).strip()


def _make_child_env(extra=None):
    from dotenv import load_dotenv
    load_dotenv(override=True)
    e = os.environ.copy()
    e.update({'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8', 'PYTHONLEGACYWINDOWSSTDIO': '0'})
    if extra:
        e.update({k: v for k, v in extra.items() if v})
    return e

"""api/qa_utils.py — Cache de preguntas/respuestas (QA) para auto-fill de formularios ATS."""
from __future__ import annotations

import json
import os
import re
import unicodedata as _ud

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.path.join(_BASE_DIR, 'data')

_PENDING_Q_PATH   = os.path.join(_DATA_DIR, 'pending_questions.json')
_QA_PATH          = os.path.join(_DATA_DIR, 'question_answers.json')
_QA_CACHE_PATH    = os.path.join(_DATA_DIR, 'qa_cache.json')
_QUICK_LINKS_PATH = os.path.join(_DATA_DIR, 'quick_links.json')
_PROFILE_KB_PATH  = os.path.join(_DATA_DIR, 'profile_kb.json')
_ENV_PATH         = os.path.join(_BASE_DIR, '.env')
_SCAN_QUEUE_PATH  = os.path.join(_DATA_DIR, 'scan_queue.json')


def _normalize_srv(text: str) -> str:
    """Normaliza texto para comparaciones: minúsculas, sin tildes, sin espacios extra."""
    nfkd = _ud.normalize("NFKD", text.lower())
    ascii_t = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_t).strip()


def _load_merged_qa() -> dict:
    """Carga y fusiona question_answers.json + qa_cache.json (normalizado)."""
    result = {}
    for path in (_QA_PATH, _QA_CACHE_PATH):
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding='utf-8') as f:
                for k, v in json.load(f).items():
                    if not k or k.startswith('_') or k.startswith('─') or not v:
                        continue
                    nk = _normalize_srv(k)
                    if nk:
                        result[nk] = v
        except Exception:
            pass
    return result


def _load_profile_kb_qa() -> dict:
    """Carga los qa_overrides de profile_kb.json (todas las categorías)."""
    result = {}
    if not os.path.exists(_PROFILE_KB_PATH):
        return result
    try:
        with open(_PROFILE_KB_PATH, encoding='utf-8') as f:
            kb = json.load(f)
        for category in kb.values():
            for q, a in (category.get('qa_overrides') or {}).items():
                nk = _normalize_srv(q)
                if nk and a:
                    result[nk] = a
    except Exception:
        pass
    return result


def _load_profile_env_qa() -> dict:
    """
    Genera respuestas básicas desde las variables USER_* del .env.
    Cubre preguntas sobre nombre, email, teléfono, ciudad, salario, etc.
    """
    result: dict = {}
    cfg: dict = {}

    # Intentar leer .env
    if os.path.exists(_ENV_PATH):
        try:
            with open(_ENV_PATH, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if '=' in line and not line.startswith('#'):
                        k, _, v = line.partition('=')
                        cfg[k.strip()] = v.strip().strip('"').strip("'")
        except Exception:
            pass

    # También leer desde config actual de la app
    try:
        from bot.config import USER_PROFILE
        cfg.update({k: str(v) for k, v in USER_PROFILE.items() if v})
    except Exception:
        pass

    # Mapeo: palabras clave de pregunta → valor del perfil
    name     = cfg.get('USER_FULL_NAME') or f"{cfg.get('USER_FIRST_NAME','')} {cfg.get('USER_LAST_NAME','')}".strip()
    email    = cfg.get('USER_EMAIL', '')
    phone    = cfg.get('USER_PHONE', cfg.get('USER_PHONE_NUMBER', ''))
    city     = cfg.get('USER_CITY', 'Maipú, Santiago')
    salary   = cfg.get('USER_SALARY', '850000')
    avail    = cfg.get('USER_AVAILABILITY', 'Inmediata')
    english  = cfg.get('USER_ENGLISH_LEVEL', 'Básico')
    wmode    = cfg.get('USER_WORK_MODE', 'Presencial')
    yexp     = cfg.get('USER_YEARS_EXP', '0')

    mappings = []
    if name:
        mappings += [
            ("nombre completo", name), ("your name", name),
            ("nombre y apellido", name), ("tu nombre", name),
        ]
    if email:
        mappings += [
            ("correo electronico", email), ("email", email),
            ("tu correo", email), ("email address", email),
        ]
    if phone:
        mappings += [
            ("telefono", phone), ("numero de telefono", phone),
            ("celular", phone), ("phone number", phone),
            ("numero celular", phone),
        ]
    if city:
        mappings += [
            ("ciudad de residencia", city), ("donde vives", city),
            ("ciudad", city), ("ubicacion", city),
        ]
    if salary:
        mappings += [
            ("pretension salarial", salary), ("pretension de renta", salary),
            ("renta esperada", salary), ("expectativa salarial", salary),
            ("cuanto quieres ganar", salary),
        ]
    if avail:
        mappings += [
            ("disponibilidad", avail), ("cuando puedes empezar", avail),
            ("fecha de incorporacion", avail), ("disponibilidad de incorporacion", avail),
        ]
    if english:
        mappings += [
            ("nivel de ingles", english), ("english level", english),
            ("hablas ingles", english),
        ]
    if wmode:
        mappings += [
            ("modalidad de trabajo", wmode), ("modalidad preferida", wmode),
            ("trabajo presencial o remoto", wmode),
        ]
    if yexp:
        mappings += [
            ("anos de experiencia", yexp), ("years of experience", yexp),
            ("cuantos anos de experiencia tienes", yexp),
        ]

    for q, a in mappings:
        nk = _normalize_srv(q)
        if nk:
            result[nk] = a

    return result


def _suggest_from_cache(norm: str) -> str:
    """
    Busca la mejor respuesta para una pregunta normalizada.
    Jerarquía:
      1. question_answers.json + qa_cache.json (exacto)
      2. profile_kb.json qa_overrides
      3. Variables USER_* del .env / config
      4. Substring match en todos los anteriores
      5. Word-overlap ≥ 60% en todos los anteriores
    """
    # Construir mapa unificado: QA principal > profile_kb > env
    qa = _load_merged_qa()
    kb_qa = _load_profile_kb_qa()
    env_qa = _load_profile_env_qa()

    # Fusionar (QA principal tiene prioridad)
    combined: dict = {}
    combined.update(env_qa)
    combined.update(kb_qa)
    combined.update(qa)  # máxima prioridad

    # 1. Match exacto
    if norm in combined:
        return combined[norm]

    # 2. Substring
    for k, v in combined.items():
        if len(k) >= 15 and k in norm:
            return v
        if len(norm) >= 15 and norm in k:
            return v

    # 3. Word-overlap ≥ 60%
    _STOP = {"de","la","el","en","y","a","con","su","tu","un","una","es","se",
             "si","no","para","que","por","al","del","lo","las","los","has",
             "have","your","you","the","and","or","is","are","do","did"}
    words_n = set(norm.split()) - _STOP
    best_v, best_s = None, 0.0
    for k, v in combined.items():
        words_k = set(k.split()) - _STOP
        if not words_n or not words_k:
            continue
        shared = words_n & words_k
        score = len(shared) / max(len(words_n), len(words_k))
        if score > best_s:
            best_s, best_v = score, v
    return best_v if best_s >= 0.55 else ""

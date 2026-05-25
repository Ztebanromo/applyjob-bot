"""
bot/qa_cache.py — Caché persistente de respuestas a preguntas de portales.

Portales hacen siempre las mismas preguntas de screening. Este módulo guarda
las respuestas la primera vez y las devuelve automáticamente en runs siguientes.

Datos en: data/qa_cache.json
Clave:    sha256(normalize(question))[:12]
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

log = logging.getLogger("applyjob.qa_cache")

_CACHE_PATH = Path(__file__).parent.parent / "data" / "qa_cache.json"


def _normalize_question(q: str) -> str:
    q = (q or "").lower().strip()
    for a, b in [("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),("ñ","n")]:
        q = q.replace(a, b)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", q)).strip()


def _qkey(question: str) -> str:
    return hashlib.sha256(_normalize_question(question).encode()).hexdigest()[:12]


def _load() -> dict:
    if _CACHE_PATH.exists():
        try:
            with open(_CACHE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save(data: dict) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_answer(question: str) -> str | None:
    """
    Retorna la respuesta guardada para esta pregunta, o None si no existe.
    La comparación es case-insensitive y accent-insensitive.
    """
    key   = _qkey(question)
    data  = _load()
    entry = data.get(key)
    if entry:
        log.debug("[QA_CACHE] Hit: '%s...' → '%s'",
                  question[:40], str(entry.get("answer", ""))[:30])
        return entry.get("answer")
    return None


def save_answer(question: str, answer: str) -> None:
    """
    Guarda la respuesta para esta pregunta.
    Si ya existe una respuesta, la sobreescribe.
    """
    key  = _qkey(question)
    data = _load()
    data[key] = {
        "question_preview": question[:80],
        "answer":           answer,
    }
    _save(data)
    log.info("[QA_CACHE] Guardado: '%s...' → '%s'", question[:40], answer[:30])


def all_answers() -> dict:
    """Retorna todas las preguntas y respuestas guardadas (para UI/debug)."""
    return _load()

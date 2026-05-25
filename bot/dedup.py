"""
bot/dedup.py — Deduplicación cross-portal de ofertas.

Un fingerprint es sha256(normalize(title) + "|" + normalize(company))[:16].
Se persiste en data/cross_dedup.json entre sesiones.

Uso típico en engine.py:
    from bot.dedup import is_duplicate, mark_seen
    if is_duplicate(title, company):
        continue
    mark_seen(title, company, portal_name)
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger("applyjob.dedup")

_DEDUP_PATH = Path(__file__).parent.parent / "data" / "cross_dedup.json"


def _normalize(text: str) -> str:
    """Minúsculas, sin tildes, sin caracteres especiales, colapsa espacios."""
    t = (text or "").lower()
    for a, b in [("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),("ñ","n")]:
        t = t.replace(a, b)
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _fingerprint(title: str, company: str = "") -> str:
    key = _normalize(title) + "|" + _normalize(company)
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _load() -> dict:
    if _DEDUP_PATH.exists():
        try:
            with open(_DEDUP_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save(data: dict) -> None:
    _DEDUP_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_DEDUP_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def is_duplicate(title: str, company: str = "") -> bool:
    """
    Retorna True si esta oferta (por título+empresa) ya fue vista
    en cualquier portal en los últimos 60 días.
    """
    if not title:
        return False
    fp   = _fingerprint(title, company)
    data = _load()
    return fp in data


def mark_seen(title: str, company: str = "", portal: str = "") -> None:
    """
    Registra la oferta como vista. Llamar ANTES de intentar aplicar.
    """
    if not title:
        return
    fp   = _fingerprint(title, company)
    data = _load()
    data[fp] = {"date": str(date.today()), "portal": portal}
    _save(data)
    log.debug("[DEDUP] Marcado: '%s' @ %s (fp=%s)", title[:40], portal, fp)


def purge_old(days: int = 60) -> int:
    """Elimina fingerprints más viejos que `days` días. Retorna cantidad eliminada."""
    data   = _load()
    cutoff = str(date.today() - timedelta(days=days))
    old    = [k for k, v in data.items() if v.get("date", "") < cutoff]
    for k in old:
        del data[k]
    if old:
        _save(data)
    return len(old)

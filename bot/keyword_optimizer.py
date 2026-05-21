"""
keyword_optimizer.py — Sistema adaptativo de keywords.

Aprende qué keywords generan postulaciones y cuáles no, POR PORTAL.
Una keyword puede funcionar en ChileTrabajos y fallar en Laborum —
se retira solo del portal donde falla, nunca globalmente.

Datos persistidos en data/keyword_stats.json (acumula entre sesiones).

Estructura de keyword_stats.json:
{
  "desarrollador junior": {
    "source": "base",           // "base" | "generated_from:<keyword>"
    "added":  "2026-05-20",
    "portals": {
      "laborum": {
        "applied": 3, "runs": 5,
        "status":  "active"     // "active" | "retired"
        "retired_at": null
      },
      "chiletrabajos": { ... }
    }
  }
}
"""
from __future__ import annotations

import json
import logging
import random
from datetime import date
from pathlib import Path

log = logging.getLogger("applyjob.keyword_optimizer")

_STATS_PATH = Path(__file__).parent.parent / "data" / "keyword_stats.json"

# ── Umbrales ────────────────────────────────────────────────────────────────
MIN_RUNS_TO_RETIRE  = 3   # corridas con 0 resultados en un mismo portal para retirar
REPLACEMENTS_PER_KW = 2   # keywords nuevas a generar al retirar una de un portal

# ── Vocabulario para generación ──────────────────────────────────────────────
_IT_BASES = [
    "desarrollador", "programador", "developer", "ingeniero de software",
    "analista programador", "analista de sistemas", "analista ti",
    "soporte tecnico", "soporte ti", "help desk", "mesa de ayuda",
    "qa", "tester", "tecnico informatica", "tecnico en informatica",
    "desarrollador web", "desarrollador backend", "desarrollador frontend",
    "desarrollador fullstack", "desarrollador react", "desarrollador node",
    "python developer", "javascript developer", "sql developer",
    "analista de datos", "analista bi", "data analyst",
    "egresado informatica", "egresado ti", "egresado sistemas",
    "recien egresado informatica", "recien egresado ti",
    "practicante informatica", "practicante ti", "practicante desarrollo",
    "ingeniero informatico", "ingeniero en informatica",
    "tecnico en telecomunicaciones", "redes y telecomunicaciones",
    "administrador de sistemas", "administrador ti",
    "desarrollador junior python", "desarrollador junior javascript",
    "junior developer", "junior programmer",
]

_IT_MODS = [
    "junior", "trainee", "sin experiencia", "egresado",
    "recien egresado", "practicante", "primer empleo",
    "entry level", "recien titulado",
]

_BODEGA_BASES = [
    "operario bodega", "auxiliar bodega", "auxiliar logistica",
    "bodeguero", "operario logistica", "picker", "packer",
    "recepcionista bodega", "despachador", "operador logistico",
    "ayudante bodega", "asistente bodega",
]

# ── I/O ──────────────────────────────────────────────────────────────────────

def _load_stats() -> dict:
    if _STATS_PATH.exists():
        try:
            with open(_STATS_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_stats(stats: dict) -> None:
    _STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_STATS_PATH, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


def _portal_entry(stats: dict, key: str, portal: str) -> dict:
    """Inicializa y retorna la entrada por portal de una keyword."""
    if key not in stats:
        stats[key] = {
            "source": "base",
            "added":  str(date.today()),
            "portals": {},
        }
    if portal not in stats[key]["portals"]:
        stats[key]["portals"][portal] = {
            "applied":    0,
            "runs":       0,
            "status":     "active",
            "retired_at": None,
        }
    return stats[key]["portals"][portal]


# ── Estadísticas ─────────────────────────────────────────────────────────────

def update_keyword_stat(keyword: str, portal: str, applied: int) -> None:
    """
    Registra el resultado de una keyword en un portal específico.
    applied = postulaciones logradas en esta corrida.
    """
    stats = _load_stats()
    key   = keyword.lower().strip()
    pe    = _portal_entry(stats, key, portal)

    pe["applied"] += applied
    pe["runs"]    += 1

    _save_stats(stats)
    log.debug("[KW_STATS] '%s' @ %s: +%d postulaciones (total=%d, runs=%d)",
              key, portal, applied, pe["applied"], pe["runs"])


def should_retire(keyword: str, portal: str) -> bool:
    """
    True si la keyword debe retirarse DEL PORTAL ESPECÍFICO.
    Condición: MIN_RUNS_TO_RETIRE corridas consecutivas con 0 postulaciones en ese portal.
    """
    stats = _load_stats()
    key   = keyword.lower().strip()
    pe    = stats.get(key, {}).get("portals", {}).get(portal)
    if not pe or pe.get("status") != "active":
        return False
    return pe["runs"] >= MIN_RUNS_TO_RETIRE and pe["applied"] == 0


def retire_keyword_from_portal(keyword: str, portal: str) -> None:
    """Marca la keyword como retirada SOLO en ese portal."""
    stats = _load_stats()
    key   = keyword.lower().strip()
    pe    = _portal_entry(stats, key, portal)
    pe["status"]     = "retired"
    pe["retired_at"] = str(date.today())
    _save_stats(stats)

    runs = pe["runs"]
    log.info("[KW_RETIRE] '%s' retirada de %s (0 postulaciones en %d corridas).",
             key, portal, runs)
    print(f"  [KW_RETIRADA] '{keyword}' en {portal.upper()} "
          f"-- 0 postulaciones en {runs} corridas.")


# ── Generación de reemplazos ─────────────────────────────────────────────────

def _all_known_keywords(stats: dict) -> set[str]:
    """Conjunto de todas las keywords conocidas (independientemente del portal)."""
    return set(stats.keys())


def _is_bodega_keyword(keyword: str) -> bool:
    kl = keyword.lower()
    return any(b in kl for b in ("bodega", "logistic", "picker", "packer",
                                  "despachador", "bodeguero"))


def generate_replacements(retired_keyword: str,
                          n: int = REPLACEMENTS_PER_KW) -> list[dict]:
    """
    Genera hasta n keywords nuevas (nunca registradas antes) como reemplazo.
    Las registra en keyword_stats.json con source='generated_from:<retired>'.
    Devuelve lista de dicts con formato KEYWORD_GROUPS.
    """
    stats     = _load_stats()
    known     = _all_known_keywords(stats)
    is_bodega = _is_bodega_keyword(retired_keyword)

    bases = _BODEGA_BASES if is_bodega else _IT_BASES
    mods  = ["sin experiencia"] if is_bodega else _IT_MODS

    generated: list[dict] = []
    shuffled   = bases[:]
    random.shuffle(shuffled)

    for base in shuffled:
        if len(generated) >= n:
            break
        for mod in random.sample(mods, k=min(len(mods), 4)):
            candidate     = f"{base} sin experiencia" if is_bodega else f"{base} {mod}"
            candidate_key = candidate.lower().strip()
            if candidate_key in known:
                continue

            # Registrar como nueva (sin corridas aún)
            stats[candidate_key] = {
                "source":  f"generated_from:{retired_keyword}",
                "added":   str(date.today()),
                "portals": {},
            }
            known.add(candidate_key)

            label = "Bodega" if is_bodega else _infer_label(base)
            generated.append({
                "label":   label,
                "keyword": candidate,
                "mode":    "bodega" if is_bodega else "it",
                "scan":    not is_bodega,
            })
            log.info("[KW_NEW] '%s' generada como reemplazo de '%s'",
                     candidate, retired_keyword)
            print(f"  [KW_NUEVA] '{candidate}' (reemplaza '{retired_keyword}')")

            if len(generated) >= n:
                break

    _save_stats(stats)
    return generated


def _infer_label(base: str) -> str:
    b = base.lower()
    if any(x in b for x in ("analista", "bi ", "datos", "data")):
        return "Analista"
    if any(x in b for x in ("soporte", "help desk", "mesa de ayuda", "tecnico")):
        return "Soporte"
    if any(x in b for x in ("qa", "tester", "quality")):
        return "QA"
    if any(x in b for x in ("egresado", "recien", "practicante", "titulado")):
        return "Egresado"
    if any(x in b for x in ("python", "javascript", "sql", "react", "node",
                              "web", "backend", "frontend", "fullstack")):
        return "Stack"
    return "Desarrollo"


# ── API principal ─────────────────────────────────────────────────────────────

def get_active_groups(base_groups: list[dict], portal: str) -> list[dict]:
    """
    Devuelve las keywords activas PARA ESE PORTAL:
      - Quita las que están retiradas en ese portal específico
      - Agrega keywords generadas automáticamente que aún no corrieron en ese portal
    """
    stats = _load_stats()

    # Keywords retiradas solo en ESTE portal
    retired_in_portal = {
        k for k, v in stats.items()
        if v.get("portals", {}).get(portal, {}).get("status") == "retired"
    }

    # Filtrar base_groups
    active = [g for g in base_groups
              if g["keyword"].lower().strip() not in retired_in_portal]

    # Agregar keywords generadas que nunca corrieron en este portal
    active_keys = {g["keyword"].lower().strip() for g in active}
    for kw_key, entry in stats.items():
        if entry.get("source", "base") == "base":
            continue  # ya está en base_groups
        if not entry.get("source", "").startswith("generated_from:"):
            continue
        if kw_key in retired_in_portal:
            continue
        if kw_key in active_keys:
            continue
        # Keyword generada que aún no está en la lista activa → agregar
        is_bodega = _is_bodega_keyword(kw_key)
        active.append({
            "label":   "Bodega" if is_bodega else _infer_label(kw_key),
            "keyword": kw_key,
            "mode":    "bodega" if is_bodega else "it",
            "scan":    not is_bodega,
        })
        active_keys.add(kw_key)

    retired_count   = len(base_groups) - sum(
        1 for g in base_groups if g["keyword"].lower().strip() not in retired_in_portal
    )
    generated_count = len(active) - len(base_groups) + retired_count
    if retired_count or generated_count:
        log.info("[KW_OPTIMIZER] Portal=%s | activas=%d | retiradas=%d | generadas=%d",
                 portal, len(active), retired_count, generated_count)
        print(f"[KEYWORDS] {portal.upper()}: {len(active)} activas "
              f"({retired_count} retiradas en este portal, {generated_count} generadas)")

    return active


def process_keyword_result(keyword: str, portal: str,
                            applied: int) -> list[dict]:
    """
    Registra el resultado de una keyword en un portal.
    Si corresponde retirarla de ese portal, genera reemplazos.
    Devuelve lista de nuevas keywords generadas (vacía si no hubo retiro).
    """
    update_keyword_stat(keyword, portal, applied)

    if should_retire(keyword, portal):
        retire_keyword_from_portal(keyword, portal)
        return generate_replacements(keyword)

    return []


# ── Resumen estadístico (para dashboard / logs) ───────────────────────────────

def get_stats_summary(portal: Optional[str] = None) -> dict:  # type: ignore[name-defined]
    """
    Resumen de estadísticas.
    Si portal es None → estadísticas globales.
    Retorna dict con listas: active, retired, generated, top_performers.
    """
    from typing import Optional as _Opt  # noqa: F401
    stats  = _load_stats()
    active, retired, generated, top = [], [], [], []

    for kw, entry in stats.items():
        portals = entry.get("portals", {})
        if portal:
            pe = portals.get(portal)
            if not pe:
                continue
            status  = pe.get("status", "active")
            applied = pe.get("applied", 0)
            runs    = pe.get("runs", 0)
        else:
            all_statuses = [p.get("status", "active") for p in portals.values()]
            status  = "retired" if all(s == "retired" for s in all_statuses) and all_statuses else "active"
            applied = sum(p.get("applied", 0) for p in portals.values())
            runs    = sum(p.get("runs", 0)    for p in portals.values())

        record = {"keyword": kw, "applied": applied, "runs": runs,
                  "rate": round(applied / runs, 2) if runs else 0,
                  "source": entry.get("source", "base")}

        if status == "retired":
            retired.append(record)
        else:
            active.append(record)
            if applied > 0:
                top.append(record)
        if entry.get("source", "").startswith("generated_from:"):
            generated.append(record)

    top.sort(key=lambda x: x["applied"], reverse=True)
    return {
        "active":        active,
        "retired":       retired,
        "generated":     generated,
        "top_performers": top[:10],
    }

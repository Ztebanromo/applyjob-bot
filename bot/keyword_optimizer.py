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
MAX_ACTIVE_KEYWORDS = 20  # máximo de keywords activas por portal en cada run
MIN_RUNS_TO_RETIRE  = 1   # 1 run con found=0 → retirar del portal (sin piedad)
REPLACEMENTS_PER_KW = 2   # keywords nuevas a generar al retirar una

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

# Solo junior y sin experiencia — lo que realmente convierte
_IT_MODS = ["junior", "sin experiencia"]

# Bodega: solo sin experiencia (junior no aplica al rubro)
_BODEGA_BASES = [
    "operario bodega", "auxiliar bodega", "bodeguero",
    "operario logistica", "auxiliar logistica",
]
_BODEGA_MODS = ["sin experiencia"]

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
            "found":      0,   # ofertas crudas encontradas en la página (antes de filtros)
            "runs":       0,
            "status":     "active",
            "retired_at": None,
        }
    return stats[key]["portals"][portal]


# ── Estadísticas ─────────────────────────────────────────────────────────────

def update_keyword_stat(keyword: str, portal: str, applied: int, found: int = 0) -> None:
    """
    Registra el resultado de una keyword en un portal.
    found   = ofertas brutas encontradas en la página (antes de filtros).
    applied = postulaciones efectivas en esta corrida.
    """
    stats = _load_stats()
    key   = keyword.lower().strip()
    pe    = _portal_entry(stats, key, portal)

    pe["applied"] += applied
    pe["found"]    = pe.get("found", 0) + found
    pe["runs"]    += 1

    _save_stats(stats)
    log.debug("[KW_STATS] '%s' @ %s: found=%d applied=%d (total_found=%d runs=%d)",
              key, portal, found, applied, pe["found"], pe["runs"])


def should_retire(keyword: str, portal: str) -> bool:
    """
    True si la keyword debe retirarse del portal.
    Condición: ≥ MIN_RUNS_TO_RETIRE runs con found=0 (ninguna oferta cruda en el portal).
    Si found nunca fue registrado, cae a applied=0 como fallback.
    """
    stats = _load_stats()
    key   = keyword.lower().strip()
    pe    = stats.get(key, {}).get("portals", {}).get(portal)
    if not pe or pe.get("status") != "active":
        return False
    runs  = pe.get("runs", 0)
    found = pe.get("found", None)
    # Usar found si está disponible; si no, usar applied como proxy
    signal = found if found is not None else pe.get("applied", 0)
    return runs >= MIN_RUNS_TO_RETIRE and signal == 0


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
    Genera hasta n keywords nuevas como reemplazo de una retirada.
    Bodega retirada → genera nuevo bodega sin experiencia.
    IT retirada     → genera nuevo IT junior/sin experiencia.
    """
    stats = _load_stats()
    known = _all_known_keywords(stats)

    # Si era bodega, generar reemplazo bodega (no IT)
    if _is_bodega_keyword(retired_keyword):
        import random as _rnd
        bases = _BODEGA_BASES[:]
        _rnd.shuffle(bases)
        generated: list[dict] = []
        for base in bases:
            if len(generated) >= n:
                break
            for mod in _BODEGA_MODS:
                candidate = f"{base} {mod}"
                ckey = candidate.lower().strip()
                if ckey in known:
                    continue
                stats[ckey] = {"source": f"generated_from:{retired_keyword}",
                               "added": str(date.today()), "portals": {}}
                known.add(ckey)
                generated.append({"label": "Bodega", "keyword": candidate,
                                   "mode": "bodega", "scan": True})
                log.info("[KW_NEW] bodega '%s' generada (reemplaza '%s')", candidate, retired_keyword)
                if len(generated) >= n:
                    break
        _save_stats(stats)
        return generated

    bases     = _IT_BASES[:]
    random.shuffle(bases)
    generated: list[dict] = []

    for base in bases:
        if len(generated) >= n:
            break
        for mod in _IT_MODS:          # solo "junior" y "sin experiencia"
            candidate     = f"{base} {mod}"
            candidate_key = candidate.lower().strip()
            if candidate_key in known:
                continue

            stats[candidate_key] = {
                "source":  f"generated_from:{retired_keyword}",
                "added":   str(date.today()),
                "portals": {},
            }
            known.add(candidate_key)

            generated.append({
                "label":   _infer_label(base),
                "keyword": candidate,
                "mode":    "it",
                "scan":    True,
            })
            log.info("[KW_NEW] '%s' generada (reemplaza '%s')", candidate, retired_keyword)
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


# ── Scoring ──────────────────────────────────────────────────────────────────

def get_keyword_score(keyword: str, portal: str) -> float:
    """
    Score de rendimiento para ordenar keywords (mayor = mejor).

    0.5  → keyword nueva / sin historial → prioridad media
    >0.5 → encontró ofertas o aplicó con éxito → primero
    <0.5 → encontró pocas/nulas ofertas → al final (pero no retirada aún)
    """
    stats = _load_stats()
    key   = keyword.lower().strip()
    pe    = stats.get(key, {}).get("portals", {}).get(portal)
    if not pe:
        return 0.5  # nueva → prioridad media

    runs    = pe.get("runs", 0)
    found   = pe.get("found", None)
    applied = pe.get("applied", 0)

    if runs == 0:
        return 0.5

    # Si nunca se registró found, estimar desde applied
    if found is None:
        found = applied

    find_rate  = found / runs                                    # cuántas ofertas por run
    apply_rate = (applied / found) if found > 0 else 0.0        # conversión a postulación

    # Score compuesto: 70% cobertura de ofertas + 30% conversión
    raw = find_rate * 0.7 + apply_rate * 0.3
    # Normalizar: clamp entre 0 y 1 (find_rate puede superar 1 si hay muchas ofertas)
    return min(1.0, raw / max(1.0, find_rate)) if find_rate > 0 else 0.01


# ── API principal ─────────────────────────────────────────────────────────────

def get_active_groups(base_groups: list[dict], portal: str) -> list[dict]:
    """
    Devuelve las keywords activas para este portal, ordenadas por rendimiento
    y limitadas a MAX_ACTIVE_KEYWORDS.

    Orden:
      1. Keywords con historial positivo (found > 0) → score alto → primero
      2. Keywords nuevas sin historial                → score 0.5  → medio
      3. Keywords con found=0 pero no retiradas aún   → score bajo → último

    Las retiradas (found=0 tras MIN_RUNS_TO_RETIRE runs) se excluyen.
    """
    stats = _load_stats()

    retired_in_portal = {
        k for k, v in stats.items()
        if v.get("portals", {}).get(portal, {}).get("status") == "retired"
    }

    active = [g for g in base_groups
              if g["keyword"].lower().strip() not in retired_in_portal]

    # Agregar keywords generadas / extraídas del scan que no estén ya en la lista
    active_keys = {g["keyword"].lower().strip() for g in active}
    for kw_key, entry in stats.items():
        if entry.get("source", "base") == "base":
            continue
        if kw_key in retired_in_portal or kw_key in active_keys:
            continue
        mode  = "bodega" if _is_bodega_keyword(kw_key) else "it"
        label = "Bodega"  if mode == "bodega" else _infer_label(kw_key)
        active.append({
            "label":   label,
            "keyword": kw_key,
            "mode":    mode,
            "scan":    True,
        })
        active_keys.add(kw_key)

    # Ordenar por score descendente: ganadores primero, cero-resultados al final
    active.sort(key=lambda g: get_keyword_score(g["keyword"], portal), reverse=True)

    retired_count = len(base_groups) - sum(
        1 for g in base_groups if g["keyword"].lower().strip() not in retired_in_portal
    )

    # Limitar a MAX_ACTIVE_KEYWORDS — conservar solo los mejores
    if len(active) > MAX_ACTIVE_KEYWORDS:
        log.info("[KW_OPTIMIZER] Recortando %d → %d keywords (portal=%s)",
                 len(active), MAX_ACTIVE_KEYWORDS, portal)
        print(f"[KEYWORDS] {portal.upper()}: {len(active)} disponibles → "
              f"usando top {MAX_ACTIVE_KEYWORDS} por rendimiento "
              f"({retired_count} retiradas)")
        active = active[:MAX_ACTIVE_KEYWORDS]
    elif retired_count:
        print(f"[KEYWORDS] {portal.upper()}: {len(active)} activas "
              f"({retired_count} retiradas, ordenadas por rendimiento)")

    return active


def process_keyword_result(keyword: str, portal: str,
                            applied: int, found: int = 0) -> list[dict]:
    """
    Registra el resultado de una keyword en un portal.
    found   = ofertas crudas vistas en la página (antes de filtros).
    applied = postulaciones efectivas.
    Si found=0 tras MIN_RUNS_TO_RETIRE runs → retira y genera reemplazos.
    """
    update_keyword_stat(keyword, portal, applied, found)

    if should_retire(keyword, portal):
        retire_keyword_from_portal(keyword, portal)
        return generate_replacements(keyword)

    return []


# ── Extracción dinámica de keywords desde títulos vistos ─────────────────────
#
# Cuando el bot recorre las páginas de ofertas extrae los títulos de los cards.
# Esta función analiza esos títulos y genera nuevas keywords que aún no están en
# la cola, incorporándolas al run actual para mejor cobertura.
#
# Ejemplo:  "Desarrollador React Native Junior (Chile, Remoto)"
#           → detecta "react native" → genera "react native developer junior"

# Mapa: fragmento encontrado en el título → término de búsqueda base
_TECH_PATTERN_MAP: list[tuple[str, str]] = [
    # Frontend específico
    ("react native",      "react native developer"),
    ("react",             "react developer"),
    ("vue.js",            "vue developer"),
    ("vue ",              "vue developer"),
    ("angular",           "angular developer"),
    ("next.js",           "nextjs developer"),
    ("nextjs",            "nextjs developer"),
    ("svelte",            "svelte developer"),
    ("flutter",           "flutter developer"),
    # Backend específico
    ("django",            "django developer"),
    ("flask",             "flask developer"),
    ("fastapi",           "fastapi developer"),
    ("spring boot",       "spring developer"),
    ("spring",            "spring developer"),
    ("laravel",           "laravel developer"),
    ("nestjs",            "nestjs developer"),
    ("express",           "node developer"),
    ("ruby on rails",     "rails developer"),
    ("rails",             "rails developer"),
    # Mobile
    ("android",           "android developer"),
    ("ios developer",     "ios developer"),
    ("kotlin",            "kotlin developer"),
    ("swift",             "swift developer"),
    ("xamarin",           "mobile developer"),
    # Data / BI / ML
    ("data engineer",     "data engineer"),
    ("data scientist",    "data scientist"),
    ("machine learning",  "machine learning engineer"),
    ("deep learning",     "machine learning engineer"),
    ("inteligencia artificial", "machine learning engineer"),
    ("power bi",          "power bi analyst"),
    ("tableau",           "tableau analyst"),
    ("looker",            "data analyst"),
    ("databricks",        "data engineer"),
    ("spark",             "data engineer"),
    # Cloud / DevOps
    ("devops",            "devops engineer"),
    ("sre",               "devops engineer"),
    ("cloud engineer",    "cloud engineer"),
    ("aws",               "aws developer"),
    ("azure",             "azure developer"),
    ("gcp",               "cloud engineer"),
    ("kubernetes",        "devops engineer"),
    ("terraform",         "devops engineer"),
    # QA / Testing
    ("qa automation",     "qa automation"),
    ("automatizacion",    "qa automation"),
    ("automatización",    "qa automation"),
    ("selenium",          "qa automation"),
    ("cypress",           "qa automation"),
    ("playwright",        "qa automation"),
    ("quality assurance", "qa engineer"),
    # Seguridad
    ("ciberseguridad",    "cybersecurity analyst"),
    ("cybersecurity",     "cybersecurity analyst"),
    ("seguridad informatica", "cybersecurity analyst"),
    # ERP / SAP
    ("sap",               "sap analyst"),
    ("oracle apex",       "oracle developer"),
    ("salesforce",        "salesforce developer"),
    ("odoo",              "odoo developer"),
    # Infraestructura / Redes
    ("administrador de redes", "network administrator"),
    ("soporte de redes",  "network support"),
    ("linux",             "linux administrator"),
    # Fullstack / Stack específico
    ("fullstack",         "fullstack developer"),
    ("full stack",        "fullstack developer"),
    ("mern",              "fullstack developer"),
    ("mean",              "fullstack developer"),
    ("python",            "python developer"),
    ("javascript",        "javascript developer"),
    ("typescript",        "typescript developer"),
    ("java ",             "java developer"),
    ("c#",                "dotnet developer"),
    (".net",              "dotnet developer"),
    ("golang",            "golang developer"),
    ("php",               "php developer"),
    ("sql server",        "database developer"),
    ("postgresql",        "backend developer"),
    ("mongodb",           "backend developer"),
]

# Portales 100% remotos internacionales — nivel en inglés
_INTL_PORTALS = {"weworkremotely", "remotive", "remoteco"}


def _normalize(text: str) -> str:
    """Normaliza texto para comparación: minúsculas + quitar tildes básicas."""
    return (text.lower()
            .replace("á","a").replace("é","e").replace("í","i")
            .replace("ó","o").replace("ú","u").replace("ñ","n"))


def extract_keywords_from_seen_titles(
    titles: list[str],
    portal: str,
    existing_keywords: set[str] | None = None,
) -> list[dict]:
    """
    Extrae nuevas keywords de los títulos de ofertas vistas en el run actual.

    Analiza cada título, detecta tecnologías/roles específicos y genera
    entradas de keyword con formato KEYWORD_GROUPS.

    Args:
        titles           : lista de títulos de ofertas vistas (pueden repetirse)
        portal           : nombre del portal (para saber si usar ES o EN)
        existing_keywords: set de keywords ya conocidas — no se duplican

    Returns:
        Lista de dicts {label, keyword, mode, scan} listos para _kw_queue
    """
    if not titles:
        return []

    stats   = _load_stats()
    known   = set(stats.keys()) | (existing_keywords or set())
    intl    = portal in _INTL_PORTALS
    level   = "entry level" if intl else "junior"

    # Contar cuántas veces aparece cada tech en los títulos (mínimo 2 para generar)
    tech_counter: dict[str, int] = {}
    for title in titles:
        norm = _normalize(title)
        for fragment, base_term in _TECH_PATTERN_MAP:
            if fragment in norm:
                tech_counter[base_term] = tech_counter.get(base_term, 0) + 1

    generated: list[dict] = []
    for base_term, count in sorted(tech_counter.items(), key=lambda x: -x[1]):
        if count < 1:
            continue
        # Generar siempre "junior" y "sin experiencia" para cada tech detectada
        mods_to_add = ["junior"] if intl else ["junior", "sin experiencia"]
        for mod in mods_to_add:
            candidate     = f"{base_term} {mod}"
            candidate_key = candidate.lower().strip()
            if candidate_key in known:
                continue

            if candidate_key not in stats:
                stats[candidate_key] = {
                    "source": f"scan_extracted:{portal}",
                    "added":  str(date.today()),
                    "portals": {},
                }
            known.add(candidate_key)

            label = _infer_label(base_term)
            generated.append({
                "label":   label,
                "keyword": candidate,
                "mode":    "it",
                "scan":    True,
            })
            log.info("[KW_SCAN_EXTRACT] '%s' extraida de %d titulos en %s",
                     candidate, count, portal)
            print(f"  [KW_SCAN] '{candidate}' detectada en {count} oferta(s) — añadida")

        if len(generated) >= 6:   # máximo 6 nuevas por run (3 techs × 2 mods)
            break

    if generated:
        _save_stats(stats)

    return generated


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

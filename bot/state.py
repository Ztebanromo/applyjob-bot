"""
Estado en memoria para la sesión actual.

Diseño simplificado:
  - Deduplicación IN-MEMORY: evita postular dos veces en la misma sesión.
    Al reiniciar el servidor se borra → cada sesión arranca fresca.
  - Sin historial persistente entre sesiones (no SQLite para postulaciones).
  - Se preserva: keyword_stats.json (rendimiento) y qa_cache.json (preguntas).

Datos permanentes (JSON, no tocar aquí):
  data/keyword_stats.json  — keywords más efectivas por portal
  data/qa_cache.json       — respuestas a preguntas de formularios
"""
import csv
import datetime
import logging
import threading
from collections import defaultdict
from pathlib import Path

log = logging.getLogger("applyjob.state")

_LOGS_DIR = Path(__file__).parent.parent / "logs"

# Estados que representan una postulación REAL ya enviada — no debe
# reintentarse aunque el bot se reinicie (ver también bot/verification.py).
_REAL_APPLY_STATUSES = {"applied", "external_apply"}

# ---------------------------------------------------------------------------
# Estado en memoria — se limpia al reiniciar el proceso
# ---------------------------------------------------------------------------
_lock = threading.Lock()

# URLs vistas esta sesión → bloquea re-visita dentro del mismo run
_seen: set[str] = set()

# Log de postulaciones de esta sesión (para stats en consola/dashboard)
_session_log: list[dict] = []


# ---------------------------------------------------------------------------
# API pública (misma interfaz que antes — compatible con el resto del código)
# ---------------------------------------------------------------------------

def already_applied(url: str) -> bool:
    """
    True si la URL ya fue procesada en esta sesión.
    Se resetea al reiniciar el servidor.
    """
    with _lock:
        return url in _seen


def save_application(url: str, portal: str, title: str, status: str) -> None:
    """
    Marca la URL como vista y registra el resultado en el log de sesión.
    No persiste en disco — solo vive mientras el proceso esté corriendo.
    """
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with _lock:
        _seen.add(url)
        _session_log.append({
            "url":        url,
            "portal":     portal,
            "title":      title,
            "status":     status,
            "applied_at": now,
        })
    log.debug("Sesión: %s -> %s", url[:60], status)


# ---------------------------------------------------------------------------
# Estadísticas de sesión
# ---------------------------------------------------------------------------

def get_stats() -> dict:
    """Resumen de la sesión actual agrupado por portal y estado."""
    with _lock:
        log_copy = list(_session_log)

    total = len(log_copy)
    by_portal: dict = defaultdict(lambda: defaultdict(int))
    for r in log_copy:
        by_portal[r["portal"]][r["status"]] += 1

    return {
        "total":    total,
        "by_portal": {p: dict(s) for p, s in by_portal.items()},
    }


def get_recent(limit: int = 20) -> list[dict]:
    """Últimas N entradas de la sesión, más recientes primero."""
    with _lock:
        return list(reversed(_session_log[-limit:]))


def get_errors(portal: str = "") -> list[dict]:
    """Entradas con status que empieza con 'error' en esta sesión."""
    with _lock:
        rows = [r for r in _session_log if r["status"].startswith("error")]
    if portal:
        rows = [r for r in rows if r["portal"] == portal]
    return rows


def seed_seen_from_logs(days: int = 7) -> int:
    """
    Precarga `_seen` con las URLs ya postuladas (status applied/external_apply)
    en los últimos `days` días desde logs/applied_*.csv.

    Evita que cada nuevo proceso del bot (cada corrida de main.py) vuelva a
    postular a la misma oferta porque su dedup en memoria arranca vacío —
    causaba postulaciones duplicadas al mismo URL en corridas separadas
    el mismo día (ej: chiletrabajos 3849229 postulado 3 veces el 2026-06-09).

    Retorna la cantidad de URLs nuevas agregadas a `_seen`.
    """
    if not _LOGS_DIR.exists():
        return 0
    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    added = 0
    for file in sorted(_LOGS_DIR.glob("applied_*.csv")):
        # nombre: applied_YYYY-MM-DD.csv
        file_date = file.stem.replace("applied_", "")
        if file_date < cutoff:
            continue
        try:
            with open(file, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    url = r.get("url") or ""
                    status = (r.get("status") or "").lower()
                    if url and status in _REAL_APPLY_STATUSES:
                        with _lock:
                            if url not in _seen:
                                _seen.add(url)
                                added += 1
        except Exception as exc:
            log.warning("seed_seen_from_logs error reading %s: %s", file, exc)
    if added:
        log.info("[STATE] Precargadas %d URLs ya postuladas (últimos %d días) — dedup cross-sesión", added, days)
    return added


def reset_session() -> None:
    """Limpia el estado de la sesión (útil entre runs del bot)."""
    with _lock:
        _seen.clear()
        _session_log.clear()
    log.info("Estado de sesión reiniciado.")


# ---------------------------------------------------------------------------
# Stubs de compatibilidad (usados en otros módulos — no hacen nada)
# ---------------------------------------------------------------------------

def purge_old(days: int = 90) -> int:
    """No-op: sin datos persistentes que purgar."""
    return 0


def print_stats() -> None:
    """Imprime estadísticas de la sesión en consola."""
    stats = get_stats()
    print(f"\n{'='*52}")
    print(f"  Sesión actual  —  Procesadas: {stats['total']}")
    print(f"{'='*52}")
    for portal, statuses in stats["by_portal"].items():
        print(f"\n  {portal}")
        for status, cnt in statuses.items():
            bar = "#" * min(cnt, 20)
            print(f"    {status:<28} {cnt:>3}  {bar}")
    recent = get_recent(5)
    if recent:
        print(f"\n  Últimas 5 procesadas:")
        for r in recent:
            print(f"    [{r['applied_at'][:10]}] {r['portal']:<14} "
                  f"{r['status']:<20} {r['title'][:35]}")
    print()

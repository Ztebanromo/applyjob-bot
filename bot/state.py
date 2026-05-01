"""
Persistencia de estado via SQLite.

Responsabilidades:
  - Deduplicación: evitar postular dos veces a la misma oferta
  - Historial: registro permanente de todas las postulaciones
  - Estadísticas: resumen por portal y estado
  - Limpieza: purge automático de registros viejos

Esquema de la base de datos:
    applications (
        id         INTEGER PK AUTOINCREMENT,
        url        TEXT UNIQUE NOT NULL,   -- URL canónica de la oferta
        portal     TEXT NOT NULL,          -- "linkedin", "indeed", etc.
        title      TEXT DEFAULT '',        -- título del puesto
        status     TEXT NOT NULL,          -- "applied", "skipped_*", "error: ..."
        applied_at TEXT NOT NULL           -- ISO timestamp
    )

Archivo: data/applyjob.db (excluido de git por .gitignore)
"""
import sqlite3
import datetime
import logging
from pathlib import Path
from contextlib import contextmanager

log     = logging.getLogger("applyjob.state")
DB_PATH = Path(__file__).parent.parent / "data" / "applyjob.db"


# ---------------------------------------------------------------------------
# Inicialización y contexto de conexión
# ---------------------------------------------------------------------------

def _init_db(conn: sqlite3.Connection) -> None:
    """Crea la tabla y el índice si no existen."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS applications (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            url         TEXT    NOT NULL UNIQUE,
            portal      TEXT    NOT NULL,
            title       TEXT    DEFAULT '',
            status      TEXT    NOT NULL,
            applied_at  TEXT    NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_url ON applications(url)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_portal ON applications(portal)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_applied_at ON applications(applied_at)")
    conn.commit()


@contextmanager
def _conn():
    """Context manager que abre, inicializa y cierra la conexión SQLite."""
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    try:
        _init_db(con)
        yield con
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Operaciones principales
# ---------------------------------------------------------------------------

def already_applied(url: str) -> bool:
    """
    Consulta O(1) — retorna True si la URL ya fue procesada con CUALQUIER status.

    Incluye "error" y "skipped" deliberadamente: si algo falló, el bot
    lo vuelve a intentar en el siguiente run manualmente o vía --retry-errors.

    Args:
        url: URL canónica de la oferta

    Returns:
        bool
    """
    with _conn() as con:
        row = con.execute(
            "SELECT id FROM applications WHERE url = ?", (url,)
        ).fetchone()
        return row is not None


def save_application(url: str, portal: str, title: str, status: str) -> None:
    """
    Inserta o actualiza el registro de una oferta (upsert atómico).

    Si la URL ya existe, actualiza status y applied_at.
    Esto permite que un "error" del run anterior sea sobreescrito
    con "applied" si se reintenta manualmente.

    Args:
        url    : URL canónica de la oferta
        portal : nombre del portal
        title  : título del puesto (puede ser vacío)
        status : resultado de la postulación
    """
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with _conn() as con:
        con.execute(
            """
            INSERT INTO applications (url, portal, title, status, applied_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                status     = excluded.status,
                applied_at = excluded.applied_at
            """,
            (url, portal, title, status, now),
        )
        con.commit()
    log.debug("Guardado: %s → %s", url[:60], status)


# ---------------------------------------------------------------------------
# Consultas y estadísticas
# ---------------------------------------------------------------------------

def get_stats() -> dict:
    """
    Retorna un resumen de postulaciones agrupado por portal y estado.

    Returns:
        {
            "total": int,
            "by_portal": {
                "linkedin": {"applied": 23, "skipped_no_easy_apply": 8, ...},
                ...
            }
        }
    """
    with _conn() as con:
        rows = con.execute("""
            SELECT portal, status, COUNT(*) as cnt
            FROM applications
            GROUP BY portal, status
            ORDER BY portal, cnt DESC
        """).fetchall()
        total = con.execute("SELECT COUNT(*) FROM applications").fetchone()[0]

    stats: dict = {"total": total, "by_portal": {}}
    for row in rows:
        p = row["portal"]
        if p not in stats["by_portal"]:
            stats["by_portal"][p] = {}
        stats["by_portal"][p][row["status"]] = row["cnt"]
    return stats


def get_recent(limit: int = 20) -> list[dict]:
    """
    Retorna las últimas N postulaciones ordenadas por fecha descendente.

    Args:
        limit: máximo de registros a retornar (default: 20)

    Returns:
        Lista de dicts con keys: portal, title, status, applied_at, url
    """
    with _conn() as con:
        rows = con.execute(
            """SELECT portal, title, status, applied_at, url
               FROM applications ORDER BY applied_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_errors(portal: str = "") -> list[dict]:
    """
    Retorna postulaciones con status que empieza con 'error'.
    Útil para identificar qué re-intentar manualmente.

    Args:
        portal: filtrar por portal (vacío = todos)

    Returns:
        Lista de dicts
    """
    with _conn() as con:
        if portal:
            rows = con.execute(
                "SELECT * FROM applications WHERE status LIKE 'error%' AND portal = ? "
                "ORDER BY applied_at DESC",
                (portal,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM applications WHERE status LIKE 'error%' "
                "ORDER BY applied_at DESC"
            ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Limpieza / Purge
# ---------------------------------------------------------------------------

def purge_old(days: int = 90) -> int:
    """
    Elimina registros con applied_at más antiguo que `days` días.

    Solo elimina registros con status 'skipped_*', 'dry_run' o 'external:*'.
    Los registros 'applied' y 'error' se conservan indefinidamente como historial.

    Args:
        days: antigüedad mínima en días para eliminar (default: 90)

    Returns:
        Cantidad de registros eliminados
    """
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()
    with _conn() as con:
        result = con.execute(
            """
            DELETE FROM applications
            WHERE applied_at < ?
              AND (
                  status LIKE 'skipped%'
                  OR status = 'dry_run'
                  OR status LIKE 'external%'
              )
            """,
            (cutoff,),
        )
        con.commit()
        deleted = result.rowcount
    if deleted:
        log.info("Purge: eliminados %d registros anteriores a %d días", deleted, days)
    return deleted


# ---------------------------------------------------------------------------
# Output de consola
# ---------------------------------------------------------------------------

def print_stats() -> None:
    """Imprime estadísticas formateadas en consola."""
    stats = get_stats()
    print(f"\n{'='*52}")
    print(f"  ApplyJob Stats  —  Total: {stats['total']}")
    print(f"{'='*52}")
    for portal, statuses in stats["by_portal"].items():
        print(f"\n  {portal}")
        for status, cnt in statuses.items():
            bar = "█" * min(cnt, 20)
            print(f"    {status:<28} {cnt:>3}  {bar}")
    recent = get_recent(5)
    if recent:
        print(f"\n  Últimas 5 postulaciones:")
        for r in recent:
            print(f"    [{r['applied_at'][:10]}] {r['portal']:<14} "
                  f"{r['status']:<20} {r['title'][:35]}")
    print()

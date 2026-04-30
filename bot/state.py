"""
Persistencia de estado via SQLite.
Evita postular dos veces a la misma oferta entre runs.
"""
import sqlite3
import datetime
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).parent.parent / "data" / "applyjob.db"


def _init_db(conn: sqlite3.Connection) -> None:
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
    conn.commit()


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    try:
        _init_db(con)
        yield con
    finally:
        con.close()


def already_applied(url: str) -> bool:
    """True si la URL ya fue procesada (con cualquier status)."""
    with _conn() as con:
        row = con.execute(
            "SELECT id FROM applications WHERE url = ?", (url,)
        ).fetchone()
        return row is not None


def save_application(url: str, portal: str, title: str, status: str) -> None:
    """Inserta o actualiza el registro de una oferta."""
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


def get_stats() -> dict:
    """Retorna un resumen de postulaciones por portal y estado."""
    with _conn() as con:
        rows = con.execute("""
            SELECT portal, status, COUNT(*) as cnt
            FROM applications
            GROUP BY portal, status
            ORDER BY portal, cnt DESC
        """).fetchall()
        total = con.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
    stats = {"total": total, "by_portal": {}}
    for row in rows:
        p = row["portal"]
        if p not in stats["by_portal"]:
            stats["by_portal"][p] = {}
        stats["by_portal"][p][row["status"]] = row["cnt"]
    return stats


def get_recent(limit: int = 20) -> list[dict]:
    """Retorna las últimas N postulaciones."""
    with _conn() as con:
        rows = con.execute(
            """SELECT portal, title, status, applied_at, url
               FROM applications ORDER BY applied_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def print_stats() -> None:
    stats = get_stats()
    print(f"\n{'='*50}")
    print(f"  ApplyJob Stats  —  Total: {stats['total']}")
    print(f"{'='*50}")
    for portal, statuses in stats["by_portal"].items():
        print(f"\n  {portal}")
        for status, cnt in statuses.items():
            print(f"    {status:<20} {cnt}")
    print()

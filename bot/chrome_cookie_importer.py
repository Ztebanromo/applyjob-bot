"""chrome_cookie_importer.py — stub. Importación de cookies sin CDP."""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

PORTAL_DOMAINS: dict[str, list[str]] = {
    "linkedin":      [".linkedin.com", ".www.linkedin.com"],
    "computrabajo":  [".computrabajo.com", ".cl.computrabajo.com"],
    "laborum":       [".laborum.cl", ".www.laborum.cl"],
    "trabajando":    [".trabajando.cl", ".www.trabajando.cl"],
    "infojobs":      [".infojobs.net", ".www.infojobs.net"],
    "chiletrabajos": [".chiletrabajos.cl", ".www.chiletrabajos.cl"],
    "getonyboard":   [".getonbrd.com", ".www.getonbrd.com"],
}


def chrome_cookies_available() -> bool:
    return False


def import_portal_cookies(portal: str, session_dir: str | Path) -> bool:
    return False


def copy_chrome_fingerprint(session_dir: str | Path) -> bool:
    return False

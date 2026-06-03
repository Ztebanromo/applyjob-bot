"""
session_importer.py — Importa cookies desde CDP al playwright_state.json de cada portal.

Uso:
    from bot.session_importer import import_all_from_cdp
    results = import_all_from_cdp()   # {portal: n_cookies_importadas}
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from bot.chrome_cookie_importer import PORTAL_DOMAINS
from bot.session_config import PORTALS_REQUIRE_LOGIN

log = logging.getLogger(__name__)

PORTALS_TO_IMPORT: list[str] = list(PORTALS_REQUIRE_LOGIN)

SESSIONS_DIR = Path(__file__).parent.parent / "sessions"


def import_all_from_cdp(
    cdp_url: str = "http://127.0.0.1:9222",
    portals: list[str] | None = None,
) -> dict[str, int]:
    """
    Conecta al Chrome CDP, extrae cookies de cada portal y las guarda
    como playwright_state.json en sessions/<portal>/.

    Args:
        cdp_url: URL del endpoint CDP (default: http://127.0.0.1:9222)
        portals: Lista de portales a importar. None = todos.

    Returns:
        dict {portal: n_cookies} — 0 si no se encontraron cookies para ese portal.
    """
    targets = portals if portals is not None else PORTALS_TO_IMPORT
    results: dict[str, int] = {p: 0 for p in targets}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error("[IMPORTER] Playwright no instalado.")
        return results

    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.connect_over_cdp(cdp_url)
            except Exception as exc:
                log.warning("[IMPORTER] No se pudo conectar a CDP (%s): %s", cdp_url, exc)
                return results

            ctx = browser.contexts[0] if browser.contexts else None
            if ctx is None:
                log.warning("[IMPORTER] Sin contexto CDP disponible.")
                return results

            all_cookies = ctx.cookies()
            log.info("[IMPORTER] %d cookies totales en CDP.", len(all_cookies))

            for portal in targets:
                domains = PORTAL_DOMAINS.get(portal, [])
                portal_cookies = [
                    c for c in all_cookies
                    if any(d.lstrip(".") in c.get("domain", "") for d in domains)
                ]

                if not portal_cookies:
                    log.info("[IMPORTER] %s: sin cookies en CDP.", portal)
                    continue

                session_dir = SESSIONS_DIR / portal
                session_dir.mkdir(parents=True, exist_ok=True)
                state_file = session_dir / "playwright_state.json"

                storage_state = {"cookies": portal_cookies, "origins": []}
                state_file.write_text(
                    json.dumps(storage_state, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                results[portal] = len(portal_cookies)
                log.info("[IMPORTER] %s: %d cookies guardadas.", portal, len(portal_cookies))

    except Exception as exc:
        log.error("[IMPORTER] Error inesperado: %s", exc)

    return results

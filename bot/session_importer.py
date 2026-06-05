"""session_importer.py — Importa storage_state desde un Chrome con CDP abierto.

Conecta a Chrome/Chromium en `cdp_url` (por defecto http://127.0.0.1:9222), extrae
el `storage_state` del contexto principal y escribe `playwright_state.json`
en `sessions/<portal>/playwright_state.json` para cada portal objetivo.

Esto permite que el bot restaure sesiones usando `storage_state` y evite
re-logins manuales repetidos.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict

log = logging.getLogger(__name__)

from bot.session_config import PORTALS_REQUIRE_LOGIN

PORTALS_TO_IMPORT: list[str] = list(PORTALS_REQUIRE_LOGIN)


def import_all_from_cdp(cdp_url: str = "http://127.0.0.1:9222", portals: list[str] | None = None) -> Dict[str, str]:
    """Importa sesiones desde un Chrome con CDP.

    Args:
        cdp_url: URL del endpoint CDP (ej: http://127.0.0.1:9222)
        portals: lista de portales a importar (por defecto PORTALS_TO_IMPORT)

    Returns:
        dict: { portal: 'imported'|'no_cookies'|'error' }
    """
    targets = portals if portals is not None else PORTALS_TO_IMPORT
    results: Dict[str, str] = {p: 'error' for p in targets}

    sessions_dir = Path(__file__).resolve().parent.parent / 'sessions'
    sessions_dir.mkdir(parents=True, exist_ok=True)

    try:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as e:
            log.warning("Playwright no disponible: %s", e)
            return {p: 'error' for p in targets}

        with sync_playwright() as pw:
            try:
                browser = pw.chromium.connect_over_cdp(cdp_url)
            except Exception as exc:
                log.warning("No se pudo conectar a CDP %s: %s", cdp_url, exc)
                return {p: 'error' for p in targets}

            # Intentar extraer storage_state del primer contexto disponible
            try:
                ctxs = getattr(browser, 'contexts', []) or []
                ctx = ctxs[0] if ctxs else None
                if ctx is None:
                    # si no hay contexts disponibles, intentar crear una página
                    pages = getattr(browser, 'pages', []) or []
                    ctx = browser.contexts[0] if getattr(browser, 'contexts', []) else None

                if ctx is None:
                    log.warning("CDP conectado pero sin context disponible")
                    return {p: 'error' for p in targets}

                state = ctx.storage_state()  # dict con cookies y localStorage
                cookies = state.get('cookies', [])
                if not cookies:
                    log.info("CDP: no se encontraron cookies en storage_state")
                    return {p: 'no_cookies' for p in targets}

                # Escribir el mismo storage_state en cada carpeta de sesión
                for p in targets:
                    try:
                        sd = sessions_dir / p
                        sd.mkdir(parents=True, exist_ok=True)
                        state_file = sd / 'playwright_state.json'
                        with open(state_file, 'w', encoding='utf-8') as f:
                            json.dump(state, f, ensure_ascii=False)
                        results[p] = 'imported'
                    except Exception as _w:
                        log.warning("No se pudo guardar storage_state para %s: %s", p, _w)
                        results[p] = 'error'
                return results

            finally:
                try:
                    browser.close()
                except Exception:
                    pass

    except Exception as exc:
        log.warning("import_all_from_cdp error: %s", exc)
        return {p: 'error' for p in targets}


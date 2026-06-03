"""session_importer.py — stub. Sin CDP; sesiones se gestionan por playwright_state.json."""
from __future__ import annotations

from bot.session_config import PORTALS_REQUIRE_LOGIN

PORTALS_TO_IMPORT: list[str] = list(PORTALS_REQUIRE_LOGIN)


def import_all_from_cdp(cdp_url: str = "", portals: list[str] | None = None) -> dict[str, int]:
    """Sin CDP — retorna dict vacío."""
    targets = portals if portals is not None else PORTALS_TO_IMPORT
    return {p: 0 for p in targets}

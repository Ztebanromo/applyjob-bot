"""api/verification.py — Verificación de postulaciones reales en cada portal."""
from __future__ import annotations

import logging

from api.app import socketio
from api.state import state

_log = logging.getLogger("applyjob.server")


def _verify_postulations(portals, retry=True):
    """Verifica las postulaciones hoy y actualiza el conteo verificado.

    Si algún portal queda con postulaciones "missing" (no confirmadas en
    "Mis postulaciones"), se reintenta UNA vez solo para esos portales —
    cubre casos de SPA lenta cargando la lista la primera vez.
    """
    from bot.verification import verify_all_postulations
    summary = {}
    try:
        summary = verify_all_postulations(portals=portals, headless=True)
    except Exception as exc:
        _log.warning("_verify_postulations error: %s", exc)

    # ── Reintento dirigido a portales con discrepancias ──────────────────
    if retry:
        _retry_portals = [
            p for p, r in summary.items()
            if isinstance(r, dict) and r.get("missing")
        ]
        if _retry_portals:
            _log.info("[VERIFY] Reintentando verificación para: %s", _retry_portals)
            socketio.emit('postulations_verified', {
                'summary': summary,
                'retrying': _retry_portals,
            }, namespace='/bot')
            try:
                retry_summary = verify_all_postulations(portals=_retry_portals, headless=True)
                for p, r in retry_summary.items():
                    if isinstance(r, dict):
                        summary[p] = r
            except Exception as exc:
                _log.warning("_verify_postulations retry error: %s", exc)

    verified_applied = sum(
        r.get("found_on_site", 0)
        for r in summary.values()
        if isinstance(r, dict)
    )
    with state.lock:
        state.stats["verified_applied"] = verified_applied
        state.stats["applied"] = verified_applied
        by_portal = state.stats.setdefault("by_portal", {})
        for p, r in summary.items():
            if not isinstance(r, dict):
                continue
            entry = by_portal.setdefault(p, {})
            entry["verify_status"] = r.get("status", "checked")
            entry["verify_found"] = r.get("found_on_site", 0)
            entry["verify_total"] = r.get("applied_today", 0)
            entry["verify_missing"] = len(r.get("missing", []) or [])
    socketio.emit('update_stats', state.stats, namespace='/bot')
    socketio.emit('postulations_verified', {
        'summary': summary,
        'verified_applied': verified_applied,
    }, namespace='/bot')
    return summary

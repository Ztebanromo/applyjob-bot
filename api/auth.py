"""api/auth.py — HTTP Basic Auth global para el dashboard."""
from __future__ import annotations

import os

from flask import request, Response as FlaskResponse

from api.app import app

# Configura DASHBOARD_PASSWORD en .env para proteger el dashboard.
# Si está vacío, no pide autenticación (comportamiento por defecto).
_DASHBOARD_PW = os.getenv("DASHBOARD_PASSWORD", "")
if not _DASHBOARD_PW:
    print(
        "\n[AVISO DE SEGURIDAD] DASHBOARD_PASSWORD no está configurado.\n"
        "  Cualquier proceso local puede acceder al dashboard y disparar el bot.\n"
        "  Agrega DASHBOARD_PASSWORD=<clave> en .env para protegerlo.\n"
    )


@app.before_request
def _global_auth():
    """
    Aplica HTTP Basic Auth a TODAS las rutas si DASHBOARD_PASSWORD está configurado.
    Las peticiones de SocketIO pasan porque van por WebSocket, no HTTP normal.
    """
    if not _DASHBOARD_PW:
        return
    auth = request.authorization
    if auth and auth.password == _DASHBOARD_PW:
        return
    return FlaskResponse(
        "Acceso restringido — configura DASHBOARD_PASSWORD en .env.",
        401,
        {"WWW-Authenticate": 'Basic realm="ApplyJob Dashboard"'},
    )

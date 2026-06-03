"""chrome_cdp.py — stub vacío. CDP eliminado; el bot usa solo Chromium."""
from __future__ import annotations

def is_port_open(*args, **kwargs) -> bool:
    return False

def connect(*args, **kwargs) -> bool:
    return False

def disconnect(*args, **kwargs) -> None:
    pass

def new_page(*args, **kwargs):
    return None

def check_session_cdp(portal: str, *args, **kwargs) -> str:
    return "no_connection"

def save_all_sessions(*args, **kwargs) -> dict:
    return {}

def get_status(*args, **kwargs) -> dict:
    return {"port_open": False, "connected": False, "mode": "chromium"}

CDP_URL = "http://127.0.0.1:9222"
CDP_PORT = 9222

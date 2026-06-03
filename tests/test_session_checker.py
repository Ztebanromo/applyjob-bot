"""Tests unitarios para session_checker — sin browser real."""
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bot.session_checker import SessionResult, has_real_cookies, check_session


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_session_dir(cookie_count: int = 0) -> Path:
    """Crea directorio de sesión temporal con estructura Chromium correcta."""
    tmp = Path(tempfile.mkdtemp())
    net = tmp / "Default" / "Network"
    net.mkdir(parents=True)
    db_path = net / "Cookies"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE cookies (
            name TEXT, host_key TEXT, path TEXT,
            expires_utc INTEGER, is_secure INTEGER,
            is_httponly INTEGER, value TEXT
        )
    """)
    for i in range(cookie_count):
        conn.execute(
            "INSERT INTO cookies VALUES (?,?,?,?,?,?,?)",
            (f"cookie_{i}", ".example.com", "/", 9999999999, 1, 0, f"val_{i}"),
        )
    conn.commit()
    conn.close()
    return tmp


def _make_playwright_ctx(url="https://example.com", pos_el=None, neg_el=None):
    """Crea mock de contexto Playwright."""
    mock_pw = MagicMock()
    mock_ctx = MagicMock()
    mock_pg = MagicMock()
    mock_pw.__enter__ = MagicMock(return_value=mock_pw)
    mock_pw.__exit__ = MagicMock(return_value=False)
    mock_pw.chromium.launch_persistent_context.return_value = mock_ctx
    mock_ctx.new_page.return_value = mock_pg
    mock_pg.url = url

    # Por defecto query_selector devuelve None (ningún selector matchea)
    if pos_el is not None:
        mock_pg.query_selector.return_value = pos_el
    else:
        mock_pg.query_selector.return_value = None

    return mock_pw, mock_ctx, mock_pg


# ─── Tests has_real_cookies ───────────────────────────────────────────────────

def test_has_real_cookies_no_directory():
    assert has_real_cookies(Path(tempfile.mkdtemp()) / "nonexistent") is False


def test_has_real_cookies_empty_db():
    session = _make_session_dir(cookie_count=0)
    assert has_real_cookies(session) is False


def test_has_real_cookies_with_cookies():
    session = _make_session_dir(cookie_count=3)
    assert has_real_cookies(session) is True


def test_has_real_cookies_missing_file():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "Default" / "Network").mkdir(parents=True)
    assert has_real_cookies(tmp) is False


# ─── Tests check_session ──────────────────────────────────────────────────────

def test_check_session_no_cookies():
    session = _make_session_dir(cookie_count=0)
    with patch("bot.chrome_cdp.is_port_open", return_value=False):
        assert check_session("laborum", session) == SessionResult.NO_COOKIES


def test_check_session_recent_state_file_returns_ok():
    session = _make_session_dir(cookie_count=5)
    state_file = session / "playwright_state.json"
    state_file.write_text('{"cookies":[]}')
    recent = time.time() - 1800  # hace 30 min
    os.utime(state_file, (recent, recent))
    assert check_session("laborum", session) == SessionResult.OK


def test_check_session_login_redirect_expired(mocker):
    session = _make_session_dir(cookie_count=10)
    mock_pw, _, mock_pg = _make_playwright_ctx(
        url="https://www.linkedin.com/login?session_redirect=..."
    )
    with patch("bot.session_checker.sync_playwright", return_value=mock_pw):
        result = check_session("linkedin", session)
    assert result == SessionResult.EXPIRED


def test_check_session_positive_selector_ok(mocker):
    from bot.session_config import NOT_LOGGED_IN_SIGNALS
    session = _make_session_dir(cookie_count=10)
    mock_pw, _, mock_pg = _make_playwright_ctx(url="https://www.laborum.cl/dashboard")
    # Neg selectors deben devolver None (no visibles); pos selectors devuelven elemento visible
    neg_count = len(NOT_LOGGED_IN_SIGNALS.get("laborum", []))
    visible_el = MagicMock()
    visible_el.is_visible.return_value = True
    # None para cada neg selector, luego visible_el para el primero positivo
    mock_pg.query_selector.side_effect = [None] * neg_count + [visible_el]
    with patch("bot.session_checker.sync_playwright", return_value=mock_pw):
        result = check_session("laborum", session)
    assert result == SessionResult.OK


def test_check_session_no_positive_selectors_expired(mocker):
    session = _make_session_dir(cookie_count=10)
    mock_pw, _, mock_pg = _make_playwright_ctx(url="https://www.laborum.cl/dashboard")
    mock_pg.query_selector.return_value = None
    mock_pg.wait_for_selector.side_effect = Exception("timeout")
    with patch("bot.chrome_cdp.is_port_open", return_value=False), \
         patch("bot.session_checker.sync_playwright", return_value=mock_pw):
        result = check_session("laborum", session)
    assert result == SessionResult.EXPIRED


def test_session_result_values():
    assert SessionResult.OK.value == "ok"
    assert SessionResult.EXPIRED.value == "expired"
    assert SessionResult.NO_COOKIES.value == "no_cookies"
    assert SessionResult.ERROR.value == "error"

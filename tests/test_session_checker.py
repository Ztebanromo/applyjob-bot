"""Tests unitarios para session_checker — sin browser real."""
import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bot.session_checker import SessionResult, has_real_cookies, check_session


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_session_dir(cookie_count: int = 0) -> Path:
    """Crea directorio de sesión temporal con playwright_state.json."""
    tmp = Path(tempfile.mkdtemp())
    
    if cookie_count > 0:
        cookies = [
            {
                "name": f"cookie_{i}",
                "domain": ".example.com",
                "path": "/",
                "value": f"val_{i}"
            }
            for i in range(cookie_count)
        ]
        state = {"cookies": cookies}
        (tmp / "playwright_state.json").write_text(json.dumps(state), encoding="utf-8")
    
    return tmp


def _make_playwright_ctx(url="https://example.com", pos_el=None):
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
    assert has_real_cookies(tmp) is False


# ─── Tests check_session ──────────────────────────────────────────────────────

def test_check_session_no_cookies():
    session = _make_session_dir(cookie_count=0)
    assert check_session("laborum", session) == SessionResult.NO_COOKIES


def test_check_session_recent_state_file_returns_ok():
    session = _make_session_dir(cookie_count=5)
    state_file = session / "playwright_state.json"
    recent = time.time() - 1800  # hace 30 min
    os.utime(state_file, (recent, recent))

    from bot.session_config import NOT_LOGGED_IN_SIGNALS
    mock_pw, _, mock_pg = _make_playwright_ctx(url="https://www.laborum.cl/dashboard")
    visible_el = MagicMock()
    visible_el.is_visible.return_value = True
    mock_pg.query_selector.side_effect = [None] * len(NOT_LOGGED_IN_SIGNALS.get("laborum", [])) + [visible_el]
    with patch("bot.session_checker.sync_playwright", return_value=mock_pw):
        assert check_session("laborum", session) == SessionResult.OK


def test_check_session_login_redirect_expired():
    session = _make_session_dir(cookie_count=10)
    mock_pw, _, mock_pg = _make_playwright_ctx(
        url="https://www.linkedin.com/login?session_redirect=..."
    )
    with patch("bot.session_checker.sync_playwright", return_value=mock_pw):
        result = check_session("linkedin", session)
    assert result == SessionResult.EXPIRED


def test_check_session_positive_selector_ok():
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


def test_check_session_no_positive_selectors_expired():
    session = _make_session_dir(cookie_count=10)
    mock_pw, _, mock_pg = _make_playwright_ctx(url="https://www.laborum.cl/dashboard")
    mock_pg.query_selector.return_value = None
    mock_pg.wait_for_selector.side_effect = Exception("timeout")
    with patch("bot.session_checker.sync_playwright", return_value=mock_pw):
        result = check_session("laborum", session)
    assert result == SessionResult.EXPIRED


def test_session_result_values():
    assert SessionResult.OK.value == "ok"
    assert SessionResult.EXPIRED.value == "expired"
    assert SessionResult.NO_COOKIES.value == "no_cookies"
    assert SessionResult.ERROR.value == "error"

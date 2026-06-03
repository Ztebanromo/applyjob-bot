"""Tests unitarios para session_config — sin Playwright, sin browser."""
from bot.session_config import (
    VERIFY_URLS, LOGIN_URLS, LOGGED_IN_SIGNALS,
    NOT_LOGGED_IN_SIGNALS, PORTALS_REQUIRE_LOGIN,
    STEALTH_USER_AGENT,
)

EXPECTED_PORTALS = {
    "linkedin", "computrabajo", "laborum", "trabajando",
    "infojobs", "chiletrabajos", "getonyboard",
}


def test_all_portals_have_verify_url():
    for p in EXPECTED_PORTALS:
        assert p in VERIFY_URLS, f"{p} falta en VERIFY_URLS"
        assert VERIFY_URLS[p].startswith("https://"), f"{p}: URL inválida"


def test_all_portals_have_login_url():
    for p in EXPECTED_PORTALS:
        assert p in LOGIN_URLS, f"{p} falta en LOGIN_URLS"


def test_all_portals_have_logged_in_signals():
    for p in EXPECTED_PORTALS:
        assert p in LOGGED_IN_SIGNALS, f"{p} falta en LOGGED_IN_SIGNALS"
        assert len(LOGGED_IN_SIGNALS[p]) >= 1, f"{p}: sin selectores positivos"


def test_all_portals_have_not_logged_in_signals():
    for p in EXPECTED_PORTALS:
        assert p in NOT_LOGGED_IN_SIGNALS, f"{p} falta en NOT_LOGGED_IN_SIGNALS"
        assert len(NOT_LOGGED_IN_SIGNALS[p]) >= 1, f"{p}: sin selectores negativos"


def test_linkedin_login_url_is_homepage_not_login_page():
    # LinkedIn bloquea acceso directo a /login desde Playwright
    assert "/login" not in LOGIN_URLS["linkedin"], (
        "LinkedIn login_url debe ser la homepage, no /login (anti-bot)"
    )


def test_chiletrabajos_logged_in_has_div_logged():
    assert "div.logged" in LOGGED_IN_SIGNALS["chiletrabajos"]


def test_chiletrabajos_not_logged_in_has_ingresa_or_login():
    sels = NOT_LOGGED_IN_SIGNALS["chiletrabajos"]
    assert any(
        "ingresa" in s.lower() or "iniciar" in s.lower() or "email" in s.lower()
        for s in sels
    ), "chiletrabajos necesita selectores de login visibles cuando no hay sesión"


def test_getonyboard_logged_in_has_body_dashboard():
    assert "body.dashboard" in LOGGED_IN_SIGNALS["getonyboard"]


def test_trabajando_not_logged_has_menu_no_logueado():
    sels = NOT_LOGGED_IN_SIGNALS["trabajando"]
    assert any("menuNoLogueado" in s or "ingresarATuCuenta" in s for s in sels), (
        "trabajando DOM muestra div.menuNoLogueadoMovil cuando no logueado"
    )


def test_portals_require_login_set():
    assert PORTALS_REQUIRE_LOGIN == EXPECTED_PORTALS


def test_stealth_user_agent_looks_real():
    assert "Chrome" in STEALTH_USER_AGENT
    assert len(STEALTH_USER_AGENT) > 50


def test_no_duplicate_selectors_per_portal():
    for portal, sels in LOGGED_IN_SIGNALS.items():
        assert len(sels) == len(set(sels)), f"{portal}: selectores duplicados en LOGGED_IN_SIGNALS"
    for portal, sels in NOT_LOGGED_IN_SIGNALS.items():
        assert len(sels) == len(set(sels)), f"{portal}: selectores duplicados en NOT_LOGGED_IN_SIGNALS"

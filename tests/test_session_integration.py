"""
Tests de integración: verifican que engine.py y gui_server.py usan
session_config — no regresionar la duplicación eliminada.
"""
import ast
import pathlib


def _parse(path: str) -> ast.Module:
    return ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))


def _local_dict_names(tree: ast.Module) -> set:
    """Retorna nombres de variables asignadas en nivel módulo."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def test_engine_does_not_define_login_signals_locally():
    names = _local_dict_names(_parse("bot/engine.py"))
    for banned in ("_LOGIN_SIGNALS", "_LOGGED_IN_SIGNALS", "_LOGIN_URLS"):
        assert banned not in names, (
            f"engine.py define '{banned}' localmente — debe venir de session_config"
        )


def test_gui_server_does_not_define_signals_locally():
    names = _local_dict_names(_parse("gui_server.py"))
    for banned in ("_LOGIN_SIGNALS_SERVER", "_HOME_URLS_SERVER", "_LOGIN_PAGE_SIGNALS"):
        assert banned not in names, (
            f"gui_server.py define '{banned}' localmente — debe venir de session_config"
        )


def test_engine_imports_from_session_config():
    src = pathlib.Path("bot/engine.py").read_text(encoding="utf-8")
    assert "from bot.session_config import" in src, (
        "engine.py debe importar desde bot.session_config"
    )


def test_gui_server_imports_from_session_config():
    src = pathlib.Path("gui_server.py").read_text(encoding="utf-8")
    assert "from bot.session_config import" in src, (
        "gui_server.py debe importar desde bot.session_config"
    )


def test_import_chrome_endpoint_exists():
    src = pathlib.Path("gui_server.py").read_text(encoding="utf-8")
    assert "import-chrome-cookies" in src


def test_chrome_button_in_frontend():
    src = pathlib.Path("templates/index.html").read_text(encoding="utf-8")
    assert "importChromeBtn" in src
    assert "importFromChrome" in src


def test_session_checker_is_single_source():
    """session_checker.py debe ser el único que abre browsers headless para verificar."""
    engine_src = pathlib.Path("bot/engine.py").read_text(encoding="utf-8")
    server_src = pathlib.Path("gui_server.py").read_text(encoding="utf-8")

    # engine.py no debe tener su propia lógica de launch_persistent_context
    # en la función _session_is_active (solo debe llamar check_session)
    assert "check_session" in engine_src, "engine.py debe llamar check_session()"
    assert "check_session" in server_src, "gui_server.py debe llamar check_session()"


def test_linkedin_login_url_not_direct_login():
    from bot.session_config import LOGIN_URLS
    assert "/login" not in LOGIN_URLS["linkedin"], (
        "LinkedIn debe abrir la homepage, no /login directamente"
    )


def test_chiletrabajos_uses_div_logged():
    from bot.session_config import LOGGED_IN_SIGNALS
    assert "div.logged" in LOGGED_IN_SIGNALS["chiletrabajos"]


def test_getonyboard_uses_body_dashboard():
    from bot.session_config import LOGGED_IN_SIGNALS
    assert "body.dashboard" in LOGGED_IN_SIGNALS["getonyboard"]


def test_trabajando_detects_not_logged_in():
    from bot.session_config import NOT_LOGGED_IN_SIGNALS
    sels = NOT_LOGGED_IN_SIGNALS["trabajando"]
    assert any("menuNoLogueado" in s or "ingresarATuCuenta" in s for s in sels)


def test_no_raw_chromium_launch_in_engine():
    """engine.py no debe llamar pw.chromium.launch_persistent_context directamente."""
    src = pathlib.Path("bot/engine.py").read_text(encoding="utf-8")
    assert "pw.chromium.launch_persistent_context" not in src, (
        "Encontrado pw.chromium.launch_persistent_context en engine.py — migrar a select_browser_backend"
    )


def test_no_raw_chromium_launch_count_zero():
    """Cero llamadas a chromium.launch_persistent_context en engine.py."""
    src = pathlib.Path("bot/engine.py").read_text(encoding="utf-8")
    count = src.count("chromium.launch_persistent_context")
    assert count == 0, f"Encontradas {count} llamadas directas a chromium.launch_persistent_context"


def test_session_importer_module_exists():
    """bot/session_importer.py debe existir con import_all_from_cdp."""
    import importlib
    mod = importlib.import_module("bot.session_importer")
    assert hasattr(mod, "import_all_from_cdp")
    assert hasattr(mod, "PORTALS_TO_IMPORT")


def test_session_importer_portals_list():
    """PORTALS_TO_IMPORT debe incluir todos los portales que requieren login."""
    from bot.session_importer import PORTALS_TO_IMPORT
    from bot.session_config import PORTALS_REQUIRE_LOGIN
    for p in PORTALS_REQUIRE_LOGIN:
        assert p in PORTALS_TO_IMPORT, f"{p} falta en PORTALS_TO_IMPORT"


def test_import_all_from_cdp_returns_dict():
    """import_all_from_cdp retorna dict {portal: int_cookies}."""
    from bot.session_importer import import_all_from_cdp
    result = import_all_from_cdp(cdp_url="http://127.0.0.1:19999")
    assert isinstance(result, dict)


def test_import_all_sessions_endpoint_exists():
    """gui_server.py debe tener el endpoint /api/import-all-sessions."""
    src = pathlib.Path("gui_server.py").read_text(encoding="utf-8")
    assert "/api/import-all-sessions" in src

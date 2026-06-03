#!/usr/bin/env python
"""
verify_e2e.py — Verificación end-to-end del bot sin pytest.

Prueba: CDP conecta → cookies presentes → portales verifican → config válida.

Uso:
    .venv/Scripts/python scripts/verify_e2e.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

PASS = "PASS"
FAIL = "FAIL"


def check(label: str, fn) -> str:
    try:
        ok, detail = fn()
        status = PASS if ok else FAIL
        marker = "OK" if ok else "!!"
        print(f"  [{marker}] {label}: {detail}")
        return status
    except Exception as e:
        print(f"  [!!] {label}: excepcion — {e}")
        return FAIL


def main():
    print("\n=== ApplyJob Bot -- Verificacion E2E ===\n")
    results = []

    # 1. Importaciones basicas
    def check_imports():
        from bot.session_config import PORTALS_REQUIRE_LOGIN
        from bot.session_checker import check_session  # noqa
        from bot.session_importer import import_all_from_cdp  # noqa
        return True, f"{len(PORTALS_REQUIRE_LOGIN)} portales configurados"
    results.append(check("Importaciones bot", check_imports))

    # 2. CDP disponible
    def check_cdp_port():
        from bot.chrome_cdp import is_port_open
        ok = is_port_open()
        return ok, "puerto 9222 abierto" if ok else "puerto 9222 CERRADO -- ejecutar chrome_debug.bat"
    results.append(check("CDP port 9222", check_cdp_port))

    # 3. Sesiones actuales
    def check_sessions():
        from bot.session_checker import check_session, SessionResult
        SESSIONS = Path("sessions")
        ok_count = 0
        details = []
        portals = ["linkedin", "computrabajo", "laborum", "chiletrabajos",
                   "getonyboard", "trabajando", "infojobs"]
        for p in portals:
            r = check_session(p, str(SESSIONS / p))
            if r == SessionResult.OK:
                ok_count += 1
            details.append(f"{p}={r.value}")
        return ok_count > 0, f"{ok_count}/7 ok | " + ", ".join(details)
    results.append(check("Sesiones portales", check_sessions))

    # 4. Config usuario
    def check_user_config():
        from bot.config import USER_PROFILE
        required = ["full_name", "email", "cv_path"]
        missing = [k for k in required if not USER_PROFILE.get(k)]
        return len(missing) == 0, "OK" if not missing else f"Faltan: {missing}"
    results.append(check("Config usuario (.env)", check_user_config))

    # 5. CV existe en disco
    def check_cv():
        from bot.config import USER_PROFILE
        cv = USER_PROFILE.get("cv_path", "")
        exists = Path(cv).exists() if cv else False
        return exists, cv if exists else f"NO ENCONTRADO: {cv!r}"
    results.append(check("CV existe en disco", check_cv))

    # 6. gui_server importa sin errores
    def check_server_import():
        import importlib.util
        spec = importlib.util.spec_from_file_location("gui_server", "gui_server.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        has_endpoint = hasattr(mod, "api_import_all_sessions")
        return has_endpoint, "endpoint /api/import-all-sessions presente" if has_endpoint else "falta endpoint"
    results.append(check("gui_server.py importa OK", check_server_import))

    # Resumen
    n_pass = results.count(PASS)
    n_fail = results.count(FAIL)
    print(f"\n{'='*42}")
    print(f"  {n_pass} pasaron  |  {n_fail} fallaron")
    if n_fail == 0:
        print("  Bot listo para correr.\n")
    else:
        print("  Revisar los !! antes de iniciar el bot.\n")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

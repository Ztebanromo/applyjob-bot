"""
login_portals.py - Abre browser visible para login manual en cada portal.

Uso:
  python login_portals.py chiletrabajos linkedin laborum
  python login_portals.py --all
"""
import sys
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
SESSIONS_DIR = BASE_DIR / "sessions"

def main():
    from bot.engine import _ensure_login, SESSIONS_DIR as ENG_SESSIONS
    from bot.config import SITE_CONFIG

    args = sys.argv[1:]
    if not args:
        print("[LOGIN] Uso: python login_portals.py <portal1> <portal2> ... | --all")
        sys.exit(1)

    login_portals = [p for p, cfg in SITE_CONFIG.items() if cfg.get("requires_login")]

    if "--all" in args:
        portals = login_portals
    else:
        portals = [p for p in args if p in SITE_CONFIG]
        unknown = [p for p in args if p not in SITE_CONFIG and p != "--all"]
        if unknown:
            print(f"[LOGIN] Portales desconocidos ignorados: {unknown}")

    if not portals:
        print("[LOGIN] Sin portales validos para loguear.")
        sys.exit(0)

    print(f"\n[LOGIN] Iniciando login en {len(portals)} portal(es): {', '.join(portals)}")
    print("[LOGIN] Se abrira un browser por portal. Inicia sesion y el bot continuara.\n")

    results = {}
    skipped = []
    for portal in portals:
        session_dir = str(ENG_SESSIONS / portal)
        Path(session_dir).mkdir(exist_ok=True)

        # _ensure_login ya verifica cookies guardadas y solo abre el navegador
        # si hace falta — evitamos duplicar ese chequeo aquí.
        had_session = (Path(session_dir) / "playwright_state.json").exists()
        print(f"\n{'='*50}")
        print(f"[LOGIN] Portal: {portal.upper()}")

        ok = _ensure_login(portal, session_dir)
        results[portal] = ok
        if ok and had_session:
            skipped.append(portal)
            print(f"[LOGIN] {portal.upper()}: [YA LOGUEADO] — sesion ya guardada.")
        else:
            status = "[OK] guardado" if ok else "[TIMEOUT] no se detecto login"
            print(f"[LOGIN] {portal.upper()}: {status}")

    print(f"\n{'='*50}")
    print("[LOGIN] Resumen:")
    for portal, ok in results.items():
        if portal in skipped:
            mark = "[YA]"
        elif ok:
            mark = "[OK]"
        else:
            mark = "[--]"
        print(f"  {mark} {portal}")
    saved   = sum(1 for v in results.values() if v)
    need_login = [p for p in portals if p not in skipped]
    if skipped:
        print(f"\n[LOGIN] {len(skipped)} portal(es) ya tenian sesion activa: {', '.join(skipped)}")
    if need_login:
        nuevos = sum(1 for p in need_login if results.get(p))
        print(f"[LOGIN] {nuevos}/{len(need_login)} sesiones nuevas guardadas.")
    else:
        print("[LOGIN] Todos los portales ya tenian sesion. No se abrio ningun browser.")


if __name__ == "__main__":
    main()

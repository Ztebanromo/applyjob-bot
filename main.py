"""
Entry point CLI — ApplyJob Bot.

Comandos disponibles:
    python main.py --portal linkedin              Postular en LinkedIn
    python main.py --portal indeed --max 20       Con límite custom
    python main.py --portal computrabajo --dry-run  Navegar sin postular
    python main.py --portal linkedin --headless    Sin ventana de browser
    python main.py --list-portals                  Ver portales configurados
    python main.py --stats                         Ver estadísticas de DB
    python main.py --validate --portal linkedin    Validar config sin correr
    python main.py --purge --days 90               Limpiar registros viejos

Notas:
    - El primer run de portales con requires_login=True debe hacerse sin
      --headless para poder iniciar sesión manualmente.
    - Las sesiones se guardan en sessions/<portal>/ y persisten entre runs.
    - Los logs se guardan en logs/ (CSV diario + applyjob.log con rotación).
"""
import argparse
import sys
import io

# Forzar UTF-8 en stdout/stderr para evitar UnicodeEncodeError en terminales Windows (cp1252)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from bot.config import SITE_CONFIG
from bot.engine import run_bot, run_bot_multi_keywords, run_scan_pass, run_apply_queue, run_persistent_session
from bot.logger import configure_logging


def list_portals() -> None:
    """Imprime tabla de portales configurados."""
    print("\nPortales disponibles:\n")
    for name, cfg in SITE_CONFIG.items():
        tipo  = cfg.get("tipo_postulacion", "?")
        max_o = cfg.get("max_offers_per_run", "?")
        login = "requiere login" if cfg.get("requires_login") else "sin login"
        print(f"  {name:<18} tipo={tipo:<10} max={max_o:<5} {login}")
    print()


def show_stats() -> None:
    """Imprime estadísticas de postulaciones desde SQLite."""
    from bot.state import print_stats
    print_stats()


def run_validate(portal_name: str) -> None:
    """Valida la configuración sin lanzar el browser."""
    from bot.validator import run_startup_validation, ConfigError
    from bot.config import USER_PROFILE

    if portal_name not in SITE_CONFIG:
        print(f"\nPortal '{portal_name}' no encontrado.\n")
        list_portals()
        sys.exit(1)

    try:
        run_startup_validation(portal_name, USER_PROFILE, SITE_CONFIG[portal_name])
        print("\n✓ Configuración válida — puedes correr el bot.\n")
    except ConfigError as e:
        print(f"\n✗ Error de configuración:\n{e}\n")
        sys.exit(1)


def run_purge(days: int) -> None:
    """Elimina registros skipped/dry_run más viejos que N días."""
    from bot.state import purge_old
    deleted = purge_old(days=days)
    print(f"\nPurge: {deleted} registros eliminados (anteriores a {days} días).\n")


def main() -> None:
    # Configurar logging antes de cualquier import que use loggers
    configure_logging()

    parser = argparse.ArgumentParser(
        description="ApplyJob — Motor universal de postulaciones automáticas",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python main.py --portal linkedin
  python main.py --portal indeed --max 20 --dry-run
  python main.py --portal computrabajo --headless
  python main.py --validate --portal linkedin
  python main.py --stats
  python main.py --purge --days 90
  python main.py --list-portals
        """,
    )

    parser.add_argument("--portal", "-p",
        help="Nombre del portal (linkedin, indeed, computrabajo, getonyboard, chiletrabajos, laborum)")
    parser.add_argument("--max", "-m", type=int, default=None,
        help="Máximo de postulaciones (sobrescribe el config del portal)")
    parser.add_argument("--dry-run", action="store_true",
        help="Navega y loguea sin postular — útil para verificar selectores")
    parser.add_argument("--headless", action="store_true",
        help="Corre el browser sin ventana visible")
    parser.add_argument("--list-portals", action="store_true",
        help="Muestra los portales configurados y sale")
    parser.add_argument("--stats", action="store_true",
        help="Muestra estadísticas de postulaciones desde la DB")
    parser.add_argument("--validate", action="store_true",
        help="Valida USER_PROFILE y config del portal sin correr el bot")
    parser.add_argument("--purge", action="store_true",
        help="Elimina registros skipped/dry_run más viejos que --days días")
    parser.add_argument("--days", type=int, default=90,
        help="Días de retención para --purge (default: 90)")
    parser.add_argument("--setup", action="store_true",
        help="Inicia el asistente de configuración (Wizard) y extrae datos del CV")
    parser.add_argument("--run-all", action="store_true",
        help="Ejecuta el bot secuencialmente en todos los portales configurados")
    parser.add_argument("--multi-keyword", action="store_true",
        help="Busqueda atomica: lanza una busqueda separada por cada keyword del KEYWORD_GROUPS")
    parser.add_argument("--scan", action="store_true",
        help="Pasada 1: escanea ofertas y recolecta preguntas SIN postular. Usa con --portal.")
    parser.add_argument("--apply-queue", action="store_true",
        help="Pasada 2: aplica a ofertas en cola con preguntas ya respondidas. Usa con --portal.")
    parser.add_argument("--persistent", action="store_true",
        help="Cicla portales hasta que TODOS tengan ≥1 postulación. Usa con --portal o --run-all.")
    parser.add_argument("--min-per-portal", type=int, default=1,
        help="Mínimo de postulaciones por portal para parar en modo --persistent (default: 1)")

    args = parser.parse_args()

    # Comandos que no necesitan portal
    if args.setup:
        from bot.profile_manager import run_setup_wizard
        run_setup_wizard()
        sys.exit(0)

    if args.list_portals:
        list_portals()
        sys.exit(0)

    if args.stats:
        show_stats()
        sys.exit(0)

    if args.purge:
        run_purge(args.days)
        sys.exit(0)

    # Indeed excluido: bloqueado por Cloudflare Turnstile (requiere Chrome real + CDP)
    _ALL_PORTALS = ["laborum", "computrabajo", "chiletrabajos", "getonyboard", "linkedin"]

    if args.scan:
        portals = _ALL_PORTALS if not args.portal else [p.strip() for p in args.portal.split(",") if p.strip()]
        for p in portals:
            print(f"\n{'='*50}")
            print(f"[SCAN] Portal: {p.upper()}")
            print(f"{'='*50}")
            run_scan_pass(p, headless=args.headless)
        sys.exit(0)

    if args.apply_queue:
        portals = _ALL_PORTALS if not args.portal else [p.strip() for p in args.portal.split(",") if p.strip()]
        for p in portals:
            print(f"\n{'='*50}")
            print(f"[APPLY-QUEUE] Portal: {p.upper()}")
            print(f"{'='*50}")
            run_apply_queue(p, headless=args.headless)
        sys.exit(0)

    # Sobrescribir límites si hay variable de entorno (para el Dashboard)
    import os
    env_max = os.getenv("USER_MAX_OFFERS")
    if env_max and env_max.isdigit():
        for p in SITE_CONFIG:
            SITE_CONFIG[p]["max_offers_per_run"] = int(env_max)

    if args.portal and args.portal not in SITE_CONFIG and "," not in args.portal:
        list_portals()
        print(f"Error: portal '{args.portal}' no encontrado.\n")
        sys.exit(1)

    if args.validate and args.portal:
        run_validate(args.portal)
        sys.exit(0)

    if args.setup:
        # Aquí podrías llamar a una función de setup si existiera
        print("Setup no implementado en CLI. Usa el Dashboard.")
        sys.exit(0)

    # ── Ejecución de Portales (Modo Master o Individual) ──
    portal_list = []
    if args.run_all:
        portal_list = list(SITE_CONFIG.keys())
    elif args.portal:
        portal_list = [p.strip() for p in args.portal.split(",") if p.strip()]
    elif args.multi_keyword:
        # Sin --portal explícito en modo multi-keyword → usar todos los portales activos
        portal_list = list(_ALL_PORTALS)

    if portal_list:
        print(f"\n[SISTEMA] Iniciando ejecución para: {', '.join(portal_list).upper()}\n")
        total_global = 0

        if args.persistent:
            # ── Modo persistente: cicla portales hasta ≥min_per_portal en cada uno ──
            valid_portals = [p for p in portal_list if p in SITE_CONFIG]
            if args.max is not None:
                for p in valid_portals:
                    SITE_CONFIG[p]["max_offers_per_run"] = args.max
            result = run_persistent_session(
                portals         = valid_portals,
                dry_run         = args.dry_run,
                headless        = args.headless,
                min_per_portal  = args.min_per_portal,
            )
            total_global = sum(result.values())

        elif args.multi_keyword:
            # multi-keyword: cada portal abre su propio sync_playwright internamente
            for portal in portal_list:
                if portal not in SITE_CONFIG:
                    print(f"Error: portal '{portal}' no encontrado. Saltando.")
                    continue
                print(f"\n[PORTAL_ACTIVO] PORTAL: {portal}")
                try:
                    if args.max is not None:
                        SITE_CONFIG[portal]["max_offers_per_run"] = args.max
                    applied = run_bot_multi_keywords(
                        portal_name = portal,
                        dry_run     = args.dry_run,
                        headless    = args.headless,
                    )
                    total_global += (applied or 0)
                except Exception as e:
                    print(f"Error crítico en portal {portal}: {e}")
        else:
            # modo normal: un solo sync_playwright compartido entre portales
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                for portal in portal_list:
                    if portal not in SITE_CONFIG:
                        print(f"Error: portal '{portal}' no encontrado. Saltando.")
                        continue
                    print(f"\n[PORTAL_ACTIVO] PORTAL: {portal}")
                    try:
                        if args.max is not None:
                            SITE_CONFIG[portal]["max_offers_per_run"] = args.max
                        applied = run_bot(
                            portal_name = portal,
                            dry_run     = args.dry_run,
                            headless    = args.headless,
                            pw          = pw,
                        )
                        total_global += (applied or 0)
                    except Exception as e:
                        print(f"Error crítico en portal {portal}: {e}")

        print(f"\n[SISTEMA] Ejecución finalizada. Total global: {total_global}\n")
        sys.stdout.flush()
        sys.exit(0)

    parser.print_help()
    print("\nError: especifica --portal, usa --run-all o --setup\n")
    sys.exit(1)


if __name__ == "__main__":
    main()

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
from bot.engine import run_bot
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
        help="Nombre del portal (linkedin, indeed, computrabajo, getonyboard)")
    parser.add_argument("--max", "-m", type=int, default=None,
        help="Máximo de postulaciones (sobreescribe el config del portal)")
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

    args = parser.parse_args()

    # Comandos que no necesitan portal
    if args.list_portals:
        list_portals()
        sys.exit(0)

    if args.stats:
        show_stats()
        sys.exit(0)

    if args.purge:
        run_purge(args.days)
        sys.exit(0)

    # Comandos que sí necesitan portal
    if not args.portal:
        parser.print_help()
        print("\nError: especifica --portal o usa --list-portals\n")
        sys.exit(1)

    if args.portal not in SITE_CONFIG:
        list_portals()
        print(f"Error: portal '{args.portal}' no encontrado.\n")
        sys.exit(1)

    if args.validate:
        run_validate(args.portal)
        sys.exit(0)

    if args.max is not None:
        SITE_CONFIG[args.portal]["max_offers_per_run"] = args.max

    run_bot(
        portal_name = args.portal,
        dry_run     = args.dry_run,
        headless    = args.headless,
    )


if __name__ == "__main__":
    main()

"""
Entry point CLI del bot de postulaciones.

Uso:
    python main.py --portal linkedin
    python main.py --portal indeed --max 20
    python main.py --portal computrabajo --dry-run
    python main.py --portal linkedin --headless
    python main.py --list-portals
    python main.py --stats
"""
import argparse
import logging
import sys

from bot.config import SITE_CONFIG
from bot.engine import run_bot

# Configuración de logging centralizada aquí, no en los módulos internos
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)


def list_portals() -> None:
    print("\nPortales disponibles:\n")
    for name, cfg in SITE_CONFIG.items():
        tipo   = cfg.get("tipo_postulacion", "?")
        max_o  = cfg.get("max_offers_per_run", "?")
        login  = "requiere login" if cfg.get("requires_login") else "sin login"
        print(f"  {name:<18} tipo={tipo:<10} max={max_o:<5} {login}")
    print()


def show_stats() -> None:
    from bot.state import print_stats
    print_stats()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ApplyJob — Motor universal de postulaciones automáticas",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python main.py --portal linkedin
  python main.py --portal indeed --max 20 --dry-run
  python main.py --portal computrabajo --headless
  python main.py --list-portals
  python main.py --stats
        """,
    )
    parser.add_argument("--portal", "-p",
        help="Nombre del portal (linkedin, indeed, computrabajo, getonyboard)")
    parser.add_argument("--max", "-m", type=int, default=None,
        help="Máximo de postulaciones (sobreescribe el config)")
    parser.add_argument("--dry-run", action="store_true",
        help="Navega sin postular — útil para verificar selectores")
    parser.add_argument("--headless", action="store_true",
        help="Corre el browser sin ventana visible")
    parser.add_argument("--list-portals", action="store_true",
        help="Muestra los portales configurados")
    parser.add_argument("--stats", action="store_true",
        help="Muestra estadísticas de postulaciones desde la DB")

    args = parser.parse_args()

    if args.list_portals:
        list_portals()
        sys.exit(0)

    if args.stats:
        show_stats()
        sys.exit(0)

    if not args.portal:
        parser.print_help()
        print("\nError: debes especificar --portal o usar --list-portals\n")
        sys.exit(1)

    if args.portal not in SITE_CONFIG:
        list_portals()
        print(f"Error: portal '{args.portal}' no encontrado.\n")
        sys.exit(1)

    if args.max is not None:
        SITE_CONFIG[args.portal]["max_offers_per_run"] = args.max

    run_bot(
        portal_name=args.portal,
        dry_run=args.dry_run,
        headless=args.headless,
    )


if __name__ == "__main__":
    main()

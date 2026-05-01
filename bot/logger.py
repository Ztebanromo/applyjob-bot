"""
Logging centralizado con rotación diaria de archivos.

Cómo funciona:
- Consola: INFO en adelante, formato corto con hora
- Archivo  (logs/applyjob_YYYY-MM-DD.log): DEBUG en adelante, formato completo
- Rotación: un archivo por día, retención de 30 días
- Un solo punto de setup: llamar configure_logging() desde main.py

Uso en cualquier módulo:
    import logging
    log = logging.getLogger("applyjob.mi_modulo")
    log.info("mensaje")
    log.error("error", exc_info=True)   # incluye traceback completo
"""
import logging
import logging.handlers
from pathlib import Path

LOGS_DIR = Path(__file__).parent.parent / "logs"

# Formatos
FMT_CONSOLE = "%(asctime)s [%(levelname)s] %(message)s"
FMT_FILE    = "%(asctime)s [%(levelname)-8s] %(name)s — %(message)s"
DATE_SHORT  = "%H:%M:%S"
DATE_FULL   = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: int = logging.INFO) -> None:
    """
    Configura el sistema de logging global. Llamar UNA sola vez desde main.py.

    Args:
        level: nivel mínimo para consola (logging.DEBUG / INFO / WARNING)

    Crea:
        logs/applyjob_YYYY-MM-DD.log  — rotación diaria, 30 días de retención
    """
    LOGS_DIR.mkdir(exist_ok=True)

    root = logging.getLogger("applyjob")
    root.setLevel(logging.DEBUG)        # captura todo; los handlers filtran

    # ── Handler de consola ────────────────────────────────────────────────────
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(logging.Formatter(FMT_CONSOLE, datefmt=DATE_SHORT))

    # ── Handler de archivo con rotación diaria ───────────────────────────────
    log_file = LOGS_DIR / "applyjob.log"
    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename   = str(log_file),
        when       = "midnight",        # rotar a medianoche
        interval   = 1,                 # cada 1 día
        backupCount= 30,                # conservar últimos 30 días
        encoding   = "utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(FMT_FILE, datefmt=DATE_FULL))
    file_handler.suffix = "%Y-%m-%d"    # nombre: applyjob.log.2024-05-15

    root.addHandler(console)
    root.addHandler(file_handler)
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    """
    Retorna un logger con namespace 'applyjob.{name}'.
    Hereda la configuración de configure_logging().

    Args:
        name: nombre del módulo (ej. "engine", "linkedin")

    Returns:
        Logger listo para usar
    """
    return logging.getLogger(f"applyjob.{name}")

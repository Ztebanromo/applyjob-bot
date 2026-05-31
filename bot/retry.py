"""
Retry logic y rate limiting para el motor de postulaciones.

Componentes:
  - with_retry(fn, attempts, delay): reintenta una función ante errores transitorios
  - RateLimiter: controla el máximo de acciones por ventana de tiempo
  - is_transient_error(e): clasifica si un error merece reintento

Por qué importa:
  - Los errores de red (timeout, connection reset) son transitorios: 1 reintento los resuelve
  - LinkedIn detecta ráfagas de postulaciones: >10/hora aumenta riesgo de ban
  - Sin rate limiting el bot puede postular 50 empleos en 5 minutos y ser bloqueado
"""
import time
import logging
import functools
from collections import deque
from datetime import datetime, timedelta
from playwright.sync_api import TimeoutError as PlaywrightTimeout

log = logging.getLogger("applyjob.retry")


# ---------------------------------------------------------------------------
# Excepción de rate limit — permite saltar al siguiente portal
# ---------------------------------------------------------------------------
class RateLimitExceeded(Exception):
    """
    Raised cuando un portal alcanza su límite de acciones por hora.
    En lugar de esperar bloqueado, el motor puede capturar esta excepción
    y pasar directamente al siguiente portal.
    """
    def __init__(self, portal: str, wait_secs: float):
        self.portal   = portal
        self.wait_secs = wait_secs
        super().__init__(
            f"{portal}: rate limit alcanzado — {wait_secs:.0f}s para liberar ventana"
        )


# ---------------------------------------------------------------------------
# Clasificación de errores
# ---------------------------------------------------------------------------
TRANSIENT_KEYWORDS = (
    "timeout", "connection", "network", "reset", "refused",
    "eof", "timed out", "net::", "aborted", "err_aborted",
    "target page", "target closed", "browser has been closed",
)


def is_transient_error(exc: Exception) -> bool:
    """
    Retorna True si el error es probablemente transitorio (red, timeout)
    y merece un reintento.

    Errores permanentes (selector not found, page error) retornan False.

    Args:
        exc: excepción capturada

    Returns:
        bool
    """
    if isinstance(exc, PlaywrightTimeout):
        return True
    msg = str(exc).lower()
    return any(kw in msg for kw in TRANSIENT_KEYWORDS)


# ---------------------------------------------------------------------------
# Retry decorator / función
# ---------------------------------------------------------------------------
def with_retry(fn, attempts: int = 2, delay: float = 5.0, portal: str = ""):
    """
    Ejecuta fn() con hasta `attempts` intentos ante errores transitorios.

    Uso directo:
        result = with_retry(lambda: page.goto(url), attempts=2, delay=5.0)

    Args:
        fn       : callable sin argumentos (usar lambda para pasar args)
        attempts : número máximo de intentos (default: 2)
        delay    : segundos de espera entre intentos (default: 5.0)
        portal   : nombre del portal para el log

    Returns:
        El valor de retorno de fn() si tiene éxito

    Raises:
        El último error si todos los intentos fallan
    """
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if not is_transient_error(exc):
                log.debug("[%s] Error no transitorio, sin reintento: %s", portal, exc)
                raise
            if attempt < attempts:
                log.warning(
                    "[%s] Intento %d/%d falló (%s). Reintentando en %.0fs…",
                    portal, attempt, attempts, type(exc).__name__, delay,
                )
                time.sleep(delay)
            else:
                log.error(
                    "[%s] Todos los intentos agotados (%d/%d). Último error: %s",
                    portal, attempt, attempts, exc,
                )
    raise last_exc


def retryable(attempts: int = 2, delay: float = 5.0):
    """
    Decorador para marcar una función como reintentable.

    Uso:
        @retryable(attempts=2, delay=5.0)
        def fetch_page(url):
            ...
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return with_retry(lambda: fn(*args, **kwargs), attempts=attempts, delay=delay)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Rate Limiter
# ---------------------------------------------------------------------------
class RateLimiter:
    """
    Controla el ritmo de acciones para evitar detección.

    Implementa una ventana deslizante: registra los timestamps de cada acción
    y bloquea cuando se supera el límite dentro de la ventana.

    Ejemplo:
        limiter = RateLimiter(max_actions=10, window_minutes=60)
        limiter.acquire("linkedin")   # bloquea si ya hubo 10 en la última hora

    Args:
        max_actions    : número máximo de acciones en la ventana
        window_minutes : tamaño de la ventana en minutos
    """

    def __init__(self, max_actions: int = 10, window_minutes: int = 60):
        self.max_actions     = max_actions
        self.window          = timedelta(minutes=window_minutes)
        self._timestamps: deque = deque()

    def acquire(self, portal: str = "", skip_on_limit: bool = True) -> None:
        """
        Registra una acción. Si se alcanzó el límite:
          - skip_on_limit=True  (default): lanza RateLimitExceeded → motor salta al siguiente portal
          - skip_on_limit=False           : bloquea con sleep hasta que haya capacidad (comportamiento antiguo)

        Args:
            portal        : nombre del portal para el log
            skip_on_limit : si True, lanza excepción en vez de dormir
        """
        now = datetime.now()

        # Eliminar timestamps fuera de la ventana
        while self._timestamps and now - self._timestamps[0] > self.window:
            self._timestamps.popleft()

        if len(self._timestamps) >= self.max_actions:
            oldest     = self._timestamps[0]
            wait_until = oldest + self.window
            wait_secs  = (wait_until - now).total_seconds() + 1.0

            log.warning(
                "[%s] Rate limit: %d/%d acciones en la última hora. "
                "%.0f segundos para liberar ventana.",
                portal, len(self._timestamps), self.max_actions, wait_secs,
            )

            if skip_on_limit:
                # Lanzar excepción — el motor captura esto y salta al siguiente portal
                print(f"\n[RATE_LIMIT] {portal.upper()}: límite {self.max_actions}/hora alcanzado "
                      f"({wait_secs/60:.0f} min para liberar). Pasando al siguiente portal...")
                raise RateLimitExceeded(portal, wait_secs)
            else:
                # Comportamiento antiguo: esperar bloqueado
                print(f"\n[RATE_LIMIT] {portal.upper()}: esperando {wait_secs:.0f}s...")
                time.sleep(wait_secs)
                now = datetime.now()
                while self._timestamps and now - self._timestamps[0] > self.window:
                    self._timestamps.popleft()

        self._timestamps.append(datetime.now())

    @property
    def current_count(self) -> int:
        """Acciones realizadas en la ventana actual."""
        now = datetime.now()
        return sum(1 for ts in self._timestamps if now - ts <= self.window)

    @property
    def remaining(self) -> int:
        """Acciones restantes antes de llegar al límite."""
        return max(0, self.max_actions - self.current_count)


# ---------------------------------------------------------------------------
# Rate limiters por portal (configurados con valores seguros)
# ---------------------------------------------------------------------------
RATE_LIMITS: dict[str, RateLimiter] = {
    "linkedin":      RateLimiter(max_actions=20, window_minutes=60),
    "indeed":        RateLimiter(max_actions=30, window_minutes=60),  # solo cuenta applies reales
    "computrabajo":  RateLimiter(max_actions=40, window_minutes=60),
    "laborum":       RateLimiter(max_actions=30, window_minutes=60),
    "chiletrabajos": RateLimiter(max_actions=40, window_minutes=60),
    "getonyboard":   RateLimiter(max_actions=30, window_minutes=60),
    "_default":      RateLimiter(max_actions=30, window_minutes=60),
}


def get_rate_limiter(portal: str) -> RateLimiter:
    """
    Retorna el rate limiter correspondiente al portal.
    Si no existe configuración específica, retorna el default.

    Args:
        portal: nombre del portal

    Returns:
        RateLimiter configurado
    """
    return RATE_LIMITS.get(portal, RATE_LIMITS["_default"])

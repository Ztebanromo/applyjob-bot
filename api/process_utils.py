"""api/process_utils.py — Utilidades de proceso: rate limiting, señal de stop,
limpieza de procesos Chromium hijos y timeouts de subprocesos del bot."""
from __future__ import annotations

import collections
import logging
import os
import threading
import time

_log = logging.getLogger("applyjob.server")

# ── Archivo señal de parada (compartido con engine.py) ────────────────────────
# gui_server escribe este archivo; engine.py lo detecta y cierra el browser limpio
_BASE_DIR        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR        = os.path.join(_BASE_DIR, "data")
STOP_SIGNAL_PATH = os.path.join(_DATA_DIR, "STOP_SIGNAL")
os.makedirs(_DATA_DIR, exist_ok=True)


def _write_stop_signal():
    """Escribe el archivo señal para que engine.py cierre el browser limpiamente."""
    try:
        with open(STOP_SIGNAL_PATH, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


def _clear_stop_signal():
    """Elimina el archivo señal tras confirmar que el proceso murió."""
    try:
        if os.path.exists(STOP_SIGNAL_PATH):
            os.remove(STOP_SIGNAL_PATH)
    except Exception:
        pass


# Max seconds a bot subprocess may run before the watchdog kills it
# Scan / apply-queue: 600s es suficiente (pocas ofertas, < 10 min)
# Master (--persistent / --multi-keyword): necesita horas → 7200s = 2 h
PORTAL_TIMEOUT_S = int(os.getenv("PORTAL_TIMEOUT_S", "600"))   # scan / cola
MASTER_TIMEOUT_S = int(os.getenv("MASTER_TIMEOUT_S", "7200"))  # run maestro (2h)


# ── Rate limiter ──────────────────────────────────────────────────────────────
class _RateLimiter:
    """
    Sliding-window rate limiter. Thread-safe.
    Raises RuntimeError if more than max_calls happen within window_s seconds.
    """
    def __init__(self, max_calls: int, window_s: float):
        self._max   = max_calls
        self._win   = window_s
        self._calls: collections.deque = collections.deque()
        self._lock  = threading.Lock()

    def check(self) -> None:
        now = time.time()
        with self._lock:
            while self._calls and now - self._calls[0] > self._win:
                self._calls.popleft()
            if len(self._calls) >= self._max:
                raise RuntimeError(
                    f"Rate limit: max {self._max} requests per {int(self._win)}s exceeded"
                )
            self._calls.append(now)


def _kill_chromium_children(pid: int) -> None:
    """Mata todos los procesos Chromium hijos de pid usando psutil (si está disponible)."""
    try:
        import psutil
        parent = psutil.Process(pid)
        for child in parent.children(recursive=True):
            try:
                if "chrom" in child.name().lower():
                    child.kill()
                    _log.debug("[CLEANUP] Chromium hijo PID %d matado", child.pid)
            except Exception:
                pass
    except ImportError:
        pass   # psutil no instalado — omitir silenciosamente
    except Exception as e:
        _log.debug("[CLEANUP] Error limpiando hijos: %s", e)

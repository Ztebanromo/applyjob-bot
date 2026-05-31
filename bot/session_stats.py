"""
Estadísticas por sesión de postulación.

Guarda un registro histórico en data/sessions.json con:
  - ID de sesión (timestamp)
  - Portal, keywords probados, aplicados, encontrados
  - Motivo de fin (rate_limit, completado, timeout, stopped)

Permite analizar qué portales y keywords funcionan mejor.
"""
import json
import logging
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("applyjob.session_stats")

_SESSIONS_PATH = Path(__file__).parent.parent / "data" / "sessions.json"
_STATS_PATH    = Path(__file__).parent.parent / "data" / "keyword_stats.json"


# ---------------------------------------------------------------------------
# Helpers de I/O
# ---------------------------------------------------------------------------
def _load_sessions() -> list:
    if not _SESSIONS_PATH.exists():
        return []
    try:
        with open(_SESSIONS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_sessions(sessions: list) -> None:
    _SESSIONS_PATH.parent.mkdir(exist_ok=True)
    with open(_SESSIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(sessions, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Clase principal
# ---------------------------------------------------------------------------
class SessionTracker:
    """
    Acumula estadísticas durante un run y las persiste al finalizar.

    Uso típico en run_bot_multi_keywords:
        tracker = SessionTracker()
        tracker.start(portals)
        ...
        tracker.record_portal(portal, applied, found, keywords, end_reason)
        ...
        tracker.finish()
        tracker.print_summary()
    """

    def __init__(self):
        self._session_id  = datetime.now().strftime("%Y-%m-%d_%H:%M")
        self._start_ts    = time.time()
        self._portals: dict = {}   # portal -> {applied, found, keywords, end_reason}

    def start(self, portals: list) -> None:
        """Inicializa entradas vacías para todos los portales del run."""
        for p in portals:
            self._portals[p] = {
                "applied":    0,
                "found":      0,
                "keywords":   0,
                "end_reason": "pending",
            }

    def record_portal(
        self,
        portal: str,
        applied: int,
        found: int,
        keywords_tried: int,
        end_reason: str = "completed",
    ) -> None:
        """
        Registra los resultados de un portal.

        end_reason: 'completed' | 'rate_limit' | 'login_timeout' | 'stopped' | 'error'
        """
        self._portals[portal] = {
            "applied":    applied,
            "found":      found,
            "keywords":   keywords_tried,
            "end_reason": end_reason,
        }

    def finish(self) -> dict:
        """Persiste la sesión y retorna el dict guardado."""
        duration_s  = int(time.time() - self._start_ts)
        total_applied = sum(p["applied"] for p in self._portals.values())
        total_found   = sum(p["found"]   for p in self._portals.values())

        session = {
            "session_id":     self._session_id,
            "date":           datetime.now().strftime("%Y-%m-%d"),
            "start":          datetime.fromtimestamp(self._start_ts).isoformat(timespec="seconds"),
            "end":            datetime.now().isoformat(timespec="seconds"),
            "duration_min":   round(duration_s / 60, 1),
            "total_applied":  total_applied,
            "total_found":    total_found,
            "portals":        self._portals,
        }

        sessions = _load_sessions()
        sessions.append(session)
        # Conservar solo las últimas 50 sesiones
        if len(sessions) > 50:
            sessions = sessions[-50:]
        _save_sessions(sessions)
        log.info("[SESSION] Guardada: %s | %d aplicadas", self._session_id, total_applied)
        return session

    def print_summary(self, session: dict) -> None:
        """Imprime resumen de la sesión en consola."""
        print("\n" + "="*60)
        print(f"  RESUMEN SESION {session['session_id']}")
        print(f"  Duracion: {session['duration_min']} min | "
              f"Total aplicadas: {session['total_applied']}")
        print("="*60)
        print(f"  {'PORTAL':<16} {'APLICADAS':>9} {'ENCONTRADAS':>12} {'KEYWORDS':>9} {'FIN'}")
        print(f"  {'-'*60}")
        for portal, data in session["portals"].items():
            reason = {
                "completed":     "OK",
                "rate_limit":    "RATE LIMIT",
                "login_timeout": "LOGIN TIMEOUT",
                "stopped":       "DETENIDO",
                "error":         "ERROR",
                "pending":       "NO EJECUTADO",
            }.get(data["end_reason"], data["end_reason"])
            applied_str = str(data["applied"]) if data["applied"] > 0 else "0"
            print(f"  {portal:<16} {applied_str:>9} {data['found']:>12} "
                  f"{data['keywords']:>9} {reason}")
        print("="*60)

        # Top keywords
        self._print_top_keywords()

    def _print_top_keywords(self) -> None:
        """Muestra los top 5 keywords por portal basado en keyword_stats.json."""
        try:
            if not _STATS_PATH.exists():
                return
            with open(_STATS_PATH, encoding="utf-8") as f:
                stats = json.load(f)

            print("\n  TOP KEYWORDS POR PORTAL (historico):")
            print(f"  {'-'*55}")

            portals_in_run = list(self._portals.keys())
            for portal in portals_in_run:
                # Recopilar todos los keywords con datos de este portal
                kw_data = []
                for kw, info in stats.items():
                    p = info.get("portals", {}).get(portal, {})
                    if p and p.get("runs", 0) > 0:
                        rate = p.get("applied", 0) / max(p.get("runs", 1), 1)
                        kw_data.append((kw, p.get("applied", 0), p.get("found", 0),
                                        p.get("runs", 0), rate))

                if not kw_data:
                    continue

                # Ordenar por: aplicadas DESC, tasa DESC, encontradas DESC
                kw_data.sort(key=lambda x: (x[1], x[4], x[2]), reverse=True)

                print(f"\n  [{portal.upper()}] Top keywords:")
                for kw, applied, found, runs, rate in kw_data[:5]:
                    stars = "*" * min(int(rate * 5 + 0.5), 5)
                    print(f"    {kw[:35]:<35} {applied:>3} apl | {found:>3} enc | "
                          f"{runs:>2} runs | {stars}")

                # Portales con 0 aplicaciones — mostrar advertencia
                if all(x[1] == 0 for x in kw_data):
                    print(f"    ⚠  Ningún keyword consiguió postulaciones en {portal.upper()}.")
                    print(f"       Revisar: ¿requiere login? ¿filtros demasiado estrictos?")

        except Exception as e:
            log.debug("[SESSION] Error leyendo stats para top keywords: %s", e)


# ---------------------------------------------------------------------------
# Función de análisis standalone — imprime sin instanciar tracker
# ---------------------------------------------------------------------------
def print_top_keywords_all_portals(top_n: int = 10) -> None:
    """
    Muestra los mejores keywords globales ordenados por postulaciones.
    Útil para decidir qué keywords priorizar.
    """
    if not _STATS_PATH.exists():
        print("No hay datos en keyword_stats.json todavía.")
        return

    with open(_STATS_PATH, encoding="utf-8") as f:
        stats = json.load(f)

    # Agrupar por portal
    by_portal: dict = {}
    for kw, info in stats.items():
        for portal, pdata in info.get("portals", {}).items():
            if pdata.get("runs", 0) == 0:
                continue
            by_portal.setdefault(portal, []).append({
                "keyword": kw,
                "applied": pdata.get("applied", 0),
                "found":   pdata.get("found", 0),
                "runs":    pdata.get("runs", 0),
                "status":  pdata.get("status", "active"),
                "rate":    pdata.get("applied", 0) / max(pdata.get("runs", 1), 1),
            })

    print("\n" + "="*65)
    print("  ANALISIS DE KEYWORDS — TOP PERFORMERS POR PORTAL")
    print("="*65)

    for portal in sorted(by_portal.keys()):
        entries = sorted(by_portal[portal],
                         key=lambda x: (x["applied"], x["rate"], x["found"]),
                         reverse=True)

        print(f"\n  [{portal.upper()}] — {len(entries)} keywords con datos")
        print(f"  {'KEYWORD':<36} {'APL':>4} {'ENC':>4} {'RUNS':>5} {'TASA':>6} {'ESTADO'}")
        print(f"  {'-'*65}")

        shown = 0
        for e in entries:
            if shown >= top_n:
                break
            tasa = f"{e['rate']:.2f}"
            estado = "activo" if e["status"] == "active" else "retirado"
            print(f"  {e['keyword'][:35]:<36} {e['applied']:>4} {e['found']:>4} "
                  f"{e['runs']:>5} {tasa:>6} {estado}")
            shown += 1

        zero = [e for e in entries if e["applied"] == 0]
        if zero:
            print(f"  ... {len(zero)} keywords con 0 postulaciones")

    # Últimas sesiones
    sessions = _load_sessions()
    if sessions:
        print("\n" + "="*65)
        print("  ULTIMAS SESIONES")
        print(f"  {'FECHA':<20} {'DUR':>6} {'APL':>5} {'ENC':>5} {'PORTALES'}")
        print(f"  {'-'*65}")
        for s in sessions[-10:]:
            portales_ok = [p for p, d in s.get("portals", {}).items()
                           if d.get("applied", 0) > 0]
            portales_str = ", ".join(portales_ok) if portales_ok else "ninguno"
            print(f"  {s['session_id']:<20} {s.get('duration_min',0):>5}m "
                  f"{s['total_applied']:>5} {s.get('total_found',0):>5}  {portales_str}")
    print("="*65)

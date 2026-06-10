"""
bot/verification.py — Comprobar que las postulaciones aparecen en la sección "Mis postulaciones".

Funciones principales:
  - verify_all_postulations(pw, portals=None)

Estrategia:
  - Lee los CSVs diarios para obtener URLs aplicadas por portal.
  - Conecta al Chrome via CDP (CDPTabBackend) y abre la sección de postulaciones
    buscando enlaces con texto conocido ("Mis postulaciones", "Ir a mis postulaciones").
  - Extrae los links visibles y compara con el CSV para reportar faltantes.
"""
from __future__ import annotations

import csv
import datetime
import logging
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright
from .browser_discovery import select_browser_backend
from .browser_backend import CDP_URL

log = logging.getLogger("applyjob.verification")

LOGS_DIR = Path(__file__).parent.parent / "logs"
SESSION_DIR = Path(__file__).parent.parent / "sessions"


# Estados que representan una postulación REAL enviada (no un "skip"/filtro).
# "applied"        → formulario nativo enviado
# "external_apply" → redirigido a ATS externo y postuló ahí
# Cualquier "skipped_*" (no_apply, practica, experience, ats, ...) NO cuenta:
# el bot decidió no postular, así que jamás debería aparecer en "Mis postulaciones".
_REAL_APPLY_STATUSES = {"applied", "external_apply"}


def _read_today_applied() -> dict[str, dict[str, dict[str, str]]]:
    """
    Retorna {portal: {url_normalizada: {"title":..., "status":...}}} solo
    para filas con un estado de postulación REAL (ver _REAL_APPLY_STATUSES).

    Se separa por status porque "applied" (formulario nativo del portal) y
    "external_apply" (redirigido a un ATS externo) tienen rutas de
    verificación distintas:
      - "applied"        → DEBE aparecer en "Mis postulaciones" del portal.
      - "external_apply" → la postulación real ocurrió en el ATS externo;
        el portal de origen normalmente NO la registra en su propia sección
        de postulaciones, así que no debe contarse como "faltante" ahí.
    """
    today = datetime.date.today().isoformat()
    file = LOGS_DIR / f"applied_{today}.csv"
    result: dict[str, dict[str, dict[str, str]]] = {}
    if not file.exists():
        return result
    try:
        with open(file, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                portal = (r.get("portal") or "").lower()
                url = r.get("url") or ""
                title = (r.get("title") or "").strip()
                status = (r.get("status") or "").lower()
                if portal and url and status in _REAL_APPLY_STATUSES:
                    result.setdefault(portal, {})[_normalize(url)] = {"title": title, "status": status}
    except Exception as exc:
        log.warning("_read_today_applied error: %s", exc)
    return result


def get_all_time_total(portals: list[str] | None = None) -> dict:
    """
    Suma todas las postulaciones REALES (applied + external_apply) registradas
    en logs/applied_*.csv desde el inicio — histórico completo, no solo hoy.

    Retorna {"total": int, "by_portal": {portal: int}}.
    """
    by_portal: dict[str, int] = {}
    if not LOGS_DIR.exists():
        return {"total": 0, "by_portal": {}}
    portals_set = {p.lower() for p in portals} if portals else None
    for file in sorted(LOGS_DIR.glob("applied_*.csv")):
        try:
            with open(file, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    portal = (r.get("portal") or "").lower()
                    status = (r.get("status") or "").lower()
                    if not portal or status not in _REAL_APPLY_STATUSES:
                        continue
                    if portals_set is not None and portal not in portals_set:
                        continue
                    by_portal[portal] = by_portal.get(portal, 0) + 1
        except Exception as exc:
            log.warning("get_all_time_total error reading %s: %s", file, exc)
    return {"total": sum(by_portal.values()), "by_portal": by_portal}


def _norm_text(s: str) -> str:
    """minúsculas + colapsar espacios, para comparar títulos contra innerText de la página."""
    return " ".join((s or "").lower().split())


def _normalize(url: str) -> str:
    try:
        p = urlparse(url)
        return f"{p.netloc.lower()}{p.path.rstrip('/')}"
    except Exception:
        return url


def verify_all_postulations(portals: list[str] | None = None, headless: bool = False) -> dict:
    """
    Verifica para cada portal de la lista si las URLs aplicadas hoy aparecen
    en la sección de "Mis postulaciones" del portal.
    Retorna un dict resumen por portal.
    """
    applied = _read_today_applied()
    portals = portals or list(applied.keys())
    summary = {}

    with sync_playwright() as pw:
        for portal in portals:
            _all_entries = applied.get(portal, {})          # {url_norm: {"title":..,"status":..}}
            # Solo las "applied" (formulario nativo) deben verificarse en
            # "Mis postulaciones" del propio portal.
            applied_entries = {u: e["title"] for u, e in _all_entries.items() if e["status"] == "applied"}
            applied_urls = set(applied_entries.keys())
            external_entries = {u: e["title"] for u, e in _all_entries.items() if e["status"] == "external_apply"}
            session_dir = SESSION_DIR / portal
            backend = select_browser_backend(pw, session_dir, headless=headless, portal_name=portal)
            if backend is None:
                summary[portal] = {"status": "no_backend"}
                continue
            page = backend.new_page()
            if page is None:
                summary[portal] = {"status": "no_page"}
                continue

            found = set()
            try:
                # ── Pre-chequeo de login ──────────────────────────────────
                # El backend CDP usa el perfil COMPARTIDO del bot — puede no
                # estar logueado en este portal en este momento (sesión
                # distinta a la de sessions/<portal>/playwright_state.json
                # que solo se usa para la badge del dashboard). Si no hay
                # sesión activa, "Mis postulaciones" redirige al home/login y
                # el scraping de anchors da 0 — eso NO significa que falten
                # postulaciones, significa que no pudimos verificar. Reportar
                # eso explícitamente evita falsos "missing".
                from .session_checker import is_logged_in_on_page
                from .session_config import VERIFY_URLS as _VERIFY_URLS
                _home = _VERIFY_URLS.get(portal, "")
                if _home:
                    try:
                        page.goto(_home, wait_until="domcontentloaded", timeout=12_000)
                        page.wait_for_timeout(2_500)
                    except Exception:
                        pass
                if not is_logged_in_on_page(page, portal):
                    summary[portal] = {
                        "status": "not_logged_in",
                        "applied_today": len(applied_urls),
                        "found_on_site": 0,
                        "missing": [],
                        "note": "No se pudo verificar: el navegador del bot no está logueado en este portal ahora mismo.",
                    }
                    try:
                        backend.close()
                    except Exception:
                        pass
                    continue

                # Intentar encontrar enlace directo por texto
                selectors = [
                    "a:has-text('Ir a mis postulaciones')",
                    "a:has-text('Mis postulaciones')",
                    "a:has-text('Mis postulaciones')",
                    "a[href*='postulaciones']",
                    "a[href*='applications']",
                    "a[href*='candidate/applications']",
                    "a[href*='dashboard']",
                ]
                clicked = False
                for sel in selectors:
                    try:
                        el = page.query_selector(sel)
                        if el:
                            el.click()
                            page.wait_for_load_state("domcontentloaded", timeout=8_000)
                            clicked = True
                            break
                    except Exception:
                        continue

                if not clicked:
                    # Fallback: navegar a VERIFY_URLS home y buscar anchors en la página
                    home = _VERIFY_URLS.get(portal)
                    if home:
                        page.goto(home, wait_until="domcontentloaded", timeout=8_000)

                page.wait_for_timeout(3_000)
                # Forzar carga de listas con scroll-lazy (React/SPA)
                for _ in range(3):
                    try:
                        page.mouse.wheel(0, 2_000)
                        page.wait_for_timeout(1_000)
                    except Exception:
                        break

                # Estrategia 1 (principal): comparar título de cada postulación
                # contra el texto visible de la página. La mayoría de los
                # portales (laborum, computrabajo, etc.) renderizan tarjetas
                # de "Mis postulaciones" como SPA sin <a href> hacia la oferta
                # original — el href nunca va a calzar, pero el título sí
                # aparece en pantalla.
                page_text = _norm_text(page.evaluate("document.body.innerText") or "")
                for url_norm, title in applied_entries.items():
                    title_n = _norm_text(title)
                    if title_n and len(title_n) >= 6 and title_n in page_text:
                        found.add(url_norm)

                # Estrategia 2 (bonus): href directo, por si el portal sí
                # enlaza a la oferta original desde la tarjeta.
                anchors = page.query_selector_all("a[href]")
                for a in anchors:
                    try:
                        href = a.get_attribute("href") or ""
                        if not href:
                            continue
                        norm = _normalize(href)
                        if norm in applied_urls:
                            found.add(norm)
                    except Exception:
                        pass

            except Exception as exc:
                log.warning("verify error for %s: %s", portal, exc)

            missing = applied_urls - found
            summary[portal] = {
                "applied_today": len(applied_urls),
                "found_on_site": len(found),
                "missing": [
                    f"{applied_entries.get(u, '')} ({u})" for u in missing
                ],
                "external_apply_today": len(external_entries),
                "external_apply_note": (
                    "Postuladas vía ATS externo — no se esperan en \"Mis postulaciones\" "
                    "de este portal; se verifican en el sitio del ATS, no aquí."
                    if external_entries else None
                ),
            }
            # limpiar pestaña
            try:
                backend.close()
            except Exception:
                pass

    return summary

"""
import_cookies.py — Importa cookies de Indeed desde Cookie-Editor al perfil del bot.

Uso:
  1. Exporta cookies de cl.indeed.com con Cookie-Editor → Export as JSON
  2. Guarda el archivo como indeed_cookies.json en esta misma carpeta
  3. Ejecuta: python import_cookies.py
"""
import json
import os
import sys
from pathlib import Path

COOKIES_FILE = Path(__file__).parent / "indeed_cookies.json"
SESSION_DIR  = Path(__file__).parent / "sessions" / "indeed"


def fix_cookie(c: dict) -> dict:
    """Normaliza campos al formato que Playwright espera."""
    # Dominio: Playwright quiere .indeed.com (con punto)
    domain = c.get("domain", c.get("host", ""))
    if "indeed.com" in domain and not domain.startswith("."):
        domain = ".indeed.com"
    elif not domain:
        domain = ".indeed.com"

    fixed = {
        "name":     c.get("name", ""),
        "value":    c.get("value", ""),
        "domain":   domain,
        "path":     c.get("path", "/"),
        "secure":   c.get("secure", False),
        "httpOnly": c.get("httpOnly", c.get("httponly", False)),
    }
    # SameSite — Chrome exporta "no_restriction"/"lax"/"strict"/None
    # Playwright solo acepta exactamente "Strict", "Lax" o "None"
    _same_raw = c.get("sameSite", c.get("samesite")) or ""
    _same_map = {
        "no_restriction": "None",
        "none":           "None",
        "lax":            "Lax",
        "strict":         "Strict",
        "unspecified":    "Lax",
        "":               "Lax",
    }
    fixed["sameSite"] = _same_map.get(str(_same_raw).lower(), "Lax")
    # Expiración
    exp = c.get("expirationDate", c.get("expires", c.get("expiry")))
    if exp and isinstance(exp, (int, float)) and exp > 0:
        fixed["expires"] = int(exp)
    return fixed


def main():
    if not COOKIES_FILE.exists():
        print(f"\n❌ No se encontró el archivo: {COOKIES_FILE}")
        print("   Exporta las cookies con Cookie-Editor y guárdalas como indeed_cookies.json")
        sys.exit(1)

    with open(COOKIES_FILE, encoding="utf-8") as f:
        raw = json.load(f)

    if not isinstance(raw, list):
        print("❌ El archivo no tiene formato de lista JSON. Usa 'Export as JSON' en Cookie-Editor.")
        sys.exit(1)

    cookies = [fix_cookie(c) for c in raw if c.get("name") and c.get("value")]
    print(f"✓ {len(cookies)} cookies cargadas desde {COOKIES_FILE.name}")

    SESSION_DIR.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    print("Abriendo perfil del bot para importar cookies...")
    with sync_playwright() as pw:
        browser = pw.chromium.launch_persistent_context(
            user_data_dir=str(SESSION_DIR),
            headless=False,
            args=["--no-sandbox"],
        )
        page = browser.new_page()

        # Navegar a indeed.com para que las cookies se acepten en el dominio correcto
        page.goto("https://cl.indeed.com", wait_until="domcontentloaded", timeout=20_000)

        # Importar cookies
        browser.add_cookies(cookies)
        print("✓ Cookies importadas al contexto")

        # Recargar para activar la sesión
        page.reload(wait_until="domcontentloaded", timeout=20_000)
        import time; time.sleep(3)

        # Verificar sesión
        title = page.title()
        url   = page.url
        print(f"  URL actual: {url}")
        print(f"  Título:     {title}")

        # Señales de sesión activa
        logged_in_sels = [
            "a[data-gnav-element-name='Account']",
            "div[data-testid='UserDropdown']",
            "#IA_AccountHamburger",
            "a[href*='/myjobs']",
            "img[class*='avatarImage']",
        ]
        session_ok = False
        for sel in logged_in_sels:
            try:
                if page.query_selector(sel):
                    session_ok = True
                    break
            except Exception:
                pass

        if session_ok:
            print("\n✅ SESIÓN VERIFICADA — Indeed reconoce tu cuenta")
        else:
            print("\n⚠️  No se pudo confirmar la sesión automáticamente.")
            print("   Revisa el navegador que se abrió — si ves tu cuenta en Indeed, está OK.")
            print("   Cierra el navegador manualmente cuando estés listo.")
            input("   Presiona ENTER para cerrar...")

        browser.close()

    print("\n✅ Sesión guardada en sessions/indeed/")
    print("   El bot usará esta sesión en todos los runs futuros.")

    # Limpiar el archivo de cookies por seguridad
    resp = input("\n¿Eliminar indeed_cookies.json por seguridad? (s/n): ").strip().lower()
    if resp == "s":
        COOKIES_FILE.unlink()
        print("✓ indeed_cookies.json eliminado.")


if __name__ == "__main__":
    main()

"""
login_indeed.py — Abre Chrome normal (sin Playwright) para hacer login en Indeed.

Abre Chrome con el perfil del bot. El usuario loguea manualmente.
Las cookies quedan guardadas en sessions/indeed/ para uso futuro del bot.

Uso:
    .venv\Scripts\python login_indeed.py
"""
import subprocess
import sys
import io
import time
from pathlib import Path

# Forzar UTF-8 en Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SESSION_DIR = Path(__file__).parent / "sessions" / "indeed"
SESSION_DIR.mkdir(parents=True, exist_ok=True)

import os
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]

def find_chrome():
    for p in CHROME_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def main():
    chrome = find_chrome()
    if not chrome:
        print("ERROR: No se encontro Chrome instalado.")
        sys.exit(1)

    print(f"Chrome: {chrome}")
    print(f"Perfil: {SESSION_DIR}")
    print()
    print("=" * 55)
    print("  Abriendo Chrome con el perfil del bot...")
    print()
    print("  1. Inicia sesion en Indeed con tu cuenta")
    print("  2. Vuelve aqui y presiona ENTER")
    print("     para guardar la sesion y cerrar Chrome")
    print("=" * 55)
    print()

    # Abrir Chrome directamente SIN Playwright — sin ninguna deteccion de automatizacion
    proc = subprocess.Popen([
        chrome,
        f"--user-data-dir={SESSION_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "https://cl.indeed.com/account/login",
    ])

    print("Chrome abierto. Inicia sesion en Indeed...")
    print("Cuando termines presiona ENTER aqui para guardar y cerrar.")
    print()

    try:
        input(">>> Presiona ENTER cuando hayas iniciado sesion: ")
    except EOFError:
        time.sleep(30)

    # Cerrar Chrome
    try:
        proc.terminate()
        time.sleep(2)
        proc.kill()
    except Exception:
        pass

    # Verificar que se guardaron cookies (Chrome nuevo: Default/Network/Cookies)
    cookies_file = SESSION_DIR / "Default" / "Network" / "Cookies"
    if not cookies_file.exists():
        cookies_file = SESSION_DIR / "Default" / "Cookies"
    if cookies_file.exists():
        size_kb = cookies_file.stat().st_size // 1024
        print()
        print("=" * 55)
        print(f"  Sesion guardada correctamente!")
        print(f"  Cookies: {cookies_file} ({size_kb} KB)")
        print(f"  El bot usara esta sesion en todos los runs.")
        print("=" * 55)
    else:
        print()
        print("AVISO: No se encontro el archivo de cookies.")
        print("Puede que Chrome no haya guardado la sesion.")
        print(f"Carpeta del perfil: {SESSION_DIR}")


if __name__ == "__main__":
    main()

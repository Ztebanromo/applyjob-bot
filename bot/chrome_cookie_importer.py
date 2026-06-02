"""
chrome_cookie_importer.py — Importa cookies del Chrome real del usuario
al directorio de sesión del bot.

Funciona leyendo el Chrome cookie store:
  Windows: %LOCALAPPDATA%/Google/Chrome/User Data/Default/Network/Cookies

Las cookies de Chrome >= 80 están cifradas con AES-256-GCM usando una clave
guardada en Local State y protegida con DPAPI (Windows).
Si no se puede descifrar, copia el valor en texto plano si está disponible.

Uso:
    from bot.chrome_cookie_importer import import_portal_cookies

    ok = import_portal_cookies("linkedin", "sessions/linkedin")
"""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


def _read_locked_file_windows(path: Path) -> bytes | None:
    """
    Lee un archivo bloqueado por otro proceso en Windows.
    Usa CreateFileW con FILE_SHARE_READ|WRITE|DELETE — bypasea el lock de Chrome.
    Retorna los bytes del archivo, o None si falla.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes, ctypes.wintypes
        GENERIC_READ          = 0x80000000
        FILE_SHARE_READ       = 0x00000001
        FILE_SHARE_WRITE      = 0x00000002
        FILE_SHARE_DELETE     = 0x00000004
        OPEN_EXISTING         = 3
        FILE_ATTRIBUTE_NORMAL = 0x80
        INVALID_HANDLE        = ctypes.c_void_p(-1).value

        k32 = ctypes.windll.kernel32
        k32.CreateFileW.restype  = ctypes.c_void_p   # HANDLE es puntero, no int32
        k32.GetFileSize.restype  = ctypes.wintypes.DWORD
        k32.ReadFile.restype     = ctypes.wintypes.BOOL
        k32.CloseHandle.restype  = ctypes.wintypes.BOOL
        handle = k32.CreateFileW(
            str(path),
            GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None,
        )
        if handle is None or handle == INVALID_HANDLE:
            return None
        try:
            size = k32.GetFileSize(handle, None)
            if size in (0, 0xFFFFFFFF):
                return None
            buf  = ctypes.create_string_buffer(size)
            read = ctypes.wintypes.DWORD(0)
            ok   = k32.ReadFile(handle, buf, size, ctypes.byref(read), None)
            return buf.raw[: read.value] if ok else None
        finally:
            k32.CloseHandle(handle)
    except Exception as exc:
        log.debug("[CHROME_IMPORT] _read_locked_file_windows: %s", exc)
        return None

# Dominios de auth por portal — solo se importan cookies de estos dominios
PORTAL_DOMAINS: dict[str, list[str]] = {
    "linkedin":      [".linkedin.com", ".www.linkedin.com"],
    "computrabajo":  [".computrabajo.com", ".cl.computrabajo.com"],
    "laborum":       [".laborum.cl", ".www.laborum.cl"],
    "trabajando":    [".trabajando.cl", ".www.trabajando.cl"],
    "infojobs":      [".infojobs.net", ".www.infojobs.net"],
    "chiletrabajos": [".chiletrabajos.cl", ".www.chiletrabajos.cl"],
    "getonyboard":   [".getonbrd.com", ".www.getonbrd.com"],
}


def _get_chrome_cookies_path() -> Path | None:
    """Retorna la ruta al archivo Cookies del Chrome del usuario."""
    if sys.platform == "win32":
        local_app = os.environ.get("LOCALAPPDATA", "")
        home = Path.home()
        candidates = [
            Path(local_app) / "Google" / "Chrome" / "User Data" / "Default" / "Network" / "Cookies",
            Path(local_app) / "Google" / "Chrome" / "User Data" / "Profile 1" / "Network" / "Cookies",
            home / "AppData" / "Local" / "Google" / "Chrome" / "User Data" / "Default" / "Network" / "Cookies",
        ]
    elif sys.platform == "darwin":
        candidates = [
            Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "Default" / "Cookies",
        ]
    else:
        candidates = [
            Path.home() / ".config" / "google-chrome" / "Default" / "Cookies",
        ]

    for p in candidates:
        if p.exists():
            return p
    return None


def _get_aes_key_windows() -> bytes | None:
    """
    Extrae y descifra la clave AES de Chrome desde Local State usando DPAPI.
    Retorna None si no puede obtenerla.
    """
    try:
        import base64
        import json
        import ctypes
        import ctypes.wintypes

        local_app = os.environ.get("LOCALAPPDATA", "")
        local_state_path = Path(local_app) / "Google" / "Chrome" / "User Data" / "Local State"
        if not local_state_path.exists():
            return None

        with open(local_state_path, encoding="utf-8") as f:
            local_state = json.load(f)

        encrypted_key_b64 = local_state.get("os_crypt", {}).get("encrypted_key", "")
        if not encrypted_key_b64:
            return None

        encrypted_key = base64.b64decode(encrypted_key_b64)[5:]  # quitar prefijo DPAPI

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", ctypes.wintypes.DWORD),
                         ("pbData", ctypes.POINTER(ctypes.c_char))]

        p = ctypes.create_string_buffer(encrypted_key, len(encrypted_key))
        blobin = DATA_BLOB(ctypes.sizeof(p), p)
        blobout = DATA_BLOB()
        retval = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blobin), None, None, None, None, 0,
            ctypes.byref(blobout)
        )
        if not retval:
            return None
        key = ctypes.string_at(blobout.pbData, blobout.cbData)
        ctypes.windll.kernel32.LocalFree(blobout.pbData)
        return key
    except Exception as exc:
        log.debug("[CHROME_IMPORT] Error obteniendo clave AES: %s", exc)
        return None


def _decrypt_cookie_value(encrypted_value: bytes, aes_key: bytes | None) -> str:
    """
    Descifra el valor de una cookie de Chrome.
    Chrome >= 80: AES-256-GCM con prefijo b'v10' o b'v11'.
    Anterior: DPAPI.
    Retorna string vacío si no puede descifrar.
    """
    if not encrypted_value:
        return ""

    # Chrome >= 80: AES-256-GCM
    if encrypted_value[:3] in (b"v10", b"v11") and aes_key:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            iv = encrypted_value[3:15]
            payload = encrypted_value[15:]
            aesgcm = AESGCM(aes_key)
            decrypted = aesgcm.decrypt(iv, payload, None)
            return decrypted.decode("utf-8", errors="replace")
        except Exception as exc:
            log.debug("[CHROME_IMPORT] Error AES-GCM: %s", exc)
            return ""

    # Fallback: DPAPI directo (Chrome antiguo)
    if sys.platform == "win32":
        try:
            import ctypes
            import ctypes.wintypes

            class DATA_BLOB(ctypes.Structure):
                _fields_ = [("cbData", ctypes.wintypes.DWORD),
                             ("pbData", ctypes.POINTER(ctypes.c_char))]

            p = ctypes.create_string_buffer(encrypted_value, len(encrypted_value))
            blobin = DATA_BLOB(ctypes.sizeof(p), p)
            blobout = DATA_BLOB()
            retval = ctypes.windll.crypt32.CryptUnprotectData(
                ctypes.byref(blobin), None, None, None, None, 0,
                ctypes.byref(blobout)
            )
            if retval:
                val = ctypes.string_at(blobout.pbData, blobout.cbData)
                ctypes.windll.kernel32.LocalFree(blobout.pbData)
                return val.decode("utf-8", errors="replace")
        except Exception:
            pass

    return ""


def import_via_cdp(portal: str, session_dir: str | Path) -> bool:
    """
    Extrae cookies del portal via CDP (Chrome corriendo con --remote-debugging-port).
    Más confiable que leer el SQLite porque Chrome no bloquea este acceso.
    Escribe las cookies en el directorio de sesión del bot.
    """
    from bot.chrome_cdp import is_port_open, CDP_URL
    if not is_port_open():
        return False

    domains = PORTAL_DOMAINS.get(portal, [])
    if not domains:
        return False

    session_dir = Path(session_dir)
    bot_cookies_dir = session_dir / "Default" / "Network"
    bot_cookies_dir.mkdir(parents=True, exist_ok=True)
    state_file = session_dir / "playwright_state.json"

    try:
        from playwright.sync_api import sync_playwright
        import json as _json

        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(CDP_URL)
            ctx = browser.contexts[0] if browser.contexts else None
            if ctx is None:
                return False

            # Obtener TODAS las cookies del contexto
            all_cookies = ctx.cookies()

            # Filtrar por dominio del portal
            portal_cookies = [
                c for c in all_cookies
                if any(
                    d.lstrip(".") in c.get("domain", "")
                    for d in domains
                )
            ]

            if not portal_cookies:
                log.info("[CDP_IMPORT] %s: sin cookies en Chrome para dominios %s", portal, domains)
                return False

            # Guardar como playwright_state.json (Playwright lo carga al iniciar)
            storage_state = {
                "cookies": portal_cookies,
                "origins": []
            }
            state_file.write_text(_json.dumps(storage_state, ensure_ascii=False, indent=2))

            # También escribir al SQLite para compatibilidad con _has_real_cookies
            _write_cookies_to_sqlite(portal_cookies, bot_cookies_dir / "Cookies")

            log.info("[CDP_IMPORT] %s: %d cookies importadas via CDP", portal, len(portal_cookies))
            return True

    except Exception as exc:
        log.warning("[CDP_IMPORT] Error importando %s via CDP: %s", portal, exc)
        return False


def _write_cookies_to_sqlite(cookies: list, db_path: Path) -> None:
    """Escribe cookies de Playwright (dicts) al formato SQLite de Chromium."""
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cookies (
            creation_utc     INTEGER NOT NULL UNIQUE PRIMARY KEY,
            host_key         TEXT NOT NULL,
            top_frame_site_key TEXT NOT NULL DEFAULT '',
            name             TEXT NOT NULL,
            value            TEXT NOT NULL,
            encrypted_value  BLOB NOT NULL DEFAULT '',
            path             TEXT NOT NULL,
            expires_utc      INTEGER NOT NULL,
            is_secure        INTEGER NOT NULL,
            is_httponly      INTEGER NOT NULL,
            last_access_utc  INTEGER NOT NULL,
            has_expires      INTEGER NOT NULL DEFAULT 1,
            is_persistent    INTEGER NOT NULL DEFAULT 1,
            priority         INTEGER NOT NULL DEFAULT 1,
            samesite         INTEGER NOT NULL DEFAULT -1,
            source_scheme    INTEGER NOT NULL DEFAULT 0,
            source_port      INTEGER NOT NULL DEFAULT -1,
            last_update_utc  INTEGER NOT NULL DEFAULT 0
        )
    """)
    import time as _time
    base_ts = int(_time.time() * 1_000_000) + 11_644_473_600 * 1_000_000
    for i, c in enumerate(cookies):
        expires = int(c.get("expires", 0))
        if expires > 0:
            expires_utc = expires * 1_000_000 + 11_644_473_600 * 1_000_000
        else:
            expires_utc = 0
        try:
            conn.execute("""
                INSERT OR REPLACE INTO cookies
                (creation_utc, host_key, top_frame_site_key, name, value,
                 encrypted_value, path, expires_utc, is_secure, is_httponly,
                 last_access_utc, has_expires, is_persistent, priority,
                 samesite, source_scheme, source_port, last_update_utc)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                base_ts + i,
                c.get("domain", ""),
                "",
                c.get("name", ""),
                c.get("value", ""),
                b"",
                c.get("path", "/"),
                expires_utc,
                1 if c.get("secure") else 0,
                1 if c.get("httpOnly") else 0,
                base_ts + i,
                1 if expires_utc > 0 else 0,
                1,
                1,
                -1,
                0,
                -1,
                0,
            ))
        except Exception:
            pass
    conn.commit()
    conn.close()


def import_portal_cookies(portal: str, session_dir: str | Path) -> bool:
    """
    Importa cookies del Chrome real al perfil del bot.

    Intenta en orden:
      1. Via CDP (Chrome corriendo con debug) — sin bloqueo de archivo
      2. Via SQLite directo — solo funciona con Chrome cerrado

    Returns:
        True si se importó al menos una cookie.
    """
    if portal not in PORTAL_DOMAINS:
        log.warning("[CHROME_IMPORT] Portal '%s' sin dominios configurados.", portal)
        return False

    # Intento 1: CDP (Chrome corriendo)
    cdp_ok = import_via_cdp(portal, session_dir)
    if cdp_ok:
        return True

    # Intento 2: SQLite directo (requiere Chrome cerrado)
    chrome_path = _get_chrome_cookies_path()
    if not chrome_path:
        log.warning("[CHROME_IMPORT] Archivo Cookies de Chrome no encontrado.")
        return False

    domains = PORTAL_DOMAINS[portal]
    session_dir = Path(session_dir)
    bot_cookies = session_dir / "Default" / "Network" / "Cookies"
    bot_cookies.parent.mkdir(parents=True, exist_ok=True)

    # Obtener clave AES una vez (Windows)
    aes_key = _get_aes_key_windows() if sys.platform == "win32" else None

    # Abrir Chrome cookies — Chrome mantiene el SQLite bloqueado en Windows.
    # Estrategia en cascada:
    #   1. CreateFileW con FILE_SHARE_WRITE (bypasea lock de Windows)
    #   2. SQLite URI inmutable  (alternativa sin copiar)
    #   3. shutil.copy2          (solo funciona con Chrome cerrado)
    tmp_chrome = None
    try:
        conn_src = None

        # Intento 1: leer bytes del archivo bloqueado y volcar a temporal
        raw_bytes = _read_locked_file_windows(chrome_path)
        if raw_bytes and len(raw_bytes) > 4096:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
                tmp_chrome = tmp.name
                tmp.write(raw_bytes)
            try:
                conn_src = sqlite3.connect(tmp_chrome)
                conn_src.row_factory = sqlite3.Row
                log.debug("[CHROME_IMPORT] Cookies leídas via CreateFileW (Chrome abierto).")
            except Exception:
                conn_src = None

        # Intento 2: SQLite URI inmutable
        if conn_src is None:
            try:
                uri = f"file:{chrome_path.as_posix()}?mode=ro&immutable=1"
                conn_src = sqlite3.connect(uri, uri=True)
                conn_src.row_factory = sqlite3.Row
            except Exception:
                conn_src = None

        # Intento 3: copia directa (requiere Chrome cerrado)
        if conn_src is None:
            if tmp_chrome is None:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
                    tmp_chrome = tmp.name
            shutil.copy2(str(chrome_path), tmp_chrome)
            conn_src = sqlite3.connect(tmp_chrome)
            conn_src.row_factory = sqlite3.Row

        # Asegurar que el destino existe con el schema mínimo
        conn_dst = sqlite3.connect(str(bot_cookies))
        conn_dst.execute("""
            CREATE TABLE IF NOT EXISTS cookies (
                creation_utc     INTEGER NOT NULL UNIQUE PRIMARY KEY,
                host_key         TEXT NOT NULL,
                top_frame_site_key TEXT NOT NULL DEFAULT '',
                name             TEXT NOT NULL,
                value            TEXT NOT NULL,
                encrypted_value  BLOB NOT NULL DEFAULT '',
                path             TEXT NOT NULL,
                expires_utc      INTEGER NOT NULL,
                is_secure        INTEGER NOT NULL,
                is_httponly      INTEGER NOT NULL,
                last_access_utc  INTEGER NOT NULL,
                has_expires      INTEGER NOT NULL DEFAULT 1,
                is_persistent    INTEGER NOT NULL DEFAULT 1,
                priority         INTEGER NOT NULL DEFAULT 1,
                samesite         INTEGER NOT NULL DEFAULT -1,
                source_scheme    INTEGER NOT NULL DEFAULT 0,
                source_port      INTEGER NOT NULL DEFAULT -1,
                last_update_utc  INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn_dst.commit()

        # Leer cookies del portal desde Chrome
        placeholders = ",".join("?" * len(domains))
        try:
            rows = conn_src.execute(
                f"SELECT * FROM cookies WHERE host_key IN ({placeholders})",
                domains,
            ).fetchall()
        except Exception as exc:
            log.warning("[CHROME_IMPORT] Error leyendo cookies de Chrome: %s", exc)
            conn_src.close()
            conn_dst.close()
            return False

        if not rows:
            log.info(
                "[CHROME_IMPORT] %s: sin cookies en Chrome para dominios %s",
                portal, domains,
            )
            conn_src.close()
            conn_dst.close()
            return False

        col_names = [d[0] for d in conn_src.execute(
            "SELECT * FROM cookies LIMIT 1"
        ).description or []]

        imported = 0
        for row in rows:
            # Descifrar valor si es necesario
            value = row["value"] if "value" in (row.keys() if hasattr(row, 'keys') else col_names) else ""
            enc_val = row["encrypted_value"] if "encrypted_value" in col_names else b""
            if not value and enc_val:
                value = _decrypt_cookie_value(bytes(enc_val), aes_key)

            def _col(name: str, default=None):
                try:
                    return row[name]
                except (IndexError, KeyError):
                    return default

            try:
                conn_dst.execute("""
                    INSERT OR REPLACE INTO cookies
                    (creation_utc, host_key, top_frame_site_key, name, value,
                     encrypted_value, path, expires_utc, is_secure, is_httponly,
                     last_access_utc, has_expires, is_persistent, priority,
                     samesite, source_scheme, source_port, last_update_utc)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    _col("creation_utc", 0),
                    _col("host_key", ""),
                    _col("top_frame_site_key", ""),
                    _col("name", ""),
                    value,
                    b"",  # no guardar cifrado — ya desciframos
                    _col("path", "/"),
                    _col("expires_utc", 0),
                    _col("is_secure", 1),
                    _col("is_httponly", 0),
                    _col("last_access_utc", 0),
                    _col("has_expires", 1),
                    _col("is_persistent", 1),
                    _col("priority", 1),
                    _col("samesite", -1),
                    _col("source_scheme", 0),
                    _col("source_port", -1),
                    _col("last_update_utc", 0),
                ))
                imported += 1
            except Exception as exc:
                log.debug(
                    "[CHROME_IMPORT] Error insertando cookie %s: %s",
                    _col("name", "?"), exc
                )

        conn_dst.commit()
        conn_src.close()
        conn_dst.close()

        log.info(
            "[CHROME_IMPORT] %s: %d cookies importadas (dominios: %s)",
            portal, imported, domains,
        )
        return imported > 0

    except Exception as exc:
        log.warning("[CHROME_IMPORT] Error importando %s: %s", portal, exc)
        return False
    finally:
        if tmp_chrome and os.path.exists(tmp_chrome):
            try:
                os.remove(tmp_chrome)
            except Exception:
                pass


def chrome_cookies_available() -> bool:
    """True si el Chrome real del usuario tiene un archivo Cookies accesible."""
    p = _get_chrome_cookies_path()
    return p is not None and p.exists()


def copy_chrome_fingerprint(session_dir: str | Path) -> bool:
    """
    Copia el fingerprint del Chrome real del usuario al directorio de sesión del bot.

    Archivos copiados (no están bloqueados aunque Chrome esté corriendo):
      - Default/Preferences   → device ID, browser settings, fingerprint
      - Local State           → AES key + browser fingerprint

    Esto hace que el bot se vea IDÉNTICO a tu Chrome para cualquier sitio,
    evitando la verificación por email/SMS que aparece en nuevos dispositivos.

    Args:
        session_dir: Directorio de sesión del bot (ej: sessions/linkedin)

    Returns:
        True si al menos un archivo fue copiado exitosamente.
    """
    if sys.platform != "win32":
        return False

    local_app = os.environ.get("LOCALAPPDATA", "")
    chrome_profile = Path(local_app) / "Google" / "Chrome" / "User Data" / "Default"
    session_dir = Path(session_dir)

    copied = 0

    # Preferences — contiene device ID y fingerprint del browser
    src_prefs = chrome_profile / "Preferences"
    if src_prefs.exists():
        try:
            dst_default = session_dir / "Default"
            dst_default.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_prefs, dst_default / "Preferences")
            copied += 1
            log.debug("[FINGERPRINT] Preferences copiado a %s", session_dir)
        except Exception as exc:
            log.debug("[FINGERPRINT] Error copiando Preferences: %s", exc)

    # Local State — AES key para descifrar cookies + config global del browser
    src_state = Path(local_app) / "Google" / "Chrome" / "User Data" / "Local State"
    if src_state.exists():
        try:
            dst_parent = session_dir.parent.parent if session_dir.name != "sessions" else session_dir
            # Escribir en la raíz de User Data del perfil aislado (un nivel arriba de sessions/)
            # Para Playwright: sessions/portal/Local State
            shutil.copy2(src_state, session_dir / "Local State")
            copied += 1
            log.debug("[FINGERPRINT] Local State copiado a %s", session_dir)
        except Exception as exc:
            log.debug("[FINGERPRINT] Error copiando Local State: %s", exc)

    return copied > 0

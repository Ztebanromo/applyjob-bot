"""
Validación de configuración al arrancar el bot.

Qué valida:
  1. USER_PROFILE — que los campos obligatorios estén completos y no sean los valores de ejemplo
  2. SITE_CONFIG  — que el portal pedido tenga todos los selectores requeridos
  3. cv_path      — que el archivo exista si está configurado
  4. Variables de entorno — que .env esté cargado si existe

Por qué importa:
  Detectar configuración incompleta ANTES de abrir el browser ahorra tiempo
  y evita runs que fallan silenciosamente a mitad de camino.
"""
import os
import logging
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

log = logging.getLogger("applyjob.validator")

# Campos que NO pueden tener los valores de ejemplo del template
PLACEHOLDER_VALUES = {
    "Tu Nombre Completo", "Tu Nombre", "Tu Apellido",
    "tuemail@gmail.com", "+1234567890", "Ciudad, País",
    "https://linkedin.com/in/tu-perfil", "https://tu-portfolio.com",
    "C:/Users/TuUsuario/Documents/CV.pdf", "",
}

# Campos mínimos obligatorios para que el bot funcione
REQUIRED_PROFILE_FIELDS = ["full_name", "email", "phone"]

# Selectores que todo portal debe tener configurados
REQUIRED_CONFIG_KEYS = ["url_busqueda", "selector_oferta", "selector_boton_aplicar", "tipo_postulacion"]

# Tipos de postulación válidos
VALID_TIPOS = {"directa", "modal", "externa"}


class ConfigError(ValueError):
    """Error de configuración — el bot no debe arrancar."""


def load_env() -> bool:
    """
    Carga variables de entorno desde .env si existe.
    Retorna True si se encontró y cargó el archivo.

    El archivo .env es opcional: si no existe, se usan los valores
    hardcodeados en config.py.
    """
    env_path = find_dotenv(usecwd=True)
    if env_path:
        load_dotenv(env_path)
        log.debug("Variables de entorno cargadas desde: %s", env_path)
        return True
    log.debug(".env no encontrado — usando configuración por defecto")
    return False


def validate_profile(profile: dict) -> list[str]:
    """
    Valida USER_PROFILE y retorna lista de advertencias.
    Si hay campos OBLIGATORIOS con valores de ejemplo, lanza ConfigError.

    Args:
        profile: diccionario USER_PROFILE

    Returns:
        Lista de strings con advertencias no bloqueantes

    Raises:
        ConfigError: si campos obligatorios tienen valores de ejemplo
    """
    warnings = []
    errors   = []

    for field in REQUIRED_PROFILE_FIELDS:
        value = profile.get(field, "")
        if not value or value in PLACEHOLDER_VALUES:
            errors.append(
                f"Campo obligatorio '{field}' no configurado "
                f"(valor actual: '{value}'). Edita bot/config.py → USER_PROFILE."
            )

    # Advertencias para campos opcionales con valores de ejemplo
    optional_fields = [k for k in profile if k not in REQUIRED_PROFILE_FIELDS]
    for field in optional_fields:
        value = str(profile.get(field, ""))
        if value in PLACEHOLDER_VALUES and field != "cv_path":
            warnings.append(f"  ⚠  '{field}' tiene valor de ejemplo — considera completarlo")

    # Validar cv_path si está configurado y no es placeholder
    cv_path = profile.get("cv_path", "")
    if cv_path and cv_path not in PLACEHOLDER_VALUES:
        if not Path(cv_path).exists():
            warnings.append(
                f"  ⚠  cv_path apunta a un archivo que no existe: '{cv_path}'\n"
                f"     El bot funcionará pero no podrá subir tu CV."
            )
    elif not cv_path or cv_path in PLACEHOLDER_VALUES:
        warnings.append("  ⚠  cv_path no configurado — el bot no subirá CV automáticamente")

    if errors:
        raise ConfigError(
            "USER_PROFILE incompleto:\n" + "\n".join(f"  ✗ {e}" for e in errors)
        )

    return warnings


def validate_portal_config(portal_name: str, config: dict) -> list[str]:
    """
    Valida la configuración de un portal específico.

    Args:
        portal_name: nombre del portal (ej. "linkedin")
        config     : diccionario de SITE_CONFIG[portal_name]

    Returns:
        Lista de advertencias no bloqueantes

    Raises:
        ConfigError: si faltan selectores obligatorios o el tipo es inválido
    """
    warnings = []
    errors   = []

    for key in REQUIRED_CONFIG_KEYS:
        if not config.get(key):
            errors.append(f"Falta '{key}' en SITE_CONFIG['{portal_name}']")

    tipo = config.get("tipo_postulacion", "")
    if tipo not in VALID_TIPOS:
        errors.append(
            f"tipo_postulacion='{tipo}' inválido. "
            f"Valores válidos: {', '.join(VALID_TIPOS)}"
        )

    max_o = config.get("max_offers_per_run", 0)
    if max_o > 50:
        warnings.append(
            f"  ⚠  max_offers_per_run={max_o} es muy alto. "
            f"LinkedIn puede bloquear ante >10-15 postulaciones por hora."
        )

    if errors:
        raise ConfigError(
            f"Configuración inválida para portal '{portal_name}':\n"
            + "\n".join(f"  ✗ {e}" for e in errors)
        )

    return warnings


def run_startup_validation(portal_name: str, profile: dict, config: dict) -> None:
    """
    Punto de entrada principal. Ejecuta todas las validaciones antes de
    lanzar el browser.

    Imprime advertencias en consola pero no detiene el bot por ellas.
    Lanza ConfigError (que sí detiene el bot) ante errores bloqueantes.

    Args:
        portal_name: nombre del portal a usar
        profile    : USER_PROFILE
        config     : SITE_CONFIG[portal_name]

    Raises:
        ConfigError: ante configuración bloqueante
    """
    load_env()

    log.info("Validando configuración para portal '%s'…", portal_name)

    profile_warns  = validate_profile(profile)
    portal_warns   = validate_portal_config(portal_name, config)
    all_warnings   = profile_warns + portal_warns

    if all_warnings:
        log.warning("Advertencias de configuración (el bot continuará):")
        for w in all_warnings:
            log.warning(w)
    else:
        log.info("✓ Configuración válida")

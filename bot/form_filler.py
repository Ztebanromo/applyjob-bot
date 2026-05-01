"""
Autocompletado inteligente de formularios de postulación.
Detecta campos por name/id/placeholder/aria-label y aplica USER_PROFILE.
"""
from pathlib import Path
from playwright.sync_api import Page
from .stealth_utils import micro_delay, human_delay


# Mapeo patrón → clave de USER_PROFILE
FIELD_PATTERNS: dict[str, list[str]] = {
    "full_name":    ["fullname", "full_name", "nombre_completo", "nombre completo"],
    "first_name":   ["firstname", "first_name", "nombre", "givenname", "given_name"],
    "last_name":    ["lastname", "last_name", "apellido", "surname", "familyname"],
    "email":        ["email", "correo", "mail", "e-mail"],
    "phone":        ["phone", "telefono", "tel", "mobile", "celular", "movil"],
    "city":         ["city", "ciudad", "location", "ubicacion", "localidad"],
    "linkedin":     ["linkedin", "linkedin_url", "perfil", "profile"],
    "portfolio":    ["portfolio", "website", "sitio", "web", "github"],
    "salary":       ["salary", "salario", "pretension", "remuneracion", "sueldo"],
    "years_exp":    ["years", "experience", "experiencia", "anos", "anios"],
    "cover_letter": ["cover", "carta", "motivation", "presentacion", "message", "mensaje"],
}


def _match_field(attrs: str) -> str | None:
    """Retorna la clave de USER_PROFILE si attrs coincide con algún patrón."""
    attrs_lower = attrs.lower()
    for profile_key, patterns in FIELD_PATTERNS.items():
        if any(p in attrs_lower for p in patterns):
            return profile_key
    return None


def _is_textarea(el) -> bool:
    """Verifica si el elemento es un textarea por tag name real (no por atributo type)."""
    try:
        return el.evaluate("el => el.tagName.toLowerCase()") == "textarea"
    except Exception:
        return False


def fill_text_fields(page: Page, profile: dict) -> int:
    """
    Itera sobre todos los inputs/textareas visibles y rellena los que reconoce.
    Retorna la cantidad de campos completados.
    """
    filled = 0
    selectors = [
        "input[type='text']", "input[type='tel']", "input[type='email']",
        "input:not([type])", "textarea",
    ]

    for sel in selectors:
        elements = page.query_selector_all(sel)
        for el in elements:
            try:
                if not el.is_visible() or not el.is_enabled():
                    continue

                # Recolectar atributos para detección
                attrs = " ".join(filter(None, [
                    el.get_attribute("name") or "",
                    el.get_attribute("id") or "",
                    el.get_attribute("placeholder") or "",
                    el.get_attribute("aria-label") or "",
                    el.get_attribute("data-testid") or "",
                ]))

                profile_key = _match_field(attrs)
                if not profile_key or profile_key not in profile:
                    continue

                value = profile[profile_key]

                # BUG FIX: usar tag name real para distinguir textarea de input
                if _is_textarea(el):
                    current = el.text_content() or ""
                else:
                    current = el.input_value() or ""

                if current.strip():
                    continue  # no sobreescribir si ya tiene contenido

                el.click()
                micro_delay()
                el.fill(str(value))
                human_delay(0.3, 0.8)
                filled += 1

            except Exception:
                continue

    return filled


def fill_file_upload(page: Page, profile: dict) -> bool:
    """Sube el CV si encuentra un input[type=file]."""
    cv_path = profile.get("cv_path", "")
    if not cv_path or not Path(cv_path).exists():
        return False
    try:
        file_input = page.query_selector("input[type='file']")
        if file_input and file_input.is_visible():
            file_input.set_input_files(cv_path)
            human_delay(1.0, 2.0)
            return True
    except Exception:
        pass
    return False


def handle_yes_no_questions(page: Page) -> None:
    """
    Responde preguntas de screening de sí/no seleccionando 'Yes'/'Sí'
    en radios cuando están disponibles.
    """
    try:
        radios = page.query_selector_all("input[type='radio']")
        for radio in radios:
            label = (radio.get_attribute("value") or "").lower()
            if label in ("yes", "si", "sí", "true", "1"):
                if radio.is_visible() and not radio.is_checked():
                    radio.click()
                    micro_delay()
    except Exception:
        pass


def fill_form(page: Page, profile: dict) -> dict:
    """
    Entry point principal. Ejecuta todas las estrategias de llenado.
    Retorna un resumen de lo que se completó.
    """
    human_delay(1.0, 2.0)
    result = {
        "text_fields":   fill_text_fields(page, profile),
        "file_uploaded": fill_file_upload(page, profile),
    }
    handle_yes_no_questions(page)
    return result

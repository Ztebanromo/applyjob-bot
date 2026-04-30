"""
Autocompletado inteligente de formularios de postulación.
Detecta campos por name/id/placeholder/aria-label y aplica USER_PROFILE.
"""
from pathlib import Path
from playwright.sync_api import Page
from .stealth_utils import human_type, micro_delay, human_delay


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


def _get_field_attrs(page: Page, selector: str) -> str:
    """Extrae los atributos relevantes de un input para detección."""
    return page.eval_on_selector(
        selector,
        """el => [
            el.name || '',
            el.id || '',
            el.placeholder || '',
            el.getAttribute('aria-label') || '',
            el.getAttribute('data-testid') || '',
            el.getAttribute('label') || ''
        ].join(' ')"""
    )


def fill_text_fields(page: Page, profile: dict) -> int:
    """
    Itera sobre todos los inputs/textareas visibles y rellena los que reconoce.
    Retorna la cantidad de campos completados.
    """
    filled = 0
    selectors = ["input[type='text']", "input[type='tel']", "input[type='email']",
                 "input:not([type])", "textarea"]

    for sel in selectors:
        elements = page.query_selector_all(sel)
        for el in elements:
            try:
                if not el.is_visible() or not el.is_enabled():
                    continue
                handle_selector = f"#{el.get_attribute('id')}" if el.get_attribute("id") else sel
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
                # No sobreescribir si ya tiene contenido
                current = el.input_value() if el.get_attribute("type") != "textarea" else el.text_content()
                if current and current.strip():
                    continue
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
    Intenta responder preguntas de screening de sí/no de forma segura
    (selecciona 'Sí' / 'Yes' en radios cuando es posible).
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
        "text_fields": fill_text_fields(page, profile),
        "file_uploaded": fill_file_upload(page, profile),
    }
    handle_yes_no_questions(page)
    return result

"""
Módulo de Autocompletado de Formularios (Form Filler).

Este módulo es responsable de identificar y rellenar automáticamente los diversos
campos que pueden aparecer en un proceso de postulación (LinkedIn Easy Apply, 
Laborum, Indeed, etc.).

Estrategias de Detección:
    - Análisis de Atributos: Se escanean atributos HTML como name, id, placeholder,
      aria-label y etiquetas <label> asociadas.
    - Pattern Matching: Se utilizan diccionarios de palabras clave (FIELD_PATTERNS)
      para mapear los campos encontrados con los datos del USER_PROFILE.
    - Heurística de Respuestas: Para preguntas de screening (sí/no, experiencia),
      el bot prioriza respuestas afirmativas o basadas en los años de experiencia
      declarados por el usuario.

Soporte Multi-ATS:
    Diseñado para funcionar en diversas plataformas (Navent, Greenhouse, Lever, 
    Workday) manejando sus particularidades de renderizado.
"""

from pathlib import Path
import json
import logging
from typing import Dict, List, Optional, Set, Any

from playwright.sync_api import Page, ElementHandle

from .stealth_utils import micro_delay, human_delay

log = logging.getLogger("applyjob.form_filler")

# ---------------------------------------------------------------------------
# Knowledge Base por modo (IT / Bodega)
# ---------------------------------------------------------------------------
_KB_PATH = Path(__file__).parent.parent / "data" / "profile_kb.json"
_PROFILE_KB: dict = {}


def _load_kb() -> dict:
    global _PROFILE_KB
    if not _PROFILE_KB and _KB_PATH.exists():
        with open(_KB_PATH, encoding="utf-8") as f:
            _PROFILE_KB = json.load(f)
    return _PROFILE_KB


def _detect_mode_from_title(job_title: str) -> str:
    """Infiere 'it' o 'bodega' desde el título del puesto."""
    title_lower = job_title.lower()
    kb = _load_kb()
    for mode, data in kb.items():
        for kw in data.get("role_keywords", []):
            if kw in title_lower:
                return mode
    return "it"

# ---------------------------------------------------------------------------
# DICCIONARIO DE PATRONES (FIELD_PATTERNS)
# ---------------------------------------------------------------------------
# Mapea claves del USER_PROFILE con posibles nombres de campos en el DOM.
# Se ha extendido para cubrir términos comunes en Chile (Laborum/Computrabajo).
# ---------------------------------------------------------------------------
FIELD_PATTERNS: Dict[str, List[str]] = {
    "full_name": [
        "fullname", "full_name", "nombre_completo", "nombre completo",
        "your name", "tu nombre", "nombre y apellido"
    ],
    "first_name": [
        "firstname", "first_name", "nombre", "givenname", "given_name",
        "first name", "primer nombre"
    ],
    "last_name": [
        "lastname", "last_name", "apellido", "surname", "familyname",
        "last name", "apellidos"
    ],
    "email": [
        "email", "correo", "mail", "e-mail", "correo electrónico", "email_address"
    ],
    "phone": [
        "phone", "telefono", "teléfono", "tel", "mobile", "celular", "móvil",
        "número de teléfono", "numero de telefono", "phone number", "whatsapp"
    ],
    "phone_number": [
        "phone number", "numero de telefono sin codigo", "número sin prefijo",
        "local phone", "number only"
    ],
    "country": [
        "country", "país", "pais", "nacionalidad", "nationality",
        "país de residencia", "pais de residencia", "country of residence",
        "ubicación pais", "selecciona tu país"
    ],
    "country_code": [
        "country code", "código de país", "codigo de pais", "prefijo",
        "phone prefix", "dial code", "código telefónico", "codigo telefonico"
    ],
    "city": [
        "city", "ciudad", "location", "ubicacion", "ubicación", "localidad",
        "ciudad de residencia", "donde vives", "comuna", "región", "region"
    ],
    "linkedin": [
        "linkedin", "linkedin_url", "perfil linkedin", "profile url", "social link"
    ],
    "portfolio": [
        "portfolio", "website", "sitio web", "web", "github", "portafolio", "personal site"
    ],
    "salary": [
        "salary", "salario", "pretension", "pretensión", "remuneracion",
        "remuneración", "sueldo", "renta", "renta líquida", "renta liquida",
        "expected salary", "desired salary", "expectativa salarial", 
        "pretensión salarial", "cuánto quieres ganar", "cuanto quieres ganar"
    ],
    "years_exp": [
        "años de experiencia", "anos de experiencia", "how many years", 
        "cuántos años", "cuantos años", "nivel de experiencia", 
        "years of experience", "experiencia en"
    ],
    "cover_letter": [
        "cover", "carta", "motivation", "presentación", "presentación",
        "message", "mensaje", "sobre ti", "about you", "tell us",
        "cuéntanos", "cuentanos", "descripción", "descripción",
        "por qué", "porque", "motiv", "interés", "interes",
        "habilidades", "skills", "fortaleza", "logro", "aporte"
    ],
    "availability": [
        "disponibilidad", "disponible", "cuando puedes", "cuándo puedes",
        "inicio", "fecha de inicio", "start date", "incorporación", "incorporación"
    ],
    "english_level": [
        "ingles", "inglés", "english", "idioma", "language", "nivel de ingles",
        "nivel de inglés", "english level"
    ],
    "work_mode": [
        "presencial", "remoto", "hibrido", "híbrido", "modalidad",
        "trabajo presencial", "work mode", "remote", "on-site"
    ],
}

# Valores que deben enviarse como números puros (sin puntos ni símbolos)
# para evitar errores de validación en inputs de tipo 'number'.
NUMERIC_VALUES: Dict[str, str] = {
    "salary":    "850000", 
    "years_exp": "0",
}

# Valores que el bot interpreta como "Afirmativos"
YES_VALUES: Set[str] = {
    "yes", "si", "sí", "true", "1", "authorized", "permitido",
    "yes, i am authorized", "sí, estoy autorizado", "habilitado", 
    "disponible", "acepto", "concedo"
}

# Valores que el bot evita seleccionar en dropdowns (filtros negativos)
NO_VALUES: Set[str] = {
    "no", "false", "0", "not authorized", "no estoy autorizado",
    "select an option", "selecciona una opción", "choose", "elegir", ""
}


def _get_label_text(page: Page, el: ElementHandle) -> str:
    """
    Busca de forma exhaustiva el texto de la etiqueta (label) asociada a un input.
    
    Busca por:
        1. Atributo 'for' en etiquetas <label>.
        2. Padre de tipo <label> (encapsulamiento).
        3. Hermanos visuales (div, span, p) que actúen como etiqueta en sistemas ATS.
    """
    try:
        field_id = el.get_attribute("id") or ""
        if field_id:
            label = page.query_selector(f"label[for='{field_id}']")
            if label:
                return (label.text_content() or "").strip()
        
        # Heurística mediante evaluación JS para subir en el DOM
        text = el.evaluate("""
            el => {
                let cur = el.parentElement;
                for (let i = 0; i < 4; i++) {
                    if (!cur) break;
                    if (cur.tagName === 'LABEL') return cur.textContent.trim();
                    let sib = cur.previousElementSibling;
                    if (sib) {
                        const tag = sib.tagName.toLowerCase();
                        if (tag === 'label' || tag === 'p' || tag === 'span' || sib.className.includes('label')) {
                            return sib.textContent.trim();
                        }
                    }
                    cur = cur.parentElement;
                }
                return '';
            }
        """)
        return (text or "").strip()
    except Exception:
        return ""


def _build_attrs(page: Page, el: ElementHandle) -> str:
    """
    Construye una cadena de texto con todos los atributos identificativos del campo.
    Esta cadena se usa luego para el matching de patrones.
    """
    try:
        parts = [
            el.get_attribute("name") or "",
            el.get_attribute("id") or "",
            el.get_attribute("placeholder") or "",
            el.get_attribute("aria-label") or "",
            el.get_attribute("data-testid") or "",
            _get_label_text(page, el),
        ]
        return " ".join(p for p in parts if p).lower()
    except Exception:
        return ""


def _match_field(attrs: str) -> Optional[str]:
    """Determina a qué clave del perfil pertenece un campo según sus atributos."""
    for profile_key, patterns in FIELD_PATTERNS.items():
        if any(p in attrs for p in patterns):
            return profile_key
    return None


def fill_text_fields(page: Page, profile: dict) -> int:
    """
    Localiza y rellena inputs de texto, email, teléfono y textareas.
    Implementa una lógica de simulación de escritura para campos de ciudad/comuna
    que activan autocompletado en el ATS.
    """
    filled = 0
    selectors = ["input[type='text']", "input[type='tel']", "input[type='email']", 
                 "input[type='number']", "input:not([type])", "textarea"]

    for sel in selectors:
        for el in page.query_selector_all(sel):
            try:
                if not el.is_visible() or not el.is_enabled():
                    continue

                attrs = _build_attrs(page, el)
                profile_key = _match_field(attrs)
                if not profile_key or profile_key not in profile:
                    continue

                # Evitar sobrescribir si ya tiene contenido (respetar datos manuales)
                current_val = el.input_value() if sel != "textarea" else el.text_content()
                if (current_val or "").strip():
                    continue

                # Determinar valor a ingresar (numérico o texto)
                value = NUMERIC_VALUES.get(profile_key, str(profile[profile_key]))

                # Limpieza especial para Teléfonos (quitar espacios si es input tel/number)
                if profile_key in ("phone", "phone_number"):
                    # Si el input es numérico o tel, quitamos todo lo que no sea número o +
                    if sel in ("input[type='tel']", "input[type='number']"):
                        value = "".join(c for c in value if c.isdigit() or c == "+")
                    
                    # Si detectamos que es un campo de "número local" (sin prefix), 
                    # usamos phone_number en vez de phone
                    if any(p in attrs for p in ["sin prefijo", "local", "number only"]):
                        value = str(profile.get("phone_number", value)).replace(" ", "")

                el.click()
                micro_delay()

                # Manejo especial para Autocomplete de ATS (Ciudad)
                if profile_key == "city":
                    el.fill("")
                    el.type(value, delay=50)
                    human_delay(1.0, 1.5)
                    page.keyboard.press("ArrowDown")
                    page.keyboard.press("Enter")
                else:
                    el.fill(value)

                filled += 1
                human_delay(0.2, 0.5)
            except Exception:
                continue
    return filled


def fill_dropdowns(page: Page, profile: dict) -> int:
    """
    Responde menús desplegables (select).
    Intenta coincidir con el perfil del usuario, de lo contrario, prioriza
    respuestas afirmativas (Yes/Sí) para avanzar en los filtros de la empresa.

    Manejo especial:
    - País / country → busca "Chile" o "CL" antes que cualquier otro valor
    - Código de país / phone prefix → busca "+56" o "56" (Chile)
    """
    filled = 0

    # Términos que identifican un dropdown de país
    _COUNTRY_ATTRS   = {"country", "país", "pais", "nationality", "nacionalidad"}
    # Términos que identifican un dropdown de código de país / prefijo
    _PHONE_PREFIX_ATTRS = {"country code", "código de país", "codigo de pais",
                           "prefijo", "phone prefix", "dial code"}
    # Términos de Chile para buscar en las opciones
    _CHILE_TERMS = {"chile", "cl", "chi"}
    _CHILE_CODE  = {"+56", "56", "56 "}

    for sel_el in page.query_selector_all("select"):
        try:
            if not sel_el.is_visible():
                continue

            attrs = _build_attrs(page, sel_el)
            options = sel_el.query_selector_all("option")
            chosen_val = None

            # ── Caso especial: dropdown de PAÍS ─────────────────────────────
            if any(t in attrs for t in _COUNTRY_ATTRS):
                for opt in options:
                    o_text = (opt.text_content() or "").strip().lower()
                    o_val  = (opt.get_attribute("value") or "").strip().lower()
                    if any(t in o_text or t == o_val for t in _CHILE_TERMS):
                        chosen_val = opt.get_attribute("value")
                        log.debug("  [form] país → Chile (%r)", chosen_val)
                        break

            # ── Caso especial: dropdown de CÓDIGO DE PAÍS / prefijo ──────────
            elif any(t in attrs for t in _PHONE_PREFIX_ATTRS):
                for opt in options:
                    o_text = (opt.text_content() or "").strip()
                    o_val  = (opt.get_attribute("value") or "").strip()
                    if any(c in o_text or c == o_val for c in _CHILE_CODE):
                        chosen_val = opt.get_attribute("value")
                        log.debug("  [form] código de país → +56 (%r)", chosen_val)
                        break

            else:
                profile_key = _match_field(attrs)

                # 1. Intentar match con perfil
                if profile_key and profile_key in profile:
                    p_val = str(profile[profile_key]).lower()
                    for opt in options:
                        o_text = (opt.text_content() or "").lower()
                        if p_val in o_text:
                            chosen_val = opt.get_attribute("value")
                            break

                # 2. Fallback: Priorizar respuesta afirmativa
                if not chosen_val:
                    for opt in options:
                        o_text = (opt.text_content() or "").lower()
                        if any(y in o_text for y in YES_VALUES):
                            chosen_val = opt.get_attribute("value")
                            break

            if chosen_val:
                sel_el.select_option(chosen_val)
                filled += 1
                micro_delay()
        except Exception:
            continue
    return filled


def handle_yes_no_questions(page: Page) -> int:
    """
    Maneja botones de radio y checkboxes de tipo sí/no o consentimiento.
    Útil para preguntas de '¿Estás legalmente autorizado para trabajar?' o
    '¿Aceptas los términos y condiciones?'.
    """
    answered = 0
    # Manejo de Radios (Sí/No)
    for radio in page.query_selector_all("input[type='radio']"):
        try:
            val = (radio.get_attribute("value") or "").lower()
            # Si el valor del radio es afirmativo y no está marcado
            if val in YES_VALUES and not radio.is_checked():
                # Algunos ATS ocultan el input, intentamos click en el label
                r_id = radio.get_attribute("id")
                label = page.query_selector(f"label[for='{r_id}']") if r_id else None
                if label and label.is_visible():
                    label.click()
                else:
                    radio.click()
                answered += 1
                micro_delay()
        except Exception:
            continue

    # Manejo de Checkboxes (Consentimiento)
    for cb in page.query_selector_all("input[type='checkbox']"):
        try:
            if not cb.is_checked() and cb.is_visible():
                cb.click()
                answered += 1
                micro_delay()
        except Exception:
            continue
    return answered


def fill_file_upload(page: Page, profile: dict) -> bool:
    """Sube el archivo de CV si se encuentra un campo de tipo file."""
    cv_path = profile.get("cv_path", "")
    if not cv_path or not Path(cv_path).exists():
        return False
    try:
        file_input = page.query_selector("input[type='file']")
        if file_input:
            file_input.set_input_files(cv_path)
            print(f"\n[!] CV SUBIDO CON ÉXITO")
            human_delay(1.0, 2.0)
            return True
    except Exception:
        pass
    return False


def fill_form(page: Page, profile: dict, job_title: str = "") -> dict:
    """
    Orquestador principal de llenado de formularios.
    Aplica el perfil contextual (IT o Bodega) para personalizar cover_letter.
    """
    human_delay(1.0, 2.0)

    # Determinar modo: explícito en profile["_mode"] o inferido del título del puesto
    mode = profile.get("_mode") or _detect_mode_from_title(job_title)
    kb = _load_kb()

    active_profile = dict(profile)
    if mode in kb:
        mode_data = kb[mode]
        if "cover_letter" in mode_data:
            active_profile["cover_letter"] = mode_data["cover_letter"]
        log.debug("  [form] modo=%s (título=%r)", mode, job_title[:50] if job_title else "")

    results = {
        "text_fields":   fill_text_fields(page, active_profile),
        "dropdowns":     fill_dropdowns(page, active_profile),
        "radio_answers": handle_yes_no_questions(page),
        "file_uploaded": fill_file_upload(page, active_profile),
    }

    log.debug("Resumen de formulario: %s", results)
    return results

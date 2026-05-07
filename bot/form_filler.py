"""
Autocompletado inteligente de formularios LinkedIn Easy Apply.

Detecta campos por name / id / placeholder / aria-label / label asociado
y aplica los valores de USER_PROFILE. Diseñado para el mercado chileno.

Campos cubiertos:
  - text / tel / email / number / textarea
  - select (dropdowns de screening)
  - radio (sí/no, disponibilidad, etc.)
  - file upload (CV)
"""
from pathlib import Path
from playwright.sync_api import Page
from .stealth_utils import micro_delay, human_delay
import logging

log = logging.getLogger("applyjob.form_filler")

# ---------------------------------------------------------------------------
# Patrones de detección  →  clave del perfil
# Cada lista contiene substrings que, si aparecen en cualquier atributo
# del campo (name / id / placeholder / aria-label / label-text), activan esa clave.
# ---------------------------------------------------------------------------
FIELD_PATTERNS: dict[str, list[str]] = {
    "full_name": [
        "fullname", "full_name", "nombre_completo", "nombre completo",
        "your name", "tu nombre",
    ],
    "first_name": [
        "firstname", "first_name", "nombre", "givenname", "given_name",
        "first name", "primer nombre",
    ],
    "last_name": [
        "lastname", "last_name", "apellido", "surname", "familyname",
        "last name",
    ],
    "email": [
        "email", "correo", "mail", "e-mail", "correo electrónico",
    ],
    "phone": [
        "phone", "telefono", "teléfono", "tel", "mobile", "celular", "móvil",
        "número de teléfono", "numero de telefono", "phone number",
    ],
    "city": [
        "city", "ciudad", "location", "ubicacion", "ubicación", "localidad",
        "ciudad de residencia",
    ],
    "linkedin": [
        "linkedin", "linkedin_url", "perfil linkedin", "profile url",
    ],
    "portfolio": [
        "portfolio", "website", "sitio web", "web", "github", "portafolio",
    ],
    "salary": [
        "salary", "salario", "pretension", "pretensión", "remuneracion",
        "remuneración", "sueldo", "renta", "renta líquida", "renta liquida",
        "expected salary", "desired salary", "wage", "compensation",
        "expectativa salarial", "expectativa de renta", "expectativas de renta",
        "expectativas salariales", "renta esperada", "renta pretendida",
        "pretension salarial", "pretensión salarial",
    ],
    "years_exp": [
        "años de experiencia", "anos de experiencia",
        "how many years", "cuántos años", "cuantos años",
        "nivel de experiencia", "years of experience",
        "years exp", "exp years", "yrs experience",
    ],
    "cover_letter": [
        "cover", "carta", "motivation", "presentacion", "presentación",
        "message", "mensaje", "sobre ti", "about you", "tell us",
        "cuéntanos", "cuentanos", "descripcion", "descripción",
        # Preguntas de screening que piden contexto personal
        "relaciona", "relacion", "perfil y experiencia", "perfil con",
        "por qué", "porque", "motiv", "interés", "interes",
        "cuéntanos", "cuéntame", "cuentanos", "habilidades", "skills",
        "fortaleza", "logro", "aporte", "valor",
    ],
    "availability": [
        "disponibilidad", "disponible", "cuando puedes", "cuándo puedes",
        "inicio", "fecha de inicio", "start date", "available from",
        "disponibilidad para comenzar", "cuando podrias", "cuándo podrías",
        "incorporacion", "incorporación",
    ],
    "english_level": [
        "ingles", "inglés", "english", "idioma", "language", "nivel de ingles",
        "nivel de inglés", "english level", "language level",
    ],
    "work_mode": [
        "presencial", "remoto", "hibrido", "híbrido", "modalidad",
        "trabajo presencial", "work mode", "remote", "on-site", "onsite",
        "dispuesto", "dispuesta", "presencial en santiago",
    ],
}

# Valores numéricos sin formato (LinkedIn espera enteros en inputs type=number)
NUMERIC_VALUES: dict[str, str] = {
    "salary":    "850000",   # sin puntos ni coma
    "years_exp": "0",
}

# Respuestas afirmativas para radios / dropdowns de screening
YES_VALUES: set[str] = {
    "yes", "si", "sí", "true", "1", "authorized",
    "yes, i am authorized", "i am authorized", "sí, estoy autorizado",
    "habilitado", "disponible",
}

# Respuestas a evitar en dropdowns
NO_VALUES: set[str] = {
    "no", "false", "0", "not authorized", "no estoy autorizado",
    "select an option", "selecciona una opción", "seleccionar",
    "choose", "elegir", "",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_textarea(el) -> bool:
    try:
        return el.evaluate("el => el.tagName.toLowerCase()") == "textarea"
    except Exception:
        return False


def _get_input_type(el) -> str:
    try:
        return (el.get_attribute("type") or "text").lower()
    except Exception:
        return "text"


def _get_label_text(page: Page, el) -> str:
    """
    Busca el texto de la etiqueta asociada al input.
    Maneja:
      - <label for="id">          (HTML estándar)
      - <label> contenedor padre  (Greenhouse, Lever)
      - div/span hermano anterior (SmartRecruiters, Workday)
    """
    try:
        field_id = el.get_attribute("id") or ""
        if field_id:
            label = page.query_selector(f"label[for='{field_id}']")
            if label:
                return (label.text_content() or "").strip()
        # Buscar vía JS: subir al contenedor más cercano y obtener texto de
        # etiquetas (label, div con clase label/field-label, span) hermanos/padres
        text = el.evaluate("""
            el => {
                // 1. <label> contenedor padre
                let cur = el.parentElement;
                for (let i = 0; i < 4; i++) {
                    if (!cur) break;
                    if (cur.tagName === 'LABEL') return cur.textContent.trim();
                    // 2. Hermano anterior con texto (div.label, span, p)
                    let sib = cur.previousElementSibling;
                    if (sib) {
                        const tag = sib.tagName.toLowerCase();
                        const cls = (sib.className || '').toLowerCase();
                        if (tag === 'label' || cls.includes('label') || cls.includes('field') || tag === 'p') {
                            const t = sib.textContent.trim();
                            if (t.length > 0 && t.length < 120) return t;
                        }
                    }
                    cur = cur.parentElement;
                }
                return '';
            }
        """)
        return (text or "").strip()
    except Exception:
        pass
    return ""


def _build_attrs(page: Page, el) -> str:
    """Concatena todos los atributos del campo para detección de patrón."""
    try:
        parts = [
            el.get_attribute("name") or "",
            el.get_attribute("id") or "",
            el.get_attribute("placeholder") or "",
            el.get_attribute("aria-label") or "",
            el.get_attribute("data-testid") or "",
            el.get_attribute("autocomplete") or "",
            _get_label_text(page, el),
        ]
        return " ".join(p for p in parts if p).lower()
    except Exception:
        return ""


def _match_field(attrs: str) -> str | None:
    for profile_key, patterns in FIELD_PATTERNS.items():
        if any(p in attrs for p in patterns):
            return profile_key
    return None


# ---------------------------------------------------------------------------
# Llenado de campos de texto / número / textarea
# ---------------------------------------------------------------------------

def fill_text_fields(page: Page, profile: dict) -> int:
    """
    Rellena todos los inputs/textareas visibles que reconoce.
    Incluye type=number (crucial para años de experiencia y salario).
    """
    filled = 0
    selectors = [
        "input[type='text']",
        "input[type='tel']",
        "input[type='email']",
        "input[type='number']",   # ← años de experiencia, salario
        "input:not([type])",
        "textarea",
    ]

    for sel in selectors:
        elements = page.query_selector_all(sel)
        for el in elements:
            try:
                if not el.is_visible() or not el.is_enabled():
                    continue

                attrs = _build_attrs(page, el)
                profile_key = _match_field(attrs)
                if not profile_key or profile_key not in profile:
                    continue

                # Para inputs type=number usar valor numérico limpio
                input_type = _get_input_type(el)
                if input_type == "number":
                    value = NUMERIC_VALUES.get(profile_key, str(profile[profile_key]))
                else:
                    value = str(profile[profile_key])

                # Leer valor actual
                if _is_textarea(el):
                    current = (el.text_content() or "").strip()
                else:
                    current = (el.input_value() or "").strip()

                if current:
                    continue  # no sobreescribir campos ya completos

                el.click()
                micro_delay()

                # Ciudad: usar type() real para activar autocomplete de ATS
                # (SmartRecruiters, Workday, Greenhouse requieren eventos de teclado)
                if profile_key == "city":
                    el.fill("")          # limpiar primero
                    micro_delay()
                    el.type(value, delay=40)  # simula escritura humana
                    human_delay(0.8, 1.2)     # espera sugerencias
                    # Seleccionar primera sugerencia si aparece
                    try:
                        page.keyboard.press("ArrowDown")
                        micro_delay()
                        page.keyboard.press("Enter")
                        micro_delay()
                    except Exception:
                        pass
                else:
                    el.fill(value)

                human_delay(0.3, 0.8)
                filled += 1
                log.debug("  fill_text: [%s] = '%s'", profile_key, value[:30])

            except Exception:
                continue

    return filled


# ---------------------------------------------------------------------------
# Llenado de selects (dropdowns de screening)
# ---------------------------------------------------------------------------

def fill_dropdowns(page: Page, profile: dict | None = None) -> int:
    """
    Responde selects de screening:
    1. Si el campo matchea un profile_key, busca opción que contenga el valor del perfil
    2. Prefiere valores afirmativos (yes/sí/authorized)
    3. Si no hay afirmativo, elige el primer valor que NO sea negativo/vacío
    4. Nunca selecciona valores de NO_VALUES
    """
    profile = profile or {}
    filled = 0
    try:
        selects = page.query_selector_all("select")
        for sel_el in selects:
            if not sel_el.is_visible():
                continue

            # No sobreescribir si ya tiene selección válida
            current = (sel_el.input_value() or "").strip()
            if current and current.lower() not in NO_VALUES:
                continue

            options = sel_el.query_selector_all("option")
            option_values = [
                (o.get_attribute("value") or "").strip()
                for o in options
            ]
            option_texts = [
                (o.text_content() or "").strip()
                for o in options
            ]

            chosen = None

            # Paso 0: intentar matchear con valor del perfil según patrón del campo
            attrs = _build_attrs(page, sel_el)
            profile_key = _match_field(attrs)
            if profile_key and profile_key in profile:
                profile_val = str(profile[profile_key]).lower()
                # Buscar opción cuyo texto o value contenga el valor del perfil
                for val, txt in zip(option_values, option_texts):
                    if profile_val in txt.lower() or profile_val in val.lower():
                        chosen = val
                        break
                # Si no encontró coincidencia exacta, buscar por primeras letras
                if not chosen:
                    for val, txt in zip(option_values, option_texts):
                        if val and val.lower() not in NO_VALUES:
                            combined = txt.lower() + val.lower()
                            if any(w in combined for w in profile_val.split()):
                                chosen = val
                                break

            # Paso 1: valor afirmativo explícito
            if not chosen:
                chosen = next(
                    (v for v in option_values if v.lower() in YES_VALUES), None
                )

            # Paso 2: primer valor que no sea negativo ni vacío
            if not chosen:
                chosen = next(
                    (v for v in option_values
                     if v and v.lower() not in NO_VALUES),
                    None
                )

            if chosen:
                sel_el.select_option(chosen)
                micro_delay()
                filled += 1
                log.debug("  fill_dropdown: [%s] selected '%s'",
                          profile_key or "?", chosen[:30])

    except Exception as exc:
        log.debug("fill_dropdowns error: %s", exc)

    return filled


# ---------------------------------------------------------------------------
# Manejo de radios (sí/no, disponibilidad)
# ---------------------------------------------------------------------------

def handle_yes_no_questions(page: Page) -> int:
    """
    Responde preguntas de sí/no seleccionando la opción afirmativa.
    Maneja tanto radios visibles (LinkedIn Easy Apply) como radios ocultos
    con label visual (Greenhouse, Lever).
    También maneja checkboxes de consentimiento (los marca).
    """
    answered = 0
    try:
        radios = page.query_selector_all("input[type='radio']")
        for radio in radios:
            label_val = (radio.get_attribute("value") or "").lower()
            if label_val not in YES_VALUES:
                continue
            try:
                if radio.is_checked():
                    continue
            except Exception:
                continue
            # Intentar click directo si es visible
            try:
                if radio.is_visible():
                    radio.click()
                    micro_delay()
                    answered += 1
                    continue
            except Exception:
                pass
            # Fallback: buscar <label> asociado y clickearlo (Greenhouse oculta el input)
            try:
                radio_id = radio.get_attribute("id") or ""
                if radio_id:
                    label = page.query_selector(f"label[for='{radio_id}']")
                    if label and label.is_visible():
                        label.click()
                        micro_delay()
                        answered += 1
                        continue
            except Exception:
                pass
            # Fallback JS: forzar check + disparar evento change
            try:
                page.evaluate("""
                    (el) => {
                        el.checked = true;
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        el.dispatchEvent(new Event('input',  {bubbles: true}));
                    }
                """, radio)
                micro_delay()
                answered += 1
            except Exception:
                pass
    except Exception as exc:
        log.debug("handle_yes_no error: %s", exc)

    # Checkboxes de consentimiento / términos
    try:
        checkboxes = page.query_selector_all("input[type='checkbox']")
        for cb in checkboxes:
            if not cb.is_visible():
                continue
            if not cb.is_checked():
                cb.click()
                micro_delay()
                answered += 1
    except Exception:
        pass

    return answered


# ---------------------------------------------------------------------------
# Upload de CV
# ---------------------------------------------------------------------------

def fill_file_upload(page: Page, profile: dict) -> bool:
    cv_path = profile.get("cv_path", "")
    if not cv_path or not Path(cv_path).exists():
        log.debug("fill_file_upload: CV no encontrado en %s", cv_path)
        return False
    try:
        # LinkedIn puede tener el input oculto — usar set_input_files directamente
        file_input = page.query_selector("input[type='file']")
        if file_input:
            file_input.set_input_files(cv_path)
            human_delay(1.0, 2.0)
            log.debug("fill_file_upload: CV subido")
            return True
    except Exception as exc:
        log.debug("fill_file_upload error: %s", exc)
    return False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def fill_form(page: Page, profile: dict) -> dict:
    """
    Ejecuta todas las estrategias de llenado en orden.
    Retorna resumen de acciones realizadas.
    """
    human_delay(0.8, 1.5)

    text   = fill_text_fields(page, profile)
    drops  = fill_dropdowns(page, profile)
    radios = handle_yes_no_questions(page)
    cv     = fill_file_upload(page, profile)

    log.debug("  fill_form: text=%d drops=%d radios=%d cv=%s",
              text, drops, radios, cv)
    return {
        "text_fields":   text,
        "dropdowns":     drops,
        "radio_answers": radios,
        "file_uploaded": cv,
    }

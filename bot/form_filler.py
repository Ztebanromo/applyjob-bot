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
import re
import time as _time
import unicodedata
from typing import Dict, List, Optional, Set, Any, Tuple

from playwright.sync_api import Page, ElementHandle

from .stealth_utils import (
    micro_delay, human_delay,
    human_type_field, scroll_to_and_pause, pre_form_pause,
    portal_action_delay,
)

log = logging.getLogger("applyjob.form_filler")

# ---------------------------------------------------------------------------
# Knowledge Base por modo (IT / Bodega)
# ---------------------------------------------------------------------------
_KB_PATH = Path(__file__).parent.parent / "data" / "profile_kb.json"
_PROFILE_KB: dict = {}

# ---------------------------------------------------------------------------
# Knowledge Base de preguntas/respuestas (pregunta -> respuesta conocida)
# ---------------------------------------------------------------------------
_QA_PATH        = Path(__file__).parent.parent / "data" / "question_answers.json"
_QA_CACHE_PATH  = Path(__file__).parent.parent / "data" / "qa_cache.json"
_PENDING_PATH   = Path(__file__).parent.parent / "data" / "pending_questions.json"


def _normalize(text: str) -> str:
    """Normaliza texto: minúsculas, sin tildes, sin espacios extra."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_text).strip()


def _load_qa() -> dict:
    """
    Carga y fusiona question_answers.json + qa_cache.json.
    qa_cache.json tiene precedencia (respuestas más ricas).
    Todas las claves se normalizan para comparación uniforme.
    """
    result: dict = {}

    # 1. question_answers.json (aprendidas en tiempo de ejecución — menor prioridad)
    if _QA_PATH.exists():
        try:
            with open(_QA_PATH, encoding="utf-8") as f:
                for k, v in json.load(f).items():
                    nk = _normalize(k)
                    if nk and v:
                        result[nk] = v
        except Exception:
            pass

    # 2. qa_cache.json (base curada manualmente — mayor prioridad)
    if _QA_CACHE_PATH.exists():
        try:
            with open(_QA_CACHE_PATH, encoding="utf-8") as f:
                for k, v in json.load(f).items():
                    # Saltar metadatos y separadores de sección
                    if not k or k.startswith("_") or k.startswith("─") or not v:
                        continue
                    nk = _normalize(k)
                    if nk:
                        result[nk] = v
        except Exception:
            pass

    return result


def _word_overlap(a: str, b: str) -> float:
    """Ratio de palabras compartidas entre dos strings normalizados (0.0–1.0)."""
    wa = set(a.split())
    wb = set(b.split())
    if not wa or not wb:
        return 0.0
    shared = wa & wb
    # Ignorar stopwords muy cortas que generan falsos positivos
    stopwords = {"de", "la", "el", "en", "y", "a", "con", "su", "tu", "un", "una",
                 "es", "se", "si", "no", "para", "que", "por", "al", "del", "lo"}
    shared -= stopwords
    wa -= stopwords
    wb -= stopwords
    if not wa or not wb:
        return 0.0
    return len(shared) / max(len(wa), len(wb))


def _match_qa(label: str) -> Optional[str]:
    """
    Busca coincidencia en question_answers.json para el label dado.
    Prioridad: exacta > substring largo (>=15 chars) > overlap de palabras >= 60%.

    Regla de especificidad: si la pregunta menciona una tecnología concreta
    (python, sql, java, excel…) solo se acepta un match que también la mencione.
    Evita que "cuántos años de experiencia en Python" matchee con la entrada
    genérica "cuántos años de experiencia tienes -> 0".
    """
    qa = _load_qa()
    norm = _normalize(label)

    # Tecnologías que deben coincidir en ambos lados del match
    _TECH_WORDS = {
        "python", "sql", "java", "javascript", "js", "php", "ruby", "go",
        "csharp", "net", "react", "angular", "nodejs", "html", "css",
        "excel", "power bi", "powerbi", "tableau", "sap", "wms", "erp",
        "azure", "aws", "gcp", "docker", "kubernetes", "linux", "git",
        "jira", "scrum", "agile", "itil", "dynamics", "salesforce",
    }
    # Tecnologías que aparecen en la pregunta
    label_techs = {t for t in _TECH_WORDS if t in norm}

    def _tech_compatible(key_norm: str) -> bool:
        """True si la clave del cache es compatible con las techs del label."""
        if not label_techs:
            return True   # pregunta genérica -> cualquier clave ok
        key_techs = {t for t in _TECH_WORDS if t in key_norm}
        if not key_techs:
            return False  # pregunta tiene tech específica pero clave es genérica
        return bool(label_techs & key_techs)  # al menos una tech en común

    # 1. Coincidencia exacta
    if norm in qa:
        return qa[norm]

    # 2. Substring solo si la clave es suficientemente larga (>=15 chars)
    # Las claves de _load_qa() ya están normalizadas — no re-normalizar
    for k, val in qa.items():
        if not k or k.startswith("-") or k.startswith("_"):
            continue
        if len(k) >= 15 and k in norm and _tech_compatible(k):
            return val
        if len(norm) >= 15 and norm in k and _tech_compatible(k):
            return val

    # 3. Overlap de palabras >= 60% con compatibilidad de techs
    best_val = None
    best_score = 0.0
    for k, val in qa.items():
        if not k or k.startswith("-") or k.startswith("_"):
            continue
        if not _tech_compatible(k):
            continue
        score = _word_overlap(norm, k)
        if score > best_score:
            best_score = score
            best_val = val
    if best_score >= 0.60:
        return best_val
    return None


def _auto_answer(label: str, profile: dict) -> Optional[str]:
    """
    Genera una respuesta automática para preguntas desconocidas analizando el label.
    Usa el perfil del usuario como fuente de verdad. Si no puede inferir nada útil
    devuelve None (el campo queda pendiente para el usuario).

    Orden de prioridad de reglas (de más específica a más general):
      1. Salario / renta
      2. Disponibilidad / incorporación
      3. Modalidad presencial / remoto
      4. Formación académica / título
      5. Contacto / ubicación
      6. Experiencia bodega / logística
      7. Excel / Office
      8. Habilidades TI específicas (Python, SQL, SAP, Git…)
      9. Soporte técnico / helpdesk
      10. Preguntas de identidad (RUT, ciudadanía, discapacidad…)
      11. Motivación / presentación
      12. Preguntas de "no tengo experiencia" (catch-all técnico)
    """
    n = _normalize(label)

    def _has(*keywords) -> bool:
        return any(k in n for k in keywords)

    # -- 1. Salario ------------------------------------------------------------
    if _has("salario", "renta", "sueldo", "remuneracion", "pretension", "cuanto quieres ganar",
            "expectativa", "pretensiones"):
        return profile.get("salary", "850000")

    # -- 2. Disponibilidad / incorporación -------------------------------------
    if _has("disponibilidad", "incorporar", "cuando puedes", "cuando podrias empezar",
            "cuando puedes empezar", "fecha de inicio", "start date"):
        return "Inmediata."

    # -- 3. Modalidad presencial / remoto --------------------------------------
    if _has("presencial", "hibrido", "remoto", "modalidad", "teletrabajo", "home office",
            "trabajo desde casa"):
        return "Sí, tengo disponibilidad para trabajar de forma presencial o híbrida en Santiago."

    # -- 4. Formación académica / título ---------------------------------------
    if _has("formacion", "titulo", "carrera", "estudios", "egresado", "titulacion",
            "casa de estudios", "institucion", "universidad", "instituto", "nivel educacional"):
        return profile.get("education", "Técnico o profesional del área, egresado reciente.")

    # -- 5. Contacto / ubicación -----------------------------------------------
    if _has("contacto", "telefono", "correo", "email", "comuna", "residencia", "donde vives"):
        return profile.get("contact_info",
                           f"{profile.get('phone','')} | {profile.get('email','')}")

    # -- 6a. Facturación / documentos tributarios (va ANTES de bodega) --------
    if _has("factura", "guia de despacho", "nota de credito", "nota de debito",
            "documento tributario", "boleta", "hes", "oc "):
        return ("No tengo experiencia directa en confección de documentos tributarios "
                "(facturas, guías de despacho, notas de crédito), pero tengo disposición "
                "para aprender este proceso.")

    # -- 6b. Bodega / logística / WMS -----------------------------------------
    if _has("bodega", "picking", "recepcion mercaderia", "inventario",
            "wms", "terminal rf", "logistica", "almacen", "stock"):
        return profile.get("bodega_exp",
            "Sí, experiencia en operaciones de bodega: SAP WM, WMS, terminales RF, "
            "picking, despacho y recepción de mercadería.")

    # -- 7. Excel / Office -----------------------------------------------------
    if _has("excel", "office", "buscarv", "tablas dinamicas", "planilla"):
        return profile.get("excel_level",
            "Manejo Excel a nivel intermedio: tablas dinámicas, BUSCARV y fórmulas condicionales. 6/10.")

    # -- 8. Habilidades TI específicas -----------------------------------------
    if _has("python"):
        return ("Manejo Python a nivel básico-intermedio: scripting, automatización, "
                "manejo de archivos y consumo de APIs.")
    if _has("sql", "base de datos", "bases de datos"):
        return ("Sí, SQL básico-intermedio: SELECT, JOIN, GROUP BY, subconsultas y diseño "
                "relacional. Aplicado en proyectos propios.")
    if _has("sap"):
        return ("Sí, usuario de SAP WM (módulo de gestión de almacenes). "
                "Uso operativo, no implementador.")
    if _has("git"):
        return ("Sí, uso Git para control de versiones: commit, push, pull, branches. "
                "Aplicado en proyectos personales y de formación.")
    if _has("html", "css", "frontend", "web"):
        return ("HTML5 y CSS3 a nivel básico-intermedio. "
                "Desarrollé interfaces web durante mi formación técnica.")
    if _has("javascript", "js"):
        return ("Conocimientos básicos de JavaScript: DOM, funciones y eventos. "
                "Adquiridos durante mi formación técnica.")
    if _has("ingles", "english", "idioma"):
        return profile.get("english_level",
            "Básico técnico. Leo documentación en inglés sin problemas; comunicación oral limitada.")
    if _has("power bi", "tableau", "visualizacion"):
        return ("No tengo experiencia formal con Power BI ni Tableau. "
                "Manejo Excel con tablas dinámicas para análisis de datos.")
    if _has("power automate", "power platform", "automatizacion de procesos"):
        return ("No tengo experiencia con Power Automate. "
                "Manejo automatización con Python en proyectos propios.")
    if _has("azure", "aws", "cloud", "gcp"):
        return ("No tengo experiencia práctica en cloud. "
                "Conozco los conceptos básicos a nivel teórico.")
    if _has("docker", "kubernetes", "contenedor"):
        return ("No tengo experiencia práctica con Docker o Kubernetes. "
                "Los conozco a nivel conceptual.")
    if _has("java", "c#", ".net", "php", "ruby", "go lang"):
        return "No tengo experiencia con este lenguaje. Mi stack principal es Python y SQL."
    if _has("scrum", "agile", "metodologia", "kanban"):
        return ("Conozco los principios de Scrum y metodologías ágiles a nivel teórico. "
                "Sin experiencia formal aún.")
    if _has("linux", "unix"):
        return ("Conocimientos básicos de Linux: comandos de terminal, navegación y permisos.")
    if _has("red", "redes", "tcp", "protocolo", "networking"):
        return ("Conocimientos básicos de redes TCP/IP y modelo OSI "
                "adquiridos durante mi formación técnica.")

    # -- 9. Soporte técnico ---------------------------------------------------
    if _has("soporte", "helpdesk", "mesa de ayuda", "tickets", "usuarios"):
        return ("No tengo experiencia formal en soporte técnico, pero cuento con base técnica "
                "en hardware, sistemas operativos y redes, y facilidad para comunicarme con usuarios.")

    # -- 10. Identidad / condiciones -------------------------------------------
    if _has("rut", "ciudadano", "chileno", "visa", "permiso de trabajo", "nacionalidad"):
        return "Sí, soy chileno con RUT vigente."
    if _has("discapacidad", "condicion medica"):
        return "No."
    if _has("acepto", "autorizo", "terminos", "condiciones", "tratamiento de datos", "acepta"):
        return "Sí, acepto."
    if _has("movilizacion", "auto", "vehiculo", "licencia de conducir", "carnet"):
        return ("No cuento con movilización propia, pero me desplazo sin inconvenientes "
                "en transporte público por Santiago RM.")

    # -- 11. Motivación / presentación ----------------------------------------
    if _has("por que te interesa", "por que le interesa", "por que quieres", "por que desea",
            "motivacion", "cuéntanos sobre ti", "cuentanos sobre ti",
            "sobre ti", "presentate", "presentacion", "why do you",
            "interesa este cargo", "interesa esta posicion", "interesa trabajar"):
        return profile.get("cover_letter", "")

    # -- 12. Catch-all: experiencia desconocida --------------------------------
    if _has("experiencia", "conocimiento", "manejo", "habilidad", "trabaj"):
        return ("No cuento con experiencia formal en esta área específica, "
                "pero tengo disposición para aprender y adaptarme rápidamente.")

    # Sin patrón reconocido -> no inventar
    return None


_NOISE_PREFIXES = (
    "filtrar",        # filtros de búsqueda: "Filtrar resultados por: ..."
    "ordenar",        # ordenar por fecha, relevancia, etc.
    "mostrar",        # "Mostrar solo empleos de..."
    "buscar",         # campo de búsqueda general
    "notificaci",     # "Notificación automática de nuevos empleos"
    "recibir boletin",
    "recibirrecibir", # duplicado del portal (bug de captura)
    "suscrib",        # suscribirse a alertas
    "alerta",         # "Configurar alerta de empleo"
)

_NOISE_PATTERNS = (
    " kb ",           # nombre de archivo con tamaño: "cv.pdf 52 kb usado..."
    ".pdf",           # archivos pdf
    ".docx",          # archivos word
    "usado por ultima vez",   # metadato de archivo subido
    "last used",
    "kb ·",
)


def _is_noise_label(label: str, norm: str) -> bool:
    """
    Retorna True si la etiqueta es ruido de la UI (no es una pregunta real del formulario).
    Filtra: filtros de búsqueda, nombres de archivos de CV, metadatos de upload, alertas.
    """
    n = norm.lower()
    l = label.lower()

    # Muy corta y sin signo de pregunta → probablemente un label de campo genérico
    # (pero no filtrar labels legítimos cortos como "Fech. Nac.")
    if len(norm) < 6:
        return True

    for prefix in _NOISE_PREFIXES:
        if n.startswith(prefix):
            return True

    for pattern in _NOISE_PATTERNS:
        if pattern in n or pattern in l:
            return True

    return False


def _save_pending_question(label: str, norm: str, portal: str = "", url: str = "") -> None:
    """Guarda una pregunta desconocida en pending_questions.json (sin duplicados).
    Filtra automáticamente ruido de UI: filtros de búsqueda, nombres de archivos, etc.
    Si puede inferir una respuesta automáticamente la guarda directo en qa_cache.json
    y no añade la pregunta a pending."""

    # Ignorar entradas que son ruido de la interfaz, no preguntas reales
    if _is_noise_label(label, norm):
        log.debug("[PENDING_SKIP_NOISE] '%s'", label[:60])
        return

    # Intentar responder automáticamente antes de guardar como pendiente
    auto_ans = _match_qa(label)
    if not auto_ans:
        try:
            from .config import USER_PROFILE as _UP
            auto_ans = _auto_answer(label, _UP)
        except Exception:
            auto_ans = None

    if auto_ans:
        # Persistir en qa_cache.json para que esté disponible en futuras postulaciones
        cache: dict = {}
        if _QA_CACHE_PATH.exists():
            try:
                with open(_QA_CACHE_PATH, encoding="utf-8") as f:
                    cache = json.load(f)
            except Exception:
                cache = {}
        if norm not in cache:
            cache[norm] = auto_ans
            _QA_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_QA_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
            log.info("[AUTO_CACHE] '%s' -> '%s'", label[:60], str(auto_ans)[:80])
        return

    pending: List[dict] = []
    if _PENDING_PATH.exists():
        try:
            with open(_PENDING_PATH, encoding="utf-8") as f:
                pending = json.load(f)
        except Exception:
            pending = []

    # Evitar duplicados por norm
    for entry in pending:
        if entry.get("norm") == norm:
            return

    pending.append({
        "label":    label,
        "norm":     norm,
        "portal":   portal,
        "url":      url,
        "answered": False,
        "answer":   "",
        "ts":       _time.time(),
    })
    _PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_PENDING_PATH, "w", encoding="utf-8") as f:
        json.dump(pending, f, ensure_ascii=False, indent=2)


def _save_qa_answer(norm: str, answer: str) -> None:
    """Persiste una respuesta aprendida en question_answers.json."""
    qa = _load_qa()
    qa[norm] = answer
    _QA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_QA_PATH, "w", encoding="utf-8") as f:
        json.dump(qa, f, ensure_ascii=False, indent=2)


def _fill_element_with_value(page: Page, el, kind: str, value: str) -> bool:
    """Rellena un elemento (input/select/textarea) con el valor dado."""
    try:
        if kind == "select":
            opts = el.query_selector_all("option")
            v_low = value.lower()
            for opt in opts:
                o_text = (opt.text_content() or "").lower()
                if v_low in o_text or o_text in v_low:
                    chosen = opt.get_attribute("value")
                    if chosen:
                        el.select_option(chosen)
                        return True
        else:
            el.click()
            micro_delay()
            el.fill(value)
            return True
    except Exception:
        pass
    return False


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
        "ciudad de residencia", "donde vives", "comuna", "región", "region",
        "comuna de residencia", "indique su comuna", "lugar de residencia",
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
        "cover letter", "cover_letter",
        "carta de presentacion", "carta de presentación",
        "carta de motivacion", "carta de motivación",
        "presentacion personal", "presentación personal",
        "sobre ti", "about yourself",
        "cuéntanos sobre ti", "cuentanos sobre ti",
        "por qué quieres trabajar", "por que quieres trabajar",
        "why do you want to work",
        "mensaje de presentacion", "mensaje de presentación",
        "motivacion personal", "motivación personal",
    ],
    "education": [
        "formacion academica", "formación académica",
        "titulo profesional", "título profesional",
        "casa de estudios", "titulacion", "titulación",
        "carrera cursada", "institucion educativa",
        "mencione su formacion", "indique su formacion",
        "formacion superior", "formación superior",
        "titulo y casa de estudios", "título y casa de estudios",
        "indique titulo", "indique título",
        "año de titulacion", "año de titulación",
        "cursos relacionados al cargo",
    ],
    "contact_info": [
        "telefono y correo", "teléfono y correo",
        "datos de contacto", "contacto actualizado",
        "correo y telefono", "correo y teléfono",
        "datos personales de contacto",
    ],
    "excel_level": [
        "manejo de excel", "nivel de excel", "excel para reportes",
        "calificaría su manejo de excel", "escala del 1 al 10",
        "conoce buscarv", "tablas dinamicas", "tablas dinámicas",
    ],
    "bodega_exp": [
        "experiencia en bodega", "experiencia bodega",
        "experiencia en inventarios", "cierres operativos",
        "picking y despacho", "recepcion de mercaderia",
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
    - País / country -> busca "Chile" o "CL" antes que cualquier otro valor
    - Código de país / phone prefix -> busca "+56" o "56" (Chile)
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

            # -- Caso especial: dropdown de PAÍS -----------------------------
            if any(t in attrs for t in _COUNTRY_ATTRS):
                for opt in options:
                    o_text = (opt.text_content() or "").strip().lower()
                    o_val  = (opt.get_attribute("value") or "").strip().lower()
                    if any(t in o_text or t == o_val for t in _CHILE_TERMS):
                        chosen_val = opt.get_attribute("value")
                        log.debug("  [form] país -> Chile (%r)", chosen_val)
                        break

            # -- Caso especial: dropdown de CÓDIGO DE PAÍS / prefijo ----------
            elif any(t in attrs for t in _PHONE_PREFIX_ATTRS):
                for opt in options:
                    o_text = (opt.text_content() or "").strip()
                    o_val  = (opt.get_attribute("value") or "").strip()
                    if any(c in o_text or c == o_val for c in _CHILE_CODE):
                        chosen_val = opt.get_attribute("value")
                        log.debug("  [form] código de país -> +56 (%r)", chosen_val)
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


def _get_radio_group_label(page: Page, radio_el) -> str:
    """
    Busca el label de pregunta que agrupa al radio button.
    Intenta: fieldset>legend, aria-labelledby, role=group, sibling anterior.
    """
    try:
        result = page.evaluate("""el => {
            const fs = el.closest('fieldset');
            if (fs) { const lg = fs.querySelector('legend'); if (lg) return lg.innerText.trim(); }
            const labelId = el.getAttribute('aria-labelledby');
            if (labelId) { const lel = document.getElementById(labelId); if (lel) return lel.innerText.trim(); }
            const grp = el.closest('[role=group]');
            if (grp) {
                const al = grp.getAttribute('aria-label');
                if (al) return al.trim();
                const lg2 = grp.querySelector('legend,h3,h4,p,span');
                if (lg2) return lg2.innerText.trim();
            }
            const parent = el.parentElement;
            if (parent) {
                let sib = parent.previousElementSibling;
                while (sib) {
                    const t = sib.innerText ? sib.innerText.trim() : '';
                    if (t.length > 8) return t;
                    sib = sib.previousElementSibling;
                }
            }
            return '';
        }""", radio_el)
        return (result or "").strip()[:120]
    except Exception:
        return ""


def _radio_answer_to_si_no(answer: str) -> str:
    """
    Convierte una respuesta de texto a 'si' o 'no'.
    Retorna '' si no se puede determinar.
    """
    if not answer:
        return ""
    low = answer.strip().lower()
    negatives = ["no tengo", "no cuento", "no poseo", "no dispongo",
                 "sin experiencia", "no formal", "nunca", "no tengo"]
    if any(w in low for w in negatives):
        return "no"
    if any(w in low for w in ["si,", "si.", "si tengo", "si cuento", "si poseo",
                               "cuento con", "tengo experiencia", "poseo", "dispongo"]):
        return "si"
    if low.strip() in ("si", "yes", "true", "1", "s"):
        return "si"
    if low.strip() in ("no", "false", "0", "n"):
        return "no"
    if low.startswith("no ") or low == "no.":
        return "no"
    if low.startswith("si ") or low == "si.":
        return "si"
    return ""


def handle_yes_no_questions(page: Page, profile: dict | None = None) -> int:
    """
    Maneja radios Si/No leyendo la pregunta y consultando cache + auto_answer.
    Solo clickea cuando tiene certeza de la respuesta correcta.
    Si no sabe -> guarda como pendiente y NO toca el radio.
    """
    if profile is None:
        profile = {}
    answered     = 0
    groups_seen: set = set()

    for radio in page.query_selector_all("input[type='radio']"):
        try:
            name = radio.get_attribute("name") or ""
            if name in groups_seen:
                continue
            groups_seen.add(name)

            question_label = _get_radio_group_label(page, radio)
            if not question_label:
                continue

            # Determinar respuesta correcta via cache o auto_answer
            raw_answer = _match_qa(question_label) or _auto_answer(question_label, profile)
            si_no = _radio_answer_to_si_no(raw_answer or "")

            if not si_no:
                norm = _normalize(question_label)
                _save_pending_question(question_label, norm)
                log.warning("[RADIO_PENDIENTE] %s", question_label[:80])
                continue

            # Guardar en cache si vino de auto_answer
            if not _match_qa(question_label) and raw_answer:
                _save_qa_answer(_normalize(question_label), raw_answer)

            # Seleccionar la opcion correcta del grupo
            target_values = YES_VALUES if si_no == "si" else {"no", "false", "0", "n"}
            all_in_group  = page.query_selector_all(f"input[type='radio'][name='{name}']")

            clicked = False
            for r in all_in_group:
                val        = (r.get_attribute("value") or "").strip().lower()
                r_id       = r.get_attribute("id")
                label_text = ""
                if r_id:
                    lbl = page.query_selector(f"label[for='{r_id}']")
                    if lbl:
                        label_text = (lbl.text_content() or "").strip().lower()

                is_match = val in target_values or label_text in target_values
                if not is_match:
                    continue

                if r_id:
                    lbl = page.query_selector(f"label[for='{r_id}']")
                    if lbl:
                        try:
                            lbl.scroll_into_view_if_needed()
                            lbl.click()
                            clicked = True
                        except Exception:
                            pass
                if not clicked:
                    try:
                        if r.is_visible():
                            r.click()
                            clicked = True
                    except Exception:
                        pass
                if not clicked:
                    try:
                        page.evaluate("""el => {
                            el.checked = true;
                            ['change','click','input'].forEach(e =>
                                el.dispatchEvent(new Event(e, {bubbles:true})));
                        }""", r)
                        clicked = True
                    except Exception:
                        pass
                break

            if clicked:
                answered += 1
                log.info("  [RADIO] '%s' -> %s", question_label[:60], si_no.upper())
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
    """
    Sube el CV al primer input[type=file] encontrado.
    Maneja inputs ocultos (display:none / visibility:hidden) forzando visibilidad vía JS.
    Registra error explícito si falla para facilitar diagnóstico.
    """
    import logging as _log
    log = _log.getLogger("applyjob.form_filler")

    cv_path = profile.get("cv_path", "")
    if not cv_path:
        log.warning("[CV] cv_path no configurado en el perfil")
        return False
    if not Path(cv_path).exists():
        log.warning("[CV] Archivo no encontrado: %s", cv_path)
        return False

    # Buscar todos los inputs file (visibles u ocultos)
    inputs = page.query_selector_all("input[type='file']")
    if not inputs:
        log.debug("[CV] No hay input[type=file] en la página")
        return False

    for file_input in inputs:
        try:
            # Forzar visibilidad vía JS para que Playwright pueda interactuar
            page.evaluate(
                "(el) => { el.style.display='block'; el.style.visibility='visible';"
                " el.style.opacity='1'; el.removeAttribute('hidden'); }",
                file_input,
            )
            file_input.set_input_files(cv_path)
            # Disparar eventos para que el ATS detecte el cambio
            page.evaluate("(el) => { el.dispatchEvent(new Event('change', {bubbles:true}));"
                          " el.dispatchEvent(new Event('input', {bubbles:true})); }", file_input)
            log.info("[CV] CV adjuntado: %s", cv_path)
            print(f"  [CV] CV subido correctamente")
            human_delay(1.0, 2.0)
            return True
        except Exception as exc:
            log.warning("[CV] set_input_files falló en input %d: %s", inputs.index(file_input), exc)
            continue

    log.warning("[CV] No se pudo adjuntar el CV en ningún input encontrado")
    return False


def _collect_unfilled_elements(page: Page) -> List[Tuple[Any, str, str]]:
    """
    Detecta campos visibles sin patrón conocido y vacíos.
    Devuelve lista de (ElementHandle, label_text, kind).
    """
    results: List[Tuple[Any, str, str]] = []
    seen: Set[str] = set()

    selectors = [
        ("input[type='text']", "text"),
        ("input[type='number']", "number"),
        ("textarea", "textarea"),
        ("select", "select"),
    ]
    for sel, kind in selectors:
        for el in page.query_selector_all(sel):
            try:
                if not el.is_visible() or not el.is_enabled():
                    continue
                attrs = _build_attrs(page, el)
                label = _get_label_text(page, el)
                if not label or len(label) < 4:
                    continue
                if _match_field(attrs):
                    continue

                if kind == "select":
                    val = el.evaluate("el => el.value || ''") or ""
                    empty_vals = {"", "0", "-1", "selecciona", "select", "choose", "elegir"}
                    if val.lower() not in empty_vals and val not in empty_vals:
                        continue
                else:
                    try:
                        val = el.input_value() if kind != "textarea" else (el.text_content() or "")
                    except Exception:
                        val = ""
                    if (val or "").strip():
                        continue

                key = label[:80].strip()
                if key not in seen:
                    seen.add(key)
                    results.append((el, key, kind))
            except Exception:
                continue
    return results


def collect_unanswered_fields(page: Page) -> List[str]:
    """Devuelve lista de textos de label de campos sin respuesta conocida (para diagnóstico)."""
    return [label for _, label, _ in _collect_unfilled_elements(page)]


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
        "radio_answers": handle_yes_no_questions(page, active_profile),
        "file_uploaded": fill_file_upload(page, active_profile),
    }

    # -- Manejar campos desconocidos: QA cache -> auto-respuesta -> usuario -> skip --
    #
    # Prioridad de resolución para cada campo sin patrón conocido:
    #   1. qa_cache / question_answers.json  -> respuesta ya aprendida
    #   2. _auto_answer()                    -> inferencia por palabras clave del label
    #   3. Espera 30 s al usuario (panel naranja)
    #   4. Si no llega respuesta -> log y sigue (campo vacío)
    #
    # Toda respuesta nueva (auto o del usuario) se guarda en qa_cache para
    # que la próxima vez se resuelva en el paso 1 sin preguntar nada.
    # --------------------------------------------------------------------------
    unfilled = _collect_unfilled_elements(page)
    element_map: dict = {}   # norm -> (el, label, kind)
    pending_norms: List[str] = []
    manual_required: List[str] = []

    for el, label, kind in unfilled:
        norm = _normalize(label)

        # -- 1. QA cache (respuesta ya conocida) ------------------------------
        qa_answer = _match_qa(label)
        if qa_answer:
            ok = _fill_element_with_value(page, el, kind, qa_answer)
            if ok:
                log.info("  [QA-cache] %s -> %s", label[:60], qa_answer[:40])
                continue

        # -- 2. Auto-respuesta por palabras clave del label -------------------
        auto = _auto_answer(label, active_profile)
        if auto:
            ok = _fill_element_with_value(page, el, kind, auto)
            if ok:
                _save_qa_answer(norm, auto)          # aprender para siempre
                log.info("  [AUTO] %s -> %s", label[:60], auto[:60])
                print(f"  [AUTO] {label[:70]} -> {auto[:60]}")
                continue

        # -- 3. Desconocida: guardar como pendiente, esperar al usuario --------
        _save_pending_question(label, norm)
        log.warning("[PREGUNTA_PENDIENTE] %s", label)
        print(f"[PREGUNTA_PENDIENTE] {label}")
        pending_norms.append(norm)
        element_map[norm] = (el, label, kind)

    # Esperar hasta 30 s (reducido — auto_answer cubre la mayoría)
    if pending_norms:
        deadline = _time.time() + 30
        remaining = set(pending_norms)
        while remaining and _time.time() < deadline:
            if _PENDING_PATH.exists():
                try:
                    with open(_PENDING_PATH, encoding="utf-8") as f:
                        data = json.load(f)
                    for entry in data:
                        n = entry.get("norm", "")
                        if n in remaining and entry.get("answered") and entry.get("answer"):
                            answer = entry["answer"]
                            el, label, kind = element_map[n]
                            _fill_element_with_value(page, el, kind, answer)
                            _save_qa_answer(n, answer)   # aprender
                            log.info("  [USER] Respuesta aprendida: %s -> %s", label, answer)
                            remaining.discard(n)
                except Exception:
                    pass
            if remaining:
                _time.sleep(3)

        # Las que siguen sin respuesta: registrar — NO se postulará
        for n in remaining:
            _, label, _ = element_map[n]
            print(f"[SIN_RESPUESTA] {label} — postulacion cancelada")
            log.warning("[SIN_RESPUESTA] Campo sin respuesta: %s — se omitira esta oferta", label)
            manual_required.append(label)

    results["unanswered"] = len(manual_required)
    results["unanswered_labels"] = manual_required  # para el log en engine
    log.debug("Resumen de formulario: %s", results)
    return results


def scan_form(page: Page, profile: dict, job_title: str = "") -> dict:
    """
    Escanea campos del formulario SIN rellenar nada en la pagina.
    Verifica cuales preguntas tienen respuesta en cache o auto_answer.

    Usado en pasada 1 (--scan) para decidir si la oferta va a la cola
    o se puede aplicar directamente.

    Returns:
        {
          "all_answered": bool,       # True si todas las preguntas tienen respuesta
          "unanswered":   list[str],  # labels sin respuesta
          "answered_count": int,      # cuantas si tienen respuesta
        }
    """
    mode = profile.get("_mode") or _detect_mode_from_title(job_title)
    kb   = _load_kb()
    active_profile = dict(profile)
    if mode in kb and "cover_letter" in kb[mode]:
        active_profile["cover_letter"] = kb[mode]["cover_letter"]

    unfilled   = _collect_unfilled_elements(page)
    unanswered: List[str] = []
    answered   = 0

    # -- Campos de texto / select / number --
    for el, label, kind in unfilled:
        if _match_qa(label) or _auto_answer(label, active_profile):
            answered += 1
        else:
            norm = _normalize(label)
            _save_pending_question(label, norm)
            unanswered.append(label)

    # -- Radios Si/No --
    groups_seen: set = set()
    for radio in page.query_selector_all("input[type='radio']"):
        try:
            name = radio.get_attribute("name") or ""
            if name in groups_seen:
                continue
            groups_seen.add(name)
            question_label = _get_radio_group_label(page, radio)
            if not question_label:
                continue
            raw = _match_qa(question_label) or _auto_answer(question_label, active_profile)
            if _radio_answer_to_si_no(raw or ""):
                answered += 1
            else:
                norm = _normalize(question_label)
                _save_pending_question(question_label, norm)
                unanswered.append(f"[radio] {question_label}")
        except Exception:
            continue

    return {
        "all_answered":   len(unanswered) == 0,
        "unanswered":     unanswered,
        "answered_count": answered,
    }

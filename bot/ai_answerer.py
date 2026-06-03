"""
ai_answerer.py — Responde preguntas de formularios ATS usando la API de Anthropic.

Cuándo se llama:
  - form_filler.py no encontró respuesta en question_answers.json
  - La pregunta es un texto abierto (textarea) que requiere respuesta personalizada

Configuración:
  ANTHROPIC_API_KEY=sk-ant-... en .env

El resultado se guarda automáticamente en data/question_answers.json
para no volver a llamar a la API con la misma pregunta.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

_QA_PATH = Path(__file__).parent.parent / "data" / "question_answers.json"

# Prompt del perfil del usuario — se construye una vez al importar
def _build_profile_context() -> str:
    from bot.config import USER_PROFILE
    p = USER_PROFILE
    return f"""
Eres el candidato {p.get('full_name', 'Ignacio Romo')}.

PERFIL:
- Formación: {p.get('education', 'Analista Programador, INACAP, egresado 2024')}
- Experiencia: {p.get('years_exp', '6')} años (logística/retail + TI)
- Skills TI: Python, SQL, JavaScript, HTML/CSS, Git, SAP WM, WMS
- Exp logística: 2 años en STL Internacional y Ripley (SAP WM, WMS, RF Terminal, picking, despacho)
- Ciudad: {p.get('city', 'Maipú, Santiago')}
- Disponibilidad: {p.get('availability', 'Inmediata')}
- Inglés: {p.get('english_level', 'Básico técnico')}
- Trabajo presencial: Sí
- Movilización propia: No
- Carta de presentación: {p.get('cover_letter', '')[:300]}
""".strip()


def auto_answer(question: str, job_title: str = "", portal: str = "") -> str | None:
    """
    Llama a la API de Anthropic para responder una pregunta de formulario ATS.

    Args:
        question: Texto de la pregunta del formulario
        job_title: Título del cargo (contexto adicional)
        portal: Portal donde aparece la pregunta

    Returns:
        Respuesta como string, o None si la API no está configurada / falla.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        log.debug("[AI_ANSWERER] ANTHROPIC_API_KEY no configurada — saltando auto-respuesta.")
        return None

    try:
        import urllib.request
        import urllib.error
        profile_ctx = _build_profile_context()

        system_prompt = f"""Eres un candidato respondiendo preguntas de formularios de postulación laboral.
Responde SIEMPRE en español, de forma concisa (1-3 oraciones máximo), honesta y profesional.
Si no tienes experiencia en algo, dilo brevemente. Nunca inventes.
Si la respuesta es un número, devuelve solo el número.

{profile_ctx}"""

        user_msg = f"Cargo al que postulo: {job_title or 'no especificado'}\nPortal: {portal or 'no especificado'}\n\nPregunta del formulario:\n{question}"

        payload = json.dumps({
            "model": "claude-haiku-4-5",
            "max_tokens": 150,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_msg}]
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            answer = data["content"][0]["text"].strip()

        if answer:
            _save_to_cache(question, answer)
            log.info("[AI_ANSWERER] Pregunta respondida y guardada: %s → %s", question[:60], answer[:60])
        return answer

    except Exception as exc:
        log.warning("[AI_ANSWERER] Error llamando a la API: %s", exc)
        return None


def _save_to_cache(question: str, answer: str) -> None:
    """Guarda la respuesta en question_answers.json para uso futuro."""
    try:
        from bot.form_filler import _normalize
        key = _normalize(question)
    except Exception:
        key = question.lower().strip()

    try:
        cache = {}
        if _QA_PATH.exists():
            cache = json.loads(_QA_PATH.read_text(encoding="utf-8"))
        cache[key] = answer
        _QA_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("[AI_ANSWERER] Error guardando en cache: %s", exc)


def answer_pending_questions() -> dict[str, str]:
    """
    Lee data/pending_questions.json, responde las no respondidas con la API
    y las marca como answered. Retorna {pregunta: respuesta}.

    Llamar desde el dashboard o desde CLI:
        python -c "from bot.ai_answerer import answer_pending_questions; print(answer_pending_questions())"
    """
    pq_path = Path(__file__).parent.parent / "data" / "pending_questions.json"
    if not pq_path.exists():
        return {}

    questions = json.loads(pq_path.read_text(encoding="utf-8"))
    answered = {}

    for q in questions:
        if q.get("answered"):
            continue
        text = q.get("question", "")
        if not text:
            continue
        answer = auto_answer(text, portal=q.get("portal", ""))
        if answer:
            q["answer"] = answer
            q["answered"] = True
            answered[text] = answer

    if answered:
        pq_path.write_text(json.dumps(questions, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("[AI_ANSWERER] %d preguntas respondidas automáticamente.", len(answered))

    return answered

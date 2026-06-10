"""api/routes/qa.py — Cola de scan y preguntas pendientes de formularios ATS."""
from __future__ import annotations

import json
import os

from flask import Blueprint, jsonify, request

from api.qa_utils import _SCAN_QUEUE_PATH, _PENDING_Q_PATH, _QA_PATH, _suggest_from_cache

bp = Blueprint('qa', __name__)


@bp.route('/api/scan-queue')
def api_scan_queue():
    """Devuelve todos los items de la cola de scan para mostrar en el panel."""
    try:
        if not os.path.exists(_SCAN_QUEUE_PATH):
            return jsonify({"count": 0, "items": []})
        with open(_SCAN_QUEUE_PATH, encoding='utf-8') as f:
            queue = json.load(f)
        items = [
            {
                "url":    e.get("url", ""),
                "title":  e.get("title", "") or e.get("url", ""),
                "portal": e.get("portal", ""),
                "unanswered": e.get("unanswered", e.get("unanswered_questions", [])),
            }
            for e in queue if e.get("url")
        ]
        return jsonify({"count": len(items), "items": items})
    except Exception as e:
        return jsonify({"count": 0, "items": [], "error": str(e)})


@bp.route('/api/scan-queue/dismiss', methods=['POST'])
def api_scan_queue_dismiss():
    """Elimina una URL de la cola de scan."""
    url = (request.json or {}).get('url', '').strip()
    if not url:
        return jsonify({'ok': False}), 400
    try:
        if os.path.exists(_SCAN_QUEUE_PATH):
            with open(_SCAN_QUEUE_PATH, encoding='utf-8') as f:
                queue = json.load(f)
            queue = [e for e in queue if e.get('url') != url]
            with open(_SCAN_QUEUE_PATH, 'w', encoding='utf-8') as f:
                json.dump(queue, f, ensure_ascii=False, indent=2)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@bp.route('/api/pending-questions')
def api_pending_questions():
    """Devuelve la lista de preguntas pendientes de respuesta del usuario.
    Incluye 'suggested_answer' si hay coincidencia en qa_cache."""
    try:
        if not os.path.exists(_PENDING_Q_PATH):
            return jsonify([])
        with open(_PENDING_Q_PATH, encoding='utf-8') as f:
            data = json.load(f)
        # Enriquecer con sugerencia del cache para preguntas sin respuesta
        for entry in data:
            if not entry.get('answered') and not entry.get('answer'):
                norm = entry.get('norm', '')
                if norm:
                    entry['suggested_answer'] = _suggest_from_cache(norm)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/api/pending-questions/auto-fill', methods=['POST'])
def api_auto_fill_questions():
    """
    Auto-rellena preguntas pendientes usando (en orden de prioridad):
      1. question_answers.json + qa_cache.json
      2. profile_kb.json qa_overrides
      3. Variables USER_* del .env / config

    Retorna detalle de cuántas se llenaron y desde qué fuente.
    """
    try:
        if not os.path.exists(_PENDING_Q_PATH):
            return jsonify({"filled": 0, "remaining": 0, "details": []})
        with open(_PENDING_Q_PATH, encoding='utf-8') as f:
            pending = json.load(f)

        # Cargar QA principal para persistir nuevas respuestas
        qa: dict = {}
        if os.path.exists(_QA_PATH):
            try:
                with open(_QA_PATH, encoding='utf-8') as f:
                    qa = json.load(f)
            except Exception:
                pass

        filled = 0
        details = []
        for entry in pending:
            if entry.get('answered'):
                continue
            norm = entry.get('norm', '')
            if not norm:
                continue
            suggestion = _suggest_from_cache(norm)
            if suggestion:
                entry['answer']   = suggestion
                entry['answered'] = True
                entry['auto_answered'] = True   # marcar como auto-respondido
                filled += 1
                qa[norm] = suggestion           # aprender para futuros runs
                details.append({
                    "label": entry.get('label', norm)[:60],
                    "answer": suggestion[:80],
                })

        os.makedirs(os.path.dirname(_PENDING_Q_PATH), exist_ok=True)
        with open(_PENDING_Q_PATH, 'w', encoding='utf-8') as f:
            json.dump(pending, f, ensure_ascii=False, indent=2)
        with open(_QA_PATH, 'w', encoding='utf-8') as f:
            json.dump(qa, f, ensure_ascii=False, indent=2)

        remaining = sum(1 for e in pending if not e.get('answered'))
        return jsonify({"ok": True, "filled": filled, "remaining": remaining, "details": details})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route('/api/pending-questions/answer', methods=['POST'])
def api_answer_question():
    """Guarda la respuesta del usuario para una pregunta pendiente."""
    try:
        payload = request.get_json(force=True)
        norm    = (payload.get('norm') or '').strip().lower()[:200]
        answer  = (payload.get('answer') or '').strip()
        if not norm or not answer:
            return jsonify({"ok": False, "error": "norm y answer son requeridos"}), 400

        existing = []
        if os.path.exists(_PENDING_Q_PATH):
            with open(_PENDING_Q_PATH, encoding='utf-8') as f:
                existing = json.load(f)

        updated = False
        for entry in existing:
            if entry.get('norm', '') == norm:
                entry['answer']   = answer
                entry['answered'] = True
                updated = True
                break

        if not updated:
            return jsonify({"ok": False, "error": "Pregunta no encontrada"}), 404

        os.makedirs(os.path.dirname(_PENDING_Q_PATH), exist_ok=True)
        with open(_PENDING_Q_PATH, 'w', encoding='utf-8') as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        # Persistir también en question_answers.json para auto-fill en futuros runs
        qa: dict = {}
        if os.path.exists(_QA_PATH):
            try:
                with open(_QA_PATH, encoding='utf-8') as f:
                    qa = json.load(f)
            except Exception:
                pass
        qa[norm] = answer
        with open(_QA_PATH, 'w', encoding='utf-8') as f:
            json.dump(qa, f, ensure_ascii=False, indent=2)

        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route('/api/save-qa', methods=['POST'])
def api_save_qa():
    """Guarda directamente una respuesta en question_answers.json (sin necesitar pending list)."""
    try:
        payload = request.get_json(force=True)
        norm    = (payload.get('norm') or '').strip().lower()[:200]
        answer  = (payload.get('answer') or '').strip()
        if not norm or not answer:
            return jsonify({"ok": False, "error": "norm y answer requeridos"}), 400

        qa: dict = {}
        if os.path.exists(_QA_PATH):
            try:
                with open(_QA_PATH, encoding='utf-8') as f:
                    qa = json.load(f)
            except Exception:
                pass
        qa[norm] = answer
        os.makedirs(os.path.dirname(_QA_PATH), exist_ok=True)
        with open(_QA_PATH, 'w', encoding='utf-8') as f:
            json.dump(qa, f, ensure_ascii=False, indent=2)

        # También marcar como respondida en pending si existe
        if os.path.exists(_PENDING_Q_PATH):
            try:
                with open(_PENDING_Q_PATH, encoding='utf-8') as f:
                    pending = json.load(f)
                for entry in pending:
                    if entry.get('norm') == norm:
                        entry['answered'] = True
                        entry['answer']   = answer
                with open(_PENDING_Q_PATH, 'w', encoding='utf-8') as f:
                    json.dump(pending, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

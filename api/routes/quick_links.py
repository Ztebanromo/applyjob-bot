"""api/routes/quick_links.py — Ofertas rápidas (bodega/operario) para postulación manual/auto."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading

from flask import Blueprint, jsonify, request

from api.qa_utils import _QUICK_LINKS_PATH, _PENDING_Q_PATH, _QA_CACHE_PATH
from api.state import state

bp = Blueprint('quick_links', __name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@bp.route('/api/quick-links')
def api_quick_links():
    """Devuelve los links rápidos de bodega pendientes (no descartados)."""
    try:
        if not os.path.exists(_QUICK_LINKS_PATH):
            return jsonify([])
        with open(_QUICK_LINKS_PATH, encoding='utf-8') as f:
            links = json.load(f)
        active = [l for l in links if not l.get('dismissed')]
        return jsonify(active)
    except Exception:
        return jsonify([])


@bp.route('/api/quick-links/dismiss', methods=['POST'])
def api_quick_links_dismiss():
    """Marca un link como descartado (ya postulado o no interesa)."""
    data = request.json or {}
    url  = data.get('url', '').strip()
    if not url:
        return jsonify({'ok': False, 'error': 'url requerida'}), 400
    try:
        if not os.path.exists(_QUICK_LINKS_PATH):
            return jsonify({'ok': True})
        with open(_QUICK_LINKS_PATH, encoding='utf-8') as f:
            links = json.load(f)
        for l in links:
            if l.get('url') == url:
                l['dismissed'] = True
        with open(_QUICK_LINKS_PATH, 'w', encoding='utf-8') as f:
            json.dump(links, f, ensure_ascii=False, indent=2)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@bp.route('/api/quick-links/clear', methods=['POST'])
def api_quick_links_clear():
    """Descarta todos los links activos."""
    try:
        if os.path.exists(_QUICK_LINKS_PATH):
            with open(_QUICK_LINKS_PATH, encoding='utf-8') as f:
                links = json.load(f)
            for l in links:
                l['dismissed'] = True
            with open(_QUICK_LINKS_PATH, 'w', encoding='utf-8') as f:
                json.dump(links, f, ensure_ascii=False, indent=2)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@bp.route('/api/scan-quick-links', methods=['POST'])
def api_scan_quick_links():
    """
    Lanza run_scan_quick_links en un subproceso y devuelve los resultados.
    Escanea los quick_links.json guardados para recopilar preguntas de ATS externos.
    """
    result_holder = {}
    done_event = threading.Event()

    def _run():
        try:
            proc = subprocess.run(
                [sys.executable, '-u', 'main.py', '--scan-quick-links', '--headless'],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                cwd=_PROJECT_ROOT,
                timeout=300,
            )
            output = proc.stdout + proc.stderr
            # Parse top_questions from output
            top_qs = []
            already = queued = failed = scanned = 0
            for line in output.splitlines():
                if '  Escaneadas' in line:
                    try: scanned = int(line.split(':')[-1].strip())
                    except ValueError: pass
                elif 'Ya respondidas' in line:
                    try: already = int(line.split(':')[-1].strip())
                    except ValueError: pass
                elif 'En cola' in line:
                    try: queued = int(line.split(':')[-1].strip())
                    except ValueError: pass
                elif 'Sin formulario' in line:
                    try: failed = int(line.split(':')[-1].strip())
                    except ValueError: pass
                elif line.strip().startswith(tuple(str(i)+'.' for i in range(1,20))):
                    # Líneas de formato "  N. [Mx] Pregunta..."
                    import re as _re
                    m = _re.match(r'\s*\d+\.\s*(?:\[\d+x\]\s*)?(.+)', line)
                    if m:
                        top_qs.append(m.group(1).strip()[:80])
            result_holder.update({
                'scanned': scanned, 'already_answered': already,
                'queued': queued, 'failed': failed,
                'top_questions': top_qs[:15],
                'output': output[-2000:],
            })
        except subprocess.TimeoutExpired:
            result_holder.update({'error': 'Timeout (300s)', 'scanned': 0})
        except Exception as e:
            result_holder.update({'error': str(e), 'scanned': 0})
        finally:
            done_event.set()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    done_event.wait(timeout=310)

    if 'error' in result_holder and 'scanned' not in result_holder:
        return jsonify({'ok': False, 'error': result_holder.get('error', 'unknown')}), 500
    return jsonify({**result_holder, 'ok': True})


@bp.route('/api/auto-answer-pending', methods=['POST'])
def api_auto_answer_pending():
    """
    Recorre pending_questions.json y responde automáticamente las que tienen
    answered=False usando _auto_answer() + _match_qa() del CV del usuario.
    No requiere browser — es solo procesamiento Python.
    """
    try:
        pending_path = _PENDING_Q_PATH
        qa_cache_path = _QA_CACHE_PATH

        if not os.path.exists(pending_path):
            return jsonify({'ok': True, 'answered': 0, 'still_pending': 0})

        with open(pending_path, encoding='utf-8') as f:
            pending = json.load(f)

        from bot.form_filler import _match_qa, _auto_answer, _normalize
        from bot.config import USER_PROFILE

        # Cargar qa_cache para persistir respuestas nuevas
        qa_cache = {}
        if os.path.exists(qa_cache_path):
            try:
                with open(qa_cache_path, encoding='utf-8') as f:
                    qa_cache = json.load(f)
            except Exception:
                qa_cache = {}

        newly_answered = 0
        for entry in pending:
            if entry.get('answered'):
                continue  # ya tiene respuesta

            label = entry.get('label', '')
            norm  = entry.get('norm', '') or _normalize(label)
            if not label:
                continue

            # Intentar respuesta automática
            ans = _match_qa(label) or _auto_answer(label, USER_PROFILE)
            if not ans:
                # Fallback: cover_letter como último recurso para preguntas abiertas
                cl = USER_PROFILE.get('cover_letter', '').strip()
                if cl and any(kw in label.lower() for kw in (
                    'experiencia', 'presentate', 'cuéntanos', 'cuentanos',
                    'sobre ti', 'motivacion', 'motivación', 'background',
                    'habilidades', 'conocimientos', 'perfil', 'describe',
                )):
                    ans = cl

            if ans:
                entry['answered'] = True
                entry['answer']   = str(ans)
                entry['source']   = 'auto'
                # Guardar en qa_cache también
                if norm not in qa_cache:
                    qa_cache[norm] = str(ans)
                newly_answered += 1

        # Guardar cambios
        with open(pending_path, 'w', encoding='utf-8') as f:
            json.dump(pending, f, ensure_ascii=False, indent=2)
        with open(qa_cache_path, 'w', encoding='utf-8') as f:
            json.dump(qa_cache, f, ensure_ascii=False, indent=2)

        still_pending = sum(1 for e in pending if not e.get('answered'))
        return jsonify({
            'ok': True,
            'answered': newly_answered,
            'still_pending': still_pending,
            'total': len(pending),
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@bp.route('/api/apply-quick-links', methods=['POST'])
def api_apply_quick_links():
    """
    Aplica automáticamente a los quick_links.json (ATS externos).
    Lanza run_apply_quick_links en subproceso — procesa hasta max (default 5).
    """
    if state.scan_active or state.apply_active or state.quick_links_active:
        return jsonify({'ok': False, 'error': 'Ya hay un proceso activo. Espera a que termine antes de iniciar otra postulación rápida.'}), 409

    data     = request.json or {}
    max_apply = max(1, min(20, int(data.get('max', 5))))

    result_holder = {}
    done_event    = threading.Event()

    with state.lock:
        state.quick_links_active = True

    def _run():
        try:
            proc = subprocess.run(
                [sys.executable, '-u', 'main.py',
                 '--apply-quick-links', '--headless', '--max', str(max_apply)],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                cwd=_PROJECT_ROOT,
                timeout=360,
            )
            output = proc.stdout + proc.stderr
            applied = pending = failed = 0
            for line in output.splitlines():
                if 'aplicadas' in line.lower():
                    import re as _re
                    m = _re.search(r'(\d+)\s+aplicadas', line, _re.I)
                    if m: applied = int(m.group(1))
                if 'pendientes' in line.lower():
                    import re as _re
                    m = _re.search(r'(\d+)\s+pendientes', line, _re.I)
                    if m: pending = int(m.group(1))
                if 'fallidas' in line.lower():
                    import re as _re
                    m = _re.search(r'(\d+)\s+fallidas', line, _re.I)
                    if m: failed = int(m.group(1))
            result_holder.update({
                'applied': applied, 'pending': pending, 'failed': failed,
                'output': output[-1500:],
            })
        except subprocess.TimeoutExpired:
            result_holder.update({'error': 'Timeout (360s)', 'applied': 0})
        except Exception as exc:
            result_holder.update({'error': str(exc), 'applied': 0})
        finally:
            done_event.set()
            with state.lock:
                state.quick_links_active = False

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    done_event.wait(timeout=370)

    if 'error' in result_holder and 'applied' not in result_holder:
        return jsonify({'ok': False, 'error': result_holder.get('error')}), 500
    return jsonify({**result_holder, 'ok': True})


@bp.route('/api/fill-curriculum', methods=['POST'])
def api_fill_curriculum():
    """
    Abre trabajando.cl/mi-curriculum#/ con la sesión guardada
    y rellena los campos del perfil con los datos del usuario.
    """
    result_holder = {}
    done_event = threading.Event()

    def _run():
        try:
            proc = subprocess.run(
                [sys.executable, '-u', 'main.py', '--fill-curriculum', '--headless'],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                cwd=_PROJECT_ROOT,
                timeout=120,
            )
            output = proc.stdout + proc.stderr
            ok = 'curriculum guardado' in output.lower() or proc.returncode == 0
            result_holder.update({'ok': ok, 'output': output[-1500:]})
        except subprocess.TimeoutExpired:
            result_holder.update({'ok': False, 'error': 'Timeout (120s)'})
        except Exception as e:
            result_holder.update({'ok': False, 'error': str(e)})
        finally:
            done_event.set()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    done_event.wait(timeout=130)
    return jsonify(result_holder)

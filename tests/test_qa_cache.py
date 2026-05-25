"""Tests for bot/qa_cache.py"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import bot.qa_cache as qa


def test_miss_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(qa, '_CACHE_PATH', tmp_path / 'qa.json')
    assert qa.get_answer("¿Tienes disponibilidad inmediata?") is None


def test_hit_after_save(tmp_path, monkeypatch):
    monkeypatch.setattr(qa, '_CACHE_PATH', tmp_path / 'qa.json')
    qa.save_answer("¿Tienes disponibilidad inmediata?", "Sí")
    assert qa.get_answer("¿Tienes disponibilidad inmediata?") == "Sí"


def test_case_insensitive_question(tmp_path, monkeypatch):
    monkeypatch.setattr(qa, '_CACHE_PATH', tmp_path / 'qa.json')
    qa.save_answer("¿Cuántos años de experiencia tienes?", "2")
    assert qa.get_answer("¿cuantos anos de experiencia tienes?") == "2"


def test_accent_insensitive(tmp_path, monkeypatch):
    monkeypatch.setattr(qa, '_CACHE_PATH', tmp_path / 'qa.json')
    qa.save_answer("¿Disponibilidad para viajar?", "No")
    assert qa.get_answer("disponibilidad para viajar") == "No"


def test_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(qa, '_CACHE_PATH', tmp_path / 'qa.json')
    qa.save_answer("¿Disponible para trabajar?", "Sí")
    qa.save_answer("¿Disponible para trabajar?", "No")
    assert qa.get_answer("¿Disponible para trabajar?") == "No"


def test_all_answers_returns_dict(tmp_path, monkeypatch):
    monkeypatch.setattr(qa, '_CACHE_PATH', tmp_path / 'qa.json')
    qa.save_answer("Pregunta 1?", "Respuesta 1")
    qa.save_answer("Pregunta 2?", "Respuesta 2")
    all_ = qa.all_answers()
    assert len(all_) == 2


def test_empty_question(tmp_path, monkeypatch):
    monkeypatch.setattr(qa, '_CACHE_PATH', tmp_path / 'qa.json')
    qa.save_answer("", "algo")
    result = qa.get_answer("")
    assert result == "algo"

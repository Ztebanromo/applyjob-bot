"""Tests for bot/dedup.py"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import bot.dedup as dedup_mod


def test_not_duplicate_initially(tmp_path, monkeypatch):
    monkeypatch.setattr(dedup_mod, '_DEDUP_PATH', tmp_path / 'dedup.json')
    assert dedup_mod.is_duplicate("Dev Backend", "Acme") is False


def test_duplicate_after_mark(tmp_path, monkeypatch):
    monkeypatch.setattr(dedup_mod, '_DEDUP_PATH', tmp_path / 'dedup.json')
    dedup_mod.mark_seen("Dev Backend", "Acme", "linkedin")
    assert dedup_mod.is_duplicate("Dev Backend", "Acme") is True


def test_different_company_not_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(dedup_mod, '_DEDUP_PATH', tmp_path / 'dedup.json')
    dedup_mod.mark_seen("Dev Backend", "Acme", "linkedin")
    assert dedup_mod.is_duplicate("Dev Backend", "OtherCorp") is False


def test_case_insensitive(tmp_path, monkeypatch):
    monkeypatch.setattr(dedup_mod, '_DEDUP_PATH', tmp_path / 'dedup.json')
    dedup_mod.mark_seen("DESARROLLADOR BACKEND", "ACME SA", "laborum")
    assert dedup_mod.is_duplicate("desarrollador backend", "acme sa") is True


def test_accent_insensitive(tmp_path, monkeypatch):
    monkeypatch.setattr(dedup_mod, '_DEDUP_PATH', tmp_path / 'dedup.json')
    dedup_mod.mark_seen("Desarrollador Móvil", "Empresa", "laborum")
    assert dedup_mod.is_duplicate("Desarrollador Movil", "Empresa") is True


def test_empty_title_never_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(dedup_mod, '_DEDUP_PATH', tmp_path / 'dedup.json')
    dedup_mod.mark_seen("", "Acme", "linkedin")
    assert dedup_mod.is_duplicate("", "Acme") is False


def test_purge_old(tmp_path, monkeypatch):
    from datetime import date, timedelta
    monkeypatch.setattr(dedup_mod, '_DEDUP_PATH', tmp_path / 'dedup.json')
    old_date = str(date.today() - timedelta(days=90))
    dedup_mod._save({"aabbcc112233aabb": {"date": old_date, "portal": "linkedin"}})
    deleted = dedup_mod.purge_old(days=60)
    assert deleted == 1
    assert dedup_mod._load() == {}


def test_purge_keeps_recent(tmp_path, monkeypatch):
    from datetime import date
    monkeypatch.setattr(dedup_mod, '_DEDUP_PATH', tmp_path / 'dedup.json')
    dedup_mod.mark_seen("Recent Job", "Company", "laborum")
    deleted = dedup_mod.purge_old(days=60)
    assert deleted == 0
    assert dedup_mod.is_duplicate("Recent Job", "Company") is True

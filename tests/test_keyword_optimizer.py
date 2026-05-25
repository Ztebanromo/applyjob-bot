"""
Tests for bot/keyword_optimizer.py

_STATS_PATH is patched to tmp_path so tests never touch data/keyword_stats.json.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import bot.keyword_optimizer as kw


@pytest.fixture(autouse=True)
def patch_stats(tmp_path, monkeypatch):
    monkeypatch.setattr(kw, '_STATS_PATH', tmp_path / 'keyword_stats.json')


def test_new_keyword_score_is_half():
    """Brand-new keyword has no history → score 0.5 (neutral priority)."""
    score = kw.get_keyword_score("desarrollador junior", "laborum")
    assert score == 0.5


def test_score_increases_with_applications():
    kw.update_keyword_stat("python developer", "laborum", applied=3, found=5)
    score = kw.get_keyword_score("python developer", "laborum")
    assert score > 0.5


def test_zero_found_gives_low_score():
    kw.update_keyword_stat("php developer", "laborum", applied=0, found=0)
    score = kw.get_keyword_score("php developer", "laborum")
    assert score < 0.5


def test_should_not_retire_new_keyword():
    assert kw.should_retire("new keyword", "laborum") is False


def test_should_retire_after_one_run_with_zero_found():
    kw.update_keyword_stat("bad keyword", "laborum", applied=0, found=0)
    assert kw.should_retire("bad keyword", "laborum") is True


def test_should_not_retire_if_found_gt_zero():
    kw.update_keyword_stat("good keyword", "laborum", applied=0, found=3)
    assert kw.should_retire("good keyword", "laborum") is False


def test_retire_marks_status():
    kw.update_keyword_stat("retiring kw", "laborum", applied=0, found=0)
    kw.retire_keyword_from_portal("retiring kw", "laborum")
    stats = kw._load_stats()
    status = stats.get("retiring kw", {}).get("portals", {}).get("laborum", {}).get("status")
    assert status == "retired"


def test_retired_keyword_excluded_from_active_groups():
    base = [{"label": "Dev", "keyword": "retiring kw", "mode": "it", "scan": True}]
    kw.update_keyword_stat("retiring kw", "laborum", applied=0, found=0)
    kw.retire_keyword_from_portal("retiring kw", "laborum")
    active = kw.get_active_groups(base, "laborum")
    assert not any(g["keyword"] == "retiring kw" for g in active)


def test_active_keywords_limit():
    base = [
        {"label": "Dev", "keyword": f"kw {i}", "mode": "it", "scan": True}
        for i in range(30)
    ]
    active = kw.get_active_groups(base, "laborum")
    assert len(active) <= kw.MAX_ACTIVE_KEYWORDS


def test_generate_replacements_returns_new_keywords():
    replacements = kw.generate_replacements("python developer junior")
    assert len(replacements) > 0
    for r in replacements:
        assert "keyword" in r
        assert "label" in r


def test_extract_keywords_from_titles():
    titles = ["Desarrollador React Junior", "Ingeniero React Senior", "Analista SQL"]
    result = kw.extract_keywords_from_seen_titles(titles, "laborum")
    keywords = [r["keyword"] for r in result]
    assert any("react" in k for k in keywords)


def test_update_accumulates_across_runs():
    kw.update_keyword_stat("dev", "laborum", applied=2, found=4)
    kw.update_keyword_stat("dev", "laborum", applied=1, found=3)
    stats = kw._load_stats()
    pe = stats["dev"]["portals"]["laborum"]
    assert pe["applied"] == 3
    assert pe["runs"] == 2

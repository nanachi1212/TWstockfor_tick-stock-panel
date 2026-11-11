"""Retired index entrypoints never read prices or reactivate persisted symbols."""
import json

import pytest

from app.services import preferences


@pytest.fixture
def isolated_preferences(monkeypatch, tmp_path):
    path = tmp_path / "preferences.json"
    monkeypatch.setattr(preferences, "_path", lambda: path)
    preferences._invalidate_cache()
    yield path
    preferences._invalidate_cache()


def test_default_preferences_have_no_active_retired_index_symbols(isolated_preferences):
    assert preferences.get_sidebar_index_symbols() == []
    assert preferences.get_realtime_index_symbols() == []
    assert preferences.get_realtime_pull_index() is False
    assert not isolated_preferences.exists()


def test_old_preferences_are_inert_and_preserved(isolated_preferences):
    original = {
        "realtime_pull_index": True,
        "realtime_index_symbols": ["000001.SH", "399001.SZ"],
        "sidebar_index_symbols": ["000001.SH"],
        "realtime_index_mode": "all",
        "unrelated": "keep",
    }
    isolated_preferences.write_text(json.dumps(original), encoding="utf-8")
    before = isolated_preferences.read_bytes()
    assert preferences.get_sidebar_index_symbols() == []
    assert preferences.get_realtime_index_symbols() == []
    assert preferences.get_realtime_pull_index() is False
    preferences.set_realtime_monitor_config(original)
    assert isolated_preferences.read_bytes() == before


def test_taiwan_monitor_settings_still_work(isolated_preferences):
    settings = preferences.set_realtime_monitor_config({
        "minute_intraday_refresh": True, "minute_intraday_refresh_interval": 10,
    })
    assert settings["minute_intraday_refresh"] is True
    assert settings["minute_intraday_refresh_interval"] == 10

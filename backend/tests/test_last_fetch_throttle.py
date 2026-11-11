"""last_fetch 落盤節流測試 — 30s 內只寫一次盤, 記憶體值路徑不受影響。"""
from __future__ import annotations

import pytest

from app.services import preferences, quote_service


@pytest.fixture(autouse=True)
def _reset_throttle_state(monkeypatch):
    monkeypatch.setattr(quote_service, "_last_fetch_written_at_ms", 0.0)
    yield
    quote_service._last_fetch_written_at_ms = 0.0


@pytest.fixture()
def save_counter(monkeypatch):
    counter = {"saves": 0, "values": []}

    def _counting(updates: dict) -> dict:
        counter["saves"] += 1
        counter["values"].append(updates)
        return {}

    monkeypatch.setattr(preferences, "save", _counting)
    return counter


def test_first_call_after_start_writes(save_counter):
    quote_service._persist_last_fetch(1_700_000_000_000.0)
    assert save_counter["saves"] == 1
    assert save_counter["values"][0] == {"last_fetch_ms": 1_700_000_000_000.0}


def test_writes_within_window_are_skipped(save_counter):
    t0 = 1_700_000_000_000.0
    quote_service._persist_last_fetch(t0)
    quote_service._persist_last_fetch(t0 + 10_000.0)   # +10s: 跳過
    quote_service._persist_last_fetch(t0 + 29_999.0)   # 仍不足 30s: 跳過
    assert save_counter["saves"] == 1


def test_write_resumes_after_window(save_counter):
    t0 = 1_700_000_000_000.0
    quote_service._persist_last_fetch(t0)
    quote_service._persist_last_fetch(t0 + 30_000.0)   # 恰好 30s: 恢復寫盤
    assert save_counter["saves"] == 2
    assert save_counter["values"][1] == {"last_fetch_ms": 1_700_000_030_000.0}


def test_write_failure_does_not_propagate_and_retries(monkeypatch):
    calls = {"n": 0}

    def _flaky(updates: dict) -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full")
        return {}

    monkeypatch.setattr(preferences, "save", _flaky)
    t0 = 1_700_000_000_000.0
    quote_service._persist_last_fetch(t0)          # 失敗但不拋出
    quote_service._persist_last_fetch(t0 + 1_000.0)  # 未成功過 -> 立即重試
    assert calls["n"] == 2

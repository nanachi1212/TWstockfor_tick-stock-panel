"""台股歷史週轉率 fail-closed。"""
from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from app.indicators import pipeline


def test_historical_taiwan_turnover_without_verified_share_history_fails_closed(monkeypatch):
    monkeypatch.setattr("app.taiwan.realtime.calendar.taipei_today", lambda: date(2026, 8, 31))
    bars = pl.DataFrame({
        "symbol": ["2330.TWSE"],
        "date": [date(2026, 8, 28)],
        "volume": [1_000_000.0],
    })
    instruments = pl.DataFrame({
        "symbol": ["2330.TWSE"],
        "float_shares": [100_000_000.0],
    })

    result = pipeline.compute_limit_signals(
        bars,
        instruments,
        needed={"turnover_rate"},
    )

    assert result["turnover_rate"][0] is None


def test_turnover_uses_share_volume(monkeypatch):
    monkeypatch.setattr("app.taiwan.realtime.calendar.taipei_today", lambda: date(2026, 8, 28))
    bars = pl.DataFrame({
        "symbol": ["2330.TWSE"],
        "date": [date(2026, 8, 28)],
        "volume": [1_000_000.0],
    })
    instruments = pl.DataFrame({"symbol": ["2330.TWSE"], "float_shares": [100_000_000.0]})

    result = pipeline.compute_limit_signals(bars, instruments, needed={"turnover_rate"})

    assert result["turnover_rate"].to_list() == pytest.approx([1.0])

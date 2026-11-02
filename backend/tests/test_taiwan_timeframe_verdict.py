"""Daily/weekly/monthly rule-based bias verdicts: direction, sentence and data-insufficient handling."""
from __future__ import annotations

import math
from datetime import date, timedelta

import polars as pl

from app.taiwan.timeframe_verdict import build_timeframe_verdicts


def _daily(closes: list[float]) -> pl.DataFrame:
    start = date(2023, 1, 2)
    return pl.DataFrame({
        "date": [start + timedelta(days=i) for i in range(len(closes))],
        "open": closes, "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes], "close": closes,
        "volume": [1_000_000] * len(closes),
    })


def test_long_uptrend_is_bullish_on_monthly_with_pullback_warning():
    closes = [100 * 1.0015**i + 2 * math.sin(i / 2) for i in range(900)]
    verdicts = build_timeframe_verdicts(_daily(closes))
    assert [v.timeframe for v in verdicts] == ["daily", "weekly", "monthly"]
    monthly = verdicts[2]
    assert monthly.status == "available"
    assert monthly.bias == "bullish" and monthly.score is not None and monthly.score >= 10
    assert monthly.summary.startswith("股價站上 MA5/MA20")
    assert monthly.summary.endswith("留意回檔")


def test_downtrend_is_bearish_and_mentions_rebound_conditions():
    closes = [400 * 0.997**i + 2 * math.sin(i / 2) for i in range(300)]
    for v in build_timeframe_verdicts(_daily(closes))[:2]:
        assert v.bias == "bearish" and v.score is not None and v.score <= -10
        assert "均線空排下彎" in v.summary
        assert v.summary.endswith("留意反彈")


def test_short_history_is_data_insufficient_not_zero():
    verdicts = build_timeframe_verdicts(_daily([100.0 + i for i in range(30)]))
    monthly = verdicts[2]
    assert monthly.status == "data_insufficient"
    assert monthly.bias is None and monthly.score is None
    assert "資料不足" in monthly.summary


def test_empty_history_returns_nothing():
    assert build_timeframe_verdicts(pl.DataFrame()) == []

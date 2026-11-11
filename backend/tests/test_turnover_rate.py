"""台股歷史週轉率 fail-closed + Data Manager 財務資料統計。"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import polars as pl
import pytest

from app.api import data as data_api
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


def test_data_status_includes_share_history(tmp_path):
    path = tmp_path / "financials" / "shares" / "part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "symbol": ["600000.SH", "600000.SH", "000001.SZ"],
        "period_end": ["2023-12-31", "2024-06-30", "2024-06-30"],
    }).write_parquet(path)

    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    result = data_api._safe_aggregate_financials(repo)

    assert result is not None
    assert result["rows"] == 3
    assert result["tables"]["shares"] == {"rows": 3, "symbols": 2}

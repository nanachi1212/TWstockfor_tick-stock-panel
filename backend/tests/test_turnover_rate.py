"""换手率历史股本计算 + Data Manager 财务数据统计。

注: 原 A 股「财务数据同步」产品 (services/financial_sync.py 的 FinancialScheduler
及 sync_* 系列) 已在 Phase 8B-5.3 移除; 本文件保留的是仍被 turnover_rate 指标
(app.indicators.pipeline) 与 Data Manager (app.api.data) 使用的通用/台股测试。
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import polars as pl
import pytest

from app.api import data as data_api
from app.indicators import pipeline


def test_historical_turnover_uses_only_available_share_capital(monkeypatch):
    monkeypatch.setattr(pipeline, "cn_today", lambda: date(2026, 7, 18))
    bars = pl.DataFrame({
        "symbol": ["600000.SH"] * 5,
        "date": [
            date(2024, 3, 31),
            date(2024, 4, 14),
            date(2024, 4, 15),
            date(2024, 6, 30),
            date(2026, 7, 18),
        ],
        "volume": [10_000.0] * 5,
    })
    instruments = pl.DataFrame({
        "symbol": ["600000.SH"],
        "float_shares": [200_000_000.0],
    })
    shares = pl.DataFrame({
        "symbol": ["600000.SH", "600000.SH"],
        "period_end": ["2023-12-31", "2024-06-30"],
        "announce_date": ["2024-04-15", None],
        "float_shares": [100_000_000.0, 50_000_000.0],
    })

    result = pipeline.compute_limit_signals(
        bars,
        instruments,
        needed={"turnover_rate"},
        historical_shares=shares,
    )

    assert result["turnover_rate"].to_list() == pytest.approx([0.5, 0.5, 1.0, 2.0, 0.5])


def test_turnover_without_share_history_keeps_existing_behavior(monkeypatch):
    monkeypatch.setattr(pipeline, "cn_today", lambda: date(2026, 7, 18))
    bars = pl.DataFrame({
        "symbol": ["600000.SH"],
        "date": [date(2024, 4, 15)],
        "volume": [10_000.0],
    })
    instruments = pl.DataFrame({
        "symbol": ["600000.SH"],
        "float_shares": [200_000_000.0],
    })

    result = pipeline.compute_limit_signals(
        bars,
        instruments,
        needed={"turnover_rate"},
    )

    assert result["turnover_rate"][0] == pytest.approx(0.5)


def test_historical_taiwan_turnover_without_verified_share_history_fails_closed(monkeypatch):
    monkeypatch.setattr(pipeline, "cn_today", lambda: date(2026, 8, 31))
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


def test_taiwan_period_end_share_row_cannot_enable_historical_turnover(monkeypatch):
    monkeypatch.setattr(pipeline, "cn_today", lambda: date(2026, 8, 31))
    bars = pl.DataFrame({
        "symbol": ["2330.TWSE"],
        "date": [date(2026, 8, 28)],
        "volume": [1_000_000.0],
    })
    instruments = pl.DataFrame({
        "symbol": ["2330.TWSE"],
        "float_shares": [100_000_000.0],
    })
    unverified_history = pl.DataFrame({
        "symbol": ["2330.TWSE"],
        "period_end": ["2026-06-30"],
        "float_shares": [80_000_000.0],
    })

    result = pipeline.compute_limit_signals(
        bars,
        instruments,
        needed={"turnover_rate"},
        historical_shares=unverified_history,
    )

    assert result["turnover_rate"][0] is None


def test_turnover_uses_market_volume_unit(monkeypatch):
    monkeypatch.setattr(pipeline, "cn_today", lambda: date(2026, 8, 28))
    bars = pl.DataFrame({
        "symbol": ["2330.TWSE", "600000.SH"],
        "date": [date(2026, 8, 28)] * 2,
        "volume": [1_000_000.0, 10_000.0],
    })
    instruments = pl.DataFrame({
        "symbol": ["2330.TWSE", "600000.SH"],
        "float_shares": [100_000_000.0, 100_000_000.0],
    })

    result = pipeline.compute_limit_signals(
        bars,
        instruments,
        needed={"turnover_rate"},
    ).sort("symbol")

    assert result["turnover_rate"].to_list() == pytest.approx([1.0, 1.0])


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

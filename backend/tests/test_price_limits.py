from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from app.api import kline
from app.indicators import pipeline
from app.price_limits import polars_limit_price, polars_price_limit_pct, price_limit_pct


def test_scalar_price_limit_rules():
    assert price_limit_pct("2330.TWSE") == pytest.approx(0.10)
    assert price_limit_pct("8069.TPEX") == pytest.approx(0.10)
    assert price_limit_pct("600001.SH") is None
    assert price_limit_pct("2330") is None


def test_polars_price_limit_pct_only_applies_to_taiwan_symbols():
    panel = pl.DataFrame({"symbol": ["2330.TWSE", "8069.TPEX", "600001.SH", None]})
    values = panel.select(
        polars_price_limit_pct(pl.col("symbol"), taiwan_limit_pct=pl.lit(0.10))
    ).to_series().to_list()
    assert values == [0.10, 0.10, None, None]


def test_polars_limit_prices_use_half_up_cent_rounding():
    frame = pl.DataFrame({"previous": [18.90, 10.00], "limit": [0.05, 0.10]})
    up = frame.select(polars_limit_price(pl.col("previous"), pl.col("limit"), up=True))
    down = frame.select(polars_limit_price(pl.col("previous"), pl.col("limit"), up=False))
    assert up.to_series().to_list() == pytest.approx([19.85, 11.00])
    assert down.to_series().to_list() == pytest.approx([17.96, 9.00])


class _InstrumentRepo:
    def get_instruments_asset(self, asset_type: str) -> pl.DataFrame:
        assert asset_type == "stock"
        return pl.DataFrame({
            "symbol": ["2330.TWSE"],
            "limit_up": [10.88],
            "limit_down": [8.90],
        })


def test_minute_price_limit_prefers_authoritative_prices_only_today(monkeypatch):
    today = date(2026, 7, 18)
    monkeypatch.setattr(kline, "taipei_today", lambda: today)
    current = kline._get_price_limit_info(
        _InstrumentRepo(), "2330.TWSE", today, "stock",
    )
    historical = kline._get_price_limit_info(
        _InstrumentRepo(), "2330.TWSE", date(2026, 7, 3), "stock",
    )

    assert current == {
        "rate": 0.10,
        "limit_up": 10.88,
        "limit_down": 8.90,
        "source": "instrument",
    }
    assert historical == {
        "rate": 0.10,
        "limit_up": None,
        "limit_down": None,
        "source": "rule",
    }


def _daily_limit_rows(current_close: float) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": ["2330.TWSE", "2330.TWSE"],
        "date": [date(2026, 7, 17), date(2026, 7, 20)],
        "open": [10.0, current_close],
        "high": [10.0, current_close],
        "low": [10.0, current_close],
        "close": [10.0, current_close],
        "raw_close": [10.0, current_close],
        "raw_high": [10.0, current_close],
    })


@pytest.mark.parametrize(
    ("instrument_as_of", "expected"),
    [
        (date(2026, 7, 17), False),
        (date(2026, 7, 20), True),
        (None, True),
    ],
)
def test_daily_limit_prices_require_matching_instrument_date(instrument_as_of, expected):
    instrument_data = {
        "symbol": ["2330.TWSE"],
        "asset_type": ["stock"],
        "name": ["普通股"],
        "limit_up": [10.90],
        "limit_down": [9.10],
    }
    if instrument_as_of is not None:
        instrument_data["as_of"] = [instrument_as_of]

    result = pipeline.compute_limit_signals(
        _daily_limit_rows(9.10),
        pl.DataFrame(instrument_data),
        needed={"signal_limit_down"},
    )

    assert result["signal_limit_down"][-1] is expected
    assert "_instrument_as_of" not in result.columns


def test_realtime_limit_prices_ignore_stale_instrument_date():
    today = date(2026, 7, 20)
    rows = pl.DataFrame({
        "symbol": ["2330.TWSE"],
        "date": [today],
        "open": [9.10],
        "high": [9.10],
        "low": [9.10],
        "close": [9.10],
        "raw_close": [9.10],
        "raw_high": [9.10],
        "raw_low": [9.10],
        "_prev_close_raw": [10.0],
        "volume": [1000.0],
    })
    instruments = pl.DataFrame({
        "symbol": ["2330.TWSE"],
        "asset_type": ["stock"],
        "name": ["普通股"],
        "limit_up": [10.90],
        "limit_down": [9.10],
        "as_of": [date(2026, 7, 17)],
    })

    result = pipeline._compute_limit_signals_today(rows, instruments)

    assert result["signal_limit_down"][0] is False
    assert "_instrument_as_of" not in result.columns


def test_limit_down_recovery_uses_raw_low_under_later_ex_div():
    """除權事件之後重算歷史時, 跌停翹板"曾觸及跌停"必須用原始價 low 判斷。

    day2 (歷史日): 原始 low 9.30 未觸及跌停價 9.00, 不應觸發翹板;
    但 day3 除權 (ex_factor=2) 使 day2 前復權 low 變為 4.65,
    若誤用復權 low 對比原始口徑跌停價會誤報翹板。
    day3 (除權日, 最新日不復權): 漲跌停基準切換為前復權昨收 4.825 → 跌停價 4.34,
    原始 low 4.34 觸及且收陽未封死 → 真翹板。
    """
    raw = pl.DataFrame({
        "symbol": ["2330.TWSE"] * 3,
        "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
        "open": [10.00, 9.60, 4.30],
        "high": [10.10, 9.70, 4.45],
        "low": [9.90, 9.30, 4.34],
        "close": [10.00, 9.65, 4.42],
        "volume": [10000.0, 10000.0, 10000.0],
        "amount": [1.0e7, 1.0e7, 1.0e7],
    })
    factors = pl.DataFrame({
        "symbol": ["2330.TWSE"],
        "trade_date": [date(2024, 1, 4)],
        "ex_factor": [2.0],
    })
    instruments = pl.DataFrame({
        "symbol": ["2330.TWSE"],
        "asset_type": ["stock"],
        "name": ["普通股"],
        "float_shares": [1.0e8],
    })

    df = pipeline.compute_enriched(raw, factors=factors, instruments=instruments)

    day2 = df.filter(pl.col("date") == date(2024, 1, 3))
    assert day2["signal_limit_down_recovery"][0] is False
    day3 = df.filter(pl.col("date") == date(2024, 1, 4))
    assert day3["signal_limit_down_recovery"][0] is True

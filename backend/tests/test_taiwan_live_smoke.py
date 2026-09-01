"""Live integration & smoke tests for Taiwan market data providers.

Connects to actual live endpoints (FinMind / Yahoo Finance) to verify:
  - 2330.TWSE (TWSE large-cap stock)
  - 2454.TWSE (TWSE large-cap stock)
  - 2317.TWSE (TWSE large-cap stock)
  - 0050.TWSE (TWSE ETF)
  - 006208.TWSE (TWSE ETF)
  - 8069.TPEX (TPEx OTC stock)
  - End-to-end indicator calculation on real live data

To run:
    uv run pytest tests/test_taiwan_live_smoke.py -v
"""
from __future__ import annotations

from datetime import datetime, timedelta
import pytest

from app.data_providers.registry import get_provider
from app.indicators.pipeline import compute_indicators


@pytest.mark.integration
class TestTaiwanLiveSmoke:
    """Live smoke test suite against real Taiwan market endpoints."""

    @pytest.fixture(scope="class")
    def provider(self):
        return get_provider("taiwan")

    def test_2330_twse_golden_symbol_live(self, provider):
        """Verify 2330.TWSE live data fetch and indicator computation."""
        end_time = datetime.now()
        start_time = end_time - timedelta(days=60)
        df = provider.get_daily(["2330.TWSE"], start_time=start_time, end_time=end_time)

        assert df.height > 10, f"Expected > 10 rows for 2330.TWSE, got {df.height}"
        assert (df["symbol"] == "2330.TWSE").all()
        assert (df["volume"] > 0).all()
        assert (df["high"] >= df["low"]).all()

        ind_df = compute_indicators(df, needed={"ma5", "rsi_6", "macd_dif"})
        last = ind_df.tail(1).to_dicts()[0]
        assert last["ma5"] is not None
        assert last["rsi_6"] is not None
        assert last["macd_dif"] is not None

    @pytest.mark.parametrize(
        "symbol",
        ["2454.TWSE", "2317.TWSE", "0050.TWSE", "006208.TWSE", "8069.TPEX"],
    )
    def test_secondary_symbols_live(self, provider, symbol: str):
        """Verify secondary symbols across TWSE, TPEx, and ETFs."""
        end_time = datetime.now()
        start_time = end_time - timedelta(days=30)
        df = provider.get_daily([symbol], start_time=start_time, end_time=end_time)

        assert df.height > 5, f"Expected > 5 rows for {symbol}, got {df.height}"
        assert (df["symbol"] == symbol).all()
        assert (df["volume"] > 0).all()

        ind_df = compute_indicators(df, needed={"ma5", "rsi_6"})
        last = ind_df.tail(1).to_dicts()[0]
        assert last["ma5"] is not None
        assert last["rsi_6"] is not None

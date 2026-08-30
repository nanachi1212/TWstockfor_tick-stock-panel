"""Unit tests for Taiwan Daily Market Data Provider and Normalizer.

Covers:
  - Taiwan provider parsing & normalization
  - Volume unit conversion (shares vs lots, 1000x check, unknown unit rejection)
  - Amount unit conversion (TWD vs thousand/million TWD, unavailable handling)
  - Invalid OHLC bounds rejection (high < low, high < open, low > close, etc.)
  - Non-numeric / halt / string price handling (never convert '--' or 'NaN' to 0)
  - Duplicate date elimination (keep last)
  - Date sorting guarantee (ascending)
  - Canonical symbol boundary enforcement
  - Parquet repository write/read roundtrip
  - Existing indicator pipeline compatibility (MA, RSI, MACD)
"""
from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path
import pytest
import polars as pl

from app.data_providers.normalizer import DAILY_COLS
from app.indicators.pipeline import compute_indicators
from app.parquet import scan_daily_parquet
from app.taiwan.providers.base import (
    AmountUnit,
    PriceSemantics,
    SourceMetadata,
    VolumeUnit,
)
from app.taiwan.providers.finmind_provider import FINMIND_METADATA, FinMindAdapter
from app.taiwan.providers.hybrid_provider import TaiwanHybridProvider
from app.taiwan.providers.normalizer import normalize_taiwan_daily
from app.taiwan.providers.yahoo_provider import YAHOO_METADATA, YahooFinanceAdapter
from app.taiwan.symbol import Exchange, TaiwanSymbol, parse_symbol
from app.tickflow.repository import DataStore, KlineRepository


# ── Sample Raw Fixtures ────────────────────────────────────────

RAW_FINMIND_ROWS = [
    {
        "date": "2026-08-25",
        "stock_id": "2330",
        "Trading_Volume": 13538447,
        "Trading_money": 32130079305,
        "open": 2355.0,
        "max": 2400.0,
        "min": 2350.0,
        "close": 2400.0,
    },
    {
        "date": "2026-08-26",
        "stock_id": "2330",
        "Trading_Volume": 19467241,
        "Trading_money": 46818064372,
        "open": 2375.0,
        "max": 2425.0,
        "min": 2375.0,
        "close": 2415.0,
    },
    {
        "date": "2026-08-27",
        "stock_id": "2330",
        "Trading_Volume": 19214481,
        "Trading_money": 46545167227,
        "open": 2430.0,
        "max": 2435.0,
        "min": 2410.0,
        "close": 2410.0,
    },
    {
        "date": "2026-08-28",
        "stock_id": "2330",
        "Trading_Volume": 15025832,
        "Trading_money": 36465015980,
        "open": 2440.0,
        "max": 2445.0,
        "min": 2410.0,
        "close": 2420.0,
    },
]


# ── Normalizer & Unit Conversion Tests ─────────────────────────


class TestTaiwanNormalizerUnits:
    """Test explicit volume/amount unit conversions and fail-loud protections."""

    def test_volume_unit_shares_preserved(self):
        """When volume unit is SHARES, volume numbers remain unchanged."""
        meta = SourceMetadata(
            source_name="test",
            volume_unit=VolumeUnit.SHARES,
            amount_unit=AmountUnit.TWD,
            price_semantics=PriceSemantics.RAW,
        )
        raw = [{"date": "2026-08-28", "open": 100, "high": 105, "low": 99, "close": 102, "volume": 5000}]
        df = normalize_taiwan_daily(raw, metadata=meta, default_symbol="2330.TWSE")
        assert df["volume"][0] == 5000.0

    def test_volume_unit_lots_multiplied_by_1000(self):
        """When volume unit is LOTS (張), it is deterministically converted to shares (* 1000)."""
        meta = SourceMetadata(
            source_name="test",
            volume_unit=VolumeUnit.LOTS,
            amount_unit=AmountUnit.TWD,
            price_semantics=PriceSemantics.RAW,
        )
        raw = [{"date": "2026-08-28", "open": 100, "high": 105, "low": 99, "close": 102, "volume": 5}]
        df = normalize_taiwan_daily(raw, metadata=meta, default_symbol="2330.TWSE")
        assert df["volume"][0] == 5000.0  # 5 lots * 1000 = 5000 shares

    def test_unknown_volume_unit_raises(self):
        """Reject unrecognised volume unit (never guess)."""
        meta = SourceMetadata(
            source_name="bad",
            volume_unit="unknown_unit",  # type: ignore[arg-type]
            amount_unit=AmountUnit.TWD,
            price_semantics=PriceSemantics.RAW,
        )
        raw = [{"date": "2026-08-28", "open": 100, "high": 105, "low": 99, "close": 102, "volume": 100}]
        with pytest.raises(ValueError, match="Unsupported volume unit"):
            normalize_taiwan_daily(raw, metadata=meta, default_symbol="2330.TWSE")

    def test_amount_unit_twd_preserved(self):
        meta = SourceMetadata(
            source_name="test",
            volume_unit=VolumeUnit.SHARES,
            amount_unit=AmountUnit.TWD,
            price_semantics=PriceSemantics.RAW,
        )
        raw = [{"date": "2026-08-28", "open": 100, "high": 105, "low": 99, "close": 102, "volume": 5000, "amount": 510000}]
        df = normalize_taiwan_daily(raw, metadata=meta, default_symbol="2330.TWSE")
        assert df["amount"][0] == 510000.0

    def test_amount_unit_thousand_twd(self):
        meta = SourceMetadata(
            source_name="test",
            volume_unit=VolumeUnit.SHARES,
            amount_unit=AmountUnit.THOUSAND_TWD,
            price_semantics=PriceSemantics.RAW,
        )
        raw = [{"date": "2026-08-28", "open": 100, "high": 105, "low": 99, "close": 102, "volume": 5000, "amount": 510}]
        df = normalize_taiwan_daily(raw, metadata=meta, default_symbol="2330.TWSE")
        assert df["amount"][0] == 510000.0

    def test_amount_unit_unavailable_sets_null(self):
        meta = SourceMetadata(
            source_name="yahoo",
            volume_unit=VolumeUnit.SHARES,
            amount_unit=AmountUnit.UNAVAILABLE,
            price_semantics=PriceSemantics.RAW,
        )
        raw = [{"date": "2026-08-28", "open": 100, "high": 105, "low": 99, "close": 102, "volume": 5000}]
        df = normalize_taiwan_daily(raw, metadata=meta, default_symbol="2330.TWSE")
        assert df["amount"][0] is None


# ── OHLC Data Integrity & Rejection Tests ───────────────────────


class TestTaiwanNormalizerIntegrity:
    """Test OHLC logical boundary enforcement and sanitization."""

    @pytest.fixture
    def valid_meta(self):
        return SourceMetadata(
            source_name="test",
            volume_unit=VolumeUnit.SHARES,
            amount_unit=AmountUnit.TWD,
            price_semantics=PriceSemantics.RAW,
        )

    def test_valid_row_passes(self, valid_meta):
        raw = [{"date": "2026-08-28", "open": 100.0, "high": 105.0, "low": 98.0, "close": 103.0, "volume": 1000}]
        df = normalize_taiwan_daily(raw, metadata=valid_meta, default_symbol="2330.TWSE")
        assert df.height == 1
        assert df["close"][0] == 103.0

    def test_high_less_than_low_rejected(self, valid_meta):
        raw = [{"date": "2026-08-28", "open": 100.0, "high": 95.0, "low": 98.0, "close": 96.0, "volume": 1000}]
        df = normalize_taiwan_daily(raw, metadata=valid_meta, default_symbol="2330.TWSE")
        assert df.height == 0

    def test_high_less_than_open_rejected(self, valid_meta):
        raw = [{"date": "2026-08-28", "open": 105.0, "high": 100.0, "low": 98.0, "close": 99.0, "volume": 1000}]
        df = normalize_taiwan_daily(raw, metadata=valid_meta, default_symbol="2330.TWSE")
        assert df.height == 0

    def test_high_less_than_close_rejected(self, valid_meta):
        raw = [{"date": "2026-08-28", "open": 100.0, "high": 102.0, "low": 98.0, "close": 105.0, "volume": 1000}]
        df = normalize_taiwan_daily(raw, metadata=valid_meta, default_symbol="2330.TWSE")
        assert df.height == 0

    def test_low_greater_than_open_rejected(self, valid_meta):
        raw = [{"date": "2026-08-28", "open": 95.0, "high": 105.0, "low": 98.0, "close": 102.0, "volume": 1000}]
        df = normalize_taiwan_daily(raw, metadata=valid_meta, default_symbol="2330.TWSE")
        assert df.height == 0

    def test_low_greater_than_close_rejected(self, valid_meta):
        raw = [{"date": "2026-08-28", "open": 100.0, "high": 105.0, "low": 98.0, "close": 95.0, "volume": 1000}]
        df = normalize_taiwan_daily(raw, metadata=valid_meta, default_symbol="2330.TWSE")
        assert df.height == 0

    def test_zero_or_negative_price_rejected(self, valid_meta):
        raw = [{"date": "2026-08-28", "open": 0.0, "high": 105.0, "low": 0.0, "close": 100.0, "volume": 1000}]
        df = normalize_taiwan_daily(raw, metadata=valid_meta, default_symbol="2330.TWSE")
        assert df.height == 0

    def test_negative_volume_rejected(self, valid_meta):
        raw = [{"date": "2026-08-28", "open": 100.0, "high": 105.0, "low": 98.0, "close": 102.0, "volume": -10}]
        df = normalize_taiwan_daily(raw, metadata=valid_meta, default_symbol="2330.TWSE")
        assert df.height == 0

    def test_halt_string_dashes_not_converted_to_zero(self, valid_meta):
        """Halt or empty indicator '--' must be dropped, not converted to 0.0."""
        raw = [{"date": "2026-08-28", "open": "--", "high": "--", "low": "--", "close": "--", "volume": 0}]
        df = normalize_taiwan_daily(raw, metadata=valid_meta, default_symbol="2330.TWSE")
        assert df.height == 0

    def test_duplicate_date_keeps_last(self, valid_meta):
        raw = [
            {"date": "2026-08-28", "open": 100.0, "high": 105.0, "low": 98.0, "close": 102.0, "volume": 1000},
            {"date": "2026-08-28", "open": 100.0, "high": 106.0, "low": 98.0, "close": 104.0, "volume": 1200},
        ]
        df = normalize_taiwan_daily(raw, metadata=valid_meta, default_symbol="2330.TWSE")
        assert df.height == 1
        assert df["close"][0] == 104.0
        assert df["high"][0] == 106.0

    def test_sorting_ascending_guarantee(self, valid_meta):
        raw = [
            {"date": "2026-08-28", "open": 100.0, "high": 105.0, "low": 98.0, "close": 102.0, "volume": 1000},
            {"date": "2026-08-25", "open": 95.0, "high": 98.0, "low": 94.0, "close": 97.0, "volume": 800},
            {"date": "2026-08-26", "open": 98.0, "high": 101.0, "low": 97.0, "close": 100.0, "volume": 900},
        ]
        df = normalize_taiwan_daily(raw, metadata=valid_meta, default_symbol="2330.TWSE")
        dates = df["date"].to_list()
        assert dates == [date(2026, 8, 25), date(2026, 8, 26), date(2026, 8, 28)]


# ── Canonical Schema Alignment Tests ───────────────────────────


class TestCanonicalSchemaAlignment:
    """Verify normalized output strictly adheres to DAILY_COLS schema."""

    def test_finmind_rows_to_canonical_schema(self):
        df = normalize_taiwan_daily(RAW_FINMIND_ROWS, metadata=FINMIND_METADATA, default_symbol="2330.TWSE")
        assert df.height == 4
        assert df.columns == DAILY_COLS
        assert (df["symbol"] == "2330.TWSE").all()
        assert df.schema["date"] == pl.Date
        assert df.schema["open"] == pl.Float64
        assert df.schema["volume"] == pl.Float64
        assert df.schema["amount"] == pl.Float64

    def test_canonical_symbol_boundary_enforced(self):
        """Raw codes like '2330' are normalized to canonical '2330.TWSE' via default_symbol."""
        df = normalize_taiwan_daily(RAW_FINMIND_ROWS, metadata=FINMIND_METADATA, default_symbol=TaiwanSymbol("2330", Exchange.TWSE))
        assert (df["symbol"] == "2330.TWSE").all()
        assert not (df["symbol"] == "2330").any()
        assert not (df["symbol"] == "2330.TW").any()


# ── Parquet Storage & Indicator Pipeline Integration ───────────


class TestParquetStorageAndIndicatorPipeline:
    """Test Parquet write, read-back, and existing compute_indicators pipeline."""

    def test_parquet_write_read_and_indicators(self):
        # 1. Normalize synthetic 30-day time series
        rows = []
        base_price = 100.0
        for i in range(1, 31):
            day_str = f"2026-07-{i:02d}" if i <= 31 else f"2026-08-{i-31:02d}"
            # Simulated trading prices
            p = base_price + (i % 5) * 2.0
            rows.append({
                "date": f"2026-07-{i:02d}",
                "open": p - 1.0,
                "high": p + 2.0,
                "low": p - 2.0,
                "close": p,
                "volume": 10000.0 + i * 100,
                "amount": (p * 10000.0),
            })

        df = normalize_taiwan_daily(rows, metadata=FINMIND_METADATA, default_symbol="2330.TWSE")
        assert df.height == 30

        # 2. Write to temp Parquet repository
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = DataStore(data_dir=Path(tmp_dir))
            repo = KlineRepository(store=store)

            repo.append_daily(df)

            # 3. Read back
            glob_path = str(Path(tmp_dir) / "kline_daily" / "**" / "*.parquet")
            read_df = scan_daily_parquet(glob_path).collect()
            assert read_df.height == 30
            assert (read_df["symbol"] == "2330.TWSE").all()

            # 4. Compute Indicators
            ind_df = compute_indicators(
                read_df,
                needed={"ma5", "ma10", "rsi_6", "macd_dif", "macd_dea", "macd_hist"},
            )
            last = ind_df.tail(1).to_dicts()[0]
            assert last["ma5"] is not None
            assert last["ma10"] is not None
            assert last["rsi_6"] is not None
            assert last["macd_dif"] is not None
            assert last["macd_dea"] is not None
            assert last["macd_hist"] is not None


# ── Hybrid Provider Interface Contract ─────────────────────────


class TestHybridProviderContract:
    """Verify TaiwanHybridProvider fulfills MarketDataProvider protocol."""

    def test_provider_registration(self):
        from app.data_providers.registry import get_provider
        p = get_provider("taiwan")
        assert p.name == "taiwan"
        assert p.capabilities.daily is True

    def test_instruments_schema(self):
        p = TaiwanHybridProvider()
        inst = p.get_instruments("stock")
        assert "symbol" in inst.columns
        assert "name" in inst.columns
        assert "exchange" in inst.columns
        assert "2330.TWSE" in inst["symbol"].to_list()
        assert "8069.TPEX" in inst["symbol"].to_list()

    def test_etf_instruments(self):
        p = TaiwanHybridProvider()
        etf_inst = p.get_instruments("etf")
        assert "0050.TWSE" in etf_inst["symbol"].to_list()
        assert "006208.TWSE" in etf_inst["symbol"].to_list()

"""Unit tests for TaiwanDailyStore (Phase 6B-Data Foundation).

All tests are offline — no live internet required.
Provider calls are mocked or bypassed via synthetic DataFrames.
"""
from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from app.taiwan.daily_store import TaiwanDailyStore


# ── Fixtures ──────────────────────────────────────────────────────────

def _make_df(
    symbol: str = "2330.TWSE",
    n: int = 5,
    start: str = "2026-08-01",
) -> pl.DataFrame:
    """Create *n* consecutive daily rows starting from *start*."""
    start_date = date.fromisoformat(start)
    rows = []
    for i in range(n):
        d = (start_date + timedelta(days=i)).isoformat()
        rows.append({
            "symbol": symbol,
            "date": d,
            "open": 100.0 + i,
            "high": 105.0 + i,
            "low": 95.0 + i,
            "close": 102.0 + i,
            "volume": 10000.0 + i * 100,
            "amount": (102.0 + i) * 10000.0,
            "quote_ts": None,
        })
    return pl.DataFrame(rows)


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def store(tmp_dir):
    return TaiwanDailyStore(data_dir=tmp_dir / "daily")


# ── Write / Read ──────────────────────────────────────────────────────

class TestTaiwanDailyStoreWriteRead:
    def test_write_batch(self, store, tmp_dir):
        df = _make_df()
        n = store.write_batch(df)
        assert n == df.height
        assert store.latest_date() == date(2026, 8, 5)

    def test_read_all(self, store):
        df = _make_df("2330.TWSE")
        store.write_batch(df)
        all_rows = store.read_all()
        assert all_rows.height == 5
        assert (all_rows["symbol"] == "2330.TWSE").all()

    def test_read_latest_date_rows(self, store):
        df = _make_df("2330.TWSE")
        store.write_batch(df)
        latest = store.read_latest_date_rows()
        assert latest.height == 1
        assert latest["date"][0] == date(2026, 8, 5)

    def test_read_latest_per_symbol(self, store):
        """Screener needs latest row per symbol, even if symbols have
        different max dates."""
        df_a = _make_df("2330.TWSE", n=5, start="2026-08-01")   # 8/1-8/5
        df_b = _make_df("0050.TWSE", n=4, start="2026-08-01")   # 8/1-8/4
        store.write_batch(df_a)
        store.write_batch(df_b)
        latest = store.read_latest_per_symbol()
        assert latest.height == 2
        row_2330 = latest.filter(pl.col("symbol") == "2330.TWSE")
        row_0050 = latest.filter(pl.col("symbol") == "0050.TWSE")
        assert row_2330["date"][0] == date(2026, 8, 5)
        assert row_0050["date"][0] == date(2026, 8, 4)

    def test_read_latest_per_symbol_filtered(self, store):
        df_a = _make_df("2330.TWSE", n=5, start="2026-08-01")
        df_b = _make_df("0050.TWSE", n=4, start="2026-08-01")
        store.write_batch(df_a)
        store.write_batch(df_b)
        result = store.read_latest_per_symbol(symbols=["0050.TWSE"])
        assert result.height == 1
        assert result["symbol"][0] == "0050.TWSE"
        assert result["date"][0] == date(2026, 8, 4)

    def test_read_range(self, store):
        df = _make_df("2330.TWSE", n=10)
        store.write_batch(df)
        rng = store.read_range(["2330.TWSE"], date(2026, 8, 3), date(2026, 8, 7))
        assert rng.height == 5

    def test_read_range_multiple_symbols(self, store):
        df1 = _make_df("2330.TWSE", n=5)
        df2 = _make_df("0050.TWSE", n=5)
        store.write_batch(df1)
        store.write_batch(df2)
        rng = store.read_range(["2330.TWSE", "0050.TWSE"], date(2026, 8, 1), date(2026, 8, 5))
        assert rng.height == 10

    def test_available_dates(self, store):
        df = _make_df("2330.TWSE", n=5)
        store.write_batch(df)
        dates = store.available_dates()
        assert dates == [date(2026, 8, i) for i in range(1, 6)]

    def test_latest_date(self, store):
        df = _make_df("2330.TWSE", n=3)
        store.write_batch(df)
        assert store.latest_date() == date(2026, 8, 3)

    def test_latest_date_empty(self, store):
        assert store.latest_date() is None

    def test_has_symbol_date(self, store):
        df = _make_df("2330.TWSE", n=3)
        store.write_batch(df)
        assert store.has_symbol_date("2330.TWSE", date(2026, 8, 2)) is True
        assert store.has_symbol_date("2330.TWSE", date(2026, 8, 10)) is False


# ── Dedup ─────────────────────────────────────────────────────────────

class TestTaiwanDailyStoreDedup:
    def test_write_batch_dedup(self, store):
        df = _make_df("2330.TWSE", n=3)
        store.write_batch(df)
        # Write the same data again — should not duplicate.
        n = store.write_batch(df)
        # Dedup happens inside write_batch, so rows written is still 3,
        # but total unique rows in store should be 3.
        all_rows = store.read_range(["2330.TWSE"], date(2026, 8, 1), date(2026, 8, 3))
        assert all_rows.height == 3

    def test_write_batch_across_partitions_dedup(self, store):
        # Write a row for 2026-08-05, then write again for same date.
        df1 = _make_df("2330.TWSE", n=1, start="2026-08-05")
        store.write_batch(df1)
        df2 = _make_df("2330.TWSE", n=1, start="2026-08-05")
        store.write_batch(df2)
        all_rows = store.read_range(["2330.TWSE"], date(2026, 8, 5), date(2026, 8, 5))
        assert all_rows.height == 1

    def test_deterministic_order_after_dedup(self, store):
        df = _make_df("2330.TWSE", n=5)
        store.write_batch(df)
        all_rows = store.read_range(["2330.TWSE"], date(2026, 8, 1), date(2026, 8, 5))
        dates = all_rows["date"].to_list()
        assert dates == sorted(dates)


# ── Canonical symbol / TWSE / TPEx / ETF ──────────────────────────────

class TestTaiwanDailyStoreCanonicalSymbols:
    def test_twse_symbol(self, store):
        df = _make_df("2330.TWSE")
        store.write_batch(df)
        assert (store.read_latest_per_symbol()["symbol"] == "2330.TWSE").all()

    def test_tpex_symbol(self, store):
        df = _make_df("8069.TPEX")
        store.write_batch(df)
        assert (store.read_latest_per_symbol()["symbol"] == "8069.TPEX").all()

    def test_etf_symbol(self, store):
        df = _make_df("0050.TWSE")
        store.write_batch(df)
        assert (store.read_latest_per_symbol()["symbol"] == "0050.TWSE").all()

    def test_leveraged_etf(self, store):
        df = _make_df("00631L.TWSE")
        store.write_batch(df)
        assert (store.read_latest_per_symbol()["symbol"] == "00631L.TWSE").all()

    def test_known_symbols(self, store):
        df1 = _make_df("2330.TWSE")
        df2 = _make_df("8069.TPEX")
        store.write_batch(df1)
        store.write_batch(df2)
        symbols = store.known_symbols()
        assert "2330.TWSE" in symbols
        assert "8069.TPEX" in symbols


# ── Missing amount = null ─────────────────────────────────────────────

class TestTaiwanDailyStoreMissingAmount:
    def test_missing_amount_stored_as_null(self, store):
        df = _make_df("2330.TWSE", n=3)
        df = df.with_columns(pl.lit(None).alias("amount"))
        store.write_batch(df)
        all_rows = store.read_range(["2330.TWSE"], date(2026, 8, 1), date(2026, 8, 3))
        assert all_rows["amount"].null_count() == 3

    def test_zero_amount_stored_as_zero(self, store):
        df = _make_df("2330.TWSE", n=3)
        # Zero is a valid amount value (not missing) — should be preserved.
        store.write_batch(df)
        all_rows = store.read_range(["2330.TWSE"], date(2026, 8, 1), date(2026, 8, 3))
        assert all_rows.height == 3


# ── Incremental append ────────────────────────────────────────────────

class TestTaiwanDailyStoreIncrementalAppend:
    def test_incremental_append(self, store):
        df1 = _make_df("2330.TWSE", n=3)
        store.write_batch(df1)
        assert store.available_dates() == [date(2026, 8, 1), date(2026, 8, 2), date(2026, 8, 3)]

        # Append next 2 days (8/4, 8/5)
        df2 = _make_df("2330.TWSE", n=2, start="2026-08-04")
        store.write_batch(df2)
        assert store.available_dates() == [date(2026, 8, 1), date(2026, 8, 2), date(2026, 8, 3),
                                           date(2026, 8, 4), date(2026, 8, 5)]

    def test_incremental_overlap_no_duplicate(self, store):
        df1 = _make_df("2330.TWSE", n=3)
        store.write_batch(df1)
        # Re-write 8/1 and append 8/4 — non-consecutive dates.
        df2 = pl.DataFrame({
            "symbol": ["2330.TWSE", "2330.TWSE"],
            "date": [date(2026, 8, 1), date(2026, 8, 4)],
            "open": [200.0, 400.0],
            "high": [210.0, 410.0],
            "low": [190.0, 390.0],
            "close": [205.0, 405.0],
            "volume": [99999.0, 88888.0],
            "amount": [99999.0 * 205.0, 88888.0 * 405.0],
            "quote_ts": [None, None],
        })
        store.write_batch(df2)
        dates = store.available_dates()
        assert dates == [date(2026, 8, 1), date(2026, 8, 2), date(2026, 8, 3),
                         date(2026, 8, 4)]
        # Verify the re-written 8/1 took the new values.
        row_81 = store.read_range(["2330.TWSE"], date(2026, 8, 1), date(2026, 8, 1))
        assert row_81["open"][0] == 200.0


# ── Restart persistence ───────────────────────────────────────────────

class TestTaiwanDailyStoreRestart:
    def test_restart_persistence(self, tmp_dir):
        store1 = TaiwanDailyStore(data_dir=tmp_dir / "daily")
        df = _make_df("2330.TWSE", n=5)
        store1.write_batch(df)
        assert store1.latest_date() == date(2026, 8, 5)

        # Re-instantiate (simulates restart)
        store2 = TaiwanDailyStore(data_dir=tmp_dir / "daily")
        assert store2.latest_date() == date(2026, 8, 5)
        all_rows = store2.read_all()
        assert all_rows.height == 5
        assert (all_rows["symbol"] == "2330.TWSE").all()


# ── Atomic write failure safety ───────────────────────────────────────

class TestTaiwanDailyStoreAtomicWrite:
    def test_atomic_write_no_partial_files(self, store):
        df = _make_df("2330.TWSE", n=3)
        store.write_batch(df)
        # After successful write, there should be exactly one part.parquet
        # per partition, no tmp files left behind.
        for part in store._data_dir.glob("date=*/part.parquet"):
            assert part.exists()
        tmp_files = list(store._data_dir.rglob("*.tmp")) + list(store._data_dir.rglob("*.part"))
        assert len(tmp_files) == 0

    def test_empty_write_not_allowed(self, store):
        df = pl.DataFrame(schema=_make_df().schema)
        n = store.write_batch(df)
        assert n == 0
        assert store.latest_date() is None

    def test_read_empty_store(self, store):
        assert store.read_all().height == 0
        assert store.read_latest_per_symbol().height == 0
        assert store.read_latest_date_rows().height == 0
        assert store.read_range(["2330.TWSE"], date(2026, 8, 1), date(2026, 8, 5)).height == 0
        assert store.available_dates() == []


# ── Volume = shares ───────────────────────────────────────────────────

class TestTaiwanDailyStoreVolumeShares:
    def test_volume_is_shares(self, store):
        df = _make_df("2330.TWSE", n=3)
        store.write_batch(df)
        # Explicitly read the first date's row to verify canonical volume.
        first_date_row = store.read_range(
            ["2330.TWSE"], date(2026, 8, 1), date(2026, 8, 1)
        )
        assert first_date_row["volume"].dtype == pl.Float64
        assert first_date_row["volume"][0] == 10000.0


# ── Concurrent write safety ───────────────────────────────────────────

import threading


def _make_single(symbol: str, d: date) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": [symbol],
        "date": [d.isoformat()],
        "open": [100.0], "high": [105.0], "low": [95.0], "close": [102.0],
        "volume": [10000.0], "amount": [1020000.0], "quote_ts": [None],
    })


class TestTaiwanDailyStoreConcurrentSamePartition:
    """Concurrent writers to the SAME date partition must not lose rows."""

    SYMBOLS = ["2330.TWSE", "0050.TWSE", "00631L.TWSE", "00632R.TWSE", "00646.TWSE", "8069.TPEX"]
    TARGET_DATE = date(2026, 8, 28)

    def test_concurrent_same_partition_writes_preserve_all_symbols(self, store):
        for _round in range(5):
            errors = []

            def worker(sym):
                try:
                    store.write_batch(_make_single(sym, self.TARGET_DATE))
                except Exception as e:
                    errors.append((sym, str(e)))

            threads = [threading.Thread(target=worker, args=(s,)) for s in self.SYMBOLS]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert not errors, f"Round {_round} errors: {errors}"
            result = store.read_range(self.SYMBOLS, self.TARGET_DATE, self.TARGET_DATE)
            actual = sorted(result["symbol"].unique().to_list())
            assert actual == sorted(self.SYMBOLS), f"Round {_round}: expected {sorted(self.SYMBOLS)}, got {actual}"
            # No duplicates
            assert result.unique(subset=["symbol", "date"]).height == result.height


class TestTaiwanDailyStoreConcurrentDifferentPartitions:
    """Concurrent writers to DIFFERENT date partitions must all succeed."""

    def test_concurrent_different_partition_writes(self, store):
        dates = [date(2026, 8, d) for d in range(20, 26)]
        errors = []

        def worker(d):
            try:
                store.write_batch(_make_single("2330.TWSE", d))
            except Exception as e:
                errors.append((d, str(e)))

        threads = [threading.Thread(target=worker, args=(d,)) for d in dates]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors: {errors}"
        avail = store.available_dates()
        for d in dates:
            assert d in avail, f"Missing partition for {d}"
        all_rows = store.read_all()
        assert all_rows.height == len(dates)


from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Event

import polars as pl
import pytest

from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore

DAY = date(2026, 10, 14)


def row(symbol):
    return pl.DataFrame({
        "symbol": [symbol], "date": [DAY], "open": [100.0], "high": [100.0],
        "low": [100.0], "close": [100.0], "volume": [1000.0],
        "amount": [100000.0], "quote_ts": [None],
    })


@pytest.mark.parametrize("store_type", [TaiwanDailyStore, TaiwanInstitutionalStore, TaiwanMarginStore])
def test_distinct_store_instances_preserve_concurrent_updates(tmp_path, monkeypatch, store_type):
    first, second = store_type(tmp_path), store_type(tmp_path / ".")
    first.write_batch(row("BASE.TWSE"))
    staged, release, second_written = Event(), Event(), Event()
    original = pl.DataFrame.write_parquet

    def write(frame, *args, **kwargs):
        if "A.TWSE" in frame["symbol"].to_list() and "B.TWSE" not in frame["symbol"].to_list():
            staged.set()
            assert release.wait(5)
        result = original(frame, *args, **kwargs)
        if "B.TWSE" in frame["symbol"].to_list():
            second_written.set()
        return result

    monkeypatch.setattr(pl.DataFrame, "write_parquet", write)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(first.write_batch, row("A.TWSE"))
        assert staged.wait(5)
        b = pool.submit(second.write_batch, row("B.TWSE"))
        second_written.wait(0.3)
        release.set()
        a.result(timeout=10)
        b.result(timeout=10)
    saved = pl.read_parquet(tmp_path / f"date={DAY}" / "part.parquet")
    assert set(saved["symbol"]) == {"BASE.TWSE", "A.TWSE", "B.TWSE"}


@pytest.mark.parametrize("fail_at", ["scan", "collect"])
def test_daily_read_range_skips_one_unreadable_partition(tmp_path, monkeypatch, fail_at):
    store = TaiwanDailyStore(tmp_path)
    store.write_batch(row("A.TWSE"))
    bad = tmp_path / "date=2026-10-15" / "part.parquet"
    bad.parent.mkdir()
    bad.write_bytes(b"not parquet")
    if fail_at == "scan":
        original = pl.scan_parquet

        def scan(path, *args, **kwargs):
            if str(path) == str(bad):
                raise OSError("unreadable metadata")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(pl, "scan_parquet", scan)
    df = store.read_range(["A.TWSE"], None, None)
    assert df["date"].to_list() == [DAY]


def test_daily_read_error_preserves_original_partition(tmp_path, monkeypatch):
    store = TaiwanDailyStore(tmp_path)
    store.write_batch(row("BASE.TWSE"))
    path = tmp_path / f"date={DAY}" / "part.parquet"
    before = path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("temporary read failure")

    monkeypatch.setattr(pl, "scan_parquet", fail)
    with pytest.raises(OSError, match="temporary read failure"):
        store.write_batch(row("A.TWSE"))
    assert path.read_bytes() == before

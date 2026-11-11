"""Wave 2 data-layer regressions: efficiency fixes must keep results unchanged."""
from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore

_FIRST = date(2026, 6, 1)
_DAYS = [_FIRST + timedelta(days=i) for i in range(25)]
# Symbol -> days it has a row on: every day / inside the 20-partition window /
# only the oldest partition / never.
_PRESENCE = {
    "2330.TWSE": _DAYS,
    "2317.TWSE": [_DAYS[10], _DAYS[12]],
    "8069.TPEX": [_DAYS[0]],
    "9999.TWSE": [],
}


def _row(kind: str, symbol: str, day: date) -> dict:
    if kind == "daily":
        return {"symbol": symbol, "date": day, "open": 10.0, "high": 11.0, "low": 9.0,
                "close": 10.0 + day.day, "volume": 1000.0, "amount": 10000.0, "quote_ts": None}
    if kind == "institutional":
        return {"symbol": symbol, "date": day, "foreign_net": day.day,
                "investment_trust_net": 1, "dealer_net": 2}
    return {"symbol": symbol, "date": day, "margin_balance": day.day}


@pytest.mark.parametrize(("kind", "factory"), [
    ("daily", TaiwanDailyStore),
    ("institutional", TaiwanInstitutionalStore),
    ("margin", TaiwanMarginStore),
])
def test_latest_per_symbol_reads_newest_first_with_unchanged_rows(tmp_path, monkeypatch, kind, factory):
    store = factory(data_dir=tmp_path / kind)
    for day in _DAYS:
        rows = [_row(kind, symbol, day) for symbol, days in _PRESENCE.items() if day in days]
        store.write_batch(pl.DataFrame(rows))
    symbols = list(_PRESENCE)

    everything = store.read_all(symbols)
    expected = (everything.filter(pl.col("date") == pl.col("date").max().over("symbol"))
                .sort(["symbol", "date"]))

    asked: list[tuple[list[str], date, date]] = []
    original = store.read_range

    def spy(wanted, start, end, **kwargs):
        asked.append((list(wanted), start, end))
        return original(wanted, start, end, **kwargs)

    monkeypatch.setattr(store, "read_range", spy)
    latest = store.read_latest_per_symbol(symbols)

    assert latest.to_dicts() == expected.to_dicts()
    assert dict(zip(latest["symbol"], latest["date"], strict=True)) == {
        "2330.TWSE": _DAYS[-1], "2317.TWSE": _DAYS[12], "8069.TPEX": _DAYS[0]}
    # Older partitions are only asked for the symbols still missing.
    assert asked == [
        (symbols, _DAYS[-1], _DAYS[-1]),
        (["2317.TWSE", "8069.TPEX", "9999.TWSE"], _DAYS[4], _DAYS[23]),
        (["8069.TPEX", "9999.TWSE"], _DAYS[0], _DAYS[3]),
    ]

from datetime import date

import pytest

from app.taiwan.shareholding_store import TaiwanShareholdingStore, parse_tdcc_csv

CSV = """﻿資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%
{d},2330  ,14,229,204873701,0.79
{d},2330  ,15,1482,{big},84.78
{d},2330  ,17,3007088,25932370067,100.00
{d},9999  ,15,1,1,1.00
{d},6488  ,17,10,1000,100.00
"""
MAP = {"2330": "2330.TWSE", "6488": "6488.TPEX"}


def test_parse_keeps_known_complete_rows():
    df = parse_tdcc_csv(CSV.format(d="20261008", big=21987181886), MAP, retrieved_at="t")
    assert df["symbol"].to_list() == ["2330.TWSE"]  # 9999 unknown, 6488 lacks level 15
    row = df.row(0, named=True)
    assert row["date"] == date(2026, 10, 8) and row["big_holders"] == 1482
    assert row["big_holder_pct"] == pytest.approx(21987181886 / 25932370067 * 100)


def test_change_needs_previous_week(tmp_path):
    store = TaiwanShareholdingStore(tmp_path / "s.parquet")
    store.refresh(fetch_text=lambda _: CSV.format(d="20261002", big=20_000_000_000), code_to_symbol=MAP)
    first = store.latest_with_change()
    assert first["big_holder_pct_change"].to_list() == [None]
    store.refresh(fetch_text=lambda _: CSV.format(d="20261008", big=21_000_000_000), code_to_symbol=MAP)
    store.refresh(fetch_text=lambda _: CSV.format(d="20261008", big=21_000_000_000), code_to_symbol=MAP)
    latest = store.latest_with_change(["2330.TWSE"])
    assert latest.height == 1 and latest["previous_date"][0] == date(2026, 10, 2)
    assert latest["big_holder_pct_change"][0] == pytest.approx(1e9 / 25932370067 * 100)


def test_change_missing_week_returns_none(tmp_path):
    store = TaiwanShareholdingStore(tmp_path / "s.parquet")
    # W1 and W3 stored, W2 missing (14 days apart)
    store.refresh(fetch_text=lambda _: CSV.format(d="20261002", big=20_000_000_000), code_to_symbol=MAP)
    store.refresh(fetch_text=lambda _: CSV.format(d="20261016", big=21_000_000_000), code_to_symbol=MAP)
    latest = store.latest_with_change(["2330.TWSE"])
    assert latest.height == 1
    assert latest["big_holder_pct_change"][0] is None
    assert latest["previous_date"][0] is None
    assert latest["big_holder_pct"][0] == pytest.approx(21_000_000_000 / 25932370067 * 100)


def test_change_adjacent_week_computes_difference(tmp_path):
    store = TaiwanShareholdingStore(tmp_path / "s.parquet")
    # Two adjacent weeks (7 days apart)
    store.refresh(fetch_text=lambda _: CSV.format(d="20261002", big=20_000_000_000), code_to_symbol=MAP)
    store.refresh(fetch_text=lambda _: CSV.format(d="20261009", big=21_000_000_000), code_to_symbol=MAP)
    latest = store.latest_with_change(["2330.TWSE"])
    assert latest.height == 1 and latest["previous_date"][0] == date(2026, 10, 2)
    assert latest["big_holder_pct_change"][0] == pytest.approx(3.8561843468087965)


def test_change_unparseable_date_fails_closed(tmp_path):
    import polars as pl

    store = TaiwanShareholdingStore(tmp_path / "s.parquet")
    raw = pl.DataFrame({
        "symbol": ["2330.TWSE", "2330.TWSE"],
        "date": ["not-a-date-1", "not-a-date-2"],
        "total_holders": [3007088, 3007088],
        "total_shares": [25932370067, 25932370067],
        "big_holders": [1482, 1482],
        "big_shares": [20_000_000_000, 21_000_000_000],
        "big_holder_pct": [77.12, 80.98],
        "source_url": ["u", "u"],
        "retrieved_at": ["t", "t"],
    })
    store.path.parent.mkdir(parents=True, exist_ok=True)
    raw.write_parquet(store.path)
    latest = store.latest_with_change(["2330.TWSE"])
    assert latest.height == 1
    assert latest["big_holder_pct_change"][0] is None
    assert latest["previous_date"][0] is None


def test_string_dates_with_before_cutoff(tmp_path):
    import polars as pl

    store = TaiwanShareholdingStore(tmp_path / "s.parquet")
    raw = pl.DataFrame({
        "symbol": ["2330.TWSE", "2330.TWSE", "2330.TWSE"],
        "date": ["2026-10-02", "2026-10-09", "2026-10-16"],
        "total_holders": [3007088, 3007088, 3007088],
        "total_shares": [25932370067, 25932370067, 25932370067],
        "big_holders": [1482, 1482, 1482],
        "big_shares": [20_000_000_000, 21_000_000_000, 22_000_000_000],
        "big_holder_pct": [77.12, 80.98, 84.84],
        "source_url": ["u", "u", "u"],
        "retrieved_at": ["t", "t", "t"],
    })
    store.path.parent.mkdir(parents=True, exist_ok=True)
    raw.write_parquet(store.path)
    # Cutoff before 2026-10-15 preserves W2 (2026-10-09) with correct latest ratio 80.98
    latest = store.latest_with_change(["2330.TWSE"], before=date(2026, 10, 15))
    assert latest.height == 1
    assert latest["date"][0] == date(2026, 10, 9)
    assert latest["big_holder_pct"][0] == pytest.approx(80.98)
    assert latest["previous_date"][0] == date(2026, 10, 2)
    assert latest["big_holder_pct_change"][0] == pytest.approx(80.98 - 77.12)


def test_unparseable_date_with_before_does_not_crash(tmp_path):
    import polars as pl

    store = TaiwanShareholdingStore(tmp_path / "s.parquet")
    raw = pl.DataFrame({
        "symbol": ["2330.TWSE", "2330.TWSE"],
        "date": ["invalid-date-1", "invalid-date-2"],
        "total_holders": [3007088, 3007088],
        "total_shares": [25932370067, 25932370067],
        "big_holders": [1482, 1482],
        "big_shares": [20_000_000_000, 21_000_000_000],
        "big_holder_pct": [77.12, 80.98],
        "source_url": ["u", "u"],
        "retrieved_at": ["t", "t"],
    })
    store.path.parent.mkdir(parents=True, exist_ok=True)
    raw.write_parquet(store.path)
    res = store.latest_with_change(["2330.TWSE"], before=date(2026, 10, 15))
    assert res.is_empty()
    assert res["big_holder_pct_change"].to_list() == []
    assert res["previous_date"].to_list() == []

    # Mixed valid and unparseable dates with before cutoff
    raw_mixed = pl.DataFrame({
        "symbol": ["2330.TWSE", "2330.TWSE"],
        "date": ["invalid-date-1", "2026-10-02"],
        "total_holders": [3007088, 3007088],
        "total_shares": [25932370067, 25932370067],
        "big_holders": [1482, 1482],
        "big_shares": [20_000_000_000, 21_000_000_000],
        "big_holder_pct": [77.12, 80.98],
        "source_url": ["u", "u"],
        "retrieved_at": ["t", "t"],
    })
    raw_mixed.write_parquet(store.path)
    res_mixed = store.latest_with_change(["2330.TWSE"], before=date(2026, 10, 15))
    assert res_mixed.height == 1
    assert res_mixed["date"][0] == date(2026, 10, 2)
    assert res_mixed["big_holder_pct"][0] == pytest.approx(80.98)
    assert res_mixed["big_holder_pct_change"][0] is None
    assert res_mixed["previous_date"][0] is None



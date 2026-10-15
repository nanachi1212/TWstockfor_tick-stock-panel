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

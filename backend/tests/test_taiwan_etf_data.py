from __future__ import annotations

import httpx
import pytest

from app.taiwan.etf_data import (
    TaiwanOfficialETFData,
)

ROW = {
    "出表日期": "1150830", "基金代號": "0050", "基金簡稱": "元大台灣50",
    "基金類型": "國內成分證券指數股票型基金", "標的指數/追蹤指數名稱": "臺灣50指數",
    "成立日期": "0920625", "上市日期": "0920630", "發行單位數/轉換數": "22360500000",
}


def _provider(row=ROW):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[row]))
    return TaiwanOfficialETFData(httpx.Client(transport=transport))


def test_official_profile_and_snapshot_preserve_semantics_and_provenance():
    profile = _provider().profile("0050.TWSE", "TWSE")
    assert profile.name == "元大台灣50" and profile.benchmark == "臺灣50指數"
    assert profile.leverage_multiplier is None and profile.inverse is None
    snapshot = _provider().snapshot("0050.TWSE", "TWSE")
    assert snapshot.issued_units == 22_360_500_000 and snapshot.outstanding_units is None
    assert snapshot.nav is None and snapshot.aum is None and snapshot.market_price is None
    assert snapshot.available_at is None and snapshot.status == "data_insufficient"
    assert snapshot.provider == "TWSE" and snapshot.raw_unit == snapshot.normalized_unit == "units"


def test_missing_and_zero_are_distinct():
    missing = {**ROW, "發行單位數/轉換數": "--"}
    zero = {**ROW, "發行單位數/轉換數": "0"}
    assert _provider(missing).snapshot("0050.TWSE", "TWSE").issued_units is None
    assert _provider(zero).snapshot("0050.TWSE", "TWSE").issued_units == 0


def test_leveraged_codes_do_not_create_multiplier_without_official_field():
    for code in ("00631L", "00632R"):
        profile = _provider({**ROW, "基金代號": code}).profile(f"{code}.TWSE", "TWSE")
        assert profile.leverage_multiplier is None and profile.inverse is None


@pytest.mark.parametrize("raw_name,expected_name", [
    ("元大S&amp;P500", "元大S&P500"),
    ("元大S&#38;P500", "元大S&P500"),
    ("元大S&#x26;P500", "元大S&P500"),
    ("元大S&P500", "元大S&P500"),
])
def test_official_profile_normalizes_html_entities(raw_name, expected_name):
    row = {
        **ROW,
        "基金代號": "00646",
        "基金簡稱": raw_name,
        "標的指數/追蹤指數名稱": "S&amp;P 500 指數",
    }
    profile = _provider(row).profile("00646.TWSE", "TWSE")
    assert profile.name == expected_name
    assert profile.benchmark == "S&P 500 指數"

from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from app.taiwan.etf_data import (
    TaiwanETFDistribution,
    TaiwanETFHolding,
    TaiwanETFSnapshot,
    TaiwanETFSnapshotStore,
    TaiwanOfficialETFData,
    calculate_premium_discount,
    latest_snapshot_as_of,
)
from app.taiwan.providers.taiwan_values import TAIPEI

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


def test_premium_discount_requires_same_date_and_real_nav():
    assert calculate_premium_discount(11, 10, price_date="2026-08-30", nav_date="2026-08-30") == pytest.approx(0.1)
    assert calculate_premium_discount(11, 10, price_date="2026-08-30", nav_date="2026-08-29") is None
    assert calculate_premium_discount(11, None, price_date="2026-08-30", nav_date="2026-08-30") is None


def test_holdings_coverage_and_distribution_are_separate_models():
    now = datetime.now(TAIPEI)
    holding = TaiwanETFHolding("0050.TWSE", "2330.TWSE", "台積電", 0.5, "2026-08-30", "TWD", "TWSE", "fixture", "https://example.invalid", now, "official", "partial")
    distribution = TaiwanETFDistribution("0050.TWSE", 1.0, None, None, None, "TWD", "TWSE", "fixture", "https://example.invalid", now, "data_insufficient")
    assert holding.coverage == "partial" and distribution.normalized_unit == "TWD_per_unit"


def test_snapshot_store_and_strict_point_in_time(tmp_path):
    available = datetime(2026, 8, 30, 18, tzinfo=TAIPEI)
    row = TaiwanETFSnapshot("0050.TWSE", "2026-08-30", 10, None, 11, 0.1, None, 100, None, "TWD", "TWSE", "fixture", "https://example.invalid", datetime.now(TAIPEI), "official", "mixed", "canonical", available_at=available)
    store = TaiwanETFSnapshotStore(tmp_path)
    assert store.save([row]) == 1
    assert latest_snapshot_as_of(store.load(), row.symbol, available) is None
    assert latest_snapshot_as_of(store.load(), row.symbol, datetime(2026, 8, 30, 18, 0, 1, tzinfo=TAIPEI)) == row


def test_leveraged_codes_do_not_create_multiplier_without_official_field():
    for code in ("00631L", "00632R"):
        profile = _provider({**ROW, "基金代號": code}).profile(f"{code}.TWSE", "TWSE")
        assert profile.leverage_multiplier is None and profile.inverse is None

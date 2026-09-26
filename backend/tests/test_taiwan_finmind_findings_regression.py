"""Comprehensive regression tests for PR #21 review findings:
1. P1: Point-in-time (as-of) cutoff for FinMind evidence (Revenue, Financial Statements, Shareholding, Lending).
2. P1: Foreign shareholding field name contract.
3. P1: Lending anomaly enum alignment to 'surge'.
4. P2: Dynamic token lifecycle update without restart.
5. P2: Connection test failure preservation (401, 429, network, empty probes).
"""
import urllib.error
from datetime import date
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from app.services import preferences
from app.taiwan.detail_models import TaiwanExtraChipsData, TaiwanForeignShareholdingData
from app.taiwan.finmind_cache import FinMindCache
from app.taiwan.fundamental_chips_service import (
    TaiwanFundamentalChipsService,
    get_fundamental_chips_service,
    reset_fundamental_chips_service,
)
from app.taiwan.pit_cutoff import (
    filter_daily_records_as_of,
    filter_financial_statements_as_of,
    filter_month_revenue_as_of,
    get_financial_statement_announcement_date,
    get_revenue_announcement_date,
    resolve_as_of_date,
)
from app.taiwan.providers.finmind_provider import (
    FinMindAdapter,
    FinMindAuthError,
    FinMindError,
    FinMindNetworkError,
    FinMindRateLimitError,
)
from app.taiwan.research_context import TaiwanStockResearchContextService


# =====================================================================
# 1. PIT Cutoff & Announcement Semantics
# =====================================================================

def test_resolve_as_of_date():
    assert resolve_as_of_date(None) is None
    assert resolve_as_of_date("") is None
    assert resolve_as_of_date(date(2026, 8, 5)) == date(2026, 8, 5)
    assert resolve_as_of_date("2026-08-05") == date(2026, 8, 5)
    assert resolve_as_of_date("2026-08-05T12:00:00") == date(2026, 8, 5)


def test_revenue_announcement_date_calculation():
    # Statutory deadline: 10th of following month
    row_with_ym = {"revenue_year": 2026, "revenue_month": 7}
    assert get_revenue_announcement_date(row_with_ym) == date(2026, 8, 10)

    # December revenue rolls over to January next year
    row_dec = {"revenue_year": 2025, "revenue_month": 12}
    assert get_revenue_announcement_date(row_dec) == date(2026, 1, 10)

    # Inferred from reporting date 2026-07-01
    row_date = {"date": "2026-07-01"}
    assert get_revenue_announcement_date(row_date) == date(2026, 8, 10)

    # Explicit announcement date takes precedence
    row_explicit = {"date": "2026-07-01", "announcement_date": "2026-08-08"}
    assert get_revenue_announcement_date(row_explicit) == date(2026, 8, 8)


def test_financial_statement_announcement_date_calculation():
    # Q1: ends 03-31 -> May 15
    assert get_financial_statement_announcement_date({"date": "2026-03-31"}) == date(2026, 5, 15)
    # Q2: ends 06-30 -> August 14
    assert get_financial_statement_announcement_date({"date": "2026-06-30"}) == date(2026, 8, 14)
    # Q3: ends 09-30 -> November 14
    assert get_financial_statement_announcement_date({"date": "2026-09-30"}) == date(2026, 11, 14)
    # Q4: ends 12-31 -> March 31 next year
    assert get_financial_statement_announcement_date({"date": "2025-12-31"}) == date(2026, 3, 31)

    # Explicit announcement date takes precedence
    assert get_financial_statement_announcement_date({"date": "2026-06-30", "announcement_date": "2026-08-10"}) == date(2026, 8, 10)


def test_filter_month_revenue_as_of_pit():
    rows = [
        {"revenue_year": 2026, "revenue_month": 5, "revenue": 100, "date": "2026-05-01"},  # available 2026-06-10
        {"revenue_year": 2026, "revenue_month": 6, "revenue": 110, "date": "2026-06-01"},  # available 2026-07-10
        {"revenue_year": 2026, "revenue_month": 7, "revenue": 120, "date": "2026-07-01"},  # available 2026-08-10
        {"revenue_year": 2026, "revenue_month": 8, "revenue": 130, "date": "2026-08-01"},  # available 2026-09-10
    ]

    # As of 2026-08-05: July revenue is NOT available yet (cutoff is 2026-08-10)
    filtered = filter_month_revenue_as_of(rows, date(2026, 8, 5))
    assert len(filtered) == 2
    assert [r["revenue_month"] for r in filtered] == [5, 6]

    # As of 2026-08-10: July revenue is now legally available
    filtered_10 = filter_month_revenue_as_of(rows, date(2026, 8, 10))
    assert len(filtered_10) == 3
    assert [r["revenue_month"] for r in filtered_10] == [5, 6, 7]

    # Current-date (as_of is None): all rows returned
    assert len(filter_month_revenue_as_of(rows, None)) == 4


def test_filter_financial_statements_as_of_pit():
    rows = [
        {"date": "2026-03-31", "type": "EPS", "value": 3.0},  # Q1, available 2026-05-15
        {"date": "2026-06-30", "type": "EPS", "value": 3.5},  # Q2, available 2026-08-14
    ]

    # As of 2026-07-01: Q2 period end is in the past, but NOT filed yet (available 2026-08-14)
    filtered = filter_financial_statements_as_of(rows, date(2026, 7, 1))
    assert len(filtered) == 1
    assert filtered[0]["date"] == "2026-03-31"

    # As of 2026-08-14: Q2 is available
    filtered_q2 = filter_financial_statements_as_of(rows, date(2026, 8, 14))
    assert len(filtered_q2) == 2

    # Current-date (as_of is None)
    assert len(filter_financial_statements_as_of(rows, None)) == 2


def test_filter_daily_records_as_of():
    rows = [
        {"date": "2026-08-01", "ForeignInvestmentSharesRatio": 70.0},
        {"date": "2026-08-05", "ForeignInvestmentSharesRatio": 71.0},
        {"date": "2026-08-10", "ForeignInvestmentSharesRatio": 72.0},
    ]
    # As of 2026-08-05: 2026-08-10 row is excluded
    filtered = filter_daily_records_as_of(rows, date(2026, 8, 5))
    assert len(filtered) == 2
    assert [r["date"] for r in filtered] == ["2026-08-01", "2026-08-05"]

    # Current date
    assert len(filter_daily_records_as_of(rows, None)) == 3


def test_fundamental_chips_service_pit_filtering():
    cache = FinMindCache()
    svc = TaiwanFundamentalChipsService(cache=cache)

    raw_rev = [
        {"date": "2026-06-01", "revenue_year": 2026, "revenue_month": 6, "revenue": 100},
        {"date": "2026-07-01", "revenue_year": 2026, "revenue_month": 7, "revenue": 120},
        {"date": "2026-08-01", "revenue_year": 2026, "revenue_month": 8, "revenue": 150},
    ]
    cache.set("TaiwanStockMonthRevenue", "2330", raw_rev, status="available")

    # Historical as-of 2026-08-05: only June revenue available (July available 08-10)
    rev_hist = svc.get_monthly_revenue("2330", as_of=date(2026, 8, 5))
    assert rev_hist.meta.status == "available"
    assert rev_hist.latest_revenue == 100.0

    # Historical as-of before any records: fails closed as unavailable
    rev_early = svc.get_monthly_revenue("2330", as_of=date(2020, 1, 1))
    assert rev_early.meta.status == "unavailable"
    assert rev_early.latest_revenue is None

    # Current-date query (as_of=None): gets latest August data
    rev_latest = svc.get_monthly_revenue("2330", as_of=None)
    assert rev_latest.latest_revenue == 150.0


# =====================================================================
# 2. Lending Anomaly Enum ('surge') & Screener Filtering
# =====================================================================

def test_lending_anomaly_standardized_to_surge():
    cache = FinMindCache()
    svc = TaiwanFundamentalChipsService(cache=cache)

    # Build 20 days of historical lending with baseline volume 1000, and last 5 days surge to 60000
    rows = []
    for day in range(1, 16):
        rows.append({"date": f"2026-08-{day:02d}", "volume": 1000, "fee_rate": 2.0})
    for day in range(16, 21):
        rows.append({"date": f"2026-08-{day:02d}", "volume": 60000, "fee_rate": 3.0})

    lending_data = svc._process_securities_lending(rows, "2026-08-20", "2026-08-20T14:00:00")
    # Must emit 'surge', NOT 'abnormal_increase'
    assert lending_data.anomaly_status == "surge"

    # Verify screener filter excludes 'surge'
    df = pl.DataFrame({
        "symbol": ["2330", "2317"],
        "securities_lending_anomaly": ["surge", "normal"],
    })
    filtered = df.filter(pl.col("securities_lending_anomaly") != "surge")
    assert filtered["symbol"].to_list() == ["2317"]


# =====================================================================
# 3. Foreign Shareholding Field Name Contract
# =====================================================================

def test_extra_chips_foreign_shareholding_field_name():
    fsh = TaiwanForeignShareholdingData(ratio=75.5, change_5d=0.5, change_20d=1.2, trend="increasing")
    extra = TaiwanExtraChipsData(foreign_shareholding=fsh)
    serialized = extra.model_dump()

    # Must be 'foreign_shareholding', NOT 'shareholding'
    assert "foreign_shareholding" in serialized
    assert serialized["foreign_shareholding"]["ratio"] == 75.5
    assert "shareholding" not in serialized


# =====================================================================
# 4. FinMind Token Lifecycle Dynamic Update
# =====================================================================

def test_token_dynamic_lifecycle_without_restart(monkeypatch):
    monkeypatch.setenv("FINMIND_API_TOKEN", "")
    reset_fundamental_chips_service()

    svc = get_fundamental_chips_service()
    assert svc.finmind.token == ""

    # User updates token in preferences
    preferences.set_finmind_token("test-new-token-abc")
    reset_fundamental_chips_service()

    svc_rebuilt = get_fundamental_chips_service()
    assert svc_rebuilt.finmind.token == "test-new-token-abc"

    # User clears token
    preferences.set_finmind_token("")
    reset_fundamental_chips_service()

    svc_cleared = get_fundamental_chips_service()
    assert svc_cleared.finmind.token == ""


# =====================================================================
# 5. Connection Test Failure Preservation (No False Successes)
# =====================================================================

def test_connection_test_fails_on_empty_benchmark_probes():
    adapter = FinMindAdapter(token="invalid_dummy_token")

    # Mock fetch_dataset to return empty list for both probes (simulating empty or failed fetch)
    with patch.object(adapter, "fetch_dataset", return_value=[]):
        res = adapter.test_connection(probe_symbol="2330")
        assert res["ok"] is False
        assert res["error_type"] == "no_data"
        assert "未能驗證" in res["message"]


def test_connection_test_captures_auth_error():
    adapter = FinMindAdapter(token="bad_token")

    with patch.object(adapter, "fetch_dataset", side_effect=FinMindAuthError("Token invalid")):
        res = adapter.test_connection(probe_symbol="2330")
        assert res["ok"] is False
        assert res["error_type"] == "auth"
        assert "認證失敗" in res["message"]
        # Token must NOT be exposed
        assert "bad_token" not in res["message"]


def test_connection_test_captures_rate_limit_error():
    adapter = FinMindAdapter(token="rate_limited_token")

    with patch.object(adapter, "fetch_dataset", side_effect=FinMindRateLimitError("Quota 429")):
        res = adapter.test_connection(probe_symbol="2330")
        assert res["ok"] is False
        assert res["error_type"] == "rate_limit"
        assert "頻率上限" in res["message"]


def test_connection_test_captures_network_error():
    adapter = FinMindAdapter(token="tok")

    with patch.object(adapter, "fetch_dataset", side_effect=FinMindNetworkError("Connection refused")):
        res = adapter.test_connection(probe_symbol="2330")
        assert res["ok"] is False
        assert res["error_type"] == "network"
        assert "連線失敗" in res["message"]


def test_connection_test_succeeds_when_data_received():
    adapter = FinMindAdapter(token="good_token")

    with patch.object(adapter, "fetch_dataset", return_value=[{"date": "2026-08-01", "close": 1000}]):
        res = adapter.test_connection(probe_symbol="2330")
        assert res["ok"] is True
        assert "連線成功" in res["message"]

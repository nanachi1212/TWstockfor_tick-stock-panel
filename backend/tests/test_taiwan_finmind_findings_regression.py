"""Regression tests for PR #21 Codex review findings.

Finding coverage:
  P1 #4110472706  Phase-6G PIT: revenue/statements unavailable for historical as_of
  P1 #4110472712  Foreign shareholding field name contract
  P1 #4110472715  Lending anomaly enum alignment to 'surge'
  P2 #4110472716  Dynamic token lifecycle update without restart
  P2 #4110472717  Connection test failure preservation
"""
from datetime import date
from unittest.mock import patch

import polars as pl

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
    resolve_as_of_date,
)
from app.taiwan.providers.finmind_provider import (
    FinMindAdapter,
    FinMindAuthError,
    FinMindNetworkError,
    FinMindRateLimitError,
)

# =====================================================================
# 1. Phase-6G PIT: Revenue / Statements fail-closed for historical as_of
# =====================================================================

def test_resolve_as_of_date():
    assert resolve_as_of_date(None) is None
    assert resolve_as_of_date("") is None
    assert resolve_as_of_date(date(2026, 8, 5)) == date(2026, 8, 5)
    assert resolve_as_of_date("2026-08-05") == date(2026, 8, 5)
    assert resolve_as_of_date("2026-08-05T12:00:00") == date(2026, 8, 5)


def test_current_revenue_is_available():
    """as_of=None (current/latest analysis): all rows returned unchanged."""
    rows = [
        {"revenue_year": 2026, "revenue_month": 6, "revenue": 100, "date": "2026-06-01"},
        {"revenue_year": 2026, "revenue_month": 7, "revenue": 120, "date": "2026-07-01"},
        {"revenue_year": 2026, "revenue_month": 8, "revenue": 150, "date": "2026-08-01"},
    ]
    result = filter_month_revenue_as_of(rows, None)
    assert len(result) == 3
    assert result is rows  # same object, no copy


def test_historical_revenue_without_verified_availability_is_unavailable():
    """as_of set to any historical date: returns [] (fail-closed, phase-6g).

    TaiwanStockMonthRevenue has no verified record-level publication
    timestamp, so no row can be proven available at any historical instant.
    """
    rows = [
        {"revenue_year": 2026, "revenue_month": 5, "revenue": 100, "date": "2026-05-01"},
        {"revenue_year": 2026, "revenue_month": 6, "revenue": 110, "date": "2026-06-01"},
    ]
    # Any historical date -> fail closed
    assert filter_month_revenue_as_of(rows, date(2026, 8, 5)) == []
    assert filter_month_revenue_as_of(rows, date(2026, 6, 11)) == []
    assert filter_month_revenue_as_of(rows, date(2020, 1, 1)) == []
    assert filter_month_revenue_as_of(rows, "2026-08-05") == []


def test_current_financial_statement_is_available():
    """as_of=None (current/latest analysis): all rows returned unchanged."""
    rows = [
        {"date": "2026-03-31", "type": "EPS", "value": 3.0},
        {"date": "2026-06-30", "type": "EPS", "value": 3.5},
    ]
    result = filter_financial_statements_as_of(rows, None)
    assert len(result) == 2
    assert result is rows


def test_historical_financial_statement_without_verified_availability_is_unavailable():
    """as_of set: returns [] (fail-closed, phase-6g).

    TaiwanStockFinancialStatements rows carry no document identity or
    revision ID that would prove availability at any historical instant.
    """
    rows = [
        {"date": "2026-03-31", "type": "EPS", "value": 3.0},
        {"date": "2026-06-30", "type": "EPS", "value": 3.5},
    ]
    assert filter_financial_statements_as_of(rows, date(2026, 7, 1)) == []
    assert filter_financial_statements_as_of(rows, date(2026, 8, 14)) == []
    assert filter_financial_statements_as_of(rows, date(2024, 1, 1)) == []


def test_restated_value_does_not_backfill_history():
    """Current corrected/restated value cannot be injected into historical context.

    If a FinMind row currently shows a restated revenue figure, passing any
    historical as_of date must return [] so the restated value never reaches
    a historical research context.
    """
    rows_with_restatement = [
        # Represents a row whose value may have been revised since original publication
        {"revenue_year": 2025, "revenue_month": 7, "revenue": 999_000, "date": "2025-07-01"},
    ]
    # Any historical date -> fail closed; restated value is blocked
    assert filter_month_revenue_as_of(rows_with_restatement, date(2025, 8, 15)) == []
    assert filter_month_revenue_as_of(rows_with_restatement, date(2025, 9, 1)) == []


def test_shareholding_date_cutoff_works():
    """TaiwanStockShareholding: daily record date <= as_of cutoff (unchanged)."""
    rows = [
        {"date": "2026-08-01", "ForeignInvestmentSharesRatio": 70.0},
        {"date": "2026-08-05", "ForeignInvestmentSharesRatio": 71.0},
        {"date": "2026-08-10", "ForeignInvestmentSharesRatio": 72.0},
    ]
    filtered = filter_daily_records_as_of(rows, date(2026, 8, 5))
    assert len(filtered) == 2
    assert [r["date"] for r in filtered] == ["2026-08-01", "2026-08-05"]

    # Current date: all rows
    assert len(filter_daily_records_as_of(rows, None)) == 3


def test_lending_date_cutoff_works():
    """TaiwanStockSecuritiesLending: daily record date <= as_of cutoff (unchanged)."""
    rows = [
        {"date": "2026-08-01", "volume": 10000, "fee_rate": 1.0},
        {"date": "2026-08-05", "volume": 20000, "fee_rate": 1.5},
        {"date": "2026-08-10", "volume": 50000, "fee_rate": 2.0},
    ]
    filtered = filter_daily_records_as_of(rows, date(2026, 8, 5))
    assert len(filtered) == 2
    assert filtered[-1]["date"] == "2026-08-05"

    # Current date: all rows
    assert len(filter_daily_records_as_of(rows, None)) == 3


def test_fundamental_chips_service_pit_filtering():
    """FundamentalChipsService: historical as_of yields unavailable for revenue;
    current (as_of=None) still works normally.
    """
    cache = FinMindCache()
    svc = TaiwanFundamentalChipsService(cache=cache)

    raw_rev = [
        {"date": "2026-06-01", "revenue_year": 2026, "revenue_month": 6, "revenue": 100},
        {"date": "2026-07-01", "revenue_year": 2026, "revenue_month": 7, "revenue": 120},
        {"date": "2026-08-01", "revenue_year": 2026, "revenue_month": 8, "revenue": 150},
    ]
    cache.set("TaiwanStockMonthRevenue", "2330", raw_rev, status="available")

    # Historical as_of: phase-6g -> fail closed -> unavailable
    rev_hist = svc.get_monthly_revenue("2330", as_of=date(2026, 8, 5))
    assert rev_hist.meta.status == "unavailable"
    assert rev_hist.latest_revenue is None

    # Historical as_of before any records: also unavailable
    rev_early = svc.get_monthly_revenue("2330", as_of=date(2020, 1, 1))
    assert rev_early.meta.status == "unavailable"
    assert rev_early.latest_revenue is None

    # Current-date query (as_of=None): gets latest August data normally
    rev_latest = svc.get_monthly_revenue("2330", as_of=None)
    assert rev_latest.latest_revenue == 150.0


# =====================================================================
# 2. Lending Anomaly Enum ('surge') & Screener Filtering
# =====================================================================

def test_lending_anomaly_standardized_to_surge():
    cache = FinMindCache()
    svc = TaiwanFundamentalChipsService(cache=cache)

    # Baseline 15 days at volume 1000, then 5 days surge to 60000
    rows = []
    for day in range(1, 16):
        rows.append({"date": f"2026-08-{day:02d}", "volume": 1000, "fee_rate": 2.0})
    for day in range(16, 21):
        rows.append({"date": f"2026-08-{day:02d}", "volume": 60000, "fee_rate": 3.0})

    lending_data = svc._process_securities_lending(rows, "2026-08-20", "2026-08-20T14:00:00")
    # Must emit 'surge', NOT 'abnormal_increase'
    assert lending_data.anomaly_status == "surge"

    # Screener filter excludes 'surge'
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

    preferences.set_finmind_token("test-new-token-abc")
    reset_fundamental_chips_service()

    svc_rebuilt = get_fundamental_chips_service()
    assert svc_rebuilt.finmind.token == "test-new-token-abc"

    preferences.set_finmind_token("")
    reset_fundamental_chips_service()

    svc_cleared = get_fundamental_chips_service()
    assert svc_cleared.finmind.token == ""


# =====================================================================
# 5. Connection Test Failure Preservation (No False Successes)
# =====================================================================

def test_connection_test_fails_on_empty_benchmark_probes():
    adapter = FinMindAdapter(token="invalid_dummy_token")

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

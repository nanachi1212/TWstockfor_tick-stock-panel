"""Fixed financial examples for institutional coverage and industry rotation."""
from datetime import date, timedelta
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.industry_intelligence import TaiwanIndustryIntelligenceService
from app.taiwan.institutional_statistics import TaiwanInstitutionalStatisticsService
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.observed_universe import ObservedUniverseStore
from app.taiwan.realtime.calendar import TaiwanTradingCalendar


@pytest.fixture
def research(tmp_path):
    sessions = [date(2026, 6, 1) + timedelta(days=i) for i in range(100)
                if (date(2026, 6, 1) + timedelta(days=i)).weekday() < 5][:61]
    master = MagicMock()
    master.to_dataframe.return_value = pl.DataFrame({
        "symbol": ["2330.TWSE", "8069.TPEX"], "name": ["A", "B"],
        "exchange": ["TWSE", "TPEX"], "industry": ["A", "B"],
        "instrument_type": ["stock", "stock"], "listing_status": ["active", "active"],
    })
    daily = TaiwanDailyStore(tmp_path / "daily")
    flows = TaiwanInstitutionalStore(tmp_path / "institutional")
    rows, prices = [], []
    for idx, day in enumerate(sessions):
        for symbol in ["2330.TWSE", "8069.TPEX"]:
            rows.append({"symbol": symbol, "date": day, "trade_date": day,
                         "foreign_net": 1500, "investment_trust_net": -500, "dealer_net": 0,
                         "status": "official", "source": "TWSE:T86" if symbol.endswith("TWSE") else "TPEx:dailyTrade"})
            prices.append({"symbol": symbol, "date": day, "close": 100.0 + idx if symbol.endswith("TWSE") else 100.0,
                           "open": 100.0, "high": 200.0, "low": 100.0, "volume": 10000.0,
                           "amount": 100.0 if symbol.endswith("TWSE") else 300.0, "quote_ts": None})
    flows.write_batch(pl.DataFrame(rows))
    daily.write_batch(pl.DataFrame(prices))
    calendar = TaiwanTradingCalendar(known_trading_days=set(sessions))
    evidence = ObservedUniverseStore(tmp_path / "census")
    return sessions, master, daily, flows, calendar, evidence


def institutional(research):
    _, master, daily, flows, calendar, evidence = research
    return TaiwanInstitutionalStatisticsService(flows, daily, master, calendar, evidence)


@pytest.mark.parametrize("window", [5, 10, 20, 45, 60])
def test_windows_units_streaks_and_matched_volume(research, window):
    snap = institutional(research).get_snapshot(research[0][-1], window)
    item = snap.securities[0].investors["foreign"]
    assert item.net_shares.value == window * 1500
    assert item.net_lots.value == window * 1.5
    assert item.net_volume_ratio.value == pytest.approx(0.15)
    assert item.buy_streak.value == window
    assert item.sell_streak.value == 0
    assert item.streak_capped
    assert item.net_shares.coverage.coverage_days == window
    assert item.net_shares.status == "available"
    assert snap.aggregates[0].investors["investment_trust"].sell_streak.value == window
    assert snap.aggregates[0].investors["dealer"].buy_streak.value == 0
    assert "amount" not in item.model_dump()


def test_missing_session_does_not_slide_window_or_bridge_streak(research):
    sessions, _, _, flows, _, _ = research
    day = sessions[-3]
    # Override the missing date with unusable records, preserving the partition.
    flows.write_batch(pl.DataFrame({"symbol": ["2330.TWSE", "8069.TPEX"], "date": [day] * 2,
                                    "trade_date": [day] * 2, "status": ["unavailable"] * 2}))
    metric = institutional(research).get_snapshot(sessions[-1]).securities[0].investors["foreign"]
    assert metric.net_shares.value == 6000
    assert metric.net_shares.status == "partial"
    assert metric.net_shares.coverage.coverage_days == 4
    assert metric.buy_streak.value == 2
    assert metric.net_shares.coverage.missing_dates == [str(day)]


def test_exchange_gap_and_nulls_never_zero_or_usable_streak(research):
    sessions, _, _, flows, _, _ = research
    flows.write_batch(pl.DataFrame({"symbol": ["8069.TPEX"], "date": [sessions[-1]],
                                    "trade_date": [sessions[-1]], "status": ["partial"]}))
    snap = institutional(research).get_snapshot(sessions[-1])
    assert snap.securities[0].investors["foreign"].net_shares.status == "partial"
    assert snap.securities[0].investors["foreign"].buy_streak.value is None
    assert snap.aggregates[0].investors["foreign"].net_shares.status == "partial"
    flows.write_batch(pl.DataFrame({"symbol": ["2330.TWSE"], "date": [sessions[-1]],
                                    "trade_date": [sessions[-1]], "status": ["official"], "foreign_net": [None]}))
    assert institutional(research).get_snapshot(sessions[-1]).securities[0].investors["foreign"].buy_streak.value is None


def test_unavailable_and_weekend_holiday_windows(research):
    svc = institutional(research)
    snap = svc.get_snapshot(date(2025, 1, 1))
    assert snap.securities[0].investors["foreign"].net_shares.value is None
    assert snap.securities[0].investors["foreign"].net_shares.status == "unavailable"
    from app.taiwan.research_metrics import research_sessions
    holiday = date(2026, 9, 7)
    svc.calendar.add_holiday(holiday)
    saturday = date(2026, 9, 5)
    dates = research_sessions(date(2026, 9, 8), 3, {saturday}, svc.calendar, svc.evidence)
    assert dates == [date(2026, 9, 4), saturday, date(2026, 9, 8)]


def rotation(research):
    _, master, daily, flows, calendar, evidence = research
    return TaiwanIndustryIntelligenceService(daily_store=daily, inst_store=flows,
        security_master=master, calendar=calendar, evidence_store=evidence)


def test_rotation_rs_equal_weight_shares_and_delta_pp(research):
    sessions = research[0]
    snap = rotation(research).get_rotation(sessions[-1])
    a = snap.industries[0]
    assert a.relative_strength_5d.value == pytest.approx((160 / 155 - 1) / 2, abs=1e-6)
    assert a.relative_strength_20d.value == pytest.approx((160 / 140 - 1) / 2, abs=1e-6)
    assert a.average_turnover_share_20d.value == 0.25
    assert a.turnover_share_delta_pp.value == 0
    assert a.relative_strength_20d.status == "available"
    daily = research[2]
    latest = daily.read_range(None, sessions[-1], sessions[-1]).with_columns(
        pl.when(pl.col("symbol") == "2330.TWSE").then(300.0).otherwise(300.0).alias("amount")
    )
    daily.write_batch(latest)
    a = rotation(research).get_rotation(sessions[-1]).industries[0]
    assert a.turnover_share.value == 0.5
    assert a.average_turnover_share_20d.value == pytest.approx(0.2625)
    assert a.turnover_share_delta_pp.value == pytest.approx(23.75)


def test_rotation_missing_exchange_invalidates_denominator_and_rs(research):
    sessions, _, daily, _, _, _ = research
    latest = daily.read_range(None, sessions[-1], sessions[-1]).with_columns(
        pl.when(pl.col("symbol") == "8069.TPEX").then(None).otherwise(pl.col("amount")).alias("amount"),
        pl.when(pl.col("symbol") == "8069.TPEX").then(None).otherwise(pl.col("close")).alias("close"),
    )
    daily.write_batch(latest)
    a, b = rotation(research).get_rotation(sessions[-1]).industries
    assert a.turnover_share.value is None
    assert a.turnover_share_delta_pp.value is None
    assert a.average_turnover_share_20d.status == "partial"
    assert a.average_turnover_share_20d.coverage.coverage_days == 19
    assert a.relative_strength_20d.status == "partial"
    assert b.relative_strength_20d.value is None


def test_rotation_gap_and_future_rows(research):
    sessions = research[0]
    earlier = rotation(research).get_rotation(sessions[-6])
    assert earlier.date == str(sessions[-6])
    assert earlier.industries[0].relative_strength_5d.value == pytest.approx((155 / 150 - 1) / 2, abs=1e-6)


def test_api_contract_validation_and_unavailable(research, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import market_research
    monkeypatch.setattr(market_research, "TaiwanInstitutionalStatisticsService", lambda: institutional(research))
    monkeypatch.setattr(market_research, "TaiwanIndustryIntelligenceService", lambda: rotation(research))
    app = FastAPI()
    app.include_router(market_research.router, prefix="/api/taiwan")
    client = TestClient(app)
    for window in (5, 10, 20, 45, 60):
        response = client.get(f"/api/taiwan/institutional-statistics?date={research[0][-1]}&window={window}")
        assert response.status_code == 200
        assert response.json()["securities"][0]["investors"]["foreign"]["net_lots"]["value"] == window * 1.5
    assert client.get("/api/taiwan/institutional-statistics?window=7").status_code == 422
    assert client.get("/api/taiwan/industry-rotation?date=bad").status_code == 422
    response = client.get("/api/taiwan/industry-rotation?date=2025-01-01")
    assert response.status_code == 200
    assert response.json()["industries"][0]["relative_strength_20d"]["status"] == "unavailable"
    monkeypatch.setattr(market_research, "TaiwanIndustryIntelligenceService", lambda: (_ for _ in ()).throw(RuntimeError("read failed")))
    assert TestClient(app, raise_server_exceptions=False).get("/api/taiwan/industry-rotation").status_code == 500


@pytest.mark.parametrize("investor,direction", [("foreign", "buy"), ("investment_trust", "sell"), ("dealer", "buy")])
def test_screener_streak_filters_and_missing_data(research, investor, direction):
    from app.taiwan.screener import TaiwanScreenerRequest, TaiwanScreenerService
    sessions, master, daily, flows, calendar, evidence = research
    svc = TaiwanScreenerService(security_master=master, daily_store=daily,
        institutional_store=flows, calendar=calendar, census_store=evidence)
    req = TaiwanScreenerRequest(streak_investor=investor, streak_direction=direction, streak_min_days=5)
    frame = pl.DataFrame({"symbol": ["2330.TWSE", "8069.TPEX"], "date": [sessions[-1]] * 2})
    joined = svc._join_institutional_streak(frame, req)
    assert svc._apply_filters(joined, req).height == (0 if investor == "dealer" else 2)
    flows.write_batch(pl.DataFrame({"symbol": ["8069.TPEX"], "date": [sessions[-1]], "trade_date": [sessions[-1]], "status": ["partial"]}))
    assert svc._apply_filters(svc._join_institutional_streak(frame, req), req).is_empty()

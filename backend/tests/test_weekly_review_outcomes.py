"""Weekly forward windows on small local official-price Parquets."""
import json
from dataclasses import replace
from datetime import date, datetime, timedelta

import polars as pl
import pytest

from app.taiwan.corporate_actions import (
    CorporateActionEvent,
    CorporateActionStore,
    event_market_open,
)
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.observed_universe import TWSE_SOURCE, ObservedUniverseStore
from app.taiwan.providers.corporate_actions import SOURCE_URLS
from app.taiwan.realtime.calendar import TAIPEI_TZ, TaiwanTradingCalendar
from app.taiwan.regulatory_history import RegulatoryHistoryStore
from app.taiwan.weekly_review_outcomes import BENCHMARK_SOURCES, WeeklyOutcomeEvaluator

START = date(2025, 1, 3)  # Friday
TW = "2330.TWSE"
OTC = "6488.TPEX"
DAY = timedelta(days=1)


def prices(day, symbol=TW, close=100.0, high=101.0, low=99.0, source="twse_tpex_official_snapshot"):
    return {"date": day, "symbol": symbol, "close": close, "high": high, "low": low,
            "open": close, "volume": 1000.0, "amount": 100000.0, "quote_ts": 1,
            "source": source, "status": "official"}


def write_bars(root, rows):
    TaiwanDailyStore(root / "daily").write_batch(pl.DataFrame(rows))


def index(root, values):
    rows = [{"date": day, "symbol": symbol, "close": close, "source": BENCHMARK_SOURCES[symbol]}
            for symbol, day, close in values]
    pl.DataFrame(rows).write_parquet(root / "benchmark_index.parquet")


def covered(root, end, events=(), *, start=START):
    store = CorporateActionStore(root / "adj_factor")
    store.save(events)
    store.path.with_name("coverage.json").write_text(json.dumps({
        "start": start.isoformat(), "end": end.isoformat(),
        "sources": sorted(SOURCE_URLS), "events_sha256": store.snapshot_digest(),
    }))


def known_closures(trades, end):
    days = [START + i * DAY for i in range((end - START).days + 1)]
    return TaiwanTradingCalendar(known_holidays={d for d in days if d.weekday() < 5 and d not in trades})


def test_short_week_whole_closed_week_and_all_horizons(tmp_path):
    # First subsequent ISO week is fully closed. The next week closes Wednesday.
    mid = START + 10 * DAY
    short = START + 12 * DAY
    dates = [short, *(START + (7 * n) * DAY for n in range(3, 15))]
    end = dates[-1]
    rows = [prices(START, high=999.0, low=1.0), prices(mid, high=140.0, low=90.0)]
    for i, day in enumerate(dates, 1):
        rows.append(prices(day, close=100.0 + i, high=102.0 + i, low=98.0))
    # Persist an even newer bar and an index point. Neither may enter earlier outcomes.
    rows.append(prices(end + 3 * DAY, high=9999.0, low=0.1))
    write_bars(tmp_path, rows)
    index(tmp_path, [("TAIEX", START, 1000.0), *(("TAIEX", d, 1010.0) for d in dates)])
    covered(tmp_path, end)
    cal = known_closures({mid, short, *dates, START}, end)
    engine = WeeklyOutcomeEvaluator(tmp_path, START, end, calendar=cal)
    assert engine.week_close(START + 7 * DAY, "TWSE") is None
    assert engine.week_close(mid, "TWSE") == short
    outcomes = engine.evaluate(TW, START, 100)
    for horizon in (1, 4, 8, 13):
        row = outcomes[horizon]
        assert row["status"] == "completed" and row["end_date"] == dates[horizon - 1].isoformat()
        assert row["return_pct"] == pytest.approx(horizon)
        assert row["benchmark_return_pct"] == pytest.approx(1)
        assert row["excess_return_pct"] == pytest.approx(horizon - 1)
        assert row["mfe_pct"] == pytest.approx(40) and row["mae_pct"] == pytest.approx(-10)
        assert row["flags"] == []


def test_pending_midweek_vs_missing_session_history(tmp_path):
    mid, friday = START + 3 * DAY, START + 7 * DAY
    write_bars(tmp_path, [prices(START), prices(mid), prices(friday), prices(START + 14 * DAY)])
    covered(tmp_path, START + 14 * DAY)
    early = WeeklyOutcomeEvaluator(tmp_path, START, mid)
    assert early.week_close(mid, "TWSE") is None
    for row in early.evaluate(TW, START, 100).values():
        assert row["status"] == "pending"
        assert all(row[k] is None for k in ("return_pct", "mfe_pct", "mae_pct", "excess_return_pct"))
    path = WeeklyOutcomeEvaluator(tmp_path, START, START + 14 * DAY).evaluate(TW, START, 100)
    assert path[1]["status"] == "unavailable" and "session_calendar_incomplete" in path[1]["flags"]
    assert path[4]["status"] == "pending"


def test_missing_week_is_not_confirmed_holiday(tmp_path):
    friday = START + 14 * DAY
    write_bars(tmp_path, [prices(START), prices(friday)])
    covered(tmp_path, friday)
    # The older empty week has no partition or calendar evidence.
    cal = TaiwanTradingCalendar(known_holidays={friday - i * DAY for i in range(1, 5)})
    result = WeeklyOutcomeEvaluator(tmp_path, START, friday, calendar=cal).evaluate(TW, START, 100)[1]
    assert result["status"] == "unavailable" and result["return_pct"] is None
    assert result["end_date"] is None and "session_calendar_incomplete" in result["flags"]


def test_exchange_benchmark_missing_start_and_frozen_close_mismatch(tmp_path):
    end = START + 7 * DAY
    write_bars(tmp_path, [prices(d, s) for d in (START, end) for s in (TW, OTC)])
    index(tmp_path, [("TPEX_INDEX", START, 100.0), ("TPEX_INDEX", end, 125.0),
                     ("TAIEX", START - DAY, 80.0), ("TAIEX", end, 200.0)])
    covered(tmp_path, end)
    engine = WeeklyOutcomeEvaluator(tmp_path, START, end, calendar=known_closures({START, end}, end))
    otc = engine.evaluate(OTC, START, 100)[1]
    assert otc["benchmark_symbol"] == "TPEX_INDEX" and otc["benchmark_return_pct"] == 25
    assert otc["excess_return_pct"] == -25 and otc["status"] == "completed"
    tw = engine.evaluate(TW, START, 100)[1]
    assert tw["return_pct"] == 0 and tw["benchmark_return_pct"] is None and tw["excess_return_pct"] is None
    assert tw["flags"] == ["benchmark_unavailable"]
    mismatch = engine.evaluate(TW, START, 101)[1]
    assert mismatch["status"] == "unavailable" and "reference_close_mismatch" in mismatch["flags"]


def test_confirmed_suspension_and_unexplained_missing_quote(tmp_path):
    end = START + 7 * DAY
    write_bars(tmp_path, [prices(START, OTC), prices(START), prices(end, "2331.TWSE")])
    index(tmp_path, [("TPEX_INDEX", START, 80.0), ("TPEX_INDEX", end, 100.0)])
    store = RegulatoryHistoryStore(tmp_path / "regulatory_history")
    store.write_cmode(end, pl.DataFrame({"code": ["6488"], "suspended": [True], "content_hash": ["abc"]}),
                      {"retrieved_at": f"{end + DAY}T09:00:00+08:00"})
    covered(tmp_path, end)
    engine = WeeklyOutcomeEvaluator(tmp_path, START, end, calendar=known_closures({START, end}, end))
    otc = engine.evaluate(OTC, START, 100)[1]
    assert otc["status"] == "unavailable" and "suspended" in otc["flags"] and otc["return_pct"] is None
    assert otc["benchmark_return_pct"] == 25 and otc["excess_return_pct"] is None
    tw = engine.evaluate(TW, START, 100)[1]
    assert tw["status"] == "unavailable" and "missing_official_ohlcv" in tw["flags"]
    assert "suspended" not in tw["flags"]


def cash_dividend(day):
    return CorporateActionEvent(
        symbol=TW, exchange="TWSE", effective_date=day, effective_at=event_market_open(day),
        event_type="cash_dividend", previous_close=100.0, reference_price=90.0, factor=0.9,
        cash_dividend=10.0, free_share_ratio=None, reduction_ratio=None, source="TWT49U",
        source_url="https://www.twse.com.tw/", retrieved_at=datetime(2025, 2, 1, tzinfo=TAIPEI_TZ),
        status="verified", precision_method="official_reference_ratio")


@pytest.mark.parametrize("missing_first_session", [False, True])
def test_action_coverage_starts_on_first_held_session(tmp_path, missing_first_session):
    monday = START + 3 * DAY
    fridays = [START + 7 * n * DAY for n in range(1, 14)]
    end = fridays[-1]
    rows = [prices(START), prices(monday)]
    for n, day in enumerate(fridays, 1):
        rows.append(prices(day, close=100. + n, high=101. + n))
    write_bars(tmp_path, rows)
    covered(tmp_path, end, start=monday + DAY if missing_first_session else monday)
    engine = WeeklyOutcomeEvaluator(tmp_path, START, end, calendar=known_closures({START, monday, *fridays}, end))
    for horizon, outcome in engine.evaluate(TW, START, 100.).items():
        if missing_first_session:
            assert outcome["status"] == "unavailable" and outcome["return_pct"] is None
            assert "corporate_action_coverage_unverified" in outcome["flags"]
        else:
            assert outcome["status"] == "completed" and outcome["return_pct"] == pytest.approx(horizon)


@pytest.mark.parametrize("reason,other_status", [("detail_provider_error", "completed"),
                                                ("unknown_provider_error", "unavailable")])
def test_scoped_ex_dividend_detail_error_and_unscoped_error(tmp_path, reason, other_status):
    end = START + 7 * DAY
    write_bars(tmp_path, [prices(d, symbol=s) for d in (START, end) for s in (TW, OTC)])
    covered(tmp_path, end, [replace(cash_dividend(end), status="provider_error", reason=reason, factor=None)])
    engine = WeeklyOutcomeEvaluator(tmp_path, START, end, calendar=known_closures({START, end}, end))
    affected = engine.evaluate(TW, START, 100.)[1]
    assert affected["status"] == "unavailable" and "corporate_action_unadjusted" in affected["flags"]
    other = engine.evaluate(OTC, START, 100.)[1]
    assert other["status"] == other_status
    if other_status == "completed":
        assert other["return_pct"] == 0 and other["price_basis"] == "verified_raw_no_action"
    else:
        assert other["return_pct"] is None and "corporate_action_coverage_unverified" in other["flags"]


@pytest.mark.parametrize("mode", ["verified", "unadjusted", "uncovered"])
def test_consistent_ex_event_basis_and_unadjusted_flags(tmp_path, mode):
    mid, end = START + 3 * DAY, START + 7 * DAY
    write_bars(tmp_path, [prices(START), prices(mid, close=100.0, high=110.0, low=95.0),
                          prices(end, close=99.0, high=108.0, low=90.0)])
    events = [cash_dividend(end), cash_dividend(end + 3 * DAY)]
    if mode == "unadjusted":
        events[0] = replace(events[0], status="data_insufficient", factor=None, reason="missing_reference")
    covered(tmp_path, mid if mode == "uncovered" else end, events)
    engine = WeeklyOutcomeEvaluator(tmp_path, START, end, calendar=known_closures({START, mid, end}, end))
    row = engine.evaluate(TW, START, 100)[1]
    if mode == "verified":
        assert row["status"] == "completed" and row["price_basis"] == "pit_adjusted"
        assert row["return_pct"] == pytest.approx(10)
        assert row["mfe_pct"] == pytest.approx(20) and row["mae_pct"] == pytest.approx(-5)
    else:
        assert row["status"] == "unavailable" and "corporate_action_unadjusted" in row["flags"]
        assert row["return_pct"] is None and row["mfe_pct"] is None


def test_pre_entry_intraday_range_is_not_required_to_adjust_the_frozen_close(tmp_path):
    end = START + 7 * DAY
    write_bars(tmp_path, [prices(START, high=None, low=None),
                         prices(end, close=99.0, high=108.0, low=95.0)])
    covered(tmp_path, end, [cash_dividend(end)])
    engine = WeeklyOutcomeEvaluator(tmp_path, START, end, calendar=known_closures({START, end}, end))
    row = engine.evaluate(TW, START, 100.0)[1]
    assert row["status"] == "completed" and row["price_basis"] == "pit_adjusted"
    assert row["return_pct"] == pytest.approx(10) and row["mfe_pct"] == pytest.approx(20)
    assert row["mae_pct"] == pytest.approx(100 * (95 / 90 - 1))


def test_missing_quotes_do_not_hide_an_unadjusted_corporate_action(tmp_path):
    end = START + 7 * DAY
    write_bars(tmp_path, [prices(START), prices(end, high=None)])
    incomplete = replace(cash_dividend(end), status="data_insufficient", factor=None, reason="missing_reference")
    covered(tmp_path, end, [incomplete])
    engine = WeeklyOutcomeEvaluator(tmp_path, START, end, calendar=known_closures({START, end}, end))
    row = engine.evaluate(TW, START, 100.0)[1]
    assert row["status"] == "unavailable" and row["return_pct"] is None
    assert {"missing_official_ohlcv", "corporate_action_unadjusted"} <= set(row["flags"])


@pytest.mark.parametrize("trust", ["untrusted", "partial_official"])
def test_official_census_quotes_for_legacy_and_untrusted_daily(tmp_path, trust):
    end = START + 7 * DAY
    store = ObservedUniverseStore(tmp_path / "observed_universe")
    for d in (START, end):
        row = prices(d)
        store.write("TWSE", d, [{**row, "raw_code": "2330", "exchange": "TWSE",
                                 "observed": True, "source": TWSE_SOURCE}])
    for n in range(3, 7):
        store.write("TWSE", START + n * DAY, [], confirmed_non_trading_source="fixture:closure")
    latest = (prices(end, close=999.0, source="third_party") if trust == "untrusted"
              else prices(end, high=None))
    write_bars(tmp_path, [latest, prices(START, source=None)])
    covered(tmp_path, end)
    engine = WeeklyOutcomeEvaluator(tmp_path, START, end)
    assert engine.official_close(TW, end) == 100
    row = engine.evaluate(TW, START, 100)[1]
    assert row["status"] == "completed" and row["return_pct"] == 0

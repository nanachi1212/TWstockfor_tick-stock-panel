"""Historical PIT replay of the frozen trend_liquidity_v1 selector."""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import polars as pl
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.taiwan.corporate_actions import (
    CorporateActionEvent,
    CorporateActionStore,
    event_market_open,
)
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.events_service import MarketEvent, TaiwanEventService
from app.taiwan.historical_classification import HistoricalClassificationStore
from app.taiwan.observed_universe import ObservedUniverseStore
from app.taiwan.providers.corporate_actions import SOURCE_URLS
from app.taiwan.quant.primary_oos_runner import PrimaryOosNotReadyError, _primary_universe
from app.taiwan.quant.selection_pit import (
    NO_REGULATORY_HISTORY,
    TREND_LIQUIDITY_V1_PIT_SPEC,
    HistoricalPitInputs,
    HistoricalPitRunStore,
    RegulatoryEvidence,
    TrendLiquidityV1PitInputError,
    build_artifact,
    evaluate_trend_liquidity_v1_history,
    horizon_metrics,
    load_trend_liquidity_v1_pit_inputs,
    load_verified_actions,
    run_trend_liquidity_v1_pit_evaluation,
)
from app.taiwan.realtime.calendar import TaiwanTradingCalendar
from app.taiwan.screener import TaiwanScreenerRequest, TaiwanScreenerService

TAIPEI = ZoneInfo("Asia/Taipei")
STOCKS = ("2330.TWSE", "2454.TWSE", "2317.TWSE", "2881.TWSE", "2603.TWSE", "2412.TWSE", "8069.TPEX")


def _weekdays(start: date, count: int, *, skip: frozenset[date] = frozenset(),
              extra: frozenset[date] = frozenset()) -> list[date]:
    days, cursor = [], start
    while len(days) < count:
        if (cursor.weekday() < 5 and cursor not in skip) or cursor in extra:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _event(symbol: str, day: date, kind: str, previous: float, reference: float,
           status: str = "verified") -> CorporateActionEvent:
    exchange = symbol.rsplit(".", 1)[1]
    verified = status == "verified"
    return CorporateActionEvent(
        symbol=symbol, exchange=exchange, effective_date=day, effective_at=event_market_open(day),
        event_type=kind, previous_close=previous if verified else None,
        reference_price=reference if verified else None,
        factor=reference / previous if verified else None,
        cash_dividend=previous - reference if kind == "cash_dividend" else None,
        free_share_ratio=None, reduction_ratio=None, source="TWT49U",
        source_url="https://www.twse.com.tw/", retrieved_at=datetime.combine(day, time(15), TAIPEI),
        status=status, precision_method="official_reference_ratio" if verified else "unresolved",
        reason=None if verified else "fixture_unverified",
    )


def _inputs(sessions, bars, *, types=None, events=(), coverage="full", regulatory=None,
            blocked=None, unresolved=frozenset()) -> HistoricalPitInputs:
    """``bars(symbol, index, day)`` returns (open, close, amount) or None for no row."""
    symbols = sorted({*STOCKS, "0050.TWSE"})
    types = types or {}
    rows, universe = [], []
    for index, day in enumerate(sessions):
        for symbol in symbols:
            bar = bars(symbol, index, day)
            if bar is None:
                continue
            opening, closing, amount = bar
            rows.append({"symbol": symbol, "date": day, "open": opening, "close": closing,
                         "amount": amount})
            kind, status = types.get(symbol, ("etf" if symbol == "0050.TWSE" else "stock", "verified"))
            universe.append({"date": day, "market_symbol": symbol,
                             "exchange": symbol.rsplit(".", 1)[1], "instrument_type": kind,
                             "instrument_type_status": status,
                             "price_bar_available": closing is not None})
    return HistoricalPitInputs(
        sessions=tuple(sessions),
        prices=pl.DataFrame(rows, schema={"symbol": pl.String, "date": pl.Date, "open": pl.Float64,
                                          "close": pl.Float64, "amount": pl.Float64}),
        universe=pl.DataFrame(universe),
        events=tuple(events),
        action_coverage=(sessions[0], sessions[-1]) if coverage == "full" else coverage,
        regulatory=regulatory or RegulatoryEvidence("fixture", frozenset(sessions)),
        blocked_exchanges=frozenset(blocked or ()),
        unresolved_days=unresolved,
    )


def _quiet(symbol, index, day):
    """Every symbol present, none liquid enough except when overridden."""
    return 50.0, 50.0 - 0.1 * index, 1_000_000.0


def _picks(result, source: date):
    return [p for p in result["strict_picks"] if p["source_session"] == source.isoformat()]


# ── live selector parity ────────────────────────────────────────


def test_historical_replay_matches_live_v1_screener(tmp_path, monkeypatch):
    holiday = date(2026, 8, 7)
    sessions = _weekdays(date(2026, 8, 3), 26, skip=frozenset({holiday}))
    source, target = sessions[24], sessions[25]

    def bars(symbol, index, day):
        base = {"2330.TWSE": (100.0, 1.0, 60e6), "2412.TWSE": (100.0, 1.0, 90e6),
                "2317.TWSE": (50.0, 1.0, 60e6), "2454.TWSE": (100.0, 3.0, 49_999_999.0),
                "2881.TWSE": (80.0, -0.5, 70e6), "2603.TWSE": (100.0, 0.2, 80e6),
                "8069.TPEX": (40.0, 2.0, 70e6), "0050.TWSE": (150.0, 2.0, 90e6)}[symbol]
        close = base[0] + base[1] * index
        if symbol == "2603.TWSE" and index >= 20:
            close -= 10.0  # cash-dividend ex-date; raw trend breaks, PIT trend does not
        return close, close, base[2]

    dividend = _event("2603.TWSE", sessions[20], "cash_dividend", 100.0 + 0.2 * 19, 90.0 + 0.2 * 19)
    store = TaiwanDailyStore(tmp_path / "daily")
    store.write_batch(pl.DataFrame([
        {"symbol": s, "date": d, "open": o, "high": c, "low": o, "close": c, "volume": 1e6,
         "amount": a, "quote_ts": 0}
        for i, d in enumerate(sessions[:25]) for s in (*STOCKS, "0050.TWSE")
        for o, c, a in [bars(s, i, d)]]))
    actions = CorporateActionStore(tmp_path / "adj_factor")
    actions.save([dividend])
    actions.path.with_name("coverage.json").write_text(json.dumps({
        "start": sessions[0].isoformat(), "end": sessions[-1].isoformat(),
        "sources": sorted(SOURCE_URLS), "events_sha256": actions.snapshot_digest(),
    }), encoding="utf-8")
    disposition = MarketEvent(
        id="d-8069", symbol="8069.TPEX", code="8069", name="元太", exchange="TPEX",
        event_date=source.isoformat(), event_type="disposition", event_type_label="處置證券",
        title="處置", summary="處置", source="TPEX", retrieved_at=f"{source}T15:00:00+08:00")

    class Risk:
        def get_cached_regulatory_snapshot(self):
            return [disposition], "available", f"{source}T15:00:00+08:00"

        def check_symbol_risk_status(self, symbol, target_date=None, events=None):
            return TaiwanEventService.check_symbol_risk_status(
                self, symbol, target_date=target_date, events=events)

    monkeypatch.setattr("app.taiwan.events_service.get_event_service", lambda: Risk())
    calendar = TaiwanTradingCalendar(known_holidays={holiday}, known_trading_days=set(sessions))
    live = TaiwanScreenerService(
        daily_store=store, action_store=actions, calendar=calendar,
        census_store=ObservedUniverseStore(tmp_path / "census"),
    ).run(TaiwanScreenerRequest(preset="trend_liquidity_v1"))
    assert live.trend_adjustment_status == "verified"
    assert live.risk_target_date == target.isoformat()

    result = evaluate_trend_liquidity_v1_history(_inputs(
        sessions, bars, events=[dividend],
        regulatory=RegulatoryEvidence("fixture", frozenset({target}),
                                      {target: frozenset({"8069.TPEX"})})))
    picks = _picks(result, source)
    assert [p["symbol"] for p in picks] == [item.symbol for item in live.items]
    # 2317 has the strongest momentum; 2412 ties 2330 and wins on turnover; 2603 only
    # qualifies on PIT prices; 8069 is under disposition, 2454 illiquid, 0050 an ETF.
    assert [p["symbol"] for p in picks] == ["2317.TWSE", "2412.TWSE", "2330.TWSE", "2603.TWSE"]
    for pick, item in zip(picks, live.items, strict=True):
        assert pick["momentum_5d"] == pytest.approx(item.momentum_5d, rel=1e-12)
        assert pick["ma20"] == pytest.approx(item.ma20, rel=1e-12)
        assert pick["adjusted_close"] == pytest.approx(item.trend_adjusted_close, rel=1e-12)
        assert pick["amount"] == item.amount
    assert "2603.TWSE" in {p["symbol"] for p in picks}  # PIT-adjusted, not raw, trend
    assert result["reproducibility"]["strict_fully_reproducible_sessions"] == 1


# ── forward definition ──────────────────────────────────────────


def _forward_fixture(skip: frozenset[date] = frozenset(), **overrides):
    sessions = _weekdays(date(2025, 1, 6), 45, skip=skip)
    source_index = 24
    special = {
        ("2330.TWSE", 25): (130.0, 131.3, 60e6),   # entry open 130, 1D close +1%
        ("2330.TWSE", 29): (140.0, 143.0, 60e6),   # 5D close +10%
        ("2330.TWSE", 44): (118.0, 117.0, 60e6),   # 20D close -10%
        ("0050.TWSE", 25): (50.0, 50.5, 1e6),
        ("0050.TWSE", 44): (54.0, 55.0, 1e6),
    }
    special.update(overrides.pop("special", {}))
    missing = overrides.pop("missing", set())

    def bars(symbol, index, day):
        if (symbol, index) in missing:
            return None
        if (symbol, index) in special:
            return special[(symbol, index)]
        if symbol == "2330.TWSE":
            return 100.0 + index, 100.0 + index, 60e6
        if symbol == "0050.TWSE":
            return 50.0, 50.0, 1e6
        return _quiet(symbol, index, day)

    overrides.setdefault(
        "regulatory", RegulatoryEvidence("fixture", frozenset({sessions[source_index + 1]})))
    return sessions, source_index, _inputs(sessions, bars, **overrides)


def test_entry_is_next_open_and_horizons_use_exact_sessions():
    sessions, index, inputs = _forward_fixture(missing={("0050.TWSE", 29)})
    result = evaluate_trend_liquidity_v1_history(inputs)
    [pick] = _picks(result, sessions[index])
    assert pick["entry_session"] == sessions[index + 1].isoformat()
    assert pick["h1d"]["return_pct"] == pytest.approx(1.0)
    assert pick["h1d"]["excess_return_pct"] == pytest.approx(0.0)
    assert pick["h5d"]["return_pct"] == pytest.approx(10.0)
    assert pick["h5d"]["benchmark_status"] == "unavailable"
    assert pick["h5d"]["benchmark_reason"] == "missing_horizon_close"
    assert pick["h5d"]["excess_return_pct"] is None
    assert pick["h20d"]["return_pct"] == pytest.approx(-10.0)
    assert pick["h20d"]["benchmark_return_pct"] == pytest.approx(10.0)
    assert pick["h20d"]["excess_return_pct"] == pytest.approx(-20.0)
    strict = result["strict_result"]
    assert strict["status"] == "available" and strict["strict_sessions"] == 1
    assert strict["top10"]["5"]["n"] == 1
    assert strict["top10"]["5"]["excess_n"] == 0
    assert strict["top10"]["5"]["avg_excess_return_pct"] is None
    assert strict["top10"]["20"]["beat_benchmark_rate_pct"] == 0.0
    assert strict["rank_groups"]["11-20"]["1"]["n"] == 0
    assert strict["rank_groups"]["11-20"]["1"]["hit_rate_pct"] is None
    assert set(strict["by_year"]) == {"2025"}


def test_strict_session_without_candidates_counts_in_its_year():
    sessions = _weekdays(date(2025, 12, 1), 45)
    source = next(i for i, d in enumerate(sessions) if d.year == 2026 and i >= 19)

    def bars(symbol, index, day):
        if symbol == "2330.TWSE" and day.year == 2025:
            return 100.0 + index, 100.0 + index, 60e6
        return _quiet(symbol, index, day)  # falling and illiquid: no candidate

    covered = frozenset({sessions[source - 1], sessions[source + 1]})
    result = evaluate_trend_liquidity_v1_history(_inputs(
        sessions, bars, regulatory=RegulatoryEvidence("fixture", covered)))
    strict = result["strict_result"]
    assert strict["strict_sessions"] == 2
    assert strict["candidate_count_distribution"]["sessions_with_zero"] == 1
    assert {year: value["sessions"] for year, value in strict["by_year"].items()} == {
        "2025": 1, "2026": 1}
    assert strict["by_year"]["2026"]["full_batch"]["1"]["n"] == 0
    assert strict["by_year"]["2026"]["full_batch"]["1"]["hit_rate_pct"] is None


def test_missing_horizon_price_is_unavailable_and_never_slides():
    sessions, index, inputs = _forward_fixture(missing={("2330.TWSE", 29)})
    [pick] = _picks(evaluate_trend_liquidity_v1_history(inputs), sessions[index])
    assert pick["h5d"] == {**pick["h5d"], "status": "unavailable", "return_pct": None,
                           "reason": "missing_horizon_close"}
    assert pick["h20d"]["return_pct"] == pytest.approx(-10.0)
    missing_entry = _forward_fixture(missing={("2330.TWSE", 25)})
    [pick] = _picks(evaluate_trend_liquidity_v1_history(missing_entry[2]), sessions[index])
    assert {pick[f"h{h}d"]["reason"] for h in (1, 5, 20)} == {"missing_entry_open"}


def test_delisted_pick_keeps_completed_horizons_and_marks_later_unavailable():
    delisted = {("2330.TWSE", i) for i in range(28, 45)}
    sessions, index, inputs = _forward_fixture(missing=delisted)
    [pick] = _picks(evaluate_trend_liquidity_v1_history(inputs), sessions[index])
    assert pick["h1d"]["status"] == "completed"
    assert pick["h5d"]["reason"] == pick["h20d"]["reason"] == "missing_horizon_close"
    metrics = horizon_metrics([pick], 20)
    assert metrics["n"] == 0 and metrics["unavailable"] == 1 and metrics["avg_return_pct"] is None


def test_dividend_inside_forward_window_is_price_normalized():
    ex_day_index = 27
    sessions = _weekdays(date(2025, 1, 6), 45)
    dividend = _event("2330.TWSE", sessions[ex_day_index], "cash_dividend", 130.0, 117.0)
    special = {("2330.TWSE", i): (117.0, 117.0, 60e6) for i in range(ex_day_index, 45)}
    special[("2330.TWSE", 25)] = (130.0, 130.0, 60e6)
    special[("2330.TWSE", 26)] = (130.0, 130.0, 60e6)
    _, index, inputs = _forward_fixture(special=special, events=[dividend])
    [pick] = _picks(evaluate_trend_liquidity_v1_history(inputs), sessions[index])
    assert pick["h1d"]["return_pct"] == pytest.approx(0.0)
    assert pick["h5d"]["return_pct"] == pytest.approx(0.0)  # not the raw -10%


@pytest.mark.parametrize("kind,previous,reference", [
    ("capital_reduction", 50.0, 100.0),   # raw price doubles; PIT trend is flat
    ("par_change", 400.0, 100.0),         # 1:4 split; raw price quarters
])
def test_share_count_actions_in_trend_window_use_pit_prices(kind, previous, reference):
    sessions = _weekdays(date(2025, 1, 6), 26)
    effective = 22
    ratio = reference / previous

    def bars(symbol, index, day):
        if symbol == "2330.TWSE":
            close = previous * (1 + 0.001 * index)
            if index >= effective:
                close *= ratio
            return close, close, 60e6
        return _quiet(symbol, index, day)

    action = _event("2330.TWSE", sessions[effective], kind, previous * (1 + 0.001 * (effective - 1)),
                    reference * (1 + 0.001 * (effective - 1)))
    target = sessions[25]
    regulatory = RegulatoryEvidence("fixture", frozenset({target}))
    result = evaluate_trend_liquidity_v1_history(_inputs(
        sessions, bars, events=[action], regulatory=regulatory))
    [pick] = _picks(result, sessions[24])
    assert pick["momentum_5d"] == pytest.approx(1.024 / 1.019 - 1, rel=1e-9)
    unverified = _event("2330.TWSE", sessions[effective], kind, previous, reference,
                        status="data_insufficient")
    blocked = evaluate_trend_liquidity_v1_history(_inputs(
        sessions, bars, events=[unverified], regulatory=regulatory))
    assert blocked["reproducibility"]["blocker_session_counts"]["corporate_action_unverified"] >= 1
    assert _picks(blocked, sessions[24]) == []


def test_future_corporate_action_never_changes_historical_features():
    sessions = _weekdays(date(2025, 1, 6), 26)

    def bars(symbol, index, day):
        if symbol == "2330.TWSE":
            return 100.0 + index, 100.0 + index, 60e6
        return _quiet(symbol, index, day)

    regulatory = RegulatoryEvidence("fixture", frozenset({sessions[25]}))
    baseline = evaluate_trend_liquidity_v1_history(_inputs(sessions, bars, regulatory=regulatory))
    later = _event("2330.TWSE", sessions[25], "cash_dividend", 125.0, 100.0)
    with_future = evaluate_trend_liquidity_v1_history(_inputs(
        sessions, bars, events=[later], regulatory=regulatory))
    [a], [b] = _picks(baseline, sessions[24]), _picks(with_future, sessions[24])
    assert (a["momentum_5d"], a["ma20"], a["adjusted_close"]) == (
        b["momentum_5d"], b["ma20"], b["adjusted_close"])


# ── trading-day and coverage evidence ───────────────────────────


def test_observed_saturday_session_counts_in_window_and_horizon():
    saturday = date(2025, 2, 8)
    sessions = _weekdays(date(2025, 1, 6), 45, extra=frozenset({saturday}))
    index = sessions.index(saturday) - 5
    assert sessions[index + 5] == saturday

    def bars(symbol, i, day):
        if symbol == "2330.TWSE":
            return 100.0 + i, 100.0 + i, 60e6
        return _quiet(symbol, i, day)

    regulatory = RegulatoryEvidence("fixture", frozenset({sessions[index + 1]}))
    [pick] = _picks(evaluate_trend_liquidity_v1_history(
        _inputs(sessions, bars, regulatory=regulatory)), sessions[index])
    assert pick["h5d"]["return_pct"] == pytest.approx(((100.0 + index + 5) / (101.0 + index) - 1) * 100)


def test_unresolved_trading_day_blocks_session_and_forward_horizon():
    weekdays = _weekdays(date(2025, 1, 6), 46)
    # in_window: inside the trend window; before_entry: between source and entry.
    in_window, before_entry, in_horizon = weekdays[21], weekdays[25], weekdays[36]
    for gap in (in_window, before_entry, in_horizon):
        sessions, index, inputs = _forward_fixture(skip=frozenset({gap}))
        inputs = HistoricalPitInputs(**{**inputs.__dict__, "unresolved_days": frozenset({gap})})
        result = evaluate_trend_liquidity_v1_history(inputs)
        if gap != in_horizon:
            assert "trading_day_unverified" in result["reproducibility"]["blocker_session_counts"]
            assert _picks(result, sessions[index]) == []
        else:
            [pick] = _picks(result, sessions[index])
            assert pick["h5d"]["status"] == "completed"
            assert pick["h20d"]["reason"] == "trading_day_unverified"


def test_missing_individual_price_in_trend_window_excludes_symbol():
    sessions, index, inputs = _forward_fixture(missing={("2330.TWSE", 17)})
    assert _picks(evaluate_trend_liquidity_v1_history(inputs), sessions[index]) == []


def test_corporate_action_coverage_gaps_fail_closed():
    sessions, index, inputs = _forward_fixture(coverage=None)
    result = evaluate_trend_liquidity_v1_history(inputs)
    assert result["reproducibility"]["strict_fully_reproducible_sessions"] == 0
    assert result["reproducibility"]["blocker_session_counts"][
        "corporate_action_coverage_unavailable"] == result["reproducibility"]["requested_sessions"]
    short = _forward_fixture(coverage=(sessions[0], sessions[index + 10]))[2]
    [pick] = _picks(evaluate_trend_liquidity_v1_history(short), sessions[index])
    assert pick["h5d"]["status"] == "completed"
    assert pick["h20d"]["reason"] == "corporate_action_coverage_unavailable"


# ── universe evidence ───────────────────────────────────────────


def test_classification_is_never_back_applied_before_its_evidence_date(tmp_path):
    census = ObservedUniverseStore(tmp_path / "census")
    classes = HistoricalClassificationStore(tmp_path / "classes")
    first, later = date(2025, 1, 6), date(2025, 1, 7)
    for day in (first, later):
        census.write("TWSE", day, [{"date": day, "raw_code": code, "exchange": "TWSE",
                                    "observed": True, "open": 10.0, "high": 10.0, "low": 10.0,
                                    "close": 10.0, "volume": 1.0, "amount": 1.0}
                                   for code in ("1101", "1102")])
    row = {"exchange": "TWSE", "instrument_type": "stock", "industry": None,
           "industry_status": "data_insufficient", "classification_status": "verified",
           "retrieved_at": "x", "instrument_type_status": "verified",
           "primary_oos_eligible_type": True}
    classes.write(first, [{**row, "code": "1101", "classification_effective_from": first,
                           "classification_source": f"twse:isin_listed@{first}"}])
    classes.write(later, [{**row, "code": "1102", "classification_effective_from": later,
                           "classification_source": f"twse:isin_listed@{later}"}])
    universe, _ = _primary_universe(census, classes)
    status = {(r["date"], r["market_symbol"]): r["instrument_type_status"]
              for r in universe.iter_rows(named=True)}
    assert status[(first, "1101.TWSE")] == "verified"
    # Evidence dated after the first observation never reaches back to it.
    assert status[(first, "1102.TWSE")] == "data_insufficient"
    assert status[(later, "1102.TWSE")] == "data_insufficient"


def test_unresolved_subtype_and_blocked_exchange_exclude_session():
    _, _, inputs = _forward_fixture(types={"2412.TWSE": (None, "data_insufficient")})
    result = evaluate_trend_liquidity_v1_history(inputs)
    assert result["reproducibility"]["strict_fully_reproducible_sessions"] == 0
    assert "twse_instrument_subtype_unresolved" in result["reproducibility"]["blocker_session_counts"]
    tpex = _forward_fixture(blocked={"TPEX"})[2]
    blocked = evaluate_trend_liquidity_v1_history(tpex)
    assert blocked["reproducibility"]["blocker_session_counts"][
        "tpex_instrument_subtype_blocked"] == blocked["reproducibility"]["requested_sessions"]


def test_exchange_evidence_is_required_on_every_session():
    # A blocked exchange blocks sessions its census never observed as well.
    sessions, index, inputs = _forward_fixture(missing={("8069.TPEX", i) for i in range(45)},
                                               blocked={"TPEX"})
    result = evaluate_trend_liquidity_v1_history(inputs)
    assert result["reproducibility"]["blocker_session_counts"][
        "tpex_instrument_subtype_blocked"] == result["reproducibility"]["requested_sessions"]
    # An unblocked exchange with no observed membership that session is not evidence of absence.
    _, _, gap = _forward_fixture(missing={("8069.TPEX", index)})
    result = evaluate_trend_liquidity_v1_history(gap)
    assert result["reproducibility"]["blocker_session_counts"] == {
        "entry_session_not_observed": 1, "regulatory_history_unavailable": 25,
        "tpex_market_session_unobserved": 1}
    assert _picks(result, sessions[index]) == []


def test_specific_regulatory_gaps_replace_the_generic_blocker():
    sessions = _weekdays(date(2025, 1, 6), 45)
    target = sessions[25]
    regulatory = RegulatoryEvidence(
        "fixture", frozenset(), {},
        {target: ("regulatory_tpex_status_unavailable",)})
    _, _, inputs = _forward_fixture(regulatory=regulatory)
    counts = evaluate_trend_liquidity_v1_history(inputs)["reproducibility"]["blocker_session_counts"]
    assert counts["regulatory_tpex_status_unavailable"] == 1
    assert counts["regulatory_history_unavailable"] == 25  # sessions with no record at all


def test_no_regulatory_history_means_no_strict_result_and_no_fake_metrics():
    _, _, inputs = _forward_fixture(regulatory=NO_REGULATORY_HISTORY,
                                    blocked={"TPEX"})
    result = evaluate_trend_liquidity_v1_history(inputs)
    strict = result["strict_result"]
    assert strict["status"] == "no_strict_sample" and strict["claimable"] is False
    assert strict["strict_sessions"] == 0 and result["strict_picks"] == []
    assert all(strict[key] is None for key in ("top10", "full_batch", "rank_groups", "by_year",
                                               "candidate_count_distribution"))
    assert "not a zero return" in strict["message"]
    diagnostics = result["degraded_diagnostics"]
    assert diagnostics["claimable"] is False and diagnostics["diagnostic_only"] is True
    assert diagnostics["candidate_count_distribution"]["max"] >= 1
    assert "return" not in json.dumps(diagnostics["candidate_count_distribution"])


def test_action_snapshot_replaced_while_loading_fails_closed(tmp_path):
    day = date(2025, 1, 6)
    store = CorporateActionStore(tmp_path / "adj_factor")
    store.save([_event("2330.TWSE", day, "cash_dividend", 100.0, 90.0)])
    store.path.with_name("coverage.json").write_text(json.dumps({
        "start": day.isoformat(), "end": day.isoformat(), "sources": sorted(SOURCE_URLS),
        "events_sha256": store.snapshot_digest()}), encoding="utf-8")
    span, record, events = load_verified_actions(store)
    assert span == (day, day) and record["status"] == "verified" and len(events) == 1

    original = CorporateActionStore.read

    def read_then_refresh(self):
        rows = original(self)
        self.save([_event("2317.TWSE", day, "cash_dividend", 50.0, 45.0)])
        return rows

    store.read = read_then_refresh.__get__(store)
    with pytest.raises(TrendLiquidityV1PitInputError):
        load_verified_actions(store)


def test_loader_refuses_to_read_while_backfill_holds_the_lock(tmp_path, monkeypatch):
    from app.taiwan.backfill_worker import TaiwanHistoricalBackfillWorker, WorkerBusyError

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    worker = TaiwanHistoricalBackfillWorker()
    worker.lock.acquire()
    try:
        with pytest.raises(WorkerBusyError):
            load_trend_liquidity_v1_pit_inputs()
    finally:
        worker.lock.release()


# ── ledger, determinism, gate, API ──────────────────────────────


def _preflight(ready: bool = True, census_sessions: int = 2859):
    readiness = SimpleNamespace(status=SimpleNamespace(value="ready" if ready else "blocked"),
                                blocking_reasons=() if ready else ("census incomplete",),
                                describe=lambda: {"status": "ready" if ready else "blocked"})
    health = SimpleNamespace(describe=lambda: {
        "census_by_exchange": {"TWSE": {"observed_trading_sessions": census_sessions}, "TPEX": {}},
        "classification": {}})
    return SimpleNamespace(readiness=readiness, data_health=health)


def test_rerun_is_deterministic_and_recorded_apart_from_forward_batches(tmp_path):
    _, _, inputs = _forward_fixture()
    first = evaluate_trend_liquidity_v1_history(inputs)
    assert evaluate_trend_liquidity_v1_history(inputs) == first
    kwargs = {"spec": TREND_LIQUIDITY_V1_PIT_SPEC, "code_sha": "a" * 40, "code_fp": "b" * 64,
              "data_coverage": {}}
    one = build_artifact(first, inputs, generated_at="2026-09-27T20:00:00+08:00", **kwargs)
    two = build_artifact(first, inputs, generated_at="2026-09-28T20:00:00+08:00", **kwargs)
    assert one["result_fingerprint"] == two["result_fingerprint"]

    store = HistoricalPitRunStore(tmp_path / "quant" / "historical.sqlite3")
    recorded = run_trend_liquidity_v1_pit_evaluation(
        store=store, loader=lambda: inputs, preflight_reader=_preflight, code_sha="c" * 40)
    again = run_trend_liquidity_v1_pit_evaluation(
        store=store, loader=lambda: inputs, preflight_reader=_preflight, code_sha="c" * 40)
    assert recorded["reused"] is False and again["reused"] is True
    assert again["result_fingerprint"] == recorded["result_fingerprint"]
    assert recorded["record_scope"] == "historical_pit"
    assert not (tmp_path / "user_data").exists()

    readings = iter([_preflight(), _preflight(census_sessions=2860)])
    changed = HistoricalPitRunStore(tmp_path / "changed.sqlite3")
    with pytest.raises(TrendLiquidityV1PitInputError):
        run_trend_liquidity_v1_pit_evaluation(
            store=changed, loader=lambda: inputs, preflight_reader=lambda: next(readings),
            code_sha="c" * 40)
    assert changed.latest_for_spec(TREND_LIQUIDITY_V1_PIT_SPEC.fingerprint) is None

    with pytest.raises(PrimaryOosNotReadyError):
        run_trend_liquidity_v1_pit_evaluation(
            store=HistoricalPitRunStore(tmp_path / "blocked.sqlite3"), loader=lambda: inputs,
            preflight_reader=lambda: _preflight(False), code_sha="c" * 40)
    assert not (tmp_path / "blocked.sqlite3").exists()


def test_api_serves_only_recorded_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    client = TestClient(app)
    empty = client.get("/api/taiwan/quant/historical-pit/trend-liquidity-v1").json()
    assert empty["status"] == "not_run" and empty["artifact"] is None
    _, _, inputs = _forward_fixture(regulatory=NO_REGULATORY_HISTORY,
                                    blocked={"TPEX"})
    run_trend_liquidity_v1_pit_evaluation(
        loader=lambda: inputs, preflight_reader=_preflight, code_sha="d" * 40)
    body = client.get("/api/taiwan/quant/historical-pit/trend-liquidity-v1").json()
    assert body["status"] == "available" and body["record_scope"] == "historical_pit"
    artifact = body["artifact"]
    assert "strict_picks" not in artifact
    assert artifact["reproducibility"]["strict_fully_reproducible_sessions"] == 0
    assert artifact["strict_result"]["top10"] is None
    starts = [r["start"] for r in artifact["reproducibility"]["excluded_session_ranges"]]
    assert starts == sorted(starts)

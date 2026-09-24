"""One current EOD freeze; this module has no historical date/model-fit CLI."""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import polars as pl

from app.taiwan.corporate_actions import CorporateActionEvent
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.observed_universe import ObservedUniverseCensus, ObservedUniverseStore
from app.taiwan.providers.corporate_actions import SOURCE_URLS, CorporateActionProvider
from app.taiwan.quant.baseline import rank_equal_weight_features
from app.taiwan.quant.live_contract import (
    FEATURES,
    LIVE_CONTRACT,
    LIVE_TIER,
    LiveModel,
    LiveSignalBatch,
    canonical_hash,
)
from app.taiwan.quant.live_store import LiveConflictError, LiveLedger, LiveRunBusyError
from app.taiwan.quant.live_universe import current_verified_universe
from app.taiwan.quant.panel import build_factor_panel
from app.taiwan.quant.regime import classify_market_regime, market_breadth_above_ma
from app.taiwan.quant_eligibility import apply_policy_filters
from app.taiwan.realtime.calendar import (
    TaiwanTradingCalendar,
    TradingDayEvidence,
    taipei_now,
)
from app.taiwan.universe.service import TaiwanSecurityMaster

PRICE_COLUMNS = ("symbol", "date", "open", "high", "low", "close", "volume", "amount")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiveInputs:
    session: date
    cutoff: datetime
    master: pl.DataFrame
    observed: pl.DataFrame
    history: pl.DataFrame
    events: tuple[CorporateActionEvent, ...]
    action_coverage: dict[str, Any]
    session_evidence: tuple[TradingDayEvidence, ...]


class CurrentLiveSource:
    """Reuse official adapters without running/writing the historical worker.

    Fresh official market observations prove the current session and membership.
    Local raw history provides prior feature bars; current official bars replace
    the target date only in memory. Complete action range responses (including
    verified empty responses) are required before adjustment is attempted.
    """
    def __init__(self, *, daily: TaiwanDailyStore | None = None,
                 calendar: TaiwanTradingCalendar | None = None,
                 clock: Callable[[], datetime] = taipei_now) -> None:
        self.daily = daily or TaiwanDailyStore()
        self.calendar = calendar or TaiwanTradingCalendar()
        self.clock = clock
        self.census = ObservedUniverseCensus()
        self.observations: dict[tuple[date, str], list[dict[str, Any]]] = {}
        self.action_cache: dict[tuple[date, date], tuple] = {}

    def close(self) -> None:
        self.census.close()

    def evidence(self, day: date, exchange: str) -> TradingDayEvidence:
        known = self.calendar.day_evidence(day, exchange)
        if known.status == "non_trading":
            return known
        key = (day, exchange)
        if key not in self.observations:
            fetch = self.census.fetch_twse if exchange == "TWSE" else self.census.fetch_tpex
            self.observations[key] = fetch(day)
        rows = self.observations[key]
        return TradingDayEvidence(day, exchange, "trading" if rows else "unresolved",
                                  "official_current_snapshot", "valid_market_rows" if rows else "unexplained_empty",
                                  self.clock())

    def load(self, session: date) -> LiveInputs:
        # Do not call ensure_loaded(): a stale local cache is not current evidence.
        master = TaiwanSecurityMaster()
        master.load_from_adapters()
        records = []
        for exchange in ("TWSE", "TPEX"):
            if self.evidence(session, exchange).status != "trading":
                raise ValueError("current_market_evidence_unavailable")
            records.extend({**r, "symbol": f'{r["raw_code"]}.{exchange}'}
                           for r in self.observations[(session, exchange)])
        observed = pl.DataFrame(records, infer_schema_length=None)
        # The fixed v1 longest feature is 60 sessions; bound disk IO and computing.
        start = session - timedelta(days=200)
        history = self.daily.read_range(None, start, session - timedelta(days=1))
        current = observed.select(PRICE_COLUMNS)
        history = pl.concat([history.select(PRICE_COLUMNS), current], how="diagonal_relaxed")
        # A stored nonempty raw market observation proves a session, but a
        # missing weekday never proves closure. Optional existing calendar
        # evidence can fill a closure without waiting for historical backfill.
        evidence = []
        census_store = ObservedUniverseStore()
        for exchange in ("TWSE", "TPEX"):
            observed_days = set(history.filter(
                pl.col("symbol").str.ends_with("." + exchange) & (pl.col("close") > 0)
                & (pl.col("volume") > 0))["date"].to_list())
            cursor = start
            while cursor <= session:
                fact = census_store.day_evidence(exchange, cursor, calendar=self.calendar)
                if cursor in observed_days:
                    if fact.status == "non_trading":
                        raise ValueError("conflicting_feature_session_evidence")
                    fact = TradingDayEvidence(cursor, exchange, "trading", "taiwan_daily_store",
                                              "raw_market_observation")
                evidence.append(fact)
                cursor += timedelta(days=1)
        events, coverage = self.actions(start, session)
        return LiveInputs(session, self.clock(), master.to_dataframe(), observed, history,
                          events, coverage, tuple(evidence))

    def actions(self, start: date, end: date):
        if (start, end) in self.action_cache:
            return self.action_cache[(start, end)]
        provider = CorporateActionProvider()
        events = []
        try:
            for source in SOURCE_URLS:
                events.extend(provider.fetch(source, start, end))
        finally:
            provider.close()
        result = tuple(events), {
            "status": "verified", "start": start, "end": end,
            "sources": sorted(SOURCE_URLS), "retrieved_at": self.clock(),
        }
        self.action_cache[(start, end)] = result
        return result

    def outcome_prices(self, symbol: str, sessions: list[date]) -> pl.DataFrame:
        exchange = symbol.split(".")[1]
        rows = []
        for day in sessions:
            self.evidence(day, exchange)
            matches = [r for r in self.observations.get((day, exchange), ())
                       if f'{r["raw_code"]}.{exchange}' == symbol]
            if len(matches) == 1:
                rows.append({"symbol": symbol, "date": day, "close": matches[0]["close"]})
        return pl.DataFrame(rows, schema={"symbol": pl.String, "date": pl.Date, "close": pl.Float64})


def build_live_batch(inputs: LiveInputs, model: LiveModel) -> LiveSignalBatch:
    if not isinstance(inputs, LiveInputs):
        raise TypeError("live runner requires current source evidence, not OOS predictions")
    session = inputs.session
    coverage = inputs.action_coverage
    if (coverage.get("status") != "verified" or set(coverage.get("sources", ())) != set(SOURCE_URLS)
            or coverage["end"] < session or coverage["start"] > inputs.history["date"].min()):
        raise ValueError("corporate_action_coverage_unavailable")
    if inputs.cutoff.utcoffset() is None:
        raise ValueError("data cutoff must be timezone-aware")
    if any(e.retrieved_at > inputs.cutoff for e in inputs.events):
        raise ValueError("corporate_action_after_cutoff")
    universe = current_verified_universe(inputs.master, inputs.observed,
                                        session=session, cutoff=inputs.cutoff)
    if universe.is_empty():
        raise ValueError("current_verified_universe_unavailable")
    symbols = universe["symbol"].to_list()
    raw = inputs.history.filter(pl.col("symbol").is_in(symbols) & (pl.col("date") <= session))
    if not raw.filter(pl.col("date") == session).select(PRICE_COLUMNS).sort("symbol").equals(
            inputs.observed.filter(pl.col("symbol").is_in(symbols)).select(PRICE_COLUMNS).sort("symbol"),
            null_equal=True):
        raise ValueError("current prices differ from verified market evidence")
    if raw.select(pl.struct("symbol", "date").is_duplicated().any()).item():
        raise ValueError("duplicate feature history")
    panel = build_factor_panel(raw, events=inputs.events, policy_version=model.policy_version,
                               factor_version=model.factor_version, universe_tier=LIVE_TIER,
                               as_of=session)
    if panel.values.is_empty():
        raise ValueError("current_factor_data_unavailable")
    latest = panel.values
    if latest.filter((pl.col("adjustment_status") == "verified") &
                     pl.all_horizontal(pl.col(f).is_not_null() & pl.col(f).is_finite()
                                       for f in FEATURES)).is_empty():
        raise ValueError("ranking_features_data_insufficient:requires_61_verified_price_sessions")
    warmup = dict(raw.group_by("symbol").len().select("symbol", "len").iter_rows())
    adv = {r["symbol"]: r["adv20_twd"] for r in latest.to_dicts() if r["adv20_twd"] is not None}
    eligible = apply_policy_filters(
        universe, min_warmup_sessions=model.min_warmup_sessions, min_adv20_twd=model.min_adv20_twd,
        warmup_sessions=warmup, adv20_twd=adv)
    eligible_symbols = eligible["symbol"].to_list()
    features = latest.filter(pl.col("symbol").is_in(eligible_symbols))
    usable = features.filter((pl.col("adjustment_status") == "verified") &
                             pl.all_horizontal(pl.col(f).is_not_null() & pl.col(f).is_finite()
                                               for f in FEATURES))
    # Reject gapped symbol windows: suspensions/missing bars do not silently
    # change the session horizon. Compare with observed exchange-wide raw dates.
    complete = []
    exclusions = {}
    for symbol in usable["symbol"].to_list():
        exchange = symbol.split(".")[1]
        facts = {e.date: e for e in inputs.session_evidence if e.exchange == exchange and e.date <= session}
        expected = sorted(day for day, e in facts.items() if e.status == "trading")[-61:]
        if expected:
            cursor = expected[0]
            while cursor <= session:
                if cursor not in facts or facts[cursor].status == "unresolved":
                    raise ValueError(f"feature_session_evidence_unresolved:{exchange}:{cursor}")
                cursor += timedelta(days=1)
        actual = raw.filter(pl.col("symbol") == symbol)["date"].sort().tail(61).to_list()
        if actual == expected and len(actual) == 61:
            complete.append(symbol)
        else:
            exclusions[symbol] = "incomplete_trading_session_window"
    usable = usable.filter(pl.col("symbol").is_in(complete))
    composite = rank_equal_weight_features(usable.sort("symbol").to_dicts(), FEATURES)
    rows = []
    for row in usable.sort("symbol").to_dicts():
        ranked = composite[row["symbol"]]
        score = ranked["score"]
        rows.append({"symbol": row["symbol"], "score": score,
                     "feature_percentiles": ranked["feature_percentiles"],
                     "selected": score >= model.min_rank and row["momentum_20d"] > 0})
    ordered = sorted(rows, key=lambda row: (-row["score"], row["symbol"]))
    for index, row in enumerate(ordered):
        row["rank"] = index + 1
    reference = dict(raw.filter(pl.col("date") == session).select("symbol", "close").iter_rows())
    signals = [{**row, "reference_close": reference[row["symbol"]],
                "confidence": None, "confidence_status": "not_implemented",
                "probability": None, "risk_assessment": None, "risk_status": "not_implemented"}
               for row in ordered if row["selected"]][:model.top_n]
    # Existing regime computes breadth; absent index history remains explicitly
    # data_insufficient and contributes no fabricated price/turnover observations.
    breadth_rows = usable.join(raw.filter(pl.col("date") == session).select("symbol", "close"), on="symbol")
    breadth = market_breadth_above_ma(breadth_rows, session)
    regime = classify_market_regime(
        pl.DataFrame({"date": [session], "close": [None]}, schema={"date": pl.Date, "close": pl.Float64}),
        as_of=session, breadth_above_ma20=breadth, source="index_history_unavailable").describe()
    if breadth is None:
        regime.update(regime=None, status="data_insufficient")
    else:
        regime["status"] = "partial"
    snapshot = {
        "contract": LIVE_CONTRACT, "signal_session": session, "data_cutoff": inputs.cutoff,
        "model": model.describe(), "feature_price_anchor": session,
        "verified_universe": universe.to_dicts(), "eligible_universe": eligible.to_dicts(),
        "ranking_universe": sorted(complete),
        "ranking_exclusions": exclusions,
        "session_evidence": [e.describe() for e in inputs.session_evidence],
        "features": features.to_dicts(), "factor_coverage": panel.coverage.to_dicts(),
        "feature_snapshot_hash": canonical_hash(features.to_dicts()),
        "ranking": ordered, "signals": signals, "regime": regime,
        "raw_history_hash": canonical_hash(raw.to_dicts()),
        "raw_history_source": "taiwan_daily_store_raw+official_current_snapshot",
        "corporate_action_coverage": coverage,
        "corporate_actions": [event.to_dict() for event in inputs.events],
        "usage_scope": "experimental_live", "validation_state": "unvalidated",
        "horizons": [1, 5, 20],
    }
    return LiveSignalBatch(model, session, snapshot)


def run_current_live(*, source=None, ledger: LiveLedger | None = None,
                     model: LiveModel | None = None) -> dict[str, Any]:
    own_source = source is None
    source = source or CurrentLiveSource()
    ledger = ledger or LiveLedger(evidence=source.evidence)
    model = model or LiveModel()
    try:
        with ledger.construction_lock(model):
            metadata = ledger.activate(model)
            session = ledger.current_session()
            existing = ledger.read_run(model.key, session.isoformat())
            if existing:
                if existing["audit_status"] == "conflict":
                    raise LiveConflictError("existing signal identity has an unresolved conflict")
                result = {"status": "noop", "session": session.isoformat(),
                          "snapshot_hash": existing["snapshot_hash"]}
            else:
                inputs = source.load(session)
                if inputs.session != session or inputs.cutoff > ledger.clock():
                    raise ValueError("live source session/cutoff mismatch")
                result = ledger.freeze(build_live_batch(inputs, model))
            result["model"] = metadata
    except LiveRunBusyError as exc:
        result = {"status": "skipped", "reason": str(exc)}
    except LiveConflictError as exc:
        result = {"status": "conflict", "reason": str(exc)}
    except ValueError as exc:
        result = {"status": "blocked", "reason": str(exc)}
    except Exception as exc:
        result = {"status": "blocked", "reason": f"live_source_or_storage_error:{type(exc).__name__}"}
    finally:
        if own_source:
            source.close()
    ledger.record_operation(result)
    return result


def run_live_after_refresh(result, *, app_state=None) -> dict[str, Any]:
    """Scheduler seam: never changes the refresh result or its success status."""
    if result.daily.status != "success":
        status = {"status": "skipped", "reason": "daily_refresh_not_ready"}
        LiveLedger().record_operation(status)
        return status
    return run_live_cycle(app_state=app_state)


def _evaluate_live_quant_alerts(freeze: dict[str, Any], ledger: LiveLedger, app_state=None) -> dict[str, Any]:
    """Evaluate alerts only after the current audited live batch was frozen."""
    if freeze.get("status") not in {"frozen", "noop"}:
        return {"status": "unavailable", "reason": "live_freeze_not_current", "appended": 0}
    session = freeze.get("session")
    if not isinstance(session, str) or not session:
        return {"status": "unavailable", "reason": "live_session_missing", "appended": 0}

    model = LiveModel()
    run = ledger.read_run(model.key, session)
    if (run is None or run.get("audit_status") != "ok"
            or run.get("session") != session):
        return {"status": "unavailable", "reason": "live_snapshot_not_audited", "appended": 0}
    signals = (run.get("snapshot") or {}).get("signals")
    if not isinstance(signals, list):
        return {"status": "unavailable", "reason": "live_signals_missing", "appended": 0}

    from app.config import settings
    from app.services import alert_store
    from app.taiwan.realtime.monitor_engine import get_monitor_engine

    events = get_monitor_engine().evaluate_quant_top10(signals, session)
    if events:
        alert_store.append_many(settings.data_dir, events)
        quote_service = getattr(app_state, "quote_service", None)
        if quote_service is not None:
            quote_service.push_alerts(events)
    return {"status": "available", "appended": len(events)}


def run_live_cycle(*, app_state=None) -> dict[str, Any]:
    """Freeze latest completed session, then independently mature prior signals."""
    from app.taiwan.quant.live_outcomes import mature_live_outcomes

    source = CurrentLiveSource()
    ledger = LiveLedger(evidence=source.evidence)
    try:
        freeze = run_current_live(source=source, ledger=ledger)
        try:
            alerts = _evaluate_live_quant_alerts(freeze, ledger, app_state)
        except Exception as exc:
            logger.exception("Live Quant reminder evaluation failed")
            alerts = {"status": "blocked", "reason": type(exc).__name__, "appended": 0}
        try:
            maturation = mature_live_outcomes(ledger, source)
        except Exception as exc:
            maturation = {"status": "blocked", "reason": f"maturation_error:{type(exc).__name__}"}
        combined = {"freeze": freeze, "quant_alerts": alerts, "maturation": maturation}
        ledger.record_operation(combined)
        return combined
    finally:
        source.close()


if __name__ == "__main__":
    import argparse
    import json

    # Operational retry only; deliberately no --date, --from or import flag.
    argparse.ArgumentParser(description="Freeze the latest completed experimental live session").parse_args()
    print(json.dumps(run_live_cycle(), ensure_ascii=False, default=str))

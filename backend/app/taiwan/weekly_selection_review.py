"""Freeze the original daily selection at the last session of an ISO week."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import math
import os
import tempfile
import zlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl

from app.file_lock import file_lock
from app.taiwan.corporate_actions import event_market_open
from app.taiwan.observed_universe import TPEX_SOURCE, TWSE_SOURCE
from app.taiwan.providers.taiwan_values import market_close
from app.taiwan.quant.live_contract import canonical_hash, canonical_json
from app.taiwan.realtime.calendar import TAIPEI_TZ, TaiwanTradingCalendar, taipei_now
from app.taiwan.selection_review_models import SelectionSnapshot
from app.taiwan.selection_v2 import strategy_metadata
from app.taiwan.symbol import parse_history_symbol, parse_symbol
from app.taiwan.trading_day_evidence import (
    TPEX_EVIDENCE_SOURCE,
    TWSE_EVIDENCE_SOURCE,
    TWSE_SCHEDULE_SOURCE,
)

if TYPE_CHECKING:
    from app.taiwan.daily_store import TaiwanDailyStore
    from app.taiwan.institutional_store import TaiwanInstitutionalStore
    from app.taiwan.observed_universe import ObservedUniverseStore

logger = logging.getLogger(__name__)
FLOW = ("foreign_net", "investment_trust_net", "dealer_net")
# Exact daily strategy inputs only. No descriptions, arbitrary dicts or weekly thresholds.
DAILY_INPUTS = (
    "amount", "ma5", "ma20", "ma60", "trend_adjusted_close", "change_pct",
    "rsi_14", "momentum_5d", "momentum_20d", "vol_ratio_20d", "momentum_acceleration",
    "breakout_20d_strength", "breakout_60d_strength", "foreign_net_5d",
    "investment_trust_net_5d", "institutional_flow_ratio_5d", "consensus_hit_count",
    "revenue_yoy", "revenue_yoy_improving",
)
CORE_INPUTS = {
    "trend_liquidity_v1": ("amount", "ma20", "momentum_5d", "trend_adjusted_close"),
    "institutional_momentum_v1": ("amount", "ma20", "foreign_net_5d", "investment_trust_net_5d",
                                  "institutional_flow_ratio_5d"),
    "growth_trend_v1": ("amount", "ma20", "ma60", "momentum_5d", "momentum_20d",
                        "revenue_yoy", "revenue_yoy_improving"),
    "breakout_v1": ("amount", "vol_ratio_20d", "momentum_acceleration", "ma20"),
    "pullback_support_v1": ("amount", "ma20", "ma60", "momentum_5d"),
    "foreign_trend_v1": ("amount", "ma20", "ma60", "momentum_20d", "foreign_net_5d"),
    "oversold_rebound_v1": ("amount", "change_pct", "ma5", "rsi_14"),
    "multi_factor_consensus_v1": ("amount", "consensus_hit_count"),
}
# Core inputs derived from institutional flow. Only a strategy that reads one of these
# depends on the same-day nets; for the others a missing net stays null in the snapshot
# and must not turn a formal pick into no_signal.
FLOW_INPUTS = frozenset({"foreign_net_5d", "investment_trust_net_5d", "institutional_flow_ratio_5d",
                         "consensus_hit_count"})


@dataclass
class WeeklyCandidates:
    """Output-only view of this execution, including rows removed by the screener."""
    symbols: set[str] = field(default_factory=set)
    eligible: set[str] = field(default_factory=set)
    risk_excluded: set[str] = field(default_factory=set)
    facts: dict[str, dict] = field(default_factory=dict)
    revenue_observed_at: str | None = None

    def observe(self, frame: pl.DataFrame, ranked: pl.DataFrame, risk_excluded: set[str]) -> None:
        fields = ("symbol", "date", "institutional_date", "revenue_latest_period", *DAILY_INPUTS)
        self.facts = {r["symbol"]: r for r in frame.select(
            [c for c in fields if c in frame.columns]
        ).iter_rows(named=True)}
        self.eligible = set(ranked["symbol"])
        self.risk_excluded = risk_excluded


@lru_cache(maxsize=3)
def _schedule(year: int) -> frozenset[date]:
    from app.taiwan.trading_day_evidence import fetch_twse_closures

    return fetch_twse_closures(year)


@dataclass
class SessionPlan:
    source_day: date
    following: date
    closures: dict[str, dict[str, str]]

    @property
    def closes_week(self) -> bool:
        return self.following.isocalendar()[:2] != self.source_day.isocalendar()[:2]


def _official_sources(sources: object) -> bool:
    return (isinstance(sources, dict) and set(sources) == {"TWSE", "TPEX"}
            and sources["TWSE"] in (TWSE_SCHEDULE_SOURCE, TWSE_EVIDENCE_SOURCE)
            and sources["TPEX"] in (TWSE_SCHEDULE_SOURCE, TPEX_EVIDENCE_SOURCE))


def resolve_session(day: date, calendar: TaiwanTradingCalendar, census: ObservedUniverseStore | None,
                    *, use_official_schedule: bool = False,
                    frozen_closures: dict[str, dict[str, str]] | None = None) -> SessionPlan:
    """Unknown weekdays remain possible opens unless official closure evidence covers both markets."""
    closures = {}
    for offset in range(1, 31):
        candidate = day + timedelta(days=offset)
        evidence = {
            market: (census.day_evidence(market, candidate, calendar=calendar) if census is not None
                     else calendar.day_evidence(candidate, market)) for market in ("TWSE", "TPEX")
        }
        statuses = {fact.status for fact in evidence.values()}
        key = candidate.isoformat()
        conflict = any(fact.reason == "conflicting_calendar_evidence" for fact in evidence.values())
        if census is not None:
            local = calendar.day_evidence(candidate, "TWSE")
            if local.status == "trading":
                statuses.add("trading")
            conflict = conflict or local.reason == "conflicting_calendar_evidence"
        if frozen_closures and key in frozen_closures:
            if "trading" in statuses or conflict:
                raise ValueError("weekly_calendar_evidence_conflict")
            continue
        if "trading" in statuses or conflict:
            return SessionPlan(day, candidate, closures)
        if candidate.weekday() >= 5 and statuses == {"non_trading"}:
            continue
        sources = {market: fact.evidence_source for market, fact in evidence.items()}
        if statuses == {"non_trading"} and _official_sources(sources):
            closures[key] = sources
            continue
        if use_official_schedule:
            try:
                if candidate in _schedule(candidate.year):
                    closures[key] = dict.fromkeys(evidence, TWSE_SCHEDULE_SOURCE)
                    continue
            except Exception as exc:
                logger.warning("Weekly capture calendar unavailable: %s", type(exc).__name__)
        if statuses != {"non_trading"}:
            return SessionPlan(day, candidate, closures)
    raise ValueError("no next market session within the supported calendar window")


def next_session(day: date, calendar: TaiwanTradingCalendar, census: ObservedUniverseStore | None,
                 *, use_official_schedule: bool = False) -> date:
    return resolve_session(day, calendar, census, use_official_schedule=use_official_schedule).following


def recorded_next_session(row: dict, calendar: TaiwanTradingCalendar, census: ObservedUniverseStore | None,
                          *, bound: bool) -> date:
    """Resolve a recorded deadline offline. Only a bound file may supply official closure evidence."""
    day = date.fromisoformat(row["last_trading_day"])
    saved = row.get("calendar_evidence")
    if not bound or saved is None:
        return next_session(day, calendar, census)
    if not isinstance(saved, dict) or set(saved) != {"next_session", "closures"}:
        raise ValueError("invalid_weekly_calendar_evidence")
    try:
        target = date.fromisoformat(saved["next_session"])
        closures = saved["closures"]
        if not (day < target <= day + timedelta(days=30) and isinstance(closures, dict)):
            raise ValueError
        for raw, sources in closures.items():
            closure = date.fromisoformat(raw)
            if not (day < closure < target and closure.isoformat() == raw and _official_sources(sources)):
                raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_weekly_calendar_evidence") from exc
    if resolve_session(day, calendar, census, frozen_closures=closures).following != target:
        raise ValueError("weekly_calendar_evidence_conflict")
    return target


def _check_window(day: date, next_day: date, now: datetime) -> None:
    if next_day <= day or next_day.isocalendar()[:2] == day.isocalendar()[:2]:
        raise ValueError("week_not_closed")
    if not market_close(day) <= now < event_market_open(next_day):
        raise ValueError("outside_weekly_capture_window")


def _number(value: Any) -> float | int | bool | None:
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


def _as_of(value: object, day: date) -> date | None:
    if value is None:
        return None
    parsed = date.fromisoformat(str(value))
    if parsed > day:
        raise ValueError("future_snapshot_date")
    return parsed


def _period(value: str | None, day: date) -> str | None:
    if value is not None:
        _as_of(value + "-01" if len(value) == 7 else value, day)
    return value


def _flow_evidence(daily: TaiwanDailyStore, store: TaiwanInstitutionalStore, day: date,
                   calendar: TaiwanTradingCalendar, census: ObservedUniverseStore | None, *, use_official_schedule: bool):
    """Check the five source sessions of the observed daily factors before freezing them."""
    sessions = sorted(d for d in daily.available_dates() if d <= day)[-5:]
    # Five stored partitions can span six trading sessions when a market refresh
    # missed a day. Preserve today's flows while withholding the incomplete factors.
    if len(sessions) != 5 or sessions[-1] != day or any(
        next_session(previous, calendar, census, use_official_schedule=use_official_schedule) != current
        for previous, current in pairwise(sessions)
    ):
        sessions = [day]
    try:
        history = store.read_range(None, sessions[0], day)
    except pl.exceptions.ColumnNotFoundError:
        # Old partitions may predate required columns.
        history = pl.DataFrame()
    today = {}
    valid_counts: dict[str, dict[str, int]] = {}
    for row in history.iter_rows(named=True):
        session = _as_of(row.get("date"), day)
        if session not in sessions:
            continue
        symbol = row["symbol"]
        trade_day = _as_of(row.get("trade_date"), day)
        source = "twse:t86" if symbol.endswith(".TWSE") else "tpex:daily_trade"
        official, computed = row.get("official_net"), row.get("computed_net")
        discrepancy = row.get("has_discrepancy") is True or (
            official is not None and computed is not None and official != computed)
        valid = (trade_day == session and row.get("source") == source
                 and row.get("status") in {"official", "available"} and not discrepancy)
        nets = {field: _number(row.get(field)) if valid else None for field in FLOW}
        counts = valid_counts.setdefault(symbol, dict.fromkeys(FLOW, 0))
        for column, value in nets.items():
            if value is not None:
                counts[column] += 1
        if session == day:
            today[symbol] = (trade_day, nets, discrepancy)
    return today, valid_counts


def _source_day(value: str | None, day: date) -> date | None:
    if value is None or len(value) == 10:
        return _as_of(value, day)
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() is None:
        raise ValueError("source_timestamp_missing_timezone")
    return _as_of(stamp.astimezone(TAIPEI_TZ).date(), day)


def _official_bars(store: TaiwanDailyStore, start: date, end: date) -> pl.DataFrame:
    frame = store.read_range(None, start, end, include_provenance=True)
    if frame.is_empty():
        return frame
    if not frame["date"].is_between(start, end).all():
        raise ValueError("daily_partition_date_mismatch")
    exchange = pl.col("symbol").str.split(".").list.last().str.to_lowercase()
    official = (pl.col("source") == "twse_tpex_official_snapshot") | (
        (pl.col("source") == "official_daily_kline") & (pl.col("provider") == exchange)
    )
    return frame.filter(official & pl.col("status").is_in(["official", "stale"])).sort("date")


def _weekly_prices(store: TaiwanDailyStore, start: date, day: date) -> dict[str, dict]:
    frame = _official_bars(store, start, day)
    if frame.is_empty():
        return {}
    # Ignore no-trade placeholders, retain missing OHLC/volume as null in the background.
    frame = frame.filter(pl.col("close").is_finite() & (pl.col("close") > 0))
    bars = frame.group_by("symbol").agg(
        pl.col("date").first().alias("first_session"), pl.col("date").last().alias("last_session"),
        pl.col("open").first(), pl.col("close").last(),
        *(pl.when(pl.col(c).is_finite().fill_null(False).all()).then(expr).alias(c)
          for c, expr in (("high", pl.col("high").max()), ("low", pl.col("low").min()),
                          ("volume", pl.col("volume").sum()))),
    )
    return {row.pop("symbol"): row for row in bars.iter_rows(named=True)}


def _prior_picks(history: list[SelectionSnapshot], strategy: str, start: date, day: date,
                 now: datetime, calendar: TaiwanTradingCalendar, census: ObservedUniverseStore | None,
                 *, use_official_schedule: bool = False) -> tuple[dict[str, date], set[date]]:
    last_seen: dict[str, date] = {}
    dates = set()
    for batch in history:
        if batch.record_type != "forward_batch" or batch.strategy_id != strategy or not batch.locked_at:
            continue
        source = date.fromisoformat(batch.as_of_date)
        if not (start <= source < day and batch.source_data_date == batch.as_of_date and batch.target_trade_date):
            continue
        locked = datetime.fromisoformat(batch.locked_at)
        if locked.utcoffset() is None or not market_close(source) <= locked <= now:
            continue
        following = next_session(source, calendar, census, use_official_schedule=use_official_schedule)
        if batch.target_trade_date != following.isoformat() or locked >= event_market_open(following):
            continue
        symbols = [item.symbol for item in batch.items]
        if len(symbols) != len(set(symbols)) or set(symbols) != set(batch.selected_symbols):
            raise ValueError(f"invalid_frozen_membership: {batch.snapshot_id}")
        dates.add(source)
        for symbol in symbols:
            last_seen[symbol] = max(source, last_seen.get(symbol, source))
    return last_seen, dates


def _classify(*, selected: bool, prior: bool, missing: list[str], official_batch: bool,
              eligible: bool, risk: bool) -> tuple[str, str]:
    if official_batch and prior and not selected:
        return "no_signal", "dropped_before_week_close"
    if missing:
        return "no_signal", "data_incomplete"
    if selected:
        return "signal", "selected_at_week_close"
    if eligible:
        return "watch", "outside_formal_selection_limit"
    return "no_signal", "risk_excluded" if risk else "daily_conditions_not_met"


def capture_week(root: Path, day: date, strategy: str, evidence: WeeklyCandidates,
                 history: list[SelectionSnapshot], daily: TaiwanDailyStore, institutional: TaiwanInstitutionalStore,
                 calendar: TaiwanTradingCalendar, census: ObservedUniverseStore | None = None,
                 *, use_official_schedule: bool = False, stage_for_batch: bool = False) -> Path:
    """Publish exactly one complete JSONL per week/strategy. Never accepts a caller-supplied clock."""
    now = taipei_now()
    plan = resolve_session(day, calendar, census, use_official_schedule=use_official_schedule)
    next_day = plan.following
    _check_window(day, next_day, now)
    start = day - timedelta(days=day.weekday())
    week = day.strftime("%G-W%V")
    meta = strategy_metadata(strategy)
    dest = root / week / f"{strategy}.jsonl"
    if dest.exists():
        raise FileExistsError(dest)
    batch = next((b for b in history if b.record_type == "forward_batch" and b.strategy_id == strategy
                  and b.as_of_date == day.isoformat()), None)
    if batch and (not batch.locked_at or not market_close(day) <= datetime.fromisoformat(batch.locked_at) <= now
                  or batch.source_data_date != day.isoformat() or batch.target_trade_date != next_day.isoformat()
                  or not batch.strategy_version):
        raise ValueError("invalid_formal_batch_provenance")
    selected = {item.symbol: item for item in batch.items} if batch else {}
    risk_day = _source_day(batch.risk_source_as_of, day) if batch else None
    revenue_day = _source_day(evidence.revenue_observed_at, day)
    last_seen, prior_dates = _prior_picks(history, strategy, start, day, now, calendar, census,
                                          use_official_schedule=use_official_schedule)
    symbols = evidence.symbols | selected.keys() | last_seen.keys()
    if not symbols:
        raise ValueError("candidate_universe_unavailable")
    bars = _weekly_prices(daily, start, day)
    if not any(bar["last_session"] == day for bar in bars.values()) and "trading" not in {
            (census.day_evidence(m, day, calendar=calendar) if census is not None else
             calendar.day_evidence(day, m)).status for m in ("TWSE", "TPEX")
    }:
        raise ValueError("last_trading_session_unverified")
    flow, flow_counts = _flow_evidence(daily, institutional, day, calendar, census, use_official_schedule=use_official_schedule)
    records = []
    for symbol in sorted(symbols):
        # The daily screener's universe is already resolved from the official directory.
        # A dropped historical pick may have been delisted since its last appearance.
        instrument = (parse_symbol(symbol, verify_listing=True) if symbol in evidence.symbols
                      else parse_history_symbol(symbol))
        fact = evidence.facts.get(symbol, {})
        quote_date = _as_of(fact.get("date"), day)
        observed_inst = _as_of(fact.get("institutional_date"), day)
        revenue_period = _period(fact.get("revenue_latest_period"), day)
        prices = bars.get(symbol, {})
        price = prices.get("close") if prices.get("last_session") == day else None
        inst_day, nets, discrepancy = flow.get(symbol, (None, dict.fromkeys(FLOW), False))
        inputs = {c: _number(fact.get(c)) for c in DAILY_INPUTS}
        counts = flow_counts.get(symbol, {})
        for column in ("foreign_net", "investment_trust_net"):
            if counts.get(column) != 5:
                inputs[f"{column}_5d"] = None
        if any(counts.get(field) != 5 for field in FLOW):
            inputs["institutional_flow_ratio_5d"] = None
            if strategy == "multi_factor_consensus_v1":
                # The formal hit count and ranking use the institutional component.
                inputs["consensus_hit_count"] = None
        required = CORE_INPUTS[strategy]
        missing = [c for c in required if inputs[c] is None]
        if strategy == "breakout_v1" and all(inputs[c] is None for c in ("breakout_20d_strength", "breakout_60d_strength")):
            missing.append("breakout_history")
        if FLOW_INPUTS.intersection(required):
            missing.extend(c for c, value in nets.items() if value is None)
            if discrepancy:
                missing.append("institutional_discrepancy")
        if quote_date != day or price is None:
            missing.append("official_close")
        item = selected.get(symbol)
        if item and item.price != price:
            missing.append("selection_price_mismatch")
        if not batch:
            missing.append("formal_batch_unavailable")
        decision, reason = _classify(selected=item is not None, prior=symbol in last_seen, missing=missing,
                                     official_batch=batch is not None, eligible=symbol in evidence.eligible,
                                     risk=symbol in evidence.risk_excluded)
        snapshot = {
            "quote_date": quote_date, "week_bar": prices or None, "institutional_date": inst_day,
            "strategy_institutional_date": observed_inst, "revenue_period": revenue_period,
            "risk_source_date": risk_day, "revenue_observation_date": revenue_day,
            "institutional_net_shares": nets, "daily_inputs": inputs, "rank": item.rank if item else None,
            "prior_formal_output_dates": sorted(prior_dates), "missing_fields": missing,
        }
        source_strategy = {"id": strategy, "name": batch.strategy_name if batch else meta["name"],
                           "version": batch.strategy_version if batch else meta["version"],
                           "rule_version": (batch.rule_version if batch else None) or strategy}
        record = {
            "schema_version": 1, "week": week, "last_trading_day": day, "code": instrument.code,
            "calendar_evidence": {"next_session": next_day, "closures": plan.closures},
            "market": instrument.exchange.value, "symbol": symbol, "source_strategy": source_strategy,
            "signal_basis": "daily_strategy_weekly_snapshot", "pattern_tag": strategy.rsplit("_v", 1)[0],
            "pattern_basis": "source_strategy_preset", "decision": decision, "reason": reason,
            "last_selected_date": day if item else last_seen.get(symbol), "reference_close": price,
            "executable_reference_open": None, "formal_batch_id": batch.snapshot_id if batch else None,
            "snapshot": snapshot, "snapshot_sha256": canonical_hash(snapshot), "written_at": now,
        }
        record["record_sha256"] = canonical_hash(record)
        records.append(canonical_json(record))
    if stage_for_batch:
        # The formal writer holds its cross-process guard. An abandoned, uncommitted
        # stage can be replaced until the daily batch binds it by content digest.
        dest = dest.with_suffix(".pending")
    _publish(dest, records, deadline=event_market_open(next_day), replace_pending=stage_for_batch)
    return dest


def _read_ledger(path: Path) -> bytes:
    """Return a ledger's canonical text bytes, whether stored gzip or legacy plain."""
    data = path.read_bytes()
    if data[:2] != b"\x1f\x8b":
        return data
    try:
        return gzip.decompress(data)
    except (OSError, EOFError, zlib.error) as exc:
        raise ValueError("weekly_ledger_corrupt") from exc


def file_sha256(path: Path) -> str:
    # Hash the uncompressed text so digests do not depend on storage encoding.
    try:
        data = _read_ledger(path)
    except ValueError:
        data = path.read_bytes()  # corrupt gzip: its raw digest cannot match a recorded one
    return hashlib.sha256(data).hexdigest()


def finish_week(path: Path, batch: SelectionSnapshot, calendar: TaiwanTradingCalendar,
                census: ObservedUniverseStore | None) -> None:
    """Publish only the exact candidate observations that the daily writer committed."""
    if batch.weekly_capture_error:
        raise ValueError(f"weekly_capture_rejected ({batch.weekly_capture_error})：正式日批次已鎖定，週快照未建立")
    expected = batch.weekly_records_sha256
    if not expected:
        # Older writers could combine an old batch ID with factors from a retry.
        # A self-consistent JSONL alone cannot establish that it came from this lock.
        raise ValueError("weekly_original_observations_unavailable：正式日批次未保存可驗證的原始週觀測")
    with file_lock(path.with_suffix(".lock")):
        artifact = path if path.is_file() else path.with_suffix(".pending")
        if not artifact.is_file():
            raise ValueError("weekly_original_observations_unavailable")
        if file_sha256(artifact) != expected:
            raise ValueError("weekly_batch_digest_mismatch")
        target = bound_batch_next_session(read_records(artifact), batch, calendar, census)
        if artifact != path:
            _check_window(date.fromisoformat(batch.as_of_date), target, taipei_now())
            artifact.rename(path)


def _publish(dest: Path, records: list[str], *, deadline: datetime | None = None,
             replace_pending: bool = False) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(gzip.compress(("\n".join(records) + "\n").encode("utf8"), mtime=0))
            stream.flush()
            os.fsync(stream.fileno())
        with file_lock(dest.with_suffix(".lock")):
            if dest.exists() and not replace_pending:
                raise FileExistsError(dest)
            if deadline is not None and taipei_now() >= deadline:
                raise ValueError("outside_weekly_capture_window")
            if replace_pending:
                temporary.replace(dest)
            else:
                temporary.rename(dest)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_records(path: Path) -> list[dict]:
    records = []
    with io.StringIO(_read_ledger(path).decode("utf8")) as stream:
        for line in stream:
            record = json.loads(line)
            if canonical_hash(record["snapshot"]) != record["snapshot_sha256"] or canonical_hash(
                {k: v for k, v in record.items() if k != "record_sha256"}
            ) != record["record_sha256"]:
                raise ValueError("weekly_record_digest_mismatch")
            records.append(record)
    if not records:
        raise ValueError("empty_weekly_batch")
    return records


def bound_batch_next_session(records: list[dict], batch: SelectionSnapshot, calendar: TaiwanTradingCalendar,
                             census: ObservedUniverseStore | None) -> date:
    """Resolve the immutable file's deadline once, after verifying its batch digest."""
    first = records[0]
    if any(row.get("last_trading_day") != batch.as_of_date or row.get("formal_batch_id") != batch.snapshot_id
           or row.get("calendar_evidence") != first.get("calendar_evidence") for row in records):
        raise ValueError("weekly_formal_batch_mismatch")
    target = recorded_next_session(first, calendar, census, bound=True)
    if target.isoformat() != batch.target_trade_date:
        raise ValueError("weekly_formal_batch_mismatch")
    return target


def read_execution_opens(path: Path, records: list[dict], target: date, cutoff: datetime) -> dict[str, dict]:
    """Read a complete observation batch, checking the frozen decision, session and time."""
    if cutoff.utcoffset() is None:
        raise ValueError("execution_open_cutoff_needs_timezone")
    close = market_close(target)
    companion = path.with_suffix(".opens.jsonl")
    if cutoff < close or not companion.is_file():
        return {}
    expected = {r["record_sha256"]: r["symbol"] for r in records}
    found = {}
    # Earlier versions had no observation timestamp. A preserved mtime can
    # establish a later availability bound; a historical opening price alone cannot.
    legacy_time = max(close, datetime.fromtimestamp(companion.stat().st_mtime, TAIPEI_TZ))
    latest = close
    with io.StringIO(_read_ledger(companion).decode("utf8")) as stream:
        for line in stream:
            try:
                event = json.loads(line)
                digest = event["record_sha256"]
                if canonical_hash({k: v for k, v in event.items() if k != "observation_sha256"}) != event["observation_sha256"]:
                    raise ValueError("execution_open_digest_mismatch")
                if digest in found or digest not in expected or event["symbol"] != expected[digest] or event["session"] != target.isoformat():
                    raise ValueError("execution_open_binding_mismatch")
                observed = datetime.fromisoformat(event["observed_at"]) if "observed_at" in event else legacy_time
                if observed.utcoffset() is None or observed < close:
                    raise ValueError("execution_open_observation_time_invalid")
                price = event["executable_reference_open"]
                if event["status"] == "available":
                    if isinstance(price, bool) or _number(price) is None or price <= 0:
                        raise ValueError("execution_open_price_invalid")
                elif event["status"] != "official_open_unavailable" or price is not None:
                    raise ValueError("execution_open_status_invalid")
                found[digest] = event
                latest = max(latest, observed)
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError("invalid_execution_open_record") from exc
    if found.keys() != expected.keys() or len(found) != len(records):
        raise ValueError("execution_open_batch_incomplete")
    return found if latest <= cutoff else {}


def _execution_session(daily, census, calendar, target: date) -> tuple[dict[str, float | None], set[str]]:
    prices: dict[str, float | None] = {}
    published: set[str] = set()

    def observe(symbol, opening, volume):
        if _number(volume) is not None and volume > 0 and _number(opening) is not None and opening > 0:
            prices[symbol] = opening
        elif volume == 0:
            prices[symbol] = None

    bars = _official_bars(daily, target, target)
    if not bars.is_empty():
        for symbol, opening, volume, source in bars.select("symbol", "open", "volume", "source").iter_rows():
            if source == "twse_tpex_official_snapshot":
                published.add(symbol.rsplit(".", 1)[-1])
            observe(symbol, opening, volume)
    if census is not None:
        for market, source in (("TWSE", TWSE_SOURCE), ("TPEX", TPEX_SOURCE)):
            if not census.has(market, target):
                continue
            evidence = census.day_evidence(market, target, calendar=calendar)
            if evidence.status == "non_trading":
                if market in published:
                    raise ValueError("execution_open_market_status_conflict")
                published.add(market)
            elif evidence.status == "trading":
                columns = ["date", "exchange", "source", "observed", "raw_code", "open", "volume"]
                rows = pl.read_parquet(census.partition_path(market, target), columns=columns, hive_partitioning=False)
                valid = ((pl.col("date") == target) & (pl.col("exchange") == market)
                         & (pl.col("source") == source) & pl.col("observed") & pl.col("raw_code").is_not_null())
                if rows.is_empty() or not rows.select(valid.fill_null(False).all()).item():
                    raise ValueError("execution_open_census_provenance_mismatch")
                published.add(market)
                for code, opening, volume in rows.select("raw_code", "open", "volume").iter_rows():
                    symbol = f"{code}.{market}"
                    if symbol not in prices:
                        observe(symbol, opening, volume)
    return prices, published


def record_execution_opens(root: Path, daily: TaiwanDailyStore, calendar: TaiwanTradingCalendar,
                           batches: list[SelectionSnapshot], census: ObservedUniverseStore | None = None) -> None:
    """Write separate, immutable observations linked to the original decision hashes."""
    if not root.is_dir():
        return
    latest = daily.latest_date()
    if latest is None:
        return
    bindings = {(date.fromisoformat(b.as_of_date).strftime("%G-W%V"), b.strategy_id): b for b in batches
                if b.record_type == "forward_batch" and b.weekly_records_sha256 and not b.weekly_capture_error}
    sessions = {}
    partial = set(daily.partial_dates())
    for path in root.glob("*/*.jsonl"):
        batch = bindings.get((path.parent.name, path.stem))
        if batch is None:
            continue
        companion = path.with_suffix(".opens.jsonl")
        if companion.exists():
            continue
        try:
            if file_sha256(path) != batch.weekly_records_sha256:
                raise ValueError("weekly_batch_digest_mismatch")
            records = read_records(path)
            target = bound_batch_next_session(records, batch, calendar, census)
        except (KeyError, ValueError) as exc:
            logger.warning("Skipping execution opens for %s: %s", path.name, exc)
            continue
        if latest < target or taipei_now() < market_close(target) or target in partial:
            continue
        if target not in sessions:
            try:
                sessions[target] = _execution_session(daily, census, calendar, target)
            except (OSError, ValueError, pl.exceptions.PolarsError) as exc:
                logger.warning("Execution opens unavailable for %s: %s", target, exc)
                sessions[target] = ({}, set())
        prices, published = sessions[target]
        # A missing symbol only becomes unavailable once its market published.
        # Other markets' data cannot finalize a one-sided partition.
        if any(r["symbol"] not in prices and r["market"] not in published for r in records):
            continue
        observed = taipei_now()
        events = []
        for row in records:
            price = prices.get(row["symbol"])
            observation = {"record_sha256": row["record_sha256"], "session": target, "symbol": row["symbol"],
                           "executable_reference_open": price, "observed_at": observed,
                           "status": "available" if price is not None else "official_open_unavailable"}
            observation["observation_sha256"] = canonical_hash(observation)
            events.append(canonical_json(observation))
        try:
            _publish(companion, events)
        except FileExistsError:
            continue

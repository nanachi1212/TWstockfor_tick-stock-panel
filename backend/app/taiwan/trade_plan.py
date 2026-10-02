"""Deterministic Taiwan trade plans and daily-bar forward outcomes.

Plans are immutable, gross-of-cost research records.  They use the existing
Taiwan tick model and PIT corporate-action normalizer; this module neither
places orders nor persists outcomes.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Literal

import polars as pl
from pydantic import BaseModel, ConfigDict

from app.taiwan.adjust import PROVENANCE_COLUMNS, adjust_prices_as_of
from app.taiwan.buy_point import BuyPointSignal
from app.taiwan.corporate_actions import CorporateActionEvent, CorporateActionStore
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.market_rules import TickSizeClass, TickSizeModel
from app.taiwan.quant.live_contract import canonical_hash
from app.taiwan.realtime.calendar import TaiwanTradingCalendar

TRADE_PLAN_VERSION = "trade_plan_v1"
PRICE_ADJUSTMENT_SEMANTICS = (
    "pit_price_normalized_cash_and_share_actions_not_total_return"
)

InstrumentType = Literal["stock", "etf"]
EntrySemantics = Literal["pullback_limit", "breakout_stop"]
FillSemantics = Literal[
    "first_low_lte_limit_fill_min_open_limit",
    "first_high_gte_trigger_fill_max_open_trigger",
]
OutcomeStatus = Literal[
    "triggered", "not_triggered", "immature", "data_insufficient", "undeterminable"
]


class TradePlan(BaseModel):
    """Frozen strategy definition plus symbol-specific executable levels."""

    model_config = ConfigDict(frozen=True)

    rule_version: Literal["trade_plan_v1"] = TRADE_PLAN_VERSION
    strategy_id: str
    symbol: str
    evidence_as_of: date
    instrument_type: InstrumentType
    price_adjustment_semantics: str = PRICE_ADJUSTMENT_SEMANTICS
    cost_assumption: Literal["gross"] = "gross"

    entry_semantics: EntrySemantics
    fill_semantics: FillSemantics
    trigger_price: float
    planned_entry_price: float
    reference_price: float
    reference_high: float | None = None
    entry_zone_low: float | None = None
    entry_zone_high: float | None = None
    breakout_trigger: float | None = None

    stop_method: str
    stop_lookback: int
    stop_price: float
    stop_trigger_semantics: Literal["daily_low_lte_stop_gap_open_else_stop"] = (
        "daily_low_lte_stop_gap_open_else_stop"
    )
    reward_risk_ratio: float
    target_price: float
    target_trigger_semantics: Literal["daily_high_gte_target_gap_open_else_target"] = (
        "daily_high_gte_target_gap_open_else_target"
    )
    entry_window_days: int
    max_holding_days: int

    plan_identity: str
    plan_instance_id: str


class TradePlanOutcome(BaseModel):
    """One deterministic evaluation result; failures remain explicit samples."""

    model_config = ConfigDict(frozen=True)

    plan_identity: str
    plan_instance_id: str
    symbol: str
    evidence_as_of: date
    evaluated_as_of: date
    status: OutcomeStatus
    reason: str | None = None
    price_adjustment_semantics: str
    cost_assumption: Literal["gross"] = "gross"
    entry_session: date | None = None
    entry_fill_price: float | None = None
    entry_at_open: bool | None = None
    exit_session: date | None = None
    exit_fill_price: float | None = None
    exit_reason: Literal["stop", "target", "max_holding"] | None = None
    gross_return_pct: float | None = None


def _tick_class(instrument_type: InstrumentType) -> TickSizeClass:
    return TickSizeClass.ETF if instrument_type == "etf" else TickSizeClass.ORDINARY_STOCK


def _plan_price(value: float, instrument_type: InstrumentType, trade_date: date) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError("plan prices must be finite numbers")
    if value <= 0:
        raise ValueError("plan prices must be positive")
    return TickSizeModel.round_order_price(
        float(value), tick_class=_tick_class(instrument_type), trade_date=trade_date
    )


def build_trade_plan(
    signal: BuyPointSignal,
    *,
    instrument_type: InstrumentType,
    stop_reference_price: float,
    entry_semantics: EntrySemantics | None = None,
    stop_method: str = "lookback_low",
    stop_lookback: int = 10,
    reward_risk_ratio: float = 2.0,
    entry_window_days: int = 5,
    max_holding_days: int = 20,
    price_adjustment_semantics: str = PRICE_ADJUSTMENT_SEMANTICS,
) -> TradePlan:
    """Create a tick-valid plan from the price evidence frozen in a buy-point signal."""
    if signal.data_as_of is None:
        raise ValueError("trade plan requires evidence_as_of")
    try:
        evidence_as_of = date.fromisoformat(signal.data_as_of)
    except ValueError as exc:
        raise ValueError("evidence_as_of must be an ISO date") from exc
    if not signal.strategy_id or not signal.symbol or not stop_method:
        raise ValueError("strategy, symbol, and stop method are required")
    if stop_lookback <= 0 or entry_window_days <= 0 or max_holding_days <= 0:
        raise ValueError("plan windows must be positive integers")
    if not math.isfinite(reward_risk_ratio) or reward_risk_ratio <= 0:
        raise ValueError("reward_risk_ratio must be positive and finite")
    if not price_adjustment_semantics:
        raise ValueError("price adjustment semantics are required")

    available: list[EntrySemantics] = []
    if signal.entry_zone_low is not None and signal.entry_zone_high is not None:
        available.append("pullback_limit")
    if signal.breakout_trigger is not None:
        available.append("breakout_stop")
    if entry_semantics is None:
        if len(available) != 1:
            raise ValueError("entry_semantics is required when signal entry evidence is ambiguous")
        entry_semantics = available[0]
    if entry_semantics not in available:
        raise ValueError("signal does not contain evidence for requested entry semantics")
    if signal.price is None:
        raise ValueError("trade plan requires a reference price")

    reference_price = _plan_price(signal.price, instrument_type, evidence_as_of)
    reference_high = (
        _plan_price(signal.reference_high, instrument_type, evidence_as_of)
        if signal.reference_high is not None
        else None
    )
    entry_zone_low = (
        _plan_price(signal.entry_zone_low, instrument_type, evidence_as_of)
        if signal.entry_zone_low is not None
        else None
    )
    entry_zone_high = (
        _plan_price(signal.entry_zone_high, instrument_type, evidence_as_of)
        if signal.entry_zone_high is not None
        else None
    )
    if (
        entry_zone_low is not None
        and entry_zone_high is not None
        and entry_zone_low > entry_zone_high
    ):
        raise ValueError("entry zone low cannot exceed entry zone high")
    breakout_trigger = (
        _plan_price(signal.breakout_trigger, instrument_type, evidence_as_of)
        if signal.breakout_trigger is not None
        else None
    )
    if entry_semantics == "pullback_limit":
        assert entry_zone_high is not None
        trigger_price = planned_entry_price = entry_zone_high
        fill_semantics: FillSemantics = "first_low_lte_limit_fill_min_open_limit"
    else:
        assert breakout_trigger is not None
        trigger_price = planned_entry_price = breakout_trigger
        fill_semantics = "first_high_gte_trigger_fill_max_open_trigger"

    stop_price = _plan_price(stop_reference_price, instrument_type, evidence_as_of)
    if stop_price >= planned_entry_price:
        raise ValueError("stop price must be below planned entry price")
    target_price = _plan_price(
        planned_entry_price + (planned_entry_price - stop_price) * reward_risk_ratio,
        instrument_type,
        evidence_as_of,
    )
    if target_price <= planned_entry_price:
        raise ValueError("target price must be above planned entry price")
    definition = {
        "rule_version": TRADE_PLAN_VERSION,
        "strategy_id": signal.strategy_id,
        "entry_semantics": entry_semantics,
        "fill_semantics": fill_semantics,
        "stop_method": stop_method,
        "stop_lookback": stop_lookback,
        "reward_risk_ratio": reward_risk_ratio,
        "entry_window_days": entry_window_days,
        "max_holding_days": max_holding_days,
        "instrument_type": instrument_type,
        "price_adjustment_semantics": price_adjustment_semantics,
        "cost_assumption": "gross",
    }
    plan_identity = canonical_hash(definition)
    instance = {
        "plan_identity": plan_identity,
        "symbol": signal.symbol,
        "evidence_as_of": evidence_as_of,
        "reference_price": reference_price,
        "reference_high": reference_high,
        "entry_zone_low": entry_zone_low,
        "entry_zone_high": entry_zone_high,
        "breakout_trigger": breakout_trigger,
        "stop_price": stop_price,
        "target_price": target_price,
    }
    return TradePlan(
        **definition,
        symbol=signal.symbol,
        evidence_as_of=evidence_as_of,
        trigger_price=trigger_price,
        planned_entry_price=planned_entry_price,
        reference_price=reference_price,
        reference_high=reference_high,
        entry_zone_low=entry_zone_low,
        entry_zone_high=entry_zone_high,
        breakout_trigger=breakout_trigger,
        stop_price=stop_price,
        target_price=target_price,
        plan_identity=plan_identity,
        plan_instance_id=canonical_hash(instance),
    )


def _outcome(
    plan: TradePlan,
    evaluated_as_of: date,
    status: OutcomeStatus,
    reason: str | None = None,
    **values: object,
) -> TradePlanOutcome:
    return TradePlanOutcome(
        plan_identity=plan.plan_identity,
        plan_instance_id=plan.plan_instance_id,
        symbol=plan.symbol,
        evidence_as_of=plan.evidence_as_of,
        evaluated_as_of=evaluated_as_of,
        status=status,
        reason=reason,
        price_adjustment_semantics=plan.price_adjustment_semantics,
        **values,
    )


def _normalized_window(
    plan: TradePlan,
    bars: pl.DataFrame,
    sessions: Sequence[date],
    events: Sequence[CorporateActionEvent],
) -> tuple[pl.DataFrame, float] | None:
    required = {"symbol", "date", "open", "high", "low", "close"}
    columns = set(bars.columns)
    if not required <= columns or columns.intersection(PROVENANCE_COLUMNS):
        return None
    try:
        frame = bars.select(sorted(required)).with_columns(
            pl.col("date").cast(pl.Date, strict=True),
            *[pl.col(name).cast(pl.Float64, strict=True)
              for name in ("open", "high", "low", "close")],
        ).filter(
            (pl.col("symbol") == plan.symbol) & pl.col("date").is_in(list(sessions))
        )
    except (TypeError, ValueError, pl.exceptions.PolarsError):
        return None
    if frame.height != len(sessions) or frame["date"].n_unique() != len(sessions):
        return None
    frame = frame.sort("date")
    if frame["date"].to_list() != list(sessions):
        return None
    invalid = frame.select(
        pl.any_horizontal(
            [pl.col(name).is_null() | ~pl.col(name).is_finite() | (pl.col(name) <= 0)
             for name in ("open", "high", "low", "close")]
        ).any()
    ).item()
    if invalid:
        return None
    for row in frame.iter_rows(named=True):
        if not row["low"] <= min(row["open"], row["close"]) <= max(
            row["open"], row["close"]
        ) <= row["high"]:
            return None

    synthetic = pl.DataFrame({
        "symbol": [plan.symbol],
        "date": [plan.evidence_as_of],
        "open": [1.0],
        "high": [1.0],
        "low": [1.0],
        "close": [1.0],
    }).with_columns(pl.col("date").cast(pl.Date))
    adjusted = adjust_prices_as_of(
        pl.concat([synthetic, frame], how="diagonal_relaxed"),
        as_of=sessions[-1],
        events=events,
        price_columns=("open", "high", "low", "close"),
    )
    if adjusted.status != "verified":
        return None
    normalized = adjusted.to_frame().sort("date")
    factor = float(normalized.filter(pl.col("date") == plan.evidence_as_of)["close"][0])
    return normalized.filter(pl.col("date") != plan.evidence_as_of), factor


def _evaluate_normalized_prefix(
    plan: TradePlan,
    *,
    frame: pl.DataFrame,
    factor: float,
    sessions: list[date],
    evaluated_as_of: date,
) -> TradePlanOutcome:
    levels = {
        "trigger": plan.trigger_price * factor,
        "stop": plan.stop_price * factor,
        "target": plan.target_price * factor,
    }
    rows = frame.iter_rows(named=True)
    entry_index: int | None = None
    entry_fill: float | None = None
    entry_at_open: bool | None = None
    rows_list = list(rows)
    for index, row in enumerate(rows_list[:plan.entry_window_days]):
        if plan.entry_semantics == "pullback_limit" and row["low"] <= levels["trigger"]:
            entry_index = index
            entry_fill = min(float(row["open"]), levels["trigger"])
            entry_at_open = row["open"] <= levels["trigger"]
            break
        if plan.entry_semantics == "breakout_stop" and row["high"] >= levels["trigger"]:
            entry_index = index
            entry_fill = max(float(row["open"]), levels["trigger"])
            entry_at_open = row["open"] >= levels["trigger"]
            break
    if entry_index is None:
        if len(sessions) < plan.entry_window_days:
            return _outcome(plan, evaluated_as_of, "immature", "entry_window_not_mature")
        return _outcome(plan, evaluated_as_of, "not_triggered", "entry_window_elapsed")

    assert entry_fill is not None and entry_at_open is not None
    entry_day = sessions[entry_index]
    expiry_index = entry_index + plan.max_holding_days - 1
    for index in range(entry_index, min(expiry_index, len(rows_list) - 1) + 1):
        row = rows_list[index]
        stop_hit = row["low"] <= levels["stop"]
        target_hit = row["high"] >= levels["target"]
        if index == entry_index:
            ambiguous = (stop_hit and target_hit) if entry_at_open else (stop_hit or target_hit)
        else:
            ambiguous = stop_hit and target_hit
        if ambiguous:
            return _outcome(
                plan,
                evaluated_as_of,
                "undeterminable",
                "daily_bar_order_ambiguous",
                entry_session=entry_day,
                entry_fill_price=entry_fill,
                entry_at_open=entry_at_open,
            )
        exit_reason: Literal["stop", "target"] | None = None
        exit_fill: float | None = None
        if stop_hit:
            exit_reason = "stop"
            exit_fill = float(row["open"]) if row["open"] <= levels["stop"] else levels["stop"]
        elif target_hit:
            exit_reason = "target"
            exit_fill = (
                float(row["open"]) if row["open"] >= levels["target"] else levels["target"]
            )
        if exit_reason is not None and exit_fill is not None:
            return _outcome(
                plan,
                evaluated_as_of,
                "triggered",
                entry_session=entry_day,
                entry_fill_price=entry_fill,
                entry_at_open=entry_at_open,
                exit_session=sessions[index],
                exit_fill_price=exit_fill,
                exit_reason=exit_reason,
                gross_return_pct=(exit_fill / entry_fill - 1) * 100,
            )
    if expiry_index >= len(rows_list):
        return _outcome(
            plan,
            evaluated_as_of,
            "immature",
            "holding_window_not_mature",
            entry_session=entry_day,
            entry_fill_price=entry_fill,
            entry_at_open=entry_at_open,
        )
    expiry = rows_list[expiry_index]
    exit_fill = float(expiry["close"])
    return _outcome(
        plan,
        evaluated_as_of,
        "triggered",
        entry_session=entry_day,
        entry_fill_price=entry_fill,
        entry_at_open=entry_at_open,
        exit_session=sessions[expiry_index],
        exit_fill_price=exit_fill,
        exit_reason="max_holding",
        gross_return_pct=(exit_fill / entry_fill - 1) * 100,
    )


def evaluate_trade_plan(
    plan: TradePlan,
    *,
    bars: pl.DataFrame,
    sessions: Sequence[date],
    evaluated_as_of: date,
    company_actions: Sequence[CorporateActionEvent] = (),
    company_action_coverage_verified: bool = True,
    company_action_coverage_start: date | None = None,
    company_action_coverage_end: date | None = None,
) -> TradePlanOutcome:
    """Evaluate only the consecutive evidence prefix needed for a terminal outcome."""
    if evaluated_as_of < plan.evidence_as_of:
        raise ValueError("evaluated_as_of cannot precede evidence_as_of")
    ordered = list(sessions)
    if (
        ordered != sorted(set(ordered))
        or any(day <= plan.evidence_as_of or day > evaluated_as_of for day in ordered)
    ):
        raise ValueError("sessions must be unique, ordered, and after evidence_as_of")
    required_count = plan.entry_window_days + plan.max_holding_days - 1
    ordered = ordered[:required_count]
    if not ordered:
        return _outcome(plan, evaluated_as_of, "immature", "entry_window_not_mature")

    latest = _outcome(plan, evaluated_as_of, "immature", "entry_window_not_mature")
    for length in range(1, len(ordered) + 1):
        prefix = ordered[:length]
        current = prefix[-1]
        coverage_missing = (
            not company_action_coverage_verified
            or (
                company_action_coverage_start is not None
                and company_action_coverage_start > plan.evidence_as_of
            )
            or (
                company_action_coverage_end is not None
                and company_action_coverage_end < current
            )
        )
        if coverage_missing:
            return _outcome(
                plan, evaluated_as_of, "data_insufficient", "corporate_action_coverage"
            )
        normalized = _normalized_window(plan, bars, prefix, company_actions)
        if normalized is None:
            return _outcome(
                plan, evaluated_as_of, "data_insufficient", "daily_or_adjustment_data"
            )
        frame, factor = normalized
        latest = _evaluate_normalized_prefix(
            plan,
            frame=frame,
            factor=factor,
            sessions=prefix,
            evaluated_as_of=evaluated_as_of,
        )
        if latest.status != "immature":
            return latest
    return latest


class TradePlanEvaluator:
    """Read-only adapter over the existing daily, calendar, and action stores."""

    def __init__(
        self,
        *,
        daily_store: TaiwanDailyStore,
        calendar: TaiwanTradingCalendar,
        action_store: CorporateActionStore,
    ) -> None:
        self.daily_store = daily_store
        self.calendar = calendar
        self.action_store = action_store

    def evaluate(self, plan: TradePlan, *, evaluated_as_of: date) -> TradePlanOutcome:
        available = set(self.daily_store.available_dates())
        exchange = plan.symbol.rsplit(".", 1)[-1]
        if exchange not in {"TWSE", "TPEX"}:
            return _outcome(
                plan, evaluated_as_of, "data_insufficient", "canonical_symbol_required"
            )
        observed: dict[date, bool] = {}

        def has_exchange_observation(day: date) -> bool:
            if day not in observed:
                if day not in available:
                    observed[day] = False
                else:
                    frame = self.daily_store.read_range(None, day, day)
                    observed[day] = bool(
                        not frame.is_empty()
                        and frame.filter(
                            pl.col("symbol").str.ends_with(f".{exchange}")
                            & pl.col("close").is_not_null()
                            & pl.col("close").is_finite()
                            & (pl.col("close") > 0)
                        ).height
                    )
            return observed[day]

        sessions: list[date] = []
        cursor = plan.evidence_as_of + timedelta(days=1)
        unresolved = False
        required_count = plan.entry_window_days + plan.max_holding_days - 1
        while cursor <= evaluated_as_of and len(sessions) < required_count:
            evidence = self.calendar.day_evidence(cursor, exchange)
            if evidence.status == "trading" or (
                has_exchange_observation(cursor)
                and (
                    evidence.status == "unresolved"
                    or evidence.evidence_source == "calendar_rule"
                )
            ):
                sessions.append(cursor)
            elif evidence.status == "unresolved":
                unresolved = True
                break
            cursor += timedelta(days=1)
        bars = self.daily_store.read_range(
            [plan.symbol], sessions[0] if sessions else None, sessions[-1] if sessions else None
        )
        coverage = self.action_store.read_verified_coverage() if sessions else None
        coverage_start, coverage_end, events = (
            coverage if coverage is not None else (None, None, ())
        )
        outcome = evaluate_trade_plan(
            plan,
            bars=bars,
            sessions=sessions,
            evaluated_as_of=evaluated_as_of,
            company_actions=events,
            company_action_coverage_verified=coverage is not None or not sessions,
            company_action_coverage_start=coverage_start,
            company_action_coverage_end=coverage_end,
        )
        if outcome.status != "immature" or not unresolved:
            return outcome
        return _outcome(
            plan, evaluated_as_of, "data_insufficient", "trading_session_evidence"
        )

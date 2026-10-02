"""Forward-only tracking for frozen Taiwan AI Advice artifacts.

This module owns admission, deterministic TradePlan outcomes, descriptive
statistics, and bounded reflections.  It never reconstructs historical AI
signals and never reads Selection Review returns.
"""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from app.taiwan.ai_research import _evidence_digest
from app.taiwan.ai_research_history import (
    AIResearchHistoryError,
    TaiwanAIResearchHistoryStore,
    frozen_record_digest,
    get_ai_research_history_store,
)
from app.taiwan.corporate_actions import CorporateActionStore
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.observed_universe import ObservedUniverseStore
from app.taiwan.providers.taiwan_values import market_close
from app.taiwan.quant.live_contract import canonical_hash
from app.taiwan.realtime.calendar import TaiwanTradingCalendar, taipei_now
from app.taiwan.trade_plan import TradePlan, TradePlanEvaluator, TradePlanOutcome

AdmissionStatus = Literal["admitted", "unavailable", "not_applicable"]
AdviceView = Literal["raw", "reviewed"]
TERMINAL_OUTCOMES = {"triggered", "not_triggered", "undeterminable"}
EVALUATOR_VERSION = "trade_plan_evaluator_v1"


class OutcomeEvaluator(Protocol):
    def evaluate(self, plan: TradePlan, *, evaluated_as_of: date) -> TradePlanOutcome: ...


class ForwardAdmission(BaseModel):
    advice_id: str
    status: AdmissionStatus
    view: AdviceView
    reasons: list[str] = Field(default_factory=list)
    effective_completed_at: str | None = None
    forward_cutoff: str | None = None


class AdviceStatistics(BaseModel):
    plan_identity: str
    view: AdviceView
    evidence_label: str = "Forward / OOS deterministic gross TradePlan outcomes"
    return_semantics: str = "gross; deterministic daily-bar plan evaluation; not actual fills"
    minimum_sample: int
    admitted_plan_instances: int
    outcome_status_counts: dict[str, int]
    hit_count: int
    win_rate_denominator: int
    win_rate_pct: float | None
    average_return_pct: float | None
    sample_sufficient: bool
    duplicate_plan_instances: int = 0
    integrity_conflicts: int = 0


class ReflectionCreate(BaseModel):
    outcome_id: str
    view: AdviceView = "raw"
    summary: str = Field(min_length=1, max_length=2000)
    lessons: list[str] = Field(default_factory=list, max_length=20)


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.utcoffset() is not None else None


def _valid_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _plan_hashes(plan: TradePlan) -> tuple[str, str]:
    definition = {
        "rule_version": plan.rule_version,
        "strategy_id": plan.strategy_id,
        "entry_semantics": plan.entry_semantics,
        "fill_semantics": plan.fill_semantics,
        "stop_method": plan.stop_method,
        "stop_lookback": plan.stop_lookback,
        "reward_risk_ratio": plan.reward_risk_ratio,
        "entry_window_days": plan.entry_window_days,
        "max_holding_days": plan.max_holding_days,
        "instrument_type": plan.instrument_type,
        "price_adjustment_semantics": plan.price_adjustment_semantics,
        "cost_assumption": plan.cost_assumption,
    }
    identity = canonical_hash(definition)
    instance = {
        "plan_identity": identity,
        "symbol": plan.symbol,
        "evidence_as_of": plan.evidence_as_of,
        "reference_price": plan.reference_price,
        "reference_high": plan.reference_high,
        "entry_zone_low": plan.entry_zone_low,
        "entry_zone_high": plan.entry_zone_high,
        "breakout_trigger": plan.breakout_trigger,
        "stop_price": plan.stop_price,
        "target_price": plan.target_price,
    }
    return identity, canonical_hash(instance)


def _record_by_id(records: list[dict[str, Any]], record_id: str) -> dict[str, Any] | None:
    return next((item for item in records if item.get("id") == record_id), None)


class AdviceTrackingService:
    def __init__(
        self,
        *,
        history: TaiwanAIResearchHistoryStore | None = None,
        calendar: TaiwanTradingCalendar | None = None,
        census: ObservedUniverseStore | None = None,
        evaluator: OutcomeEvaluator | None = None,
    ) -> None:
        self.history = history or get_ai_research_history_store()
        self.calendar = calendar or TaiwanTradingCalendar()
        self.census = census or ObservedUniverseStore()
        self.evaluator = evaluator or TradePlanEvaluator(
            daily_store=TaiwanDailyStore(),
            calendar=self.calendar,
            action_store=CorporateActionStore(),
        )

    def _forward_status(
        self,
        advice: dict[str, Any],
        *,
        view: AdviceView,
        records: list[dict[str, Any]],
    ) -> ForwardAdmission:
        advice_id = str(advice.get("id") or "")
        cutoff = _aware(advice.get("forward_cutoff"))
        completed = _aware(advice.get("completed_at"))
        effective = completed
        response = advice.get("response")
        artifact = response.get("advice") if isinstance(response, dict) else None
        selected = advice.get("selected_plan_instance_id")
        artifact_selected = (
            artifact.get("selected_trade_plan", {}).get("plan_instance_id")
            if isinstance(artifact, dict) and isinstance(artifact.get("selected_trade_plan"), dict)
            else None
        )
        if (
            isinstance(artifact, dict)
            and artifact.get("action") != "buy"
            and not selected
            and not artifact_selected
        ):
            return ForwardAdmission(
                advice_id=advice_id,
                status="not_applicable",
                view=view,
                reasons=["no_selected_trade_plan"],
                effective_completed_at=completed.isoformat() if completed else None,
                forward_cutoff=cutoff.isoformat() if cutoff else None,
            )

        reasons: list[str] = []
        if not isinstance(artifact, dict):
            reasons.append("invalid_advice_payload")
        plan: TradePlan | None = None
        try:
            plan = TradePlan.model_validate(advice.get("trade_plan"))
        except Exception:
            reasons.append("invalid_trade_plan")
        if plan is not None:
            identity, instance = _plan_hashes(plan)
            if identity != plan.plan_identity or identity != advice.get("plan_identity"):
                reasons.append("plan_identity_mismatch")
            if instance != plan.plan_instance_id or instance != advice.get("plan_instance_id"):
                reasons.append("plan_instance_id_mismatch")
            if selected != instance or artifact_selected != instance:
                reasons.append("selected_plan_instance_id_mismatch")
            if plan.symbol != advice.get("symbol"):
                reasons.append("symbol_mismatch")
            if plan.strategy_id != advice.get("strategy_id"):
                reasons.append("strategy_id_mismatch")
            if plan.evidence_as_of.isoformat() != advice.get("evidence_as_of"):
                reasons.append("evidence_as_of_mismatch")
            if isinstance(artifact, dict):
                if artifact.get("symbol") != plan.symbol:
                    reasons.append("response_symbol_mismatch")
                if artifact.get("strategy_id") != plan.strategy_id:
                    reasons.append("response_strategy_mismatch")
                if artifact.get("evidence_as_of") != plan.evidence_as_of.isoformat():
                    reasons.append("response_evidence_as_of_mismatch")

        evidence = advice.get("evidence_payload")
        digest = advice.get("evidence_digest")
        if not isinstance(evidence, dict) or _evidence_digest(evidence) != digest:
            reasons.append("evidence_digest_mismatch")
        if advice.get("source_snapshot_digest") != digest:
            reasons.append("source_snapshot_digest_mismatch")
        expected_snapshot = (
            f"taiwan_research:{advice.get('symbol')}:{advice.get('evidence_as_of')}:{str(digest)[:16]}"
        )
        if advice.get("source_snapshot_id") != expected_snapshot:
            reasons.append("source_snapshot_id_mismatch")
        definition_digest = advice.get("strategy_definition_digest")
        definition = advice.get("strategy_definition")
        if (
            not _valid_digest(definition_digest)
            or not isinstance(definition, dict)
            or canonical_hash(definition) != definition_digest
        ):
            reasons.append("invalid_strategy_definition_digest")
        elif plan is not None and definition.get("id") != plan.strategy_id:
            reasons.append("strategy_definition_id_mismatch")
        provenance = advice.get("evidence_admission_provenance")
        if not isinstance(provenance, dict):
            reasons.append("missing_evidence_admission_provenance")
        else:
            if provenance.get("market_evidence_as_of") != advice.get("evidence_as_of"):
                reasons.append("market_evidence_as_of_mismatch")
            if provenance.get("buy_point_evidence_as_of") != advice.get("evidence_as_of"):
                reasons.append("buy_point_evidence_as_of_mismatch")
            if provenance.get("candidate_plan_source") != "server":
                reasons.append("untrusted_candidate_plan_source")

        evidence_cutoff = _aware(advice.get("evidence_cutoff"))
        started = _aware(advice.get("started_at"))
        if None in {evidence_cutoff, started, completed, cutoff}:
            reasons.append("missing_or_naive_timestamp")
        elif not (evidence_cutoff <= started <= completed < cutoff):
            reasons.append("invalid_forward_time_order")

        if view == "reviewed":
            reviews = [
                item for item in records
                if item.get("kind") == "review" and item.get("parent_id") == advice_id
            ]
            if not reviews:
                reasons.append("review_missing")
            else:
                parsed_reviews = [
                    (_aware(item.get("started_at")), _aware(item.get("completed_at")), item)
                    for item in reviews
                ]
                if any(start is None or completed_at is None for start, completed_at, _ in parsed_reviews):
                    reasons.append("review_timestamp_invalid")
                else:
                    _review_started, review_completed, review = max(
                        parsed_reviews, key=lambda values: values[1]
                    )
                    assert review_completed is not None
                    assert _review_started is not None
                    if _review_started > review_completed:
                        reasons.append("review_time_order_invalid")
                    effective = max(completed, review_completed) if completed else review_completed
                    payload = review.get("review")
                    issues = payload.get("issues") if isinstance(payload, dict) else None
                    if not isinstance(issues, list):
                        reasons.append("review_payload_invalid")
                    elif any(
                        isinstance(issue, dict) and issue.get("kind") == "blocking"
                        for issue in issues
                    ):
                        reasons.append("review_blocking")
                    if review.get("advice_run_id") != advice.get("run_id"):
                        reasons.append("review_parent_mismatch")
                    review_material = {
                        "advice_run_id": review.get("advice_run_id"),
                        "started_at": review.get("started_at"),
                        "completed_at": review.get("completed_at"),
                        "review": payload,
                    }
                    if review.get("advice_digest") != frozen_record_digest(advice):
                        reasons.append("review_advice_digest_mismatch")
                    if review.get("review_digest") != frozen_record_digest(review_material):
                        reasons.append("review_digest_mismatch")
                    if cutoff is not None and effective >= cutoff:
                        reasons.append("review_completed_after_forward_cutoff")

        if cutoff is not None:
            symbol = advice.get("symbol")
            exchange = symbol.rsplit(".", 1)[-1] if isinstance(symbol, str) else ""
            if exchange not in {"TWSE", "TPEX"}:
                reasons.append("unsupported_exchange")
            else:
                day = self.census.day_evidence(exchange, cutoff.date(), calendar=self.calendar)
                if day.status == "unresolved":
                    reasons.append("forward_session_unresolved")
                elif day.status == "non_trading":
                    reasons.append("forward_cutoff_not_trading")

        return ForwardAdmission(
            advice_id=advice_id,
            status="unavailable" if reasons else "admitted",
            view=view,
            reasons=list(dict.fromkeys(reasons)),
            effective_completed_at=effective.isoformat() if effective else None,
            forward_cutoff=cutoff.isoformat() if cutoff else None,
        )

    def forward_status(self, advice_id: str, *, view: AdviceView = "raw") -> ForwardAdmission:
        records = self.history.all_records()
        advice = _record_by_id(records, advice_id)
        if advice is None or advice.get("kind") != "advice":
            raise KeyError(advice_id)
        return self._forward_status(advice, view=view, records=records)

    def evaluate_outcome(
        self,
        advice_id: str,
        *,
        evaluated_as_of: date,
        view: AdviceView = "raw",
    ) -> dict[str, Any]:
        records = self.history.all_records()
        advice = _record_by_id(records, advice_id)
        if advice is None or advice.get("kind") != "advice":
            raise KeyError(advice_id)
        admission = self._forward_status(advice, view=view, records=records)
        if admission.status != "admitted":
            raise AIResearchHistoryError(
                "Advice is not forward-admitted: " + ",".join(admission.reasons)
            )
        plan = TradePlan.model_validate(advice["trade_plan"])
        outcome = self.evaluator.evaluate(plan, evaluated_as_of=evaluated_as_of)
        evaluated_at = taipei_now()
        matured_at = (
            market_close(outcome.terminal_session)
            if outcome.status in TERMINAL_OUTCOMES and outcome.terminal_session is not None
            else None
        )
        outcome_payload = outcome.model_dump(mode="json")
        terminal_payload = dict(outcome_payload)
        terminal_payload.pop("evaluated_as_of", None)
        evaluator_digest = canonical_hash(
            {
                "version": EVALUATOR_VERSION,
                "price_adjustment_semantics": plan.price_adjustment_semantics,
                "cost_assumption": plan.cost_assumption,
            }
        )
        input_digest = canonical_hash(
            {
                "plan": plan.model_dump(mode="json"),
                "evaluated_as_of": evaluated_as_of,
                "view": view,
                "evaluator_digest": evaluator_digest,
            }
        )
        artifact = {
            "view": view,
            "evaluated_at": evaluated_at.isoformat(),
            "completed_at": evaluated_at.isoformat(),
            "evaluated_as_of": evaluated_as_of.isoformat(),
            "outcome_matured_at": matured_at.isoformat() if matured_at else None,
            "evaluator_digest": evaluator_digest,
            "input_digest": input_digest,
            "outcome_digest": canonical_hash(outcome_payload),
            "terminal_digest": (
                canonical_hash(terminal_payload)
                if outcome.status in TERMINAL_OUTCOMES
                else None
            ),
            "outcome": outcome_payload,
        }
        return self.history.ensure_outcome(advice_id, artifact)

    def statistics(
        self,
        plan_identity: str,
        *,
        view: AdviceView = "raw",
        minimum_sample: int = 5,
    ) -> AdviceStatistics:
        if not 5 <= minimum_sample <= 1000:
            raise ValueError("minimum_sample must be between 5 and 1000")
        records = self.history.all_records()
        advice_rows = [
            item for item in records
            if item.get("kind") == "advice" and item.get("plan_identity") == plan_identity
        ]
        grouped: dict[str, list[tuple[dict[str, Any], dict[str, Any] | None]]] = {}
        for advice in advice_rows:
            admission = self._forward_status(advice, view=view, records=records)
            if admission.status != "admitted":
                continue
            instance_id = advice.get("plan_instance_id")
            if not isinstance(instance_id, str):
                continue
            outcomes = [
                item for item in records
                if item.get("kind") == "outcome"
                and item.get("parent_id") == advice.get("id")
                and item.get("view", "raw") == view
            ]
            terminal = [
                item for item in outcomes
                if isinstance(item.get("outcome"), dict)
                and item["outcome"].get("status") in TERMINAL_OUTCOMES
            ]
            candidates = terminal or outcomes
            selected = max(
                candidates,
                key=lambda item: (str(item.get("evaluated_at") or ""), str(item.get("id") or "")),
                default=None,
            )
            grouped.setdefault(instance_id, []).append((advice, selected))

        counts = {name: 0 for name in (
            "triggered", "not_triggered", "immature", "data_insufficient", "undeterminable",
            "not_evaluated",
        )}
        returns: list[float] = []
        conflicts = 0
        duplicates = 0
        admitted = 0
        for rows in grouped.values():
            duplicates += max(0, len(rows) - 1)
            digests = {
                item.get("terminal_digest") or item.get("outcome_digest")
                for _advice, item in rows
                if item is not None
            }
            if len(digests) > 1:
                conflicts += 1
                continue
            admitted += 1
            selected = rows[0][1]
            if selected is None:
                counts["not_evaluated"] += 1
                continue
            outcome = selected.get("outcome")
            status = outcome.get("status") if isinstance(outcome, dict) else None
            if status not in counts:
                conflicts += 1
                continue
            counts[status] += 1
            value = outcome.get("gross_return_pct") if status == "triggered" else None
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
            ):
                returns.append(float(value))

        denominator = len(returns)
        hits = sum(value > 0 for value in returns)
        sufficient = denominator >= minimum_sample
        return AdviceStatistics(
            plan_identity=plan_identity,
            view=view,
            minimum_sample=minimum_sample,
            admitted_plan_instances=admitted,
            outcome_status_counts=counts,
            hit_count=hits,
            win_rate_denominator=denominator,
            win_rate_pct=(100 * hits / denominator) if sufficient else None,
            average_return_pct=(sum(returns) / denominator) if sufficient else None,
            sample_sufficient=sufficient,
            duplicate_plan_instances=duplicates,
            integrity_conflicts=conflicts,
        )

    def create_reflection(
        self,
        advice_id: str,
        payload: ReflectionCreate,
    ) -> dict[str, Any]:
        records = self.history.all_records()
        advice = _record_by_id(records, advice_id)
        outcome = _record_by_id(records, payload.outcome_id)
        if advice is None or advice.get("kind") != "advice":
            raise KeyError(advice_id)
        if (
            outcome is None
            or outcome.get("kind") != "outcome"
            or outcome.get("parent_id") != advice_id
            or outcome.get("view", "raw") != payload.view
        ):
            raise AIResearchHistoryError("Reflection outcome does not belong to Advice")
        admission = self._forward_status(advice, view=payload.view, records=records)
        if admission.status != "admitted":
            raise AIResearchHistoryError("Reflection source Advice is not forward-admitted")
        result = outcome.get("outcome")
        matured_at = _aware(outcome.get("outcome_matured_at"))
        outcome_completed = _aware(outcome.get("completed_at"))
        if (
            not isinstance(result, dict)
            or result.get("status") not in TERMINAL_OUTCOMES
            or matured_at is None
            or outcome_completed is None
        ):
            raise AIResearchHistoryError("Reflection requires a terminal mature outcome")
        completed = taipei_now()
        if outcome_completed < matured_at or completed < matured_at:
            raise AIResearchHistoryError("Reflection outcome has not reached its maturity time")
        summary = payload.summary.strip()
        lessons = [item.strip() for item in payload.lessons if item.strip()]
        material = {
            "advice_id": advice_id,
            "outcome_id": payload.outcome_id,
            "view": payload.view,
            "summary": summary,
            "lessons": lessons,
            "plan_identity": advice.get("plan_identity"),
            "plan_instance_id": advice.get("plan_instance_id"),
            "advice_digest": outcome.get("advice_digest"),
            "outcome_digest": outcome.get("outcome_digest"),
        }
        artifact = {
            **material,
            "reflection_digest": canonical_hash(material),
            "completed_at": completed.isoformat(),
        }
        return self.history.ensure_reflection(advice_id, payload.outcome_id, artifact)

    def select_reflections(
        self,
        plan_identity: str,
        *,
        decision_cutoff: str,
        view: AdviceView = "raw",
        limit: int = 2,
    ) -> list[dict[str, Any]]:
        cutoff = _aware(decision_cutoff)
        if cutoff is None or limit <= 0:
            return []
        records = self.history.all_records()
        selected: list[dict[str, Any]] = []
        for reflection in records:
            if reflection.get("kind") != "reflection" or reflection.get("plan_identity") != plan_identity:
                continue
            if reflection.get("view", "raw") != view:
                continue
            advice = _record_by_id(records, str(reflection.get("parent_id") or ""))
            outcome = _record_by_id(records, str(reflection.get("outcome_id") or ""))
            if advice is None or outcome is None:
                continue
            admission = self._forward_status(advice, view=view, records=records)
            reflection_completed = _aware(reflection.get("completed_at"))
            outcome_completed = _aware(outcome.get("completed_at"))
            matured = _aware(outcome.get("outcome_matured_at"))
            if (
                admission.status != "admitted"
                or advice.get("plan_identity") != plan_identity
                or reflection_completed is None
                or outcome_completed is None
                or matured is None
                or not (reflection_completed < cutoff and outcome_completed < cutoff and matured < cutoff)
            ):
                continue
            selected.append(reflection)
        selected.sort(
            key=lambda item: (str(item.get("completed_at") or ""), str(item.get("id") or "")),
            reverse=True,
        )
        return selected[: min(limit, 2)]


def get_advice_tracking_service() -> AdviceTrackingService:
    return AdviceTrackingService()

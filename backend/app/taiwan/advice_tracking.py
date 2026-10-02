"""Forward-only tracking for frozen Taiwan AI Advice artifacts.

This module owns admission, deterministic TradePlan outcomes, descriptive
statistics, and bounded reflections.  It never reconstructs historical AI
signals and never reads Selection Review returns.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
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
from app.taiwan.trade_plan import (
    TradePlan,
    TradePlanEvaluation,
    TradePlanEvaluator,
    TradePlanOutcome,
    canonical_trade_plan_hashes,
)

AdmissionStatus = Literal["admitted", "unavailable", "not_applicable"]
AdviceView = Literal["raw", "reviewed"]
TERMINAL_OUTCOMES = {"triggered", "not_triggered", "undeterminable"}
EVALUATOR_VERSION = "trade_plan_evaluator_v1"


class OutcomeEvaluator(Protocol):
    def evaluate_with_provenance(
        self, plan: TradePlan, *, evaluated_as_of: date
    ) -> TradePlanEvaluation: ...


@dataclass(frozen=True)
class PersistedOutcomeValidation:
    outcome: TradePlanOutcome | None
    reasons: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return self.outcome is not None and not self.reasons


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

    def _validated_review(
        self,
        advice: dict[str, Any],
        *,
        records: list[dict[str, Any]],
        review_id: str | None = None,
    ) -> tuple[dict[str, Any] | None, datetime | None, list[str]]:
        advice_id = advice.get("id")
        reviews = [
            item for item in records
            if item.get("kind") == "review" and item.get("parent_id") == advice_id
        ]
        reasons: list[str] = []
        if review_id is None:
            if not reviews:
                return None, None, ["review_missing"]
            if len(reviews) != 1:
                return None, None, ["multiple_review_records"]
            review = reviews[0]
        else:
            matched = [item for item in reviews if item.get("id") == review_id]
            if len(matched) != 1:
                return None, None, ["supporting_review_missing"]
            review = matched[0]

        advice_completed = _aware(advice.get("completed_at"))
        cutoff = _aware(advice.get("forward_cutoff"))
        review_started = _aware(review.get("started_at"))
        review_completed = _aware(review.get("completed_at"))
        if None in {advice_completed, cutoff, review_started, review_completed}:
            reasons.append("review_timestamp_invalid")
        else:
            if not (advice_completed <= review_started <= review_completed):
                reasons.append("review_time_order_invalid")
            if review_completed >= cutoff:
                reasons.append("review_completed_after_forward_cutoff")

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
        return review, review_completed, list(dict.fromkeys(reasons))

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
            identity, instance = canonical_trade_plan_hashes(plan)
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
                response_plan = artifact.get("selected_trade_plan")
                try:
                    parsed_response_plan = TradePlan.model_validate(response_plan)
                except Exception:
                    reasons.append("invalid_response_trade_plan")
                else:
                    response_identity, response_instance = canonical_trade_plan_hashes(
                        parsed_response_plan
                    )
                    if (
                        response_identity != parsed_response_plan.plan_identity
                        or response_instance != parsed_response_plan.plan_instance_id
                    ):
                        reasons.append("response_trade_plan_id_mismatch")
                    if parsed_response_plan.model_dump(mode="json") != plan.model_dump(mode="json"):
                        reasons.append("response_trade_plan_mismatch")

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
            _review, review_completed, review_reasons = self._validated_review(
                advice, records=records
            )
            reasons.extend(review_reasons)
            if review_completed is not None:
                effective = max(completed, review_completed) if completed else review_completed

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
        evaluation = self.evaluator.evaluate_with_provenance(
            plan, evaluated_as_of=evaluated_as_of
        )
        outcome = evaluation.outcome
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
        supporting_review_id = None
        supporting_review_digest = None
        effective_completed_at = admission.effective_completed_at
        if view == "reviewed":
            review, _review_completed, review_reasons = self._validated_review(
                advice, records=records
            )
            if review is None or review_reasons:
                raise AIResearchHistoryError(
                    "Reviewed Advice support is invalid: " + ",".join(review_reasons)
                )
            supporting_review_id = review["id"]
            supporting_review_digest = review["review_digest"]
        input_digest = canonical_hash(
            {
                "plan": plan.model_dump(mode="json"),
                "evaluated_as_of": evaluated_as_of,
                "view": view,
                "evaluator_digest": evaluator_digest,
                "daily_bars_digest": evaluation.daily_bars_digest,
                "session_evidence_digest": evaluation.session_evidence_digest,
                "company_action_digest": evaluation.company_action_digest,
                "supporting_review_id": supporting_review_id,
                "supporting_review_digest": supporting_review_digest,
                "effective_completed_at": effective_completed_at,
            }
        )
        artifact = {
            "view": view,
            "supporting_review_id": supporting_review_id,
            "supporting_review_digest": supporting_review_digest,
            "effective_completed_at": effective_completed_at,
            "evaluated_at": evaluated_at.isoformat(),
            "completed_at": evaluated_at.isoformat(),
            "evaluated_as_of": evaluated_as_of.isoformat(),
            "outcome_matured_at": matured_at.isoformat() if matured_at else None,
            "evaluator_digest": evaluator_digest,
            "daily_bars_provenance": evaluation.daily_bars_provenance,
            "daily_bars_digest": evaluation.daily_bars_digest,
            "session_evidence_provenance": evaluation.session_evidence_provenance,
            "session_evidence_digest": evaluation.session_evidence_digest,
            "company_action_provenance": evaluation.company_action_provenance,
            "company_action_digest": evaluation.company_action_digest,
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

    def _validate_persisted_outcome(
        self,
        advice: dict[str, Any],
        record: dict[str, Any],
        *,
        records: list[dict[str, Any]],
        expected_view: AdviceView,
    ) -> PersistedOutcomeValidation:
        reasons: list[str] = []
        if record.get("kind") != "outcome" or record.get("parent_id") != advice.get("id"):
            reasons.append("outcome_parent_mismatch")
        if record.get("view", "raw") != expected_view:
            reasons.append("outcome_view_mismatch")
        if record.get("advice_digest") != frozen_record_digest(advice):
            reasons.append("outcome_advice_digest_mismatch")

        try:
            plan = TradePlan.model_validate(advice.get("trade_plan"))
            identity, instance = canonical_trade_plan_hashes(plan)
        except Exception:
            plan = None
            identity = instance = None
            reasons.append("outcome_parent_plan_invalid")
        if plan is not None and (
            identity != plan.plan_identity
            or instance != plan.plan_instance_id
            or record.get("plan_identity") != identity
            or record.get("plan_instance_id") != instance
        ):
            reasons.append("outcome_plan_identity_mismatch")

        payload = record.get("outcome")
        outcome: TradePlanOutcome | None = None
        try:
            outcome = TradePlanOutcome.model_validate(payload)
        except Exception:
            reasons.append("outcome_payload_invalid")
        if isinstance(payload, dict):
            try:
                expected_outcome_digest = canonical_hash(payload)
            except (TypeError, ValueError):
                expected_outcome_digest = None
            if record.get("outcome_digest") != expected_outcome_digest:
                reasons.append("outcome_digest_mismatch")
        else:
            expected_outcome_digest = None

        if outcome is not None and plan is not None:
            if (
                outcome.plan_identity != identity
                or outcome.plan_instance_id != instance
                or outcome.symbol != plan.symbol
                or outcome.evidence_as_of != plan.evidence_as_of
            ):
                reasons.append("outcome_contract_mismatch")
            if record.get("evaluated_as_of") != outcome.evaluated_as_of.isoformat():
                reasons.append("outcome_evaluated_as_of_mismatch")

        evaluated_at = _aware(record.get("evaluated_at"))
        completed_at = _aware(record.get("completed_at"))
        if evaluated_at is None or completed_at is None or evaluated_at != completed_at:
            reasons.append("outcome_completed_at_invalid")
        matured_at = _aware(record.get("outcome_matured_at"))
        if outcome is not None:
            terminal_payload = dict(payload) if isinstance(payload, dict) else {}
            terminal_payload.pop("evaluated_as_of", None)
            expected_terminal_digest = (
                canonical_hash(terminal_payload)
                if outcome.status in TERMINAL_OUTCOMES and terminal_payload
                else None
            )
            if record.get("terminal_digest") != expected_terminal_digest:
                reasons.append("outcome_terminal_digest_mismatch")
            if outcome.status in TERMINAL_OUTCOMES:
                expected_maturity = (
                    market_close(outcome.terminal_session)
                    if outcome.terminal_session is not None
                    else None
                )
                if expected_maturity is None or matured_at != expected_maturity:
                    reasons.append("outcome_maturity_mismatch")
                if completed_at is None or matured_at is None or completed_at < matured_at:
                    reasons.append("outcome_completed_before_maturity")
                if outcome.status == "triggered" and outcome.exit_session != outcome.terminal_session:
                    reasons.append("outcome_terminal_session_mismatch")
                if outcome.status != "triggered" and outcome.gross_return_pct is not None:
                    reasons.append("outcome_nontriggered_return")
            elif matured_at is not None or outcome.terminal_session is not None:
                reasons.append("outcome_nonterminal_maturity")

        provenance_pairs = (
            ("daily_bars_provenance", "daily_bars_digest"),
            ("session_evidence_provenance", "session_evidence_digest"),
            ("company_action_provenance", "company_action_digest"),
        )
        for provenance_key, digest_key in provenance_pairs:
            provenance = record.get(provenance_key)
            try:
                provenance_digest = canonical_hash(provenance) if isinstance(provenance, dict) else None
            except (TypeError, ValueError):
                provenance_digest = None
            if record.get(digest_key) != provenance_digest:
                reasons.append(f"{digest_key}_mismatch")

        evaluator_digest = canonical_hash(
            {
                "version": EVALUATOR_VERSION,
                "price_adjustment_semantics": (
                    plan.price_adjustment_semantics if plan is not None else None
                ),
                "cost_assumption": plan.cost_assumption if plan is not None else None,
            }
        )
        if record.get("evaluator_digest") != evaluator_digest:
            reasons.append("outcome_evaluator_digest_mismatch")

        supporting_review_id = record.get("supporting_review_id")
        supporting_review_digest = record.get("supporting_review_digest")
        effective_completed_at = record.get("effective_completed_at")
        if expected_view == "reviewed":
            review, review_completed, review_reasons = self._validated_review(
                advice,
                records=records,
                review_id=supporting_review_id if isinstance(supporting_review_id, str) else "",
            )
            reasons.extend(review_reasons)
            if review is None or review.get("review_digest") != supporting_review_digest:
                reasons.append("supporting_review_digest_mismatch")
            advice_completed = _aware(advice.get("completed_at"))
            expected_effective = (
                max(advice_completed, review_completed)
                if advice_completed is not None and review_completed is not None
                else None
            )
            if expected_effective is None or effective_completed_at != expected_effective.isoformat():
                reasons.append("outcome_effective_completed_at_mismatch")
        elif supporting_review_id is not None or supporting_review_digest is not None:
            reasons.append("raw_outcome_has_review_support")
        elif effective_completed_at != advice.get("completed_at"):
            reasons.append("raw_outcome_effective_completed_at_mismatch")

        if plan is not None and outcome is not None:
            expected_input_digest = canonical_hash(
                {
                    "plan": plan.model_dump(mode="json"),
                    "evaluated_as_of": outcome.evaluated_as_of,
                    "view": expected_view,
                    "evaluator_digest": evaluator_digest,
                    "daily_bars_digest": record.get("daily_bars_digest"),
                    "session_evidence_digest": record.get("session_evidence_digest"),
                    "company_action_digest": record.get("company_action_digest"),
                    "supporting_review_id": supporting_review_id,
                    "supporting_review_digest": supporting_review_digest,
                    "effective_completed_at": effective_completed_at,
                }
            )
            if record.get("input_digest") != expected_input_digest:
                reasons.append("outcome_input_digest_mismatch")

        return PersistedOutcomeValidation(
            outcome=outcome if not reasons else None,
            reasons=tuple(dict.fromkeys(reasons)),
        )

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
        grouped: dict[
            str,
            list[tuple[dict[str, Any] | None, TradePlanOutcome | None]],
        ] = {}
        grouped_advice_counts: dict[str, int] = {}
        invalid_groups: set[str] = set()
        orphan_conflicts = 0
        for advice in advice_rows:
            instance_id = advice.get("plan_instance_id")
            if not isinstance(instance_id, str):
                orphan_conflicts += 1
                continue
            outcomes = [
                item for item in records
                if item.get("kind") == "outcome"
                and item.get("parent_id") == advice.get("id")
                and item.get("view", "raw") == view
            ]
            raw_admission = self._forward_status(advice, view="raw", records=records)
            if raw_admission.status != "admitted":
                if outcomes:
                    invalid_groups.add(instance_id)
                continue
            validated: list[tuple[dict[str, Any], TradePlanOutcome]] = []
            for item in outcomes:
                result = self._validate_persisted_outcome(
                    advice, item, records=records, expected_view=view
                )
                if not result.valid or result.outcome is None:
                    invalid_groups.add(instance_id)
                    continue
                validated.append((item, result.outcome))
            if instance_id in invalid_groups:
                continue
            if not validated and view == "reviewed":
                current = self._forward_status(advice, view="reviewed", records=records)
                if current.status != "admitted":
                    continue
            grouped_advice_counts[instance_id] = grouped_advice_counts.get(instance_id, 0) + 1
            grouped.setdefault(instance_id, []).extend(validated or [(None, None)])

        counts = {name: 0 for name in (
            "triggered", "not_triggered", "immature", "data_insufficient", "undeterminable",
            "not_evaluated",
        )}
        returns: list[float] = []
        conflicts = orphan_conflicts + len(invalid_groups)
        duplicates = 0
        admitted = 0
        for instance_id, rows in grouped.items():
            duplicates += max(0, grouped_advice_counts[instance_id] - 1)
            if instance_id in invalid_groups:
                continue
            terminal_rows = [
                row for row in rows
                if row[0] is not None
                and row[1] is not None
                and row[1].status in TERMINAL_OUTCOMES
            ]
            nonnull_rows = [row for row in rows if row[0] is not None and row[1] is not None]
            candidates = terminal_rows or nonnull_rows
            digests = {
                item.get("terminal_digest") or item.get("outcome_digest")
                for item, _outcome in candidates
                if item is not None
            }
            if len(digests) > 1:
                conflicts += 1
                continue
            admitted += 1
            if not candidates:
                counts["not_evaluated"] += 1
                continue
            selected_record, selected_outcome = max(
                candidates,
                key=lambda pair: (
                    str(pair[0].get("evaluated_at") or "") if pair[0] else "",
                    str(pair[0].get("id") or "") if pair[0] else "",
                ),
            )
            assert selected_record is not None and selected_outcome is not None
            status = selected_outcome.status
            if status not in counts:
                conflicts += 1
                continue
            counts[status] += 1
            value = selected_outcome.gross_return_pct if status == "triggered" else None
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
        admission = self._forward_status(advice, view="raw", records=records)
        if admission.status != "admitted":
            raise AIResearchHistoryError("Reflection source Advice is not forward-admitted")
        validation = self._validate_persisted_outcome(
            advice, outcome, records=records, expected_view=payload.view
        )
        if not validation.valid or validation.outcome is None:
            raise AIResearchHistoryError(
                "Reflection outcome is invalid: " + ",".join(validation.reasons)
            )
        result = validation.outcome
        matured_at = _aware(outcome.get("outcome_matured_at"))
        outcome_completed = _aware(outcome.get("completed_at"))
        if (
            result.status not in TERMINAL_OUTCOMES
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
            admission = self._forward_status(advice, view="raw", records=records)
            validation = self._validate_persisted_outcome(
                advice, outcome, records=records, expected_view=view
            )
            reflection_completed = _aware(reflection.get("completed_at"))
            outcome_completed = _aware(outcome.get("completed_at"))
            matured = _aware(outcome.get("outcome_matured_at"))
            reflection_material = {
                key: reflection.get(key)
                for key in (
                    "advice_id", "outcome_id", "view", "summary", "lessons",
                    "plan_identity", "plan_instance_id", "advice_digest", "outcome_digest",
                )
            }
            if (
                admission.status != "admitted"
                or not validation.valid
                or advice.get("plan_identity") != plan_identity
                or reflection.get("reflection_digest") != canonical_hash(reflection_material)
                or reflection_completed is None
                or outcome_completed is None
                or matured is None
                or not (reflection_completed < cutoff and outcome_completed < cutoff and matured < cutoff)
            ):
                continue
            selected.append(reflection)
        def reflection_sort_key(item: dict[str, Any]) -> tuple[float, float, str]:
            outcome = _record_by_id(records, str(item.get("outcome_id") or "")) or {}
            matured = _aware(outcome.get("outcome_matured_at"))
            completed = _aware(item.get("completed_at"))
            return (
                matured.timestamp() if matured is not None else float("-inf"),
                completed.timestamp() if completed is not None else float("-inf"),
                str(item.get("id") or ""),
            )

        selected.sort(key=reflection_sort_key, reverse=True)
        return selected[: min(limit, 2)]


def get_advice_tracking_service() -> AdviceTrackingService:
    return AdviceTrackingService()

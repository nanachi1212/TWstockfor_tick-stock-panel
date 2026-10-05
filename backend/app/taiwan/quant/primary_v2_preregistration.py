"""Immutable Primary OOS V2 preregistration and confirmatory guards."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

import polars as pl

from app.taiwan.quant.live_contract import canonical_hash

V1_RUN_ID = "2538dbd6-acc5-43ae-9061-a74149aeb9f1"
V1_DATASET_IDENTITY = "c0336dd5980a165bfb0fa1fa99b7e5438ea4bd862ade90e17b73210296e57626"
V1_EVALUATION_START = date(2018, 10, 24)
V1_EVALUATION_END = date(2026, 8, 4)
PREREGISTRATION_CREATED_AT = "2026-10-05T00:00:00+08:00"
REQUIRED_DECISION_SESSIONS = 63
HORIZONS = (5, 20)


class EvaluationPurpose(StrEnum):
    RETROSPECTIVE_DIAGNOSTIC = "retrospective_diagnostic"
    DEVELOPMENT_SELECTION = "development_selection"
    CONFIRMATORY_EVALUATION = "confirmatory_evaluation"


class ConfirmatoryStatus(StrEnum):
    WAITING_FOR_SESSIONS = "waiting_for_sessions"
    WAITING_FOR_LABELS = "waiting_for_labels"
    READY = "ready"
    EVALUATED = "evaluated"


@dataclass(frozen=True)
class CandidateSpec:
    key: str
    factors: tuple[str, ...]
    weights: tuple[float, ...]

    def __post_init__(self) -> None:
        if not self.key or not self.factors or len(self.factors) != len(self.weights):
            raise ValueError("candidate factors and weights must be non-empty and aligned")
        if abs(sum(self.weights) - 1.0) > 1e-12:
            raise ValueError("candidate weights must sum to one")

    def contract(self) -> dict[str, Any]:
        return {
            "candidate_key": self.key,
            "factor_set": list(self.factors),
            "factor_directions": {factor: "higher_is_better" for factor in self.factors},
            "weights": dict(zip(self.factors, self.weights, strict=True)),
            "normalization": "daily_cross_sectional_ordinal_percentile_rank",
            "normalization_version": "ordinal-percentile-v1",
            "missing_policy": "complete_case_for_candidate; missing evidence is unavailable",
            "universe_policy": "primary_verified_twse_v1",
            "universe_policy_version": "v1",
            "pit_policy": "features available at decision-session close only",
            "pit_policy_version": "primary-oos-pit-v1",
            "factor_version": "tw-factors-v1",
            "feature_schema_version": "live-features-v1",
            "scorer_version": "weighted-cross-sectional-percentile-v1",
            "label_horizons_sessions": list(HORIZONS),
            "bucket_rule": "top and bottom ceil(20 percent); symbol ascending tie break",
            "benchmark": "0050.TWSE",
        }

    @property
    def identity(self) -> str:
        return canonical_hash(self.contract())

    def describe(self) -> dict[str, Any]:
        return {**self.contract(), "model_identity": self.identity}


CANDIDATES = (
    CandidateSpec(
        "primary_v1_equal_momentum",
        ("momentum_5d", "momentum_20d", "momentum_60d"),
        (1 / 3, 1 / 3, 1 / 3),
    ),
    CandidateSpec("primary_v2_momentum_60d", ("momentum_60d",), (1.0,)),
    CandidateSpec(
        "primary_v2_momentum_20d_60d",
        ("momentum_20d", "momentum_60d"),
        (0.5, 0.5),
    ),
)
CANDIDATE_BY_KEY = {candidate.key: candidate for candidate in CANDIDATES}


@dataclass(frozen=True)
class SelectionRule:
    primary_metric: str = "direction_adjusted_mean_ic"
    secondary_metric: str = "top_minus_universe"
    minimum_valid_dates: int = 40
    minimum_observations: int = 1200
    minimum_cross_section_observations_per_date: int = 30
    tie_break: tuple[str, ...] = tuple(candidate.key for candidate in CANDIDATES)

    def describe(self) -> dict[str, Any]:
        return {
            "scope": "outer train and inner validation only",
            "outer_test_access": "prohibited",
            "primary_metric": self.primary_metric,
            "secondary_metric": self.secondary_metric,
            "minimum_valid_dates": self.minimum_valid_dates,
            "minimum_observations": self.minimum_observations,
            "minimum_cross_section_observations_per_date": (
                self.minimum_cross_section_observations_per_date
            ),
            "missing_evidence": "candidate is ineligible; unavailable is never converted to zero",
            "tie_break": list(self.tie_break),
            "automation": "deterministic; AI selection prohibited",
        }


SELECTION_RULE = SelectionRule()


@dataclass(frozen=True)
class CandidateSelectionMetrics:
    direction_adjusted_mean_ic: float | None
    top_minus_universe: float | None
    valid_dates: int
    observations: int
    minimum_cross_section_observations: int

    def eligible(self, rule: SelectionRule = SELECTION_RULE) -> bool:
        return (
            self.direction_adjusted_mean_ic is not None
            and self.top_minus_universe is not None
            and self.valid_dates >= rule.minimum_valid_dates
            and self.observations >= rule.minimum_observations
            and self.minimum_cross_section_observations
            >= rule.minimum_cross_section_observations_per_date
        )


def select_candidate(
    metrics: Mapping[str, CandidateSelectionMetrics],
    rule: SelectionRule = SELECTION_RULE,
) -> CandidateSpec | None:
    """Select one preregistered candidate without outer-test evidence."""
    unknown = set(metrics) - set(CANDIDATE_BY_KEY)
    if unknown:
        raise ValueError(f"unregistered candidate metrics: {sorted(unknown)}")
    priority = {key: index for index, key in enumerate(rule.tie_break)}
    eligible = [
        (key, value) for key, value in metrics.items() if value.eligible(rule)
    ]
    if not eligible:
        return None

    def selection_key(
        item: tuple[str, CandidateSelectionMetrics],
    ) -> tuple[float, float, int]:
        key, value = item
        assert value.direction_adjusted_mean_ic is not None
        assert value.top_minus_universe is not None
        return (
            -value.direction_adjusted_mean_ic,
            -value.top_minus_universe,
            priority[key],
        )

    selected, _ = min(
        eligible,
        key=selection_key,
    )
    return CANDIDATE_BY_KEY[selected]


@dataclass(frozen=True)
class ConfirmatoryWindow:
    start_session: date | None
    required_decision_sessions: int
    observed_decision_sessions: int
    block_end_session: date | None
    horizons: tuple[int, ...]
    label_maturity: Mapping[int, bool]
    label_end_sessions: Mapping[int, date | None]
    status: ConfirmatoryStatus

    def describe(self) -> dict[str, Any]:
        return {
            "start_session": self.start_session.isoformat() if self.start_session else None,
            "required_decision_sessions": self.required_decision_sessions,
            "observed_decision_sessions": self.observed_decision_sessions,
            "block_end_session": (
                self.block_end_session.isoformat() if self.block_end_session else None
            ),
            "horizons": list(self.horizons),
            "label_maturity": {
                str(horizon): matured for horizon, matured in self.label_maturity.items()
            },
            "label_end_sessions": {
                str(horizon): end.isoformat() if end else None
                for horizon, end in self.label_end_sessions.items()
            },
            "status": self.status.value,
        }


def build_confirmatory_window(
    verified_twse_sessions: Sequence[date], *, evaluated: bool = False,
) -> ConfirmatoryWindow:
    """Build readiness from verified sessions. Calendar weekdays are not evidence."""
    sessions = list(verified_twse_sessions)
    if sessions != sorted(set(sessions)):
        raise ValueError("verified sessions must be sorted and unique")
    eligible = [session for session in sessions if session > V1_EVALUATION_END]
    start = eligible[0] if eligible else None
    decision_sessions = eligible[:REQUIRED_DECISION_SESSIONS]
    block_end = (
        decision_sessions[-1]
        if len(decision_sessions) == REQUIRED_DECISION_SESSIONS
        else None
    )
    later = [session for session in eligible if block_end and session > block_end]
    label_end_sessions = {
        horizon: later[horizon - 1] if len(later) >= horizon else None
        for horizon in HORIZONS
    }
    maturity = {horizon: end is not None for horizon, end in label_end_sessions.items()}
    if block_end is None:
        status = ConfirmatoryStatus.WAITING_FOR_SESSIONS
    elif not maturity[max(HORIZONS)]:
        status = ConfirmatoryStatus.WAITING_FOR_LABELS
    elif evaluated:
        status = ConfirmatoryStatus.EVALUATED
    else:
        status = ConfirmatoryStatus.READY
    return ConfirmatoryWindow(
        start_session=start,
        required_decision_sessions=REQUIRED_DECISION_SESSIONS,
        observed_decision_sessions=len(decision_sessions),
        block_end_session=block_end,
        horizons=HORIZONS,
        label_maturity=maturity,
        label_end_sessions=label_end_sessions,
        status=status,
    )


def guard_evaluation_frame(
    frame: pl.DataFrame,
    *,
    purpose: EvaluationPurpose,
    window: ConfirmatoryWindow,
    date_column: str = "date",
) -> pl.DataFrame:
    """Apply the central confirmatory boundary to every V2 evaluation read."""
    if date_column not in frame.columns:
        raise ValueError(f"evaluation frame has no {date_column!r} column")
    if window.start_session is None:
        if purpose is EvaluationPurpose.CONFIRMATORY_EVALUATION:
            raise RuntimeError("confirmatory evaluation is not ready")
        return frame.filter(pl.col(date_column) <= V1_EVALUATION_END)
    if purpose is not EvaluationPurpose.CONFIRMATORY_EVALUATION:
        return frame.filter(pl.col(date_column) < window.start_session)
    ensure_confirmatory_ready(window)
    return frame.filter(
        pl.col(date_column).is_between(window.start_session, window.block_end_session)
    )


def ensure_confirmatory_ready(window: ConfirmatoryWindow) -> None:
    if window.status is not ConfirmatoryStatus.READY:
        raise RuntimeError(f"confirmatory evaluation is not ready ({window.status.value})")


@dataclass(frozen=True)
class BenchmarkResult:
    """Backward-compatible optional benchmark fields for a future artifact."""

    benchmark_symbol: str | None = None
    benchmark_return: float | None = None
    top_minus_benchmark: float | None = None
    valid_benchmark_dates: int = 0

    def __post_init__(self) -> None:
        supplied = (
            self.benchmark_symbol is not None
            or self.benchmark_return is not None
            or self.top_minus_benchmark is not None
        )
        if supplied and self.valid_benchmark_dates <= 0:
            raise ValueError("benchmark evidence needs positive valid_benchmark_dates")
        if not supplied and self.valid_benchmark_dates != 0:
            raise ValueError("missing benchmark evidence must have zero valid dates")

    def describe(self) -> dict[str, Any]:
        return {
            "benchmark_symbol": self.benchmark_symbol,
            "benchmark_return": self.benchmark_return,
            "top_minus_benchmark": self.top_minus_benchmark,
            "valid_benchmark_dates": self.valid_benchmark_dates,
        }


@dataclass(frozen=True)
class ConfirmatoryRunContract:
    preregistration_id: str
    candidate_key: str
    candidate_identity: str
    selection_rule_identity: str
    start_session: date
    block_end_session: date
    dataset_identity: str

    def content(self) -> dict[str, Any]:
        return {
            "purpose": EvaluationPurpose.CONFIRMATORY_EVALUATION.value,
            "preregistration_id": self.preregistration_id,
            "candidate_key": self.candidate_key,
            "candidate_identity": self.candidate_identity,
            "selection_rule_identity": self.selection_rule_identity,
            "selection_rule": SELECTION_RULE.describe(),
            "start_session": self.start_session.isoformat(),
            "block_end_session": self.block_end_session.isoformat(),
            "required_decision_sessions": REQUIRED_DECISION_SESSIONS,
            "horizons": list(HORIZONS),
            "dataset_identity": self.dataset_identity,
        }

    @property
    def run_identity(self) -> str:
        return canonical_hash(self.content())

    def describe(self) -> dict[str, Any]:
        return {**self.content(), "run_identity": self.run_identity}


def build_confirmatory_run_contract(
    *,
    window: ConfirmatoryWindow,
    selected_candidate_key: str,
    dataset_identity: str,
) -> ConfirmatoryRunContract:
    """Create the one-run identity only after every preregistered gate passes."""
    ensure_confirmatory_ready(window)
    if selected_candidate_key not in CANDIDATE_BY_KEY:
        raise ValueError("confirmatory run requires a preregistered candidate")
    if not dataset_identity:
        raise ValueError("confirmatory run requires a dataset identity")
    assert window.start_session is not None
    assert window.block_end_session is not None
    candidate = CANDIDATE_BY_KEY[selected_candidate_key]
    return ConfirmatoryRunContract(
        preregistration_id=PREREGISTRATION_ID,
        candidate_key=candidate.key,
        candidate_identity=candidate.identity,
        selection_rule_identity=canonical_hash(SELECTION_RULE.describe()),
        start_session=window.start_session,
        block_end_session=window.block_end_session,
        dataset_identity=dataset_identity,
    )


@dataclass(frozen=True)
class ConfirmatoryMetrics:
    direction_adjusted_mean_ic: float | None
    median_ic: float | None
    positive_ic_ratio: float | None
    top_bucket_return: float | None
    universe_return: float | None
    bottom_bucket_return: float | None
    top_minus_universe: float | None
    long_short: float | None
    top_minus_0050: float | None
    valid_date_count: int
    pit_or_data_quality_violation: bool


def assess_confirmatory_success(metrics: ConfirmatoryMetrics) -> str:
    """Return pass, fail, or insufficient_evidence for one horizon."""
    required = (
        metrics.direction_adjusted_mean_ic,
        metrics.top_minus_universe,
        metrics.long_short,
        metrics.top_minus_0050,
    )
    if metrics.valid_date_count < 50 or any(value is None for value in required):
        return "insufficient_evidence"
    if metrics.pit_or_data_quality_violation:
        return "fail"
    mean_ic = metrics.direction_adjusted_mean_ic
    top_minus_universe = metrics.top_minus_universe
    long_short = metrics.long_short
    top_minus_0050 = metrics.top_minus_0050
    assert mean_ic is not None
    assert top_minus_universe is not None
    assert long_short is not None
    assert top_minus_0050 is not None
    return "pass" if (
        mean_ic > 0
        and top_minus_universe > 0
        and long_short > 0
        and top_minus_0050 >= 0
    ) else "fail"


def preregistration_content() -> dict[str, Any]:
    return {
        "version": "primary-oos-v2-preregistration-v1",
        "created_at": PREREGISTRATION_CREATED_AT,
        "historical_baseline": {
            "run_id": V1_RUN_ID,
            "dataset_identity": V1_DATASET_IDENTITY,
            "evaluation_start": V1_EVALUATION_START.isoformat(),
            "evaluation_end": V1_EVALUATION_END.isoformat(),
            "interpretation": "immutable historical baseline",
        },
        "candidate_status": "development_candidates",
        "candidates": [candidate.describe() for candidate in CANDIDATES],
        "selection_rule": SELECTION_RULE.describe(),
        "retrospective_protocol": {
            "range_start": V1_EVALUATION_START.isoformat(),
            "range_end": V1_EVALUATION_END.isoformat(),
            "required_label": EvaluationPurpose.RETROSPECTIVE_DIAGNOSTIC.value,
            "prohibited_labels": ["confirmatory", "untouched", "clean_oos", "prospective"],
        },
        "confirmatory": {
            "start_policy": "first verified TWSE trading session after 2026-08-04",
            "decision_block_sessions": REQUIRED_DECISION_SESSIONS,
            "horizons_sessions": list(HORIZONS),
            "label_maturity": "last decision session needs its full 5D or 20D forward label",
            "overall_ready": "63 decision sessions and the final 20D label are complete",
            "pending_label_policy": "fail_closed; do not drop, fill zero, or use partial return",
        },
        "benchmark": "0050.TWSE",
        "success_criteria": {
            "per_horizon": True,
            "minimum_valid_dates": 50,
            "direction_adjusted_mean_ic": "> 0",
            "top_minus_universe": "> 0",
            "long_short": "> 0",
            "top_minus_0050": ">= 0",
            "pit_or_data_quality_violation": False,
            "small_sample_result": "insufficient_evidence",
            "statistical_significance_threshold": "not_preregistered",
        },
    }


PREREGISTRATION_ID = canonical_hash(preregistration_content())


def describe_preregistration() -> dict[str, Any]:
    return {**preregistration_content(), "preregistration_id": PREREGISTRATION_ID}

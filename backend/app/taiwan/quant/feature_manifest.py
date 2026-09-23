"""Feature manifest x data capability -> training eligibility.

The question this answers is "given what the data layer can currently prove,
which features may enter a training run?" — and it answers it by *computing*,
never by consulting a hand-maintained list.

There is deliberately no ``TRAINING_ELIGIBLE = {...}`` anywhere. A feature
whose dataset is short on coverage today is rejected today; when the background
backfill pushes that dataset past the manifest's thresholds the same feature
becomes eligible with **no code change**. That property is what keeps a
long-running census from turning into a source of stale hardcoded truth.

Every rejection carries a machine-readable :class:`RejectionReason`, so a Data
Health panel can show *why* a feature is out rather than just that it is.

Availability
------------
``required_availability_policy`` is checked through
``app.taiwan.quant_eligibility.pit_usable``, so the narrow corporate-action
exception (audit §8) applies where it should and nowhere else: a fundamentals
or revenue feature carrying ``market_mechanism_inferred`` is still rejected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.taiwan.quant_eligibility import pit_usable


class RejectionReason(StrEnum):
    """Machine-readable reasons a feature did not make a training run."""

    DATASET_UNKNOWN = "dataset_unknown"
    INSUFFICIENT_HISTORY = "insufficient_history"
    INSUFFICIENT_COVERAGE = "insufficient_coverage"
    AVAILABILITY_POLICY_NOT_PIT_SAFE = "availability_policy_not_pit_safe"
    REVISION_UNSTABLE = "revision_unstable"
    DATASET_ERROR = "dataset_error"


#: Ordered weakest → strongest. A manifest requiring ``stable`` rejects a
#: dataset that can only offer ``current_reference``.
REVISION_STABILITY_ORDER: tuple[str, ...] = (
    "current_reference",   # only today's view exists; history can be rewritten
    "append_only",         # revisions arrive as new rows, old ones survive
    "stable",              # values for a past date never change
)


def _stability_rank(value: str) -> int:
    try:
        return REVISION_STABILITY_ORDER.index(value)
    except ValueError:
        return -1


@dataclass(frozen=True)
class FeatureSpec:
    """What a feature needs from the data layer before it may be trained on."""

    feature_name: str
    dataset: str
    feature_group: str
    required_history_sessions: int = 0
    #: Fraction of the required window that must actually be present, 0..1.
    required_coverage_ratio: float = 0.0
    #: ``None`` means "a verified available_at is required".
    required_availability_policy: str | None = None
    required_revision_stability: str = "current_reference"
    #: Absolute floor regardless of the training window length.
    min_history: int = 0

    def describe(self) -> dict[str, Any]:
        return {
            "feature_name": self.feature_name,
            "dataset": self.dataset,
            "feature_group": self.feature_group,
            "required_history_sessions": self.required_history_sessions,
            "required_coverage_ratio": self.required_coverage_ratio,
            "required_availability_policy": self.required_availability_policy,
            "required_revision_stability": self.required_revision_stability,
            "min_history": self.min_history,
        }


@dataclass(frozen=True)
class DatasetCapability:
    """What the data layer can currently prove about one dataset.

    Produced from the census / classification / store coverage metadata — see
    ``app/taiwan/quant/data_health.py``.
    """

    dataset: str
    available_sessions: int = 0
    coverage_ratio: float = 0.0
    availability_policy: str | None = None
    #: Truthy when the dataset carries a verified ``available_at``.
    has_verified_available_at: bool = False
    revision_stability: str = "current_reference"
    error: str | None = None

    def describe(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "available_sessions": self.available_sessions,
            "coverage_ratio": self.coverage_ratio,
            "availability_policy": self.availability_policy,
            "has_verified_available_at": self.has_verified_available_at,
            "revision_stability": self.revision_stability,
            "error": self.error,
        }


@dataclass(frozen=True)
class FeatureManifest:
    """A versioned set of feature requirements.

    Bumping ``model_version`` is how a change in what a model demands becomes
    auditable against artifacts produced by an earlier version.
    """

    model_version: str
    features: tuple[FeatureSpec, ...] = ()

    def describe(self) -> dict[str, Any]:
        return {
            "model_version": self.model_version,
            "features": [f.describe() for f in self.features],
        }


@dataclass(frozen=True)
class FeatureVerdict:
    feature_name: str
    dataset: str
    feature_group: str
    eligible: bool
    reasons: tuple[RejectionReason, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> dict[str, Any]:
        return {
            "feature_name": self.feature_name,
            "dataset": self.dataset,
            "feature_group": self.feature_group,
            "eligible": self.eligible,
            "reasons": [r.value for r in self.reasons],
            "detail": self.detail,
        }


@dataclass(frozen=True)
class EligibilityResolution:
    model_version: str
    verdicts: tuple[FeatureVerdict, ...]

    @property
    def eligible_features(self) -> tuple[str, ...]:
        return tuple(v.feature_name for v in self.verdicts if v.eligible)

    @property
    def rejected_features(self) -> tuple[str, ...]:
        return tuple(v.feature_name for v in self.verdicts if not v.eligible)

    def reasons_for(self, feature_name: str) -> tuple[RejectionReason, ...]:
        for verdict in self.verdicts:
            if verdict.feature_name == feature_name:
                return verdict.reasons
        raise KeyError(feature_name)

    def describe(self) -> dict[str, Any]:
        return {
            "model_version": self.model_version,
            "eligible": list(self.eligible_features),
            "rejected": [v.describe() for v in self.verdicts if not v.eligible],
        }


def resolve_training_eligibility(
    manifest: FeatureManifest,
    capabilities: dict[str, DatasetCapability],
) -> EligibilityResolution:
    """Evaluate every feature in *manifest* against current data capability.

    Pure and deterministic: the same (manifest, capabilities) pair always gives
    the same verdicts, and nothing is cached between calls.
    """
    verdicts: list[FeatureVerdict] = []
    for spec in manifest.features:
        capability = capabilities.get(spec.dataset)
        reasons: list[RejectionReason] = []
        detail: dict[str, Any] = {}

        if capability is None:
            verdicts.append(FeatureVerdict(
                spec.feature_name, spec.dataset, spec.feature_group,
                eligible=False, reasons=(RejectionReason.DATASET_UNKNOWN,),
                detail={"dataset": spec.dataset},
            ))
            continue

        if capability.error:
            reasons.append(RejectionReason.DATASET_ERROR)
            detail["error"] = capability.error

        required_sessions = max(spec.required_history_sessions, spec.min_history)
        if capability.available_sessions < required_sessions:
            reasons.append(RejectionReason.INSUFFICIENT_HISTORY)
            detail["available_sessions"] = capability.available_sessions
            detail["required_sessions"] = required_sessions

        if capability.coverage_ratio < spec.required_coverage_ratio:
            reasons.append(RejectionReason.INSUFFICIENT_COVERAGE)
            detail["coverage_ratio"] = capability.coverage_ratio
            detail["required_coverage_ratio"] = spec.required_coverage_ratio

        usable = pit_usable(
            spec.dataset,
            available_at=True if capability.has_verified_available_at else None,
            availability_policy=capability.availability_policy,
        )
        if not usable:
            reasons.append(RejectionReason.AVAILABILITY_POLICY_NOT_PIT_SAFE)
            detail["availability_policy"] = capability.availability_policy
        elif (
            spec.required_availability_policy is not None
            and capability.availability_policy != spec.required_availability_policy
            and not capability.has_verified_available_at
        ):
            reasons.append(RejectionReason.AVAILABILITY_POLICY_NOT_PIT_SAFE)
            detail["availability_policy"] = capability.availability_policy
            detail["required_availability_policy"] = spec.required_availability_policy

        if _stability_rank(capability.revision_stability) < _stability_rank(
            spec.required_revision_stability
        ):
            reasons.append(RejectionReason.REVISION_UNSTABLE)
            detail["revision_stability"] = capability.revision_stability
            detail["required_revision_stability"] = spec.required_revision_stability

        verdicts.append(FeatureVerdict(
            spec.feature_name, spec.dataset, spec.feature_group,
            eligible=not reasons, reasons=tuple(reasons), detail=detail,
        ))
    return EligibilityResolution(manifest.model_version, tuple(verdicts))


def training_matrix(
    frame: Any,
    manifest: FeatureManifest,
    capabilities: dict[str, DatasetCapability],
    *,
    keep_columns: tuple[str, ...] = ("symbol", "date"),
) -> tuple[Any, EligibilityResolution]:
    """Project *frame* down to the eligible features only.

    A rejected feature cannot reach the model: it is dropped from the returned
    frame even when the caller's frame still has the column. The resolution is
    returned alongside so the caller can record exactly what was excluded.
    """
    from app.taiwan.adjust import PROVENANCE_COLUMNS, assert_training_safe

    assert_training_safe(frame)
    resolution = resolve_training_eligibility(manifest, capabilities)
    eligible = set(resolution.eligible_features)
    columns = [c for c in keep_columns if c in frame.columns]
    columns += [c for c in frame.columns if c in eligible and c not in columns]
    columns += [c for c in PROVENANCE_COLUMNS if c in frame.columns and c not in columns]
    return frame.select(columns), resolution

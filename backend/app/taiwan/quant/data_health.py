"""Graded data-health readiness, so framework work never waits on a full backfill.

A single ``ready: true/false`` would be useless here: at 20% census coverage the
fold generator, regime rules and eligibility resolver are all perfectly usable,
while training and any OOS claim clearly are not. So readiness is graded, and
each level that is *not* met says why.

Levels (monotonic — a higher level implies every lower one)
-----------------------------------------------------------
``ready_for_framework``      enough to build and unit-test framework code.
                             Always true; frameworks must degrade, not crash.
``ready_for_factor_compute`` enough sessions to compute indicators/factors
                             over a usable window.
``ready_for_training``       census and classification coverage high enough
                             that a training set is not mostly holes.
``ready_for_primary_oos``    the Primary "TWSE Verified OOS" tier is honest:
                             near-complete census plus verified TWSE
                             instrument types.

``blocked_reasons`` is keyed by level, so a panel can render exactly which gate
a dataset is sitting behind.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from enum import StrEnum
from typing import Any

import polars as pl

from app.taiwan.historical_classification import HistoricalClassificationStore
from app.taiwan.observed_universe import ObservedUniverseStore, census_coverage
from app.taiwan.quant.feature_manifest import DatasetCapability


class ReadinessLevel(StrEnum):
    FRAMEWORK = "ready_for_framework"
    FACTOR_COMPUTE = "ready_for_factor_compute"
    TRAINING = "ready_for_training"
    PRIMARY_OOS = "ready_for_primary_oos"


LEVEL_ORDER: tuple[ReadinessLevel, ...] = (
    ReadinessLevel.FRAMEWORK,
    ReadinessLevel.FACTOR_COMPUTE,
    ReadinessLevel.TRAINING,
    ReadinessLevel.PRIMARY_OOS,
)


@dataclass(frozen=True)
class ReadinessThresholds:
    """Tunable gates. Policy, not market truth — versioned by the caller."""

    #: Sessions needed before factor computation is meaningful (≈1 trading year).
    factor_compute_sessions: int = 252
    #: Census completeness needed before a training set stops being mostly holes.
    training_census_ratio: float = 0.80
    #: TWSE codes that must carry a verified instrument type before training.
    training_classification_ratio: float = 0.80
    #: The Primary OOS tier demands a near-complete, verified picture.
    primary_oos_census_ratio: float = 0.99
    primary_oos_classification_ratio: float = 0.99


@dataclass(frozen=True)
class DataHealth:
    census_sessions: int
    census_total_sessions: int
    census_ratio: float
    twse_codes_observed: int
    twse_codes_classified: int
    classification_ratio: float
    levels: dict[str, bool] = field(default_factory=dict)
    blocked_reasons: dict[str, list[str]] = field(default_factory=dict)
    #: Primary TWSE and experimental TPEx have independent coverage records.
    #: The legacy census fields above refer to Primary TWSE only.
    census_by_exchange: dict[str, dict[str, int | float]] = field(default_factory=dict)

    @property
    def primary_census_ratio(self) -> float:
        return self.census_ratio

    @property
    def secondary_tpex_census_ratio(self) -> float | None:
        coverage = self.census_by_exchange.get("TPEX")
        return float(coverage["trading_coverage_ratio"]) if coverage else None

    def is_ready(self, level: ReadinessLevel) -> bool:
        return bool(self.levels.get(level.value, False))

    def highest_level(self) -> ReadinessLevel:
        best = ReadinessLevel.FRAMEWORK
        for level in LEVEL_ORDER:
            if self.levels.get(level.value):
                best = level
        return best

    def describe(self) -> dict[str, Any]:
        return {
            "census": {
                "sessions": self.census_sessions,
                "total_sessions": self.census_total_sessions,
                "ratio": round(self.census_ratio, 4),
            },
            "classification": {
                "twse_codes_observed": self.twse_codes_observed,
                "twse_codes_classified": self.twse_codes_classified,
                "ratio": round(self.classification_ratio, 4),
            },
            "levels": dict(self.levels),
            "highest_level": self.highest_level().value,
            "blocked_reasons": {k: list(v) for k, v in self.blocked_reasons.items()},
            "census_by_exchange": {k: dict(v) for k, v in self.census_by_exchange.items()},
            "primary_twse": dict(self.census_by_exchange.get("TWSE", {})),
            "secondary_tpex_experimental": dict(self.census_by_exchange.get("TPEX", {})),
            "primary_census_ratio": self.primary_census_ratio,
            "secondary_tpex_census_ratio": self.secondary_tpex_census_ratio,
        }


def evaluate_data_health(
    *,
    census_sessions: int,
    census_total_sessions: int,
    twse_codes_observed: int,
    twse_codes_classified: int,
    thresholds: ReadinessThresholds | None = None,
) -> DataHealth:
    """Grade readiness from plain counts, so it is trivially testable."""
    gates = thresholds or ReadinessThresholds()
    census_ratio = (census_sessions / census_total_sessions) if census_total_sessions else 0.0
    classification_ratio = (
        twse_codes_classified / twse_codes_observed) if twse_codes_observed else 0.0

    blocked: dict[str, list[str]] = {level.value: [] for level in LEVEL_ORDER}

    # Framework work is never blocked — that is the whole point of the split.
    levels = {ReadinessLevel.FRAMEWORK.value: True}

    if census_sessions < gates.factor_compute_sessions:
        blocked[ReadinessLevel.FACTOR_COMPUTE.value].append(
            f"census has {census_sessions} sessions, "
            f"needs {gates.factor_compute_sessions}"
        )
    levels[ReadinessLevel.FACTOR_COMPUTE.value] = not blocked[
        ReadinessLevel.FACTOR_COMPUTE.value]

    training_blocked = list(blocked[ReadinessLevel.FACTOR_COMPUTE.value])
    if census_ratio < gates.training_census_ratio:
        training_blocked.append(
            f"census coverage {census_ratio:.1%} < {gates.training_census_ratio:.0%}")
    if classification_ratio < gates.training_classification_ratio:
        training_blocked.append(
            f"TWSE classification coverage {classification_ratio:.1%} "
            f"< {gates.training_classification_ratio:.0%}")
    blocked[ReadinessLevel.TRAINING.value] = training_blocked
    levels[ReadinessLevel.TRAINING.value] = not training_blocked

    oos_blocked = list(training_blocked)
    if census_ratio < gates.primary_oos_census_ratio:
        oos_blocked.append(
            f"census coverage {census_ratio:.1%} < {gates.primary_oos_census_ratio:.0%} "
            "required for a Primary OOS claim")
    if classification_ratio < gates.primary_oos_classification_ratio:
        oos_blocked.append(
            f"TWSE classification coverage {classification_ratio:.1%} "
            f"< {gates.primary_oos_classification_ratio:.0%} required for a Primary OOS claim")
    blocked[ReadinessLevel.PRIMARY_OOS.value] = oos_blocked
    levels[ReadinessLevel.PRIMARY_OOS.value] = not oos_blocked

    return DataHealth(
        census_sessions=census_sessions,
        census_total_sessions=census_total_sessions,
        census_ratio=census_ratio,
        twse_codes_observed=twse_codes_observed,
        twse_codes_classified=twse_codes_classified,
        classification_ratio=classification_ratio,
        levels=levels,
        blocked_reasons={k: v for k, v in blocked.items() if v},
    )


def health_from_stores(
    census: ObservedUniverseStore | None = None,
    classification: HistoricalClassificationStore | None = None,
    *,
    start: date | None = None,
    end: date | None = None,
    thresholds: ReadinessThresholds | None = None,
) -> DataHealth:
    """Grade readiness from the live staging stores."""
    from datetime import datetime

    from app.taiwan.backfill_worker import CENSUS_START
    from app.taiwan.observed_universe import candidate_sessions, first_observed_dates
    from app.taiwan.providers.taiwan_values import TAIPEI

    census = census or ObservedUniverseStore()
    classification = classification or HistoricalClassificationStore()
    start = start or CENSUS_START
    end = end or datetime.now(TAIPEI).date()

    candidates = set(candidate_sessions(start, end))
    twse_coverage = census_coverage(census, "TWSE", candidates)
    tpex_coverage = census_coverage(census, "TPEX", candidates)

    observed = first_observed_dates(census, "TWSE")
    classified = classification.read()
    classified_codes = (
        set(classified.filter(
            (pl.col("exchange") == "TWSE")
            & (pl.col("classification_status") == "verified")
        )["code"].to_list()) if classified.height else set()
    )
    # Primary OOS is TWSE Verified OOS.  TPEx historical instrument type is
    # blocked, so its experimental coverage cannot block or promote Primary.
    health = evaluate_data_health(
        census_sessions=twse_coverage.observed_trading_sessions,
        census_total_sessions=twse_coverage.expected_trading_sessions,
        twse_codes_observed=len(observed),
        twse_codes_classified=len(set(observed) & classified_codes),
        thresholds=thresholds,
    )
    return replace(health, census_by_exchange={
        "TWSE": twse_coverage.describe(),
        "TPEX": tpex_coverage.describe(),
    })


def price_dataset_capability(health: DataHealth) -> DatasetCapability:
    """Capability record for the daily price/indicator dataset.

    Price observations come from the official daily snapshot, whose
    ``effective_at`` is the session itself — which is why this dataset carries
    the corporate-action-style availability policy rather than a verified
    ``available_at``. Every other dataset must supply its own capability.
    """
    return DatasetCapability(
        dataset="corporate_action",
        available_sessions=health.census_sessions,
        coverage_ratio=health.census_ratio,
        availability_policy="market_mechanism_inferred",
        has_verified_available_at=False,
        revision_stability="append_only",
    )

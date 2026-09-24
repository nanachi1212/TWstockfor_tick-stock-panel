"""Pinned methodology for the sole formal TWSE Primary OOS evaluation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.taiwan.providers.corporate_actions import SOURCE_URLS
from app.taiwan.quant.data_health import ReadinessThresholds
from app.taiwan.quant.feature_manifest import FeatureSpec
from app.taiwan.quant.live_contract import FEATURES, canonical_hash
from app.taiwan.quant.panel import FACTOR_VERSION
from app.taiwan.quant.storage import factor_meta
from app.taiwan.quant.validation.folds import FoldConfig
from app.taiwan.quant_eligibility import PRIMARY_VERIFIED, EligibilityPolicy


@dataclass(frozen=True)
class PrimaryOosEvaluationSpec:
    """Versioned, serializable methodology; change requires a version bump."""

    version: str = "primary-oos-v1"
    factor_version: str = FACTOR_VERSION
    feature_schema_version: str = "live-features-v1"
    policy_version: str = "v1"
    horizons: tuple[int, ...] = (5, 20)
    bucket_fraction: float = 0.2
    ic_method: str = "cross-sectional Spearman rank correlation; average ties"
    universe_policy: EligibilityPolicy = field(default_factory=lambda: EligibilityPolicy(
        version="v1", tier=PRIMARY_VERIFIED, min_warmup_sessions=0, min_adv20_twd=0.0,
    ))
    fold_config: FoldConfig = field(default_factory=lambda: FoldConfig(
        train_sessions=756, val_sessions=126, test_sessions=63, step_sessions=63,
        label_horizon=20, embargo_sessions=5,
    ))

    def __post_init__(self) -> None:
        if self.horizons != (5, 20):
            raise ValueError("formal Primary OOS horizons are fixed at 5D and 20D")
        if self.universe_policy.tier != PRIMARY_VERIFIED:
            raise ValueError("formal OOS requires the Primary Verified universe")
        if self.fold_config.label_horizon < max(self.horizons):
            raise ValueError("walk-forward purge must cover the longest evaluation horizon")
        if not 0 < self.bucket_fraction <= 0.5:
            raise ValueError("bucket_fraction must be in (0, 0.5]")

    @property
    def feature_specs(self) -> tuple[FeatureSpec, ...]:
        return tuple(
            FeatureSpec(
                feature_name=name,
                dataset="corporate_action",
                feature_group="technical",
                required_availability_policy="market_mechanism_inferred",
                required_revision_stability="append_only",
            )
            for name in FEATURES
        )

    def describe(self) -> dict[str, Any]:
        metadata = factor_meta()["factors"]
        if not isinstance(metadata, dict):
            raise RuntimeError("factor metadata has no factor definitions")
        factor_definitions = {name: metadata[name] for name in FEATURES}
        return {
            "spec_version": self.version,
            "factor_panel": {
                "factor_version": self.factor_version,
                "feature_schema_version": self.feature_schema_version,
                "factor_definitions": factor_definitions,
            },
            "composite_scorer": {
                "reference": "app.taiwan.quant.baseline.rank_equal_weight_features",
                "version": "equal-weight-cross-sectional-percentile-v1",
                "features": list(FEATURES),
                "weights": "fixed_equal_weight; no fitted weights",
                "tie_break": "canonical symbol ascending",
            },
            "horizons_sessions": list(self.horizons),
            "universe_policy": self.universe_policy.describe(),
            "pit_policy": {
                "universe": "A2a observed TWSE membership joined to A2b first-observed-date type evidence",
                "features": "usage_scope=pit_feature; adjustment anchored at each feature session close",
                "labels": "separate evaluation-only forward adjusted close returns; never feature inputs",
                "price_source": "A2a observed-universe raw daily observations (includes delisted history)",
            },
            "corporate_action_coverage": {
                "required_status": "verified",
                "required_sources": sorted(SOURCE_URLS),
                "required_interval": "first admitted feature date through latest observed market date",
            },
            "walk_forward": self.fold_config.describe(),
            "bucket": {
                "fraction_per_tail": self.bucket_fraction,
                "size": "ceil(cross_section_count * fraction) per session",
                "sort": "composite score descending, symbol ascending",
            },
            "ic_method": self.ic_method,
            "minimum_data_health": {
                "data_health_thresholds": ReadinessThresholds().__dict__,
                "a2b": "completed_jobs == unique_first_seen_dates; pending=0; failed=0",
                "worker": "no running A2b worker required once queue is complete",
            },
            "reproducibility": {
                "randomness": "none",
                "deterministic_ordering": "date then canonical symbol; stable average ranks",
                "comparison_tolerance": 1e-12,
            },
        }

    @property
    def fingerprint(self) -> str:
        return canonical_hash(self.describe())


PRIMARY_OOS_SPEC = PrimaryOosEvaluationSpec()

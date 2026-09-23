"""Quant framework contracts: feature eligibility, data health, regime, folds.

All offline. These frameworks must be usable while the background census is
still filling, so several tests deliberately assert on *partial* coverage.
"""

from __future__ import annotations

from datetime import date, timedelta
from itertools import pairwise

import polars as pl
import pytest

from app.taiwan.quant.data_health import (
    ReadinessLevel,
    ReadinessThresholds,
    evaluate_data_health,
    price_dataset_capability,
)
from app.taiwan.quant.feature_manifest import (
    DatasetCapability,
    FeatureManifest,
    FeatureSpec,
    RejectionReason,
    resolve_training_eligibility,
    training_matrix,
)
from app.taiwan.quant.regime import (
    INDUSTRY_DEPENDENT_COMPONENTS,
    ComponentStatus,
    Regime,
    RegimeThresholds,
    classify_market_regime,
    market_breadth_above_ma,
)
from app.taiwan.quant.validation.folds import (
    FoldConfig,
    PurgedFold,
    generate_folds,
    iter_fold_blocks,
    leaks_into,
    purged_training_sessions,
)

# ── Feature manifest x capability ──────────────────────────────

PRICE_FEATURE = FeatureSpec(
    feature_name="rsi_14", dataset="corporate_action", feature_group="technical",
    required_history_sessions=252, required_coverage_ratio=0.8,
    required_availability_policy="market_mechanism_inferred",
    required_revision_stability="append_only",
)
REVENUE_FEATURE = FeatureSpec(
    feature_name="revenue_yoy", dataset="monthly_revenue", feature_group="fundamental",
    required_history_sessions=252, required_coverage_ratio=0.8,
    required_revision_stability="stable",
)
MANIFEST = FeatureManifest("v1", (PRICE_FEATURE, REVENUE_FEATURE))


def _price_capability(**overrides) -> DatasetCapability:
    base = {
        "dataset": "corporate_action", "available_sessions": 1000,
        "coverage_ratio": 0.95, "availability_policy": "market_mechanism_inferred",
        "has_verified_available_at": False, "revision_stability": "append_only",
    }
    return DatasetCapability(**{**base, **overrides})


def _revenue_capability(**overrides) -> DatasetCapability:
    base = {
        "dataset": "monthly_revenue", "available_sessions": 1000,
        "coverage_ratio": 0.95, "availability_policy": "exact_timestamp",
        "has_verified_available_at": True, "revision_stability": "stable",
    }
    return DatasetCapability(**{**base, **overrides})


def test_pit_safe_dataset_is_eligible() -> None:
    resolution = resolve_training_eligibility(MANIFEST, {
        "corporate_action": _price_capability(),
        "monthly_revenue": _revenue_capability(),
    })
    assert set(resolution.eligible_features) == {"rsi_14", "revenue_yoy"}
    assert resolution.rejected_features == ()


def test_current_reference_availability_is_rejected() -> None:
    """An alpha feature without a verified available_at cannot train."""
    resolution = resolve_training_eligibility(MANIFEST, {
        "corporate_action": _price_capability(),
        "monthly_revenue": _revenue_capability(
            has_verified_available_at=False,
            availability_policy="market_mechanism_inferred",
        ),
    })
    assert "revenue_yoy" in resolution.rejected_features
    assert RejectionReason.AVAILABILITY_POLICY_NOT_PIT_SAFE in resolution.reasons_for(
        "revenue_yoy")
    # the corporate-action exception does not leak to revenue
    assert "rsi_14" in resolution.eligible_features


def test_insufficient_coverage_is_rejected_with_a_reason() -> None:
    resolution = resolve_training_eligibility(MANIFEST, {
        "corporate_action": _price_capability(coverage_ratio=0.2),
        "monthly_revenue": _revenue_capability(),
    })
    reasons = resolution.reasons_for("rsi_14")
    assert RejectionReason.INSUFFICIENT_COVERAGE in reasons
    detail = next(v for v in resolution.verdicts if v.feature_name == "rsi_14").detail
    assert detail["coverage_ratio"] == 0.2
    assert detail["required_coverage_ratio"] == 0.8


def test_coverage_growth_flips_eligibility_with_no_code_change() -> None:
    """The whole point: no hardcoded TRAINING_ELIGIBLE set."""
    low = resolve_training_eligibility(MANIFEST, {
        "corporate_action": _price_capability(available_sessions=100, coverage_ratio=0.1),
        "monthly_revenue": _revenue_capability(),
    })
    assert "rsi_14" in low.rejected_features

    grown = resolve_training_eligibility(MANIFEST, {
        "corporate_action": _price_capability(available_sessions=900, coverage_ratio=0.9),
        "monthly_revenue": _revenue_capability(),
    })
    assert "rsi_14" in grown.eligible_features


def test_insufficient_history_and_unstable_revision_are_distinct_reasons() -> None:
    resolution = resolve_training_eligibility(MANIFEST, {
        "corporate_action": _price_capability(
            available_sessions=10, revision_stability="current_reference"),
        "monthly_revenue": _revenue_capability(),
    })
    reasons = set(resolution.reasons_for("rsi_14"))
    assert RejectionReason.INSUFFICIENT_HISTORY in reasons
    assert RejectionReason.REVISION_UNSTABLE in reasons


def test_unknown_dataset_is_rejected_not_assumed_fine() -> None:
    resolution = resolve_training_eligibility(MANIFEST, {"corporate_action": _price_capability()})
    assert resolution.reasons_for("revenue_yoy") == (RejectionReason.DATASET_UNKNOWN,)


def test_rejected_feature_cannot_enter_the_training_matrix() -> None:
    frame = pl.DataFrame({
        "symbol": ["2330.TWSE"], "date": [date(2024, 6, 3)],
        "rsi_14": [55.0], "revenue_yoy": [0.12],
    })
    matrix, resolution = training_matrix(frame, MANIFEST, {
        "corporate_action": _price_capability(),
        "monthly_revenue": _revenue_capability(has_verified_available_at=False,
                                               availability_policy=None),
    })
    assert "revenue_yoy" not in matrix.columns
    assert "rsi_14" in matrix.columns
    assert "revenue_yoy" in resolution.rejected_features


def test_resolution_is_deterministic() -> None:
    capabilities = {"corporate_action": _price_capability(),
                    "monthly_revenue": _revenue_capability()}
    first = resolve_training_eligibility(MANIFEST, capabilities).describe()
    second = resolve_training_eligibility(MANIFEST, capabilities).describe()
    assert first == second


# ── Data health readiness ──────────────────────────────────────

def test_partial_coverage_blocks_training_but_not_the_framework() -> None:
    health = evaluate_data_health(
        census_sessions=600, census_total_sessions=3000,
        twse_codes_observed=900, twse_codes_classified=180,
    )
    assert health.is_ready(ReadinessLevel.FRAMEWORK) is True
    assert health.is_ready(ReadinessLevel.FACTOR_COMPUTE) is True
    assert health.is_ready(ReadinessLevel.TRAINING) is False
    assert health.is_ready(ReadinessLevel.PRIMARY_OOS) is False
    assert health.highest_level() is ReadinessLevel.FACTOR_COMPUTE
    assert health.blocked_reasons[ReadinessLevel.TRAINING.value]


def test_early_census_still_allows_framework_work() -> None:
    health = evaluate_data_health(
        census_sessions=11, census_total_sessions=3060,
        twse_codes_observed=911, twse_codes_classified=880,
    )
    assert health.is_ready(ReadinessLevel.FRAMEWORK) is True
    assert health.is_ready(ReadinessLevel.FACTOR_COMPUTE) is False
    assert "252" in " ".join(health.blocked_reasons[ReadinessLevel.FACTOR_COMPUTE.value])


def test_full_coverage_unlocks_primary_oos() -> None:
    health = evaluate_data_health(
        census_sessions=3000, census_total_sessions=3000,
        twse_codes_observed=1000, twse_codes_classified=1000,
    )
    assert health.is_ready(ReadinessLevel.PRIMARY_OOS) is True
    assert health.blocked_reasons == {}
    assert health.highest_level() is ReadinessLevel.PRIMARY_OOS


def test_readiness_is_graded_not_a_single_boolean() -> None:
    health = evaluate_data_health(
        census_sessions=2500, census_total_sessions=3000,
        twse_codes_observed=1000, twse_codes_classified=900,
        thresholds=ReadinessThresholds(),
    )
    described = health.describe()
    assert set(described["levels"]) == {level.value for level in ReadinessLevel}
    assert described["levels"][ReadinessLevel.TRAINING.value] is True
    assert described["levels"][ReadinessLevel.PRIMARY_OOS.value] is False


def test_data_health_feeds_the_eligibility_resolver() -> None:
    health = evaluate_data_health(
        census_sessions=50, census_total_sessions=3000,
        twse_codes_observed=900, twse_codes_classified=10,
    )
    capability = price_dataset_capability(health)
    resolution = resolve_training_eligibility(
        FeatureManifest("v1", (PRICE_FEATURE,)), {"corporate_action": capability})
    assert "rsi_14" in resolution.rejected_features
    assert RejectionReason.INSUFFICIENT_HISTORY in resolution.reasons_for("rsi_14")


# ── Market regime ──────────────────────────────────────────────

def _index_history(n: int, drift: float) -> pl.DataFrame:
    closes, level = [], 10000.0
    for _ in range(n):
        level *= 1.0 + drift
        closes.append(level)
    return pl.DataFrame({
        "date": [date(2024, 1, 1) + timedelta(days=i) for i in range(n)],
        "close": closes,
        "amount": [1.0e11] * n,
    })


def test_uptrend_with_calm_vol_and_strong_breadth_is_risk_on() -> None:
    verdict = classify_market_regime(_index_history(120, 0.002), breadth_above_ma20=0.75)
    assert verdict.regime is Regime.RISK_ON
    assert verdict.score > 0
    trend = next(c for c in verdict.components if c.name == "index_trend")
    assert trend.status is ComponentStatus.OK and trend.vote == 1


def test_downtrend_with_weak_breadth_is_risk_off() -> None:
    verdict = classify_market_regime(_index_history(120, -0.002), breadth_above_ma20=0.2)
    assert verdict.regime is Regime.RISK_OFF
    assert verdict.score < 0


def test_regime_is_deterministic() -> None:
    history = _index_history(120, 0.001)
    first = classify_market_regime(history, breadth_above_ma20=0.5).describe()
    second = classify_market_regime(history, breadth_above_ma20=0.5).describe()
    assert first == second


def test_every_component_reports_value_threshold_status_source_and_asof() -> None:
    verdict = classify_market_regime(_index_history(120, 0.001), breadth_above_ma20=0.5)
    for component in verdict.components:
        described = component.describe()
        assert set(described) >= {"name", "raw_value", "threshold", "status",
                                  "source", "as_of", "vote"}
        assert described["as_of"] is not None


def test_short_history_abstains_instead_of_inventing_a_regime() -> None:
    verdict = classify_market_regime(_index_history(20, 0.001))
    statuses = {c.name: c.status for c in verdict.components}
    assert statuses["index_trend"] is ComponentStatus.DATA_INSUFFICIENT
    assert verdict.regime is Regime.NEUTRAL
    assert verdict.score == 0


def test_industry_components_are_disabled_as_not_pit_safe() -> None:
    """Probe §4.3: no historical industry label exists, so they cannot vote."""
    verdict = classify_market_regime(_index_history(120, 0.002), breadth_above_ma20=0.75)
    disabled = {c.name: c for c in verdict.disabled_components}
    assert set(disabled) == set(INDUSTRY_DEPENDENT_COMPONENTS)
    for component in disabled.values():
        assert component.status is ComponentStatus.NOT_PIT_SAFE
        assert component.vote == 0
        assert component.raw_value is None
        assert "current industry" in (component.note or "")
    # and they are not part of the scored set
    assert not (set(INDUSTRY_DEPENDENT_COMPONENTS)
                & {c.name for c in verdict.components})


def test_breadth_is_none_rather_than_zero_when_unknown() -> None:
    panel = pl.DataFrame({
        "symbol": ["2330.TWSE"], "date": [date(2024, 6, 3)],
        "close": [100.0], "ma20": [None],
    }, schema_overrides={"ma20": pl.Float64})
    assert market_breadth_above_ma(panel, date(2024, 6, 3)) is None

    warm = pl.DataFrame({
        "symbol": ["2330.TWSE", "1101.TWSE"],
        "date": [date(2024, 6, 3)] * 2,
        "close": [100.0, 90.0], "ma20": [95.0, 95.0],
    })
    assert market_breadth_above_ma(warm, date(2024, 6, 3)) == 0.5


def test_thresholds_are_inspectable_policy() -> None:
    gates = RegimeThresholds()
    assert gates.ma_short == 20 and gates.ma_long == 60
    assert gates.breadth_strong > gates.breadth_weak


# ── Purged walk-forward folds ──────────────────────────────────

def _sessions(n: int) -> list[date]:
    """Synthetic trading sessions: weekdays only, so gaps are real."""
    out, day = [], date(2015, 1, 1)
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


SMALL = FoldConfig(train_sessions=50, val_sessions=20, test_sessions=10,
                   step_sessions=10, label_horizon=5, embargo_sessions=3)


def test_folds_are_session_based_not_calendar_based() -> None:
    sessions = _sessions(200)
    folds = generate_folds(sessions, SMALL)
    assert folds
    fold = folds[0]
    # 50 training sessions spans more than 50 calendar days because of weekends
    assert (fold.train_end - fold.train_start).days > SMALL.train_sessions
    assert sessions.index(fold.train_end) - sessions.index(fold.train_start) == 49


def test_purge_and_embargo_open_a_real_gap_at_each_boundary() -> None:
    sessions = _sessions(200)
    fold = generate_folds(sessions, SMALL)[0]
    gap = SMALL.purge_sessions + SMALL.embargo_sessions

    assert sessions.index(fold.val_start) - sessions.index(fold.train_end) == gap + 1
    assert sessions.index(fold.test_start) - sessions.index(fold.val_end) == gap + 1
    assert fold.purge_sessions == SMALL.label_horizon
    assert fold.embargo_sessions == SMALL.embargo_sessions
    assert fold.contains_leak() is False


def test_no_training_label_window_can_reach_validation() -> None:
    """With gap = purge + embargo >= horizon, the geometry itself does the purging."""
    sessions = _sessions(200)
    fold = generate_folds(sessions, SMALL)[0]
    kept = purged_training_sessions(sessions, fold)

    assert kept, "purging must not empty the training block"
    for day in kept:
        assert leaks_into(fold, sessions, day) is False
    # the structural gap already exceeds the horizon, so nothing needs dropping
    whole_block = [d for d in sessions if fold.train_start <= d <= fold.train_end]
    assert kept == whole_block
    # and the very last training row's label lands inside the gap, not in validation
    assert sessions.index(fold.val_start) - sessions.index(fold.train_end) > SMALL.label_horizon


def test_purge_drops_rows_when_a_hand_built_gap_is_too_small() -> None:
    """A fold whose gap is smaller than the horizon must lose its tail."""
    sessions = _sessions(200)
    leaky = PurgedFold(
        index=0,
        train_start=sessions[0], train_end=sessions[49],
        val_start=sessions[51], val_end=sessions[70],
        test_start=sessions[72], test_end=sessions[81],
        purge_sessions=5, embargo_sessions=0, label_horizon=5,
    )
    kept = purged_training_sessions(sessions, leaky)
    dropped = [d for d in sessions
               if leaky.train_start <= d <= leaky.train_end and d not in set(kept)]

    assert dropped, "a 2-session gap cannot absorb a 5-session label window"
    assert all(leaks_into(leaky, sessions, d) for d in dropped)
    assert all(not leaks_into(leaky, sessions, d) for d in kept)


def test_validation_and_test_blocks_never_overlap() -> None:
    sessions = _sessions(400)
    for fold in generate_folds(sessions, SMALL):
        blocks: dict[date, str] = {}
        for block, day in iter_fold_blocks(fold, sessions):
            assert day not in blocks, f"{day} in both {blocks.get(day)} and {block}"
            blocks[day] = block
        assert fold.contains_leak() is False


def test_test_block_is_excluded_from_what_a_model_may_fit_on() -> None:
    sessions = _sessions(200)
    fold = generate_folds(sessions, SMALL)[0]
    train_start, train_end, val_start, val_end = fold.fittable_sessions
    assert val_end < fold.test_start
    fittable = [d for d in sessions
                if train_start <= d <= train_end or val_start <= d <= val_end]
    test_days = [d for d in sessions if fold.test_start <= d <= fold.test_end]
    assert not set(fittable) & set(test_days)


def test_walk_forward_steps_forward_without_reusing_a_test_block() -> None:
    sessions = _sessions(400)
    folds = generate_folds(sessions, SMALL)
    assert len(folds) > 1
    for earlier, later in pairwise(folds):
        assert later.train_start > earlier.train_start
        assert later.test_start > earlier.test_start


def test_short_history_yields_no_folds_rather_than_a_squeezed_one() -> None:
    assert generate_folds(_sessions(40), SMALL) == []
    assert generate_folds([], SMALL) == []


def test_partial_coverage_marks_folds_degraded_instead_of_failing() -> None:
    sessions = _sessions(200)
    folds = generate_folds(sessions, SMALL, min_coverage_sessions=120)
    assert folds
    assert any(f.confidence_degraded for f in folds)
    degraded = next(f for f in folds if f.confidence_degraded)
    assert "verified coverage" in (degraded.degraded_reason or "")
    assert folds[0].confidence_degraded is False


def test_fold_config_rejects_nonsense() -> None:
    with pytest.raises(ValueError, match="train_sessions"):
        FoldConfig(train_sessions=0)
    with pytest.raises(ValueError, match="embargo_sessions"):
        FoldConfig(embargo_sessions=-1)


def test_unsorted_or_duplicate_sessions_are_rejected() -> None:
    sessions = _sessions(200)
    with pytest.raises(ValueError, match="sorted"):
        generate_folds(list(reversed(sessions)), SMALL)
    with pytest.raises(ValueError, match="duplicate"):
        generate_folds([*sessions, sessions[-1]], SMALL)


def test_default_config_matches_the_agreed_geometry() -> None:
    cfg = FoldConfig()
    assert (cfg.train_sessions, cfg.val_sessions, cfg.test_sessions) == (756, 126, 63)
    assert (cfg.step_sessions, cfg.label_horizon, cfg.embargo_sessions) == (63, 5, 5)
    assert cfg.purge_sessions == 5

"""Fold-isolated deterministic ranking dry-run. It never publishes OOS."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TypedDict

import polars as pl

from app.backtest.factor import FactorBacktestService
from app.taiwan.quant.training import TrainingMatrixResult
from app.taiwan.quant.validation.folds import PurgedFold

ALLOWED_GROUPS = frozenset({"technical", "chip", "relative_strength", "market_regime"})


class CompositeRank(TypedDict):
    score: float
    feature_percentiles: dict[str, float]


@dataclass(frozen=True)
class BaselineDryRun:
    ranks: pl.DataFrame
    train_ic: dict[str, float]
    train_icir: dict[str, float | None]
    weights: dict[str, float]
    validation_threshold: float | None
    selected_features: tuple[str, ...]
    usage_scope: str = "experimental_only"
    status: str = "dry_run_only"


def deterministic_percentiles(values: dict[str, float]) -> dict[str, float]:
    ordered = sorted(values, key=lambda symbol: (values[symbol], symbol))
    return {symbol: (index + 1) / len(ordered) for index, symbol in enumerate(ordered)}


def rank_equal_weight_features(
    rows: list[dict[str, object]], features: tuple[str, ...],
) -> dict[str, CompositeRank]:
    """Apply the live deterministic equal-weight percentile scorer to one day."""
    if not features:
        raise ValueError("composite scorer requires at least one feature")
    symbols: set[str] = set()
    values: dict[str, dict[str, float]] = {feature: {} for feature in features}
    for row in rows:
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or symbol in symbols:
            raise ValueError("composite scorer requires unique string symbols")
        symbols.add(symbol)
        for feature in features:
            value = row.get(feature)
            if (not isinstance(value, int | float) or isinstance(value, bool)
                    or not math.isfinite(float(value))):
                raise ValueError(f"composite scorer requires finite {feature}")
            values[feature][symbol] = float(value)
    percentiles = {feature: deterministic_percentiles(values[feature]) for feature in features}
    return {
        symbol: {
            "score": sum(percentiles[feature][symbol] for feature in features) / len(features),
            "feature_percentiles": {
                feature: percentiles[feature][symbol] for feature in features
            },
        }
        for symbol in sorted(symbols)
    }


def run_baseline_dry_run(
    admission: TrainingMatrixResult, fold: PurgedFold, *,
    factor_groups: dict[str, str], label_col: str = "forward_return",
    thresholds: tuple[float, ...] = (0.5, 0.6, 0.7),
    primary_ready: bool = False,
) -> BaselineDryRun:
    """Fit IC/ICIR on train, choose threshold on validation, rank untouched test.

    ``primary_ready`` does not enable publication. This primitive is always an
    in-memory experimental dry-run, including when given a synthetic primary
    universe. Any formal Primary OOS path must be a separate, future contract.
    """
    del primary_ready
    if not isinstance(admission, TrainingMatrixResult):
        raise TypeError("baseline requires an admitted training matrix")
    if not set(factor_groups) <= set(admission.resolution.eligible_features):
        raise ValueError("baseline factor rejected by capability x feature manifest")
    matrix = admission.matrix
    required = {"symbol", "date", "factor_version", "policy_version", "universe_tier",
                "feature_schema_version", label_col, *factor_groups}
    if not required <= set(matrix.columns):
        raise ValueError("baseline requires eligible training matrix and realized labels")
    if not factor_groups or any(group not in ALLOWED_GROUPS for group in factor_groups.values()):
        raise ValueError("industry/fundamental or unknown baseline group is disabled")
    if not thresholds or any(not 0 < threshold < 1 for threshold in thresholds):
        raise ValueError("validation thresholds must be within (0,1)")
    for col in ("factor_version", "policy_version", "universe_tier", "feature_schema_version"):
        if matrix[col].n_unique() != 1:
            raise ValueError(f"baseline cannot mix {col}")
    train = matrix.filter(pl.col("date").is_between(fold.train_start, fold.train_end))
    validation = matrix.filter(pl.col("date").is_between(fold.val_start, fold.val_end))
    test = matrix.filter(pl.col("date").is_between(fold.test_start, fold.test_end))
    train_ic: dict[str, float] = {}
    train_icir: dict[str, float | None] = {}
    strengths: dict[str, float] = {}
    for feature in sorted(factor_groups):
        sliced = train.filter(pl.col(feature).is_not_null() & pl.col(label_col).is_not_null())
        if not sliced.height:
            continue
        ic_frame = FactorBacktestService._calc_ic(
            sliced.rename({label_col: "_next_return"}), feature)
        ics = [float(x) for x in ic_frame["ic"].to_list() if x is not None and math.isfinite(x)]
        if not ics:
            continue
        mean = sum(ics) / len(ics)
        std = math.sqrt(sum((x - mean) ** 2 for x in ics) / len(ics))
        icir = mean / std if std > 1e-12 else None
        train_ic[feature] = mean
        train_icir[feature] = icir
        if mean > 0:
            strengths[feature] = max(0.0, icir) if icir is not None else mean
    total = sum(strengths.values())
    weights = {feature: value / total for feature, value in strengths.items() if value > 0} if total else {}

    def score_block(block: pl.DataFrame) -> pl.DataFrame:
        scored = []
        for day in block["date"].unique().sort().to_list():
            rows = block.filter(pl.col("date") == day).sort("symbol").to_dicts()
            pct = {}
            for feature in weights:
                available = {row["symbol"]: float(row[feature]) for row in rows
                             if row[feature] is not None and math.isfinite(row[feature])}
                pct[feature] = deterministic_percentiles(available) if available else {}
            for row in rows:
                symbol = row["symbol"]
                groups = {}
                for group in sorted(set(factor_groups.values())):
                    items = [(weights[f], pct[f][symbol]) for f in weights
                             if factor_groups[f] == group and symbol in pct[f]]
                    groups[group] = (sum(w * p for w, p in items) / sum(w for w, _ in items)
                                     if items else None)
                items = [(weights[f], pct[f][symbol]) for f in weights if symbol in pct[f]]
                overall = sum(w * p for w, p in items) / sum(w for w, _ in items) if items else None
                scored.append({
                    "date": day, "symbol": symbol, "group_percentiles": groups,
                    "overall_rank": overall, "eligible_count": len(rows),
                    "factor_version": row["factor_version"], "policy_version": row["policy_version"],
                    "universe_tier": row["universe_tier"], "feature_schema_version": row["feature_schema_version"],
                    "fold_index": fold.index, "status": "ranked" if overall is not None else "data_insufficient",
                    "usage_scope": "experimental_only", label_col: row[label_col],
                })
        return pl.DataFrame(scored) if scored else pl.DataFrame()

    scored_val = score_block(validation)
    best_threshold = None
    best_mean = float("-inf")
    for threshold in sorted(set(thresholds)):
        if scored_val.is_empty():
            break
        selected = scored_val.filter((pl.col("overall_rank") >= threshold) &
                                     pl.col(label_col).is_not_null())
        if not selected.height:
            continue
        mean = float(selected[label_col].mean())
        if mean > best_mean:
            best_mean, best_threshold = mean, threshold
    ranked = score_block(test)
    if not ranked.is_empty():
        ranked = ranked.drop(label_col).with_columns(
            pl.lit(best_threshold).cast(pl.Float64).alias("validation_threshold"))
    return BaselineDryRun(ranked, train_ic, train_icir, weights, best_threshold,
                          tuple(sorted(weights)))

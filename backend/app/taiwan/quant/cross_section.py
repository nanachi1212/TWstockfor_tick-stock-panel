"""Deterministic transforms over an already policy-eligible daily population."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import polars as pl

from app.taiwan.quant.training import TrainingMatrixResult


@dataclass(frozen=True)
class CrossSection:
    values: pl.DataFrame
    date: date
    factor: str
    policy_version: str
    universe_tier: str
    eligible_count: int


def _quantile(sorted_values: list[float], p: float) -> float:
    position = p * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (position - lower)


def transform_cross_section(admission: TrainingMatrixResult, *, day: date, factor: str,
                            policy_version: str, universe_tier: str,
                            winsor_tail: float = 0.01) -> CrossSection:
    """Winsorize, population z-score and stable percentile in the eligible set.

    ``admission`` must be the output of ``panel_training_matrix``. Missing factor
    values stay null and never enter the numeric transform population.
    """
    if not isinstance(admission, TrainingMatrixResult):
        raise TypeError("cross-section requires an admitted training matrix")
    if factor not in admission.resolution.eligible_features:
        raise ValueError("factor was rejected by capability x feature manifest")
    matrix = admission.matrix
    if not 0 <= winsor_tail < 0.5:
        raise ValueError("winsor_tail must be in [0, .5)")
    required = {"date", "symbol", "policy_version", "universe_tier", "feature_schema_version", factor}
    if not required <= set(matrix.columns):
        raise ValueError("cross-section requires eligible training matrix and factor")
    if matrix.filter((pl.col("policy_version") != policy_version) |
                     (pl.col("universe_tier") != universe_tier)).height:
        raise ValueError("cross-section cannot mix policy or universe tiers")
    rows = matrix.filter(pl.col("date") == day).sort("symbol").to_dicts()
    if len({row["symbol"] for row in rows}) != len(rows):
        raise ValueError("duplicate eligible symbol/date")
    numeric = []
    for row in rows:
        value = row[factor]
        if value is not None and not math.isfinite(value):
            raise ValueError("factor contains non-finite values")
        if value is not None:
            numeric.append(float(value))
    sorted_values = sorted(numeric)
    if sorted_values:
        low, high = (_quantile(sorted_values, winsor_tail),
                     _quantile(sorted_values, 1 - winsor_tail))
    else:
        low = high = None
    clipped = {row["symbol"]: min(max(float(row[factor]), low), high)
               for row in rows if row[factor] is not None}
    mean = sum(clipped.values()) / len(clipped) if clipped else None
    std = (math.sqrt(sum((v - mean) ** 2 for v in clipped.values()) / len(clipped))
           if clipped else None)
    order = sorted(clipped, key=lambda symbol: (clipped[symbol], symbol))
    rank = {symbol: (i + 1) / len(order) for i, symbol in enumerate(order)}
    output = [{
        "date": day, "symbol": row["symbol"], "factor": factor,
        "winsorized": clipped.get(row["symbol"]),
        "zscore": (clipped[row["symbol"]] - mean) / std
        if row["symbol"] in clipped and std and std > 0 else None,
        "rank_pct": rank.get(row["symbol"]),
        "eligible_count": len(rows), "policy_version": policy_version,
        "universe_tier": universe_tier,
    } for row in rows]
    return CrossSection(pl.DataFrame(output) if output else pl.DataFrame(),
                        day, factor, policy_version, universe_tier, len(rows))


def industry_neutralize(*_: object, **__: object) -> None:
    """V1 historical industry assignments cannot pass a PIT evidence check."""
    raise ValueError("not_pit_safe: historical industry assignment is unavailable")

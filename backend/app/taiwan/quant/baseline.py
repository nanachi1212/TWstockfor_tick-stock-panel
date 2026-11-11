"""Deterministic equal-weight percentile scorer shared by live and evaluation runs."""
from __future__ import annotations

import math
from typing import TypedDict


class CompositeRank(TypedDict):
    score: float
    feature_percentiles: dict[str, float]


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

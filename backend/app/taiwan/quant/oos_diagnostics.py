"""Deterministic diagnostics for an immutable Primary OOS run.

This module does not fit weights or change the production scorer. It rebuilds
descriptive views from the formal OOS rows and their evaluation-only labels.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from statistics import median
from typing import Any, cast

import polars as pl

from app.taiwan.adjust import forward_adjusted_return
from app.taiwan.corporate_actions import (
    PRICE_SUPPORT,
    CorporateActionEvent,
    resolve_event_conflicts,
)
from app.taiwan.quant.evaluation import ActionCoverage, _spearman
from app.taiwan.quant.live_contract import FEATURES

BUCKET_COUNT = 5


@dataclass(frozen=True)
class OosFold:
    """The immutable test interval from one formal walk-forward fold."""

    index: int
    test_start: date
    test_end: date


def _validate_sessions(sessions: Sequence[date]) -> dict[date, int]:
    ordered = list(sessions)
    if ordered != sorted(ordered) or len(ordered) != len(set(ordered)):
        raise ValueError("sessions must be sorted and unique")
    return {day: index for index, day in enumerate(ordered)}


def _events_by_symbol(
    events: Iterable[CorporateActionEvent],
) -> dict[str, tuple[list[date], list[CorporateActionEvent]]]:
    """Keep the canonical event order for exact interval multiplication."""
    grouped: dict[str, list[CorporateActionEvent]] = {}
    for event in resolve_event_conflicts(events):
        grouped.setdefault(event.symbol, []).append(event)
    return {
        symbol: ([event.effective_date for event in rows], rows)
        for symbol, rows in grouped.items()
    }


def _interval_event_factor(
    grouped: Mapping[str, tuple[list[date], list[CorporateActionEvent]]],
    symbol: str,
    start: date,
    end: date,
) -> float | None:
    record = grouped.get(symbol)
    if record is None:
        return 1.0
    days, events = record
    first = bisect_right(days, start)
    last = bisect_right(days, end)
    factor = 1.0
    for event in events[first:last]:
        supported = "close" in PRICE_SUPPORT.get(
            (event.exchange, event.event_type), frozenset()
        )
        if (
            event.status != "verified"
            or not supported
            or event.factor is None
            or not math.isfinite(event.factor)
            or event.factor <= 0
        ):
            return None
        factor *= event.factor
    return factor


def build_forward_labels_fast(
    keys: pl.DataFrame,
    daily: pl.DataFrame,
    *,
    sessions: Sequence[date],
    events: Iterable[CorporateActionEvent],
    action_coverage: ActionCoverage,
    horizons: tuple[int, ...] = (5, 20),
) -> pl.DataFrame:
    """Build labels with the same close-normalization contract as the formal run.

    The formal implementation calls ``forward_adjusted_return`` once per row.
    This implementation multiplies the same canonical interval events directly.
    It exists only to make post-run diagnostics practical on the 1.8M-row OOS set.
    """
    if not {"date", "symbol"} <= set(keys.columns):
        raise ValueError("label keys require date and symbol")
    if keys.schema["date"] != pl.Date:
        raise ValueError("label key dates must use Date")
    if keys.select(pl.struct("date", "symbol").is_duplicated().any()).item():
        raise ValueError("label keys must be unique")
    if not {"date", "symbol", "close"} <= set(daily.columns):
        raise ValueError("daily prices require date, symbol and close")
    if daily.schema["date"] != pl.Date:
        raise ValueError("daily dates must use Date")
    if daily.select(pl.struct("date", "symbol").is_duplicated().any()).item():
        raise ValueError("daily prices must be unique")
    if not horizons or any(type(value) is not int or value <= 0 for value in horizons):
        raise ValueError("horizons must be positive integers")
    if len(horizons) != len(set(horizons)):
        raise ValueError("horizons must be unique")

    position = _validate_sessions(sessions)
    ordered_sessions = list(sessions)
    end_maps: dict[int, dict[date, date]] = {}
    for horizon in horizons:
        end_maps[horizon] = {
            day: ordered_sessions[index + horizon]
            for day, index in position.items()
            if index + horizon < len(ordered_sessions)
        }
    close_by_key = {
        (row["symbol"], row["date"]): row["close"]
        for row in daily.select("symbol", "date", "close").iter_rows(named=True)
    }
    events_by_symbol = _events_by_symbol(events)
    rows: list[dict[str, Any]] = []
    for key in keys.select("date", "symbol").sort(["date", "symbol"]).iter_rows(named=True):
        start = key["date"]
        symbol = key["symbol"]
        row: dict[str, Any] = {"date": start, "symbol": symbol}
        for horizon in horizons:
            value_column = f"forward_return_{horizon}d"
            status_column = f"label_status_{horizon}d"
            end_column = f"label_end_{horizon}d"
            row[value_column] = None
            end = end_maps[horizon].get(start)
            row[end_column] = end
            if start not in position:
                row[status_column] = "data_insufficient"
                continue
            if end is None:
                row[status_column] = "pending"
                continue
            if not action_coverage.covers(start, end):
                row[status_column] = "corporate_action_coverage_unavailable"
                continue
            expected_sessions = ordered_sessions[position[start]:position[start] + horizon + 1]
            closes = [close_by_key.get((symbol, day)) for day in expected_sessions]
            numeric_closes = [float(value) for value in closes if value is not None]
            if len(numeric_closes) != len(closes) or any(
                not math.isfinite(value) or value <= 0 for value in numeric_closes
            ):
                row[status_column] = "missing_session_price"
                continue
            interval_factor = _interval_event_factor(events_by_symbol, symbol, start, end)
            if interval_factor is None:
                row[status_column] = "data_insufficient"
                continue
            row[value_column] = (
                numeric_closes[-1] / (numeric_closes[0] * interval_factor) - 1.0
            )
            row[status_column] = "verified"
        rows.append(row)
    schema: dict[str, Any] = {"date": pl.Date, "symbol": pl.String}
    for horizon in horizons:
        schema[f"forward_return_{horizon}d"] = pl.Float64
        schema[f"label_status_{horizon}d"] = pl.String
        schema[f"label_end_{horizon}d"] = pl.Date
    return pl.DataFrame(rows, schema=schema).sort(["date", "symbol"])


def validate_fast_labels_sample(
    keys: pl.DataFrame,
    labels: pl.DataFrame,
    daily: pl.DataFrame,
    *,
    sessions: Sequence[date],
    events: Iterable[CorporateActionEvent],
    horizons: tuple[int, ...] = (5, 20),
    sample_size: int = 24,
) -> None:
    """Compare a deterministic sample with the authoritative label primitive."""
    if sample_size <= 0:
        raise ValueError("sample_size must be positive")
    event_rows = tuple(events)
    by_symbol: dict[str, tuple[CorporateActionEvent, ...]] = {}
    for symbol in keys["symbol"].unique().sort().to_list():
        by_symbol[symbol] = tuple(event for event in event_rows if event.symbol == symbol)
    daily_by_symbol = {}
    for frame in daily.partition_by("symbol", maintain_order=True):
        daily_by_symbol[frame["symbol"][0]] = frame.sort("date")
    sampled = keys.sort(["date", "symbol"]).gather_every(
        max(1, math.ceil(keys.height / sample_size))
    ).head(sample_size)
    actual = labels.join(sampled, on=["date", "symbol"], how="semi")
    position = _validate_sessions(sessions)
    for row in actual.iter_rows(named=True):
        start = row["date"]
        symbol = row["symbol"]
        for horizon in horizons:
            expected_status = row[f"label_status_{horizon}d"]
            end_index = position.get(start, len(sessions)) + horizon
            if end_index >= len(sessions):
                if expected_status != "pending":
                    raise ValueError("fast label pending status differs from formal contract")
                continue
            end = sessions[end_index]
            history = daily_by_symbol[symbol].filter(pl.col("date").is_between(start, end))
            result = forward_adjusted_return(
                history,
                start_session=start,
                horizon_sessions=horizon,
                events=by_symbol[symbol],
            )
            if result.status != expected_status:
                raise ValueError("fast label status differs from formal contract")
            fast_value = row[f"forward_return_{horizon}d"]
            if result.value is not None and not math.isclose(
                result.value, fast_value, rel_tol=0.0, abs_tol=1e-12
            ):
                raise ValueError("fast label value differs from formal contract")


def _summary(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "mean": None,
            "median": None,
            "std": None,
            "icir": None,
            "positive_ratio": None,
            "n_dates": 0,
        }
    mean = sum(values) / len(values)
    std = math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))
    return {
        "mean": mean,
        "median": median(values),
        "std": std,
        "icir": mean / std if std > 1e-12 else None,
        "positive_ratio": sum(value > 0 for value in values) / len(values),
        "n_dates": len(values),
    }


def _rank_percentiles(frame: pl.DataFrame, feature: str) -> pl.DataFrame:
    ranked = frame.select("date", "symbol", feature).sort(["date", feature, "symbol"])
    ranked = ranked.with_columns(
        pl.int_range(1, pl.len() + 1).over("date").alias("_rank"),
        pl.len().over("date").alias("_count"),
    )
    percentiles = [
        rank / count
        for rank, count in zip(ranked["_rank"].to_list(), ranked["_count"].to_list(), strict=True)
    ]
    return ranked.select("date", "symbol").with_columns(
        pl.Series(f"{feature}_pct", percentiles, dtype=pl.Float64)
    )


def score_variants(
    matrix: pl.DataFrame,
    *,
    features: tuple[str, ...] = FEATURES,
) -> tuple[pl.DataFrame, dict[str, tuple[str, ...]]]:
    """Score full, leave-one-out, and single-factor variants on one fixed universe."""
    missing = sorted({"date", "symbol", *features} - set(matrix.columns))
    if missing:
        raise ValueError(f"diagnostic matrix is missing columns: {missing}")
    complete = matrix.filter(
        pl.all_horizontal(
            pl.col(feature).is_not_null() & pl.col(feature).is_finite()
            for feature in features
        )
    ).sort(["date", "symbol"])
    scored = complete
    for feature in features:
        scored = scored.join(_rank_percentiles(complete, feature), on=["date", "symbol"])
    variants: dict[str, tuple[str, ...]] = {"full": features}
    variants.update(
        {f"minus_{feature}": tuple(item for item in features if item != feature)
         for feature in features}
    )
    variants.update({f"single_{feature}": (feature,) for feature in features})
    expressions: list[pl.Series] = []
    for name, selected in variants.items():
        columns = [scored[f"{feature}_pct"].to_list() for feature in selected]
        expressions.append(pl.Series(
            f"score__{name}",
            [sum(values) / len(selected) for values in zip(*columns, strict=True)],
            dtype=pl.Float64,
        ))
    return scored.with_columns(*expressions), variants


def _daily_ic(frame: pl.DataFrame, signal: str, value: str) -> dict[date, float]:
    result: dict[date, float] = {}
    usable = frame.filter(
        pl.col(signal).is_not_null()
        & pl.col(signal).is_finite()
        & pl.col(value).is_not_null()
        & pl.col(value).is_finite()
    )
    for section in usable.partition_by("date", maintain_order=True):
        ic = _spearman(section[signal].to_list(), section[value].to_list())
        if ic is not None:
            result[section["date"][0]] = ic
    return result


def _bucket_rows(
    frame: pl.DataFrame,
    *,
    signal: str,
    value: str,
    benchmark_by_date: Mapping[date, float],
    bucket_count: int,
) -> tuple[list[dict[str, Any]], dict[date, dict[str, float]]]:
    output: list[dict[str, Any]] = []
    daily_tails: dict[date, dict[str, float]] = {}
    usable = frame.filter(
        pl.col(signal).is_not_null()
        & pl.col(signal).is_finite()
        & pl.col(value).is_not_null()
        & pl.col(value).is_finite()
    )
    for section in usable.partition_by("date", maintain_order=True):
        day = section["date"][0]
        rows = section.sort([signal, "symbol"]).to_dicts()
        count = len(rows)
        if count < bucket_count:
            continue
        universe_mean = sum(float(row[value]) for row in rows) / count
        buckets: list[list[float]] = [[] for _ in range(bucket_count)]
        for index, row in enumerate(rows):
            bucket = min(index * bucket_count // count, bucket_count - 1)
            buckets[bucket].append(float(row[value]))
        benchmark = benchmark_by_date.get(day)
        for index, values in enumerate(buckets, start=1):
            bucket_mean = sum(values) / len(values)
            output.append({
                "date": day,
                "bucket": index,
                "mean_return": bucket_mean,
                "median_return": median(values),
                "observations": len(values),
                "universe_relative_return": bucket_mean - universe_mean,
                "benchmark_relative_return": (
                    bucket_mean - benchmark if benchmark is not None else None
                ),
            })
        tail_size = max(1, math.ceil(count / bucket_count))
        formal_rows = section.sort(
            [signal, "symbol"], descending=[True, False]
        ).to_dicts()
        top = sum(float(row[value]) for row in formal_rows[:tail_size]) / tail_size
        bottom = sum(float(row[value]) for row in formal_rows[-tail_size:]) / tail_size
        daily_tails[day] = {
            "top": top,
            "bottom": bottom,
            "universe": universe_mean,
            "benchmark": benchmark if benchmark is not None else math.nan,
        }
    return output, daily_tails


def _aggregate_buckets(rows: Sequence[dict[str, Any]], bucket_count: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for bucket in range(1, bucket_count + 1):
        selected = [row for row in rows if row["bucket"] == bucket]
        benchmark = [row["benchmark_relative_return"] for row in selected
                     if row["benchmark_relative_return"] is not None]
        result.append({
            "bucket": bucket,
            "mean_forward_return": (
                sum(row["mean_return"] for row in selected) / len(selected) if selected else None
            ),
            "median_forward_return": (
                median(row["median_return"] for row in selected) if selected else None
            ),
            "observation_count": sum(row["observations"] for row in selected),
            "universe_relative_return": (
                sum(row["universe_relative_return"] for row in selected) / len(selected)
                if selected else None
            ),
            "benchmark_relative_return": (
                sum(benchmark) / len(benchmark) if benchmark else None
            ),
            "n_dates": len(selected),
        })
    return result


def _bucket_shape(rows: Sequence[dict[str, Any]], bucket_count: int) -> dict[str, Any]:
    by_date: dict[date, list[dict[str, Any]]] = {}
    for row in rows:
        by_date.setdefault(row["date"], []).append(row)
    strict = 0
    top_best = 0
    bottom_worst = 0
    positive_steps = 0
    usable_dates = 0
    for day_rows in by_date.values():
        ordered = sorted(day_rows, key=lambda row: row["bucket"])
        if len(ordered) != bucket_count:
            continue
        values = [row["mean_return"] for row in ordered]
        steps = [right > left for left, right in pairwise(values)]
        strict += all(steps)
        top_best += values[-1] == max(values)
        bottom_worst += values[0] == min(values)
        positive_steps += sum(steps)
        usable_dates += 1
    return {
        "strict_monotonic_ratio": strict / usable_dates if usable_dates else None,
        "top_is_best_ratio": top_best / usable_dates if usable_dates else None,
        "bottom_is_worst_ratio": bottom_worst / usable_dates if usable_dates else None,
        "mean_positive_steps": (
            positive_steps / usable_dates if usable_dates else None
        ),
        "max_steps": bucket_count - 1,
        "n_dates": usable_dates,
    }


def _tail_summary(tails: Mapping[date, Mapping[str, float]]) -> dict[str, Any]:
    if not tails:
        return {"top": None, "bottom": None, "long_short": None, "n_dates": 0}
    values = list(tails.values())
    top = sum(row["top"] for row in values) / len(values)
    bottom = sum(row["bottom"] for row in values) / len(values)
    universe = sum(row["universe"] for row in values) / len(values)
    paired = [row for row in values if math.isfinite(row["benchmark"])]
    benchmark = sum(row["benchmark"] for row in paired) / len(paired) if paired else None
    return {
        "top": top,
        "bottom": bottom,
        "long_short": top - bottom,
        "universe": universe,
        "top_vs_universe": top - universe,
        "bottom_vs_universe": bottom - universe,
        "benchmark": benchmark,
        "top_vs_benchmark": (
            sum(row["top"] - row["benchmark"] for row in paired) / len(paired)
            if paired else None
        ),
        "bottom_vs_benchmark": (
            sum(row["bottom"] - row["benchmark"] for row in paired) / len(paired)
            if paired else None
        ),
        "universe_vs_benchmark": (
            sum(row["universe"] - row["benchmark"] for row in paired) / len(paired)
            if paired else None
        ),
        "top_mean_minus_benchmark_mean": top - benchmark if benchmark is not None else None,
        "n_dates": len(values),
        "benchmark_n_dates": len(paired),
    }


def _period_rows(
    daily_ic: Mapping[date, float],
    tails: Mapping[date, Mapping[str, float]],
    period_by_date: Mapping[date, int],
) -> list[dict[str, Any]]:
    periods = sorted(set(period_by_date.values()))
    result: list[dict[str, Any]] = []
    for period in periods:
        dates = {day for day, value in period_by_date.items() if value == period}
        ic_values = [value for day, value in daily_ic.items() if day in dates]
        period_tails = {day: value for day, value in tails.items() if day in dates}
        result.append({"period": period, "ic": _summary(ic_values), **_tail_summary(period_tails)})
    return result


def _factor_correlations(matrix: pl.DataFrame, features: tuple[str, ...]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    sections = matrix.partition_by("date", maintain_order=True)
    for left_index, left in enumerate(features):
        for right in features[left_index + 1:]:
            values = []
            for section in sections:
                value = _spearman(section[left].to_list(), section[right].to_list())
                if value is not None:
                    values.append(value)
            output.append({"left": left, "right": right, **_summary(values)})
    return output


def _integrity(
    matrix: pl.DataFrame,
    scored: pl.DataFrame,
    labels: pl.DataFrame,
    *,
    features: tuple[str, ...],
    horizons: tuple[int, ...],
) -> dict[str, Any]:
    full_column = "score__full"
    duplicate_rows = 0
    total_rows = 0
    daily_counts: list[int] = []
    for section in scored.partition_by("date", maintain_order=True):
        counts = section.group_by(full_column).len()
        duplicate_rows += int(counts.filter(pl.col("len") > 1)["len"].sum() or 0)
        total_rows += section.height
        daily_counts.append(section.height)
    missing = {
        feature: int(matrix.filter(pl.col(feature).is_null() | ~pl.col(feature).is_finite()).height)
        for feature in features
    }
    own_vs_complete: dict[str, Any] = {}
    joined_all = matrix.join(labels, on=["date", "symbol"], how="left")
    joined_complete = scored.join(labels, on=["date", "symbol"], how="left")
    for feature in features:
        own_vs_complete[feature] = {}
        for horizon in horizons:
            value = f"forward_return_{horizon}d"
            status = f"label_status_{horizon}d"
            own_ic = _daily_ic(joined_all.filter(pl.col(status) == "verified"), feature, value)
            complete_ic = _daily_ic(
                joined_complete.filter(pl.col(status) == "verified"), feature, value
            )
            own_mean = _summary(list(own_ic.values()))["mean"]
            complete_mean = _summary(list(complete_ic.values()))["mean"]
            own_vs_complete[feature][str(horizon)] = {
                "own_available": own_mean,
                "complete_case": complete_mean,
                "delta": (
                    complete_mean - own_mean
                    if isinstance(own_mean, float) and isinstance(complete_mean, float)
                    else None
                ),
            }
    return {
        "admitted_rows": matrix.height,
        "complete_case_rows": scored.height,
        "excluded_incomplete_rows": matrix.height - scored.height,
        "excluded_incomplete_ratio": (
            (matrix.height - scored.height) / matrix.height if matrix.height else None
        ),
        "missing_by_factor": missing,
        "score_tie_rows": duplicate_rows,
        "score_tie_ratio": duplicate_rows / total_rows if total_rows else None,
        "daily_population_min": min(daily_counts) if daily_counts else None,
        "daily_population_max": max(daily_counts) if daily_counts else None,
        "daily_population_mean": sum(daily_counts) / len(daily_counts) if daily_counts else None,
        "winsorization": "none",
        "clipping": "none",
        "normalization": "daily_cross_sectional_ordinal_percentile_symbol_tie_break",
        "missing_selection_effect": own_vs_complete,
        "factor_redundancy": _factor_correlations(scored, features),
        "industry_concentration": {
            "status": "data_insufficient",
            "reason": "formal Primary OOS has no PIT historical industry labels",
        },
        "market_cap_concentration": {
            "status": "data_insufficient",
            "reason": "formal Primary OOS has no PIT historical share-count series",
        },
    }


def _price_liquidity_concentration(
    frame: pl.DataFrame,
    *,
    horizons: tuple[int, ...],
) -> dict[str, Any]:
    if not {"close", "adv20_twd"} <= set(frame.columns):
        return {"status": "data_insufficient", "reason": "close or adv20_twd is unavailable"}
    result: dict[str, Any] = {"status": "available"}
    for horizon in horizons:
        status = f"label_status_{horizon}d"
        value = f"forward_return_{horizon}d"
        usable = frame.filter(
            (pl.col(status) == "verified")
            & pl.col(value).is_not_null()
            & pl.col("close").is_not_null()
            & pl.col("adv20_twd").is_not_null()
        )
        rows = []
        for section in usable.partition_by("date", maintain_order=True):
            count = section.height
            if count < BUCKET_COUNT:
                continue
            top_size = max(1, math.ceil(count / BUCKET_COUNT))
            top = section.sort(["score__full", "symbol"], descending=[True, False]).head(top_size)
            price_cut = section["close"].quantile(0.8, interpolation="nearest")
            liquidity_cut = section["adv20_twd"].quantile(0.8, interpolation="nearest")
            rows.append({
                "median_close": float(cast(float, top["close"].median())),
                "median_adv20_twd": float(cast(float, top["adv20_twd"].median())),
                "upper_price_quintile_ratio": float(
                    cast(float, (top["close"] >= price_cut).mean())
                ),
                "upper_liquidity_quintile_ratio": float(
                    cast(float, (top["adv20_twd"] >= liquidity_cut).mean())
                ),
            })
        result[str(horizon)] = {
            key: sum(row[key] for row in rows) / len(rows) if rows else None
            for key in (
                "median_close",
                "median_adv20_twd",
                "upper_price_quintile_ratio",
                "upper_liquidity_quintile_ratio",
            )
        }
        result[str(horizon)]["n_dates"] = len(rows)
    return result


def diagnose_oos(
    matrix: pl.DataFrame,
    labels: pl.DataFrame,
    benchmark_labels: pl.DataFrame,
    *,
    folds: Sequence[OosFold],
    auxiliary: pl.DataFrame | None = None,
    features: tuple[str, ...] = FEATURES,
    horizons: tuple[int, ...] = (5, 20),
    bucket_count: int = BUCKET_COUNT,
) -> dict[str, Any]:
    """Create factor, bucket, ablation, stability, and integrity diagnostics."""
    if bucket_count < 2:
        raise ValueError("bucket_count must be at least two")
    scored, variants = score_variants(matrix, features=features)
    joined = scored.join(labels, on=["date", "symbol"], how="left")
    factor_joined = matrix.join(labels, on=["date", "symbol"], how="left")
    if auxiliary is not None:
        joined = joined.join(auxiliary, on=["date", "symbol"], how="left")
    year_by_date = {day: day.year for day in scored["date"].unique().to_list()}
    fold_by_date = {
        day: fold.index
        for fold in folds
        for day in scored["date"].unique().to_list()
        if fold.test_start <= day <= fold.test_end
    }
    output: dict[str, Any] = {
        "contract": {
            "features": list(features),
            "expected_direction": {feature: "higher_is_better" for feature in features},
            "normalization": "daily cross-sectional ordinal percentile",
            "winsorization": "none",
            "missing_values": "complete-case across all formal factors for composite and variants",
            "aggregation": "fixed equal-weight arithmetic mean",
            "scoring_direction": "higher composite score predicts higher forward return",
            "bucket_assignment": f"{bucket_count} deterministic equal-population rank buckets",
        },
        "factor_ic": {},
        "signals": {},
    }
    benchmark_by_horizon: dict[int, dict[date, float]] = {}
    for horizon in horizons:
        benchmark_by_horizon[horizon] = {
            row["date"]: float(row[f"forward_return_{horizon}d"])
            for row in benchmark_labels.filter(
                pl.col(f"label_status_{horizon}d") == "verified"
            ).iter_rows(named=True)
        }
    for feature in features:
        output["factor_ic"][feature] = {
            "expected_direction": "higher_is_better",
            "raw": {},
            "direction_adjusted": {},
        }
        for horizon in horizons:
            status = f"label_status_{horizon}d"
            value = f"forward_return_{horizon}d"
            valid = factor_joined.filter(pl.col(status) == "verified")
            daily = _daily_ic(valid, feature, value)
            summary = _summary(list(daily.values()))
            bucket_rows, tails = _bucket_rows(
                valid,
                signal=feature,
                value=value,
                benchmark_by_date=benchmark_by_horizon[horizon],
                bucket_count=bucket_count,
            )
            output["factor_ic"][feature]["raw"][str(horizon)] = summary
            output["factor_ic"][feature]["direction_adjusted"][str(horizon)] = summary
            output["factor_ic"][feature].setdefault("buckets", {})[str(horizon)] = (
                _aggregate_buckets(bucket_rows, bucket_count)
            )
            output["factor_ic"][feature].setdefault("bucket_shape", {})[str(horizon)] = (
                _bucket_shape(bucket_rows, bucket_count)
            )
            output["factor_ic"][feature].setdefault("tails", {})[str(horizon)] = (
                _tail_summary(tails)
            )
            output["factor_ic"][feature].setdefault("yearly", {})[str(horizon)] = [
                {"year": row.pop("period"), **row}
                for row in _period_rows(daily, tails, year_by_date)
            ]
            output["factor_ic"][feature].setdefault("fold", {})[str(horizon)] = [
                {"fold": row.pop("period"), **row}
                for row in _period_rows(daily, tails, fold_by_date)
            ]
    for name in variants:
        signal = f"score__{name}"
        report: dict[str, Any] = {"features": list(variants[name]), "horizons": {}}
        for horizon in horizons:
            status = f"label_status_{horizon}d"
            value = f"forward_return_{horizon}d"
            valid = joined.filter(pl.col(status) == "verified")
            daily_ic = _daily_ic(valid, signal, value)
            bucket_rows, tails = _bucket_rows(
                valid,
                signal=signal,
                value=value,
                benchmark_by_date=benchmark_by_horizon[horizon],
                bucket_count=bucket_count,
            )
            report["horizons"][str(horizon)] = {
                "ic": _summary(list(daily_ic.values())),
                "buckets": _aggregate_buckets(bucket_rows, bucket_count),
                "bucket_shape": _bucket_shape(bucket_rows, bucket_count),
                "tails": _tail_summary(tails),
                "yearly": [
                    {"year": row.pop("period"), **row}
                    for row in _period_rows(daily_ic, tails, year_by_date)
                ],
                "fold": [
                    {"fold": row.pop("period"), **row}
                    for row in _period_rows(daily_ic, tails, fold_by_date)
                ],
            }
        output["signals"][name] = report
    output["integrity"] = _integrity(
        matrix, scored, labels, features=features, horizons=horizons
    )
    output["integrity"]["price_liquidity_concentration"] = _price_liquidity_concentration(
        joined, horizons=horizons
    )
    return output

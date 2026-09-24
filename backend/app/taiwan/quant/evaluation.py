"""Out-of-sample diagnostics for the existing point-in-time factor panel.

Forward returns are built in a separate table and joined only for evaluation.
Universe admission remains owned by ``panel_training_matrix`` and the scorer is
the same deterministic composite used by the live runner.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import polars as pl

from app.taiwan.adjust import assert_training_safe, forward_adjusted_return, reject_presentation
from app.taiwan.corporate_actions import CorporateActionEvent
from app.taiwan.providers.corporate_actions import SOURCE_URLS
from app.taiwan.providers.taiwan_values import market_close
from app.taiwan.quant.baseline import rank_equal_weight_features
from app.taiwan.quant.data_health import DataHealth, ReadinessLevel
from app.taiwan.quant.live_contract import FEATURES, LIVE_TIER
from app.taiwan.quant.training import TrainingMatrixResult
from app.taiwan.quant.validation.folds import FoldConfig, generate_folds
from app.taiwan.quant_eligibility import PRIMARY_VERIFIED


@dataclass(frozen=True)
class ActionCoverage:
    """Verified corporate-action query coverage for one exchange and interval."""

    start: date
    end: date
    status: str
    sources: tuple[str, ...]

    def covers(self, start: date, end: date) -> bool:
        return (self.status == "verified" and self.start <= start and self.end >= end
                and set(self.sources) == set(SOURCE_URLS))


def _validate_sessions(sessions_by_exchange: Mapping[str, Sequence[date]]) -> None:
    for exchange, sessions in sessions_by_exchange.items():
        if exchange not in ("TWSE", "TPEX"):
            raise ValueError("unsupported exchange in verified session calendar")
        ordered = list(sessions)
        if ordered != sorted(ordered) or len(set(ordered)) != len(ordered):
            raise ValueError(f"{exchange} sessions must be sorted and unique")


def build_forward_labels(
    admission: TrainingMatrixResult,
    daily: pl.DataFrame,
    *,
    exchange_by_symbol: Mapping[str, str],
    sessions_by_exchange: Mapping[str, Sequence[date]],
    events: Iterable[CorporateActionEvent],
    action_coverage: Mapping[str, ActionCoverage],
    horizons: tuple[int, ...] = (5, 20),
) -> pl.DataFrame:
    """Create evaluation-only labels using verified exchange trading sessions.

    The caller supplies sessions from a complete official census and an action
    coverage record for every exchange. Missing bars stay explicit; later bars
    are never substituted for an absent session.
    """
    if not isinstance(admission, TrainingMatrixResult):
        raise TypeError("labels require PIT-admitted factor rows")
    if not isinstance(daily, pl.DataFrame):
        raise TypeError("labels require raw daily prices")
    reject_presentation(daily)
    if not {"symbol", "date", "close"} <= set(daily.columns):
        raise ValueError("raw daily prices require symbol, date and close")
    if daily.schema["date"] != pl.Date:
        raise ValueError("raw daily dates must use trading-session Date values")
    if daily.select(pl.struct("symbol", "date").is_duplicated().any()).item():
        raise ValueError("duplicate raw daily symbol/session")
    matrix = admission.matrix
    required = {"symbol", "date", "universe_tier", "usage_scope", "adjustment_as_of",
                "adjustment_status"}
    if not required <= set(matrix.columns):
        raise ValueError("evaluation requires PIT-admitted factor provenance")
    if matrix.is_empty():
        return pl.DataFrame(schema={"date": pl.Date, "symbol": pl.String})
    if matrix["universe_tier"].n_unique() != 1 or matrix["universe_tier"][0] == LIVE_TIER:
        raise ValueError("evaluation requires one historical PIT universe tier")
    if matrix.select(pl.struct("symbol", "date").is_duplicated().any()).item():
        raise ValueError("duplicate admitted feature symbol/session")
    if matrix.filter((pl.col("usage_scope") != "pit_feature")
                     | (pl.col("adjustment_status") != "verified")).height:
        raise ValueError("evaluation requires verified point-in-time features")
    assert_training_safe(matrix)
    for feature_stamp in matrix.select("date", "adjustment_as_of").iter_rows(named=True):
        if feature_stamp["adjustment_as_of"] != market_close(feature_stamp["date"]).isoformat():
            raise ValueError("feature timestamp must be anchored at its session close")
    label_columns = [column for column in matrix.columns
                     if column.startswith(("forward_return_", "label_", "future_return"))]
    if label_columns:
        raise ValueError("future labels must not enter the feature matrix")
    if not horizons or any(type(horizon) is not int or horizon <= 0 for horizon in horizons):
        raise ValueError("horizons must be positive trading-session counts")
    if len(set(horizons)) != len(horizons):
        raise ValueError("horizons must be unique")
    _validate_sessions(sessions_by_exchange)

    daily_by_symbol: dict[str, dict[date, dict[str, Any]]] = {}
    for price_row in daily.select("symbol", "date", "close").iter_rows(named=True):
        daily_by_symbol.setdefault(price_row["symbol"], {})[price_row["date"]] = price_row
    event_rows = tuple(events)
    events_by_symbol: dict[str, list[CorporateActionEvent]] = {}
    for event in event_rows:
        events_by_symbol.setdefault(event.symbol, []).append(event)

    rows: list[dict[str, Any]] = []
    for feature in matrix.select("date", "symbol").iter_rows(named=True):
        symbol, start = feature["symbol"], feature["date"]
        exchange = exchange_by_symbol.get(symbol)
        if not isinstance(exchange, str) or exchange not in sessions_by_exchange:
            raise ValueError(f"missing verified exchange/session mapping for {symbol}")
        calendar = sessions_by_exchange[exchange]
        position = {day: index for index, day in enumerate(calendar)}
        row: dict[str, Any] = {"date": start, "symbol": symbol}
        for horizon in horizons:
            value_column = f"forward_return_{horizon}d"
            status_column = f"label_status_{horizon}d"
            end_column = f"label_end_{horizon}d"
            row[value_column] = None
            row[end_column] = None
            if start not in position:
                row[status_column] = "data_insufficient"
                continue
            end_index = position[start] + horizon
            if end_index >= len(calendar):
                row[status_column] = "pending"
                continue
            end = calendar[end_index]
            row[end_column] = end
            coverage = action_coverage.get(exchange)
            if coverage is None or not coverage.covers(start, end):
                row[status_column] = "corporate_action_coverage_unavailable"
                continue
            expected = calendar[position[start]:end_index + 1]
            by_day = daily_by_symbol.get(symbol, {})
            closes = [by_day.get(day) for day in expected]
            if any(item is None or item["close"] is None
                   or not math.isfinite(float(item["close"])) or float(item["close"]) <= 0
                   for item in closes):
                row[status_column] = "missing_session_price"
                continue
            price_window = pl.DataFrame([
                {"symbol": symbol, "date": day, "close": float(by_day[day]["close"])}
                for day in expected
            ], schema={"symbol": pl.String, "date": pl.Date, "close": pl.Float64})
            result = forward_adjusted_return(
                price_window, start_session=start, horizon_sessions=horizon,
                events=events_by_symbol.get(symbol, ()),
            )
            row[status_column] = result.status
            if result.status == "verified":
                row[value_column] = result.value
            elif result.reason == "insufficient_horizon":
                row[status_column] = "pending"
        rows.append(row)

    schema: dict[str, Any] = {"date": pl.Date, "symbol": pl.String}
    for horizon in horizons:
        schema[f"forward_return_{horizon}d"] = pl.Float64
        schema[f"label_status_{horizon}d"] = pl.String
        schema[f"label_end_{horizon}d"] = pl.Date
    return pl.DataFrame(rows, schema=schema).sort(["date", "symbol"])


def _spearman(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    pair = pl.DataFrame({"x": left, "y": right})
    value = pair.select(pl.corr(
        pl.col("x").rank(method="average"),
        pl.col("y").rank(method="average"),
    )).item()
    return float(value) if value is not None and math.isfinite(float(value)) else None


def _summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"ic_mean": None, "ic_std": None, "ic_positive_ratio": None, "n_dates": 0}
    mean = sum(values) / len(values)
    std = math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))
    return {"ic_mean": mean, "ic_std": std,
            "ic_positive_ratio": sum(value > 0 for value in values) / len(values),
            "n_dates": len(values)}


def _ranked_composite(matrix: pl.DataFrame) -> pl.DataFrame:
    missing = sorted(set(FEATURES) - set(matrix.columns))
    if missing:
        raise ValueError(f"existing composite features are missing: {missing}")
    scored: list[pl.DataFrame] = []
    for day in matrix["date"].unique().sort().to_list():
        section = matrix.filter(pl.col("date") == day).sort("symbol")
        usable = section.filter(pl.all_horizontal(
            pl.col(feature).is_not_null() & pl.col(feature).is_finite() for feature in FEATURES
        ))
        if usable.is_empty():
            continue
        ranks = rank_equal_weight_features(usable.select("symbol", *FEATURES).to_dicts(), FEATURES)
        scores = pl.DataFrame([
            {"symbol": symbol, "composite_score": values["score"]}
            for symbol, values in ranks.items()
        ], schema={"symbol": pl.String, "composite_score": pl.Float64})
        scored.append(usable.select("date", "symbol").join(scores, on="symbol", how="inner"))
    return pl.concat(scored).sort(["date", "symbol"]) if scored else pl.DataFrame(
        schema={"date": pl.Date, "symbol": pl.String, "composite_score": pl.Float64})


def _metrics(
    features: pl.DataFrame,
    labels: pl.DataFrame,
    *,
    factors: Sequence[str],
    horizons: tuple[int, ...],
) -> dict[str, Any]:
    joined = features.join(labels, on=["date", "symbol"], how="left")
    factor_ic: dict[str, dict[str, Any]] = {}
    by_feature: dict[str, dict[int, list[float]]] = {}
    for factor in factors:
        factor_ic[factor] = {}
        by_feature[factor] = {}
        for horizon in horizons:
            value_column = f"forward_return_{horizon}d"
            status_column = f"label_status_{horizon}d"
            values: list[float] = []
            for section in joined.filter(pl.col(status_column) == "verified").partition_by("date"):
                valid = section.filter(pl.col(factor).is_not_null() & pl.col(factor).is_finite()
                                       & pl.col(value_column).is_not_null()
                                       & pl.col(value_column).is_finite())
                ic = _spearman(valid[factor].to_list(), valid[value_column].to_list())
                if ic is not None:
                    values.append(ic)
            by_feature[factor][horizon] = values
            factor_ic[factor][str(horizon)] = _summary(values)

    composite = _ranked_composite(features)
    composite_rows = composite.join(labels, on=["date", "symbol"], how="left")
    composite_ic: dict[str, Any] = {}
    bucket_stats: dict[str, Any] = {}
    for horizon in horizons:
        value_column = f"forward_return_{horizon}d"
        status_column = f"label_status_{horizon}d"
        ics: list[float] = []
        top_returns: list[float] = []
        bottom_returns: list[float] = []
        bucket_observations = 0
        for section in composite_rows.filter(pl.col(status_column) == "verified").partition_by("date"):
            valid = section.filter(pl.col("composite_score").is_finite()
                                   & pl.col(value_column).is_not_null()
                                   & pl.col(value_column).is_finite())
            ic = _spearman(valid["composite_score"].to_list(), valid[value_column].to_list())
            if ic is not None:
                ics.append(ic)
            rows = sorted(valid.to_dicts(), key=lambda item: (-item["composite_score"], item["symbol"]))
            if len(rows) >= 2:
                bucket_size = max(1, math.ceil(len(rows) * 0.2))
                top_returns.append(sum(row[value_column] for row in rows[:bucket_size]) / bucket_size)
                bottom_returns.append(sum(row[value_column] for row in rows[-bucket_size:]) / bucket_size)
                bucket_observations += len(rows)
        composite_ic[str(horizon)] = _summary(ics)
        top_mean = sum(top_returns) / len(top_returns) if top_returns else None
        bottom_mean = sum(bottom_returns) / len(bottom_returns) if bottom_returns else None
        bucket_stats[str(horizon)] = {
            "top_bucket_future_return": top_mean,
            "bottom_bucket_future_return": bottom_mean,
            "long_short_spread": top_mean - bottom_mean
            if top_mean is not None and bottom_mean is not None else None,
            "bucket_fraction": 0.2,
            "n_dates": min(len(top_returns), len(bottom_returns)),
            "n_observations": bucket_observations,
        }
    composite_coverage = {
        "admitted_rows": features.height,
        "scored_rows": composite.height,
        "excluded_for_incomplete_composite_features": features.height - composite.height,
    }
    return {"factor_ic": factor_ic, "factor_values": by_feature,
            "composite_score_ic": composite_ic,
            "composite_score_buckets": bucket_stats,
            "composite_score_coverage": composite_coverage}


def _label_coverage(labels: pl.DataFrame, horizons: tuple[int, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for horizon in horizons:
        column = f"label_status_{horizon}d"
        counts = labels.group_by(column).len().to_dict(as_series=False) if labels.height else {}
        status_counts = dict(zip(counts.get(column, []), counts.get("len", []), strict=True))
        result[str(horizon)] = {
            "rows": labels.height,
            "verified": int(status_counts.get("verified", 0)),
            "pending": int(status_counts.get("pending", 0)),
            "data_insufficient": int(sum(
                count for status, count in status_counts.items()
                if status not in ("verified", "pending")
            )),
            "by_status": status_counts,
        }
    return result


def evaluate_quant(
    admission: TrainingMatrixResult,
    daily: pl.DataFrame,
    *,
    exchange_by_symbol: Mapping[str, str],
    sessions_by_exchange: Mapping[str, Sequence[date]],
    walk_forward_sessions: Sequence[date],
    events: Iterable[CorporateActionEvent],
    action_coverage: Mapping[str, ActionCoverage],
    data_health: DataHealth | None = None,
    horizons: tuple[int, ...] = (5, 20),
    fold_config: FoldConfig | None = None,
) -> dict[str, Any]:
    """Evaluate PIT-admitted existing factors and their live composite score."""
    if not isinstance(admission, TrainingMatrixResult):
        raise TypeError("evaluation requires PIT-admitted factors")
    matrix = admission.matrix
    if not matrix.height:
        raise ValueError("evaluation requires non-empty admitted factor rows")
    if matrix["universe_tier"].n_unique() != 1:
        raise ValueError("evaluation cannot mix historical universe tiers")
    tier = matrix["universe_tier"][0]
    if tier == LIVE_TIER:
        raise ValueError("current live universe cannot be used for historical evaluation")
    eligible = set(admission.resolution.eligible_features)
    if not set(FEATURES) <= eligible:
        raise ValueError("existing live composite features are not all PIT-admitted")
    labels = build_forward_labels(
        admission, daily, exchange_by_symbol=exchange_by_symbol,
        sessions_by_exchange=sessions_by_exchange, events=events,
        action_coverage=action_coverage, horizons=horizons,
    )
    factor_names = tuple(sorted(eligible & set(matrix.columns)))
    full = _metrics(matrix, labels, factors=factor_names, horizons=horizons)
    if len(walk_forward_sessions) != len(set(walk_forward_sessions)) \
            or list(walk_forward_sessions) != sorted(walk_forward_sessions):
        raise ValueError("walk-forward sessions must be sorted and unique")
    cfg = fold_config or FoldConfig(label_horizon=max(horizons))
    if cfg.label_horizon < max(horizons):
        raise ValueError("walk-forward purge horizon must cover the longest label")
    folds = generate_folds(walk_forward_sessions, cfg)
    fold_reports: list[dict[str, Any]] = []
    oos_rows: list[pl.DataFrame] = []
    prior_test_end: date | None = None
    for fold in folds:
        if prior_test_end is not None and fold.test_start <= prior_test_end:
            raise ValueError("walk-forward test blocks must not overlap")
        prior_test_end = fold.test_end
        test_features = matrix.filter(pl.col("date").is_between(fold.test_start, fold.test_end))
        test_labels = labels.filter(pl.col("date").is_between(fold.test_start, fold.test_end))
        metrics = _metrics(test_features, test_labels, factors=factor_names, horizons=horizons)
        fold_reports.append({
            "index": fold.index,
            "train_start": fold.train_start.isoformat(), "train_end": fold.train_end.isoformat(),
            "validation_start": fold.val_start.isoformat(), "validation_end": fold.val_end.isoformat(),
            "test_start": fold.test_start.isoformat(), "test_end": fold.test_end.isoformat(),
            "purge_sessions": fold.purge_sessions,
            "embargo_sessions": fold.embargo_sessions,
            "label_horizon": fold.label_horizon,
            "test_metrics": {key: value for key, value in metrics.items() if key != "factor_values"},
        })
        if test_features.height:
            oos_rows.append(test_features)
    oos_features = pl.concat(oos_rows) if oos_rows else matrix.head(0)
    oos_dates = set(oos_features["date"].to_list()) if oos_features.height else set()
    oos_labels = labels.filter(pl.col("date").is_in(sorted(oos_dates))) if oos_dates else labels.head(0)
    oos = _metrics(oos_features, oos_labels, factors=factor_names, horizons=horizons)
    primary_oos_ready = bool(
        tier == PRIMARY_VERIFIED and data_health is not None
        and data_health.is_ready(ReadinessLevel.PRIMARY_OOS)
    )
    return {
        "status": "success" if folds else "insufficient_walk_forward_history",
        "universe_tier": tier,
        "claim_scope": (
            "primary_verified_oos" if primary_oos_ready else
            "experimental_only" if tier != PRIMARY_VERIFIED else
            "primary_oos_readiness_blocked"
        ),
        "primary_oos_ready": primary_oos_ready,
        "primary_oos_blocked_reasons": (
            data_health.blocked_reasons.get(ReadinessLevel.PRIMARY_OOS.value, [])
            if data_health is not None and not primary_oos_ready else []
        ),
        "label_source": "pit_forward_adjusted_close_return_evaluation_only",
        "horizons": list(horizons),
        "label_coverage": _label_coverage(labels, horizons),
        "factor_ic": full["factor_ic"],
        "composite_score_ic": full["composite_score_ic"],
        "composite_score_buckets": full["composite_score_buckets"],
        "composite_score_coverage": full["composite_score_coverage"],
        "walk_forward": {
            "config": cfg.describe(),
            "folds": fold_reports,
            "oos": {key: value for key, value in oos.items() if key != "factor_values"},
        },
    }

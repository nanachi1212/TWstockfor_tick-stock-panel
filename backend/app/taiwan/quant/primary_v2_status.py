"""Read-only product projection for the frozen V1 and preregistered V2."""
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from app.config import settings
from app.taiwan.observed_universe import ObservedUniverseStore
from app.taiwan.quant.evaluation_store import PrimaryOosRunStore
from app.taiwan.quant.primary_v2_preregistration import (
    CANDIDATES,
    PREREGISTRATION_ID,
    V1_DATASET_IDENTITY,
    V1_EVALUATION_END,
    V1_EVALUATION_START,
    V1_RUN_ID,
    build_confirmatory_window,
)
from app.taiwan.realtime.calendar import taipei_today


def _diagnostics_path() -> Path:
    return (
        Path(settings.data_dir)
        / "user_data"
        / "primary_oos_diagnostics"
        / f"{V1_RUN_ID}.json"
    )


def _read_diagnostics(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    source = value.get("source")
    if not isinstance(source, Mapping):
        return None
    if (
        source.get("run_id") != V1_RUN_ID
        or source.get("dataset_identity") != V1_DATASET_IDENTITY
    ):
        return None
    return value


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _integer(value: object) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _horizon_metrics(
    horizon: int,
    artifact: Mapping[str, Any] | None,
    diagnostics: Mapping[str, Any] | None,
) -> dict[str, Any]:
    evaluation = _mapping(artifact.get("evaluation")) if artifact else {}
    oos = _mapping(_mapping(evaluation.get("walk_forward")).get("oos"))
    formal_ic = _mapping(_mapping(oos.get("composite_score_ic")).get(str(horizon)))
    formal_buckets = _mapping(_mapping(oos.get("composite_score_buckets")).get(str(horizon)))
    diagnostic_horizon = _mapping(
        _mapping(_mapping(_mapping(diagnostics).get("signals")).get("full"))
        .get("horizons")
    ).get(str(horizon))
    diagnostic_horizon = _mapping(diagnostic_horizon)
    diagnostic_ic = _mapping(diagnostic_horizon.get("ic"))
    tails = _mapping(diagnostic_horizon.get("tails"))
    top = _number(tails.get("top"))
    if top is None:
        top = _number(formal_buckets.get("top_bucket_future_return"))
    bottom = _number(tails.get("bottom"))
    if bottom is None:
        bottom = _number(formal_buckets.get("bottom_bucket_future_return"))
    long_short = _number(tails.get("long_short"))
    if long_short is None:
        long_short = _number(formal_buckets.get("long_short_spread"))
    benchmark_dates = _integer(tails.get("benchmark_n_dates")) or 0
    top_minus_benchmark = _number(tails.get("top_vs_benchmark"))
    return {
        "mean_ic": _number(diagnostic_ic.get("mean"))
        if diagnostic_ic else _number(formal_ic.get("ic_mean")),
        "median_ic": _number(diagnostic_ic.get("median")),
        "positive_ic_ratio": _number(diagnostic_ic.get("positive_ratio"))
        if diagnostic_ic else _number(formal_ic.get("ic_positive_ratio")),
        "top_bucket_return": top,
        "universe_return": _number(tails.get("universe")),
        "bottom_bucket_return": bottom,
        "top_minus_universe": _number(tails.get("top_vs_universe")),
        "long_short": long_short,
        "benchmark_symbol": "0050.TWSE" if top_minus_benchmark is not None else None,
        "benchmark_return": _number(tails.get("benchmark")),
        "top_minus_benchmark": top_minus_benchmark,
        "valid_benchmark_dates": benchmark_dates,
        "valid_date_count": _integer(diagnostic_ic.get("n_dates"))
        or _integer(formal_ic.get("n_dates")),
    }


def _has_regime_instability(diagnostics: Mapping[str, Any] | None) -> bool | None:
    if diagnostics is None:
        return None
    horizons = _mapping(
        _mapping(_mapping(_mapping(diagnostics).get("signals")).get("full"))
        .get("horizons")
    )
    signs: set[bool] = set()
    for horizon in (5, 20):
        for row in _mapping(horizons.get(str(horizon))).get("yearly", []):
            mean = _number(_mapping(_mapping(row).get("ic")).get("mean"))
            if mean is not None:
                signs.add(mean > 0)
    return len(signs) > 1


def model_validation_status(
    *,
    run_store: PrimaryOosRunStore | None = None,
    census_store: ObservedUniverseStore | None = None,
    diagnostics_path: Path | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Read saved evidence. This function never runs an evaluation or fetches data."""
    run_store = run_store or PrimaryOosRunStore()
    census_store = census_store or ObservedUniverseStore()
    stored = run_store.successful_run(V1_RUN_ID)
    artifact = _mapping(stored.get("artifact")) if stored else None
    diagnostics = _read_diagnostics(diagnostics_path or _diagnostics_path())
    end = today or taipei_today()
    sessions = (
        sorted(census_store.session_dates(
            "TWSE", start=V1_EVALUATION_END + timedelta(days=1), end=end,
        ))
        if end > V1_EVALUATION_END else []
    )
    window = build_confirmatory_window(sessions)
    horizon_metrics = {
        str(horizon): _horizon_metrics(horizon, artifact, diagnostics)
        for horizon in (5, 20)
    }
    mean_ics = [horizon_metrics[str(horizon)]["mean_ic"] for horizon in (5, 20)]
    candidate_a = CANDIDATES[0]
    provenance = _mapping(artifact.get("provenance")) if artifact else {}
    return {
        "current_model": {
            "candidate_key": candidate_a.key,
            "model_identity": candidate_a.identity,
            "factor_set": list(candidate_a.factors),
            "evaluation_status": "historical_baseline_available" if artifact else "artifact_unavailable",
        },
        "primary_oos": {
            "run_id": V1_RUN_ID,
            "period": {
                "start": V1_EVALUATION_START.isoformat(),
                "end": V1_EVALUATION_END.isoformat(),
            },
            "dataset_identity": V1_DATASET_IDENTITY,
            "readiness": "available" if artifact else "unavailable",
            "artifact_created_at": provenance.get("created_at"),
            "horizons": horizon_metrics,
        },
        "diagnostics": {
            "status": "available" if diagnostics else "unavailable",
            "diagnostics_identity": _mapping(_mapping(diagnostics).get("source")).get(
                "diagnostics_identity"
            ),
            "negative_ic": all(value is not None and value < 0 for value in mean_ics)
            if diagnostics else None,
            "regime_instability": _has_regime_instability(diagnostics),
            "conclusion": "尚未證明穩定 Alpha" if diagnostics else "diagnostics unavailable",
        },
        "v2": {
            "preregistration_id": PREREGISTRATION_ID,
            "candidate_status": "development_candidates",
            "candidates": [candidate.describe() for candidate in CANDIDATES],
            "confirmatory_window": window.describe(),
        },
        "lightgbm": {"status": "NOT_YET"},
    }

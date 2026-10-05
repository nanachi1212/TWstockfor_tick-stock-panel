"""Rebuild deterministic diagnostics for one immutable Primary OOS run."""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from app.taiwan.backfill_worker import TaiwanHistoricalBackfillWorker
from app.taiwan.corporate_actions import CorporateActionStore
from app.taiwan.providers.corporate_actions import SOURCE_URLS
from app.taiwan.quant.evaluation import ActionCoverage
from app.taiwan.quant.evaluation_store import PrimaryOosRunStore
from app.taiwan.quant.live_contract import FEATURES, canonical_hash
from app.taiwan.quant.oos_diagnostics import (
    OosFold,
    build_forward_labels_fast,
    diagnose_oos,
    validate_fast_labels_sample,
)
from app.taiwan.quant.storage import FactorPanelStore

DEFAULT_RUN_ID = "2538dbd6-acc5-43ae-9061-a74149aeb9f1"
DEFAULT_DATASET_IDENTITY = "c0336dd5980a165bfb0fa1fa99b7e5438ea4bd862ade90e17b73210296e57626"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build read-only descriptive diagnostics for an immutable Primary OOS run."
    )
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--dataset-identity", default=DEFAULT_DATASET_IDENTITY)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true", help="print the complete result")
    return parser


def _folds(evaluation: dict[str, Any]) -> list[OosFold]:
    rows = evaluation.get("walk_forward", {}).get("folds")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("the formal artifact has no walk-forward folds")
    return [
        OosFold(
            index=int(row["index"]),
            test_start=date.fromisoformat(row["test_start"]),
            test_end=date.fromisoformat(row["test_end"]),
        )
        for row in rows
    ]


def _panel_paths(store: FactorPanelStore, folds: list[OosFold]) -> list[Path]:
    root = (
        store.root
        / "factor_version=tw-factors-v1"
        / "policy_version=v1"
        / "universe_tier=primary_verified"
    )
    paths = []
    for path in sorted(root.glob("date=*/values.parquet")):
        day = date.fromisoformat(path.parent.name.removeprefix("date="))
        if any(fold.test_start <= day <= fold.test_end for fold in folds):
            paths.append(path)
    if not paths:
        raise RuntimeError("the formal OOS panel partitions are unavailable")
    return paths


def _read_oos_panel(paths: list[Path]) -> tuple[pl.DataFrame, pl.DataFrame]:
    required = [
        "date",
        "symbol",
        "factor_version",
        "policy_version",
        "universe_tier",
        "usage_scope",
        "adjustment_as_of",
        "adjustment_status",
        *FEATURES,
        "amount",
        "adv20_twd",
    ]
    panel = pl.scan_parquet(paths).select(required).collect().sort(["date", "symbol"])
    if panel.filter(
        (pl.col("usage_scope") != "pit_feature")
        | (pl.col("adjustment_status") != "verified")
    ).height:
        raise RuntimeError("the OOS factor panel contains unverified rows")
    matrix = panel.select(
        "date",
        "symbol",
        "factor_version",
        "policy_version",
        "universe_tier",
        *FEATURES,
    )
    auxiliary = panel.select("date", "symbol", "amount", "adv20_twd")
    return matrix, auxiliary


def _load_prices() -> tuple[pl.DataFrame, list[date]]:
    worker = TaiwanHistoricalBackfillWorker()
    raw = worker.census_store.read("TWSE")
    daily = raw.select(
        pl.concat_str([pl.col("raw_code"), pl.lit(".TWSE")]).alias("symbol"),
        "date",
        "close",
    ).sort(["symbol", "date"])
    sessions = sorted(worker.census_store.session_dates("TWSE"))
    if daily.is_empty() or not sessions:
        raise RuntimeError("verified TWSE daily prices or sessions are unavailable")
    return daily, sessions


def _coverage() -> tuple[ActionCoverage, tuple[Any, ...]]:
    verified = CorporateActionStore().read_verified_coverage()
    if verified is None:
        raise RuntimeError("local corporate-action coverage is not verified")
    start, end, events = verified
    return ActionCoverage(start, end, "verified", tuple(sorted(SOURCE_URLS))), events


def _assert_close(actual: float | None, expected: float | None, name: str) -> None:
    if actual is None or expected is None or not math.isclose(
        actual, expected, rel_tol=0.0, abs_tol=1e-12
    ):
        raise RuntimeError(
            "diagnostic reconstruction differs from formal artifact: "
            f"{name}; actual={actual!r}; expected={expected!r}"
        )


def _validate_formal_reconstruction(
    diagnostics: dict[str, Any], evaluation: dict[str, Any]
) -> None:
    expected = evaluation["walk_forward"]["oos"]
    full = diagnostics["signals"]["full"]["horizons"]
    for horizon in (5, 20):
        key = str(horizon)
        actual_ic = full[key]["ic"]["mean"]
        expected_ic = expected["composite_score_ic"][key]["ic_mean"]
        if actual_ic is None or expected_ic is None or not math.isclose(
            actual_ic, expected_ic, rel_tol=0.0, abs_tol=1e-12
        ):
            expected_folds = {
                int(row["index"]): row["test_metrics"]["composite_score_ic"][key]["ic_mean"]
                for row in evaluation["walk_forward"]["folds"]
            }
            actual_folds = {int(row["fold"]): row["ic"]["mean"] for row in full[key]["fold"]}
            deltas = sorted(
                (
                    abs(actual_folds[index] - value),
                    index,
                    actual_folds[index],
                    value,
                )
                for index, value in expected_folds.items()
                if value is not None and actual_folds.get(index) is not None
            )
            largest = deltas[-1] if deltas else None
            raise RuntimeError(
                "diagnostic reconstruction differs from formal artifact: "
                f"{horizon}D composite IC; actual={actual_ic!r}; expected={expected_ic!r}; "
                f"largest_fold_delta={largest!r}"
            )
        for name, old_name in (
            ("top", "top_bucket_future_return"),
            ("bottom", "bottom_bucket_future_return"),
            ("long_short", "long_short_spread"),
        ):
            _assert_close(
                full[key]["tails"][name],
                expected["composite_score_buckets"][key][old_name],
                f"{horizon}D {name}",
            )


def _build(args: argparse.Namespace) -> dict[str, Any]:
    stored = PrimaryOosRunStore().successful_run(args.run_id)
    if stored is None:
        raise RuntimeError("the requested successful Primary OOS run is unavailable")
    artifact = stored["artifact"]
    provenance = artifact.get("provenance")
    evaluation = artifact.get("evaluation")
    if not isinstance(provenance, dict) or not isinstance(evaluation, dict):
        raise RuntimeError("the successful Primary OOS artifact is incomplete")
    if provenance.get("dataset_identity") != args.dataset_identity:
        raise RuntimeError("the requested dataset identity does not match the run artifact")
    if set(provenance.get("evaluation_spec", {}).get("composite_scorer", {}).get(
        "features", []
    )) != set(FEATURES):
        raise RuntimeError("the run artifact does not use the current formal factor contract")

    folds = _folds(evaluation)
    paths = _panel_paths(FactorPanelStore(), folds)
    matrix, auxiliary = _read_oos_panel(paths)
    expected_rows = evaluation["walk_forward"]["oos"]["composite_score_coverage"][
        "admitted_rows"
    ]
    if matrix.height != expected_rows:
        raise RuntimeError("the local OOS panel row count differs from the formal artifact")

    daily, sessions = _load_prices()
    coverage, events = _coverage()
    latest_end = max(fold.test_end for fold in folds)
    first_start = min(fold.test_start for fold in folds)
    last_position = sessions.index(latest_end) + max(provenance["horizons"])
    price_end = sessions[last_position]
    symbols = matrix["symbol"].unique().to_list()
    needed_daily = daily.filter(
        pl.col("symbol").is_in([*symbols, "0050.TWSE"])
        & pl.col("date").is_between(first_start, price_end)
    )
    labels = build_forward_labels_fast(
        matrix.select("date", "symbol"),
        needed_daily,
        sessions=sessions,
        events=events,
        action_coverage=coverage,
    )
    validate_fast_labels_sample(
        matrix.select("date", "symbol"),
        labels,
        needed_daily,
        sessions=sessions,
        events=events,
    )
    oos_dates = matrix["date"].unique().sort().to_list()
    benchmark_keys = pl.DataFrame(
        {"date": oos_dates, "symbol": ["0050.TWSE"] * len(oos_dates)},
        schema={"date": pl.Date, "symbol": pl.String},
    )
    benchmark_labels = build_forward_labels_fast(
        benchmark_keys,
        needed_daily.filter(pl.col("symbol") == "0050.TWSE"),
        sessions=sessions,
        events=events,
        action_coverage=coverage,
    )
    auxiliary = auxiliary.join(
        needed_daily.select("date", "symbol", "close"), on=["date", "symbol"], how="left"
    )
    diagnostics = diagnose_oos(
        matrix,
        labels,
        benchmark_labels,
        folds=folds,
        auxiliary=auxiliary,
    )
    _validate_formal_reconstruction(diagnostics, evaluation)
    diagnostics["source"] = {
        "run_id": args.run_id,
        "dataset_identity": args.dataset_identity,
        "identity_key": stored["identity_key"],
        "artifact_recorded_at": stored["recorded_at"],
        "evaluation_spec_version": provenance["evaluation_spec_version"],
        "evaluation_spec_hash": provenance["evaluation_spec_hash"],
        "formal_code_sha": provenance["code_sha"],
        "oos_start": min(oos_dates).isoformat(),
        "oos_end": max(oos_dates).isoformat(),
        "folds": len(folds),
        "oos_dates": len(oos_dates),
        "oos_rows": matrix.height,
        "reconstruction_check": "matches_formal_ic_and_tail_metrics_at_1e-12",
        "diagnostics_identity": canonical_hash({
            "run_id": args.run_id,
            "dataset_identity": args.dataset_identity,
            "spec_hash": provenance["evaluation_spec_hash"],
            "oos_start": min(oos_dates),
            "oos_end": max(oos_dates),
            "oos_rows": matrix.height,
            "result": diagnostics,
        }),
    }
    return diagnostics


def _default_output(run_id: str) -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "user_data" / "primary_oos_diagnostics" / f"{run_id}.json"


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = _build(args)
        output = (args.output or _default_output(args.run_id)).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
        )
        temporary.replace(output)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_code": type(exc).__name__, "error": str(exc)}, ensure_ascii=False))
        return 1
    payload = result if args.json else {
        "status": "diagnostics_available",
        "run_id": args.run_id,
        "dataset_identity": args.dataset_identity,
        "diagnostics_identity": result["source"]["diagnostics_identity"],
        "output": str(output),
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

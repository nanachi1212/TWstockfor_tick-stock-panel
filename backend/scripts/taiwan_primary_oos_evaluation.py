"""Maintainer entry point for the readiness-gated Primary OOS evaluation."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from app.taiwan.quant.primary_oos_runner import (
    PrimaryOosInputError,
    PrimaryOosNotReadyError,
    PrimaryOosRunFailedError,
    read_primary_oos_preflight,
    run_primary_oos_evaluation,
)


def _emit(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
    else:
        print(f"Primary OOS: {payload['status']}")
        if payload.get("run_id"):
            print(f"Run: {payload['run_id']}")
        for reason in payload.get("blocking_reasons", []):
            print(f"Blocked: {reason}")
        if payload.get("error_code"):
            print(f"Error: {payload['error_code']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="taiwan_primary_oos_evaluation",
        description="Run the formal TWSE Primary OOS evaluation after shared readiness gates pass.",
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable output")
    parser.add_argument("--build-factor-panel", action="store_true",
                        help="materialize the historical Primary PIT factor panel with the existing "
                             "panel/storage code (readiness-gated), then exit")
    parser.add_argument("--workers", type=int, default=6,
                        help="worker processes for --build-factor-panel")
    args = parser.parse_args(argv)
    try:
        preflight = read_primary_oos_preflight()
    except Exception as exc:
        _emit({"status": "failed", "error_code": type(exc).__name__}, as_json=args.json)
        return 1
    if preflight.readiness.status.value != "ready":
        _emit({
            "status": "waiting_for_data_health",
            "readiness_status": preflight.readiness.status.value,
            "a2b": preflight.a2b_progress,
            "blocking_reasons": list(preflight.readiness.blocking_reasons),
        }, as_json=args.json)
        return 2
    if args.build_factor_panel:
        try:
            from app.taiwan.quant.primary_panel import build_primary_factor_panel

            built = build_primary_factor_panel(preflight, workers=args.workers)
        except Exception as exc:
            _emit({"status": "failed", "error_code": type(exc).__name__}, as_json=args.json)
            return 1
        _emit({"status": "factor_panel_available", **built}, as_json=args.json)
        return 0
    try:
        result = run_primary_oos_evaluation(preflight)
    except PrimaryOosNotReadyError as exc:
        _emit({
            "status": "waiting_for_data_health",
            "readiness_status": exc.readiness.status.value,
            "blocking_reasons": list(exc.readiness.blocking_reasons),
        }, as_json=args.json)
        return 2
    except PrimaryOosRunFailedError as exc:
        _emit({"status": "failed", "run_id": exc.run_id, "error_code": exc.code},
              as_json=args.json)
        return 1
    except PrimaryOosInputError as exc:
        _emit({"status": "failed", "error_code": type(exc).__name__}, as_json=args.json)
        return 1
    except Exception as exc:
        # Provider, filesystem, and SQLite errors must produce a safe, non-zero
        # maintainer result without printing exception text that may contain URLs.
        _emit({"status": "failed", "error_code": type(exc).__name__}, as_json=args.json)
        return 1
    _emit({
        "status": result["status"],
        "run_id": result.get("provenance", {}).get("run_id"),
        "reused": result.get("reused", False),
        "evaluation_spec_version": result.get("provenance", {}).get("evaluation_spec_version"),
        "dataset_identity": result.get("provenance", {}).get("dataset_identity"),
        "latest_market_date": result.get("provenance", {}).get("latest_market_date"),
        "horizons": result.get("evaluation", {}).get("horizons"),
    }, as_json=args.json)
    return 0 if result["status"] == "evaluation_available" else 2


if __name__ == "__main__":
    sys.exit(main())

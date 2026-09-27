"""Maintainer entry point for the historical PIT evaluation of trend_liquidity_v1."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from app.taiwan.quant.primary_oos_runner import PrimaryOosNotReadyError
from app.taiwan.quant.selection_pit import (
    artifact_summary,
    run_trend_liquidity_v1_pit_evaluation,
)


def _progress(step: int, total: int) -> None:
    if step % 250 == 0:
        print(f"session {step}/{total}", file=sys.stderr, flush=True)


def _emit(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
        return
    print(f"Historical PIT trend_liquidity_v1: {payload['status']}")
    for key in ("run_id", "error_code"):
        if payload.get(key):
            print(f"{key}: {payload[key]}")
    for reason in payload.get("blocking_reasons", []):
        print(f"Blocked: {reason}")
    summary = payload.get("summary")
    if summary:
        reproducibility = summary["reproducibility"]
        print(f"requested sessions: {reproducibility['requested_sessions']}")
        print(f"strict sessions: {reproducibility['strict_fully_reproducible_sessions']}")
        print(f"blockers: {reproducibility['blocker_session_counts']}")
        print(f"result fingerprint: {summary['result_fingerprint']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="taiwan_trend_liquidity_v1_pit_evaluation",
        description="Replay the frozen trend_liquidity_v1 rules on historical PIT evidence.",
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable output")
    args = parser.parse_args(argv)
    try:
        artifact = run_trend_liquidity_v1_pit_evaluation(progress=_progress)
    except PrimaryOosNotReadyError as exc:
        _emit({"status": "waiting_for_data_health",
               "blocking_reasons": list(exc.readiness.blocking_reasons)}, as_json=args.json)
        return 2
    except Exception as exc:
        _emit({"status": "failed", "error_code": type(exc).__name__}, as_json=args.json)
        return 1
    _emit({"status": "reused" if artifact["reused"] else "recorded", "run_id": artifact["run_id"],
           "summary": artifact_summary(artifact)}, as_json=args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

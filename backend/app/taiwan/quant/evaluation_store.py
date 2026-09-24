"""Read/write handoff for completed, readiness-gated Primary OOS reports."""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.taiwan.quant.data_health import (
    DataHealth,
    QuantEvaluationStatus,
    quant_evaluation_readiness,
)


class QuantEvaluationReportStore:
    """Persist one report outside Git and invalidate it when its gate changes."""

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            from app.taiwan.data_root import taiwan_data_root

            path = taiwan_data_root() / "quant" / "primary_oos_report.json"
        self.path = Path(path)

    @staticmethod
    def _progress_snapshot(progress: Mapping[str, int]) -> dict[str, int]:
        return {
            name: progress[name]
            for name in ("completed_jobs", "pending_jobs", "failed_jobs", "unique_first_seen_dates")
        }

    def save(
        self,
        report: Mapping[str, Any],
        *,
        health: DataHealth,
        progress: Mapping[str, int],
        worker_status: str,
    ) -> None:
        readiness = quant_evaluation_readiness(
            health, dict(progress), worker_status=worker_status,
        )
        if readiness.status is not QuantEvaluationStatus.READY:
            raise ValueError("Primary OOS report cannot be saved before readiness is ready")
        if (report.get("primary_oos_ready") is not True
                or report.get("claim_scope") != "primary_verified_oos"
                or set(report.get("horizons", ())) != {5, 20}):
            raise ValueError("report does not contain a verified Primary OOS result")
        generated_at = report.get("generated_at")
        if not isinstance(generated_at, str):
            raise ValueError("report requires a generated_at timestamp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "generated_at": generated_at,
            "health": health.describe(),
            "a2b": self._progress_snapshot(progress),
            "report": dict(report),
        }
        descriptor, temporary = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, allow_nan=False, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def read(
        self,
        *,
        health: DataHealth,
        progress: Mapping[str, int],
        worker_status: str,
    ) -> dict[str, Any] | None:
        readiness = quant_evaluation_readiness(
            health, dict(progress), worker_status=worker_status,
        )
        if readiness.status is not QuantEvaluationStatus.READY or not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        report = payload.get("report") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != 1
            or payload.get("health") != health.describe()
            or payload.get("a2b") != self._progress_snapshot(progress)
            or not isinstance(report, dict)
            or report.get("primary_oos_ready") is not True
            or report.get("claim_scope") != "primary_verified_oos"
            or set(report.get("horizons", ())) != {5, 20}
            or not isinstance(report.get("generated_at"), str)
        ):
            return None
        return report

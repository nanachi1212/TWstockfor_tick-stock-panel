"""Append-only local persistence for frozen Taiwan AI research artifacts."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from contextlib import suppress
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.taiwan.ai_research import (
    ADVICE_PROMPT_VERSION,
    RESEARCH_PROMPT_VERSION,
    REVIEW_PROMPT_VERSION,
    TaiwanAIResearchRun,
)
from app.taiwan.realtime.calendar import taipei_now

HISTORY_FILE_NAME = "taiwan_ai_research_history.jsonl"


class AIResearchHistoryError(ValueError):
    """Raised when persisted history is malformed or cannot satisfy an operation."""


def _default_path() -> Path:
    return settings.data_dir / "user_data" / HISTORY_FILE_NAME


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _legacy_id(record: dict[str, Any]) -> str:
    return f"legacy_{hashlib.sha256(_canonical(record).encode('utf-8')).hexdigest()[:24]}"


def _normalize_record(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AIResearchHistoryError("AI research history line must be a JSON object")
    record = dict(value)
    record.setdefault("id", _legacy_id(record))
    record.setdefault("kind", "report")
    record.setdefault("run_id", None)
    record.setdefault("parent_id", None)
    record.setdefault("saved_at", record.get("generated_at"))
    if record["kind"] not in {"report", "link"}:
        # Future record kinds remain readable; operations that require a report reject them explicitly.
        record.setdefault("compatibility", {})
        record["compatibility"]["unsupported_kind"] = True
    missing = [name for name in ("run_id", "saved_at") if not record.get(name)]
    if missing:
        record.setdefault("compatibility", {})
        record["compatibility"]["missing_fields"] = missing
    return record


def _field_changes(before: Any, after: Any, path: str = "") -> list[dict[str, Any]]:
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[dict[str, Any]] = []
        for key in sorted(set(before) | set(after)):
            child = f"{path}.{key}" if path else str(key)
            if key not in before:
                changes.append({"path": child, "before": None, "after": after[key]})
            elif key not in after:
                changes.append({"path": child, "before": before[key], "after": None})
            else:
                changes.extend(_field_changes(before[key], after[key], child))
        return changes
    if isinstance(before, list) and isinstance(after, list):
        changes = []
        for index in range(max(len(before), len(after))):
            child = f"{path}[{index}]"
            if index >= len(before):
                changes.append({"path": child, "before": None, "after": after[index]})
            elif index >= len(after):
                changes.append({"path": child, "before": before[index], "after": None})
            else:
                changes.extend(_field_changes(before[index], after[index], child))
        return changes
    return [] if before == after else [{"path": path or "$", "before": before, "after": after}]


def _report_content(record: dict[str, Any]) -> dict[str, Any] | None:
    response = record.get("response")
    report = response.get("report") if isinstance(response, dict) else record.get("report")
    if not isinstance(report, dict):
        return None
    ignored = {
        "symbol", "code", "name", "industry", "instrument_type", "evidence_as_of",
        "personal_context_as_of", "run_id", "started_at", "completed_at", "generated_at",
        "prompt_version", "provider", "model",
    }
    return {key: value for key, value in report.items() if key not in ignored}


class TaiwanAIResearchHistoryStore:
    """Thread-safe JSONL store. It intentionally provides no cross-process locking."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else _default_path()
        self._lock = threading.Lock()

    def _read_unlocked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if not raw:
            return []
        lines = raw.splitlines(keepends=True)
        records: list[dict[str, Any]] = []
        for index, raw_line in enumerate(lines):
            if not raw_line.strip():
                continue
            is_last = index == len(lines) - 1
            is_truncated_tail = is_last and not raw.endswith((b"\n", b"\r"))
            try:
                value = json.loads(raw_line.decode("utf-8"))
                records.append(_normalize_record(value))
            except (UnicodeDecodeError, json.JSONDecodeError, AIResearchHistoryError) as exc:
                if is_truncated_tail:
                    break
                raise AIResearchHistoryError(
                    f"Malformed AI research history at line {index + 1}"
                ) from exc
        return records

    def _append_unlocked(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        prefix = ""
        if self.path.exists():
            raw = self.path.read_bytes()
            if raw and not raw.endswith((b"\n", b"\r")):
                tail_start = max(raw.rfind(b"\n"), raw.rfind(b"\r")) + 1
                try:
                    json.loads(raw[tail_start:].decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    with self.path.open("r+b") as stream:
                        stream.truncate(tail_start)
                        stream.flush()
                        os.fsync(stream.fileno())
                else:
                    prefix = "\n"
        payload = prefix + _canonical(record) + "\n"
        with self.path.open("a", encoding="utf-8", newline="") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())

    def ensure_report(self, run: TaiwanAIResearchRun) -> dict[str, Any]:
        """Persist a successful frozen run once, keyed by run_id."""
        if run.response.status != "success" or run.response.report is None:
            raise AIResearchHistoryError("Only successful research reports can be persisted")
        with self._lock:
            records = self._read_unlocked()
            for record in records:
                if record.get("kind") == "report" and record.get("run_id") == run.run_id:
                    return record
            response = run.response.model_dump(mode="json")
            report = response["report"]
            saved_at = taipei_now().isoformat()
            record = {
                "id": f"research_{uuid.uuid4().hex}",
                "kind": "report",
                "run_id": run.run_id,
                "parent_id": None,
                "saved_at": saved_at,
                "symbol": report.get("symbol"),
                "purpose": run.purpose,
                "evidence_as_of": run.response.evidence_as_of,
                "started_at": run.response.started_at,
                "completed_at": run.response.completed_at,
                "generated_at": run.response.completed_at or run.response.generated_at,
                "prompt_versions": {
                    "research": RESEARCH_PROMPT_VERSION,
                    "advice": ADVICE_PROMPT_VERSION,
                    "review": REVIEW_PROMPT_VERSION,
                },
                "provider": run.response.provider,
                "model": run.response.model,
                "generation_config": run.generation_config,
                "evidence_digest": run.evidence_digest,
                "evidence_registry_keys": list(run.evidence_registry_keys),
                "evidence_payload": run.evidence_payload,
                "response": response,
            }
            self._append_unlocked(record)
            return record

    def append_link(self, record_id: str, target: Any) -> dict[str, Any]:
        with self._lock:
            records = self._read_unlocked()
            parent = next((record for record in records if record.get("id") == record_id), None)
            if parent is None:
                raise KeyError(record_id)
            record = {
                "id": f"link_{uuid.uuid4().hex}",
                "kind": "link",
                "run_id": parent.get("run_id"),
                "parent_id": record_id,
                "saved_at": taipei_now().isoformat(),
                "symbol": parent.get("symbol"),
                "purpose": parent.get("purpose"),
                "target": target,
            }
            try:
                _canonical(record)
            except (TypeError, ValueError) as exc:
                raise AIResearchHistoryError("Link target is not JSON serializable") from exc
            self._append_unlocked(record)
            return record

    def list(
        self,
        *,
        symbol: str | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        purpose: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._lock:
            records = self._read_unlocked()
        filtered: list[dict[str, Any]] = []
        for record in records:
            saved_at = record.get("saved_at")
            saved_date: date | None = None
            if isinstance(saved_at, str):
                with suppress(ValueError):
                    saved_date = datetime.fromisoformat(saved_at.replace("Z", "+00:00")).date()
            if symbol is not None and record.get("symbol") != symbol:
                continue
            if purpose is not None and record.get("purpose") != purpose:
                continue
            if from_date is not None and (saved_date is None or saved_date < from_date):
                continue
            if to_date is not None and (saved_date is None or saved_date > to_date):
                continue
            filtered.append(record)
        filtered.sort(key=lambda item: (str(item.get("saved_at") or ""), str(item.get("id") or "")), reverse=True)
        return filtered[:limit]

    def get(self, record_id: str) -> dict[str, Any] | None:
        with self._lock:
            return next(
                (record for record in self._read_unlocked() if record.get("id") == record_id),
                None,
            )

    def compare(self, record_id: str, other_id: str) -> dict[str, Any]:
        with self._lock:
            records = self._read_unlocked()
        by_id = {record.get("id"): record for record in records}
        left = by_id.get(record_id)
        right = by_id.get(other_id)
        if left is None:
            raise KeyError(record_id)
        if right is None:
            raise KeyError(other_id)
        if left.get("kind") != "report" or right.get("kind") != "report":
            raise AIResearchHistoryError("Only report records can be compared")

        left_evidence = left.get("evidence_payload")
        right_evidence = right.get("evidence_payload")
        evidence_available = isinstance(left_evidence, dict) and isinstance(right_evidence, dict)
        evidence_changes = _field_changes(left_evidence, right_evidence) if evidence_available else []

        def generation(record: dict[str, Any]) -> dict[str, Any]:
            return {
                "purpose": record.get("purpose"),
                "prompt_versions": record.get("prompt_versions") or {
                    "research": record.get("prompt_version")
                },
                "provider": record.get("provider"),
                "model": record.get("model"),
                "generation_config": record.get("generation_config") or {},
            }

        generation_changes = _field_changes(generation(left), generation(right))
        left_report = _report_content(left)
        right_report = _report_content(right)
        interpretation_available = left_report is not None and right_report is not None
        interpretation_changes = _field_changes(left_report, right_report) if interpretation_available else []
        return {
            "left_id": record_id,
            "right_id": other_id,
            "evidence_data_changes": {
                "available": evidence_available,
                "changed": bool(evidence_changes),
                "digest_changed": left.get("evidence_digest") != right.get("evidence_digest"),
                "field_changes": evidence_changes,
            },
            "model_prompt_config_changes": {
                "available": True,
                "changed": bool(generation_changes),
                "field_changes": generation_changes,
            },
            "interpretation_report_changes": {
                "available": interpretation_available,
                "changed": bool(interpretation_changes),
                "field_changes": interpretation_changes,
            },
        }


_stores_lock = threading.Lock()
_stores: dict[str, TaiwanAIResearchHistoryStore] = {}


def get_ai_research_history_store(path: Path | None = None) -> TaiwanAIResearchHistoryStore:
    resolved = str((Path(path) if path is not None else _default_path()).resolve())
    with _stores_lock:
        store = _stores.get(resolved)
        if store is None:
            store = TaiwanAIResearchHistoryStore(Path(resolved))
            _stores[resolved] = store
        return store

"""Small shared contract and cache helpers for optional external context providers."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

ExternalStatus = Literal["available", "partial", "unavailable", "stale"]


class ExternalProviderResult(BaseModel):
    source: str
    status: ExternalStatus
    as_of: str | None = None
    retrieved_at: str | None = None
    freshness: str = "unavailable"
    data: Any = None
    error_reason: str | None = None

    @classmethod
    def unavailable(cls, source: str, reason: str) -> ExternalProviderResult:
        return cls(source=source, status="unavailable", error_reason=reason)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def read_json_cache(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def write_json_cache(path: Path, payload: dict[str, Any]) -> None:
    """Write runtime cache atomically; cache paths are outside version control."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

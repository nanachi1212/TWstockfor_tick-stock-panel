"""Fail-closed privacy audit for release seed and desktop artifacts."""
from __future__ import annotations

import csv
import json
import math
import os
import re
import sqlite3
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl
import yaml

_PRIVATE_PATH_PARTS = {
    "user_data",
    "portfolio",
    "portfolios",
    "watchlist",
    "alerts",
    "alert_history",
    "monitor_rules",
    "social_sentiment",
    "ai_cache",
    "ai_threads",
    "logs",
    "cookies",
    "sessions",
}
_PRIVATE_FILE_NAMES = {
    ".env",
    ".ai-memory.toml",
    "secrets.json",
    "ai_key_profiles_secrets.json",
    "preferences.json",
    "auth.json",
}
_PORTFOLIO_FIELDS = {"portfolio", "position", "positions", "shares_owned", "cost_basis", "average_cost", "pnl"}
_WATCHLIST_FIELDS = {"watchlist", "watchlists", "watchlist_groups"}
_SNAPSHOT_FIELDS = {
    "selection_snapshots",
    "buy_point_snapshots",
    "personal_snapshots",
    "ai_conversation",
    "ai_research_history",
    "daily_brief_history",
    "social_history",
}
_CREDENTIAL_FIELDS = {
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "authorization",
    "cookie",
    "session",
    "webhook",
    "credential",
    "credentials",
    "finmind_token",
    "telegram_bot_token",
    "line_channel_access_token",
}
_TEXT_SUFFIXES = {
    ".txt", ".json", ".jsonl", ".csv", ".tsv", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".xml", ".html", ".md", ".manifest", ".metadata",
}
_DATABASE_SUFFIXES = {".sqlite", ".sqlite3", ".db", ".duckdb"}
_BINARY_CREDENTIAL_PATTERNS = (
    re.compile(rb"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(rb"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b"),
    re.compile(rb"\b\d{8,}:[A-Za-z0-9_-]{25,}\b"),
)


@dataclass
class PrivacyAuditReport:
    result: str = "PASS"
    seed_files_scanned: int = 0
    artifact_files_scanned: int = 0
    credentials_found: int = 0
    private_user_files_found: int = 0
    maintainer_paths_found: int = 0
    portfolio_records_found: int = 0
    watchlist_records_found: int = 0
    personal_snapshots_found: int = 0
    findings: list[dict[str, str]] = field(default_factory=list)
    _finding_samples: Counter[str] = field(default_factory=Counter, repr=False)

    def add(self, category: str, path: Path, detail: str) -> None:
        field_name = {
            "credentials": "credentials_found",
            "private_user_file": "private_user_files_found",
            "maintainer_path": "maintainer_paths_found",
            "portfolio": "portfolio_records_found",
            "watchlist": "watchlist_records_found",
            "personal_snapshot": "personal_snapshots_found",
        }[category]
        setattr(self, field_name, getattr(self, field_name) + 1)
        self.result = "FAIL"
        # Findings contain category/location only; never copy detected values.
        if self._finding_samples[category] < 20:
            self.findings.append({"category": category, "path": str(path), "detail": detail})
            self._finding_samples[category] += 1

    def as_public_dict(self) -> dict[str, Any]:
        return {
            "result": self.result,
            "seed_files_scanned": self.seed_files_scanned,
            "artifact_files_scanned": self.artifact_files_scanned,
            "credentials_found": self.credentials_found,
            "private_user_files_found": self.private_user_files_found,
            "maintainer_paths_found": self.maintainer_paths_found,
            "portfolio_records_found": self.portfolio_records_found,
            "watchlist_records_found": self.watchlist_records_found,
            "personal_snapshots_found": self.personal_snapshots_found,
            "findings": self.findings,
        }


def _normalised_field(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def _record_count(value: Any) -> int:
    if value in (None, "", [], {}):
        return 0
    if isinstance(value, list):
        return len(value)
    return 1


def _inspect_fields(report: PrivacyAuditReport, path: Path, fields: Iterable[tuple[str, Any]]) -> None:
    for raw_name, value in fields:
        name = _normalised_field(raw_name)
        count = max(1, _record_count(value))
        if name in _CREDENTIAL_FIELDS:
            report.add("credentials", path, f"forbidden credential schema field: {name}")
        if name in _PORTFOLIO_FIELDS:
            for _ in range(count):
                report.add("portfolio", path, f"forbidden schema field: {name}")
        if name in _WATCHLIST_FIELDS:
            for _ in range(count):
                report.add("watchlist", path, f"forbidden schema field: {name}")
        if name in _SNAPSHOT_FIELDS:
            for _ in range(count):
                report.add("personal_snapshot", path, f"forbidden schema field: {name}")


def _walk_json_fields(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from _walk_json_fields(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json_fields(child)


def _entropy(value: str) -> float:
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _credential_hits(text: str) -> int:
    patterns = (
        r"\bsk-[A-Za-z0-9_-]{16,}\b",
        r"\bgh[pousr]_[A-Za-z0-9]{20,}\b",
        r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}\b",
        r"\b\d{8,}:[A-Za-z0-9_-]{25,}\b",
        r"(?i)\b(?:api[_-]?key|token|secret|password|authorization|webhook)\b\s*[:=]\s*[\"']?([A-Za-z0-9._~+/=-]{8,})",
    )
    hits = sum(len(re.findall(pattern, text)) for pattern in patterns)
    for match in re.findall(r"[A-Za-z0-9+/=_-]{40,}", text):
        if _entropy(match) >= 4.5 and any(
            marker in text[max(0, text.find(match) - 40): text.find(match)].lower()
            for marker in ("token", "secret", "credential", "authorization", "api_key", "apikey")
        ):
            hits += 1
    return hits


def _binary_credential_hits(blob: bytes) -> int:
    """Find strong credential signatures in every artifact, including binaries."""
    return sum(len(pattern.findall(blob)) for pattern in _BINARY_CREDENTIAL_PATTERNS)


def _maintainer_path_hits(blob: bytes, *, username: str, hostname: str) -> int:
    text = blob.decode("utf-8", errors="ignore")
    # JSON/YAML may escape Windows separators; scan their decoded shape too.
    text = text.replace("\\\\", "\\")
    patterns = [
        r"(?i)F:\\Projects\\",
        r"(?i)E:\\Git\\",
        r"(?i)[A-Z]:\\[^\r\n\x00]{0,160}\\\.(?:codex|claude|gemini)(?:\\|\b)",
    ]
    hits = sum(len(re.findall(pattern, text)) for pattern in patterns)
    if username and re.search(rf"(?i)[A-Z]:\\Users\\{re.escape(username)}(?:\\|\b)", text):
        hits += 1
    if hostname and len(hostname) >= 4 and re.search(rf"(?i)\\\\{re.escape(hostname)}(?:\\|\b)", text):
        hits += 1
    return hits


def _scan_structured_file(report: PrivacyAuditReport, path: Path) -> None:
    suffix = path.suffix.lower()
    try:
        if suffix == ".parquet":
            schema = pl.read_parquet_schema(path)
            _inspect_fields(report, path, ((name, None) for name in schema))
        elif suffix in {".json", ".jsonl"}:
            texts = path.read_text(encoding="utf-8", errors="strict").splitlines()
            values = [json.loads(line) for line in texts if line.strip()] if suffix == ".jsonl" else [json.loads("\n".join(texts))]
            for value in values:
                _inspect_fields(report, path, _walk_json_fields(value))
        elif suffix in {".csv", ".tsv"}:
            delimiter = "\t" if suffix == ".tsv" else ","
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream, delimiter=delimiter)
                _inspect_fields(report, path, ((name, None) for name in (reader.fieldnames or [])))
        elif suffix in {".yaml", ".yml"}:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
            _inspect_fields(report, path, _walk_json_fields(value))
        elif suffix in _DATABASE_SUFFIXES:
            with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as db:
                tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                for table in tables:
                    quoted = table.replace('"', '""')
                    columns = [row[1] for row in db.execute(f'PRAGMA table_info("{quoted}")')]
                    _inspect_fields(report, path, ((name, None) for name in columns))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, sqlite3.DatabaseError) as exc:
        report.add("private_user_file", path, f"structured file could not be audited: {type(exc).__name__}")


def audit_release_paths(
    paths: Iterable[Path],
    *,
    scope: str,
    report: PrivacyAuditReport | None = None,
) -> PrivacyAuditReport:
    """Audit files without emitting detected secret values."""
    result = report or PrivacyAuditReport()
    # GitHub-hosted runners legitimately embed their ephemeral runner profile
    # in packaged dependency metadata. It is not maintainer personal data.
    # Keep local maintainer profile/hostname detection outside that environment,
    # while the invariant private-workspace and agent-config patterns still run.
    is_github_runner = os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
    username = "" if is_github_runner else os.environ.get("USERNAME", "")
    hostname = "" if is_github_runner else os.environ.get("COMPUTERNAME", "")
    files: list[Path] = []
    for root in paths:
        root = root.resolve()
        files.extend(path for path in root.rglob("*") if path.is_file()) if root.is_dir() else files.append(root)

    for path in sorted(set(files)):
        if scope == "seed":
            result.seed_files_scanned += 1
        else:
            result.artifact_files_scanned += 1
        lowered_parts = {_normalised_field(part) for part in path.parts}
        lower_name = path.name.lower()
        if lower_name in _PRIVATE_FILE_NAMES or lower_name.startswith(".env.") or lowered_parts & _PRIVATE_PATH_PARTS:
            result.add("private_user_file", path, "forbidden private runtime path")

        try:
            raw = path.read_bytes()
        except OSError:
            result.add("private_user_file", path, "file could not be read for audit")
            continue
        for _ in range(_maintainer_path_hits(raw, username=username, hostname=hostname)):
            result.add("maintainer_path", path, "absolute maintainer/build-machine path")

        for _ in range(_binary_credential_hits(raw)):
            result.add("credentials", path, "strong credential signature")

        if path.suffix.lower() in _TEXT_SUFFIXES and len(raw) <= 128 * 1024 * 1024:
            text = raw.decode("utf-8", errors="ignore")
            text_hits = max(0, _credential_hits(text) - _binary_credential_hits(raw))
            for _ in range(text_hits):
                result.add("credentials", path, "credential-like value")
        if scope == "seed":
            _scan_structured_file(result, path)
    return result


def write_privacy_report(report: PrivacyAuditReport, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(".tmp")
    temp.write_text(json.dumps(report.as_public_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, output)


def assert_privacy_pass(report: PrivacyAuditReport) -> None:
    if report.result != "PASS":
        raise RuntimeError(
            "PRIVACY RELEASE GATE failed; inspect release-privacy-audit.json (secret values are redacted)"
        )

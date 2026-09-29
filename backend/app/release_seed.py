"""Build, verify, and install the public market-data seed bundled with desktop releases.

The seed is built from a positive allowlist into a fresh staging directory.  It
never copies ``data/`` wholesale and never includes ``user_data`` or runtime
research/history stores.  First-run installation is all-or-nothing and only
occurs when the writable Taiwan market-data directory has no existing data.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

import polars as pl
from pydantic import BaseModel, Field

SEED_SCHEMA_VERSION = 1
SEED_ARCHIVE_NAME = "release-seed.zip"

_EXACT_PUBLIC_FILES = frozenset(
    {
        "security_master.parquet",
        "adj_factor/events.parquet",
        "adj_factor/coverage.json",
        "events_cache/regulatory_events.json",
        "instrument_evidence/company.parquet",
        "instrument_evidence/isin_listed.parquet",
        "instrument_evidence/isin_unlisted.parquet",
        "instrument_evidence/termination.parquet",
        "monthly_revenue_evidence/fetches.jsonl",
    }
)
_PUBLIC_PREFIXES = (
    "daily/date=",
    "historical_classification/date=",
    "institutional/date=",
    "margin/date=",
    "monthly_revenue_evidence/tables/",
    "observed_universe/exchange=TWSE/date=",
    "observed_universe/exchange=TPEX/date=",
    "regulatory_history/source=",
)
_PRIVATE_PARTS = frozenset(
    {
        "user_data",
        "portfolio",
        "watchlist",
        "alerts",
        "monitor_rules",
        "social_sentiment",
        "ai_cache",
        "live_quant",
        "quant",
        "logs",
        "finmind_cache",
        "factors",
    }
)


class SeedFileEntry(BaseModel):
    path: str
    size_bytes: int
    sha256: str


class ReleaseSeedManifest(BaseModel):
    schema_version: int = SEED_SCHEMA_VERSION
    seed_version: str
    data_as_of: str
    created_at: str
    file_count: int
    total_size_bytes: int
    sha256_algorithm: Literal["SHA256"] = "SHA256"
    contains_user_data: bool = False
    sources: list[dict[str, str]] = Field(default_factory=list)
    files: list[SeedFileEntry] = Field(default_factory=list)


class SeedInstallResult(BaseModel):
    status: Literal[
        "installed",
        "missing",
        "preserved_existing",
        "preserved_newer",
        "failed",
    ]
    data_as_of: str | None = None
    latest_existing: str | None = None
    installed_files: int = 0
    needs_incremental: bool = False
    error: str | None = None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _normalise_public_path(raw_path: str) -> str:
    raw = raw_path.replace("\\", "/")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise ValueError(f"unsafe seed path: {raw_path}")
    normalised = pure.as_posix().removeprefix("./")
    lowered_parts = {part.lower() for part in pure.parts}
    if lowered_parts & _PRIVATE_PARTS:
        raise ValueError(f"private path is forbidden in release seed: {raw_path}")
    if normalised in _EXACT_PUBLIC_FILES:
        return normalised
    if any(normalised.startswith(prefix) for prefix in _PUBLIC_PREFIXES):
        return normalised
    raise ValueError(f"path is not in the public release-seed allowlist: {raw_path}")


def _latest_partition_files(root: Path, pattern: str, limit: int) -> list[Path]:
    files = sorted(root.glob(pattern), key=lambda path: path.as_posix())
    return files[-limit:] if limit > 0 else []


def collect_public_seed_files(source_taiwan_dir: Path) -> list[Path]:
    """Return the deterministic positive allowlist of public release inputs."""
    source = source_taiwan_dir.resolve()
    required = [source / "security_master.parquet"]
    daily = sorted(source.glob("daily/date=*/part.parquet"))
    if not daily:
        raise ValueError(f"release seed requires daily partitions under {source}")
    required.extend(daily)

    optional: list[Path] = []
    for rel in sorted(_EXACT_PUBLIC_FILES - {"security_master.parquet"}):
        candidate = source / rel
        if candidate.is_file():
            optional.append(candidate)
    optional.extend(sorted(source.glob("historical_classification/date=*/part.parquet")))
    optional.extend(sorted(source.glob("monthly_revenue_evidence/tables/*.json")))

    # Recent official evidence is sufficient for startup/screener readiness and
    # avoids shipping the heavyweight multi-year research census.
    for exchange in ("TWSE", "TPEX"):
        optional.extend(
            _latest_partition_files(
                source,
                f"observed_universe/exchange={exchange}/date=*/part.parquet",
                60,
            )
        )
    optional.extend(_latest_partition_files(source, "institutional/date=*/part.parquet", 60))
    optional.extend(_latest_partition_files(source, "margin/date=*/part.parquet", 60))
    for source_dir in sorted((source / "regulatory_history").glob("source=*")):
        optional.extend(_latest_partition_files(source_dir, "date=*.parquet", 60))

    files: list[Path] = []
    seen: set[str] = set()
    for path in [*required, *optional]:
        if not path.is_file():
            if path in required:
                raise FileNotFoundError(path)
            continue
        rel = path.resolve().relative_to(source).as_posix()
        normalised = _normalise_public_path(rel)
        if normalised not in seen:
            seen.add(normalised)
            files.append(path.resolve())
    return sorted(files, key=lambda path: path.relative_to(source).as_posix())


def _validate_staged_public_data(stage_taiwan: Path) -> str:
    security_master = stage_taiwan / "security_master.parquet"
    master = pl.read_parquet(security_master)
    required_master = {"symbol", "code", "name", "exchange", "instrument_type"}
    missing = required_master - set(master.columns)
    if master.is_empty() or missing:
        raise ValueError(f"invalid staged security master; missing={sorted(missing)}")

    daily_files = sorted(stage_taiwan.glob("daily/date=*/part.parquet"))
    if not daily_files:
        raise ValueError("staged release seed has no daily partitions")
    required_daily = {"symbol", "date", "open", "high", "low", "close", "volume"}
    for path in daily_files:
        schema = set(pl.read_parquet_schema(path))
        if required_daily - schema:
            raise ValueError(f"invalid daily partition schema: {path}")
    return daily_files[-1].parent.name.removeprefix("date=")


def build_release_seed(
    source_taiwan_dir: Path,
    output_zip: Path,
    *,
    seed_version: str | None = None,
) -> tuple[Path, ReleaseSeedManifest]:
    """Build a release seed from a fresh allowlisted staging tree."""
    source = source_taiwan_dir.resolve()
    output = output_zip.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    selected = collect_public_seed_files(source)

    with tempfile.TemporaryDirectory(prefix="nanachi_release_seed_") as temp_name:
        stage_taiwan = Path(temp_name) / "data" / "taiwan"
        for src in selected:
            rel = src.relative_to(source)
            dest = stage_taiwan / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)

        data_as_of = _validate_staged_public_data(stage_taiwan)
        entries: list[SeedFileEntry] = []
        for staged in sorted(stage_taiwan.rglob("*")):
            if not staged.is_file():
                continue
            archive_rel = staged.relative_to(stage_taiwan).as_posix()
            _normalise_public_path(archive_rel)
            entries.append(
                SeedFileEntry(
                    path=archive_rel,
                    size_bytes=staged.stat().st_size,
                    sha256=sha256_file(staged),
                )
            )

        manifest = ReleaseSeedManifest(
            seed_version=seed_version or f"tw-{data_as_of}",
            data_as_of=data_as_of,
            created_at=datetime.now(UTC).isoformat(),
            file_count=len(entries),
            total_size_bytes=sum(entry.size_bytes for entry in entries),
            sources=[
                {"name": "Taiwan Stock Exchange Corporation", "type": "official_public_data"},
                {"name": "Taipei Exchange", "type": "official_public_data"},
                {"name": "MOPS", "type": "official_public_data"},
            ],
            files=entries,
        )

        temp_zip = output.with_name(f"{output.name}.{uuid.uuid4().hex}.tmp")
        try:
            with zipfile.ZipFile(temp_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                archive.writestr("manifest.json", manifest.model_dump_json(indent=2))
                for staged in sorted(stage_taiwan.rglob("*")):
                    if staged.is_file():
                        archive_rel = staged.relative_to(stage_taiwan).as_posix()
                        archive.write(staged, f"data/taiwan/{archive_rel}")
            os.replace(temp_zip, output)
        finally:
            temp_zip.unlink(missing_ok=True)
    return output, manifest


def verify_release_seed(seed_zip: Path) -> ReleaseSeedManifest:
    seed_path = seed_zip.resolve()
    with zipfile.ZipFile(seed_path, "r") as archive:
        names = [info.filename.replace("\\", "/") for info in archive.infolist() if not info.is_dir()]
        if len(names) != len(set(names)):
            raise ValueError("release seed contains duplicate archive members")
        if "manifest.json" not in names:
            raise ValueError("release seed is missing manifest.json")
        manifest = ReleaseSeedManifest.model_validate_json(archive.read("manifest.json"))
        if manifest.schema_version != SEED_SCHEMA_VERSION or manifest.contains_user_data:
            raise ValueError("release seed manifest is unsafe or unsupported")
        declared: dict[str, SeedFileEntry] = {}
        for entry in manifest.files:
            rel = _normalise_public_path(entry.path)
            member = f"data/taiwan/{rel}"
            if member in declared:
                raise ValueError(f"duplicate seed manifest path: {rel}")
            declared[member] = entry
        actual = {name for name in names if name != "manifest.json"}
        if actual != set(declared):
            raise ValueError("release seed archive members do not exactly match manifest")
        if manifest.file_count != len(declared):
            raise ValueError("release seed file_count does not match manifest entries")
        if manifest.total_size_bytes != sum(entry.size_bytes for entry in declared.values()):
            raise ValueError("release seed total_size_bytes does not match manifest entries")
        for member, entry in declared.items():
            info = archive.getinfo(member)
            if info.file_size != entry.size_bytes:
                raise ValueError(f"release seed size mismatch: {entry.path}")
            digest = hashlib.sha256()
            with archive.open(member) as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            if digest.hexdigest() != entry.sha256:
                raise ValueError(f"release seed checksum mismatch: {entry.path}")
    return manifest


def latest_market_date(target_taiwan_dir: Path) -> str | None:
    dates = sorted(
        path.parent.name.removeprefix("date=")
        for path in target_taiwan_dir.glob("daily/date=*/part.parquet")
    )
    return dates[-1] if dates else None


def install_release_seed(seed_zip: Path, data_dir: Path) -> SeedInstallResult:
    """Install seed atomically without overwriting any existing market/user data."""
    seed_path = seed_zip.resolve()
    if not seed_path.is_file():
        return SeedInstallResult(status="missing")
    try:
        manifest = verify_release_seed(seed_path)
        data_root = data_dir.resolve()
        target_taiwan = data_root / "taiwan"
        existing_latest = latest_market_date(target_taiwan)
        if target_taiwan.exists() and any(target_taiwan.iterdir()):
            status: Literal["preserved_existing", "preserved_newer"] = (
                "preserved_newer"
                if existing_latest is not None and existing_latest >= manifest.data_as_of
                else "preserved_existing"
            )
            return SeedInstallResult(
                status=status,
                data_as_of=manifest.data_as_of,
                latest_existing=existing_latest,
                needs_incremental=(existing_latest is not None and existing_latest < manifest.data_as_of),
            )

        data_root.mkdir(parents=True, exist_ok=True)
        if target_taiwan.exists():
            target_taiwan.rmdir()  # only an empty directory is eligible
        with tempfile.TemporaryDirectory(prefix=".release-seed-", dir=data_root) as temp_name:
            staged_taiwan = Path(temp_name) / "taiwan"
            with zipfile.ZipFile(seed_path, "r") as archive:
                for entry in manifest.files:
                    rel = _normalise_public_path(entry.path)
                    dest = staged_taiwan / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(f"data/taiwan/{rel}") as src, dest.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
            _validate_staged_public_data(staged_taiwan)
            if target_taiwan.exists():
                raise RuntimeError("market data appeared during seed installation")
            os.replace(staged_taiwan, target_taiwan)

        state_path = data_root / "release-seed-state.json"
        temporary = state_path.with_suffix(f".tmp-{uuid.uuid4().hex}")
        temporary.write_text(
            json.dumps(
                {
                    "schema_version": SEED_SCHEMA_VERSION,
                    "seed_version": manifest.seed_version,
                    "data_as_of": manifest.data_as_of,
                    "installed_at": datetime.now(UTC).isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        os.replace(temporary, state_path)
        return SeedInstallResult(
            status="installed",
            data_as_of=manifest.data_as_of,
            installed_files=manifest.file_count,
            needs_incremental=True,
        )
    except Exception as exc:  # startup caller records the failure and continues offline
        return SeedInstallResult(status="failed", error=str(exc))

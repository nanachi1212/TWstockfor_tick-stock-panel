"""Taiwan Market Data Bundle Creation, Verification, and Installation Service.

Implements the minimal Core Bundle and Historical Bundle packaging specified
by the Taiwan Data Bundle Audit (2026-09-26 in docs/taiwan-data-sources.md):
  - Core Bundle: security_master.parquet + daily/ (OHLCV for daily screening / charting)
  - Historical Bundle: TWSE observed_universe + historical_classification + instrument_evidence + adj_factor
    (Optional data for Quant / Primary OOS evaluation)
  - Strict exclusion list: user_data, credentials, alerts, portfolio, AI conversations,
    live_quant signals, locks, logs, exports, and unverified data sources.
  - Manifest & SHA-256 verification, safe extraction (anti-Zip Slip), and atomic installation.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import shutil
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import polars as pl
from pydantic import BaseModel, Field

from app.taiwan.data_root import taiwan_data_root

logger = logging.getLogger(__name__)

# Strict exclusion patterns: items that MUST NEVER enter any data bundle
EXCLUDED_PATTERNS: tuple[str, ...] = (
    "*user_data*",
    "*signals.sqlite3*",
    "*monitor_rules*",
    "*backfill_worker_state*",
    "*.lock*",
    "*.guard*",
    "*secrets*",
    "*.env*",
    "*ai_cache*",
    "*logs*",
    "*.log*",
    "*exports*",
    "*release-assets*",
    "*.pytest*",
    "*exchange=TPEX*",  # TPEx observed universe is Secondary experimental, excluded from primary bundles
    "*factors*",        # Quant factor panels can be regenerated on demand, not distributed
    "*.tmp*",
)

DEFAULT_LICENSE_NOTE = (
    "Audit Notice (2026-09-26): This bundle contains data sourced from TWSE and TPEx. "
    "While OpenAPI datasets follow the Taiwan Government Open Data License (v1), "
    "historical exchange web queries have unclear redistribution terms and FinMind terms "
    "explicitly prohibit redistribution. Sourced data has mixed/unclear redistribution rights. "
    "This bundle is intended for local offline migration and evaluation only; "
    "do NOT publicly upload or redistribute without explicit upstream authorization."
)


class BundleFileEntry(BaseModel):
    path: str  # Relative to data/taiwan/, e.g., "daily/date=2024-01-02/part.parquet"
    size_bytes: int
    sha256: str


class BundleManifest(BaseModel):
    schema_version: int = 1
    bundle_type: Literal["core", "historical"]
    package_name: str
    created_at: str
    data_through: str
    date_range: dict[str, str] = Field(default_factory=dict)
    statistics: dict[str, Any] = Field(default_factory=dict)
    sources: list[dict[str, str]] = Field(default_factory=list)
    license_note: str = DEFAULT_LICENSE_NOTE
    contains_user_data: bool = False
    bundle_sha256: str | None = None
    files: list[BundleFileEntry] = Field(default_factory=list)


def _compute_sha256(path: Path) -> str:
    """Compute SHA-256 hash of a file on disk."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(128 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest().lower()


def _is_excluded(rel_path: str) -> bool:
    """Check if relative path matches any forbidden pattern."""
    normalized = rel_path.replace("\\", "/")
    parts = normalized.split("/")
    for pat in EXCLUDED_PATTERNS:
        if fnmatch.fnmatch(normalized, pat):
            return True
        for part in parts:
            if fnmatch.fnmatch(part, pat):
                return True
    return False


def build_core_bundle(
    output_zip: Path | None = None,
    source_taiwan_dir: Path | None = None,
    package_name: str | None = None,
) -> tuple[Path, BundleManifest]:
    """Package Core Bundle (security_master.parquet + daily/).

    Returns (bundle_zip_path, manifest).
    """
    src_dir = Path(source_taiwan_dir) if source_taiwan_dir else taiwan_data_root()
    if not src_dir.exists():
        raise FileNotFoundError(f"Source Taiwan directory does not exist: {src_dir}")

    sec_master_path = src_dir / "security_master.parquet"
    if not sec_master_path.exists():
        raise FileNotFoundError(f"Missing security_master.parquet in {src_dir}")

    daily_dir = src_dir / "daily"
    if not daily_dir.exists():
        raise FileNotFoundError(f"Missing daily directory in {src_dir}")

    daily_files = sorted(daily_dir.glob("date=*/part.parquet"))
    if not daily_files:
        raise ValueError(f"No daily partitions found in {daily_dir}")

    # Gather file entries
    file_entries: list[BundleFileEntry] = []

    # 1. security_master
    sec_rel = "security_master.parquet"
    sec_size = sec_master_path.stat().st_size
    sec_sha = _compute_sha256(sec_master_path)
    file_entries.append(BundleFileEntry(path=sec_rel, size_bytes=sec_size, sha256=sec_sha))

    # Read security master rows count
    try:
        sec_df = pl.scan_parquet(sec_master_path).select(pl.len()).collect()
        sec_rows = int(sec_df[0, 0])
    except Exception:
        sec_rows = 0

    # 2. daily partitions
    dates: list[str] = []
    total_rows = 0
    for p in daily_files:
        rel = str(p.relative_to(src_dir)).replace("\\", "/")
        if _is_excluded(rel):
            continue
        part_date = p.parent.name.replace("date=", "")
        dates.append(part_date)
        size = p.stat().st_size
        sha = _compute_sha256(p)
        file_entries.append(BundleFileEntry(path=rel, size_bytes=size, sha256=sha))

    dates.sort()
    start_date = dates[0] if dates else ""
    end_date = dates[-1] if dates else ""

    # Estimate or count total daily rows (sample count if many files)
    try:
        sample_df = pl.scan_parquet(daily_files[0]).select(pl.len()).collect()
        sample_len = int(sample_df[0, 0])
        total_rows = sample_len * len(daily_files)
    except Exception:
        total_rows = 0

    pkg_name = package_name or f"nanachi-tw-core-bundle-{start_date}_to_{end_date}"
    output_zip = src_dir.parent / f"{pkg_name}.zip" if output_zip is None else Path(output_zip)
    output_zip.parent.mkdir(parents=True, exist_ok=True)

    manifest = BundleManifest(
        bundle_type="core",
        package_name=pkg_name,
        created_at=datetime.now(UTC).isoformat(),
        data_through=end_date,
        date_range={"start": start_date, "end": end_date},
        statistics={
            "trading_days": len(daily_files),
            "approx_total_rows": total_rows,
            "security_master_rows": sec_rows,
            "files_count": len(file_entries),
        },
        sources=[
            {"name": "Taiwan Stock Exchange Corporation (TWSE)", "type": "official_public_data"},
            {"name": "Taipei Exchange (TPEx)", "type": "official_public_data"},
        ],
        files=file_entries,
    )

    manifest_json = manifest.model_dump_json(indent=2)
    sums_lines = [f"{e.sha256}  data/taiwan/{e.path}\n" for e in file_entries]

    # Write zip archive
    tmp_zip = output_zip.with_suffix(f".tmp_{uuid.uuid4().hex[:6]}")
    try:
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", manifest_json)
            z.writestr("SHA256SUMS.txt", "".join(sums_lines))
            # security_master
            z.write(sec_master_path, f"data/taiwan/{sec_rel}")
            # daily files
            for p in daily_files:
                rel = str(p.relative_to(src_dir)).replace("\\", "/")
                if not _is_excluded(rel):
                    z.write(p, f"data/taiwan/{rel}")

        bundle_sha = _compute_sha256(tmp_zip)
        manifest.bundle_sha256 = bundle_sha

        # Re-write manifest inside zip with bundle_sha256 if desired, or replace file
        tmp_zip.replace(output_zip)
        logger.info("Successfully built Core Bundle at %s (SHA-256: %s)", output_zip, bundle_sha)
        return output_zip, manifest
    except Exception:
        if tmp_zip.exists():
            tmp_zip.unlink(missing_ok=True)
        raise


def build_historical_bundle(
    output_zip: Path | None = None,
    source_taiwan_dir: Path | None = None,
    package_name: str | None = None,
) -> tuple[Path, BundleManifest]:
    """Package Historical Bundle for Quant / Primary OOS evaluation.

    Contains:
      - observed_universe/exchange=TWSE/ (all partition files + _month_verification_TWSE.json)
      - historical_classification/ (all partition files)
      - instrument_evidence/ (all *.parquet files)
      - adj_factor/ (events.parquet + coverage.json)
    Strictly excludes:
      - observed_universe/exchange=TPEX/ (Secondary experimental, excluded from primary OOS)
      - factors/ (Can be computed on-the-fly via --build-factor-panel)
      - user_data, signals, rules, locks, logs
    """
    src_dir = Path(source_taiwan_dir) if source_taiwan_dir else taiwan_data_root()
    if not src_dir.exists():
        raise FileNotFoundError(f"Source Taiwan directory does not exist: {src_dir}")

    # Verify required subdirectories
    twse_obs_dir = src_dir / "observed_universe" / "exchange=TWSE"
    if not twse_obs_dir.exists():
        raise FileNotFoundError(f"Missing observed_universe/exchange=TWSE in {src_dir}")

    classif_dir = src_dir / "historical_classification"
    if not classif_dir.exists():
        raise FileNotFoundError(f"Missing historical_classification in {src_dir}")

    evidence_dir = src_dir / "instrument_evidence"
    if not evidence_dir.exists():
        raise FileNotFoundError(f"Missing instrument_evidence in {src_dir}")

    adj_dir = src_dir / "adj_factor"
    if not adj_dir.exists():
        raise FileNotFoundError(f"Missing adj_factor in {src_dir}")

    files_to_pack: list[Path] = []

    # 1. TWSE observed_universe partitions
    twse_partitions = sorted(twse_obs_dir.glob("date=*/part.parquet"))
    files_to_pack.extend(twse_partitions)

    # TWSE month verification
    month_verif = src_dir / "observed_universe" / "_month_verification_TWSE.json"
    if month_verif.exists():
        files_to_pack.append(month_verif)

    # 2. Historical classification
    classif_partitions = sorted(classif_dir.glob("date=*/part.parquet"))
    files_to_pack.extend(classif_partitions)

    # 3. Instrument evidence
    evidence_files = sorted(evidence_dir.glob("*.parquet"))
    files_to_pack.extend(evidence_files)

    # 4. Adj factor
    adj_events = adj_dir / "events.parquet"
    if adj_events.exists():
        files_to_pack.append(adj_events)
    adj_coverage = adj_dir / "coverage.json"
    if adj_coverage.exists():
        files_to_pack.append(adj_coverage)

    # Gather file entries and filter exclusions
    file_entries: list[BundleFileEntry] = []
    final_pack_list: list[Path] = []

    for f in files_to_pack:
        rel = str(f.relative_to(src_dir)).replace("\\", "/")
        if _is_excluded(rel):
            logger.warning("Excluding forbidden file from Historical Bundle: %s", rel)
            continue
        sha = _compute_sha256(f)
        size = f.stat().st_size
        file_entries.append(BundleFileEntry(path=rel, size_bytes=size, sha256=sha))
        final_pack_list.append(f)

    # Determine date range from TWSE census partitions
    twse_dates = [p.parent.name.replace("date=", "") for p in twse_partitions]
    twse_dates.sort()
    start_date = twse_dates[0] if twse_dates else "2015-01-05"
    end_date = twse_dates[-1] if twse_dates else "2026-09-25"

    pkg_name = package_name or f"nanachi-tw-historical-bundle-{start_date}_to_{end_date}"
    output_zip = src_dir.parent / f"{pkg_name}.zip" if output_zip is None else Path(output_zip)
    output_zip.parent.mkdir(parents=True, exist_ok=True)

    manifest = BundleManifest(
        bundle_type="historical",
        package_name=pkg_name,
        created_at=datetime.now(UTC).isoformat(),
        data_through=end_date,
        date_range={"start": start_date, "end": end_date},
        statistics={
            "twse_census_partitions": len(twse_partitions),
            "classification_partitions": len(classif_partitions),
            "evidence_tables": len(evidence_files),
            "corporate_actions_present": adj_events.exists(),
            "files_count": len(file_entries),
        },
        sources=[
            {"name": "Taiwan Stock Exchange Corporation (TWSE)", "type": "official_public_data"},
            {"name": "MOPS / ISIN registries", "type": "official_public_data"},
        ],
        files=file_entries,
    )

    manifest_json = manifest.model_dump_json(indent=2)
    sums_lines = [f"{e.sha256}  data/taiwan/{e.path}\n" for e in file_entries]

    tmp_zip = output_zip.with_suffix(f".tmp_{uuid.uuid4().hex[:6]}")
    try:
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", manifest_json)
            z.writestr("SHA256SUMS.txt", "".join(sums_lines))
            for f in final_pack_list:
                rel = str(f.relative_to(src_dir)).replace("\\", "/")
                z.write(f, f"data/taiwan/{rel}")

        bundle_sha = _compute_sha256(tmp_zip)
        manifest.bundle_sha256 = bundle_sha
        tmp_zip.replace(output_zip)
        logger.info("Successfully built Historical Bundle at %s (SHA-256: %s)", output_zip, bundle_sha)
        return output_zip, manifest
    except Exception:
        if tmp_zip.exists():
            tmp_zip.unlink(missing_ok=True)
        raise


def verify_bundle(bundle_zip_path: Path) -> BundleManifest:
    """Verify bundle zip integrity, structure, manifest, and individual SHA-256 hashes.

    Raises:
        FileNotFoundError: If zip file does not exist.
        ValueError: If zip is corrupted, contains Zip Slip attempts, or checksum mismatches.
    """
    bundle_path = Path(bundle_zip_path)
    if not bundle_path.exists():
        raise FileNotFoundError(f"Bundle file not found: {bundle_path}")

    with zipfile.ZipFile(bundle_path, "r") as z:
        names = z.namelist()
        # Security: Anti-Zip Slip check on all members
        for name in names:
            if os.path.isabs(name) or name.startswith(("/", "\\")):
                raise ValueError(f"Zip Slip attack detected: absolute path {name}")
            if ".." in name.replace("\\", "/").split("/"):
                raise ValueError(f"Zip Slip attack detected: path traversal in {name}")
            if ":" in name:
                raise ValueError(f"Zip Slip attack detected: drive letter in {name}")

        if "manifest.json" not in names:
            raise ValueError(f"Invalid bundle: missing manifest.json in {bundle_path.name}")

        manifest_data = json.loads(z.read("manifest.json").decode("utf-8"))
        manifest = BundleManifest.model_validate(manifest_data)

        # Check each declared file in manifest
        for entry in manifest.files:
            zip_member_path = f"data/taiwan/{entry.path}"
            if zip_member_path not in names:
                raise ValueError(f"Bundle missing declared file: {zip_member_path}")

            info = z.getinfo(zip_member_path)
            if info.file_size != entry.size_bytes:
                raise ValueError(
                    f"File size mismatch for {entry.path}: expected {entry.size_bytes}, got {info.file_size}"
                )

            # Compute and verify SHA256 of file stream
            hasher = hashlib.sha256()
            with z.open(zip_member_path) as stream:
                while chunk := stream.read(128 * 1024):
                    hasher.update(chunk)
            actual_sha = hasher.hexdigest().lower()
            if actual_sha != entry.sha256.lower():
                raise ValueError(
                    f"SHA-256 mismatch for {entry.path}: expected {entry.sha256}, got {actual_sha}"
                )

    return manifest


def install_bundle(
    bundle_zip_path: Path,
    target_taiwan_dir: Path | None = None,
    verify_checksums: bool = True,
) -> dict[str, Any]:
    """Safely install/merge a verified bundle into target_taiwan_dir.

    Preserves existing partitions and files that are not part of the bundle.
    Replaces identical files atomically without breaking active readers.
    """
    bundle_path = Path(bundle_zip_path)
    dest_taiwan = Path(target_taiwan_dir) if target_taiwan_dir else taiwan_data_root()
    dest_taiwan = dest_taiwan.resolve()

    # Explicit security check: NEVER install into user_data
    if "user_data" in [p.lower() for p in dest_taiwan.parts]:
        raise RuntimeError(f"Security violation: target path cannot be inside user_data: {dest_taiwan}")

    # 1. Verify bundle
    manifest = verify_bundle(bundle_path) if verify_checksums else None
    if manifest is None:
        with zipfile.ZipFile(bundle_path, "r") as z:
            manifest_data = json.loads(z.read("manifest.json").decode("utf-8"))
            manifest = BundleManifest.model_validate(manifest_data)

    dest_taiwan.mkdir(parents=True, exist_ok=True)

    installed_files = 0
    with (
        tempfile.TemporaryDirectory(prefix="nanachi_tw_install_"),
        zipfile.ZipFile(bundle_path, "r") as z,
    ):
        for member in z.infolist():
                filename = member.filename
                # Skip manifest and checksum files from data root installation
                if filename in ("manifest.json", "SHA256SUMS.txt"):
                    continue
                if not filename.startswith("data/taiwan/"):
                    continue

                rel_in_taiwan = filename[len("data/taiwan/"):]
                if not rel_in_taiwan:
                    continue

                if _is_excluded(rel_in_taiwan):
                    logger.warning("Skipping forbidden entry during install: %s", rel_in_taiwan)
                    continue

                target_dest = dest_taiwan / rel_in_taiwan
                if member.is_dir():
                    target_dest.mkdir(parents=True, exist_ok=True)
                else:
                    target_dest.parent.mkdir(parents=True, exist_ok=True)
                    # Extract to temp file then atomic replace
                    tmp_dest = target_dest.parent / f"{target_dest.name}.tmp_{uuid.uuid4().hex[:6]}"
                    with z.open(member) as src, open(tmp_dest, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    tmp_dest.replace(target_dest)
                    installed_files += 1

    return {
        "ok": True,
        "bundle_type": manifest.bundle_type,
        "package_name": manifest.package_name,
        "data_through": manifest.data_through,
        "installed_files": installed_files,
        "target_directory": str(dest_taiwan),
    }

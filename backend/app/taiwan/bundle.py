"""Taiwan Market Data Bundle Creation, Verification, and Installation Service.

Implements the minimal Core Bundle and Historical Bundle packaging specified
by the Taiwan Data Bundle Audit (2026-09-26 in docs/taiwan-data-sources.md):
  - Core Bundle: security_master.parquet + daily/ (OHLCV for daily screening / charting)
  - Historical Bundle: TWSE observed_universe + historical_classification + instrument_evidence + adj_factor
    (Optional data for Quant / Primary OOS evaluation)
  - Strict exclusion list: user_data, credentials, alerts, portfolio, AI conversations,
    live_quant signals, locks, logs, exports, and unverified data sources.
  - Manifest & SHA-256 verification, safe staging extraction (anti-Zip Slip),
    staged atomic commit with rollback, and fail-closed data integrity validation.
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
    "*live_quant*",
    "*monitor_rules*",
    "*backfill_worker_state*",
    "*.lock*",
    "*.guard*",
    "*secrets*",
    "*credentials*",
    "*.env*",
    "*ai_cache*",
    "*ai_threads*",
    "*alerts*",
    "*portfolio*",
    "*watchlist*",
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


def _validate_manifest_entry(entry_path: str, bundle_type: str) -> None:
    """Validate that entry path is secure, not excluded, and belongs to the bundle type allowlist."""
    norm = entry_path.replace("\\", "/").strip("/")
    if not norm or entry_path.startswith(("/", "\\")) or ":" in entry_path:
        raise ValueError(f"Insecure manifest file entry path: {entry_path}")
    parts = norm.split("/")
    if ".." in parts:
        raise ValueError(f"Zip Slip path traversal detected in manifest entry: {entry_path}")

    # P1 Finding 1: Exclusion list check
    if _is_excluded(norm):
        raise ValueError(f"Forbidden manifest entry matching exclusion pattern: {entry_path}")

    # P1 Finding 1: Strict bundle-type allowlist
    if bundle_type == "core":
        if norm == "security_master.parquet":
            return
        if len(parts) == 3 and parts[0] == "daily" and parts[1].startswith("date=") and parts[2] == "part.parquet":
            return
        raise ValueError(f"File path '{entry_path}' not permitted in Core Bundle allowlist")

    elif bundle_type == "historical":
        if norm == "observed_universe/_month_verification_TWSE.json":
            return
        if (
            len(parts) == 4
            and parts[0] == "observed_universe"
            and parts[1] == "exchange=TWSE"
            and parts[2].startswith("date=")
            and parts[3] == "part.parquet"
        ):
            return
        if (
            len(parts) == 3
            and parts[0] == "historical_classification"
            and parts[1].startswith("date=")
            and parts[2] == "part.parquet"
        ):
            return
        if len(parts) == 2 and parts[0] == "instrument_evidence" and norm.endswith(".parquet"):
            return
        if len(parts) == 2 and parts[0] == "adj_factor" and parts[1] in ("events.parquet", "coverage.json"):
            return
        raise ValueError(f"File path '{entry_path}' not permitted in Historical Bundle allowlist")

    else:
        raise ValueError(f"Unsupported bundle type: {bundle_type}")


def build_core_bundle(
    output_zip: Path | None = None,
    source_taiwan_dir: Path | None = None,
    package_name: str | None = None,
) -> tuple[Path, BundleManifest]:
    """Package Core Bundle (security_master.parquet + daily/).

    Returns (bundle_zip_path, manifest).
    Raises:
        FileNotFoundError: If essential files or directories are missing.
        ValueError: If security_master or daily partitions are corrupt, unreadable, or empty.
    """
    src_dir = Path(source_taiwan_dir) if source_taiwan_dir else taiwan_data_root()
    if not src_dir.exists():
        raise FileNotFoundError(f"Source Taiwan directory does not exist: {src_dir}")

    sec_master_path = src_dir / "security_master.parquet"
    if not sec_master_path.exists():
        raise FileNotFoundError(f"Missing security_master.parquet in {src_dir}")

    # P2 Finding 5: Fail-closed validation for security_master.parquet readability and schema
    try:
        sec_df = pl.read_parquet(sec_master_path)
        if sec_df.is_empty():
            raise ValueError(f"security_master.parquet is empty: {sec_master_path}")
        required_sec_cols = {"symbol", "name", "exchange"}
        missing_sec = required_sec_cols - set(sec_df.columns)
        if missing_sec:
            raise ValueError(
                f"security_master.parquet missing required columns {sorted(missing_sec)}: {sec_master_path}"
            )
        sec_rows = len(sec_df)
    except Exception as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError(f"Failed to read and validate security_master.parquet: {exc}") from exc

    daily_dir = src_dir / "daily"
    if not daily_dir.exists():
        raise FileNotFoundError(f"Missing daily directory in {src_dir}")

    daily_files = sorted(daily_dir.glob("date=*/part.parquet"))
    if not daily_files:
        raise ValueError(f"No daily partitions found in {daily_dir}")

    # P2 Finding 5: Fail-closed validation for every daily partition's readability and schema
    required_daily_cols = {"symbol", "date", "open", "high", "low", "close", "volume"}
    total_rows = 0
    for p in daily_files:
        try:
            part_df = pl.read_parquet(p)
            if part_df.is_empty():
                raise ValueError(f"Daily partition is empty: {p}")
            missing_daily = required_daily_cols - set(part_df.columns)
            if missing_daily:
                raise ValueError(
                    f"Daily partition missing required columns {sorted(missing_daily)}: {p}"
                )
            total_rows += len(part_df)
        except Exception as exc:
            if isinstance(exc, ValueError):
                raise
            raise ValueError(f"Failed to read and validate daily partition {p}: {exc}") from exc

    # Gather file entries
    file_entries: list[BundleFileEntry] = []

    # 1. security_master
    sec_rel = "security_master.parquet"
    sec_size = sec_master_path.stat().st_size
    sec_sha = _compute_sha256(sec_master_path)
    file_entries.append(BundleFileEntry(path=sec_rel, size_bytes=sec_size, sha256=sec_sha))

    # 2. daily partitions
    dates: list[str] = []
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
    Raises:
        FileNotFoundError: If required source directory is missing.
        ValueError: If TWSE census partitions or required datasets are missing or empty.
    """
    src_dir = Path(source_taiwan_dir) if source_taiwan_dir else taiwan_data_root()
    if not src_dir.exists():
        raise FileNotFoundError(f"Source Taiwan directory does not exist: {src_dir}")

    # Verify required subdirectories
    twse_obs_dir = src_dir / "observed_universe" / "exchange=TWSE"
    if not twse_obs_dir.exists():
        raise FileNotFoundError(f"Missing observed_universe/exchange=TWSE in {src_dir}")

    # P2 Finding 4: Reject historical bundles with no TWSE census partitions (no fallback dates)
    twse_partitions = sorted(twse_obs_dir.glob("date=*/part.parquet"))
    if not twse_partitions:
        raise ValueError(
            f"No TWSE census partitions found in {twse_obs_dir}; cannot build historical bundle"
        )

    classif_dir = src_dir / "historical_classification"
    if not classif_dir.exists():
        raise FileNotFoundError(f"Missing historical_classification in {src_dir}")

    classif_partitions = sorted(classif_dir.glob("date=*/part.parquet"))
    if not classif_partitions:
        raise ValueError(
            f"No classification partitions found in {classif_dir}; cannot build historical bundle"
        )

    evidence_dir = src_dir / "instrument_evidence"
    if not evidence_dir.exists():
        raise FileNotFoundError(f"Missing instrument_evidence in {src_dir}")

    evidence_files = sorted(evidence_dir.glob("*.parquet"))
    if not evidence_files:
        raise ValueError(
            f"No evidence parquet files found in {evidence_dir}; cannot build historical bundle"
        )

    adj_dir = src_dir / "adj_factor"
    if not adj_dir.exists():
        raise FileNotFoundError(f"Missing adj_factor in {src_dir}")

    adj_events = adj_dir / "events.parquet"
    if not adj_events.exists():
        raise ValueError(
            f"Missing required adj_factor/events.parquet in {adj_dir}; cannot build historical bundle"
        )

    files_to_pack: list[Path] = []
    files_to_pack.extend(twse_partitions)

    # P1 Finding 2: Require valid TWSE month-verification marker
    month_verif = src_dir / "observed_universe" / "_month_verification_TWSE.json"
    if not month_verif.exists():
        raise ValueError(
            f"Missing required _month_verification_TWSE.json in {src_dir / 'observed_universe'}; cannot build historical bundle"
        )
    try:
        verif_data = json.loads(month_verif.read_text(encoding="utf-8"))
        if not isinstance(verif_data, (dict, list)) or not verif_data:
            raise ValueError(f"Invalid or empty _month_verification_TWSE.json in {month_verif}")
    except Exception as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError(f"Failed to read/parse _month_verification_TWSE.json: {exc}") from exc
    files_to_pack.append(month_verif)

    files_to_pack.extend(classif_partitions)
    files_to_pack.extend(evidence_files)
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

    # P2 Finding 4: Determine date range strictly from verified TWSE census partitions
    twse_dates = [p.parent.name.replace("date=", "") for p in twse_partitions]
    twse_dates.sort()
    start_date = twse_dates[0]
    end_date = twse_dates[-1]

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

    P1 Finding 1: Strict Zip Slip check rejecting absolute paths, traversal, drive letters.
    P1 Finding 2: Rejects any archive members omitted from the manifest or undeclared.

    Raises:
        FileNotFoundError: If zip file does not exist.
        ValueError: If zip is corrupted, contains Zip Slip attempts, checksum mismatches,
                    or undeclared data files omitted from manifest.
    """
    bundle_path = Path(bundle_zip_path)
    if not bundle_path.exists():
        raise FileNotFoundError(f"Bundle file not found: {bundle_path}")

    with zipfile.ZipFile(bundle_path, "r") as z:
        names = z.namelist()

        # Security: Anti-Zip Slip check on all members
        for name in names:
            norm_name = name.replace("\\", "/")
            if os.path.isabs(name) or norm_name.startswith("/"):
                raise ValueError(f"Zip Slip attack detected: absolute path {name}")
            if ":" in name:
                raise ValueError(f"Zip Slip attack detected: drive letter in {name}")
            parts = [p for p in norm_name.split("/") if p]
            if ".." in parts:
                raise ValueError(f"Zip Slip attack detected: path traversal in {name}")

        if "manifest.json" not in names:
            raise ValueError(f"Invalid bundle: missing manifest.json in {bundle_path.name}")

        manifest_data = json.loads(z.read("manifest.json").decode("utf-8"))
        manifest = BundleManifest.model_validate(manifest_data)

        # P1 Finding 2: Exact declaration matching between ZIP members and manifest.files
        # Every non-metadata member in the archive MUST be in manifest.files, and vice versa.
        declared_map: dict[str, BundleFileEntry] = {}
        for entry in manifest.files:
            # P1 Finding 1: Disallow crafted entry paths, exclusions, and non-allowlist paths
            _validate_manifest_entry(entry.path, manifest.bundle_type)
            entry_norm = entry.path.replace("\\", "/").strip("/")
            zip_member_path = f"data/taiwan/{entry_norm}"
            declared_map[zip_member_path] = entry

        # Check for any archive member omitted from manifest
        for info in z.infolist():
            fname = info.filename.replace("\\", "/")
            if info.is_dir():
                continue
            if fname in ("manifest.json", "SHA256SUMS.txt"):
                continue
            if not fname.startswith("data/taiwan/"):
                raise ValueError(f"Invalid bundle: unexpected file outside data/taiwan: {fname}")
            if fname not in declared_map:
                raise ValueError(f"Untrusted archive member omitted from manifest: {fname}")

        # Check each declared file in manifest
        for zip_member_path, entry in declared_map.items():
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

    P1 Finding 1: Strict destination containment resolution.
    P1 Finding 2: Only installs verified manifest entries; undeclared files rejected.
    P2 Finding 3: Stages complete bundle and validates snapshot before committing to live DATA_DIR.
                 Rolls back cleanly on any write error so live DATA_DIR is never left mixed.
    """
    bundle_path = Path(bundle_zip_path)
    dest_taiwan = Path(target_taiwan_dir) if target_taiwan_dir else taiwan_data_root()
    dest_taiwan = dest_taiwan.resolve()

    # Explicit security check: NEVER install into user_data
    if "user_data" in [p.lower() for p in dest_taiwan.parts]:
        raise RuntimeError(f"Security violation: target path cannot be inside user_data: {dest_taiwan}")

    # 1. Verify bundle integrity, manifest declaration, and Zip Slip
    manifest = verify_bundle(bundle_path) if verify_checksums else None
    if manifest is None:
        with zipfile.ZipFile(bundle_path, "r") as z:
            manifest_data = json.loads(z.read("manifest.json").decode("utf-8"))
            manifest = BundleManifest.model_validate(manifest_data)

    dest_taiwan.mkdir(parents=True, exist_ok=True)

    # P2 Finding 3: Stage the whole bundle into a staging directory first
    with tempfile.TemporaryDirectory(prefix="nanachi_tw_staging_") as stage_tmp_str:
        stage_root = Path(stage_tmp_str) / "taiwan"
        stage_root.mkdir(parents=True, exist_ok=True)

        staged_files: list[tuple[Path, Path]] = []  # (staged_path, live_target_path)

        with zipfile.ZipFile(bundle_path, "r") as z:
            for entry in manifest.files:
                # P1 Finding 1: Enforce exclusion rules and bundle-type allowlist
                _validate_manifest_entry(entry.path, manifest.bundle_type)

                # P1 Finding 1: Normalize and resolve relative path beneath destination
                raw_rel = entry.path.replace("\\", "/")
                clean_rel = raw_rel.strip("/")
                if not clean_rel or ":" in clean_rel:
                    raise ValueError(f"Insecure member path detected: {entry.path}")

                target_dest = (dest_taiwan / clean_rel).resolve()
                staged_dest = (stage_root / clean_rel).resolve()

                # Verify target stays strictly beneath resolved dest_taiwan
                try:
                    if not target_dest.is_relative_to(dest_taiwan):
                        raise ValueError(f"Zip Slip attack detected: {entry.path} resolves outside destination")
                except AttributeError:
                    if not str(target_dest).startswith(str(dest_taiwan)):
                        raise ValueError(f"Zip Slip attack detected: {entry.path} resolves outside destination") from None

                # Disallow escaping to user_data
                if "user_data" in [p.lower() for p in target_dest.parts]:
                    raise RuntimeError(f"Security violation: target path cannot be inside user_data: {target_dest}")

                zip_member_path = f"data/taiwan/{clean_rel}"
                staged_dest.parent.mkdir(parents=True, exist_ok=True)

                with z.open(zip_member_path) as src, open(staged_dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)

                staged_files.append((staged_dest, target_dest))

        # Staging Validation: Verify extracted staged files
        for staged_p, _ in staged_files:
            if not staged_p.exists() or staged_p.stat().st_size == 0:
                raise ValueError(f"Staged file extraction failed or empty: {staged_p}")

        if manifest.bundle_type == "core":
            staged_sec = stage_root / "security_master.parquet"
            if not staged_sec.exists():
                raise ValueError("Core bundle missing staged security_master.parquet")
            sec_scan = pl.read_parquet(staged_sec)
            if sec_scan.is_empty():
                raise ValueError("Staged security_master.parquet is empty")
            # P2 Finding 4: Require daily partitions in every core bundle
            staged_daily = list((stage_root / "daily").glob("date=*/part.parquet"))
            if not staged_daily:
                raise ValueError("Core bundle missing staged daily partitions (daily/date=*/part.parquet)")
            try:
                d_scan = pl.read_parquet(staged_daily[0])
                if d_scan.is_empty():
                    raise ValueError(f"Staged daily partition is empty: {staged_daily[0]}")
            except Exception as exc:
                if isinstance(exc, ValueError):
                    raise
                raise ValueError(f"Failed to read staged daily partition: {exc}") from exc

        elif manifest.bundle_type == "historical":
            staged_obs = stage_root / "observed_universe" / "exchange=TWSE"
            if not staged_obs.exists() or not list(staged_obs.glob("date=*/part.parquet")):
                raise ValueError("Historical bundle missing staged TWSE census partitions")
            # P1 Finding 2: Require valid TWSE month-verification marker
            staged_verif = stage_root / "observed_universe" / "_month_verification_TWSE.json"
            if not staged_verif.exists():
                raise ValueError("Historical bundle missing required _month_verification_TWSE.json")
            try:
                verif_data = json.loads(staged_verif.read_text(encoding="utf-8"))
                if not isinstance(verif_data, (dict, list)) or not verif_data:
                    raise ValueError("Staged _month_verification_TWSE.json is empty or invalid JSON")
            except Exception as exc:
                if isinstance(exc, ValueError):
                    raise
                raise ValueError(f"Staged _month_verification_TWSE.json validation failed: {exc}") from exc

        # P2 Finding 3: Atomic commit with rollback
        # Prepare backup of existing live files in case a write fails midway
        with tempfile.TemporaryDirectory(prefix="nanachi_tw_backup_") as backup_tmp_str:
            backup_root = Path(backup_tmp_str)
            backed_up: list[tuple[Path, Path]] = []  # (live_path, backup_path)
            created_new_live_files: list[Path] = []
            installed_count = 0

            try:
                for staged_p, live_dest in staged_files:
                    live_dest.parent.mkdir(parents=True, exist_ok=True)
                    if live_dest.exists():
                        # Backup existing live file
                        rel_live = live_dest.relative_to(dest_taiwan)
                        backup_p = backup_root / rel_live
                        backup_p.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(live_dest, backup_p)
                        backed_up.append((live_dest, backup_p))
                    else:
                        created_new_live_files.append(live_dest)

                    # Atomic file replace
                    tmp_replace = live_dest.parent / f"{live_dest.name}.tmp_{uuid.uuid4().hex[:6]}"
                    shutil.copy2(staged_p, tmp_replace)
                    tmp_replace.replace(live_dest)
                    installed_count += 1

            except BaseException as commit_err:
                logger.error("Commit failed during bundle install; rolling back live files: %s", commit_err)
                unrecovered_restores: list[tuple[Path, Path, Exception]] = []
                unrecovered_unlinks: list[tuple[Path, Exception]] = []

                # Rollback step 1: restore backed up original files
                for live_dest, backup_p in backed_up:
                    try:
                        shutil.copy2(backup_p, live_dest)
                    except Exception as rb_exc:
                        logger.critical("Rollback failure restoring %s: %s", live_dest, rb_exc)
                        unrecovered_restores.append((live_dest, backup_p, rb_exc))

                # Rollback step 2: remove newly created files
                for new_file in created_new_live_files:
                    try:
                        if new_file.exists():
                            new_file.unlink(missing_ok=True)
                    except Exception as rb_exc:
                        logger.critical("Rollback failure unlinking %s: %s", new_file, rb_exc)
                        unrecovered_unlinks.append((new_file, rb_exc))

                # P1 Finding 3: Propagate rollback failure and preserve backups if rollback failed
                if unrecovered_restores or unrecovered_unlinks:
                    emergency_backup_path = dest_taiwan / f".unrecovered_backup_{uuid.uuid4().hex[:8]}"
                    preserve_msg = ""
                    try:
                        shutil.copytree(backup_root, emergency_backup_path)
                        preserve_msg = f" Original files preserved at: {emergency_backup_path}"
                    except Exception as preserve_exc:
                        preserve_msg = f" Could not copy backup directory ({preserve_exc}). Backups remain in: {backup_root}"

                    restore_failures = [f"{p} ({err})" for p, _, err in unrecovered_restores]
                    unlink_failures = [f"{p} ({err})" for p, err in unrecovered_unlinks]
                    raise RuntimeError(
                        f"CRITICAL: Bundle installation commit failed ({commit_err}) AND rollback FAILED. "
                        f"Target directory is in an inconsistent state! "
                        f"Failed restores: {restore_failures}; Failed unlinks: {unlink_failures}.{preserve_msg}"
                    ) from commit_err

                raise RuntimeError(
                    f"Bundle installation failed and was rolled back cleanly: {commit_err}"
                ) from commit_err

    # P2 Finding 6: Invalidate/reload TaiwanSecurityMaster singleton upon core bundle installation
    if manifest.bundle_type == "core":
        try:
            from app.taiwan.universe import reset_security_master

            reset_security_master()
        except Exception as exc:
            logger.warning("Could not reset security master singleton: %s", exc)

    return {
        "ok": True,
        "bundle_type": manifest.bundle_type,
        "package_name": manifest.package_name,
        "data_through": manifest.data_through,
        "installed_files": installed_count,
        "target_directory": str(dest_taiwan),
    }

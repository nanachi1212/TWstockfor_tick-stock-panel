"""Tests for Taiwan Market Data Bundle creation, verification, security boundaries, and installation."""
from __future__ import annotations

import zipfile
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from app.taiwan.bundle import (
    BundleManifest,
    build_core_bundle,
    build_historical_bundle,
    install_bundle,
    verify_bundle,
)


def _create_mock_security_master(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pl.DataFrame({
        "symbol": ["2330.TWSE", "2454.TWSE"],
        "code": ["2330", "2454"],
        "name": ["台積電", "聯發科"],
        "exchange": ["TWSE", "TWSE"],
        "instrument_type": ["stock", "stock"],
        "listing_status": ["listed", "listed"],
    })
    df.write_parquet(path)


def _create_mock_daily_partition(path: Path, part_date: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pl.DataFrame({
        "symbol": ["2330.TWSE", "2454.TWSE"],
        "date": [date.fromisoformat(part_date)] * 2,
        "open": [590.0, 950.0],
        "high": [595.0, 960.0],
        "low": [588.0, 945.0],
        "close": [593.0, 955.0],
        "volume": [25000000.0, 5000000.0],
        "amount": [14800000000.0, 4775000000.0],
        "quote_ts": [1704153600000, 1704153600000],
    })
    df.write_parquet(path)


def _create_mock_census_partition(path: Path, part_date: str, exchange: str = "TWSE") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pl.DataFrame({
        "raw_code": ["2330", "2454"],
        "name": ["台積電", "聯發科"],
        "date": [date.fromisoformat(part_date)] * 2,
        "exchange": [exchange] * 2,
    })
    df.write_parquet(path)


def _create_mock_historical_data(taiwan_dir: Path) -> None:
    # 1. TWSE observed universe partitions
    twse_dir = taiwan_dir / "observed_universe" / "exchange=TWSE"
    for d in ["2024-01-02", "2024-01-03"]:
        _create_mock_census_partition(twse_dir / f"date={d}" / "part.parquet", d, "TWSE")

    # TWSE month verification
    month_verif = taiwan_dir / "observed_universe" / "_month_verification_TWSE.json"
    month_verif.write_text('{"verified": true, "months": ["2024-01"]}', encoding="utf-8")

    # TPEX observed universe (should be EXCLUDED)
    tpex_dir = taiwan_dir / "observed_universe" / "exchange=TPEX"
    _create_mock_census_partition(tpex_dir / "date=2024-01-02" / "part.parquet", "2024-01-02", "TPEX")

    # 2. Historical classification
    classif_dir = taiwan_dir / "historical_classification"
    for d in ["2024-01-02", "2024-01-03"]:
        p = classif_dir / f"date={d}" / "part.parquet"
        p.parent.mkdir(parents=True, exist_ok=True)
        df = pl.DataFrame({
            "code": ["2330"],
            "date": [date.fromisoformat(d)],
            "instrument_type": ["stock"],
            "industry": ["半導體業"],
        })
        df.write_parquet(p)

    # 3. Instrument evidence
    evidence_dir = taiwan_dir / "instrument_evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    df_ev = pl.DataFrame({"code": ["2330"], "source": ["isin"]})
    df_ev.write_parquet(evidence_dir / "isin_listed.parquet")

    # 4. Adj factor
    adj_dir = taiwan_dir / "adj_factor"
    adj_dir.mkdir(parents=True, exist_ok=True)
    df_adj = pl.DataFrame({"code": ["2330"], "event_date": [date(2024, 1, 2)], "factor": [1.0]})
    df_adj.write_parquet(adj_dir / "events.parquet")
    (adj_dir / "coverage.json").write_text('{"start": "2024-01-02", "end": "2024-01-03"}', encoding="utf-8")


def test_build_core_bundle_and_verify(tmp_path: Path):
    """Test creating a Core Bundle and verifying its contents and checksums."""
    src_taiwan = tmp_path / "src_taiwan"
    _create_mock_security_master(src_taiwan / "security_master.parquet")
    _create_mock_daily_partition(src_taiwan / "daily" / "date=2024-01-02" / "part.parquet", "2024-01-02")
    _create_mock_daily_partition(src_taiwan / "daily" / "date=2024-01-03" / "part.parquet", "2024-01-03")

    # Put a forbidden file to verify exclusion
    (src_taiwan / "signals.sqlite3").write_text("forbidden", encoding="utf-8")
    (src_taiwan / "monitor_rules.json").write_text("{}", encoding="utf-8")

    out_zip = tmp_path / "dist" / "core_bundle.zip"
    zip_path, manifest = build_core_bundle(output_zip=out_zip, source_taiwan_dir=src_taiwan)

    assert zip_path.exists()
    assert manifest.bundle_type == "core"
    assert manifest.statistics["trading_days"] == 2
    assert manifest.statistics["security_master_rows"] == 2
    assert manifest.data_through == "2024-01-03"
    assert manifest.contains_user_data is False

    # Check zip contents
    with zipfile.ZipFile(zip_path, "r") as z:
        names = z.namelist()
        assert "manifest.json" in names
        assert "SHA256SUMS.txt" in names
        assert "data/taiwan/security_master.parquet" in names
        assert "data/taiwan/daily/date=2024-01-02/part.parquet" in names
        assert "data/taiwan/daily/date=2024-01-03/part.parquet" in names
        # Forbidden files must NOT be in zip
        assert "data/taiwan/signals.sqlite3" not in names
        assert "data/taiwan/monitor_rules.json" not in names

    # Verify bundle
    verified_manifest = verify_bundle(zip_path)
    assert verified_manifest.package_name == manifest.package_name
    assert len(verified_manifest.files) == len(manifest.files)


def test_build_historical_bundle_and_verify(tmp_path: Path):
    """Test creating a Historical Bundle and verifying exclusion of TPEX and factors."""
    src_taiwan = tmp_path / "src_taiwan"
    _create_mock_historical_data(src_taiwan)

    # Add factors directory (should be excluded)
    factors_dir = src_taiwan / "factors"
    factors_dir.mkdir(parents=True, exist_ok=True)
    (factors_dir / "factor_panel.parquet").write_text("dummy factors", encoding="utf-8")

    out_zip = tmp_path / "dist" / "historical_bundle.zip"
    zip_path, manifest = build_historical_bundle(output_zip=out_zip, source_taiwan_dir=src_taiwan)

    assert zip_path.exists()
    assert manifest.bundle_type == "historical"
    assert manifest.statistics["twse_census_partitions"] == 2
    assert manifest.statistics["classification_partitions"] == 2
    assert manifest.statistics["evidence_tables"] == 1

    with zipfile.ZipFile(zip_path, "r") as z:
        names = z.namelist()
        assert "data/taiwan/observed_universe/exchange=TWSE/date=2024-01-02/part.parquet" in names
        assert "data/taiwan/observed_universe/_month_verification_TWSE.json" in names
        assert "data/taiwan/historical_classification/date=2024-01-02/part.parquet" in names
        assert "data/taiwan/instrument_evidence/isin_listed.parquet" in names
        assert "data/taiwan/adj_factor/events.parquet" in names
        assert "data/taiwan/adj_factor/coverage.json" in names
        # Excluded items
        assert not any("exchange=TPEX" in n for n in names)
        assert not any("factors" in n for n in names)

    verified = verify_bundle(zip_path)
    assert verified.bundle_type == "historical"


def test_verify_bundle_checksum_mismatch(tmp_path: Path):
    """Tampering with a file inside the bundle must cause verify_bundle to fail."""
    src_taiwan = tmp_path / "src_taiwan"
    _create_mock_security_master(src_taiwan / "security_master.parquet")
    _create_mock_daily_partition(src_taiwan / "daily" / "date=2024-01-02" / "part.parquet", "2024-01-02")

    out_zip = tmp_path / "core.zip"
    build_core_bundle(output_zip=out_zip, source_taiwan_dir=src_taiwan)

    # 1. Tamper with size
    tampered_size_zip = tmp_path / "tampered_size.zip"
    with zipfile.ZipFile(out_zip, "r") as z_in, zipfile.ZipFile(tampered_size_zip, "w") as z_out:
        for item in z_in.infolist():
            data = z_in.read(item.filename)
            if "security_master.parquet" in item.filename:
                data = b"tampered corrupt content"
            z_out.writestr(item, data)

    with pytest.raises(ValueError, match="File size mismatch"):
        verify_bundle(tampered_size_zip)

    # 2. Tamper with exact same size but corrupt content
    tampered_sha_zip = tmp_path / "tampered_sha.zip"
    with zipfile.ZipFile(out_zip, "r") as z_in, zipfile.ZipFile(tampered_sha_zip, "w") as z_out:
        for item in z_in.infolist():
            data = z_in.read(item.filename)
            if "security_master.parquet" in item.filename:
                data = b"X" * len(data)
            z_out.writestr(item, data)

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        verify_bundle(tampered_sha_zip)


def test_verify_bundle_zip_slip_protection(tmp_path: Path):
    """Zip archives containing path traversal entries must be rejected immediately."""
    malicious_zip = tmp_path / "malicious.zip"
    manifest = BundleManifest(
        bundle_type="core",
        package_name="malicious",
        created_at="2026-01-01T00:00:00Z",
        data_through="2026-01-01",
    )
    with zipfile.ZipFile(malicious_zip, "w") as z:
        z.writestr("manifest.json", manifest.model_dump_json())
        z.writestr("../../etc/evil.txt", b"attack")

    with pytest.raises(ValueError, match="Zip Slip attack detected"):
        verify_bundle(malicious_zip)


def test_install_bundle_into_isolated_target(tmp_path: Path):
    """Test installing bundles into a clean isolated target directory."""
    src_taiwan = tmp_path / "src_taiwan"
    _create_mock_security_master(src_taiwan / "security_master.parquet")
    _create_mock_daily_partition(src_taiwan / "daily" / "date=2024-01-02" / "part.parquet", "2024-01-02")

    core_zip = tmp_path / "core.zip"
    build_core_bundle(output_zip=core_zip, source_taiwan_dir=src_taiwan)

    dest_taiwan = tmp_path / "clean_data" / "taiwan"
    res = install_bundle(core_zip, target_taiwan_dir=dest_taiwan)

    assert res["ok"] is True
    assert res["bundle_type"] == "core"
    assert (dest_taiwan / "security_master.parquet").exists()
    assert (dest_taiwan / "daily" / "date=2024-01-02" / "part.parquet").exists()


def test_install_bundle_rejects_user_data_path(tmp_path: Path):
    """install_bundle must refuse to install into paths containing user_data."""
    src_taiwan = tmp_path / "src_taiwan"
    _create_mock_security_master(src_taiwan / "security_master.parquet")
    _create_mock_daily_partition(src_taiwan / "daily" / "date=2024-01-02" / "part.parquet", "2024-01-02")

    core_zip = tmp_path / "core.zip"
    build_core_bundle(output_zip=core_zip, source_taiwan_dir=src_taiwan)

    forbidden_dest = tmp_path / "data" / "user_data" / "taiwan"
    with pytest.raises(RuntimeError, match="Security violation: target path cannot be inside user_data"):
        install_bundle(core_zip, target_taiwan_dir=forbidden_dest)


# ── Regression tests for Codex Review Findings ─────────────────────────

def test_p1_reject_archive_members_omitted_from_manifest(tmp_path: Path):
    """P1 Finding 2: Reject any archive member omitted from the manifest."""
    src_taiwan = tmp_path / "src_taiwan"
    _create_mock_security_master(src_taiwan / "security_master.parquet")
    _create_mock_daily_partition(src_taiwan / "daily" / "date=2024-01-02" / "part.parquet", "2024-01-02")

    out_zip = tmp_path / "core.zip"
    build_core_bundle(output_zip=out_zip, source_taiwan_dir=src_taiwan)

    # Inject an undeclared extra file into the archive
    injected_zip = tmp_path / "injected.zip"
    with zipfile.ZipFile(out_zip, "r") as z_in, zipfile.ZipFile(injected_zip, "w") as z_out:
        for item in z_in.infolist():
            z_out.writestr(item, z_in.read(item.filename))
        z_out.writestr("data/taiwan/extra_untrusted.parquet", b"untrusted data")

    with pytest.raises(ValueError, match="Untrusted archive member omitted from manifest"):
        verify_bundle(injected_zip)


def test_p1_resolve_every_archive_member_beneath_destination(tmp_path: Path):
    """P1 Finding 1: Disallow crafted members that attempt to resolve outside destination."""
    malicious_zip = tmp_path / "crafted_leading_slash.zip"
    manifest = BundleManifest(
        bundle_type="core",
        package_name="crafted",
        created_at="2026-01-01T00:00:00Z",
        data_through="2026-01-01",
        files=[
            {"path": "/tmp/payload.parquet", "size_bytes": 7, "sha256": "35a9e381b1a27567549b5f8a6f783c167ebf809f1c4d6a9e367240484d8be802"}
        ],
    )
    with zipfile.ZipFile(malicious_zip, "w") as z:
        z.writestr("manifest.json", manifest.model_dump_json())
        z.writestr("data/taiwan//tmp/payload.parquet", b"payload")

    # verify_bundle must reject insecure member path
    with pytest.raises(ValueError, match="Insecure manifest file entry path"):
        verify_bundle(malicious_zip)


def test_p2_stage_whole_bundle_and_rollback_on_failure(tmp_path: Path, monkeypatch):
    """P2 Finding 3: Rollback cleanly if commit fails midway, leaving no mixed state."""
    src_taiwan = tmp_path / "src_taiwan"
    _create_mock_security_master(src_taiwan / "security_master.parquet")
    _create_mock_daily_partition(src_taiwan / "daily" / "date=2024-01-02" / "part.parquet", "2024-01-02")
    _create_mock_daily_partition(src_taiwan / "daily" / "date=2024-01-03" / "part.parquet", "2024-01-03")

    core_zip = tmp_path / "core.zip"
    build_core_bundle(output_zip=core_zip, source_taiwan_dir=src_taiwan)

    # Prepare existing live data
    dest_taiwan = tmp_path / "live_taiwan"
    dest_taiwan.mkdir(parents=True, exist_ok=True)
    live_sec = dest_taiwan / "security_master.parquet"
    live_sec.write_text("ORIGINAL_LIVE_SECURITY_MASTER", encoding="utf-8")

    # Simulate write failure during second file replacement
    import shutil
    orig_copy2 = shutil.copy2
    call_count = 0

    def mock_copy2_failing(src, dst, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        # Let security_master pass, but fail on the daily partition
        if "2024-01-02" in str(dst):
            raise OSError("Disk full simulation during commit")
        return orig_copy2(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "copy2", mock_copy2_failing)

    with pytest.raises(RuntimeError, match="installation failed and was rolled back"):
        install_bundle(core_zip, target_taiwan_dir=dest_taiwan)

    # Live security_master must be restored to its original content
    assert live_sec.read_text(encoding="utf-8") == "ORIGINAL_LIVE_SECURITY_MASTER"
    # The failed daily partition must NOT exist
    assert not (dest_taiwan / "daily" / "date=2024-01-02" / "part.parquet").exists()


def test_p2_reject_historical_bundle_with_no_census_partitions(tmp_path: Path):
    """P2 Finding 4: Reject historical bundle build when TWSE census partitions are missing."""
    src_taiwan = tmp_path / "src_taiwan"
    # Create required subdirectories but keep exchange=TWSE empty of date partitions
    (src_taiwan / "observed_universe" / "exchange=TWSE").mkdir(parents=True, exist_ok=True)
    (src_taiwan / "historical_classification").mkdir(parents=True, exist_ok=True)
    (src_taiwan / "instrument_evidence").mkdir(parents=True, exist_ok=True)
    (src_taiwan / "adj_factor").mkdir(parents=True, exist_ok=True)

    out_zip = tmp_path / "empty_hist.zip"
    with pytest.raises(ValueError, match="No TWSE census partitions found"):
        build_historical_bundle(output_zip=out_zip, source_taiwan_dir=src_taiwan)


def test_p2_reject_unreadable_security_master(tmp_path: Path):
    """P2 Finding 5: Fail-closed when security_master.parquet is empty or unreadable."""
    src_taiwan = tmp_path / "src_taiwan"
    sec_path = src_taiwan / "security_master.parquet"
    sec_path.parent.mkdir(parents=True, exist_ok=True)
    # Write a zero-byte empty file
    sec_path.write_bytes(b"")

    _create_mock_daily_partition(src_taiwan / "daily" / "date=2024-01-02" / "part.parquet", "2024-01-02")

    out_zip = tmp_path / "bad_core.zip"
    with pytest.raises(ValueError, match=r"Failed to read and validate security_master\.parquet"):
        build_core_bundle(output_zip=out_zip, source_taiwan_dir=src_taiwan)


def test_p2_reject_corrupt_daily_partition(tmp_path: Path):
    """P2 Finding 5: Fail-closed when daily partition is unreadable."""
    src_taiwan = tmp_path / "src_taiwan"
    _create_mock_security_master(src_taiwan / "security_master.parquet")

    # Corrupt daily partition
    bad_daily = src_taiwan / "daily" / "date=2024-01-02" / "part.parquet"
    bad_daily.parent.mkdir(parents=True, exist_ok=True)
    bad_daily.write_bytes(b"not a parquet file")

    out_zip = tmp_path / "bad_core.zip"
    with pytest.raises(ValueError, match="Failed to read and validate daily partitions"):
        build_core_bundle(output_zip=out_zip, source_taiwan_dir=src_taiwan)

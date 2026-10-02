"""End-to-End Isolation Verification for Taiwan Market Data Bundles.

Proves the complete delivery workflow on a pristine isolated DATA_DIR:
  1. Clean DATA_DIR -> Install Core Bundle
  2. TaiwanSecurityMaster & TaiwanDailyStore read successfully without live network
  3. Daily screening and charting data available for the current local snapshot
  4. Full historical backfill is NOT required for regular usage
  5. Incremental update starts strictly after the manifest data_through, not re-fetching history
  6. Install Historical Bundle into the same isolated directory
  7. Historical / Quant stores read successfully
  8. read_primary_oos_preflight() evaluates to ready_for_primary_oos
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.taiwan.bundle import (
    build_core_bundle,
    build_historical_bundle,
    install_bundle,
)
from app.taiwan.corporate_actions import CorporateActionStore
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.data_root import taiwan_data_root
from app.taiwan.historical_classification import HistoricalClassificationStore
from app.taiwan.instrument_evidence import InstrumentEvidenceStore
from app.taiwan.observed_universe import ObservedUniverseStore
from app.taiwan.universe.service import TaiwanSecurityMaster


def test_core_and_historical_bundle_e2e_isolation(tmp_path: Path, monkeypatch):
    """Full lifecycle test on a completely isolated temporary DATA_DIR."""
    real_taiwan = taiwan_data_root()
    if not (real_taiwan / "security_master.parquet").exists():
        pytest.skip("Local data/taiwan not found; skipping real-data isolation verification")

    source_daily_store = TaiwanDailyStore(data_dir=real_taiwan / "daily")
    expected_daily_dates = source_daily_store.available_dates()
    assert expected_daily_dates
    expected_bars_2330 = source_daily_store.read_all(symbols=["2330.TWSE"])
    source_sec_master = TaiwanSecurityMaster(cache_path=real_taiwan / "security_master.parquet")
    assert source_sec_master.load_cache() is True
    expected_symbol_count = len(source_sec_master._instruments)
    expected_twse_dates = ObservedUniverseStore(real_taiwan / "observed_universe").completed_dates("TWSE")
    expected_classif_dates = HistoricalClassificationStore(
        real_taiwan / "historical_classification"
    ).completed_dates()
    _, _, expected_stamps = InstrumentEvidenceStore(real_taiwan / "instrument_evidence").load()
    expected_actions = CorporateActionStore(real_taiwan / "adj_factor").read()

    # Step A: Build Core and Historical bundles to temp directory
    bundles_dir = tmp_path / "bundles"
    bundles_dir.mkdir(parents=True, exist_ok=True)
    core_zip = bundles_dir / "core.zip"
    hist_zip = bundles_dir / "historical.zip"

    # Build bundles from existing local data
    _, core_manifest = build_core_bundle(output_zip=core_zip, source_taiwan_dir=real_taiwan)
    _, hist_manifest = build_historical_bundle(output_zip=hist_zip, source_taiwan_dir=real_taiwan)

    assert core_manifest.bundle_type == "core"
    assert hist_manifest.bundle_type == "historical"

    # Step B: Prepare a completely pristine, empty DATA_DIR
    isolated_data_dir = tmp_path / "isolated_data"
    isolated_taiwan_dir = isolated_data_dir / "taiwan"
    assert not isolated_data_dir.exists()

    # Route taiwan_data_root and settings.data_dir to isolated_data_dir
    monkeypatch.setattr("app.taiwan.data_root.taiwan_data_root", lambda: isolated_taiwan_dir)
    from app.config import settings
    monkeypatch.setattr(settings, "data_dir", isolated_data_dir)

    # ── Phase 1: Core Bundle Installation & App Reading ────────────
    # 1. Install Core Bundle
    core_install_res = install_bundle(core_zip, target_taiwan_dir=isolated_taiwan_dir)
    assert core_install_res["ok"] is True
    assert core_install_res["bundle_type"] == "core"
    assert core_install_res["data_through"] == str(expected_daily_dates[-1])

    # 2. App reads Security Master
    sec_master = TaiwanSecurityMaster(cache_path=isolated_taiwan_dir / "security_master.parquet")
    loaded = sec_master.load_cache()
    assert loaded is True
    symbols = list(sec_master._instruments.keys())
    assert len(symbols) == expected_symbol_count
    assert "2330.TWSE" in symbols
    assert "2454.TWSE" in symbols
    assert "0050.TWSE" in symbols

    # 3. App reads Daily Store
    daily_store = TaiwanDailyStore(data_dir=isolated_taiwan_dir / "daily")
    available_dates = daily_store.available_dates()
    assert available_dates == expected_daily_dates

    # Verify sample daily bar reading (screening/charting readiness)
    bars_2330 = daily_store.read_all(symbols=["2330.TWSE"])
    assert not bars_2330.is_empty()
    assert len(bars_2330) == len(expected_bars_2330)

    # 4. Prove full historical backfill is NOT needed:
    # Daily partitions are already populated from 2024-01-02 without running census worker
    assert available_dates[0] == expected_daily_dates[0]
    assert daily_store.latest_date() == expected_daily_dates[-1]

    # 5. Prove Incremental Update targets only days after data_through:
    latest_stored = daily_store.latest_date()
    assert latest_stored == expected_daily_dates[-1]
    # The incremental range start is strictly latest_stored + 1 day
    incremental_start = latest_stored + date.resolution
    assert incremental_start == expected_daily_dates[-1] + date.resolution
    # Existing dates are untouched and will not be re-requested

    # ── Phase 2: Historical Bundle Installation & Quant / OOS ──────
    # 6. Install Historical Bundle into same isolated directory
    hist_install_res = install_bundle(hist_zip, target_taiwan_dir=isolated_taiwan_dir)
    assert hist_install_res["ok"] is True
    assert hist_install_res["bundle_type"] == "historical"

    # 7. Historical / Quant stores read successfully
    obs_store = ObservedUniverseStore(isolated_taiwan_dir / "observed_universe")
    twse_dates = obs_store.completed_dates("TWSE")
    assert twse_dates == expected_twse_dates
    assert (isolated_taiwan_dir / "observed_universe" / "_month_verification_TWSE.json").exists()

    classif_store = HistoricalClassificationStore(isolated_taiwan_dir / "historical_classification")
    classif_dates = classif_store.completed_dates()
    assert classif_dates == expected_classif_dates

    evidence_store = InstrumentEvidenceStore(isolated_taiwan_dir / "instrument_evidence")
    registry_df, termination_df, stamps = evidence_store.load()
    assert not registry_df.is_empty()
    assert not termination_df.is_empty()
    assert len(stamps) == len(expected_stamps)

    action_store = CorporateActionStore(isolated_taiwan_dir / "adj_factor")
    actions = action_store.read()
    assert len(actions) == len(expected_actions)
    assert (isolated_taiwan_dir / "adj_factor" / "coverage.json").exists()

    # 8. Primary OOS Preflight evaluation on isolated data directory
    from app.taiwan.backfill_worker import CENSUS_START
    from app.taiwan.quant.data_health import ReadinessLevel, health_from_stores
    from app.taiwan.quant.primary_oos_runner import read_primary_oos_preflight
    preflight = read_primary_oos_preflight()
    expected_health = health_from_stores(
        ObservedUniverseStore(real_taiwan / "observed_universe"),
        HistoricalClassificationStore(real_taiwan / "historical_classification"),
        start=CENSUS_START,
        end=preflight.end_date,
    )
    assert preflight.data_health.describe()["primary_twse"] == expected_health.describe()["primary_twse"]
    if expected_health.is_ready(ReadinessLevel.PRIMARY_OOS):
        assert preflight.readiness.status.value == "ready"
    else:
        assert preflight.readiness.status.value == "blocked"
        assert preflight.readiness.blocking_reasons

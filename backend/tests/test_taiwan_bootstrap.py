"""Tests for Taiwan historical daily data bootstrap service and security boundaries."""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import tempfile
import zipfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import polars as pl
import pytest

from app.taiwan.bootstrap import (
    TaiwanBootstrapService,
    get_history_status,
    safe_extract_zip,
    verify_extracted_taiwan_data,
)
from app.taiwan.daily_store import TaiwanDailyStore


def _create_dummy_parquet(path: Path, rows: int = 5) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pl.DataFrame({
        "symbol": ["2330.TWSE"] * rows,
        "date": [date(2024, 1, 2)] * rows,
        "open": [590.0] * rows,
        "high": [595.0] * rows,
        "low": [588.0] * rows,
        "close": [593.0] * rows,
        "volume": [25000000.0] * rows,
        "amount": [14800000000.0] * rows,
        "quote_ts": [1704153600000] * rows,
    })
    df.write_parquet(str(path))


def _create_dummy_security_master(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pl.DataFrame({
        "symbol": ["2330.TWSE", "2317.TWSE"],
        "name": ["台積電", "鴻海"],
        "exchange": ["TWSE", "TWSE"],
        "asset_type": ["stock", "stock"],
    })
    df.write_parquet(str(path))


def test_empty_taiwan_data_status(tmp_path: Path):
    """When data/taiwan has no partitions, needs_bootstrap should be True."""
    store = TaiwanDailyStore(data_dir=tmp_path / "taiwan" / "daily")
    status = get_history_status(store=store)

    assert status.has_data is False
    assert status.needs_bootstrap is True
    assert status.trading_days == 0
    assert status.earliest_date is None
    assert status.latest_date is None
    assert status.asset_info["repo"] == "nanachi1212/TWstockfor_tick-stock-panel"


def test_existing_taiwan_data_status(tmp_path: Path):
    """When data/taiwan has sufficient partitions, needs_bootstrap should be False."""
    daily_dir = tmp_path / "taiwan" / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)

    # Create 25 mock partitions
    for day in range(1, 26):
        d_str = f"2024-01-{day:02d}"
        part_dir = daily_dir / f"date={d_str}"
        _create_dummy_parquet(part_dir / "part.parquet")

    store = TaiwanDailyStore(data_dir=daily_dir)
    status = get_history_status(store=store)

    assert status.has_data is True
    assert status.needs_bootstrap is False
    assert status.trading_days == 25
    assert status.earliest_date == "2024-01-01"
    assert status.latest_date == "2024-01-25"


def test_zip_slip_path_traversal_detection(tmp_path: Path):
    """Verify that any zip containing path traversal characters is rejected."""
    bad_zip_path = tmp_path / "malicious.zip"
    extract_target = tmp_path / "extracted"
    extract_target.mkdir(parents=True, exist_ok=True)

    # 1. Zip Slip with ../..
    with zipfile.ZipFile(bad_zip_path, "w") as z:
        z.writestr("../../escape.txt", b"evil content")

    with pytest.raises(ValueError, match="Zip Slip"):
        safe_extract_zip(bad_zip_path, extract_target)

    # 2. Zip Slip with leading slash
    bad_zip_path2 = tmp_path / "malicious_abs.zip"
    with zipfile.ZipFile(bad_zip_path2, "w") as z:
        z.writestr("/tmp/abs_file.txt", b"evil content")

    with pytest.raises(ValueError, match="Zip Slip"):
        safe_extract_zip(bad_zip_path2, extract_target)

    # Ensure no file escaped to parent directory
    assert not (tmp_path / "escape.txt").exists()


def test_checksum_mismatch_failure(tmp_path: Path, monkeypatch):
    """When downloaded zip checksum does not match expected SHA256, abort and fail."""
    # Create a mock zip with some arbitrary content
    mock_zip_data = b"PK\x03\x04 corrupted content"
    wrong_sha = "0000000000000000000000000000000000000000000000000000000000000000"

    class MockResponse:
        status_code = 200
        headers = {"content-length": str(len(mock_zip_data))}
        def iter_bytes(self, chunk_size=1024):
            yield mock_zip_data
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    class MockClient:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def stream(self, method, url):
            return MockResponse()

    monkeypatch.setattr(httpx, "Client", MockClient)

    # Set destination root
    target_root = tmp_path / "dest_taiwan"
    monkeypatch.setattr("app.taiwan.bootstrap.taiwan_data_root", lambda: target_root)

    svc = TaiwanBootstrapService(
        download_url="https://mock/data.zip",
        expected_sha256=wrong_sha,
    )
    job_id = svc.start_bootstrap()
    # Wait for thread to finish
    import time
    for _ in range(50):
        job = svc.get_job(job_id)
        if job and job.status in ("success", "failed"):
            break
        time.sleep(0.05)

    assert job is not None
    assert job.status == "failed"
    assert "SHA256" in (job.error or "")
    # Target directory should not exist or have no partitions
    assert not (target_root / "daily").exists()


def test_network_download_failure(tmp_path: Path, monkeypatch):
    """When network fails during download, job should fail gracefully without leaving partial state."""
    class FailingClient:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def stream(self, method, url):
            raise httpx.ConnectError("Network unreachable")

    monkeypatch.setattr(httpx, "Client", FailingClient)

    svc = TaiwanBootstrapService(download_url="https://mock/data.zip")
    job_id = svc.start_bootstrap()

    import time
    for _ in range(50):
        job = svc.get_job(job_id)
        if job and job.status in ("success", "failed"):
            break
        time.sleep(0.05)

    assert job is not None
    assert job.status == "failed"
    assert "無法連線" in (job.error or "")


def test_user_data_safety_and_bootstrap_success(tmp_path: Path, monkeypatch):
    """Verify that user_data is strictly untouched and valid archive is imported properly."""
    # Setup mock data directory
    data_root = tmp_path / "data"
    taiwan_dir = data_root / "taiwan"
    user_data_dir = data_root / "user_data"
    user_data_dir.mkdir(parents=True, exist_ok=True)

    # Sentinel file in user_data
    sentinel_file = user_data_dir / "user_watchlist.json"
    sentinel_content = '{"favorites": ["2330.TWSE", "2454.TWSE"]}'
    sentinel_file.write_text(sentinel_content, encoding="utf-8")

    # Build a valid in-memory zip archive
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as z:
        # 1. security_master
        sec_bytes = io.BytesIO()
        df_sec = pl.DataFrame({"symbol": ["2330.TWSE"], "name": ["台積電"], "exchange": ["TWSE"], "asset_type": ["stock"]})
        df_sec.write_parquet(sec_bytes)
        z.writestr("data/taiwan/security_master.parquet", sec_bytes.getvalue())

        # 2. daily partitions
        part_bytes = io.BytesIO()
        df_part = pl.DataFrame({
            "symbol": ["2330.TWSE"],
            "date": [date(2024, 1, 2)],
            "open": [590.0],
            "high": [595.0],
            "low": [588.0],
            "close": [593.0],
            "volume": [1000.0],
            "amount": [593000.0],
            "quote_ts": [1704153600000],
        })
        df_part.write_parquet(part_bytes)
        z.writestr("data/taiwan/daily/date=2024-01-02/part.parquet", part_bytes.getvalue())

    zip_bytes = zip_buf.getvalue()
    real_sha256 = hashlib.sha256(zip_bytes).hexdigest()

    class MockValidResponse:
        status_code = 200
        headers = {"content-length": str(len(zip_bytes))}
        def iter_bytes(self, chunk_size=1024):
            yield zip_bytes
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    class MockValidClient:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def stream(self, method, url):
            return MockValidResponse()

    monkeypatch.setattr(httpx, "Client", MockValidClient)
    monkeypatch.setattr("app.taiwan.bootstrap.taiwan_data_root", lambda: taiwan_dir)

    # Mock incremental sync so it doesn't try actual internet refresh
    mock_refresh_svc = MagicMock()
    mock_refresh_svc.refresh_dates.return_value = {"dates_fetched": 1, "total_rows_written": 2000}

    svc = TaiwanBootstrapService(
        download_url="https://mock/valid_asset.zip",
        expected_sha256=real_sha256,
        refresh_service=mock_refresh_svc,
    )

    job_id = svc.start_bootstrap()

    import time
    for _ in range(50):
        job = svc.get_job(job_id)
        if job and job.status in ("success", "failed"):
            break
        time.sleep(0.05)

    assert job is not None
    assert job.status == "success", f"Job failed with error: {job.error}"
    assert job.progress == 100

    # Verify security_master and daily partitions exist in taiwan_dir
    assert (taiwan_dir / "security_master.parquet").exists()
    assert (taiwan_dir / "daily" / "date=2024-01-02" / "part.parquet").exists()

    # Verify user_data sentinel is completely untouched!
    assert sentinel_file.exists()
    assert sentinel_file.read_text(encoding="utf-8") == sentinel_content


def test_update_to_latest_when_data_exists(tmp_path: Path, monkeypatch):
    """When data already exists, update_to_latest should call refresh_dates without redownloading zip."""
    daily_dir = tmp_path / "taiwan" / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)

    part_dir = daily_dir / "date=2024-01-02"
    _create_dummy_parquet(part_dir / "part.parquet")

    store = TaiwanDailyStore(data_dir=daily_dir)
    mock_refresh = MagicMock()
    mock_refresh.refresh_dates.return_value = {"dates_fetched": 3}

    svc = TaiwanBootstrapService(store=store, refresh_service=mock_refresh)
    res = svc.update_to_latest()

    assert res["ok"] is True
    assert mock_refresh.refresh_dates.called


def test_taiwan_bootstrap_api_endpoints(monkeypatch):
    from starlette.testclient import TestClient
    from app.main import app

    client = TestClient(app, client=("127.0.0.1", 50000))

    # 1. history-status
    resp = client.get("/api/taiwan/history-status")
    assert resp.status_code == 200
    data = resp.json()
    assert "has_data" in data
    assert "trading_days" in data
    assert "needs_bootstrap" in data
    assert "asset_info" in data
    assert data["asset_info"]["approx_size_mb"] == 31

    # 2. bootstrap run and job status
    from app.taiwan.bootstrap import BootstrapJobState
    mock_svc = MagicMock()
    mock_job = BootstrapJobState(
        job_id="test-job-123",
        status="downloading",
        stage="下載資料包",
        progress=25,
        message="下載中...",
    )
    mock_svc.start_bootstrap.return_value = "test-job-123"
    mock_svc.get_job.return_value = mock_job
    mock_svc.update_to_latest.return_value = {
        "ok": True,
        "already_current": True,
        "message": "已是最新",
        "dates_fetched": 0,
    }

    monkeypatch.setattr("app.api.taiwan.TaiwanBootstrapService", lambda *a, **kw: mock_svc)

    resp_run = client.post("/api/taiwan/bootstrap/run")
    assert resp_run.status_code == 200
    assert resp_run.json()["job_id"] == "test-job-123"

    resp_job = client.get("/api/taiwan/bootstrap/jobs/test-job-123")
    assert resp_job.status_code == 200
    assert resp_job.json()["job_id"] == "test-job-123"
    assert resp_job.json()["progress"] == 25

    resp_update = client.post("/api/taiwan/bootstrap/update-latest")
    assert resp_update.status_code == 200
    assert resp_update.json()["ok"] is True

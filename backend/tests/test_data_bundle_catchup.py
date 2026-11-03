"""Startup catch-up from the auto-published data bundle (merge never overwrites local files)."""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import polars as pl
import pytest

from app.release_seed import build_release_seed, merge_release_seed
from app.taiwan import bootstrap


def _bundle(root: Path, days: list[str]) -> Path:
    source = root / "source_taiwan"
    pl.DataFrame({
        "symbol": ["2330.TWSE"], "code": ["2330"], "name": ["台積電"],
        "exchange": ["TWSE"], "instrument_type": ["stock"],
    }).write_parquet((source.mkdir(parents=True), source / "security_master.parquet")[1])
    for day in days:
        part = source / "daily" / f"date={day}" / "part.parquet"
        part.parent.mkdir(parents=True)
        pl.DataFrame({"symbol": ["2330.TWSE"], "date": [day], "open": [1.0], "high": [1.0],
                      "low": [1.0], "close": [1.0], "volume": [1.0]}).write_parquet(part)
    return build_release_seed(source, root / "bundle.zip")[0]


def test_merge_adds_missing_files_and_keeps_local_ones(tmp_path):
    bundle = _bundle(tmp_path, ["2026-10-30", "2026-11-02"])
    local = tmp_path / "data" / "taiwan"
    kept = local / "daily" / "date=2026-10-30" / "part.parquet"
    kept.parent.mkdir(parents=True)
    kept.write_bytes(b"local")
    (local / "security_master.parquet").write_bytes(b"local-master")

    assert merge_release_seed(bundle, tmp_path / "data") == 1
    assert kept.read_bytes() == b"local"
    assert (local / "security_master.parquet").read_bytes() == b"local-master"
    assert pl.read_parquet(local / "daily" / "date=2026-11-02" / "part.parquet").height == 1
    assert not list(local.rglob("*.tmp"))


@pytest.fixture
def served(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path, ["2026-10-30", "2026-11-02"])
    payload = bundle.read_bytes()
    pointer = {"tag": "data-daily-2026-11-02", "asset": "b.zip",
               "sha256": hashlib.sha256(payload).hexdigest(), "data_as_of": "2026-11-02"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("data-latest.json"):
            return httpx.Response(200, content=json.dumps(pointer))
        return httpx.Response(200, content=payload)

    data_dir = tmp_path / "data"
    monkeypatch.setattr("app.config.settings.data_dir", data_dir)
    return httpx.Client(transport=httpx.MockTransport(handler)), pointer, data_dir


def _store(latest):
    store = MagicMock()
    store.latest_date.return_value = latest
    return store


def test_small_gap_skips_download(served):
    client, _, data_dir = served
    result = bootstrap.catch_up_from_latest_bundle(_store(date(2026, 10, 30)), client)
    assert result["status"] == "skipped"
    assert not (data_dir / "taiwan").exists()


def test_long_gap_merges_bundle(served):
    client, _, data_dir = served
    result = bootstrap.catch_up_from_latest_bundle(_store(date(2026, 10, 7)), client)
    assert result == {"status": "merged", "local": "2026-10-07", "bundle": "2026-11-02", "files_added": 3}
    assert (data_dir / "taiwan" / "daily" / "date=2026-11-02" / "part.parquet").is_file()


def test_checksum_mismatch_adds_nothing(served):
    client, pointer, data_dir = served
    pointer["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA256"):
        bootstrap.catch_up_from_latest_bundle(_store(None), client)
    assert not (data_dir / "taiwan").exists()
    assert not list(data_dir.glob(".bundle-*"))


def test_unsafe_pointer_is_rejected(served):
    client, pointer, _ = served
    pointer["asset"] = "../../evil.zip"
    with pytest.raises(ValueError, match="pointer"):
        bootstrap.catch_up_from_latest_bundle(_store(None), client)

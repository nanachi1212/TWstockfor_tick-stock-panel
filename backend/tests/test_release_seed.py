from __future__ import annotations

import json
import zipfile
from pathlib import Path

import polars as pl

from app.release_seed import (
    build_release_seed,
    install_release_seed,
    verify_release_seed,
)
from app.repository import migrate_legacy_desktop_data


def _source(root: Path, *, end_date: str = "2026-09-29") -> Path:
    source = root / "source_taiwan"
    source.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["8358.TPEX", "2344.TWSE"],
            "code": ["8358", "2344"],
            "name": ["金居", "華邦電"],
            "exchange": ["TPEX", "TWSE"],
            "instrument_type": ["stock", "stock"],
            "listing_status": ["active", "active"],
        }
    ).write_parquet(source / "security_master.parquet")
    part = source / "daily" / f"date={end_date}" / "part.parquet"
    part.parent.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["8358.TPEX", "2344.TWSE"],
            "date": [end_date, end_date],
            "open": [100.0, 50.0],
            "high": [101.0, 51.0],
            "low": [99.0, 49.0],
            "close": [100.5, 50.5],
            "volume": [1000.0, 2000.0],
        }
    ).write_parquet(part)
    return source


def test_build_verify_and_fresh_install_seed(tmp_path: Path):
    source = _source(tmp_path)
    bundle, manifest = build_release_seed(source, tmp_path / "release-seed.zip")

    verified = verify_release_seed(bundle)
    assert verified.data_as_of == "2026-09-29"
    assert verified.file_count == 2
    assert manifest.contains_user_data is False

    data_dir = tmp_path / "profile" / "data"
    result = install_release_seed(bundle, data_dir)
    assert result.status == "installed"
    assert result.needs_incremental is True
    assert (data_dir / "taiwan" / "security_master.parquet").is_file()
    assert (data_dir / "user_data").exists() is False
    state = json.loads((data_dir / "release-seed-state.json").read_text(encoding="utf-8"))
    assert state["data_as_of"] == "2026-09-29"


def test_existing_newer_market_and_user_data_are_preserved(tmp_path: Path):
    bundle, _ = build_release_seed(_source(tmp_path), tmp_path / "release-seed.zip")
    data_dir = tmp_path / "profile" / "data"
    existing = data_dir / "taiwan" / "daily" / "date=2026-09-30" / "part.parquet"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"newer-market-sentinel")
    private = data_dir / "user_data" / "watchlist.parquet"
    private.parent.mkdir(parents=True)
    private.write_bytes(b"private-sentinel")

    result = install_release_seed(bundle, data_dir)

    assert result.status == "preserved_newer"
    assert existing.read_bytes() == b"newer-market-sentinel"
    assert private.read_bytes() == b"private-sentinel"
    assert not (data_dir / "release-seed-state.json").exists()


def test_corrupted_seed_fails_without_touching_target(tmp_path: Path):
    bundle, _ = build_release_seed(_source(tmp_path), tmp_path / "release-seed.zip")
    corrupt = tmp_path / "corrupt-seed.zip"
    with zipfile.ZipFile(bundle, "r") as source_archive, zipfile.ZipFile(corrupt, "w") as target_archive:
        for info in source_archive.infolist():
            payload = source_archive.read(info.filename)
            if info.filename == "manifest.json":
                manifest = json.loads(payload)
                manifest["files"][0]["sha256"] = "0" * 64
                payload = json.dumps(manifest).encode("utf-8")
            target_archive.writestr(info, payload)
    data_dir = tmp_path / "profile" / "data"
    sentinel = data_dir / "user_data" / "settings.json"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("preserve", encoding="utf-8")

    result = install_release_seed(corrupt, data_dir)

    assert result.status == "failed"
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert not (data_dir / "taiwan").exists()


def test_builder_never_copies_private_siblings(tmp_path: Path):
    source = _source(tmp_path)
    private = source / "user_data" / "portfolio.json"
    private.parent.mkdir(parents=True)
    private.write_text('{"symbol":"8358","cost_basis":123}', encoding="utf-8")

    bundle, _ = build_release_seed(source, tmp_path / "release-seed.zip")

    with zipfile.ZipFile(bundle) as archive:
        assert all("user_data" not in name for name in archive.namelist())
        assert all("portfolio" not in name for name in archive.namelist())


def test_frozen_data_root_uses_local_appdata(monkeypatch, tmp_path: Path):
    from app import config

    monkeypatch.setattr(config, "_IS_FROZEN", True)
    monkeypatch.setattr(config, "user_data_path", lambda *_args, **_kwargs: tmp_path / "Local" / "NanachiStockPanel")

    assert config._user_data_root() == tmp_path / "Local" / "NanachiStockPanel" / "data"


def test_legacy_frozen_data_is_copied_before_seed_without_removing_source(
    monkeypatch, tmp_path: Path
):
    install_dir = tmp_path / "old-install"
    executable = install_dir / "NanachiStockPanel.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    old_private = install_dir / "data" / "user_data" / "preferences.json"
    old_private.parent.mkdir(parents=True)
    old_private.write_text('{"theme":"dark"}', encoding="utf-8")
    old_market = install_dir / "data" / "taiwan" / "daily" / "date=2026-09-28" / "part.parquet"
    old_market.parent.mkdir(parents=True)
    old_market.write_bytes(b"legacy-market")
    target = tmp_path / "LocalAppData" / "NanachiStockPanel" / "data"

    monkeypatch.setattr("app.repository.sys.frozen", True, raising=False)
    monkeypatch.setattr("app.repository.sys.executable", str(executable))

    assert migrate_legacy_desktop_data(target)
    assert (target / "user_data" / "preferences.json").read_text(encoding="utf-8") == '{"theme":"dark"}'
    assert (target / "taiwan" / "daily" / "date=2026-09-28" / "part.parquet").read_bytes() == b"legacy-market"
    assert old_private.exists()
    assert old_market.exists()

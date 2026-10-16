from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sqlite3
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from app.services import device_transfer as dt
from app.taiwan import ai_feedback
from app.taiwan.chip_ranking_tracking import ChipRankingTracker
from app.taiwan.quant.live_store import LiveLedger

CATEGORIES = ["ai_feedback", "research_tracking"]


def seed(root: Path, value: str) -> Path:
    user_dir = root / "user_data"
    ai_feedback.record(symbol="2330.TWSE", helpful=True, note=value, data_dir=root)
    tracker = root / "taiwan" / "chip_ranking_tracking.jsonl"
    tracker.parent.mkdir(parents=True, exist_ok=True)
    tracker.write_text(json.dumps({"date": "2026-10-14", "picks": [], "note": value}) + "\n", encoding="utf-8")
    ledger = LiveLedger(root / "taiwan" / "live_quant")
    with ledger._connect() as db:
        db.execute("INSERT INTO operations VALUES (?, ?)", ("2026-10-14", value))
    with ledger._connect(outcomes=True) as db:
        db.execute("INSERT INTO evaluations VALUES (?,?,?,?,?,?)", (value, "2026-10-14", "2330.TWSE", 1, value, "now"))
    return user_dir


def contents(user_dir):
    ledger = LiveLedger(user_dir.parent / "taiwan" / "live_quant")
    with ledger._connect(read_only=True) as db:
        operations = [tuple(row) for row in db.execute("SELECT * FROM operations")]
    with ledger._connect(outcomes=True, read_only=True) as db:
        evaluations = [tuple(row) for row in db.execute("SELECT * FROM evaluations")]
    return (
        (user_dir / "ai_feedback.jsonl").read_text(encoding="utf-8"),
        (user_dir.parent / "taiwan" / "chip_ranking_tracking.jsonl").read_text(encoding="utf-8"),
        operations, evaluations,
    )


def test_full_backup_tracking_round_trip_and_rollback_includes_wal(tmp_path):
    home, office = seed(tmp_path / "home", "home"), seed(tmp_path / "office", "office")
    before = contents(office)
    metadata = (office / "ai_feedback.jsonl").stat()
    path = home.parent / "taiwan" / "live_quant" / "signals.sqlite3"
    with contextlib.closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("INSERT INTO operations VALUES ('2026-10-15', 'committed-in-wal')")
        db.commit()
        assert path.with_name(path.name + "-wal").stat().st_size > 0
        blob = dt.build_backup(list(dt.PRESETS["full"]), user_dir=home)
        assert dt.preview(blob)["compatible"]
        result = dt.restore(blob, CATEGORIES, user_dir=office)
        assert contents(office) == contents(home)
    assert result["written"] == 4
    saved = office / ".restore_points" / result["restore_point"] / "files" / "ai_feedback.jsonl"
    assert saved.stat().st_mode == metadata.st_mode
    assert saved.stat().st_mtime_ns == metadata.st_mtime_ns
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        assert not any(name.endswith(("-wal", "-shm", ".lock")) for name in archive.namelist())
    dt.rollback(result["restore_point"], user_dir=office)
    assert contents(office) == before


def test_old_backup_preserves_new_tracking_categories(tmp_path):
    home = tmp_path / "home" / "user_data"
    home.mkdir(parents=True)
    office = seed(tmp_path / "office", "office")
    before = contents(office)
    legacy_categories = [c for c in dt.PRESETS["full"] if c not in CATEGORIES]
    blob = dt.build_backup(legacy_categories, user_dir=home)
    dt.restore(blob, legacy_categories, user_dir=office)
    assert contents(office) == before


def test_restore_failure_recovers_tracking_and_sqlite(tmp_path, monkeypatch):
    home, office = seed(tmp_path / "home", "home"), seed(tmp_path / "office", "office")
    blob = dt.build_backup(CATEGORIES, user_dir=home)
    before = contents(office)
    apply = dt._apply
    failed = False

    def fail_after_write(user_dir, rel, data):
        nonlocal failed
        apply(user_dir, rel, data)
        if rel.endswith("signals.sqlite3") and not failed:
            failed = True
            raise OSError("disk unavailable")

    monkeypatch.setattr(dt, "_apply", fail_after_write)
    with pytest.raises(dt.TransferError, match="已自動復原"):
        dt.restore(blob, CATEGORIES, user_dir=office)
    assert contents(office) == before


def test_restore_point_removes_new_tracking_files(tmp_path):
    home = seed(tmp_path / "home", "home")
    office = tmp_path / "office" / "user_data"
    blob = dt.build_backup(CATEGORIES, user_dir=home)
    result = dt.restore(blob, CATEGORIES, user_dir=office)
    assert contents(office) == contents(home)
    dt.rollback(result["restore_point"], user_dir=office)
    assert not (office / "ai_feedback.jsonl").exists()
    assert not list((office.parent / "taiwan").rglob("*.sqlite3"))
    assert not (office.parent / "taiwan" / "chip_ranking_tracking.jsonl").exists()


@pytest.mark.parametrize("bad_sqlite", [b"broken database", None])
def test_invalid_sqlite_blocks_restore_before_any_writes(tmp_path, bad_sqlite):
    home, office = seed(tmp_path / "home", "home"), seed(tmp_path / "office", "office")
    before = contents(office)
    blob = dt.build_backup(CATEGORIES, user_dir=home)
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        entries = {n: archive.read(n) for n in archive.namelist()}
    if bad_sqlite is None:
        with contextlib.closing(sqlite3.connect(":memory:")) as db:
            db.execute("CREATE TABLE wrong (id INTEGER)")
            bad_sqlite = db.serialize()
    name = "files/research_tracking/live_quant/signals.sqlite3"
    entries[name] = bad_sqlite
    manifest = json.loads(entries["manifest.json"])
    manifest["hashes"][name] = hashlib.sha256(bad_sqlite).hexdigest()
    entries["manifest.json"] = json.dumps(manifest).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    assert not dt.preview(output.getvalue())["compatible"]
    with pytest.raises(dt.TransferError, match="不相容"):
        dt.restore(output.getvalue(), CATEGORIES, user_dir=office)
    assert contents(office) == before


def test_sqlite_export_rejects_machine_paths(tmp_path):
    home = seed(tmp_path / "home", r"C:\Users\private\records")
    with pytest.raises(dt.TransferError, match="含本機路徑"):
        dt.build_backup(CATEGORIES, user_dir=home)


@pytest.mark.parametrize("rel", ["../outside.json", "taiwan/../outside.json", "taiwan/daily/part.parquet", "C:/outside.json"])
def test_restore_target_rejects_paths_outside_allowlist(tmp_path, rel):
    with pytest.raises(dt.TransferError):
        dt._apply(tmp_path / "user_data", rel, b"overwrite")
    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.parametrize("writer", ["feedback", "ranking", "quant"])
def test_backup_blocks_tracking_writers_until_snapshot_complete(tmp_path, monkeypatch, writer):
    home = seed(tmp_path / "home", "original")
    snapshot_started, release, writer_started, writer_finished = Event(), Event(), Event(), Event()
    original = dt._read_file

    def read(path):
        snapshot_started.set()
        assert release.wait(5)
        return original(path)

    def write():
        writer_started.set()
        if writer == "feedback":
            ai_feedback.record(symbol="2330.TWSE", helpful=False, data_dir=home.parent)
        elif writer == "ranking":
            ChipRankingTracker(path=home.parent / "taiwan" / "chip_ranking_tracking.jsonl").freeze({
                "date": "2026-10-15", "TWSE": [{"symbol": "2330.TWSE", "chip_score": 80}],
            })
        else:
            with LiveLedger(home.parent / "taiwan" / "live_quant")._connect() as db:
                db.execute("INSERT INTO operations VALUES ('2026-10-15', 'new')")
        writer_finished.set()

    monkeypatch.setattr(dt, "_read_file", read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        backup = pool.submit(dt.build_backup, CATEGORIES, user_dir=home)
        assert snapshot_started.wait(5)
        pending = pool.submit(write)
        assert writer_started.wait(5)
        try:
            assert not writer_finished.wait(0.2)
        finally:
            release.set()
        blob = backup.result(timeout=10)
        pending.result(timeout=10)
    restored = tmp_path / "restored" / "user_data"
    dt.restore(blob, CATEGORIES, user_dir=restored)
    assert len(contents(restored)[0].splitlines()) == 1
    assert len(contents(restored)[1].splitlines()) == 1
    assert contents(restored)[2] == [("2026-10-14", "original")]

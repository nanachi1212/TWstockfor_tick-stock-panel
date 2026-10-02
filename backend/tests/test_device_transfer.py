"""裝置轉移 (.twstock-backup) 備份 / 還原測試 — 只用合成的測試 profile。"""
from __future__ import annotations

import io
import json
import socket
import zipfile
from pathlib import Path

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import device_transfer as dt

FAKE_AI_KEY = "sk-" + "Synthetic0Test1Key2Value3"
FAKE_FINMIND = "finmind-synthetic-token-0001"
FAKE_LINE = "line-synthetic-token-0002"
FAKE_PROFILE_KEY = "sk-" + "SyntheticProfile0Key1Value"
PASSWORD = "correct horse battery"


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _make_profile(user_dir: Path) -> Path:
    _write_json(user_dir / "preferences.json", {
        "realtime_quotes_enabled": True,
        "nav_order": ["/taiwan-screener"],
        "line_target_id": "U-synthetic",
        "custom_export_dir": "C:\\Users\\someone\\Documents\\exports",
    })
    _write_json(user_dir / "secrets.json", {
        "ai_api_key": FAKE_AI_KEY,
        "finmind_token": FAKE_FINMIND,
        "line_channel_access_token": FAKE_LINE,
        "ai_model": "synthetic-model",
        "ai_base_url": "https://example.invalid/v1",
        "ai_codex_command": "C:\\Users\\someone\\.codex\\bin\\codex.exe",
    })
    _write_json(user_dir / "ai_key_profiles.json", [{"id": "p1", "name": "demo", "active": True}])
    _write_json(user_dir / "ai_key_profiles_secrets.json", {"p1": FAKE_PROFILE_KEY})
    _write_json(user_dir / "monitor_rules" / "r1.json", {"id": "r1", "name": "價格突破"})
    _write_json(user_dir / "taiwan_buy_point_strategies.json", {"strategies": [{"id": "b1"}]})
    _write_json(user_dir / "taiwan_selection_snapshots.json", [{"id": "s1"}])
    (user_dir / "alerts.jsonl").write_text('{"id": "a1"}\n', encoding="utf-8")
    (user_dir / "taiwan_ai_research_history.jsonl").write_text(
        json.dumps({
            "id": "research-1", "kind": "report", "run_id": "run-1", "parent_id": None,
            "saved_at": "2026-10-02T10:00:00+08:00", "evidence_digest": "digest-1",
        }) + "\n",
        encoding="utf-8",
    )
    pl.DataFrame({
        "symbol": ["2330.TWSE", "8069.TPEX"], "added_at": ["2026-09-01", "2026-09-02"],
        "note": [None, "觀察"], "group_ids": [["g1"], []],
    }).write_parquet(user_dir / "watchlist.parquet")
    _write_json(user_dir / "watchlist_groups.json", [{"id": "g1", "name": "核心"}])
    # 不得匯出的檔案
    (user_dir / "auth.json").write_text('{"hash": "x"}', encoding="utf-8")
    (user_dir / ".env").write_text("AI_API_KEY=" + FAKE_AI_KEY, encoding="utf-8")
    (user_dir / "tmp.tmp").write_text("x", encoding="utf-8")
    (user_dir.parent / "backend.log").write_text("log", encoding="utf-8")
    (user_dir.parent / ".desktop.lock").write_text("1", encoding="utf-8")
    (user_dir.parent / "taiwan").mkdir(exist_ok=True)
    (user_dir.parent / "taiwan" / "cache.json").write_text("{}", encoding="utf-8")
    return user_dir


BROWSER = {
    "portfolio": {"portfolio_transactions": json.dumps([{"symbol": "2330.TWSE", "shares": 1000}])},
    "ui_preferences": {"tf-theme": "light", "unknown-key": "drop-me"},
}


@pytest.fixture
def home(tmp_path) -> Path:
    return _make_profile(tmp_path / "home" / "user_data")


@pytest.fixture
def office(tmp_path) -> Path:
    d = tmp_path / "office" / "user_data"
    d.mkdir(parents=True)
    return d


def _entries(blob: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def _rezip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, d in entries.items():
            zf.writestr(n, d)
    return buf.getvalue()


def _snapshot(d: Path) -> dict[str, bytes]:
    return {str(p.relative_to(d)): p.read_bytes() for p in d.rglob("*")
            if p.is_file() and dt._RESTORE_POINTS_DIR not in p.parts}


def _full(home: Path, **kw) -> bytes:
    return dt.build_backup(list(dt.PRESETS["full"]), browser_storage=BROWSER, user_dir=home, **kw)


def test_export_manifest(home):
    entries = _entries(_full(home))
    manifest = json.loads(entries["manifest.json"])
    assert manifest["format"] == "twstock-backup"
    assert manifest["backup_version"] == dt.BACKUP_VERSION
    assert manifest["app_version"] and manifest["created_at"]
    assert manifest["source_machine"] == {"os": manifest["source_machine"]["os"]}
    assert manifest["categories"] == list(dt.PRESETS["full"])
    payload = {n: d for n, d in entries.items() if n.startswith("files/")}
    assert manifest["file_count"] == len(payload)
    import hashlib
    assert manifest["hashes"] == {n: hashlib.sha256(d).hexdigest() for n, d in payload.items()}
    assert manifest["secrets"]["included"] is False
    assert "secrets.enc" not in entries


def test_default_backup_excludes_secrets_and_private_runtime_files(home):
    entries = _entries(_full(home))
    blob = b"".join(entries.values())
    for secret in (FAKE_AI_KEY, FAKE_FINMIND, FAKE_LINE, FAKE_PROFILE_KEY):
        assert secret.encode() not in blob
    names = "\n".join(entries)
    for forbidden in ("secrets.json", "ai_key_profiles_secrets", "auth.json", ".env", "tmp.tmp",
                      "backend.log", ".desktop.lock", "taiwan/"):
        assert forbidden not in names
    ai_config = json.loads(entries["files/app_settings/ai_config.json"])
    assert ai_config == {"ai_model": "synthetic-model", "ai_base_url": "https://example.invalid/v1"}
    from app.release_privacy import _binary_credential_hits
    assert _binary_credential_hits(blob) == 0


def test_backup_has_no_absolute_path_or_machine_identity(home):
    entries = _entries(_full(home))
    text = b"".join(entries.values()).decode("utf-8", errors="ignore")
    assert str(home) not in text and home.as_posix() not in text
    assert "C:\\\\Users" not in text and "C:\\Users" not in text and ".codex" not in text
    assert socket.gethostname() not in json.loads(entries["manifest.json"])["source_machine"].values()
    prefs = json.loads(entries["files/app_settings/preferences.json"])
    assert "custom_export_dir" not in prefs and prefs["realtime_quotes_enabled"] is True
    assert json.loads(entries["manifest.json"])["redacted_path_values"] == 1
    # 未列入允許清單的 localStorage key 不匯出
    ui = json.loads(entries["files/ui_preferences/local_storage.json"])
    assert ui == {"tf-theme": "light"}


def test_presets_limit_categories(home):
    names = _entries(dt.build_backup(list(dt.PRESETS["settings"]), browser_storage=BROWSER,
                                     user_dir=home))
    assert not any("watchlist" in n or "portfolio" in n or "selection" in n for n in names)
    assert "files/monitor_rules/monitor_rules/r1.json" in names


def test_secrets_require_password(home):
    with pytest.raises(dt.TransferError, match="備份密碼"):
        _full(home, include_secrets=True, password="short")


def test_encrypted_secrets_round_trip(home, office):
    blob = _full(home, include_secrets=True, password=PASSWORD)
    for secret in (FAKE_AI_KEY, FAKE_FINMIND, FAKE_LINE, FAKE_PROFILE_KEY):
        assert secret.encode() not in blob
        assert secret.encode() not in b"".join(_entries(blob).values())
    assert PASSWORD.encode() not in b"".join(_entries(blob).values())

    info = dt.preview(blob)
    assert info["secrets"]["included"] is True and info["compatible"] is True

    dt.restore(blob, ["app_settings", "secrets"], password=PASSWORD, user_dir=office)
    restored = json.loads((office / "secrets.json").read_text(encoding="utf-8"))
    assert restored == {
        "ai_api_key": FAKE_AI_KEY, "finmind_token": FAKE_FINMIND,
        "line_channel_access_token": FAKE_LINE,
        "ai_model": "synthetic-model", "ai_base_url": "https://example.invalid/v1",
    }
    assert json.loads((office / "ai_key_profiles_secrets.json").read_text()) == {"p1": FAKE_PROFILE_KEY}


def test_restoring_settings_keeps_local_secrets_and_machine_paths(home, office):
    local = {"ai_api_key": "office-key", "ai_codex_command": "D:\\tools\\codex.exe", "ai_model": "old"}
    _write_json(office / "secrets.json", local)
    dt.restore(_full(home), ["app_settings"], user_dir=office)
    restored = json.loads((office / "secrets.json").read_text(encoding="utf-8"))
    assert restored["ai_api_key"] == "office-key"          # 未選 secrets → 保留本機金鑰
    assert restored["ai_codex_command"] == "D:\\tools\\codex.exe"  # 本機路徑不被覆蓋
    assert restored["ai_model"] == "synthetic-model"


def test_wrong_password_writes_nothing(home, office):
    blob = _full(home, include_secrets=True, password=PASSWORD)
    _write_json(office / "secrets.json", {"ai_api_key": "office-key"})
    before = _snapshot(office)
    with pytest.raises(dt.TransferError, match="密碼錯誤"):
        dt.restore(blob, ["app_settings", "secrets"], password="wrong password!", user_dir=office)
    with pytest.raises(dt.TransferError, match="需要輸入備份密碼"):
        dt.restore(blob, ["secrets"], user_dir=office)
    assert _snapshot(office) == before


def test_tampered_payload_is_detected(home):
    entries = _entries(_full(home))
    entries["files/monitor_rules/monitor_rules/r1.json"] = b'{"id": "evil"}'
    with pytest.raises(dt.TransferError, match="雜湊不符"):
        dt.preview(_rezip(entries))


def test_unlisted_file_is_detected(home):
    entries = _entries(_full(home))
    entries["files/app_settings/extra.json"] = b"{}"
    with pytest.raises(dt.TransferError, match="不一致"):
        dt.preview(_rezip(entries))


def test_tampered_manifest_fails_secret_authentication(home, office):
    entries = _entries(_full(home, include_secrets=True, password=PASSWORD))
    manifest = json.loads(entries["manifest.json"])
    manifest["created_at"] = "2000-01-01T00:00:00+00:00"
    entries["manifest.json"] = json.dumps(manifest).encode()
    with pytest.raises(dt.TransferError, match="密碼錯誤, 或備份檔已被修改"):
        dt.restore(_rezip(entries), ["secrets"], password=PASSWORD, user_dir=office)


def test_path_traversal_entry_is_rejected(home):
    entries = _entries(_full(home))
    manifest = json.loads(entries["manifest.json"])
    evil = "files/app_settings/../../evil.json"
    manifest["hashes"][evil] = __import__("hashlib").sha256(b"{}").hexdigest()
    entries[evil] = b"{}"
    entries["manifest.json"] = json.dumps(manifest).encode()
    with pytest.raises(dt.TransferError, match="不合法的路徑"):
        dt.preview(_rezip(entries))


def test_portfolio_and_watchlist_round_trip(home, office):
    blob = dt.build_backup(list(dt.PRESETS["settings_portfolio"]), browser_storage=BROWSER,
                           user_dir=home)
    result = dt.restore(blob, ["watchlist", "portfolio"], user_dir=office)
    assert (office / "watchlist.parquet").read_bytes() == (home / "watchlist.parquet").read_bytes()
    assert pl.read_parquet(office / "watchlist.parquet")["symbol"].to_list() == ["2330.TWSE", "8069.TPEX"]
    assert (office / "watchlist_groups.json").read_bytes() == (home / "watchlist_groups.json").read_bytes()
    assert result["browser_storage"] == {"portfolio": BROWSER["portfolio"]}


def test_research_history_round_trip_preserves_ids_and_evidence_digest(home, office):
    blob = dt.build_backup(["history"], browser_storage={}, user_dir=home)
    result = dt.restore(blob, ["history"], user_dir=office)

    restored = json.loads(
        (office / "taiwan_ai_research_history.jsonl").read_text(encoding="utf-8").strip()
    )
    assert restored["id"] == "research-1"
    assert restored["run_id"] == "run-1"
    assert restored["evidence_digest"] == "digest-1"
    assert result["written"] >= 1


def test_restore_replaces_selected_category_only(home, office):
    _write_json(office / "monitor_rules" / "stale.json", {"id": "stale"})
    _write_json(office / "preferences.json", {"keep": True})
    dt.restore(_full(home), ["monitor_rules"], user_dir=office)
    assert sorted(p.name for p in (office / "monitor_rules").iterdir()) == ["r1.json"]
    assert json.loads((office / "preferences.json").read_text()) == {"keep": True}


def test_restore_failure_rolls_back_atomically(home, office, monkeypatch):
    _write_json(office / "preferences.json", {"keep": True})
    _write_json(office / "monitor_rules" / "stale.json", {"id": "stale"})
    before = _snapshot(office)
    real = dt._atomic_write
    calls = {"n": 0}

    def flaky(path, data):
        calls["n"] += 1
        if calls["n"] == 4:  # 還原點 index 之後, 寫到一半失敗
            raise OSError("disk full")
        real(path, data)

    monkeypatch.setattr(dt, "_atomic_write", flaky)
    with pytest.raises(dt.TransferError, match="已自動復原"):
        dt.restore(_full(home), ["app_settings", "monitor_rules", "watchlist"], user_dir=office)
    assert _snapshot(office) == before


def test_rollback_restore_point(home, office):
    _write_json(office / "preferences.json", {"keep": True})
    before = _snapshot(office)
    result = dt.restore(_full(home), ["app_settings", "watchlist"], user_dir=office)
    assert _snapshot(office) != before
    dt.rollback(result["restore_point"], user_dir=office)
    assert _snapshot(office) == before
    with pytest.raises(dt.TransferError, match="不合法"):
        dt.rollback("../../etc", user_dir=office)


def test_version_mismatch(home, office):
    entries = _entries(_full(home))
    manifest = json.loads(entries["manifest.json"])

    manifest["app_version"] = "0.0.1"
    entries["manifest.json"] = json.dumps(manifest).encode()
    info = dt.preview(_rezip(entries))
    assert info["version_match"] is False and info["compatible"] is True

    manifest["backup_version"] = 99
    entries["manifest.json"] = json.dumps(manifest).encode()
    with pytest.raises(dt.TransferError, match="較新版本"):
        dt.preview(_rezip(entries))

    manifest["backup_version"] = "legacy"
    entries["manifest.json"] = json.dumps(manifest).encode()
    with pytest.raises(dt.TransferError, match="無法識別"):
        dt.preview(_rezip(entries))


def test_schema_incompatibility_blocks_restore(home, office):
    entries = _entries(_full(home))
    manifest = json.loads(entries["manifest.json"])
    bad = b'"not a rule object"'
    name = "files/monitor_rules/monitor_rules/r1.json"
    entries[name] = bad
    manifest["hashes"][name] = __import__("hashlib").sha256(bad).hexdigest()
    manifest["categories"].append("future_category")
    entries["manifest.json"] = json.dumps(manifest).encode()
    info = dt.preview(_rezip(entries))
    assert info["compatible"] is False
    assert any(c["id"] == "monitor_rules" and not c["compatible"] for c in info["categories"])
    with pytest.raises(dt.TransferError):
        dt.restore(_rezip(entries), ["monitor_rules"], user_dir=office)
    assert not (office / "monitor_rules").exists()


def test_api_round_trip(home, office, monkeypatch):
    from app.api import device_transfer as api_mod
    from app.config import settings

    app = FastAPI()
    app.include_router(api_mod.router)
    client = TestClient(app)

    monkeypatch.setattr(settings, "data_dir", home.parent)
    res = client.post("/api/device-transfer/export", json={
        "preset": "settings_portfolio", "include_secrets": True, "password": PASSWORD,
        "browser_storage": BROWSER,
    })
    assert res.status_code == 200
    assert ".twstock-backup" in res.headers["content-disposition"]
    blob = res.content

    bad = client.post("/api/device-transfer/export", json={"preset": "nope"})
    assert bad.status_code == 400

    monkeypatch.setattr(settings, "data_dir", office.parent)
    files = {"file": ("x.twstock-backup", blob, "application/octet-stream")}
    info = client.post("/api/device-transfer/preview", files=files).json()
    assert "secrets" in [c["id"] for c in info["categories"]]

    wrong = client.post("/api/device-transfer/restore", files=files,
                        data={"categories": "secrets", "password": "wrong password!"})
    assert wrong.status_code == 400 and PASSWORD not in wrong.text

    ok = client.post("/api/device-transfer/restore", files=files,
                     data={"categories": "watchlist,portfolio,secrets", "password": PASSWORD})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["browser_storage"]["portfolio"] == BROWSER["portfolio"]
    assert (office / "watchlist.parquet").exists()

    rb = client.post(f"/api/device-transfer/rollback/{body['restore_point']}")
    assert rb.status_code == 200
    assert not (office / "watchlist.parquet").exists()

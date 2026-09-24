from __future__ import annotations

import json

from app.services import alert_store


def test_alert_read_state_and_delete_survive_reload(tmp_path):
    data_dir = tmp_path / "data"
    alert_store.append_many(data_dir, [{
        "ts": 1000,
        "rule_id": "test_rule",
        "source": "price",
        "type": "price_above",
        "symbol": "2330.TWSE",
        "message": "價格突破門檻",
    }])

    stored = alert_store.list_recent(data_dir, days=999999)
    alert_id = stored[0]["alert_id"]
    assert stored[0]["is_read"] is False
    assert alert_store.update_read(data_dir, alert_id) == 1
    reloaded = alert_store.list_recent(data_dir, days=999999)
    assert reloaded[0]["alert_id"] == alert_id
    assert reloaded[0]["is_read"] is True
    assert alert_store.update_read(data_dir) == 0
    assert alert_store.delete_by_id(data_dir, alert_id) is True
    assert alert_store.list_recent(data_dir, days=999999) == []


def test_legacy_alerts_get_stable_ids_for_read_and_delete(tmp_path):
    data_dir = tmp_path / "data"
    path = data_dir / "user_data" / "alerts.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "ts": 1000,
        "rule_id": "legacy_rule",
        "source": "price",
        "type": "price",
        "symbol": "2330.TWSE",
        "message": "舊提醒",
    }) + "\n", encoding="utf-8")

    alert = alert_store.list_recent(data_dir, days=999999)[0]
    assert alert["alert_id"].startswith("legacy_")
    assert alert_store.update_read(data_dir, alert["alert_id"]) == 1
    saved = json.loads(path.read_text(encoding="utf-8").strip())
    assert saved["is_read"] is True
    assert alert_store.delete_by_id(data_dir, alert["alert_id"]) is True


def test_read_and_delete_rewrites_preserve_original_when_atomic_replace_fails(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    alert_store.append_many(data_dir, [{
        "ts": 1000,
        "rule_id": "test_rule",
        "source": "price",
        "type": "price_above",
        "symbol": "2330.TWSE",
        "message": "價格突破門檻",
    }])
    path = data_dir / "user_data" / "alerts.jsonl"
    original = path.read_bytes()
    alert_id = alert_store.list_recent(data_dir, days=999999)[0]["alert_id"]

    def fail_replace(_source, _destination):
        raise OSError("replace failed")

    monkeypatch.setattr(alert_store.os, "replace", fail_replace)
    assert alert_store.update_read(data_dir, alert_id) == 0
    assert path.read_bytes() == original
    assert alert_store.delete_by_id(data_dir, alert_id) is False
    assert path.read_bytes() == original
    assert list(path.parent.glob(".alerts.jsonl.*.tmp")) == []

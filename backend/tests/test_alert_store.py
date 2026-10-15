from __future__ import annotations

import json

import pytest

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
    with pytest.raises(OSError, match="replace failed"):
        alert_store.update_read(data_dir, alert_id)
    assert path.read_bytes() == original
    with pytest.raises(OSError, match="replace failed"):
        alert_store.delete_by_id(data_dir, alert_id)
    assert path.read_bytes() == original
    assert list(path.parent.glob(".alerts.jsonl.*.tmp")) == []


def test_append_many_skips_an_already_persisted_alert_id(tmp_path):
    data_dir = tmp_path / "data"
    event = {
        "alert_id": "stable-alert-id",
        "ts": 1000,
        "rule_id": "test_rule",
        "source": "quant",
        "type": "quant_top10_enter",
        "symbol": "2330.TWSE",
        "message": "進入 Top 10",
    }

    assert alert_store.append_many(data_dir, [event]) == ["stable-alert-id"]
    assert alert_store.append_many(data_dir, [event]) == []
    assert [item["alert_id"] for item in alert_store.list_recent(data_dir, days=999999)] == [
        "stable-alert-id",
    ]


def test_append_many_keeps_existing_file_when_atomic_replace_fails(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    alert_store.append_many(data_dir, [{
        "alert_id": "existing",
        "ts": 1000,
        "rule_id": "test_rule",
        "source": "price",
        "type": "price_above",
        "symbol": "2330.TWSE",
        "message": "既有提醒",
    }])
    path = data_dir / "user_data" / "alerts.jsonl"
    original = path.read_bytes()

    def fail_replace(_source, _destination):
        raise OSError("replace failed")

    monkeypatch.setattr(alert_store.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        alert_store.append_many(data_dir, [
            {"alert_id": "new-1", "ts": 1001, "rule_id": "test_rule"},
            {"alert_id": "new-2", "ts": 1002, "rule_id": "test_rule"},
        ])

    assert path.read_bytes() == original
    assert list(path.parent.glob(".alerts.jsonl.*.tmp")) == []


def test_list_recent_reads_legacy_percent_point_change_pct_as_ratio(tmp_path):
    """Fixed samples: legacy writers stored percent points; current writers store a ratio."""
    data_dir = tmp_path / "data"
    path = data_dir / "user_data" / "alerts.jsonl"
    path.parent.mkdir(parents=True)
    rows = [
        # Legacy rule alert: change_pct was a verbatim copy of trigger_value (percent points).
        {"ts": 6000, "alert_id": "rule-old", "type": "change_pct_below", "trigger_value": -5.65, "change_pct": -5.65},
        # Current rule alert: ratio, trigger_value stays in the rule's percent points.
        {"ts": 5000, "alert_id": "rule-new", "type": "change_pct_above", "trigger_value": 3.66, "change_pct": 0.0366},
        # Legacy anomaly alert: message embeds the same percent-point value.
        {"ts": 4000, "alert_id": "anomaly-old", "type": "watchlist_anomaly", "message": "自選股大跌 -3.89%，現價 1235.0",
         "trigger_value": 1235.0, "change_pct": -3.8911},
        # Current anomaly alert: ratio.
        {"ts": 3000, "alert_id": "anomaly-new", "type": "watchlist_anomaly", "message": "自選股大跌 -5.65%，現價 200",
         "trigger_value": 200, "change_pct": -0.0565},
        # Other producers already store a ratio and are never touched.
        {"ts": 2000, "alert_id": "other", "type": "price_above", "trigger_value": 0.05, "change_pct": 0.05},
    ]
    raw = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    path.write_text(raw, encoding="utf-8")

    pct = {ev["alert_id"]: ev["change_pct"] for ev in alert_store.list_recent(data_dir, days=999999)}

    assert pct == {
        "rule-old": -0.0565, "rule-new": 0.0366,
        "anomaly-old": -0.038911, "anomaly-new": -0.0565,
        "other": 0.05,
    }
    assert path.read_text(encoding="utf-8") == raw  # read-time only; user data is not rewritten

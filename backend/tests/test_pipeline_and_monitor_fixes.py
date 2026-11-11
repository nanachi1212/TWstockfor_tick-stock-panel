"""迴歸測試: 本輪修復的幾處高風險行為(併發單飛 / 重任務槽 / sector fail-closed)。

均為純邏輯, 不觸網, 不依賴真實資料來源。
"""
from __future__ import annotations


from app.services import pipeline_jobs, preferences
from app.services.pipeline_jobs import JobStore

# ── JobStore 單飛 ────────────────────────────────────────────────────────

def test_create_singleflight_dedupes_pending_window(monkeypatch, tmp_path):
    """兩次快速 create() 在 pending 窗口內應複用同一 job(is_new=False)。"""
    monkeypatch.setattr(preferences, "load", lambda: {"data_source_job_timeout_s": 3600})
    store = JobStore(store_dir=tmp_path / "jobs")

    jid1, new1 = store.create()
    assert new1 is True
    assert store.get(jid1)["timeout_s"] == 3600

    # 尚未 start(), job 仍是 pending —— 舊實現會在此另起新 job(併發雙跑根因)
    jid2, new2 = store.create()
    assert jid2 == jid1
    assert new2 is False

    # start() 後仍復用同一活躍 job
    store.start(jid1)
    jid3, new3 = store.create()
    assert jid3 == jid1
    assert new3 is False


def test_create_new_after_terminal(monkeypatch, tmp_path):
    """job 終態(succeed/fail)後, create() 應給出新 job。"""
    monkeypatch.setattr(preferences, "load", lambda: {"data_source_long_job_timeout_s": 5400})
    store = JobStore(store_dir=tmp_path / "jobs")
    jid1, _ = store.create(long_running=True)
    assert store.get(jid1)["timeout_s"] == 5400
    store.start(jid1)
    store.succeed(jid1, {"ok": True})

    jid2, new2 = store.create()
    assert jid2 != jid1
    assert new2 is True


def test_get_rejects_path_traversal_job_id(monkeypatch, tmp_path):
    """job_id 來自 URL: 穿越/絕對路徑不得讀出 job_store 外的 .json。"""
    monkeypatch.setattr(preferences, "load", lambda: {})
    (tmp_path / "secret.json").write_text('{"api_key": "x"}', encoding="utf-8")
    store = JobStore(store_dir=tmp_path / "jobs")
    for bad in ("..\\secret", "../secret", str(tmp_path / "secret")):
        assert store.get(bad) is None

    # 正常 id 仍可從磁盤讀回
    jid, _ = store.create()
    store.start(jid)
    store.succeed(jid, {"ok": True})
    assert store.get(jid)["status"] == "succeeded"


def test_run_slot_is_exclusive():
    """重任務執行槽同一時刻只允許一個持有者(防殭屍併發)。"""
    assert pipeline_jobs.try_acquire_run_slot() is True
    try:
        # 已被佔用, 第二次獲取失敗
        assert pipeline_jobs.try_acquire_run_slot() is False
    finally:
        pipeline_jobs.release_run_slot()
    # 釋放後可再次獲取
    assert pipeline_jobs.try_acquire_run_slot() is True
    pipeline_jobs.release_run_slot()
    # 重複釋放冪等, 不拋
    pipeline_jobs.release_run_slot()


# ── 監控 sector fail-closed ──────────────────────────────────────────────

def _base_price_rule(scope: str) -> dict:
    return {
        "id": "r_test",
        "name": "t",
        "type": "price",
        "conditions": [{"field": "close", "op": ">", "value": 10}],
        "logic": "and",
        "scope": scope,
    }

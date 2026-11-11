"""迴歸測試: 卡死判定從「總時長一刀切」改為「進度停滯」+ 協作式取消 + 執行槽所有權。

背景(使用者反饋): 慢帶寬環境冷啟動全市場拉取超過 20 分鐘被誤標失敗,
拉取執行緒(殭屍)仍在寫盤, UI 狀態與實際不對齊; 重複點擊還可能撞執行鎖。
均為純邏輯, 不觸網。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import pipeline_jobs, preferences
from app.services.pipeline_jobs import JobCancelledError, JobStore


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _reset_module_globals():
    """取消標誌與執行槽是模組級單例, 每個用例前後復位, 避免相互汙染。"""
    pipeline_jobs._CANCEL_FLAGS.clear()
    pipeline_jobs._run_slot_owner = None
    yield
    pipeline_jobs._CANCEL_FLAGS.clear()
    pipeline_jobs._run_slot_owner = None


def _make_running_job(store: JobStore, timeout_s: int) -> str:
    jid, _ = store.create(timeout_s=timeout_s)
    store.start(jid)
    return jid


# ── 進度停滯判定 ────────────────────────────────────────────────────────

def test_stalled_job_is_reaped(monkeypatch, tmp_path):
    """無進度上報超過閾值 → 標記失敗 + 置取消標誌 + 釋放執行槽。"""
    monkeypatch.setattr(preferences, "load", lambda: {})
    store = JobStore(store_dir=tmp_path / "jobs")
    jid = _make_running_job(store, timeout_s=60)
    # 啟動後 5 分鐘無任何進度 → 停滯 300s > 60s
    stale = _iso(_now() - timedelta(minutes=5))
    store._active_jobs[jid]["started_at"] = stale
    store._active_jobs[jid]["last_progress_at"] = stale

    assert pipeline_jobs.try_acquire_run_slot(jid) is True
    store.reap_stale()

    j = store.get(jid)
    assert j["status"] == "failed"
    assert "進度停滯" in j["error"]
    # 協作式取消: 殭屍執行緒通過 flag 感知(記錄已被 fail 彈出, flag 仍在)
    assert pipeline_jobs.is_cancelled(jid)
    # 執行槽已按所有權釋放
    assert pipeline_jobs.try_acquire_run_slot("next") is True


def test_progressing_job_is_not_reaped(monkeypatch, tmp_path):
    """慢但在推進: 總時長遠超閾值, 但進度心跳新鮮 → 不得誤殺(核心迴歸)。"""
    monkeypatch.setattr(preferences, "load", lambda: {})
    store = JobStore(store_dir=tmp_path / "jobs")
    jid = _make_running_job(store, timeout_s=60)
    # 總時長 2 小時(遠超 60s 閾值), 但 10 秒前剛上報過進度
    store._active_jobs[jid]["started_at"] = _iso(_now() - timedelta(hours=2))
    store._active_jobs[jid]["last_progress_at"] = _iso(_now() - timedelta(seconds=10))

    store.reap_stale()
    assert store.get(jid)["status"] == "running"


def test_hard_cap_terminates_endless_progress(tmp_path):
    """進度回調持續但總時長超硬上限 → 兜底終止。"""
    store = JobStore(store_dir=tmp_path / "jobs")
    jid = _make_running_job(store, timeout_s=60)
    beyond = timedelta(seconds=pipeline_jobs.HARD_JOB_TIMEOUT_S + 3600)
    store._active_jobs[jid]["started_at"] = _iso(_now() - beyond)
    store._active_jobs[jid]["last_progress_at"] = _iso(_now())

    store.reap_stale()
    j = store.get(jid)
    assert j["status"] == "failed"
    assert "硬上限" in j["error"]


def test_progress_updates_heartbeat(tmp_path):
    """progress() 刷新 last_progress_at(停滯計時的基準)。"""
    store = JobStore(store_dir=tmp_path / "jobs")
    jid = _make_running_job(store, timeout_s=60)
    store.progress(jid, "sync", 10, "chunk 1/10")
    assert store.get(jid)["last_progress_at"] is not None


# ── 協作式取消 ──────────────────────────────────────────────────────────

def test_progress_raises_after_cancel(tmp_path):
    """取消後, 殭屍執行緒下一次 progress() 回調拋 JobCancelledError 自行退出。"""
    store = JobStore(store_dir=tmp_path / "jobs")
    jid = _make_running_job(store, timeout_s=60)

    pipeline_jobs.request_cancel(jid)
    with pytest.raises(JobCancelledError):
        store.progress(jid, "sync", 20, "chunk 2/10")


def test_progress_raises_after_record_popped(tmp_path):
    """terminate() 已把記錄彈出後, flag 仍需生效(殭屍靠 flag 而非記錄感知)。"""
    store = JobStore(store_dir=tmp_path / "jobs")
    jid = _make_running_job(store, timeout_s=60)
    store.terminate(jid, "超時自動取消")

    # 記錄已從記憶體彈出
    assert store.get(jid)["status"] == "failed"
    with pytest.raises(JobCancelledError):
        store.progress(jid, "sync", 20, "zombie chunk")


def test_cancelled_error_survives_chunk_isolation():
    """JobCancelledError 繼承 BaseException: 同步循環的分塊異常隔離不得吞掉它。"""
    def chunk_loop(cancel_at: int) -> str:
        for i in range(5):
            try:
                if i == cancel_at:
                    raise JobCancelledError("j1")
            except Exception:  # noqa: BLE001  — 分塊隔離的典型寫法
                continue
        return "completed"

    with pytest.raises(JobCancelledError):
        chunk_loop(2)


# ── 執行槽所有權 ────────────────────────────────────────────────────────

def test_run_slot_ownership_guard():
    """非持有者的釋放一律忽略 —— 殭屍執行緒 finally 不得誤釋放新任務的槽。"""
    assert pipeline_jobs.try_acquire_run_slot("jobA") is True
    assert pipeline_jobs.try_acquire_run_slot("jobB") is False

    # 舊 job(殭屍)的 finally 釋放: 槽屬於 jobA, 忽略
    pipeline_jobs.release_run_slot("jobB")
    assert pipeline_jobs.try_acquire_run_slot("jobC") is False

    # 持有者自己釋放後才可用
    pipeline_jobs.release_run_slot("jobA")
    assert pipeline_jobs.try_acquire_run_slot("jobC") is True
    pipeline_jobs.release_run_slot("jobC")


def test_run_slot_reap_release_prevents_zombie_release():
    """reap 強制釋放後, 殭屍晚到的同 owner 釋放是冪等 no-op, 不影響新持有者。"""
    assert pipeline_jobs.try_acquire_run_slot("jobA") is True
    pipeline_jobs.release_run_slot("jobA")  # terminate 的強制釋放

    assert pipeline_jobs.try_acquire_run_slot("jobB") is True  # 新任務立即入槽
    pipeline_jobs.release_run_slot("jobA")  # 僵尸 finally: owner 不匹配 → 忽略
    assert pipeline_jobs.try_acquire_run_slot("jobC") is False  # jobB 仍持有

    pipeline_jobs.release_run_slot("jobB")
    assert pipeline_jobs.try_acquire_run_slot("jobC") is True
    pipeline_jobs.release_run_slot("jobC")

"""非同步盤後管道任務註冊表 — 每個 job 獨立 JSON 檔案。

設計:
  - job_store/ 資料夾,每個 job 一個 {id}.json,最多保留 max_jobs 個檔案
  - running/pending 狀態的 job 僅存記憶體(高頻讀寫)
  - succeeded/failed 後寫入獨立檔案並從記憶體釋放
  - 列表查詢 = 記憶體中的活躍 job + 磁碟檔案掃描,按時間排序
  - 單個查詢 = 記憶體優先,沒有則讀磁碟
  - 建立新 job 前檢查檔案數量,>= max_jobs 時刪除最老的檔案
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

JobStatus = Literal["pending", "running", "succeeded", "failed"]

# 卡死判定閾值(秒)。語意是「進度停滯」而非「總時長」:
# running 期間只要 progress() 還在上報(每個分塊都會回呼), 就永遠不算卡死 ——
# 慢帶寬/高延遲環境下的冷啟動全市場取得可能遠超 20 分鐘, 但只要分塊在推進,
# 不應被誤殺(reap 舊實現按總時長一刀切, 導致 UI 標記失敗而取得執行緒仍在寫盤)。
#
# 閾值按任務類型區分,可在 Web 資料來源設定中調整:
#   - 普通任務(日K管道/擴展/修正/重算): 1200s (20 分鐘無進度)
#   - 長任務(分鐘K全市場同步,資料量是日K的 ~240 倍): 1800s (30 分鐘無進度)
DEFAULT_JOB_TIMEOUT_S = 1200
LONG_JOB_TIMEOUT_S = 1800
# 總時長硬上限(保底): 進度回呼持續上報但永不結束的病態循環無法靠停滯判定捕獲,
# 超過該值無條件終止。取 12h(最長合法任務分鐘K補齊的歷史量級遠小於此)。
HARD_JOB_TIMEOUT_S = 12 * 3600
# 向後相容: 舊呼叫方引用 STALE_JOB_TIMEOUT_S
STALE_JOB_TIMEOUT_S = DEFAULT_JOB_TIMEOUT_S


class JobCancelledError(BaseException):
    """任務已被取消(reap 判定卡死後自動取消,或未來的手動取消)。

    繼承 BaseException 而非 Exception(對齊 asyncio.CancelledError 的設計):
    同步循環內部的分塊異常隔離(``except Exception: continue``)不得吞掉取消信號,
    它必須從 executor 執行緒一路傳播回 API 邊界的 task()。API 層應有獨立
    ``except JobCancelledError`` 分支(job 此時已被 reap 標記 failed,無需再寫狀態)。
    """

    def __init__(self, job_id: str) -> None:
        super().__init__(f"job {job_id} 已取消")
        self.job_id = job_id


# ── 取消標誌註冊表 ──────────────────────────────────────────────────────
# reap 終止 job 時置位; 殭屍執行緒隨後每次 progress() 回呼檢查到即拋
# JobCancelledError 自行退出。flag 獨立於 job 記錄存活 —— fail() 會把記錄從
# _active_jobs 彈出, 但殭屍執行緒仍需通過 flag 感知取消。
# 有界(最多 _CANCEL_FLAG_MAX 條, 淘汰最老): 真卡死的殭屍永遠不會回來清 flag。
_CANCEL_FLAG_MAX = 32
_CANCEL_FLAGS: dict[str, threading.Event] = {}
_CANCEL_FLAGS_LOCK = threading.Lock()


def request_cancel(job_id: str) -> bool:
    """請求取消指定 job。回傳是否存在該 job 的 flag。"""
    with _CANCEL_FLAGS_LOCK:
        ev = _CANCEL_FLAGS.get(job_id)
        if ev is None:
            return False
        ev.set()
        return True


def is_cancelled(job_id: str) -> bool:
    ev = _CANCEL_FLAGS.get(job_id)
    return ev is not None and ev.is_set()


def _register_cancel_flag(job_id: str) -> None:
    with _CANCEL_FLAGS_LOCK:
        if job_id not in _CANCEL_FLAGS:
            _CANCEL_FLAGS[job_id] = threading.Event()
        # 有界淘汰最老(當前活躍 job 總是最新註冊, 不會被誤淘汰)
        while len(_CANCEL_FLAGS) > _CANCEL_FLAG_MAX:
            oldest = next(iter(_CANCEL_FLAGS))
            _CANCEL_FLAGS.pop(oldest)


def _default_store_dir() -> Path:
    from app.config import settings
    return settings.data_dir / "job_store"


_STORE_DIR = _default_store_dir()


class JobStore:
    def __init__(self, max_jobs: int = 50, store_dir: Path = _STORE_DIR) -> None:
        self._max_jobs = max_jobs
        self._store_dir = store_dir
        self._active_jobs: dict[str, dict[str, Any]] = {}   # running/pending
        self._active_id: str | None = None
        self._lock = threading.Lock()
        self._store_dir.mkdir(parents=True, exist_ok=True)

    # ===== persistence =====

    def _write_file(self, job: dict[str, Any]) -> None:
        """將終態 job 寫入獨立 JSON 檔案。"""
        path = self._store_dir / f"{job['id']}.json"
        try:
            path.write_text(
                json.dumps(job, ensure_ascii=False, indent=None),
                encoding="utf-8",
            )
        except Exception:
            logger.warning("failed to write job file %s", path)

    def _read_file(self, job_id: str) -> dict[str, Any] | None:
        """從磁碟讀取單個 job 檔案。"""
        # job_id 來自 URL 路徑參數: 只接受 create() 生成的格式(uuid4().hex[:10]),
        # 防止 "..\\" / 絕對路徑逃出 job_store 讀取任意 .json(路徑穿越)。
        if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{10}", job_id):
            return None
        path = self._store_dir / f"{job_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text("utf-8"))
        except Exception:
            logger.warning("failed to read job file %s", path)
            return None

    def _delete_oldest(self) -> None:
        """刪除最老的 job 檔案,保持檔案數量 < max_jobs。"""
        try:
            files = sorted(self._store_dir.glob("*.json"), key=lambda f: f.stat().st_mtime)
        except Exception:
            return
        while len(files) >= self._max_jobs:
            oldest = files.pop(0)
            try:
                oldest.unlink()
            except Exception:
                logger.warning("failed to delete old job file %s", oldest)

    def _job_files_sorted(self) -> list[dict[str, Any]]:
        """掃描磁碟上所有 job 檔案,按 started_at 從新到舊排序。"""
        jobs: list[dict[str, Any]] = []
        for f in self._store_dir.glob("*.json"):
            try:
                jobs.append(json.loads(f.read_text("utf-8")))
            except Exception:
                continue
        jobs.sort(key=lambda j: j.get("started_at") or "", reverse=True)
        return jobs

    # ===== lifecycle =====

    def create(
        self,
        timeout_s: int | None = None,
        *,
        long_running: bool = False,
    ) -> tuple[str, bool]:
        """單飛建立任務。回傳 (job_id, is_new)。

        去重條件為 **pending ∨ running**(而非僅 running):`/run` 先 create() 再在
        背景任務裡 start() 置 running,兩者之間存在 pending 視窗。舊實現只在 running 時
        重用,兩次快速點擊時首個 job 仍是 pending → 第二次繞過去重、另起並行任務、覆蓋
        _active_id,導致兩條全市場取得同時讀改寫同一 parquet。納入 pending 後該視窗關閉。

        is_new=False 表示重用了已有活躍任務,呼叫方**不得**再調度新的背景任務。

        timeout_s: reap_stale 判定「進度停滯卡死」的閾值。None 時讀取使用者設定。
        long_running: timeout_s 為 None 時,是否讀取長任務設定;普通任務預設
            1200s,分鐘K全市場同步等長任務預設 1800s。
        """
        if timeout_s is None:
            from app.services import preferences
            if long_running:
                timeout_s = preferences.get_data_source_long_job_timeout_s()
            else:
                timeout_s = preferences.get_data_source_job_timeout_s()

        with self._lock:
            if self._active_id:
                active = self._active_jobs.get(self._active_id)
                if active and active.get("status") in ("pending", "running"):
                    return self._active_id, False

            job_id = uuid.uuid4().hex[:10]
            self._active_jobs[job_id] = {
                "id": job_id,
                "status": "pending",
                "stage": "init",
                "progress": 0,
                "stage_pct": 0,
                "log": [],
                "started_at": None,
                "last_progress_at": None,
                "finished_at": None,
                "duration_s": None,
                "result": None,
                "error": None,
                "timeout_s": timeout_s,
            }
            self._active_id = job_id
        _register_cancel_flag(job_id)
        return job_id, True

    def start(self, job_id: str) -> None:
        with self._lock:
            j = self._active_jobs.get(job_id)
            if not j:
                return
            j["status"] = "running"
            j["started_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
            # 心跳基準初始化為啟動時刻: start() 到首次 progress() 之間的
            # 初始化階段(解析標的池等)同樣計入停滯計時。
            j["last_progress_at"] = j["started_at"]

    def succeed(self, job_id: str, result: Any) -> None:
        with self._lock:
            j = self._active_jobs.pop(job_id, None)
            if not j:
                return
            j["status"] = "succeeded"
            j["finished_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
            j["progress"] = 100
            j["result"] = result
            j["duration_s"] = _duration_s(j)
            if self._active_id == job_id:
                self._active_id = None
            self._delete_oldest()
            self._write_file(j)

    def fail(self, job_id: str, error: str) -> None:
        with self._lock:
            j = self._active_jobs.pop(job_id, None)
            if not j:
                return
            j["status"] = "failed"
            j["finished_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
            j["error"] = error
            j["duration_s"] = _duration_s(j)
            if self._active_id == job_id:
                self._active_id = None
            self._delete_oldest()
            self._write_file(j)

    # ===== progress =====

    def progress(self, job_id: str, stage: str, pct: int, msg: str,
                 stage_pct: int | None = None, skip_log: bool = False) -> None:
        with self._lock:
            j = self._active_jobs.get(job_id)
            if not j:
                # 記錄已不在(通常是被 reap 終止後 fail() 彈出)。
                # 殭屍執行緒仍需感知取消 —— flag 檢查不能依賴記錄存在。
                cancelled = _CANCEL_FLAGS.get(job_id)
                if cancelled is not None and cancelled.is_set():
                    raise JobCancelledError(job_id)
                return
            j["stage"] = stage
            j["progress"] = max(0, min(100, int(pct)))
            j["last_progress_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
            if stage_pct is not None:
                j["stage_pct"] = max(0, min(100, int(stage_pct)))
            elif j["stage"] != stage:
                j["stage_pct"] = 0
            entry = {
                "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "stage": stage,
                "msg": msg,
            }
            if skip_log:
                entry["_skip"] = True
            if skip_log and j["log"] and j["log"][-1].get("stage") == stage and j["log"][-1].get("_skip"):
                j["log"][-1] = entry
            else:
                j["log"].append(entry)
                if len(j["log"]) > 200:
                    j["log"] = j["log"][-200:]
        # 鎖外檢查取消: reap 可能在本次更新剛結束後置位,下一次回呼必然命中;
        # 在這裡立即檢查可以把終止延遲壓縮到當次回呼。
        ev = _CANCEL_FLAGS.get(job_id)
        if ev is not None and ev.is_set():
            raise JobCancelledError(job_id)

    # ===== query =====

    def get(self, job_id: str) -> dict[str, Any] | None:
        # 記憶體中的活躍 job 優先
        j = self._active_jobs.get(job_id)
        if j:
            return j
        # 否則從磁碟讀
        return self._read_file(job_id)

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        # 合併: 記憶體中的活躍 job + 磁碟檔案
        all_jobs: list[dict[str, Any]] = list(self._active_jobs.values())
        all_jobs.extend(self._job_files_sorted())
        # 按 started_at 從新到舊排序,去重(理論上不會有重複)
        seen: set[str] = set()
        result: list[dict[str, Any]] = []
        for j in sorted(all_jobs, key=lambda x: x.get("started_at") or "", reverse=True):
            jid = j["id"]
            if jid in seen:
                continue
            seen.add(jid)
            result.append(_summary(j))
            if len(result) >= limit:
                break
        return result

    def active_id(self) -> str | None:
        return self._active_id

    def reap_stale(self, timeout_s: int | None = None) -> None:
        """回收卡死的 running job。兩種判定:

        1. 進度停滯(主判定): 距上次 progress() 上報超過閾值秒數。
           慢帶寬環境下任務只要仍在分塊推進就不會被誤殺 —— 這正是舊的
           總時長判定的問題(冷啟動全市場取得 >20min 就被標死,執行緒卻還在寫盤)。
        2. 總時長硬上限(保底): 進度回呼持續但永不結束的病態循環。

        在 /run 和 /jobs/{id} 輪詢端點都會呼叫 — 保證卡死後任意輪詢都能自愈,
        無需使用者再次手動觸發同步。reload 後的孤兒 task(記憶體裡已無 job 記錄)
        不在此處理:它們沒有 active_id,只能靠 executor 執行緒自然結束或行程重啟。

        終止是**協作式**的: 置 cancel flag → 殭屍執行緒在下一個分塊進度回呼處
        拋 JobCancelledError 自行退出(BaseException,不會被分塊異常隔離吞掉)。
        執行緒真正退出前,由所有權 token 保證它誤釋放不了新任務的執行槽。

        timeout_s: 顯式覆蓋停滯閾值。None 時用 job 自身 create() 時存的 timeout_s,
        缺失則回退 DEFAULT_JOB_TIMEOUT_S。分鐘K長任務在 create 時存了更大閾值,
        不被普通任務的 1200s 誤殺。
        """
        with self._lock:
            jid = self._active_id
            if not jid:
                return
            j = self._active_jobs.get(jid)
            if not j or j.get("status") != "running":
                return
            started = j.get("started_at")
            last_alive = j.get("last_progress_at") or started
            if not started:
                return
            # 優先用顯式傳入, 其次 job 自身閾值, 最後預設值
            effective_timeout = timeout_s if timeout_s is not None else j.get("timeout_s", DEFAULT_JOB_TIMEOUT_S)
            timeout_s = effective_timeout
            started_at = started
            last_alive_at = last_alive
        # 時間計算放到鎖外(避免 datetime 解析持鎖)。
        # started_at 形如 "2026-07-04T12:00:00Z"(start() 用 datetime.utcnow 存)。
        # 兩端都用 timezone-aware UTC 比較,避免 naive/aware 混用導致 TypeError。
        try:
            start_dt = _parse_utc(started_at)
            alive_dt = _parse_utc(last_alive_at)
            now = datetime.now(start_dt.tzinfo)
            stalled_s = (now - alive_dt).total_seconds()
            total_s = (now - start_dt).total_seconds()
        except Exception:  # noqa: BLE001
            return
        if stalled_s > timeout_s:
            logger.warning(
                "reap_stale: 強制取消卡死 job %s (進度停滯 %.0fs > 閾值 %ss, 總執行 %.0fs)",
                jid, stalled_s, timeout_s, total_s)
            self.terminate(jid, f"超時自動取消: 進度停滯 {int(stalled_s)}s 超過閾值 {timeout_s}s,已請求終止")
        elif total_s > HARD_JOB_TIMEOUT_S:
            logger.warning(
                "reap_stale: 強制取消 job %s (總執行 %.0fs 超過硬上限 %ss)",
                jid, total_s, HARD_JOB_TIMEOUT_S)
            self.terminate(jid, f"超時自動取消: 總執行 {int(total_s)}s 超過硬上限,已請求終止")

    def terminate(self, job_id: str, message: str) -> None:
        """標記失敗 + 請求協作式終止 + 強制釋放執行槽(帶所有權)。

        reap_stale(判定卡死)與手動取消端點共用。
        """
        # 先置 cancel flag 再標失敗: fail() 彈出記錄後,殭屍執行緒的 progress()
        # 依賴 flag(而非記錄)感知取消。
        request_cancel(job_id)
        self.fail(job_id, message)
        # 強制釋放重任務槽(按所有權): 卡死執行緒可能永遠回不來釋放。
        # job 已標記 failed 且已請求終止; 殭屍執行緒即使後續短暫寫盤,
        # 也會在下一個分塊回呼處自行退出, 下次取得會覆蓋, 安全。
        release_run_slot(job_id)

    def clear(self) -> None:
        """清空所有任務（記憶體 + 磁碟檔案 + 取消標誌）。"""
        with self._lock:
            self._active_jobs.clear()
            self._active_id = None
            for f in self._store_dir.glob("*.json"):
                try:
                    f.unlink()
                except Exception:
                    pass
        with _CANCEL_FLAGS_LOCK:
            _CANCEL_FLAGS.clear()


def _summary(j: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": j["id"],
        "status": j["status"],
        "stage": j["stage"],
        "progress": j["progress"],
        "stage_pct": j.get("stage_pct", 0),
        "started_at": j["started_at"],
        "finished_at": j["finished_at"],
        "duration_s": j["duration_s"],
        "result": j["result"],
        "error": j["error"],
    }


def _duration_s(j: dict[str, Any]) -> float | None:
    if not j.get("started_at") or not j.get("finished_at"):
        return None
    try:
        s = datetime.fromisoformat(j["started_at"])
        e = datetime.fromisoformat(j["finished_at"])
        return round((e - s).total_seconds(), 2)
    except Exception:  # noqa: BLE001
        return None


# 行程內單例
job_store = JobStore()


# ================================================================
# 重任務互斥執行槽 — 防「殭屍並行」, 帶所有權 token
# ================================================================
# create() 的單飛去重能擋住 pending/running 視窗內的重複點擊, 但擋不住
# reap_stale 把卡死 job 標記 failed、清掉 _active_id 之後 —— 此時 executor
# 執行緒可能仍在跑(執行緒無法被硬中斷), 下一次 /run 會視作無活躍任務而另起一條,
# 與殭屍執行緒並行讀改寫同一 parquet。
#
# 該槽綁定「實際執行體」的生命週期而非 job 狀態: 每個重任務在真正開跑前
# try_acquire_run_slot(job_id), 結束(含異常)在 finally 裡 release_run_slot(job_id)。
#
# 所有權 token 修復的競態: 舊實現用裸 threading.Lock + 無參 release ——
# 殭屍執行緒最終結束時會在 finally 裡誤釋放**新任務**正持有的鎖(Lock 允許
# 任意執行緒 release), 第三次點擊又能插入並行。現在 release 必須攜帶持有者
# job_id, 非持有者的釋放一律忽略; reap 的強制釋放也走同一入口。
# 代價: 真卡死且協作式終止不生效(如卡在單個無限阻塞的網路讀裡)時,
# 需重啟行程才能再次跑重任務(優先保證資料不損壞)。
_run_slot_lock = threading.Lock()
_run_slot_owner: str | None = None


def try_acquire_run_slot(owner: str = "") -> bool:
    """嘗試佔用重任務執行槽(非阻塞)。成功回傳 True 並記錄持有者。

    owner: 持有者標識(呼叫方傳 job_id), 供 release_run_slot 校驗所有權。
    """
    global _run_slot_owner
    with _run_slot_lock:
        if _run_slot_owner is not None:
            return False
        _run_slot_owner = owner
        return True


def release_run_slot(owner: str | None = None) -> None:
    """釋放重任務執行槽。

    owner=None 時無條件釋放(相容舊呼叫/測試);
    owner 非 None 時僅當它是當前持有者才釋放 —— 殭屍執行緒 finally 裡的
    誤釋放(持有者已換成新 job 或槽已被 reap 釋放)會被忽略, 冪等不拋。
    """
    global _run_slot_owner
    with _run_slot_lock:
        if owner is not None and _run_slot_owner is not None and _run_slot_owner != owner:
            return
        _run_slot_owner = None


def _parse_utc(ts: str) -> datetime:
    """解析 start()/progress() 存的 "2026-07-04T12:00:00Z" 形式時間戳。"""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))

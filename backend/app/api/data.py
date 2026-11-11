"""資料畫像 API —— 讓前端知道"我們本地有什麼資料"。

Phase 8B-5.18: 移除已確認 zero 前端/後端/scheduler/腳本消費者的
POST /clear(清空所有本地 parquet, 原供已刪除的 Data.tsx 使用)與
GET /schema/{table}(表結構查看, 原供 Data.tsx 的 schema viewer 使用)。
兩者專屬的 _TABLE_FIELD_DESC / _SCHEMA_VIEWS 常量一併移除。
invalidate_data_cache / invalidate_storage_cache(仍被 daily_pipeline.py /
extend_history.py / kline.py / pipeline.py / data_integrity.py 使用)、
EnrichedPublication(仍被 repository.py 的即時/排程寫入使用)完全未變更。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/data", tags=["data"])

# ===== 快取:storage(檔案掃描) + 每張表 aggregate 各自快取 =====
# 同步期間前端 2s 輪一次 status,每張表 aggregate 全表 count + min/max + distinct
# 太重,加 TTL + 事件失效。stage 寫完只清對應那張表的快取。

_TABLE_TTL = 30.0  # 保底 TTL,即使沒人調 invalidate 也會過期
_TABLE_TTL_LARGE = 120.0  # 大表(分鐘K等)單獨 TTL，避免多分區聚合反覆重算
_STORAGE_TTL = 60.0  # storage 檔案掃描獨立 TTL,stage 寫完不觸發重算

# 聚合慢的大表（分區數多、列數多），使用更長的 TTL
_LARGE_TABLES = {"minute"}

_storage_cache: dict[str, Any] | None = None
_storage_cache_ts: float = 0.0
_storage_lock = threading.Lock()

_table_cache: dict[str, dict | None] = {
    "daily": None,
    "enriched": None,
    "index_daily": None,
    "index_enriched": None,
    "index_instruments": None,
    "etf_daily": None,
    "etf_enriched": None,
    "etf_instruments": None,
    "minute": None,
    "adj_factor": None,
    "instruments": None,
    "financials": None,
}
_table_cache_ts: dict[str, float] = {k: 0.0 for k in _table_cache}
_table_cache_lock = threading.Lock()

_last_finished_cache: dict[str, str | None] | None = None
_last_finished_lock = threading.Lock()


def invalidate_data_cache(table: str | None = None) -> None:
    """資料寫入/清除後呼叫。

    table=None 時清所有表 cache + storage(粗粒度,用於 pipeline 完成/clear);
    指定 table 時只清那張表,不影響 storage(細粒度,用於單 stage 寫完)。
    """
    with _table_cache_lock:
        if table is None:
            global _storage_cache, _storage_cache_ts, _last_finished_cache
            _storage_cache = None
            _storage_cache_ts = 0.0
            _last_finished_cache = None
            for k in _table_cache:
                _table_cache[k] = None
                _table_cache_ts[k] = 0.0
        elif table in _table_cache:
            _table_cache[table] = None
            _table_cache_ts[table] = 0.0


def invalidate_storage_cache() -> None:
    """向後相容入口 — 清全部快取。新代碼請用 invalidate_data_cache(table)。"""
    invalidate_data_cache(None)


def _get_table_stats(name: str, fetch: Callable[[], dict | None]) -> dict | None:
    """走 TTL+事件 雙重快取。fetch 在鎖外執行避免阻塞別的請求。"""
    ttl = _TABLE_TTL_LARGE if name in _LARGE_TABLES else _TABLE_TTL
    now = time.time()
    with _table_cache_lock:
        cached = _table_cache.get(name)
        cached_ts = _table_cache_ts.get(name, 0.0)
        if cached is not None and (now - cached_ts) < ttl:
            return cached

    fresh = fetch()

    with _table_cache_lock:
        _table_cache[name] = fresh
        _table_cache_ts[name] = now
    return fresh


def _safe_aggregate(repo, view: str) -> dict | None:
    """聚合檢視表基礎統計;檢視表不存在或為空時返 None。"""
    try:
        row = repo.execute_one(
            f"""SELECT count(*) AS rows,
                       min(date) AS earliest,
                       max(date) AS latest,
                       count(DISTINCT symbol) AS symbols,
                       count(DISTINCT date) AS trading_days
                FROM {view}"""
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("aggregate %s failed: %s", view, e)
        return None
    if not row or not row[0]:
        return None
    return {
        "rows": int(row[0]),
        "earliest_date": str(row[1]) if row[1] else None,
        "latest_date": str(row[2]) if row[2] else None,
        "symbols_covered": int(row[3] or 0),
        "trading_days": int(row[4] or 0),
    }


def _safe_aggregate_daily(repo, view: str = "kline_daily") -> dict | None:
    """日K輕量統計 — 零資料掃描。

    從分區目錄名取得日期範圍和交易日數，不讀任何 parquet。
    標的數從 instruments 小表取得（~5000行，毫秒級）。
    """
    daily_dir = repo.store.data_dir / "kline_daily"
    if not daily_dir.exists():
        return None
    dates: list[str] = []
    for d in daily_dir.iterdir():
        if d.is_dir() and d.name.startswith("date="):
            dates.append(d.name[5:])
    if not dates:
        return None
    dates.sort()

    symbols = _count_instruments_symbols(repo)

    return {
        "rows": 0,
        "earliest_date": dates[0],
        "latest_date": dates[-1],
        "symbols_covered": symbols,
        "trading_days": len(dates),
    }


def _safe_aggregate_enriched(repo) -> dict | None:
    """Enriched 輕量統計 — 零資料掃描。

    欄位數從 DESCRIBE 讀 schema（不碰資料），毫秒級。
    日期範圍從分區目錄名取得（同 minute 策略），不讀任何 parquet。
    標的數從 instruments 小表取。
    """
    # 欄位數：讀 schema，不碰資料
    fields = 0
    try:
        cols = repo.execute_all("DESCRIBE kline_enriched")
        fields = len(cols)
    except Exception:  # noqa: BLE001
        pass

    # 日期範圍：從分區目錄名取得，不掃資料
    enriched_dir = repo.store.data_dir / "kline_daily_enriched"
    if not enriched_dir.exists():
        return None
    dates: list[str] = []
    for d in enriched_dir.iterdir():
        if d.is_dir() and d.name.startswith("date="):
            dates.append(d.name[5:])
    if not dates:
        return None
    dates.sort()

    symbols = _count_instruments_symbols(repo)

    return {
        "rows": 0,
        "fields": fields,
        "earliest_date": dates[0],
        "latest_date": dates[-1],
        "symbols_covered": symbols,
        "trading_days": len(dates),
    }


def _count_instruments_symbols(repo) -> int:
    """從 instruments 小表取標的數（~5000行，毫秒級）。"""
    try:
        sym_row = repo.execute_one(
            "SELECT count(DISTINCT symbol) FROM instruments"
        )
        if sym_row and sym_row[0]:
            return int(sym_row[0])
    except Exception:  # noqa: BLE001
        pass
    return 0


def _safe_aggregate_instruments(repo) -> dict | None:
    """instruments 檢視表統計(無 date 欄,用 as_of)。"""
    try:
        row = repo.execute_one(
            """SELECT count(*) AS rows,
                      count(DISTINCT symbol) AS symbols,
                      max(as_of) AS latest_as_of,
                      count_if(name IS NOT NULL AND name != '') AS named
               FROM instruments"""
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("aggregate instruments failed: %s", e)
        return None
    if not row or not row[0]:
        return None
    return {
        "rows": int(row[0]),
        "symbols_covered": int(row[1] or 0),
        "latest_as_of": str(row[2]) if row[2] else None,
        "named": int(row[3] or 0),
    }


def _safe_aggregate_index_daily(repo) -> dict | None:
    """指數日K統計。指數資料量較小，直接讀取 parquet 元資料統計真實列數。"""
    return _safe_aggregate(repo, "kline_index_daily")


def _safe_aggregate_index_enriched(repo) -> dict | None:
    """指數 enriched 統計。指數資料量較小，直接讀取 parquet 元資料統計真實列數。"""
    fields = 0
    try:
        cols = repo.execute_all("DESCRIBE kline_index_enriched")
        fields = len(cols)
    except Exception:  # noqa: BLE001
        pass
    stats = _safe_aggregate(repo, "kline_index_enriched")
    if not stats:
        return None
    return {**stats, "fields": fields}


def _safe_aggregate_index_instruments(repo) -> dict | None:
    """指數 instruments 檢視表統計。"""
    try:
        row = repo.execute_one(
            """SELECT count(*) AS rows,
                      count(DISTINCT symbol) AS symbols,
                      count_if(name IS NOT NULL AND name != '') AS named
               FROM instruments_index"""
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("aggregate instruments_index failed: %s", e)
        return None
    if not row or not row[0]:
        return None
    return {
        "rows": int(row[0]),
        "symbols_covered": int(row[1] or 0),
        "latest_as_of": None,
        "named": int(row[2] or 0),
    }


def _safe_aggregate_etf_instruments(repo) -> dict | None:
    """ETF instruments 統計 — 優先獨立 instruments_etf，相容舊 instruments_index。"""
    queries = [
        """SELECT count(*) AS rows,
                  count(DISTINCT symbol) AS symbols,
                  count_if(name IS NOT NULL AND name != '') AS named
           FROM instruments_etf""",
        """SELECT count(*) AS rows,
                  count(DISTINCT symbol) AS symbols,
                  count_if(name IS NOT NULL AND name != '') AS named
           FROM instruments_index
           WHERE asset_type = 'etf'""",
    ]
    for sql in queries:
        try:
            row = repo.execute_one(sql)
        except Exception as e:  # noqa: BLE001
            logger.debug("aggregate etf instruments fallback failed: %s", e)
            continue
        if row and row[0]:
            return {
                "rows": int(row[0]),
                "symbols_covered": int(row[1] or 0),
                "latest_as_of": None,
                "named": int(row[2] or 0),
            }
    return None


def _safe_aggregate_etf_enriched(repo) -> dict | None:
    """ETF enriched 統計 — 獨立 kline_etf_enriched。"""
    fields = 0
    try:
        cols = repo.execute_all("DESCRIBE kline_etf_enriched")
        fields = len(cols)
    except Exception:  # noqa: BLE001
        pass
    stats = _safe_aggregate(repo, "kline_etf_enriched")
    if not stats:
        return None
    return {**stats, "fields": fields}


def _safe_aggregate_etf_daily(repo) -> dict | None:
    """ETF 日K統計 — 優先獨立 kline_etf_daily，相容舊 index 儲存。"""
    queries = [
        """SELECT count(*) AS rows,
                  min(date) AS earliest,
                  max(date) AS latest,
                  count(DISTINCT symbol) AS symbols,
                  count(DISTINCT date) AS trading_days
           FROM kline_etf_daily""",
        """SELECT count(*) AS rows,
                  min(date) AS earliest,
                  max(date) AS latest,
                  count(DISTINCT symbol) AS symbols,
                  count(DISTINCT date) AS trading_days
           FROM kline_index_daily
           WHERE symbol IN (
               SELECT DISTINCT symbol FROM instruments_index WHERE asset_type = 'etf'
           )""",
    ]
    for sql in queries:
        try:
            row = repo.execute_one(sql)
        except Exception as e:  # noqa: BLE001
            logger.debug("aggregate etf daily fallback failed: %s", e)
            continue
        if row and row[0]:
            return {
                "rows": int(row[0]),
                "earliest_date": str(row[1]) if row[1] else None,
                "latest_date": str(row[2]) if row[2] else None,
                "symbols_covered": int(row[3] or 0),
                "trading_days": int(row[4] or 0),
            }
    return None


def _safe_aggregate_adj_factor(repo) -> dict | None:
    """adj_factor 檢視表統計,日期範圍對齊日 K 覆蓋區間。"""
    try:
        # 取日 K 的日期範圍作為過濾條件
        dr = repo.execute_one(
            "SELECT min(date), max(date) FROM kline_daily"
        )
        if not dr or not dr[0]:
            return None
        d_min, d_max = dr[0], dr[1]
        row = repo.execute_one(
            """SELECT count(*) AS rows,
                      count(DISTINCT symbol) AS symbols,
                      count(DISTINCT trade_date) AS trading_days
               FROM adj_factor
               WHERE trade_date BETWEEN ? AND ?""",
            [str(d_min), str(d_max)],
        )
        if not row or not row[0]:
            return None
        return {
            "rows": int(row[0]),
            "symbols_covered": int(row[1]) if isinstance(row[1], (int, float)) else 0,
            "earliest_date": str(d_min),
            "latest_date": str(d_max),
            "trading_days": int(row[2] or 0),
        }
    except Exception as e:  # noqa: BLE001
        logger.debug("aggregate adj_factor failed: %s", e)
        return None


def _safe_aggregate_minute(repo) -> dict | None:
    """kline_minute 統計 — 從分區目錄名取得交易日數，跳過全表掃描。

    分鐘 K 按 date=YYYY-MM-DD 分區儲存，直接數目錄即可，
    無需 count(*) / count(DISTINCT ...) 等昂貴查詢。
    """
    minute_dir = repo.store.data_dir / "kline_minute"
    if not minute_dir.exists():
        return None

    # 從 date=YYYY-MM-DD 目錄名提取交易日
    dates: list[str] = []
    for d in minute_dir.iterdir():
        if d.is_dir() and d.name.startswith("date="):
            dates.append(d.name[5:])

    if not dates:
        return None

    dates.sort()
    return {
        "rows": 0,  # 不再查詢列數
        "earliest_date": dates[0],
        "latest_date": dates[-1],
        "symbols_covered": 0,  # 不再查詢標的數
        "trading_days": len(dates),
    }


def _safe_aggregate_financials(repo) -> dict | None:
    """財務資料統計 — 檢查各表檔案是否存在及列數。"""
    data_dir = repo.store.data_dir
    tables_info: dict[str, dict] = {}
    total_rows = 0

    for table in ("metrics", "income", "balance_sheet", "cash_flow", "shares"):
        path = data_dir / "financials" / table / "part.parquet"
        if path.exists():
            try:
                import polars as pl
                df = pl.read_parquet(path, columns=["symbol"])
                rows = len(df)
                symbols = df["symbol"].n_unique() if not df.is_empty() else 0
                tables_info[table] = {"rows": rows, "symbols": symbols}
                total_rows += rows
            except Exception:
                tables_info[table] = {"rows": 0, "symbols": 0}
        else:
            tables_info[table] = {"rows": 0, "symbols": 0}

    if total_rows == 0:
        return None

    return {
        "rows": total_rows,
        "tables": tables_info,
    }


def _scan_dir_stats(dirpath: Path) -> tuple[int, float]:
    """單次走訪統計目錄下檔案數和總大小(MB)。比 rglob+stat 快很多。"""
    if not dirpath.exists():
        return 0, 0.0
    count = 0
    total = 0
    for entry in os.scandir(dirpath):
        if entry.is_dir(follow_symlinks=False):
            c, s = _scan_dir_recursive(entry)
            count += c
            total += s
        elif entry.is_file(follow_symlinks=False):
            try:
                total += entry.stat().st_size
            except OSError:
                pass
            count += 1
    return count, round(total / 1048576, 2)


def _scan_dir_recursive(entry: os.DirEntry) -> tuple[int, int]:
    """遞迴統計一個 DirEntry 下的檔案數和總字節數。"""
    count = 0
    total = 0
    try:
        for sub in os.scandir(entry.path):
            if sub.is_dir(follow_symlinks=False):
                c, s = _scan_dir_recursive(sub)
                count += c
                total += s
            elif sub.is_file(follow_symlinks=False):
                try:
                    total += sub.stat().st_size
                except OSError:
                    pass
                count += 1
    except PermissionError:
        pass
    return count, total


def _compute_storage(data_dir: Path) -> dict:
    """單次走訪計算 storage 統計，避免多次 rglob。"""
    import os

    # 只統計關心的子目錄
    subdirs = {
        "daily": data_dir / "kline_daily",
        "enriched": data_dir / "kline_daily_enriched",
        "index_daily": data_dir / "kline_index_daily",
        "index_enriched": data_dir / "kline_index_enriched",
        "index_instruments": data_dir / "instruments_index",
        "etf_daily": data_dir / "kline_etf_daily",
        "etf_enriched": data_dir / "kline_etf_enriched",
        "etf_instruments": data_dir / "instruments_etf",
        "etf_adj_factor": data_dir / "adj_factor_etf",
        "minute": data_dir / "kline_minute",
        "adj_factor": data_dir / "adj_factor",
        "instruments": data_dir / "instruments",
        "ext_data": data_dir / "ext_data",
    }
    stats = {}
    total_size = 0
    for key, d in subdirs.items():
        fc, sz = _scan_dir_stats(d)
        total_size += sz
        stats[f"{key}_files"] = fc
        stats[f"{key}_size_mb"] = sz

    # total: 再加上其他零散目錄 (financials 有下方專屬明細統計, 不在此列)
    other_dirs = ["pools", "backtest_results", "screener_results", "ai_cache"]
    for name in other_dirs:
        d = data_dir / name
        if d.exists():
            _, s = _scan_dir_stats(d)
            total_size += s

    # financials 單獨統計 (明細與 total 各計入一次, 不得與其他目錄循環重複累加)
    fin_dir = data_dir / "financials"
    if fin_dir.exists():
        fc, sz = _scan_dir_stats(fin_dir)
        stats["financials_files"] = fc
        stats["financials_size_mb"] = sz
        total_size += sz
    # 根目錄散檔案
    for entry in os.scandir(data_dir):
        if entry.is_file(follow_symlinks=False):
            try:
                total_size += entry.stat().st_size / 1048576
            except OSError:
                pass
    stats["total_size_mb"] = round(total_size, 2)
    return stats


def _next_cron_run(scheduler, job_id: str) -> str | None:
    """讀 APScheduler 下次執行時間。"""
    if not scheduler:
        return None
    try:
        job = scheduler.get_job(job_id)
        if job and job.next_run_time:
            return job.next_run_time.isoformat(timespec="seconds")
    except Exception:  # noqa: BLE001
        pass
    return None


def _get_storage(data_dir: Path) -> dict:
    """回傳快取的 storage 統計；走獨立 TTL，stage 寫完不觸發重算。"""
    global _storage_cache, _storage_cache_ts
    now = time.time()
    with _storage_lock:
        if _storage_cache is not None and (now - _storage_cache_ts) < _STORAGE_TTL:
            return _storage_cache
    fresh = _compute_storage(data_dir)
    with _storage_lock:
        _storage_cache = fresh
        _storage_cache_ts = now
    return fresh


def _last_finished(job_label: str) -> str | None:
    """從 JobStore 讀最近一次該類型任務的完成時間（快取到 pipeline 終態失效）。"""
    global _last_finished_cache
    with _last_finished_lock:
        if _last_finished_cache is not None:
            return _last_finished_cache.get(job_label)

    from app.services.pipeline_jobs import job_store
    jobs = job_store.list_recent(limit=50)
    cache: dict[str, str | None] = {}
    for j in jobs:
        if j["status"] not in ("succeeded", "failed"):
            continue
        if "instruments_rows" in (j.get("result") or {}) and "instruments" not in cache:
            cache["instruments"] = j["finished_at"]
        if "daily_days" in (j.get("result") or {}) and "pipeline" not in cache:
            cache["pipeline"] = j["finished_at"]
    with _last_finished_lock:
        _last_finished_cache = cache
    return cache.get(job_label)


@router.get("/status")
def status(request: Request) -> dict:
    repo = request.app.state.repo
    scheduler = getattr(request.app.state, "scheduler", None)
    data_dir = repo.store.data_dir

    return {
        "daily":       _get_table_stats("daily",       lambda: _safe_aggregate_daily(repo)),
        "enriched":    _get_table_stats("enriched",    lambda: _safe_aggregate_enriched(repo)),
    "index_daily":       _get_table_stats("index_daily",       lambda: _safe_aggregate_index_daily(repo)),
    "index_enriched":    _get_table_stats("index_enriched",    lambda: _safe_aggregate_index_enriched(repo)),
    "index_instruments": _get_table_stats("index_instruments", lambda: _safe_aggregate_index_instruments(repo)),
    "etf_daily":         _get_table_stats("etf_daily",         lambda: _safe_aggregate_etf_daily(repo)),
    "etf_enriched":      _get_table_stats("etf_enriched",      lambda: _safe_aggregate_etf_enriched(repo)),
    "etf_instruments":   _get_table_stats("etf_instruments",   lambda: _safe_aggregate_etf_instruments(repo)),
    "minute":      _get_table_stats("minute",      lambda: _safe_aggregate_minute(repo)),
        "adj_factor":  _get_table_stats("adj_factor",  lambda: _safe_aggregate_adj_factor(repo)),
        "instruments": _get_table_stats("instruments", lambda: _safe_aggregate_instruments(repo)),
        "financials":  _get_table_stats("financials",  lambda: _safe_aggregate_financials(repo)),

        # 檔案層面資訊(快取)
        "storage": _get_storage(data_dir),

        # 調度
        "next_instruments_run": None,
        "next_pipeline_run":    _next_cron_run(scheduler, "taiwan_daily_update"),
        "last_instruments_run": _last_finished("instruments"),
        "last_pipeline_run":    _last_finished("pipeline"),
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        # 指標快取就緒標誌 (啟動時 enriched 非同步預熱, 完成前為 false)
        "indicators_ready": getattr(request.app.state, "indicators_ready", True),
    }


@router.get("/version")
def get_version() -> dict:
    """Return the current project version defined by VERSION."""
    from app import __version__

    return {"version": f"v{__version__}"}


@router.post("/refresh-cache")
def refresh_cache(request: Request) -> dict:
    """重建 Polars 記憶體快取 (clear + refresh), 不清資料、不清 alerts。

    用於跨天後快取殘留、或維表更新後手動重新整理場景。
    與設定頁「清理並重新整理」的區別: 那個清前端 react-query, 這個重建後端 enriched 快取。
    """
    repo = request.app.state.repo
    repo.clear_cache()
    repo.refresh_cache()
    logger.info("refresh-cache: Polars 快取已重建")
    return {"ok": True}

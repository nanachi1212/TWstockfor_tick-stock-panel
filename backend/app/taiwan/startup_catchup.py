"""啟動時補跑錯過的台股盤後更新。

後端沒在 16:30 執行時 (電腦關機、程式沒開)，日線就會停在舊日期。
這裡在啟動後延遲幾秒檢查資料新鮮度；只要日線落後最近已確認交易日，
就在背景執行一次既有的增量更新 (與 16:30 排程同一條流程)，不另建資料路徑。

- 不在盤中補跑 (官方全市場日線要收盤後才完整)。
- 一個行程只補跑一次，失敗只記 log，不影響啟動。
"""
# ruff: noqa: RUF002 -- user-facing Traditional Chinese text is intentional.
from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from app.taiwan.shareholding_store import catchup_shareholding, needs_shareholding_catchup

__all__ = [
    "cancel",
    "catchup_shareholding",
    "last_result",
    "needs_catchup",
    "needs_fundamentals_catchup",
    "needs_shareholding_catchup",
    "schedule",
]

logger = logging.getLogger(__name__)

_STARTED = threading.Event()
_LAST: dict[str, Any] = {}
_TIMER: threading.Timer | None = None


def last_result() -> dict[str, Any]:
    return dict(_LAST)


def needs_catchup() -> tuple[bool, str]:
    """回傳 (是否需要補跑, 原因)。盤中或資料已最新時回 False。"""
    from app.taiwan.daily_update import TaiwanDailyUpdateService
    from app.taiwan.realtime import get_market_status, taipei_now

    now = taipei_now()
    status = get_market_status(now, require_verified_trading_day=True).value
    if status in {"pre_open", "open", "scheduled_open_unverified", "post_close"}:
        return False, f"market_{status}"
    freshness = TaiwanDailyUpdateService().get_freshness()
    if freshness.daily_status == "current":
        return False, "current"
    return True, f"daily_{freshness.daily_status}:{freshness.daily_as_of or 'none'}"


def _catchup_official_evidence(app_state: Any) -> dict[str, Any]:
    """開機時補抓月營收收存檔與官方事件快照 (排程沒開機時漏掉的部分)。

    兩者都會略過仍新鮮的資料: 月營收依各頁的抓取時間 (1 小時內不重抓, 與排程一致), 事件快照依 1 小時 TTL。
    """
    from datetime import timedelta

    from app.config import settings
    from app.jobs import daily_pipeline
    from app.taiwan.events_service import get_event_service
    from app.taiwan.monthly_revenue_evidence import MonthlyRevenueEvidenceStore, refresh_monthly_revenue_evidence

    store = MonthlyRevenueEvidenceStore(settings.data_dir / "taiwan" / "monthly_revenue_evidence")
    revenue = refresh_monthly_revenue_evidence(store, refetch_after=timedelta(hours=1))
    event_service = get_event_service()
    event_service.get_all_regulatory_and_official_events()
    if revenue.get("fetched") or event_service.last_status in {"available", "partial"}:
        daily_pipeline.notify_daily_data_updated(app_state)
    return {"revenue": revenue.get("status"), "events": event_service.last_status}


def _run(app_state: Any) -> None:
    from app.jobs import daily_pipeline

    # 落後好幾天時, 先下載 GitHub 上每天自動發布的資料包, 比逐日向交易所補快很多。
    try:
        from app.taiwan.bootstrap import catch_up_from_latest_bundle

        _LAST["bundle"] = catch_up_from_latest_bundle()
        logger.info("startup bundle catch-up: %s", _LAST["bundle"])
        if _LAST["bundle"].get("files_added"):
            daily_pipeline.notify_daily_data_updated(app_state)
    except Exception as exc:
        _LAST["bundle"] = {"status": "error", "reason": type(exc).__name__}
        logger.warning("startup bundle catch-up failed: %s", exc)

    try:
        need, reason = needs_catchup()
        if not need:
            _LAST.update(status="skipped", reason=reason)
            logger.info("startup catch-up skipped: %s", reason)
        else:
            logger.info("startup catch-up: daily data stale (%s); running the shared post-close update chain", reason)
            # 與 16:30 排程同一條流程 (資料更新 + 研究檢視表 + Quant + Auto Watch + 自動 AI 說明 + 收盤推播)
            daily_pipeline.set_app_state(app_state)
            daily_pipeline.run_taiwan_update(trigger="startup_catchup")
            _LAST.update(status="ran", reason=reason)
            qs = getattr(app_state, "quote_service", None)
            if qs is not None and hasattr(qs, "_broadcast_quote_updated"):
                with contextlib.suppress(Exception):
                    qs._broadcast_quote_updated()
    except Exception as exc:
        _LAST.update(status="error", reason=type(exc).__name__)
        logger.warning("startup catch-up failed: %s", exc)

    try:
        sh_result = catchup_shareholding()
        _LAST["shareholding"] = sh_result
        if sh_result.get("status") == "written":
            daily_pipeline.notify_daily_data_updated(app_state)
    except Exception as exc:
        _LAST["shareholding"] = {"status": "error", "reason": type(exc).__name__}
        logger.warning("startup shareholding catch-up failed: %s", exc)

    try:
        _LAST["official_evidence"] = _catchup_official_evidence(app_state)
        logger.info("startup official evidence catch-up: %s", _LAST["official_evidence"])
    except Exception as exc:
        _LAST["official_evidence"] = {"status": "error", "reason": type(exc).__name__}
        logger.warning("startup official evidence catch-up failed: %s", exc)

    # 放在日線更新之後 (預熱名單取自最新日線的成交量)。FinMind 有限速, 可能跑數小時。
    try:
        need, reason = needs_fundamentals_catchup()
        if need:
            from app.taiwan.fundamentals_warm import warm_fundamentals

            logger.info("startup fundamentals warm: %s", reason)
            _LAST["fundamentals"] = warm_fundamentals()
        else:
            _LAST["fundamentals"] = {"status": "skipped", "reason": reason}
    except Exception as exc:
        _LAST["fundamentals"] = {"status": "error", "reason": type(exc).__name__}
        logger.warning("startup fundamentals warm failed: %s", exc)


def needs_fundamentals_catchup(marker: Path | None = None) -> tuple[bool, str]:
    """17:30 的基本面預熱沒跑到時回 True (以上次預熱完成的時間判斷)。"""
    from app.taiwan.fundamentals_warm import REFRESH_AFTER, last_run_path

    path = marker or last_run_path()
    if not path.exists():
        return True, "never"
    age = time.time() - path.stat().st_mtime
    if age < REFRESH_AFTER.total_seconds():
        return False, "current"
    return True, f"last_warm_{age / 3600:.0f}h_ago"


def schedule(app_state: Any, delay_seconds: float = 20.0) -> bool:
    """啟動後延遲執行一次；重複呼叫無效。

    pytest 啟動 app 時不排程: 補跑會真的連網並寫入共用資料目錄, 污染其他測試。
    """
    global _TIMER
    if _STARTED.is_set():
        return False
    if os.environ.get("PYTEST_CURRENT_TEST"):
        _LAST.update(status="skipped", reason="pytest")
        return False
    _STARTED.set()
    _TIMER = threading.Timer(delay_seconds, _run, args=(app_state,))
    _TIMER.daemon = True
    _TIMER.start()
    return True


def cancel() -> None:
    """關閉程式時取消尚未執行的補跑 (已開始執行的不中斷)。"""
    global _TIMER
    if _TIMER is not None:
        _TIMER.cancel()
        _TIMER = None

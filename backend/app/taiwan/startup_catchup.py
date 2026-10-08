"""啟動時補跑錯過的台股盤後更新。

後端沒在 16:30 執行時 (電腦關機、程式沒開)，日線就會停在舊日期。
這裡在啟動後延遲幾秒檢查資料新鮮度；只要日線落後最近已確認交易日，
就在背景執行一次既有的增量更新 (與 16:30 排程同一條流程)，不另建資料路徑。

- 不在盤中補跑 (官方全市場日線要收盤後才完整)。
- 一個程序只補跑一次，失敗只記 log，不影響啟動。
"""
# ruff: noqa: RUF002 -- user-facing Traditional Chinese text is intentional.
from __future__ import annotations

import contextlib
import logging
import os
import threading
from typing import Any

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


def _run(app_state: Any) -> None:
    from app.taiwan.daily_update import TaiwanDailyUpdateService

    try:
        need, reason = needs_catchup()
        if not need:
            _LAST.update(status="skipped", reason=reason)
            logger.info("startup catch-up skipped: %s", reason)
            return
        logger.info("startup catch-up: daily data stale (%s); running incremental update", reason)
        result = TaiwanDailyUpdateService().run_update(refresh_daily=True)
        _LAST.update(
            status=result.overall_status,
            daily=result.daily.status,
            target=result.target_latest_trading_date,
        )
        logger.info("startup catch-up finished: overall=%s daily=%s", result.overall_status, result.daily.status)
        if result.overall_status == "success" and result.daily.status == "success":
            try:
                from app.taiwan.auto_watch import sync_watchlist_plans

                sync_watchlist_plans()
            except Exception:
                logger.exception("startup catch-up: Auto Watch sync failed; old rules remain")
            qs = getattr(app_state, "quote_service", None)
            if qs is not None and hasattr(qs, "_broadcast_quote_updated"):
                with contextlib.suppress(Exception):
                    qs._broadcast_quote_updated()
    except Exception as exc:
        _LAST.update(status="error", reason=type(exc).__name__)
        logger.warning("startup catch-up failed: %s", exc)


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
    """關閉程序時取消尚未執行的補跑 (已開始執行的不中斷)。"""
    global _TIMER
    if _TIMER is not None:
        _TIMER.cancel()
        _TIMER = None

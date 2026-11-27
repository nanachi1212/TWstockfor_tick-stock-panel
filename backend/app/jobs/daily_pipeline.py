"""排程 job: 台股盤後增量更新、晚間補抓、買點評估。

所有 job 都只讀本機已寫入磁碟資料與官方公開資料來源 (TWSE / TPEx)。
"""
from __future__ import annotations

import json
import logging
from datetime import date, time, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.capabilities import CapabilitySet
from app.repository import KlineRepository
from app.taiwan.realtime.calendar import taipei_now

logger = logging.getLogger(__name__)


def _lock_forward_selections(update, *, only_missing: bool = False) -> None:
    try:
        from app.taiwan.selection_review_service import lock_daily_forward_batches

        # A request can succeed while an exchange is still publishing. Only the
        # actual persisted source dates establish readiness for this session.
        # Margin is not part of that: no formal strategy reads it, it is published
        # around 21:00, and the weekly ledger can only be written before midnight.
        target = update.target_latest_trading_date
        freshness = update.freshness
        if any(as_of != target for as_of in (freshness.daily_as_of, freshness.institutional_as_of)):
            logger.info("Taiwan forward batches waiting for official inputs for %s", target)
            return
        if only_missing:
            result = lock_daily_forward_batches(only_missing_for=date.fromisoformat(target))
        else:
            result = lock_daily_forward_batches()
        logger.info("Taiwan forward batches: %s", result)
    except Exception:
        logger.exception("Taiwan forward batch lock failed")


def _refresh_after_close_research() -> dict[str, object]:
    """Extend audited corporate-action coverage and append valuation history.

    This uses the existing audited corporate-action updater and valuation store;
    the scheduled job only fetches the uncovered tail and never bootstraps a
    missing full history implicitly.
    """
    result: dict[str, object] = {}
    try:
        from app.taiwan.corporate_actions import CorporateActionStore
        from app.taiwan.daily_update import resolve_target_latest_trading_date
        from app.taiwan.quant.primary_oos_runner import _action_snapshot

        store = CorporateActionStore()
        target = resolve_target_latest_trading_date()
        coverage = store.read_verified_coverage()
        if coverage is None:
            result["corporate_actions"] = {"status": "unavailable", "reason": "coverage_unverified"}
        elif coverage[1] < target:
            _action_snapshot(coverage[0], target, store=store)
            coverage = store.read_verified_coverage()
            result["corporate_actions"] = {
                "status": "verified" if coverage is not None and coverage[1] >= target else "partial",
                "end": coverage[1].isoformat() if coverage else None,
            }
        else:
            result["corporate_actions"] = {
                "status": "verified", "end": coverage[1].isoformat(),
            }
    except Exception as exc:
        logger.warning("Scheduled corporate-action coverage refresh failed: %s", exc)
        result["corporate_actions"] = {"status": "unavailable", "reason": "refresh_failed"}

    try:
        from app.taiwan.market_breadth_service import fundamental_store
        from app.taiwan.market_valuation import refresh_valuation

        result["valuation"] = refresh_valuation(fundamental_store())
    except Exception as exc:
        logger.warning("Scheduled valuation refresh failed: %s", exc)
        result["valuation"] = {"status": "unavailable", "reason": "refresh_failed"}
    return result


def run_taiwan_update(evening: bool = False, *, trigger: str = "scheduled",
                      target_date: date | None = None, inputs_only: bool = False):
    """台股盤後更新 + 後續鏈 (研究檢視表、Quant、Auto Watch、自動 AI 說明、收盤推播)。

    16:30/晚間排程與啟動補更新共用這一條, 避免補更新只刷資料卻漏掉盤後產出。
    """
    try:
        if not evening:
            # 新上市/下市股票: 商品清單每天更新一次 (失敗不影響其他流程)。要在抓日線之前,
            # 否則補抓多天時, 清單還沒有的新上市股那幾天的日線會被丟掉。
            try:
                from app.taiwan.universe import refresh_security_master

                logger.info("Taiwan security master: %s", refresh_security_master())
            except Exception:
                logger.exception("Taiwan security master refresh failed; cached list kept")
        from app.taiwan.daily_update import TaiwanDailyUpdateService
        svc = TaiwanDailyUpdateService()
        result = svc.run_update(refresh_daily=True, target_date=target_date)
        logger.info(
            "Taiwan daily update (%s) finished: overall=%s, daily=%s, inst=%s, margin=%s",
            trigger, result.overall_status, result.daily.status, result.institutional.status, result.margin.status,
        )
        if not evening:
            logger.info("Scheduled Taiwan research refresh: %s", _refresh_after_close_research())
        # Runs before the evening early-return: the ranking is frozen only once margin data is
        # published, which is exactly what the 21:30/23:00 catch-ups wait for.
        try:
            from app.taiwan.chip_ranking_tracking import freeze_latest_ranking

            logger.info("Taiwan chip ranking freeze: %s", freeze_latest_ranking())
        except Exception:
            logger.exception("Taiwan chip ranking freeze failed")
        # Evening catch-up exists for margin (published in the evening); only a
        # newly fetched daily date gives Quant anything new to freeze.
        # Formal selections read daily bars and institutional flow only, so a failed
        # margin fetch must not hold their lock; the rest of the chain still waits for it.
        lock_ready = result.daily.status == "success" and result.institutional.status == "success"
        if evening and (inputs_only or result.daily.dates_fetched == 0):
            if lock_ready:
                _lock_forward_selections(result, only_missing=True)
            return result
        # Quant is downstream and isolated: it must never roll back or
        # relabel the completed market-data refresh.
        try:
            from app.taiwan.quant.live_runner import run_live_after_refresh

            logger.info("Taiwan experimental live quant: %s", run_live_after_refresh(
                result, app_state=_app_state_ref,
            ))
        except Exception:
            logger.exception("Taiwan live quant failed; market refresh remains complete")
        if result.overall_status == "success" and result.daily.status == "success":
            try:
                from app.taiwan.auto_watch import sync_watchlist_plans

                logger.info("Taiwan Auto Watch sync: %s", sync_watchlist_plans())
            except Exception:
                logger.exception("Taiwan Auto Watch sync failed; old rules remain")
            _lock_forward_selections(result)
            try:
                from app.taiwan.auto_ai_explain import run_auto_explain

                logger.info("Taiwan auto AI explain: %s", run_auto_explain())
            except Exception:
                logger.exception("Taiwan auto AI explain failed")
            try:
                from app.taiwan.advice_reflection import run_auto_reflections

                logger.info("Taiwan advice settlement/reflection: %s",
                            run_auto_reflections(result.freshness.daily_as_of))
            except Exception:
                logger.exception("Taiwan advice settlement/reflection failed")
            try:
                from app.taiwan import push_digest

                logger.info("Taiwan push digest (evening): %s",
                            push_digest.run("evening", result.freshness.daily_as_of))
            except Exception:
                logger.exception("Taiwan evening push digest failed")
        elif lock_ready:
            _lock_forward_selections(result)
        return result
    except Exception as e:
        logger.exception("Taiwan daily update job (%s) failed: %s", trigger, e)
    finally:
        # After the research/ranking/Quant chain, so refetched queries see completed outputs.
        notify_daily_data_updated()


def _refresh_missing_taiex(target: date, trigger: str) -> str:
    from app.taiwan.benchmark_store import TaiwanBenchmarkStore

    store = TaiwanBenchmarkStore()
    try:
        result = store.refresh(target_date=target, symbols=("TAIEX",))
        latest = store.latest("TAIEX", on_or_before=target)
    except Exception:
        logger.exception("TAIEX-only catch-up (%s) failed for %s", trigger, target)
        return "failed"
    logger.info("TAIEX-only catch-up (%s) for %s: %s", trigger, target, result)
    if latest is None or latest["date"] != target:
        return "incomplete"
    notify_daily_data_updated()
    return "ran"


def run_due_after_close(evening: bool = False, *, trigger: str = "scheduled") -> str:
    """Coalesce a delayed cron/boot run against the last eligible Taipei session."""
    from app.taiwan.backfill_worker import WorkerBusyError, WorkerLock
    from app.taiwan.daily_update import resolve_target_latest_trading_date
    from app.taiwan.data_root import taiwan_data_root
    from app.taiwan.realtime import get_market_status
    from app.taiwan.startup_catchup import pending_after_close

    now = taipei_now()
    if get_market_status(now, require_verified_trading_day=True).value in {
        "pre_open", "open", "scheduled_open_unverified", "post_close",
    }:
        return "market_open"
    target = resolve_target_latest_trading_date(as_of_dt=now)
    if target == now.date() and now.time() < time(16, 30):
        return "before_due"
    root = taiwan_data_root()
    marker = root / "after_close_primary.json"
    try:
        with WorkerLock(root / "after_close_update.lock", max_age=timedelta(0)):
            recorded = {}
            if marker.is_file():
                try:
                    recorded = json.loads(marker.read_text("utf-8"))
                except ValueError:
                    logger.warning("Invalid primary after-close record %s", marker)
            if not isinstance(recorded, dict):
                recorded = {}
            session = target.isoformat()
            attempted = session in (recorded.get("attempted_for"), recorded.get("primary_daily_as_of"))
            same_day = target == now.date()
            # A delayed primary cron and the 19:30 cron may reach the executor together.
            # Whichever obtains the lock first owns the missed primary pass. A failed
            # earlier attempt instead leaves 19:30 to repair inputs and selections only.
            primary = same_day and not attempted and (not evening or trigger != "startup_catchup")
            if not primary:
                pending = pending_after_close()
                if pending is None:
                    return "current"
                if pending == f"taiex:{session}":
                    return _refresh_missing_taiex(target, trigger)
            late_job = evening and trigger == "scheduled" and same_day and now.time() >= time(21, 30)

            def save_attempt(status):
                recorded["attempt_status"] = status
                temporary = marker.with_suffix(".tmp")
                temporary.write_text(json.dumps(recorded), "utf-8")
                temporary.replace(marker)

            if primary:
                recorded.update(attempted_for=session, trigger=trigger, started_at=now.isoformat(),
                                finished_at=None, market_update_status=None)
                save_attempt("started")
            result = run_taiwan_update(evening=not primary, trigger=trigger, target_date=target,
                                       inputs_only=not primary and not late_job)
            if primary:
                recorded["finished_at"] = taipei_now().isoformat()
            if result is None:
                if primary:
                    save_attempt("failed")
                return "failed"
            daily_current = (result.target_latest_trading_date == session and result.freshness.daily_as_of == session
                             and result.freshness.daily_status == "current")
            if primary:
                recorded["market_update_status"] = result.overall_status
                if daily_current and result.daily.status == "success":
                    # Evidence of the main pass and daily partition, not a receipt for every downstream service.
                    recorded["primary_daily_as_of"] = session
                save_attempt("returned" if daily_current and result.daily.status == "success" else "incomplete")
            if not daily_current or (primary and result.daily.status != "success"):
                return "incomplete"
            return "ran" if result.overall_status == "success" else "incomplete"
    except WorkerBusyError:
        return "already_running"


def start_scheduler(repo: KlineRepository, capset: CapabilitySet) -> AsyncIOScheduler:
    """啟動排程器 (Asia/Taipei)。

    工作日 16:30 台股盤後增量更新，19:30/21:30/23:00 補資料與正式選股，10:00/14:00 買點評估。
    """
    scheduler = AsyncIOScheduler(timezone="Asia/Taipei")

    # 台股盤後增量更新 (Taiwan Full-Market Daily OHLCV + Institutional + Margin Update)
    # 每天 16:30 Asia/Taipei 觸發。
    # 官方全市場快照端點極速更新: Daily (TWSE 1 + TPEx 1) + Inst (2) + Margin (2) = ~6 次 HTTP 請求 / 日
    _scheduled_taiwan_update = run_due_after_close

    scheduler.add_job(
        _scheduled_taiwan_update,
        trigger=CronTrigger(day_of_week="mon-fri", hour=16, minute=30, timezone="Asia/Taipei"),
        id="taiwan_daily_update",
        misfire_grace_time=None,
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )

    # 選股器的基本面/籌碼條件只讀本機快取; 收盤後替流動性足夠的普通股預熱 (FinMind 限流, 約數小時)。
    def _scheduled_fundamentals_warm():
        try:
            from app.taiwan.fundamentals_warm import warm_fundamentals

            logger.info("Taiwan fundamentals warm: %s", warm_fundamentals())
        except Exception:
            logger.exception("Taiwan fundamentals warm failed")

    scheduler.add_job(
        _scheduled_fundamentals_warm,
        trigger=CronTrigger(day_of_week="mon-fri", hour=17, minute=30, timezone="Asia/Taipei"),
        id="taiwan_fundamentals_warm",
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )

    # 月營收收存檔與官方事件快照只在手動更新時重新整理, 過期後選股會判為資料不可用。
    # 月營收於盤前 (開盤前取得) 與收盤後各重新整理一次, 事件快照每小時重新整理 (TTL 1 小時)。
    def _scheduled_official_evidence_refresh(kind: str) -> None:
        try:
            from app.config import settings

            if kind == "revenue":
                from datetime import timedelta

                from app.taiwan.monthly_revenue_evidence import (
                    MonthlyRevenueEvidenceStore,
                    refresh_monthly_revenue_evidence,
                )

                store = MonthlyRevenueEvidenceStore(settings.data_dir / "taiwan" / "monthly_revenue_evidence")
                # 兩次排程相隔 9 小時以上, 預設 20 小時的重抓門檻會讓 17:45 完全跳過。
                summary = refresh_monthly_revenue_evidence(store, refetch_after=timedelta(hours=1))
                logger.info("Taiwan monthly revenue evidence refresh: %s", summary.get("status"))
                if summary.get("fetched"):
                    notify_daily_data_updated()
            else:
                from app.taiwan.events_service import get_event_service

                event_service = get_event_service()
                event_service.get_all_regulatory_and_official_events(force_refresh=True)
                logger.info("Taiwan regulatory events refresh: %s", event_service.last_status)
                if event_service.last_status in {"available", "partial"}:
                    notify_daily_data_updated()
        except Exception:
            logger.exception("Taiwan official evidence refresh failed: %s", kind)

    for hour, minute in ((8, 30), (17, 45)):
        scheduler.add_job(
            _scheduled_official_evidence_refresh,
            kwargs={"kind": "revenue"},
            trigger=CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone="Asia/Taipei"),
            id=f"taiwan_revenue_evidence_{hour:02d}{minute:02d}",
            misfire_grace_time=1800,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )

    scheduler.add_job(
        _scheduled_official_evidence_refresh,
        kwargs={"kind": "events"},
        trigger=CronTrigger(day_of_week="mon-fri", hour="8-17", minute=15, timezone="Asia/Taipei"),
        id="taiwan_regulatory_events_refresh",
        misfire_grace_time=900,
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )

    # 融資融券官方於當日晚間公布，沒有固定時點。
    from app.taiwan.daily_update import EVENING_UPDATE_TIMES

    for slot in EVENING_UPDATE_TIMES:
        hour, minute = slot.hour, slot.minute
        scheduler.add_job(
            _scheduled_taiwan_update,
            kwargs={"evening": True, "trigger": "scheduled_1930"} if hour == 19 else {"evening": True},
            trigger=CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone="Asia/Taipei"),
            id=f"taiwan_evening_update_{hour:02d}{minute:02d}",
            misfire_grace_time=None,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )

    # 買點策略只讀本地已寫入磁碟資料與自選股設定, 每個交易日盤中至少評估兩次。
    # 外部通知沿用既有 alert/SSE/LINE/Telegram 管線, 資料不足時由 evaluator fail closed。
    def _scheduled_buy_point_evaluation():
        try:
            from app.api.buy_points import evaluate_watchlist

            app_state = _get_app_state()
            result = evaluate_watchlist(
                repo.store.data_dir,
                getattr(app_state, "quote_service", None),
            )
            logger.info("Scheduled Taiwan buy-point evaluation finished: signals=%d, triggered=%d", len(result["signals"]), len(result["triggered"]))
        except Exception:
            logger.exception("Scheduled Taiwan buy-point evaluation failed")

    for hour in (10, 14):
        scheduler.add_job(
            _scheduled_buy_point_evaluation,
            trigger=CronTrigger(day_of_week="mon-fri", hour=hour, minute=0, timezone="Asia/Taipei"),
            id=f"taiwan_buy_point_evaluation_{hour:02d}",
            misfire_grace_time=1800,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )

    # 盤前一句話推播 (預設關閉, 由 push_digest.is_enabled 控制); 非交易日略過。
    def _scheduled_morning_digest() -> None:
        try:
            from app.taiwan import push_digest
            from app.taiwan.realtime import get_realtime_service, taipei_now

            # 用即時服務的交易日曆 (含已知假日); 08:45 尚無第一方報價可驗證, 所以只排除確定非交易日,
            # 文字本身帶資料日期 (as_of), 不會把舊資料當成今天。
            status = get_realtime_service().trading_calendar.get_market_status(
                taipei_now(), require_verified_trading_day=True,
            ).value
            if status == "non_trading_day":
                return
            logger.info("Taiwan push digest (morning): %s", push_digest.run("morning"))
        except Exception:
            logger.exception("Taiwan morning push digest failed")

    scheduler.add_job(
        _scheduled_morning_digest,
        trigger=CronTrigger(day_of_week="mon-fri", hour=8, minute=45, timezone="Asia/Taipei"),
        id="taiwan_morning_digest",
        misfire_grace_time=1200,
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )

    # 自選股盤中異常摘要: 連續競價時段每 5 分鐘掃一次 (預設關閉)。
    def _scheduled_watchlist_anomaly() -> None:
        try:
            from app.taiwan import watchlist_anomaly

            outcome = watchlist_anomaly.scan(_get_app_state())
            if outcome.get("flagged"):
                logger.info("Taiwan watchlist anomaly: %s", outcome)
        except Exception:
            logger.exception("Taiwan watchlist anomaly scan failed")

    scheduler.add_job(
        _scheduled_watchlist_anomaly,
        trigger=CronTrigger(day_of_week="mon-fri", hour="9-13", minute="*/5", timezone="Asia/Taipei"),
        id="taiwan_watchlist_anomaly",
        misfire_grace_time=120,
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )

    scheduler.start()
    logger.info("scheduler started; taiwan@16:30 (+19:30/21:30/23:00 catch-up), buy-points@10:00/14:00 mon-fri")
    return scheduler


# app_state 延遲引用(start_scheduler 在 lifespan 早期呼叫, app.state 可能還沒就緒)
_app_state_ref = None


def set_app_state(app_state) -> None:
    """lifespan 註冊 app.state 引用, 供 scheduled job 存取 quote_service 等單例。"""
    global _app_state_ref
    _app_state_ref = app_state


def _get_app_state():
    return _app_state_ref


def notify_daily_data_updated(app_state=None) -> None:
    """盤後資料已寫入磁碟: SSE 通知前端重新整理依賴日線的查詢 (含持股分析)。失敗不影響更新本身。"""
    qs = getattr(app_state or _app_state_ref, "quote_service", None)
    if qs is None:
        return
    try:
        qs.notify_daily_data_updated()
    except Exception:
        logger.exception("daily_data_updated SSE notify failed")

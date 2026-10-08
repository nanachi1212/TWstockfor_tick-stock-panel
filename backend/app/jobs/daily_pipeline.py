"""排程 job: 台股盤後增量更新、晚間補抓、買點評估。

所有 job 都只讀本機已落盤資料與官方公開資料來源 (TWSE / TPEx)。
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.tickflow.capabilities import CapabilitySet
from app.tickflow.repository import KlineRepository

logger = logging.getLogger(__name__)


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


def _refresh_single_view(repo: KlineRepository, name: str) -> None:
    """刷新单个 DuckDB 视图。"""
    d = repo.store.data_dir.as_posix()
    paths = {
        "kline_daily": f"{d}/kline_daily/**/*.parquet",
        "kline_enriched": f"{d}/kline_daily_enriched/**/*.parquet",
        "kline_index_daily": f"{d}/kline_index_daily/**/*.parquet",
        "kline_index_enriched": f"{d}/kline_index_enriched/**/*.parquet",
        "kline_etf_daily": f"{d}/kline_etf_daily/**/*.parquet",
        "kline_etf_enriched": f"{d}/kline_etf_enriched/**/*.parquet",
        "kline_etf_minute": f"{d}/kline_etf_minute/**/*.parquet",
        "kline_minute": f"{d}/kline_minute/**/*.parquet",
        "adj_factor": f"{d}/adj_factor/**/*.parquet",
        "adj_factor_etf": f"{d}/adj_factor_etf/**/*.parquet",
        "instruments": f"{d}/instruments/**/*.parquet",
        "instruments_index": f"{d}/instruments_index/**/*.parquet",
        "instruments_etf": f"{d}/instruments_etf/**/*.parquet",
    }
    path = paths.get(name)
    if not path:
        return
    try:
        repo.db.execute(
            f"CREATE OR REPLACE VIEW {name} AS "
            f"SELECT * FROM read_parquet('{path}', union_by_name=true)"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("refresh view %s failed: %s", name, e)


def start_scheduler(repo: KlineRepository, capset: CapabilitySet) -> AsyncIOScheduler:  # noqa: ARG001
    """啟動排程器 (Asia/Taipei)。

    工作日 16:30 台股盤後增量更新 (+21:30/23:00 融資融券補抓)、10:00/14:00 買點評估。
    """
    scheduler = AsyncIOScheduler(timezone="Asia/Taipei")

    # 台股盘后增量更新 (Taiwan Full-Market Daily OHLCV + Institutional + Margin Update)
    # 每天 16:30 Asia/Taipei 触发。
    # 官方全市場快照端點極速更新：Daily (TWSE 1 + TPEx 1) + Inst (2) + Margin (2) = ~6 次 HTTP 請求 / 日
    def _scheduled_taiwan_update(evening: bool = False):
        try:
            from app.taiwan.daily_update import TaiwanDailyUpdateService
            svc = TaiwanDailyUpdateService()
            result = svc.run_update(refresh_daily=True)
            logger.info(
                "Scheduled Taiwan daily update finished: overall=%s, daily=%s, inst=%s, margin=%s",
                result.overall_status, result.daily.status, result.institutional.status, result.margin.status,
            )
            if not evening:
                logger.info("Scheduled Taiwan research refresh: %s", _refresh_after_close_research())
            # Evening catch-up exists for margin (published in the evening); only a
            # newly fetched daily date gives Quant anything new to freeze.
            if evening and result.daily.dates_fetched == 0:
                return
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
                try:
                    from app.taiwan.auto_ai_explain import run_auto_explain

                    logger.info("Taiwan auto AI explain: %s", run_auto_explain())
                except Exception:
                    logger.exception("Taiwan auto AI explain failed")
                try:
                    from app.taiwan import push_digest

                    logger.info("Taiwan push digest (evening): %s",
                                push_digest.run("evening", result.freshness.daily_as_of))
                except Exception:
                    logger.exception("Taiwan evening push digest failed")
        except Exception as e:
            logger.exception("Scheduled Taiwan daily update job failed: %s", e)

    scheduler.add_job(
        _scheduled_taiwan_update,
        trigger=CronTrigger(day_of_week="mon-fri", hour=16, minute=30, timezone="Asia/Taipei"),
        id="taiwan_daily_update",
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )

    # 融資融券官方於「當日晚間」公布 (無固定時點), 16:30 批次必然取不到當日資料。
    # 晚間沿用同一個增量更新器補抓; 已存在的日期不會重抓 (每次僅 ~2 次 HTTP)。
    for hour, minute in ((21, 30), (23, 0)):
        scheduler.add_job(
            _scheduled_taiwan_update,
            kwargs={"evening": True},
            trigger=CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone="Asia/Taipei"),
            id=f"taiwan_evening_update_{hour:02d}{minute:02d}",
            misfire_grace_time=1800,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )

    # 買點策略只讀本地已落盤資料與自選股設定, 每個交易日盤中至少評估兩次。
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
            from app.taiwan.realtime import get_market_status, taipei_now

            if get_market_status(taipei_now(), require_verified_trading_day=True).value == "non_trading_day":
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
    logger.info("scheduler started; taiwan@16:30 (+21:30/23:00 catch-up), buy-points@10:00/14:00 mon-fri")
    return scheduler


# app_state 延迟引用(start_scheduler 在 lifespan 早期调用, app.state 可能还没就绪)
_app_state_ref = None


def set_app_state(app_state) -> None:
    """lifespan 註冊 app.state 引用, 供 scheduled job 存取 quote_service 等單例。"""
    global _app_state_ref
    _app_state_ref = app_state


def _get_app_state():
    return _app_state_ref

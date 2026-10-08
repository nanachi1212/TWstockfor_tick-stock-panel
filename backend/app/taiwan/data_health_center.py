"""Read-only projection of existing Taiwan metadata. No fetching or pipeline here."""

# ruff: noqa: RUF001
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from datetime import time as dt_time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.taiwan.realtime.calendar import taipei_now

HealthStatus = Literal[
    "current", "stale", "partial", "unavailable", "updating", "error",
    "awaiting_publication", "not_run", "provider_error", "config_missing",
]
# Scheduled post-close update (16:30) plus its grace; before this a missing
# target-day partition means "not run yet", not a failure.
SCHEDULED_UPDATE_GRACE = dt_time(17, 30)
HealthAction = Literal["update", "validate", "retry"]

DATASETS = {
    "daily": "Daily OHLC",
    "realtime": "Realtime",
    "institutional": "Institutional",
    "margin": "Margin / Short",
    "securities_lending": "Securities Lending",
    "financial": "Financial Statements",
    "monthly_revenue": "Monthly Revenue",
    "foreign_shareholding": "Foreign Shareholding",
    "taiex": "TAIEX",
    "tpex_index": "TPEX Index",
    "trading_calendar": "Trading Calendar",
    "security_master": "Security Master",
    "quant_live": "Quant Live Run",
    "selection_snapshot": "Selection snapshot",
    "selection_outcome": "Selection outcome",
    "ptt": "PTT",
    "dcard": "Dcard",
    "social_ai": "Social AI",
    "ai_provider": "AI Provider/Profile",
    "fugle": "Fugle Intraday",
    "frankfurter": "Frankfurter FX",
    "fred": "FRED Macro",
    "finbridge": "FinBridge Cross-check",
}
REASONS = {
    # Updater outcomes from the daily_update last-run record; most specific first.
    "TWSE_not_published": "TWSE 官方尚未發布此交易日資料（TPEx 已發布，未單獨寫入）",
    "TPEX_not_published": "TPEx 官方尚未發布此交易日資料（TWSE 已發布，未單獨寫入）",
    "official_not_published": "TWSE 與 TPEx 官方皆尚未發布此交易日資料",
    "empty_official_daily_snapshot": "官方日行情尚未發布（回應為空）",
    "official_daily_snapshot_incomplete": "官方來源回應不完整或連線中斷，下次排程自動重試",
    "exchange_missing": "部分交易所資料缺漏，尚未完整涵蓋 TWSE 與 TPEx",
    "reference_snapshot": "已保存參考快照，更新頻率未記錄，無法確認目前新鮮度",
    "security_master_missing": "本地證券主檔尚不存在",
    "cache_metadata_invalid": "本地快取 metadata 無法讀取",
    "live_run_missing": "目前交易日尚未建立有效 Live Quant run",
    "operation_not_current_success": "目前交易日尚未完成有效 Live Quant 更新",
    "audit_conflict": "Live Quant 快照驗證衝突",
    "session_unavailable": "缺少官方交易日證據，無法確認目前 Live Quant session",
    "corporate_action_coverage_unavailable": "公司行動涵蓋範圍尚未驗證至到期日（執行選股資料更新後計算）",
    "current_market_evidence_unavailable": "目前交易日官方市場證據不可用",
    "ranking_features_data_insufficient": "不足 61 個已驗證交易日，無法建立排名因子",
    "FinMind provider disabled": "FinMind 資料源未啟用",
    "AUTH_ERROR": "AI Provider 驗證失敗，請檢查授權設定",
    "INVALID_MODEL": "AI Provider 模型設定無效",
    "ENDPOINT_ERROR": "AI Provider 連線設定無效",
}


# "HTTP 403", "status code 403" and httpx's "Client error '403 Forbidden'".
_HTTP_STATUS = re.compile(
    r"(?:HTTP[ /:]*(?:status[ :=]*)?|status code[ :=]*|(?:Client|Server) error ')([45]\d{2})", re.I
)


def safe_reason(value: Any, fallback: str = "資料源回報失敗，無法取得有效資料") -> str:
    """Never send raw provider exceptions, URLs, paths or credentials to the client."""
    text = str(value or "")
    for code, explanation in REASONS.items():
        if code in text:
            return explanation
    match = _HTTP_STATUS.search(text)
    if match:
        code = match.group(1)
        return f"HTTP {code}，" + HTTP_REASONS.get(code, "資料源拒絕或無法完成請求")
    return fallback


HTTP_REASONS = {
    "401": "資料源驗證失敗，請檢查授權設定",
    "402": "服務帳戶餘額或付費額度不足（Payment Required）",
    "403": "資料源拒絕存取（Forbidden），可能為反爬蟲或權限限制",
    "429": "資料源限流，稍後重試",
}


def http_error(value: Any) -> bool:
    return bool(_HTTP_STATUS.search(str(value or "")))


def daily_policy(
    key: str,
    *,
    freshness_status: str,
    days_behind: int,
    target: str,
    exchanges: set[str],
    row_statuses: list[HealthStatus],
    pending: list[str],
    failed: list[str],
    attempted: bool,
    now: datetime,
) -> tuple[HealthStatus, str]:
    """Official post-close datasets: publication timing differs per dataset.

    Exchange completeness and freshness are separate facts; a complete but
    older partition is never reported as "exchange missing".
    """
    if not exchanges:
        return "unavailable", "本地尚無有效資料"
    missing = {"TWSE", "TPEX"} - exchanges
    if missing:
        return "partial", f"最新分區只有 {'/'.join(sorted(exchanges))}，缺少 {'/'.join(sorted(missing))}"
    if any(s != "current" for s in row_statuses):
        return "partial", "最新分區含非官方或未驗證的資料列"
    if freshness_status == "current":
        return "current", "已取得目標交易日官方資料（TWSE 與 TPEx）"
    if days_behind == 1 and target == now.date().isoformat():
        if pending:
            return "awaiting_publication", safe_reason(pending[0])
        if failed and "empty_official_daily_snapshot" in failed[0]:
            return "awaiting_publication", REASONS["empty_official_daily_snapshot"]
        if key == "margin" and not failed:
            return "awaiting_publication", "融資融券官方於當日晚間公布；晚間排程自動補抓"
        if failed:
            return "stale", safe_reason(failed[0], "今日更新失敗，原有資料保留；下次排程自動重試")
        if not attempted and now.time() < SCHEDULED_UPDATE_GRACE:
            return "not_run", "今日盤後排程（16:30）尚未執行；官方資料將自動更新"
        return "stale", "今日盤後更新未取得目標交易日資料；請按「重試」"
    return "stale", f"落後目標交易日 {days_behind} 個交易日"


def expected_financial_period(today: date) -> date:
    """Latest quarter whose statutory filing deadline has passed (TW listed cos.)."""
    y = today.year
    for deadline, period in (
        (date(y, 11, 14), date(y, 9, 30)),
        (date(y, 8, 14), date(y, 6, 30)),
        (date(y, 5, 15), date(y, 3, 31)),
        (date(y, 3, 31), date(y - 1, 12, 31)),
    ):
        if today > deadline:
            return period
    return date(y - 1, 9, 30)


def expected_revenue_date(today: date) -> date:
    """FinMind month-revenue date of the latest month due (revenue due by the 10th).

    FinMind dates a month's revenue on the first day of the following month.
    """
    first = today.replace(day=1)
    return first if today.day > 10 else (first - timedelta(days=1)).replace(day=1)


def _previous_daily_session() -> date | None:
    """Session before the latest stored daily date (one-session publication lag)."""
    try:
        from app.taiwan.daily_store import TaiwanDailyStore

        sessions = sorted(TaiwanDailyStore().available_dates())
    except Exception:
        return None
    return (sessions[-2] if len(sessions) > 1 else sessions[-1]) if sessions else None


def finmind_policy(
    key: str, records: list[dict[str, Any]], expected: date | None, ttl_seconds: int
) -> tuple[HealthStatus, str]:
    if not records:
        return "not_run", "尚未快取任何適用標的（開啟個股頁時才會按需抓取）"
    errors = [r for r in records if r.get("status") == "error"]
    missing = [r for r in records if r.get("status") == "unavailable"]
    if expected is not None:
        # Reporting cycle, not cache TTL: a quarter/month is current until the next is due.
        dated = [d for r in records if (d := data_date(r.get("data_date")))]
        behind = [d for d in dated if d < expected.isoformat()]
        invalid_count = len(errors) + len(missing)
        if invalid_count == len(records):
            if errors:
                return "error", safe_reason(errors[0].get("error_msg"))
            return "unavailable", "快取沒有有效資料"
        if errors or missing:
            return "partial", f"{invalid_count}/{len(records)} 檔快取沒有有效資料"
        if not dated:
            return "unavailable", "快取沒有可判定期別的資料"
        if behind:
            return "stale", f"{len(behind)}/{len(records)} 檔早於應已公告期別 {expected}"
        return "current", f"已快取標的均已涵蓋應已公告期別 {expected}"
    if errors:
        return "error", safe_reason(errors[0].get("error_msg"))
    stale = [r for r in records if r.get("status") == "stale"]
    if missing and len(missing) == len(records):
        return "unavailable", "快取沒有有效資料"
    if stale:
        hours = ttl_seconds // 3600
        return "stale", f"{len(stale)}/{len(records)} 檔快取超過 {hours} 小時 TTL；開啟個股頁時自動更新"
    if missing:
        return "partial", f"{len(missing)}/{len(records)} 檔快取沒有有效資料"
    return "current", "已快取標的均在 TTL 內"


def ai_reason(meta: dict[str, Any], errors: list[Any]) -> str:
    analyzed, batches = meta.get("analyzed_symbols", 0), meta.get("batches", 0)
    head = safe_reason(errors, "AI 批次失敗")
    return f"{head}；本次 {batches} 批、完成 {analyzed} 檔分析"


def partition_mtime(store: Any, day: str | None) -> str | None:
    """Write time of the latest persisted partition = last successful write."""
    if not day:
        return None
    try:
        stamp = (Path(store._data_dir) / f"date={day}").stat().st_mtime
    except (OSError, AttributeError):
        return None
    return datetime.fromtimestamp(stamp, UTC).isoformat()


def etf_symbols() -> set[str]:
    """Read the persisted security master directly (never triggers live IO)."""
    import polars as pl

    from app.taiwan.universe import get_security_master

    try:
        rows = pl.read_parquet(get_security_master().cache_path, columns=["symbol", "instrument_type"])
    except Exception:
        return set()
    return set(rows.filter(pl.col("instrument_type") == "etf")["symbol"].to_list())


def session_evidence() -> Callable[[date, str], Any]:
    """Trading-day evidence as the Live Quant writer sees it.

    Persisted census evidence first; a nonempty official daily partition for an
    exchange is itself proof of a session (same rule as CurrentLiveSource.load).
    """
    from app.taiwan.daily_store import TaiwanDailyStore
    from app.taiwan.observed_universe import ObservedUniverseStore
    from app.taiwan.realtime.calendar import TaiwanTradingCalendar, TradingDayEvidence

    store, cal, daily = ObservedUniverseStore(), TaiwanTradingCalendar(), TaiwanDailyStore()
    observed: dict[date, set[str]] = {}

    def evidence(day: date, exchange: str) -> Any:
        fact = store.day_evidence(exchange, day, calendar=cal)
        if fact.status != "unresolved":
            return fact
        if day not in observed:
            frame = daily.read_range(None, day, day) if day in set(daily.available_dates()) else None
            observed[day] = (
                {s.rsplit(".", 1)[-1] for s in frame["symbol"]} if frame is not None else set()
            )
        if exchange in observed[day]:
            return TradingDayEvidence(day, exchange, "trading", "taiwan_daily_store",
                                      "raw_market_observation")
        return fact

    return evidence


def timestamp(value: Any) -> str | None:
    try:
        stamp = datetime.fromisoformat(str(value))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        return stamp.astimezone(UTC).isoformat()
    except (ValueError, TypeError):
        return None


def data_date(value: Any) -> str | None:
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except (ValueError, TypeError):
        return None


def normalize_status(value: Any, *, stale: bool = False) -> HealthStatus:
    status = str(value or "unavailable")
    if status in {"error", "failed", "conflict"}:
        return "error"
    if status in {"unavailable", "missing", "not_queried", "data_insufficient", "disabled"}:
        return "unavailable"
    if status in {"updating", "queued", "running"}:
        return "updating"
    if status in {
        "partial",
        "degraded",
        "fallback",
        "daily_fallback",
        "official_snapshot_fallback",
        "pending",
    }:
        return "partial"
    if stale or status == "stale":
        return "stale"
    if status in {
        "current",
        "available",
        "verified",
        "official",
        "official_close",
        "official_snapshot",
        "realtime",
        "delayed",
        "completed",
        "frozen",
        "noop",
    }:
        return "current"
    return "unavailable"


def combined_status(statuses: list[HealthStatus]) -> HealthStatus:
    if not statuses:
        return "unavailable"
    if len(set(statuses)) == 1:
        return statuses[0]
    if any(s in {"current", "stale", "partial"} for s in statuses):
        return "partial"
    return "error" if "error" in statuses else "unavailable"


class DatasetHealth(BaseModel):
    id: str
    name: str
    status: HealthStatus = "unavailable"
    source: str | None = None
    provider: str | None = None
    enabled: bool | None = None
    auth_configured: bool | None = None
    as_of: str | None = None
    error: str | None = None
    data_date: str | None = None
    freshness: str = "未知"
    reason: str = "尚未查詢或尚未保存 metadata"
    last_attempt: str | None = None
    last_success: str | None = None
    actions: list[HealthAction] = Field(default=["validate"])


class HealthReport(BaseModel):
    generated_at: str
    datasets: list[DatasetHealth]
    current_count: int
    total_count: int


class DataHealthService:
    def __init__(self, readers: dict[str, Callable[[], dict[str, Any]]] | None = None) -> None:
        self.readers = readers

    def snapshot(self) -> HealthReport:
        rows = []
        readers = self.readers if self.readers is not None else self._readers()
        for key, label in DATASETS.items():
            try:
                meta = readers[key]()
                row = DatasetHealth(id=key, name=label, **meta)
            except Exception:
                # Exceptions may contain credentials or absolute paths; expose only a fixed reason.
                row = DatasetHealth(
                    id=key, name=label, status="error", reason="無法讀取既有 metadata，請重新驗證"
                )
            if key in {"daily", "institutional", "margin", "taiex", "tpex_index", "ptt", "social_ai"}:
                row.actions = ["validate", "update" if row.status == "current" else "retry"]
            if key == "dcard":
                row.actions = []
            rows.append(row)
        return HealthReport(
            generated_at=taipei_now().isoformat(),
            datasets=rows,
            current_count=sum(row.status == "current" for row in rows),
            total_count=len(rows),
        )

    def _readers(self) -> dict[str, Callable[[], dict[str, Any]]]:
        # Shared groups are loaded once per snapshot, never once per row.
        cached: dict[str, dict[str, dict[str, Any]]] = {}

        def group(
            name: str, loader: Callable[[], dict[str, dict[str, Any]]], key: str
        ) -> dict[str, Any]:
            if name not in cached:
                cached[name] = loader()
            return cached[name][key]

        def bind_group(
            name: str, loader: Callable[[], dict[str, dict[str, Any]]], key: str
        ) -> Callable[[], dict[str, Any]]:
            return lambda: group(name, loader, key)

        # Construct fresh closures for every snapshot; no metadata cache that can hide updates.
        return {
            **{
                key: bind_group("daily", self._daily, key)
                for key in ("daily", "institutional", "margin")
            },
            **{
                key: bind_group("finmind", self._finmind, key)
                for key in (
                    "securities_lending",
                    "financial",
                    "monthly_revenue",
                    "foreign_shareholding",
                )
            },
            **{
                key: bind_group("social", self._social, key)
                for key in ("ptt", "dcard", "social_ai")
            },
            **{
                key: bind_group("selection", self._selection, key)
                for key in ("selection_snapshot", "selection_outcome")
            },
            "realtime": self._realtime,
            "security_master": self._security_master,
            "trading_calendar": self._calendar,
            "quant_live": self._quant,
            "ai_provider": self._ai,
            "taiex": lambda: self._index("taiex"),
            "tpex_index": lambda: self._index("tpex_index"),
            "fugle": self._fugle,
            "frankfurter": self._frankfurter,
            "fred": self._fred,
            "finbridge": self._finbridge,
        }

    @staticmethod
    def _fugle() -> dict[str, Any]:
        from app.taiwan.realtime.fugle_provider import get_fugle_aggregates_provider

        return get_fugle_aggregates_provider().health_metadata()

    @staticmethod
    def _frankfurter() -> dict[str, Any]:
        from app.taiwan.providers.fx_context import get_frankfurter_fx_provider

        return get_frankfurter_fx_provider().health_metadata()

    @staticmethod
    def _fred() -> dict[str, Any]:
        from app.taiwan.providers.fred_macro import get_fred_macro_provider

        return get_fred_macro_provider().health_metadata()

    @staticmethod
    def _finbridge() -> dict[str, Any]:
        from app.taiwan.providers.finbridge import get_finbridge_provider

        return get_finbridge_provider().health_metadata()

    def _daily(self) -> dict[str, dict[str, Any]]:
        from app.taiwan.daily_update import TaiwanDailyUpdateService, read_last_run

        svc = TaiwanDailyUpdateService()
        freshness = svc.get_freshness().model_dump()
        target = freshness["target_latest_trading_date"]
        run = read_last_run(svc.daily_store) or {}
        run_for_target = run.get("target_latest_trading_date") == target
        result: dict[str, dict[str, Any]] = {}
        for key, store in (
            ("daily", svc.daily_store),
            ("institutional", svc.inst_store),
            ("margin", svc.margin_store),
        ):
            day = freshness[f"{key}_as_of"]
            try:
                frame = store.read_latest_date_rows()
            except Exception:
                result[key] = {
                    "status": "error",
                    "source": f"Taiwan {key} store",
                    "reason": "本地分區 metadata 無法讀取，原有資料保留",
                }
                continue
            stats = (run.get(key) or {}) if run_for_target else {}
            status, reason = daily_policy(
                key,
                freshness_status=freshness[f"{key}_status"],
                days_behind=freshness[f"{key}_days_behind"],
                target=target,
                exchanges=set() if frame.is_empty()
                else {s.rsplit(".", 1)[-1] for s in frame["symbol"]},
                row_statuses=[normalize_status(s) for s in frame["status"].unique()]
                if "status" in frame.columns
                else [],
                pending=[p["reason"] for p in stats.get("pending_dates", [])
                         if p.get("date") == target],
                failed=[str(f.get("error")) for f in stats.get("failed_dates", [])
                        if f.get("date") in {target, None}],
                attempted=run_for_target,
                now=taipei_now(),
            )
            sources = (
                sorted(set(frame["source"].drop_nulls().to_list()))
                if "source" in frame.columns
                else []
            )
            allowed_sources = {
                "twse:t86",
                "tpex:daily_trade",
                "twse:mi_margn",
                "tpex:margin_balance",
            }
            source = " / ".join(s for s in sources if s in allowed_sources) or f"Taiwan {key} store"
            result[key] = {
                "status": status,
                "source": source,
                "data_date": None if frame.is_empty() else day,
                "freshness": f"目標交易日 {target}"
                + ("；融資融券官方於當日晚間公布，21:30、23:00 自動補抓" if key == "margin"
                   else "；16:00 後以當日為目標，16:30 排程更新"),
                "reason": reason,
                "last_attempt": timestamp(run.get("run_started_at")),
                "last_success": partition_mtime(store, day),
            }
        return result

    def _finmind(self) -> dict[str, dict[str, Any]]:
        from app.taiwan.finmind_cache import DATASET_TTL, FinMindCache

        cache = FinMindCache()
        etfs = etf_symbols()
        today = taipei_now().date()
        chips_floor = _previous_daily_session()
        result = {}
        for key, dataset in (
            ("financial", "TaiwanStockFinancialStatements"),
            ("monthly_revenue", "TaiwanStockMonthRevenue"),
            ("foreign_shareholding", "TaiwanStockShareholding"),
            ("securities_lending", "TaiwanStockSecuritiesLending"),
        ):
            # Legacy cache keys without an exchange suffix are not canonical symbols.
            records = [r for r in cache.health_metadata(dataset) if "." in str(r.get("symbol", ""))]
            expected = (
                expected_financial_period(today) if key == "financial"
                else expected_revenue_date(today) if key == "monthly_revenue"
                # Daily chips are cached for days; judge them by data date, not by cache age.
                else chips_floor
            )
            if key in {"financial", "monthly_revenue"}:
                # ETFs publish neither statements nor monthly revenue: not applicable.
                records = [r for r in records if r.get("symbol") not in etfs]
            status, reason = finmind_policy(
                key, records, expected, DATASET_TTL.get(dataset, 6 * 3600)
            )
            attempts = [t for r in records if (t := timestamp(r.get("fetched_at")))]
            successes = [
                t
                for r in records
                if r.get("status") in {"available", "stale"}
                and (t := timestamp(r.get("fetched_at")))
            ]
            dates = [d for r in records if (d := data_date(r.get("data_date")))]
            event_based = key == "securities_lending"
            result[key] = {
                "status": status,
                "source": "FinMind cache",
                "reason": reason,
                "data_date": (max(dates) if event_based else min(dates)) if dates else None,
                "freshness": (
                    f"財報依申報期限；應已公告期別 {expected}" if key == "financial"
                    else f"月營收次月 10 日前公告；應已公告至 {expected}（FinMind 日期）"
                    if key == "monthly_revenue"
                    else "借券為成交事件資料，日期為最近一筆成交；新鮮度依快取 TTL"
                    if event_based
                    else "依快取 TTL；日期為已快取標的中最舊的資料日"
                ) + f"；按需快取 {len(records)} 檔",
                "last_attempt": max(attempts) if attempts else None,
                "last_success": max(successes) if successes else None,
            }
        return result

    def _social(self) -> dict[str, dict[str, Any]]:
        from app.taiwan.social_sentiment import load_social_sentiment

        payload = load_social_sentiment() or {}
        day = data_date(payload.get("as_of"))
        result = {}
        for key in ("ptt", "dcard", "social_ai"):
            meta = (
                payload.get("ai", {})
                if key == "social_ai"
                else payload.get("sources", {}).get(key, {})
            )
            status = normalize_status(
                meta.get("status"), stale=bool(day and day < taipei_now().date().isoformat())
            )
            if status == "current" and not day:
                status = "partial"
            errors = meta.get("errors") or []
            if key == "social_ai" and errors and meta.get("batches") and not meta.get("analyzed_symbols"):
                status = "unavailable"  # every batch failed: nothing usable, not partial
            if status in {"unavailable", "error"} and http_error(errors):
                status = "provider_error"
            if key == "social_ai" and any("not configured" in str(e) for e in errors):
                status = "config_missing"
            reason = (
                ai_reason(meta, errors)
                if key == "social_ai" and errors and status != "config_missing"
                else safe_reason(errors)
                if errors
                else (
                    "既有社群快照資料"
                    if status == "current"
                    else "社群快照不是今日資料"
                    if status == "stale"
                    else "來源只取得部分資料"
                    if status == "partial"
                    else "尚未取得有效來源資料或 AI 解讀"
                )
            )
            result[key] = {
                "status": status,
                "source": {
                    "ptt": "PTT Stock",
                    "dcard": "Dcard",
                    "social_ai": "Social Sentiment AI",
                }[key],
                "data_date": day if status in {"current", "stale", "partial"} else None,
                "freshness": "社群快照日期；非今日即視為過期",
                "reason": reason,
                "last_attempt": timestamp(payload.get("started_at")),
                "last_success": timestamp(payload.get("finished_at"))
                if status in {"current", "stale", "partial"}
                else None,
            }
        return result

    def _realtime(self) -> dict[str, Any]:
        from app.taiwan.realtime.service import get_realtime_service

        records = get_realtime_service().health_metadata()
        statuses = [
            normalize_status(r.get("status"), stale=bool(r.get("is_stale"))) for r in records
        ]
        status: HealthStatus = combined_status(statuses) if records else "not_run"
        attempts = [t for r in records if (t := timestamp(r.get("fetched_at")))]
        successes = [
            t
            for r, s in zip(records, statuses, strict=True)
            if s in {"current", "stale", "partial"} and (t := timestamp(r.get("fetched_at")))
        ]
        dates = [d for r in records if (d := data_date(r.get("trade_date")))]
        reasons = [r["fallback_reason"] for r in records if r.get("fallback_reason")]
        sources = {
            "TWSE MIS"
            if "mis" in str(r.get("source"))
            else "Yahoo"
            if "yahoo" in str(r.get("source"))
            else "官方收盤／既有 fallback"
            for r in records
        }
        return {
            "status": status,
            "source": " / ".join(sorted(sources)) or None,
            "data_date": min(dates) if dates else None,
            "freshness": "沿用即時服務 freshness policy，僅觀察已查詢行情",
            "reason": "本次服務啟動後尚未查詢即時行情（開啟報價頁面時才會取得）"
            if not records
            else safe_reason(
                reasons[0] if reasons else None,
                "依既有行情來源、延遲、fallback 與 stale metadata 判定",
            ),
            "last_attempt": max(attempts) if attempts else None,
            "last_success": max(successes) if successes else None,
        }

    def _security_master(self) -> dict[str, Any]:
        from app.taiwan.universe import get_security_master

        meta = get_security_master().health_metadata()
        status = normalize_status(meta.get("status"))
        # A persisted reference timestamp is not evidence of an up-to-date universe.
        if status == "current":
            status = "partial"
        return {
            "status": status,
            "source": "TWSE / TPEx security master",
            "data_date": data_date(meta.get("fetched_at")),
            "last_success": timestamp(meta.get("fetched_at")),
            "reason": safe_reason(meta.get("reason")),
            "freshness": "參考快照，更新頻率未記錄",
        }

    def _calendar(self) -> dict[str, Any]:
        day = taipei_now().date()
        evidence = session_evidence()
        facts = [evidence(day, exchange) for exchange in ("TWSE", "TPEX")]
        known = sum(f.status in {"trading", "non_trading"} for f in facts)
        return {
            "status": "current" if known == 2 else "partial" if known else "awaiting_publication",
            "source": "官方交易日 evidence / 官方日行情 / TaiwanTradingCalendar",
            "data_date": day.isoformat(),
            "freshness": "今日交易日證據",
            "reason": "兩交易所交易日狀態已確認"
            if known == 2
            else "今日交易日將於官方盤後日行情入庫後確認；未將未知平日視為開市或休市",
        }

    def _quant(self) -> dict[str, Any]:
        from app.taiwan.quant.live_contract import LiveModel
        from app.taiwan.quant.live_store import LiveLedger

        ledger = LiveLedger(evidence=session_evidence())
        op = ledger.latest_operation() or {}
        now = taipei_now()
        today = now.date().isoformat()
        try:
            session = ledger.current_session().isoformat()
        except ValueError as exc:
            pending = str(exc).rsplit(":", 1)[-1] if "session_evidence_unresolved" in str(exc) else None
            return {
                "status": "not_run" if pending == today else "unavailable",
                "source": "LiveLedger",
                "data_date": pending,
                "freshness": "目前已完成且有官方證據的交易日",
                # Downstream consequence; an older ranking is never shown as today's.
                "reason": f"等待 {today} 官方日行情入庫後才執行今日 Live Quant（下游依賴 Daily）"
                if pending == today
                else REASONS["session_unavailable"],
                "last_attempt": timestamp(op.get("recorded_at")),
            }
        run = ledger.read_run(LiveModel().key, session)
        projection = ledger.operation_projection(op, session)
        valid = bool(run and run.get("audit_status") == "ok" and projection["reason"] == "current")
        reason = (
            "current"
            if valid
            else "audit_conflict"
            if run and run.get("audit_status") == "conflict"
            else projection["reason"]
        )
        if not run:
            reason = "live_run_missing"
        not_run = not run and session == today and now.time() < SCHEDULED_UPDATE_GRACE
        return {
            "status": "current"
            if valid
            else "error"
            if reason == "audit_conflict"
            else "not_run"
            if not_run
            else "unavailable",
            "source": "LiveLedger / current_live_gate",
            "data_date": session,
            "freshness": "目前已完成且有官方證據的交易日",
            "reason": "目前交易日 Live Quant run 與快照驗證有效"
            if valid
            else "今日 Live Quant 於 16:30 盤後更新完成後執行"
            if not_run
            else safe_reason(reason),
            "last_attempt": timestamp(op.get("recorded_at")),
            "last_success": timestamp(run.get("frozen_at")) if valid and run else None,
        }

    def _selection(self) -> dict[str, dict[str, Any]]:
        from app.taiwan.selection_review_service import get_selection_review_service

        rows = get_selection_review_service().health_metadata()
        outcome = rows["selection_outcome"]
        codes = outcome.pop("reason_codes", [])
        if codes and outcome["status"] != "current":
            outcome["reason"] += "：" + "；".join(sorted({safe_reason(c, c) for c in codes}))
        return rows

    def _ai(self) -> dict[str, Any]:
        from app.services.ai_provider import is_codex_cli_provider, snapshot_ai_provider_config

        cfg = snapshot_ai_provider_config()
        configured = bool(cfg.model and (is_codex_cli_provider(cfg.provider) or cfg.api_key))
        return {
            "status": "not_run" if configured else "config_missing",
            "source": "Codex CLI" if is_codex_cli_provider(cfg.provider) else "AI Provider/Profile",
            "freshness": "設定狀態，連線尚未驗證",
            "reason": "目前 provider/profile 已設定，尚未驗證連線；請按「重新驗證」"
            if configured
            else "AI provider/profile 尚未完整設定",
        }

    @staticmethod
    def _index(key: str) -> dict[str, Any]:
        from app.taiwan.benchmark_store import latest_benchmark
        from app.taiwan.daily_update import read_last_run, resolve_target_latest_trading_date

        symbol = "TAIEX" if key == "taiex" else "TPEX_INDEX"
        row = latest_benchmark(symbol)
        if not row:
            return {
                "status": "unavailable",
                "source": "TWSE MI_5MINS_HIST / TPEx tpex_index（官方 OpenAPI）",
                "freshness": "官方指數日收盤，隨盤後更新保存",
                "reason": "尚未保存官方指數收盤；執行盤後更新後自動取得",
            }
        target = resolve_target_latest_trading_date()
        day = row["date"]
        run = read_last_run() or {}
        benchmark = run.get("benchmark") or {}
        failures = benchmark.get("failed") or []
        failure = next(
            (item for item in failures if item.get("symbol") in {symbol, None}),
            None,
        ) if run.get("target_latest_trading_date") == target.isoformat() else None
        status: HealthStatus = (
            "current" if day >= target
            else "provider_error" if failure
            else "awaiting_publication" if target == taipei_now().date()
            and day >= target - timedelta(days=7)
            else "stale"
        )
        return {
            "status": status,
            "source": row["source"],
            "data_date": day.isoformat(),
            "freshness": f"官方指數日收盤；目標交易日 {target}",
            "reason": "已保存官方指數收盤"
            if status == "current"
            else safe_reason(failure.get("error"), "本次官方指數更新失敗，原有資料保留")
            if status == "provider_error" and failure
            else f"官方 OpenAPI 尚未提供 {target} 指數收盤；已保存最近官方收盤"
            if status == "awaiting_publication"
            else f"已保存的官方指數收盤早於目標交易日 {target}",
            "last_success": timestamp(row.get("retrieved_at")),
            "last_attempt": timestamp(run.get("run_started_at")) if failure else None,
        }


def get_health_snapshot() -> HealthReport:
    return DataHealthService().snapshot()

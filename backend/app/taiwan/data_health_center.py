"""Read-only projection of existing Taiwan metadata. No fetching or pipeline here."""

# ruff: noqa: RUF001
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.taiwan.realtime.calendar import taipei_now

HealthStatus = Literal["current", "stale", "partial", "unavailable", "updating", "error"]
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
}
REASONS = {
    "exchange_missing": "部分交易所資料缺漏，尚未完整涵蓋 TWSE 與 TPEx",
    "reference_snapshot": "已保存參考快照，更新頻率未記錄，無法確認目前新鮮度",
    "security_master_missing": "本地證券主檔尚不存在",
    "cache_metadata_invalid": "本地快取 metadata 無法讀取",
    "live_run_missing": "目前交易日尚未建立有效 Live Quant run",
    "operation_not_current_success": "目前交易日尚未完成有效 Live Quant 更新",
    "audit_conflict": "Live Quant 快照驗證衝突",
    "session_unavailable": "缺少官方交易日證據，無法確認目前 Live Quant session",
    "corporate_action_coverage_unavailable": "公司行動涵蓋範圍尚未驗證",
    "current_market_evidence_unavailable": "目前交易日官方市場證據不可用",
    "ranking_features_data_insufficient": "不足 61 個已驗證交易日，無法建立排名因子",
    "FinMind provider disabled": "FinMind 資料源未啟用",
    "AUTH_ERROR": "AI Provider 驗證失敗，請檢查授權設定",
    "INVALID_MODEL": "AI Provider 模型設定無效",
    "ENDPOINT_ERROR": "AI Provider 連線設定無效",
}


def safe_reason(value: Any, fallback: str = "資料源回報失敗，無法取得有效資料") -> str:
    """Never send raw provider exceptions, URLs, paths or credentials to the client."""
    text = str(value or "")
    for code, explanation in REASONS.items():
        if code in text:
            return explanation
    match = re.search(r"(?:HTTP[ /:]*(?:status[ :=]*)?|status code[ :=]*)([45]\d{2})", text, re.I)
    if match:
        return f"HTTP {match.group(1)}，資料源拒絕或無法完成請求"
    return fallback


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
            if key in {"daily", "institutional", "margin", "ptt", "social_ai"}:
                row.actions = ["validate", "update" if row.status == "current" else "retry"]
            if key in {"dcard", "taiex", "tpex_index"}:
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
        }

    def _daily(self) -> dict[str, dict[str, Any]]:
        from app.taiwan.daily_update import TaiwanDailyUpdateService

        svc = TaiwanDailyUpdateService()
        freshness = svc.get_freshness().model_dump()
        result: dict[str, dict[str, Any]] = {}
        for key, store in (
            ("daily", svc.daily_store),
            ("institutional", svc.inst_store),
            ("margin", svc.margin_store),
        ):
            status = normalize_status(freshness[f"{key}_status"])
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
            if frame.is_empty():
                status, day = "unavailable", None
            elif not {"TWSE", "TPEX"}.issubset({s.rsplit(".", 1)[-1] for s in frame["symbol"]}):
                status = "partial"
            elif "status" in frame.columns:
                status = combined_status(
                    [status, *[normalize_status(s) for s in frame["status"].unique()]]
                )
            reason = {
                "current": "符合既有更新器的交易日與盤後發布時點規則",
                "stale": "尚未取得目標交易日的官方資料；官方未發布或前次更新未完成",
                "partial": REASONS["exchange_missing"],
                "unavailable": "本地尚無有效資料",
                "error": "已保存的資料回報驗證失敗",
            }[status]
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
                "data_date": day if status in {"current", "stale", "partial"} else None,
                "freshness": f"目標交易日 {freshness['target_latest_trading_date']}",
                "reason": reason,
            }
        return result

    def _finmind(self) -> dict[str, dict[str, Any]]:
        from app.taiwan.finmind_cache import FinMindCache

        cache = FinMindCache()
        result = {}
        for key, dataset in (
            ("financial", "TaiwanStockFinancialStatements"),
            ("monthly_revenue", "TaiwanStockMonthRevenue"),
            ("foreign_shareholding", "TaiwanStockShareholding"),
            ("securities_lending", "TaiwanStockSecuritiesLending"),
        ):
            records = cache.health_metadata(dataset)
            status = combined_status([normalize_status(r.get("status")) for r in records])
            if status == "current" and any(not data_date(r.get("data_date")) for r in records):
                status = "partial"
            attempts = [t for r in records if (t := timestamp(r.get("fetched_at")))]
            successes = [
                t
                for r in records
                if r.get("status") in {"available", "stale"}
                and (t := timestamp(r.get("fetched_at")))
            ]
            dates = [d for r in records if (d := data_date(r.get("data_date")))]
            errors = [r.get("error_msg") for r in records if r.get("error_msg")]
            reason = (
                safe_reason(errors[0])
                if errors
                else {
                    "current": "已快取標的符合既有 TTL，未快取標的仍屬未查詢",
                    "partial": "已快取標的的 availability 或 freshness 不一致",
                    "stale": "已快取資料超過既有資料集 TTL",
                    "unavailable": "尚未查詢或快取沒有有效資料",
                    "error": "既有快取 metadata 無法讀取",
                }[status]
            )
            result[key] = {
                "status": status,
                "source": "FinMind cache",
                "reason": reason,
                "data_date": min(dates) if dates else None,
                "freshness": "依既有資料集 TTL；範圍為已快取標的，日期為最舊資料日",
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
            reason = (
                safe_reason(meta.get("errors"))
                if meta.get("errors")
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
                "freshness": "既有社群快照日期",
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
        status = combined_status(statuses)
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
            "reason": "尚未查詢即時行情"
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
        from app.taiwan.observed_universe import ObservedUniverseStore
        from app.taiwan.realtime.calendar import TaiwanTradingCalendar

        store, cal, day = ObservedUniverseStore(), TaiwanTradingCalendar(), taipei_now().date()
        facts = [store.day_evidence(exchange, day, calendar=cal) for exchange in ("TWSE", "TPEX")]
        known = sum(f.status in {"trading", "non_trading"} for f in facts)
        return {
            "status": "current" if known == 2 else "partial" if known else "unavailable",
            "source": "官方交易日 evidence / TaiwanTradingCalendar",
            "data_date": day.isoformat(),
            "freshness": "今日交易日證據",
            "reason": "兩交易所交易日狀態已確認"
            if known == 2
            else "交易日證據尚未完整確認，未將未知平日視為開市或休市",
        }

    def _quant(self) -> dict[str, Any]:
        from app.taiwan.observed_universe import ObservedUniverseStore
        from app.taiwan.quant.live_contract import LiveModel
        from app.taiwan.quant.live_store import LiveLedger
        from app.taiwan.realtime.calendar import TaiwanTradingCalendar

        store, cal = ObservedUniverseStore(), TaiwanTradingCalendar()
        ledger = LiveLedger(
            evidence=lambda day, exchange: store.day_evidence(exchange, day, calendar=cal)
        )
        op = ledger.latest_operation() or {}
        try:
            session = ledger.current_session().isoformat()
        except ValueError:
            return {
                "status": "unavailable",
                "source": "LiveLedger",
                "reason": REASONS["session_unavailable"],
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
        return {
            "status": "current"
            if valid
            else "error"
            if reason == "audit_conflict"
            else "unavailable",
            "source": "LiveLedger / current_live_gate",
            "data_date": session,
            "freshness": "目前已完成且有官方證據的交易日",
            "reason": "目前交易日 Live Quant run 與快照驗證有效" if valid else safe_reason(reason),
            "last_attempt": timestamp(op.get("recorded_at")),
            "last_success": timestamp(run.get("frozen_at")) if valid and run else None,
        }

    def _selection(self) -> dict[str, dict[str, Any]]:
        from app.taiwan.selection_review_service import get_selection_review_service

        return get_selection_review_service().health_metadata()

    def _ai(self) -> dict[str, Any]:
        from app.services.ai_provider import is_codex_cli_provider, snapshot_ai_provider_config

        cfg = snapshot_ai_provider_config()
        configured = bool(cfg.model and (is_codex_cli_provider(cfg.provider) or cfg.api_key))
        return {
            "status": "partial" if configured else "unavailable",
            "source": "Codex CLI" if is_codex_cli_provider(cfg.provider) else "AI Provider/Profile",
            "freshness": "設定狀態，連線尚未驗證",
            "reason": "目前 provider/profile 已設定，需重新驗證連線"
            if configured
            else "AI provider/profile 尚未完整設定",
        }

    @staticmethod
    def _index(key: str) -> dict[str, Any]:
        from app.taiwan.market_intelligence import persisted_index_snapshot

        meta = getattr(persisted_index_snapshot(), key)
        status = normalize_status(meta.status if meta else None)
        return {
            "status": status,
            "source": "taiwan_index_provider / persisted benchmark",
            "data_date": data_date(meta.trade_date) if meta and status != "unavailable" else None,
            "reason": "本地 persisted benchmark 尚不存在，既有即時指數不等於歷史基準資料"
            if status == "unavailable"
            else "既有 persisted benchmark metadata",
        }


def get_health_snapshot() -> HealthReport:
    return DataHealthService().snapshot()

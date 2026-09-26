"""Taiwan Market Event Center Service (A11).

Provides authoritative, deterministic market and company events for Taiwan stocks:
- Corporate Actions: Cash/stock dividends, capital reduction, stock splits (par change)
- MOPS Dividend Lifecycle: Board resolution, shareholder resolution, ex-date announcement
- Regulatory & Trading Status: Disposition securities (處置股), warning securities (注意股),
  suspended trading (暫停交易), resumption (恢復交易), delisting (終止上市/下市)
- Announcements: Monthly revenue announcements, financial statement announcements

Severity mapping (Deterministic, no AI):
- info: Dividends, revenue announcements, financial statements
- attention: Disposition, warning securities, capital reduction, par change, resumed trading
- risk: Suspended trading, delisting
"""
# ruff: noqa: RUF001
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.config import settings
from app.taiwan.corporate_actions import CorporateActionStore
from app.taiwan.finmind_cache import FinMindCache
from app.taiwan.providers.http import fetch_json
from app.taiwan.providers.taiwan_values import parse_taiwan_date
from app.taiwan.realtime.calendar import taipei_now
from app.taiwan.universe import TaiwanSecurityMaster, get_security_master

logger = logging.getLogger(__name__)

EventSeverity = Literal["info", "attention", "risk"]
EventScope = Literal["today", "week", "portfolio", "watchlist", "all"]

SEVERITY_BY_EVENT_TYPE: dict[str, EventSeverity] = {
    "cash_dividend": "info",
    "stock_dividend": "info",
    "dividend_announcement": "info",
    "revenue_announcement": "info",
    "financial_statement_announcement": "info",
    "disposition": "attention",
    "warning": "attention",
    "capital_reduction": "attention",
    "par_change": "attention",
    "resume_trading": "attention",
    "suspended_trading": "risk",
    "delisting": "risk",
}

EVENT_TYPE_LABELS: dict[str, str] = {
    "cash_dividend": "除息",
    "stock_dividend": "除權",
    "dividend_announcement": "股利宣告",
    "revenue_announcement": "營收公布",
    "financial_statement_announcement": "財報公布",
    "disposition": "處置證券",
    "warning": "注意股票",
    "capital_reduction": "減資",
    "par_change": "面額變更/分割",
    "suspended_trading": "暫停交易",
    "resume_trading": "恢復交易",
    "delisting": "終止上市/下市",
}


class MarketEvent(BaseModel):
    """Canonical normalized market event model."""

    id: str = Field(..., description="確定性事件識別碼")
    symbol: str = Field(..., description="標準標的代碼，如 2330.TWSE")
    code: str = Field(..., description="標的代號，如 2330")
    name: str = Field(..., description="標的名稱，如 台積電")
    exchange: str = Field(..., description="市場，TWSE 或 TPEX")
    event_date: str = Field(..., description="事件所屬或生效日期 YYYY-MM-DD")
    event_type: str = Field(..., description="事件類型")
    event_type_label: str = Field(..., description="事件類型繁體中文標籤")
    severity: EventSeverity = Field("info", description="嚴重等級: info, attention, risk")
    title: str = Field(..., description="事件簡短標題")
    summary: str = Field(..., description="事件摘要說明")
    source: str = Field(..., description="官方或權威資料來源識別")
    source_url: str | None = Field(None, description="來源 URL")
    retrieved_at: str = Field(..., description="資料檢索時間 ISO 字串")
    freshness: str = Field("fresh", description="資料新鮮度: fresh, cached, stale, unavailable")
    details: dict[str, Any] = Field(default_factory=dict, description="結構化細節")


class EventCandidatesResponse(BaseModel):
    """Event-driven stocks worthy of attention."""

    candidates: list[dict[str, Any]] = Field(default_factory=list)
    as_of_date: str
    total: int


class EventsListResponse(BaseModel):
    """Event Center API query response."""

    events: list[MarketEvent]
    total: int
    as_of_date: str
    status: str = "available"
    sources_status: dict[str, str] = Field(default_factory=dict)


def _cache_path() -> Path:
    p = settings.data_dir / "taiwan" / "events_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p / "regulatory_events.json"


def _delivered_alerts_cache_path(data_dir: Path) -> Path:
    p = data_dir / "taiwan" / "events_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p / "delivered_event_alerts.json"


def _load_delivered_alert_ids(data_dir: Path) -> set[str]:
    path = _delivered_alerts_cache_path(data_dir)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return set(data)
        except Exception as e:
            logger.warning("Failed to load delivered event alert ids: %s", e)
    return set()


def _save_delivered_alert_ids(data_dir: Path, ids: set[str]) -> None:
    path = _delivered_alerts_cache_path(data_dir)
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(ids), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        logger.warning("Failed to save delivered event alert ids: %s", e)


class TaiwanEventService:
    """Singleton service managing Taiwan market events."""

    def __init__(
        self,
        security_master: TaiwanSecurityMaster | None = None,
        finmind_cache: FinMindCache | None = None,
    ) -> None:
        self.security_master = security_master or get_security_master()
        self.finmind_cache = finmind_cache or FinMindCache()
        self._memory_cache: dict[str, tuple[float, list[MarketEvent]]] = {}
        self._cache_ttl = 3600  # 1 hour cache for official announcements
        self.sources_status: dict[str, str] = {
            "twse_punish": "available",
            "tpex_disposal": "available",
            "twse_warning": "available",
            "tpex_warning": "available",
            "twse_delisting": "available",
            "tpex_cmode": "available",
            "corporate_actions": "available",
        }
        self.last_status: str = "available"

    def _resolve_symbol(self, raw_code: str, fallback_exchange: str = "TWSE") -> tuple[str, str, str, str]:
        """Resolve raw code into (symbol, code, name, exchange)."""
        code = str(raw_code).strip().upper()
        if "." in code:
            parts = code.split(".")
            code = parts[0]
            fallback_exchange = parts[1]

        inst = self.security_master.get_instrument(f"{code}.{fallback_exchange}")
        if not inst:
            # Try alternate exchange
            alt_exchange = "TPEX" if fallback_exchange == "TWSE" else "TWSE"
            inst = self.security_master.get_instrument(f"{code}.{alt_exchange}")

        if inst:
            exch = inst.exchange.value if hasattr(inst.exchange, "value") else str(inst.exchange)
            return inst.symbol, code, inst.name, exch
        return f"{code}.{fallback_exchange}", code, code, fallback_exchange

    def fetch_twse_punish_events(self) -> list[MarketEvent]:
        """Fetch TWSE disposition securities (處置股票)."""
        url = "https://openapi.twse.com.tw/v1/announcement/punish"
        now_iso = taipei_now().isoformat()
        try:
            rows = fetch_json(url, timeout=8.0)
            if not isinstance(rows, list):
                self.sources_status["twse_punish"] = "unavailable"
                return []
            self.sources_status["twse_punish"] = "available"
            events: list[MarketEvent] = []
            for r in rows:
                if not isinstance(r, dict):
                    continue
                code = str(r.get("Code") or "").strip()
                if not code or code == "0":
                    continue
                raw_date = str(r.get("Date") or "").strip()
                try:
                    ev_date = parse_taiwan_date(raw_date).isoformat()
                except Exception:
                    ev_date = taipei_now().date().isoformat()

                symbol, clean_code, name, exchange = self._resolve_symbol(code, "TWSE")
                reasons = str(r.get("ReasonsOfDisposition") or "").strip()
                period = str(r.get("DispositionPeriod") or "").strip()
                measures = str(r.get("DispositionMeasures") or "").strip()
                detail = str(r.get("Detail") or "").strip()

                event_id = hashlib.sha256(f"twse_punish_{clean_code}_{ev_date}_{period}".encode()).hexdigest()[:16]
                summary = f"處置期間: {period}，措施: {measures}" if period else measures
                events.append(
                    MarketEvent(
                        id=f"evt_disp_{event_id}",
                        symbol=symbol,
                        code=clean_code,
                        name=name,
                        exchange=exchange,
                        event_date=ev_date,
                        event_type="disposition",
                        event_type_label=EVENT_TYPE_LABELS["disposition"],
                        severity="attention",
                        title=f"列為處置有價證券 ({measures or '撮合管制'})",
                        summary=summary,
                        source="twse:punish",
                        source_url="https://www.twse.com.tw/rwd/zh/announcement/punish",
                        retrieved_at=now_iso,
                        freshness="fresh",
                        details={
                            "period": period,
                            "measures": measures,
                            "reasons": reasons,
                            "detail": detail,
                        },
                    )
                )
            return events
        except Exception as e:
            logger.warning("Failed to fetch TWSE punish announcements: %s", e)
            self.sources_status["twse_punish"] = "unavailable"
            return []

    def fetch_tpex_disposal_events(self) -> list[MarketEvent]:
        """Fetch TPEx disposition securities (上櫃處置股票)."""
        url = "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"
        now_iso = taipei_now().isoformat()
        try:
            rows = fetch_json(url, timeout=8.0)
            if not isinstance(rows, list):
                self.sources_status["tpex_disposal"] = "unavailable"
                return []
            self.sources_status["tpex_disposal"] = "available"
            events: list[MarketEvent] = []
            for r in rows:
                if not isinstance(r, dict):
                    continue
                code = str(r.get("SecuritiesCompanyCode") or "").strip()
                if not code or code == "0":
                    continue
                raw_date = str(r.get("Date") or "").strip()
                try:
                    ev_date = parse_taiwan_date(raw_date).isoformat()
                except Exception:
                    ev_date = taipei_now().date().isoformat()

                symbol, clean_code, name, exchange = self._resolve_symbol(code, "TPEX")
                reasons = str(r.get("DispositionReasons") or "").strip()
                period = str(r.get("DispositionPeriod") or "").strip()
                cond = str(r.get("DisposalCondition") or "").strip()

                event_id = hashlib.sha256(f"tpex_disp_{clean_code}_{ev_date}_{period}".encode()).hexdigest()[:16]
                summary = f"處置期間: {period}。{reasons}" if period else reasons
                events.append(
                    MarketEvent(
                        id=f"evt_disp_{event_id}",
                        symbol=symbol,
                        code=clean_code,
                        name=name,
                        exchange=exchange,
                        event_date=ev_date,
                        event_type="disposition",
                        event_type_label=EVENT_TYPE_LABELS["disposition"],
                        severity="attention",
                        title="列為上櫃處置有價證券",
                        summary=summary,
                        source="tpex:disposal",
                        source_url="https://www.tpex.org.tw/openapi/v1/tpex_disposal_information",
                        retrieved_at=now_iso,
                        freshness="fresh",
                        details={
                            "period": period,
                            "reasons": reasons,
                            "condition": cond,
                        },
                    )
                )
            return events
        except Exception as e:
            logger.warning("Failed to fetch TPEx disposal announcements: %s", e)
            self.sources_status["tpex_disposal"] = "unavailable"
            return []

    def fetch_twse_warning_events(self) -> list[MarketEvent]:
        """Fetch TWSE attention/warning securities (注意股票)."""
        url = "https://openapi.twse.com.tw/v1/announcement/notice"
        now_iso = taipei_now().isoformat()
        try:
            rows = fetch_json(url, timeout=8.0)
            if not isinstance(rows, list):
                self.sources_status["twse_warning"] = "unavailable"
                return []
            self.sources_status["twse_warning"] = "available"
            events: list[MarketEvent] = []
            for r in rows:
                if not isinstance(r, dict):
                    continue
                code = str(r.get("Code") or "").strip()
                if not code or code == "0":
                    continue
                raw_date = str(r.get("Date") or "").strip()
                try:
                    ev_date = parse_taiwan_date(raw_date).isoformat()
                except Exception:
                    ev_date = taipei_now().date().isoformat()

                symbol, clean_code, name, exchange = self._resolve_symbol(code, "TWSE")
                info = str(r.get("TradingInfoForAttention") or "").strip()

                event_id = hashlib.sha256(f"twse_notice_{clean_code}_{ev_date}".encode()).hexdigest()[:16]
                events.append(
                    MarketEvent(
                        id=f"evt_warn_{event_id}",
                        symbol=symbol,
                        code=clean_code,
                        name=name,
                        exchange=exchange,
                        event_date=ev_date,
                        event_type="warning",
                        event_type_label=EVENT_TYPE_LABELS["warning"],
                        severity="attention",
                        title="公布注意有價證券",
                        summary=info or "達到證交所公布注意有價證券標準",
                        source="twse:notice",
                        source_url="https://www.twse.com.tw/rwd/zh/announcement/notice",
                        retrieved_at=now_iso,
                        freshness="fresh",
                        details={"info": info},
                    )
                )
            return events
        except Exception as e:
            logger.warning("Failed to fetch TWSE warning announcements: %s", e)
            self.sources_status["twse_warning"] = "unavailable"
            return []

    def fetch_tpex_warning_events(self) -> list[MarketEvent]:
        """Fetch TPEx warning securities (上櫃注意股票)."""
        url = "https://www.tpex.org.tw/openapi/v1/tpex_trading_warning_information"
        now_iso = taipei_now().isoformat()
        try:
            rows = fetch_json(url, timeout=8.0)
            if not isinstance(rows, list):
                self.sources_status["tpex_warning"] = "unavailable"
                return []
            self.sources_status["tpex_warning"] = "available"
            events: list[MarketEvent] = []
            for r in rows:
                if not isinstance(r, dict):
                    continue
                code = str(r.get("SecuritiesCompanyCode") or "").strip()
                if not code or code == "0":
                    continue
                raw_date = str(r.get("Date") or "").strip()
                try:
                    ev_date = parse_taiwan_date(raw_date).isoformat()
                except Exception:
                    ev_date = taipei_now().date().isoformat()

                symbol, clean_code, name, exchange = self._resolve_symbol(code, "TPEX")
                info = str(r.get("TradingInformation") or "").strip()

                event_id = hashlib.sha256(f"tpex_warn_{clean_code}_{ev_date}".encode()).hexdigest()[:16]
                events.append(
                    MarketEvent(
                        id=f"evt_warn_{event_id}",
                        symbol=symbol,
                        code=clean_code,
                        name=name,
                        exchange=exchange,
                        event_date=ev_date,
                        event_type="warning",
                        event_type_label=EVENT_TYPE_LABELS["warning"],
                        severity="attention",
                        title="公布上櫃注意股票",
                        summary=info[:120] + "…" if len(info) > 120 else (info or "達到櫃買中心注意股票標準"),
                        source="tpex:warning",
                        source_url="https://www.tpex.org.tw/openapi/v1/tpex_trading_warning_information",
                        retrieved_at=now_iso,
                        freshness="fresh",
                        details={"info": info},
                    )
                )
            return events
        except Exception as e:
            logger.warning("Failed to fetch TPEx warning announcements: %s", e)
            self.sources_status["tpex_warning"] = "unavailable"
            return []

    def fetch_twse_delisting_events(self) -> list[MarketEvent]:
        """Fetch TWSE delisting announcements (終止上市/下市)."""
        url = "https://openapi.twse.com.tw/v1/company/suspendListingCsvAndHtml"
        now_iso = taipei_now().isoformat()
        try:
            rows = fetch_json(url, timeout=8.0)
            if not isinstance(rows, list):
                self.sources_status["twse_delisting"] = "unavailable"
                return []
            self.sources_status["twse_delisting"] = "available"
            events: list[MarketEvent] = []
            # Keep recent delistings (within current/recent years)
            current_year = taipei_now().year
            for r in rows:
                if not isinstance(r, dict):
                    continue
                code = str(r.get("Code") or "").strip()
                if not code or code == "0":
                    continue
                raw_date = str(r.get("DelistingDate") or "").strip()
                try:
                    ev_date_obj = parse_taiwan_date(raw_date)
                    # Only take delistings from the past 2 years or future
                    if ev_date_obj.year < current_year - 2:
                        continue
                    ev_date = ev_date_obj.isoformat()
                except Exception:
                    continue

                symbol, clean_code, name, exchange = self._resolve_symbol(code, "TWSE")
                event_id = hashlib.sha256(f"twse_delist_{clean_code}_{ev_date}".encode()).hexdigest()[:16]
                events.append(
                    MarketEvent(
                        id=f"evt_delist_{event_id}",
                        symbol=symbol,
                        code=clean_code,
                        name=name,
                        exchange=exchange,
                        event_date=ev_date,
                        event_type="delisting",
                        event_type_label=EVENT_TYPE_LABELS["delisting"],
                        severity="risk",
                        title="終止上市 (下市)",
                        summary=f"證交所公告終止上市生效日: {ev_date}",
                        source="twse:delisting",
                        source_url="https://openapi.twse.com.tw/v1/company/suspendListingCsvAndHtml",
                        retrieved_at=now_iso,
                        freshness="fresh",
                        details={"delisting_date": ev_date},
                    )
                )
            return events
        except Exception as e:
            logger.warning("Failed to fetch TWSE delisting announcements: %s", e)
            self.sources_status["twse_delisting"] = "unavailable"
            return []

    def fetch_tpex_cmode_events(self) -> list[MarketEvent]:
        """Fetch TPEx altered trading / suspended trading (變更交易 / 停止買賣)."""
        url = "https://www.tpex.org.tw/openapi/v1/tpex_cmode"
        now_iso = taipei_now().isoformat()
        try:
            rows = fetch_json(url, timeout=8.0)
            if not isinstance(rows, list):
                self.sources_status["tpex_cmode"] = "unavailable"
                return []
            self.sources_status["tpex_cmode"] = "available"
            events: list[MarketEvent] = []
            for r in rows:
                if not isinstance(r, dict):
                    continue
                code = str(r.get("SecuritiesCompanyCode") or "").strip()
                if not code or code == "0":
                    continue
                raw_date = str(r.get("Date") or "").strip()
                try:
                    ev_date = parse_taiwan_date(raw_date).isoformat()
                except Exception:
                    ev_date = taipei_now().date().isoformat()

                symbol, clean_code, name, exchange = self._resolve_symbol(code, "TPEX")
                is_suspended = bool(str(r.get("SuspensionOfTrading") or "").strip())
                is_altered = bool(str(r.get("AlteredTrading") or "").strip())

                if is_suspended:
                    event_id = hashlib.sha256(f"tpex_suspend_{clean_code}_{ev_date}".encode()).hexdigest()[:16]
                    events.append(
                        MarketEvent(
                            id=f"evt_susp_{event_id}",
                            symbol=symbol,
                            code=clean_code,
                            name=name,
                            exchange=exchange,
                            event_date=ev_date,
                            event_type="suspended_trading",
                            event_type_label=EVENT_TYPE_LABELS["suspended_trading"],
                            severity="risk",
                            title="櫃買中心停止買賣 (暫停交易)",
                            summary="該證券經櫃買中心公告停止買賣",
                            source="tpex:cmode",
                            source_url="https://www.tpex.org.tw/openapi/v1/tpex_cmode",
                            retrieved_at=now_iso,
                            freshness="fresh",
                            details={"raw": r},
                        )
                    )
                elif is_altered:
                    event_id = hashlib.sha256(f"tpex_altered_{clean_code}_{ev_date}".encode()).hexdigest()[:16]
                    events.append(
                        MarketEvent(
                            id=f"evt_alt_{event_id}",
                            symbol=symbol,
                            code=clean_code,
                            name=name,
                            exchange=exchange,
                            event_date=ev_date,
                            event_type="warning",
                            event_type_label=EVENT_TYPE_LABELS["warning"],
                            severity="attention",
                            title="變更交易方法股票 (全額交割)",
                            summary="該證券經櫃買中心公告列為變更交易方法股票",
                            source="tpex:cmode",
                            source_url="https://www.tpex.org.tw/openapi/v1/tpex_cmode",
                            retrieved_at=now_iso,
                            freshness="fresh",
                            details={"raw": r},
                        )
                    )
            return events
        except Exception as e:
            logger.warning("Failed to fetch TPEx cmode announcements: %s", e)
            self.sources_status["tpex_cmode"] = "unavailable"
            return []

    def get_corporate_action_events(self) -> list[MarketEvent]:
        """Load canonical corporate actions from existing CorporateActionStore."""
        now_iso = taipei_now().isoformat()
        try:
            store = CorporateActionStore()
            actions = store.read()
            self.sources_status["corporate_actions"] = "available"
            events: list[MarketEvent] = []
            for a in actions:
                symbol, code, name, exchange = self._resolve_symbol(a.symbol, a.exchange)
                ev_date = a.effective_date.isoformat()
                event_type = a.event_type
                if event_type not in SEVERITY_BY_EVENT_TYPE:
                    event_type = "cash_dividend"

                # Generate concise title & summary
                if event_type == "cash_dividend":
                    div = f"{a.cash_dividend:.2f} 元" if a.cash_dividend else ""
                    title = f"除息 {div}".strip()
                    summary = f"除息基準日參考價 {a.reference_price or '—'} 元，前收盤價 {a.previous_close or '—'} 元"
                elif event_type == "stock_dividend":
                    ratio = f"{a.free_share_ratio * 100:.1f}%" if a.free_share_ratio else ""
                    title = f"除權配股 {ratio}".strip()
                    summary = f"除權基準日參考價 {a.reference_price or '—'} 元"
                elif event_type == "capital_reduction":
                    title = "減資換發新股"
                    summary = f"減資恢復買賣參考價 {a.reference_price or '—'} 元"
                elif event_type == "par_change":
                    title = "股票分割 / 面額變更"
                    summary = f"面額變更恢復買賣參考價 {a.reference_price or '—'} 元"
                else:
                    title = f"公司行動 ({event_type})"
                    summary = f"參考價 {a.reference_price or '—'} 元"

                event_id = hashlib.sha256(f"ca_{symbol}_{ev_date}_{event_type}".encode()).hexdigest()[:16]
                events.append(
                    MarketEvent(
                        id=f"evt_ca_{event_id}",
                        symbol=symbol,
                        code=code,
                        name=name,
                        exchange=exchange,
                        event_date=ev_date,
                        event_type=event_type,
                        event_type_label=EVENT_TYPE_LABELS.get(event_type, event_type),
                        severity=SEVERITY_BY_EVENT_TYPE.get(event_type, "info"),
                        title=title,
                        summary=summary,
                        source=f"official:{a.source}",
                        source_url=a.source_url,
                        retrieved_at=a.retrieved_at.isoformat() if a.retrieved_at else now_iso,
                        freshness="fresh",
                        details={
                            "previous_close": a.previous_close,
                            "reference_price": a.reference_price,
                            "factor": a.factor,
                            "cash_dividend": a.cash_dividend,
                            "free_share_ratio": a.free_share_ratio,
                            "status": a.status,
                        },
                    )
                )
            return events
        except Exception as e:
            logger.warning("Failed to load corporate actions from store: %s", e)
            self.sources_status["corporate_actions"] = "unavailable"
            return []

    def get_last_sources_status(self) -> tuple[str, dict[str, str]]:
        """Return the overall status and individual source statuses from the last query."""
        return self.last_status, dict(self.sources_status)

    def get_all_regulatory_and_official_events(self, force_refresh: bool = False) -> list[MarketEvent]:
        """Fetch and aggregate all official events with local file caching."""
        cache_file = _cache_path()
        now = datetime.now(UTC).timestamp()

        # Check memory cache
        if not force_refresh and "official" in self._memory_cache:
            ts, cached_events = self._memory_cache["official"]
            if now - ts < self._cache_ttl:
                return cached_events

        # Check disk cache
        if not force_refresh and cache_file.exists():
            try:
                raw_json = json.loads(cache_file.read_text(encoding="utf-8"))
                saved_at = raw_json.get("saved_at", 0)
                if now - saved_at < self._cache_ttl:
                    items = [MarketEvent(**it) for it in raw_json.get("events", [])]
                    self.last_status = raw_json.get("status", "available")
                    if "sources_status" in raw_json and isinstance(raw_json["sources_status"], dict):
                        self.sources_status.update(raw_json["sources_status"])
                    self._memory_cache["official"] = (saved_at, items)
                    return items
            except Exception as e:
                logger.warning("Failed to read events cache from disk: %s", e)

        # Fresh fetch from official sources
        all_events: list[MarketEvent] = []
        all_events.extend(self.fetch_twse_punish_events())
        all_events.extend(self.fetch_tpex_disposal_events())
        all_events.extend(self.fetch_twse_warning_events())
        all_events.extend(self.fetch_tpex_warning_events())
        all_events.extend(self.fetch_twse_delisting_events())
        all_events.extend(self.fetch_tpex_cmode_events())
        all_events.extend(self.get_corporate_action_events())

        avail_count = sum(1 for s in self.sources_status.values() if s == "available")
        total_sources = len(self.sources_status)
        if avail_count == total_sources:
            overall_status = "available"
        elif avail_count == 0:
            overall_status = "unavailable"
        else:
            overall_status = "partial"
        self.last_status = overall_status

        # Dedup by event ID
        deduped: dict[str, MarketEvent] = {}
        for ev in all_events:
            deduped[ev.id] = ev
        result = list(deduped.values())

        if overall_status == "unavailable":
            # Outage: Do NOT overwrite disk cache with empty data!
            # If disk cache exists, load stale items as fallback with freshness="stale"
            if cache_file.exists():
                try:
                    raw_json = json.loads(cache_file.read_text(encoding="utf-8"))
                    cached_items = [MarketEvent(**it) for it in raw_json.get("events", [])]
                    for it in cached_items:
                        it.freshness = "stale"
                    result = cached_items
                except Exception as e:
                    logger.warning("Failed to read stale fallback events cache from disk: %s", e)
            # Memory cache set with 0 TTL so it retries on next query
            self._memory_cache["official"] = (now - self._cache_ttl, result)
            return result

        # Save to disk cache
        try:
            payload = {
                "saved_at": now,
                "count": len(result),
                "status": overall_status,
                "sources_status": self.sources_status,
                "events": [ev.model_dump() for ev in result],
            }
            tmp = cache_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(cache_file)
        except Exception as e:
            logger.warning("Failed to save events cache to disk: %s", e)

        self._memory_cache["official"] = (now, result)
        return result

    def get_pit_events(
        self,
        symbol: str,
        as_of: date,
        limit: int = 10,
    ) -> list[MarketEvent]:
        """Fetch Point-in-Time (PIT) safe events for an explicit historical target date.

        Strict PIT Rules:
        - NEVER call live regulatory OpenAPI endpoints (punish, notice, delist, cmode)
          which only reflect current/live market state and cannot prove historical status.
        - Only include Corporate Actions from CorporateActionStore that have a verifiable
          availability or retrieval timestamp <= as_of.
        - If an event cannot prove it was known on or before as_of, fail-closed (exclude).
        """
        clean_sym = symbol.strip().upper()
        clean_code = clean_sym.split(".")[0]

        try:
            store = CorporateActionStore()
            actions = store.read()
        except Exception as e:
            logger.warning("Failed to read CorporateActionStore in get_pit_events: %s", e)
            return []

        pit_events: list[MarketEvent] = []
        for a in actions:
            a_sym, a_code, a_name, a_exch = self._resolve_symbol(a.symbol, a.exchange)
            if a_sym.upper() != clean_sym and a_code.upper() != clean_code and not clean_sym.startswith(a_code.upper()):
                continue

            # Proven availability check:
            # Must have availability timestamp or retrieval timestamp on or before as_of date
            avail_dt = a.available_at or a.retrieved_at
            if avail_dt is None:
                # No verified timestamp -> fail-closed
                continue
            if avail_dt.date() > as_of:
                # Discovered/retrieved after as_of -> fail-closed (cannot use future knowledge)
                continue

            # The event must also have an effective_date <= as_of (or be known by as_of)
            if a.effective_date > as_of:
                continue

            ev_date = a.effective_date.isoformat()
            event_type = a.event_type if a.event_type in SEVERITY_BY_EVENT_TYPE else "cash_dividend"

            if event_type == "cash_dividend":
                div = f"{a.cash_dividend:.2f} 元" if a.cash_dividend else ""
                title = f"除息 {div}".strip()
                summary = f"除息基準日參考價 {a.reference_price or '—'} 元，前收盤價 {a.previous_close or '—'} 元"
            elif event_type == "stock_dividend":
                ratio = f"{a.free_share_ratio * 100:.1f}%" if a.free_share_ratio else ""
                title = f"除權配股 {ratio}".strip()
                summary = f"除權基準日參考價 {a.reference_price or '—'} 元"
            elif event_type == "capital_reduction":
                title = "減資換發新股"
                summary = f"減資恢復買賣參考價 {a.reference_price or '—'} 元"
            elif event_type == "par_change":
                title = "股票分割 / 面額變更"
                summary = f"面額變更恢復買賣參考價 {a.reference_price or '—'} 元"
            else:
                title = f"公司行動 ({event_type})"
                summary = f"參考價 {a.reference_price or '—'} 元"

            event_id = hashlib.sha256(f"ca_{a_sym}_{ev_date}_{event_type}".encode()).hexdigest()[:16]
            pit_events.append(
                MarketEvent(
                    id=f"evt_ca_{event_id}",
                    symbol=a_sym,
                    code=a_code,
                    name=a_name,
                    exchange=a_exch,
                    event_date=ev_date,
                    event_type=event_type,
                    event_type_label=EVENT_TYPE_LABELS.get(event_type, event_type),
                    severity=SEVERITY_BY_EVENT_TYPE.get(event_type, "info"),
                    title=title,
                    summary=summary,
                    source=f"official:{a.source}",
                    source_url=a.source_url,
                    retrieved_at=avail_dt.isoformat(),
                    freshness="fresh",
                    details={
                        "previous_close": a.previous_close,
                        "reference_price": a.reference_price,
                        "factor": a.factor,
                        "cash_dividend": a.cash_dividend,
                        "free_share_ratio": a.free_share_ratio,
                        "status": a.status,
                    },
                )
            )

        pit_events.sort(key=lambda e: e.event_date, reverse=True)
        return pit_events[:limit]

    def get_events(
        self,
        scope: EventScope = "all",
        symbols: list[str] | None = None,
        symbol: str | None = None,
        event_types: list[str] | None = None,
        severity: EventSeverity | None = None,
        target_date: date | None = None,
        limit: int = 200,
    ) -> list[MarketEvent]:
        """Query normalized market events with scope and severity filtering."""
        all_events = self.get_all_regulatory_and_official_events()

        ref_date = target_date or taipei_now().date()
        today_str = ref_date.isoformat()
        week_ago_str = (ref_date - timedelta(days=7)).isoformat()
        week_ahead_str = (ref_date + timedelta(days=7)).isoformat()

        target_symbols_set = {s.strip().upper() for s in (symbols or []) if s.strip()}
        if symbol:
            target_symbols_set.add(symbol.strip().upper())

        filtered: list[MarketEvent] = []
        for ev in all_events:
            # Symbol filter
            if target_symbols_set:
                clean_sym = ev.symbol.upper()
                clean_code = ev.code.upper()
                if clean_sym not in target_symbols_set and clean_code not in target_symbols_set and not any(ts.startswith(clean_code) for ts in target_symbols_set):
                    continue

            # Event type filter
            if event_types and ev.event_type not in event_types:
                continue

            # Severity filter
            if severity and ev.severity != severity:
                continue

            # Scope date filter
            if scope == "today":
                # Matches today or ongoing disposition period
                if ev.event_date != today_str:
                    # If it's a disposition event, check if today is within its active period
                    period = ev.details.get("period", "")
                    if period and ("～" in period or "~" in period or "-" in period):
                        # Active period check
                        parts = re.split(r"[～~\-]", period)
                        if len(parts) >= 2:
                            try:
                                p_start = parse_taiwan_date(parts[0].strip()).isoformat()
                                p_end = parse_taiwan_date(parts[1].strip()).isoformat()
                                if not (p_start <= today_str <= p_end):
                                    continue
                            except Exception:
                                continue
                        else:
                            continue
                    else:
                        continue
            elif scope == "week":
                # Events in the past 7 days or upcoming 7 days
                if not (week_ago_str <= ev.event_date <= week_ahead_str):
                    continue
            elif scope in ("portfolio", "watchlist") and not target_symbols_set:
                # No symbols provided -> empty
                continue

            filtered.append(ev)

        # Deterministic Sort:
        # 1. event_date DESC (newest first)
        # 2. severity DESC (risk > attention > info)
        # 3. symbol ASC
        severity_rank = {"risk": 0, "attention": 1, "info": 2}
        filtered.sort(
            key=lambda e: (
                e.event_date,
                -severity_rank.get(e.severity, 3),
                e.symbol,
            ),
            reverse=True,
        )

        return filtered[:limit]

    def get_event_candidates(self, limit: int = 10) -> list[dict[str, Any]]:
        """Identify event-driven candidate stocks worthy of reviewing.

        Deterministic rules:
        - Recent disposition or warning securities
        - Imminent dividend ex-date
        - Capital reduction / split resumption
        - Risk warnings (suspended trading, delisting)
        """
        all_events = self.get_all_regulatory_and_official_events()
        today_str = taipei_now().date().isoformat()
        week_ahead_str = (taipei_now().date() + timedelta(days=7)).isoformat()

        candidates_map: dict[str, dict[str, Any]] = {}
        for ev in all_events:
            sym = ev.symbol
            if sym in candidates_map:
                continue

            reason = ""
            tag = ""
            if ev.event_type == "disposition":
                reason = f"列入處置股票 ({ev.summary})"
                tag = "處置列管"
            elif ev.event_type == "suspended_trading":
                reason = "暫停交易 / 停止買賣風險標的"
                tag = "交易暫停"
            elif ev.event_type == "delisting":
                reason = f"下市/終止上市風險 ({ev.summary})"
                tag = "重大風險"
            elif ev.event_type == "warning":
                reason = f"公布注意股票 ({ev.summary})"
                tag = "異常注意"
            elif ev.event_type in ("cash_dividend", "stock_dividend") and today_str <= ev.event_date <= week_ahead_str:
                reason = f"近期除權息 ({ev.title}, {ev.event_date})"
                tag = "除權息預告"
            elif ev.event_type in ("capital_reduction", "par_change"):
                reason = f"重大公司行動 ({ev.title})"
                tag = "公司行動"

            if reason:
                candidates_map[sym] = {
                    "symbol": sym,
                    "code": ev.code,
                    "name": ev.name,
                    "exchange": ev.exchange,
                    "reason": reason,
                    "tag": tag,
                    "severity": ev.severity,
                    "event_date": ev.event_date,
                    "event_type": ev.event_type,
                }
            if len(candidates_map) >= limit:
                break

        return list(candidates_map.values())

    def check_symbol_risk_status(self, symbol: str) -> dict[str, bool | str]:
        """Check if a stock currently has disposition, suspension, or severe risk events."""
        clean = symbol.strip().upper()
        events = self.get_events(scope="all", symbol=clean, limit=50)

        is_disposition = False
        is_suspended = False
        has_risk_event = False
        latest_risk_reason = ""

        today_str = taipei_now().date().isoformat()
        for ev in events:
            if ev.event_type == "disposition":
                # Check active period
                period = ev.details.get("period", "")
                if period:
                    parts = re.split(r"[～~\-]", period)
                    if len(parts) >= 2:
                        try:
                            p_start = parse_taiwan_date(parts[0].strip()).isoformat()
                            p_end = parse_taiwan_date(parts[1].strip()).isoformat()
                            if p_start <= today_str <= p_end:
                                is_disposition = True
                        except Exception:
                            is_disposition = True
                    else:
                        is_disposition = True
                else:
                    is_disposition = True
            elif ev.event_type == "suspended_trading":
                is_suspended = True
                has_risk_event = True
                latest_risk_reason = "暫停交易"
            elif ev.event_type == "delisting":
                has_risk_event = True
                latest_risk_reason = "終止上市"

        return {
            "is_disposition": is_disposition,
            "is_suspended": is_suspended,
            "has_risk_event": has_risk_event,
            "risk_reason": latest_risk_reason,
        }

    def trigger_event_alerts(
        self,
        symbols: list[str],
        data_dir: Path | None = None,
    ) -> list[dict[str, Any]]:
        """Check and trigger alerts for critical events (disposition, suspension, resumption,
        reduction, split, delisting, dividends) for user's watchlist or portfolio.
        Reuses existing alert_store and webhook_adapter.
        """
        from app.services import alert_store, preferences, webhook_adapter

        if not symbols:
            return []

        if data_dir is None:
            data_dir = settings.data_dir

        target_events = self.get_events(
            scope="all",
            symbols=symbols,
            event_types=[
                "disposition",
                "suspended_trading",
                "resume_trading",
                "capital_reduction",
                "par_change",
                "delisting",
                "cash_dividend",
                "stock_dividend",
            ],
            limit=100,
        )

        delivered_ids = _load_delivered_alert_ids(data_dir)
        existing_alerts = alert_store.list_recent(data_dir, days=7, limit=1000)
        existing_alert_ids = {str(a.get("alert_id")) for a in existing_alerts} | delivered_ids

        today = taipei_now().date()
        today_str = today.isoformat()
        min_date_str = (today - timedelta(days=3)).isoformat()
        max_date_str = (today + timedelta(days=30)).isoformat()

        triggered: list[dict[str, Any]] = []
        prefs = preferences.load()

        for ev in target_events:
            # Active disposition period check
            is_active_disposition = False
            if ev.event_type == "disposition":
                period = ev.details.get("period", "")
                if period:
                    parts = re.split(r"[～~\-]", period)
                    if len(parts) >= 2:
                        try:
                            p_start = parse_taiwan_date(parts[0].strip()).isoformat()
                            p_end = parse_taiwan_date(parts[1].strip()).isoformat()
                            if p_start <= today_str <= p_end:
                                is_active_disposition = True
                        except Exception:
                            pass

            if not is_active_disposition and (ev.event_date < min_date_str or ev.event_date > max_date_str):
                continue  # historical event or too distant in future, skip!

            alert_id = f"evt_alert_{ev.id}"
            if alert_id in existing_alert_ids:
                continue

            alert_event = {
                "alert_id": alert_id,
                "symbol": ev.symbol,
                "name": ev.name,
                "rule_id": f"event_rule_{ev.event_type}",
                "rule_name": f"{ev.event_type_label}重大事件",
                "rule_type": f"event_{ev.event_type}",
                "severity": "critical" if ev.severity == "risk" else "warning",
                "message": f"【{ev.event_type_label}】{ev.name} ({ev.code}): {ev.title}。{ev.summary}",
                "ts": int(datetime.now(UTC).timestamp() * 1000),
                "source": "event_center",
                "details": ev.details,
            }

            alert_store.append(data_dir, alert_event)
            body = webhook_adapter.alert_message(alert_event)
            if prefs.get("line_enabled"):
                line_token = str(prefs.get("line_channel_access_token") or "")
                line_target = str(prefs.get("line_user_id") or "")
                if line_token and line_target:
                    webhook_adapter.send_line(line_token, line_target, "【事件中心提醒】", body)
            if prefs.get("telegram_enabled"):
                tg_token = str(prefs.get("telegram_bot_token") or "")
                tg_chat = str(prefs.get("telegram_chat_id") or "")
                if tg_token and tg_chat:
                    webhook_adapter.send_telegram(tg_token, tg_chat, "【事件中心提醒】", body)
            triggered.append(alert_event)
            existing_alert_ids.add(alert_id)
            delivered_ids.add(alert_id)

        if triggered:
            _save_delivered_alert_ids(data_dir, delivered_ids)

        return triggered



_event_service_instance: TaiwanEventService | None = None


def get_event_service() -> TaiwanEventService:
    global _event_service_instance
    if _event_service_instance is None:
        _event_service_instance = TaiwanEventService()
    return _event_service_instance

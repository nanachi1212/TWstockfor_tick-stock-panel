"""A13 Buy Point API.

The route is a thin adapter around the deterministic Taiwan buy-point engine.
It reads only existing local stores and delegates alert persistence/routing to
the application's alert infrastructure.
"""
# ruff: noqa: RUF001 -- user-facing Traditional Chinese API messages.
from __future__ import annotations

import hashlib
import logging
import math
import time
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.services import alert_store, watchlist
from app.taiwan.buy_point import (
    BuyPointConditions,
    BuyPointMarketData,
    BuyPointRiskFilters,
    BuyPointSignal,
    BuyPointStrategy,
    BuyPointStrategyStore,
    _now,
    evaluate_buy_point,
    strategy_catalog,
)
from app.taiwan.realtime.calendar import taipei_today

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/taiwan/buy-points", tags=["taiwan-buy-points"])


class StrategyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = ""
    category: str = "自訂"
    conditions: BuyPointConditions = Field(default_factory=BuyPointConditions)
    risk_filters: BuyPointRiskFilters = Field(default_factory=BuyPointRiskFilters)
    alert_channels: list[str] = Field(default_factory=lambda: ["app"])
    enabled: bool = True


class StrategyUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = None
    category: str | None = None
    conditions: BuyPointConditions | None = None
    risk_filters: BuyPointRiskFilters | None = None
    alert_channels: list[str] | None = None
    enabled: bool | None = None


class CloneRequest(BaseModel):
    preset_id: str
    name: str | None = None


class AssignmentRequest(BaseModel):
    strategy_ids: list[str] = Field(default_factory=list)


class SnapshotRequest(BaseModel):
    strategy_id: str
    symbol: str
    market_context_summary: str = ""


def _store(request: Request) -> BuyPointStrategyStore:
    data_dir = request.app.state.repo.store.data_dir
    return BuyPointStrategyStore(Path(data_dir) / "user_data" / "taiwan_buy_point_strategies.json")


def _catalog_map(store: BuyPointStrategyStore) -> dict[str, BuyPointStrategy]:
    return {item.id: item for item in strategy_catalog(store)}


def _daily_data(symbol: str) -> tuple[list[dict[str, Any]], str | None]:
    from app.taiwan.daily_store import TaiwanDailyStore

    frame = TaiwanDailyStore().read_all([symbol])
    if frame.is_empty():
        return [], None
    frame = frame.sort("date").tail(90)
    rows = []
    for row in frame.iter_rows(named=True):
        item = dict(row)
        item["date"] = str(item.get("date"))[:10]
        rows.append(item)
    return rows, rows[-1].get("date") if rows else None


def _market_data(
    symbol: str,
    screen: Any = None,
    item: Any = None,
    event_service: Any = None,
    events: list[Any] | None = None,
    risk_source_status: str | None = None,
    eligible_date: str | None = None,
    *,
    load_screen: bool = True,
) -> BuyPointMarketData:
    """Build a local snapshot from existing daily/screener data.

    Screener enrichment is best-effort.  A missing field stays ``None`` so the
    evaluator can return unavailable rather than inventing a value.
    """
    rows, data_as_of = _daily_data(symbol)
    if load_screen and screen is None:
        try:
            from app.taiwan.screener import TaiwanScreenerRequest, TaiwanScreenerService

            screen = TaiwanScreenerService().run(TaiwanScreenerRequest(page_size=200, sort_by="symbol"))
            item = next((candidate for candidate in screen.items if candidate.symbol == symbol), None)
        except Exception as exc:
            logger.debug("buy-point screener enrichment unavailable for %s: %s", symbol, type(exc).__name__)

    last = rows[-1] if rows else {}
    prior_volume_rows = rows[-21:-1]
    volumes = [float(row["volume"]) for row in prior_volume_rows if row.get("volume") is not None]
    volume_average = sum(volumes) / len(volumes) if len(volumes) == 20 else None
    try:
        parsed_price = float(last["close"]) if last.get("close") is not None else None
    except (TypeError, ValueError):
        parsed_price = None
    price = parsed_price if parsed_price is not None and math.isfinite(parsed_price) else None
    ma20 = getattr(item, "ma20", None)
    price_extension = (
        (price - float(ma20)) / float(ma20) * 100
        if price is not None and ma20 is not None and float(ma20) > 0
        else None
    )
    foreign_net = getattr(item, "foreign_net", None)
    foreign_net_5d = getattr(item, "foreign_net_5d", None)
    trust_net = getattr(item, "investment_trust_net", None)
    trust_net_5d = getattr(item, "investment_trust_net_5d", None)
    institutional_day = (
        sum(float(value) for value in (foreign_net, trust_net) if value is not None)
        if foreign_net is not None or trust_net is not None
        else None
    )
    institutional_5d_average = (
        sum(float(value) for value in (foreign_net_5d, trust_net_5d) if value is not None) / 5
        if foreign_net_5d is not None or trust_net_5d is not None
        else None
    )

    matching_events: list[Any] = []
    risk: dict[str, Any] | None = None
    reference_date: date | None = None
    try:
        reference_date = date.fromisoformat(data_as_of) if data_as_of else None
    except ValueError:
        reference_date = None
    if events is not None and reference_date is not None:
        clean_symbol = symbol.strip().upper()
        clean_code = clean_symbol.split(".", 1)[0]
        matching_events = [
            event for event in events
            if (str(getattr(event, "symbol", "")).upper() == clean_symbol
                or str(getattr(event, "code", "")).upper() == clean_code)
            and str(getattr(event, "event_date", "")) <= reference_date.isoformat()
        ]
    if risk_source_status == "available" and event_service is not None and reference_date is not None:
        try:
            risk = event_service.check_symbol_risk_status(
                symbol,
                target_date=reference_date,
                events=events or [],
            )
        except Exception as exc:
            logger.debug("buy-point risk evaluation unavailable for %s: %s", symbol, type(exc).__name__)
    recent_cutoff = reference_date - timedelta(days=30) if reference_date is not None else None
    positive_event = (
        any(
            getattr(event, "severity", None) == "info"
            and recent_cutoff.isoformat() <= str(getattr(event, "event_date", ""))
            for event in matching_events
        )
        if risk_source_status == "available" and recent_cutoff is not None
        else None
    )
    capital_reduction = (
        any(
            getattr(event, "event_type", None) == "capital_reduction"
            and recent_cutoff.isoformat() <= str(getattr(event, "event_date", ""))
            for event in matching_events
        )
        if risk_source_status == "available" and recent_cutoff is not None
        else None
    )
    return BuyPointMarketData(
        symbol=symbol,
        name=getattr(item, "name", "") or "",
        data_as_of=data_as_of,
        freshness=(
            "daily_cached" if rows and data_as_of == eligible_date and price is not None
            else "stale" if rows and eligible_date is not None and data_as_of != eligible_date
            else "unavailable"
        ),
        price=price,
        quant_score=getattr(item, "quant_score", None),
        quant_universe=True if item is not None else None,
        daily=rows,
        volume=float(last["volume"]) if last.get("volume") is not None else None,
        volume_average_20d=volume_average,
        foreign_net=foreign_net,
        foreign_net_5d=foreign_net_5d,
        investment_trust_net=trust_net,
        institutional_trend=(
            [institutional_5d_average, institutional_day]
            if institutional_5d_average is not None and institutional_day is not None
            else []
        ),
        foreign_shareholding_change_20d=getattr(item, "foreign_shareholding_change_20d", None),
        revenue_yoy=getattr(item, "revenue_yoy", None),
        revenue_mom=getattr(item, "revenue_mom", None),
        eps=getattr(item, "latest_eps", None),
        pe=getattr(item, "pe", None),
        pb=getattr(item, "pb", None),
        positive_event=positive_event,
        price_extension_pct=price_extension,
        risk_data_available=(risk_source_status == "available") if risk_source_status is not None else None,
        disposition=bool(risk.get("is_disposition")) if risk is not None else None,
        suspended=bool(risk.get("is_suspended")) if risk is not None else None,
        delisted=(risk.get("risk_reason") == "終止上市") if risk is not None else None,
        capital_reduction_critical=capital_reduction,
        regulatory_unknown=(risk_source_status != "available" or risk is None),
        severe_event_risk=bool(risk.get("has_risk_event")) if risk is not None else None,
    )


def _signals_for_symbols(store: BuyPointStrategyStore, symbols: list[str]) -> list[BuyPointSignal]:
    catalog = _catalog_map(store)
    assignments = store.assignments()
    output: list[BuyPointSignal] = []
    items: dict[str, Any] = {}
    assigned_symbols = [symbol for symbol in dict.fromkeys(symbols) if assignments.get(symbol)]
    try:
        from app.taiwan.screener import TaiwanScreenerRequest, TaiwanScreenerService

        screener = TaiwanScreenerService()
        for offset in range(0, len(assigned_symbols), 200):
            scope = assigned_symbols[offset:offset + 200]
            screen = screener.run(TaiwanScreenerRequest(
                page_size=max(1, len(scope)),
                sort_by="symbol",
                symbol_scope=scope,
            ))
            items.update({candidate.symbol: candidate for candidate in screen.items})
    except Exception as exc:
        logger.debug("buy-point batch screener enrichment unavailable: %s", type(exc).__name__)
    event_service = None
    events: list[Any] | None = None
    risk_source_status: str | None = None
    try:
        from app.taiwan.events_service import get_event_service

        event_service = get_event_service()
        events, risk_source_status, _risk_as_of = event_service.get_cached_regulatory_snapshot()
    except Exception as exc:
        logger.debug("buy-point regulatory evidence unavailable: %s", type(exc).__name__)
    eligible_date: str | None = None
    try:
        from app.taiwan.daily_update import resolve_target_latest_trading_date

        eligible_date = resolve_target_latest_trading_date().isoformat()
    except Exception as exc:
        logger.debug("buy-point eligible trading date unavailable: %s", type(exc).__name__)
    for symbol in symbols:
        data = _market_data(
            symbol,
            item=items.get(symbol),
            event_service=event_service,
            events=events,
            risk_source_status=risk_source_status,
            eligible_date=eligible_date,
            load_screen=False,
        )
        for strategy_id in assignments.get(symbol, []):
            strategy = catalog.get(strategy_id)
            if strategy is not None and strategy.enabled:
                output.append(evaluate_buy_point(strategy, data))
    return output


def _dispatch_alerts(
    data_dir: Path,
    quote_service: Any,
    store: BuyPointStrategyStore,
    signals: list[BuyPointSignal],
) -> list[dict[str, Any]]:
    catalog = _catalog_map(store)
    emitted: list[dict[str, Any]] = []
    emitted_state_keys: dict[str, str] = {}
    now_ms = int(time.time() * 1000)
    for signal in signals:
        if signal.status != "triggered":
            store.set_state(f"{signal.strategy_id}:{signal.symbol}", signal.status)
            continue
        strategy = catalog[signal.strategy_id]
        state_key = f"{signal.strategy_id}:{signal.symbol}"
        previous, last_triggered = store.state_snapshot(state_key)
        cooldown_seconds = max(0, strategy.conditions.cooldown_minutes) * 60
        if previous == "triggered":
            continue
        if last_triggered is not None and (time.time() - last_triggered) < cooldown_seconds:
            continue
        transition_key = (
            f"buy-point:{signal.strategy_id}:{signal.symbol}:"
            f"after:{last_triggered!r}"
        )
        alert_id = "buy_point_" + hashlib.sha256(transition_key.encode("utf-8")).hexdigest()[:24]
        event = {
            "alert_id": alert_id,
            "ts": now_ms,
            "rule_id": f"buy_point:{signal.strategy_id}",
            "rule_name": strategy.name,
            "source": "strategy",
            "type": "buy_point",
            "symbol": signal.symbol,
            "name": signal.name,
            "message": signal.explanation,
            "price": signal.price,
            "quant_score": signal.quant_score,
            "signals": signal.triggered_conditions,
            "severity": "info",
            "notify_channels": [channel for channel in strategy.alert_channels if channel in {"line", "telegram"}],
            "dedup_key": transition_key,
            "buy_point_strategy_id": signal.strategy_id,
            "buy_point_risk_flags": signal.risk_flags,
        }
        emitted.append(event)
        emitted_state_keys[alert_id] = state_key

    if not emitted:
        return []
    inserted_ids = alert_store.append_many(data_dir, emitted)
    events_by_id = {event["alert_id"]: event for event in emitted}
    inserted = [events_by_id[alert_id] for alert_id in inserted_ids if alert_id in events_by_id]
    triggered_at = time.time()
    for state_key in emitted_state_keys.values():
        store.set_state(state_key, "triggered")
        store.mark_triggered(state_key, triggered_at)
    if not inserted:
        return []
    if quote_service is not None:
        try:
            quote_service.push_alerts(inserted)
        except Exception:
            logger.warning("買點提醒 SSE 推送失敗", exc_info=True)
        try:
            quote_service._maybe_send_webhook(inserted, None)
        except Exception:
            logger.warning("買點提醒外部推播提交失敗", exc_info=True)
        app_events = [
            event for event in inserted
            if "app" in catalog[event["buy_point_strategy_id"]].alert_channels
        ]
        if app_events:
            try:
                quote_service._maybe_send_system_notifications(app_events)
            except Exception:
                logger.debug("買點系統提醒失敗", exc_info=True)
    return inserted


def evaluate_watchlist(
    data_dir: Path,
    quote_service: Any = None,
    symbols: list[str] | None = None,
) -> dict[str, Any]:
    """Evaluate assigned watchlist strategies for both API and scheduler callers."""
    store = BuyPointStrategyStore(data_dir / "user_data" / "taiwan_buy_point_strategies.json")
    target_symbols = symbols if symbols is not None else [
        str(row.get("symbol")) for row in watchlist.list_symbols() if row.get("symbol")
    ]
    signals = _signals_for_symbols(store, target_symbols)
    emitted = _dispatch_alerts(data_dir, quote_service, store, signals)
    return {
        "signals": [signal.model_dump() for signal in signals],
        "triggered": emitted,
        "as_of": taipei_today().isoformat(),
    }


@router.get("/presets")
def list_presets(request: Request):
    return {"presets": [item.model_dump() for item in strategy_catalog(_store(request)) if item.preset]}


@router.get("/strategies")
def list_strategies(request: Request):
    store = _store(request)
    assignments = store.assignments()
    strategies = []
    for item in strategy_catalog(store):
        row = item.model_dump()
        row["assigned_symbols"] = [symbol for symbol, ids in assignments.items() if item.id in ids]
        row["assigned_count"] = len(row["assigned_symbols"])
        strategies.append(row)
    return {"strategies": strategies}


@router.post("/strategies/clone")
def clone_strategy(req: CloneRequest, request: Request):
    store = _store(request)
    preset = next((item for item in strategy_catalog(store) if item.id == req.preset_id and item.preset), None)
    if preset is None:
        raise HTTPException(404, "找不到內建買點策略")
    now = _now()
    custom = preset.model_copy(update={
        "id": f"custom_{uuid.uuid4().hex[:12]}", "name": req.name or f"我的{preset.name}",
        "preset": False, "source_preset_id": preset.id, "created_at": now, "updated_at": now,
    })
    return store.save(custom).model_dump()


@router.post("/strategies")
def create_strategy(req: StrategyCreateRequest, request: Request):
    now = _now()
    channels = [channel for channel in req.alert_channels if channel in {"app", "line", "telegram"}]
    strategy = BuyPointStrategy(
        id=f"custom_{uuid.uuid4().hex[:12]}", name=req.name.strip(), description=req.description.strip(),
        category=req.category.strip() or "自訂", enabled=req.enabled, preset=False,
        conditions=req.conditions, risk_filters=req.risk_filters, alert_channels=channels or ["app"],
        created_at=now, updated_at=now,
    )
    return _store(request).save(strategy).model_dump()


@router.put("/strategies/{strategy_id}")
def update_strategy(strategy_id: str, req: StrategyUpdateRequest, request: Request):
    store = _store(request)
    existing = _catalog_map(store).get(strategy_id)
    if existing is None:
        raise HTTPException(404, "找不到買點策略")
    if existing.preset:
        raise HTTPException(409, "內建策略請先複製後再編輯")
    updates = req.model_dump(exclude_unset=True)
    if "alert_channels" in updates:
        updates["alert_channels"] = [channel for channel in updates["alert_channels"] if channel in {"app", "line", "telegram"}]
    updates["updated_at"] = _now()
    return store.save(existing.model_copy(update=updates)).model_dump()


@router.delete("/strategies/{strategy_id}")
def delete_strategy(strategy_id: str, request: Request):
    deleted = _store(request).delete(strategy_id)
    if not deleted:
        raise HTTPException(404, "自訂買點策略不存在")
    return {"ok": True, "deleted_id": strategy_id}


@router.get("/assignments")
def get_assignments(request: Request, symbol: str | None = Query(default=None)):
    return {"assignments": _store(request).assignments(symbol)}


@router.put("/assignments/{symbol}")
def put_assignment(symbol: str, req: AssignmentRequest, request: Request):
    store = _store(request)
    known = _catalog_map(store)
    strategy_ids = list(dict.fromkeys(req.strategy_ids))
    unknown = [strategy_id for strategy_id in strategy_ids if strategy_id not in known]
    if unknown:
        raise HTTPException(400, f"未知買點策略: {', '.join(unknown)}")
    return {"symbol": symbol, "strategy_ids": store.assign(symbol, strategy_ids)}


@router.get("/signals")
def list_signals(request: Request, symbol: str | None = Query(default=None)):
    store = _store(request)
    symbols = [symbol] if symbol else [str(row.get("symbol")) for row in watchlist.list_symbols() if row.get("symbol")]
    signals = _signals_for_symbols(store, symbols)
    return {"signals": [signal.model_dump() for signal in signals], "as_of": taipei_today().isoformat()}


@router.post("/evaluate")
def evaluate_signals(request: Request, symbol: str | None = Query(default=None)):
    symbols = [symbol] if symbol else None
    return evaluate_watchlist(
        request.app.state.repo.store.data_dir,
        getattr(request.app.state, "quote_service", None),
        symbols,
    )


@router.get("/summary")
def get_summary(request: Request):
    store = _store(request)
    symbols = [str(row.get("symbol")) for row in watchlist.list_symbols() if row.get("symbol")]
    signals = _signals_for_symbols(store, symbols)
    counts = {status: sum(signal.status == status for signal in signals) for status in ("triggered", "approaching", "blocked", "waiting", "unavailable")}
    return {"counts": counts, "total": len(signals)}


@router.post("/snapshots")
def save_snapshot(req: SnapshotRequest, request: Request):
    store = _store(request)
    strategy = _catalog_map(store).get(req.strategy_id)
    if strategy is None:
        raise HTTPException(404, "找不到買點策略")
    signal = next((item for item in _signals_for_symbols(store, [req.symbol]) if item.strategy_id == req.strategy_id), None)
    if signal is None or signal.status not in {"triggered", "approaching"} or signal.price is None:
        raise HTTPException(409, "目前買點資料不足或尚未形成，無法保存快照")
    from app.taiwan.daily_update import resolve_target_latest_trading_date

    eligible_date = resolve_target_latest_trading_date().isoformat()
    if signal.data_as_of != eligible_date:
        raise HTTPException(409, "買點資料不是目前可用的最新交易日，無法保存前瞻快照")
    from app.taiwan.selection_review_models import (
        SaveSelectionSnapshotRequest,
        SelectionSnapshotItem,
    )
    from app.taiwan.selection_review_service import get_selection_review_service

    saved = get_selection_review_service().save_snapshot(SaveSelectionSnapshotRequest(
        strategy_id=f"buy_point:{strategy.id}", strategy_name=strategy.name,
        as_of_date=signal.data_as_of or taipei_today().isoformat(),
        market_context_summary=req.market_context_summary,
        source="Buy Point",
        items=[SelectionSnapshotItem(
            symbol=signal.symbol, name=signal.name, rank=1, quant_score=signal.quant_score,
            match_reasons=signal.triggered_conditions, strategy_conditions=strategy.conditions.model_dump(),
            price=signal.price, risk_status=signal.risk_status,
            event_risk_summary="、".join(signal.risk_flags) or None,
            quote_status="available",
        )],
    ))
    return saved.model_dump()


@router.get("/stats")
def get_stats():
    from app.taiwan.selection_review_service import get_selection_review_service

    return {"stats": [item.model_dump() for item in get_selection_review_service().get_strategy_reviews(source="Buy Point")], "disclaimer": "歷史訊號表現不代表未來結果。"}

"""A13 Buy Point API.

The route is a thin adapter around the deterministic Taiwan buy-point engine.
It reads only existing local stores and delegates alert persistence/routing to
the application's alert infrastructure.
"""
# ruff: noqa: RUF001 -- user-facing Traditional Chinese API messages.
from __future__ import annotations

import logging
import time
import uuid
from datetime import date
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
    volumes = [float(row["volume"]) for row in rows[-20:] if row.get("volume") is not None]
    volume_average = sum(volumes) / len(volumes) if volumes else None
    return BuyPointMarketData(
        symbol=symbol,
        name=getattr(item, "name", "") or "",
        data_as_of=data_as_of,
        freshness="daily_cached" if rows else "unavailable",
        price=float(last["close"]) if last.get("close") is not None else None,
        quant_score=getattr(item, "quant_score", None),
        quant_universe=True if item is not None else None,
        daily=rows,
        volume=float(last["volume"]) if last.get("volume") is not None else None,
        volume_average_20d=volume_average,
        foreign_net=getattr(item, "foreign_net", None),
        foreign_net_5d=getattr(item, "foreign_net_5d", None),
        investment_trust_net=getattr(item, "investment_trust_net", None),
        foreign_shareholding_change_20d=getattr(item, "foreign_shareholding_change_20d", None),
        revenue_yoy=getattr(item, "revenue_yoy", None),
        revenue_mom=getattr(item, "revenue_mom", None),
        eps=getattr(item, "latest_eps", None),
        pe=getattr(item, "pe", None),
        pb=getattr(item, "pb", None),
        risk_data_available=(getattr(screen, "risk_source_status", None) == "available") if screen is not None else None,
        regulatory_unknown=(getattr(item, "risk_status", None) == "unknown") if item is not None else None,
    )


def _signals_for_symbols(store: BuyPointStrategyStore, symbols: list[str]) -> list[BuyPointSignal]:
    catalog = _catalog_map(store)
    assignments = store.assignments()
    output: list[BuyPointSignal] = []
    screen = None
    items: dict[str, Any] = {}
    try:
        from app.taiwan.screener import TaiwanScreenerRequest, TaiwanScreenerService

        screen = TaiwanScreenerService().run(TaiwanScreenerRequest(page_size=200, sort_by="symbol"))
        items = {candidate.symbol: candidate for candidate in screen.items}
    except Exception as exc:
        logger.debug("buy-point batch screener enrichment unavailable: %s", type(exc).__name__)
    for symbol in symbols:
        data = _market_data(symbol, screen=screen, item=items.get(symbol), load_screen=False)
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
    now_ms = int(time.time() * 1000)
    for signal in signals:
        if signal.status != "triggered":
            store.set_state(f"{signal.strategy_id}:{signal.symbol}", signal.status)
            continue
        strategy = catalog[signal.strategy_id]
        state_key = f"{signal.strategy_id}:{signal.symbol}"
        previous = store.state(state_key)
        last_triggered = store.last_triggered_at(state_key)
        cooldown_seconds = max(0, strategy.conditions.cooldown_minutes) * 60
        if previous == "triggered" and (last_triggered is None or (time.time() - last_triggered) < cooldown_seconds):
            continue
        if last_triggered is not None and (time.time() - last_triggered) < cooldown_seconds:
            store.set_state(state_key, "triggered")
            continue
        event = {
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
            "dedup_key": f"buy-point:{signal.strategy_id}:{signal.symbol}:triggered",
            "buy_point_strategy_id": signal.strategy_id,
            "buy_point_risk_flags": signal.risk_flags,
        }
        emitted.append(event)
        store.set_state(state_key, "triggered")
        store.mark_triggered(state_key, time.time())

    if not emitted:
        return []
    inserted_ids = alert_store.append_many(data_dir, emitted)
    for event, alert_id in zip(emitted, inserted_ids, strict=False):
        event["alert_id"] = alert_id
    if quote_service is not None:
        try:
            quote_service.push_alerts(emitted)
        except Exception:
            logger.warning("買點提醒 SSE 推送失敗", exc_info=True)
        try:
            quote_service._maybe_send_webhook(emitted, None)
        except Exception:
            logger.warning("買點提醒外部推播提交失敗", exc_info=True)
    for event in emitted:
        if "app" in catalog[event["buy_point_strategy_id"]].alert_channels:
            try:
                from app.services.notify_adapter import notify

                notify(event["rule_name"], event["message"])
            except Exception:
                logger.debug("買點系統提醒失敗", exc_info=True)
    return emitted


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
        "as_of": date.today().isoformat(),
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
        "preset": False, "created_at": now, "updated_at": now,
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
    return {"signals": [signal.model_dump() for signal in signals], "as_of": date.today().isoformat()}


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
    from app.taiwan.selection_review_models import (
        SaveSelectionSnapshotRequest,
        SelectionSnapshotItem,
    )
    from app.taiwan.selection_review_service import get_selection_review_service

    saved = get_selection_review_service().save_snapshot(SaveSelectionSnapshotRequest(
        strategy_id=f"buy_point:{strategy.id}", strategy_name=strategy.name,
        as_of_date=signal.data_as_of or date.today().isoformat(),
        market_context_summary=req.market_context_summary,
        source="Buy Point",
        items=[SelectionSnapshotItem(
            symbol=signal.symbol, name=signal.name, rank=1, quant_score=signal.quant_score,
            match_reasons=signal.triggered_conditions, strategy_conditions=strategy.conditions.model_dump(),
            price=signal.price, risk_status="unknown" if signal.risk_flags else "clear",
            quote_status="available",
        )],
    ))
    return saved.model_dump()


@router.get("/stats")
def get_stats():
    from app.taiwan.selection_review_service import get_selection_review_service

    return {"stats": [item.model_dump() for item in get_selection_review_service().get_strategy_reviews(source="Buy Point")], "disclaimer": "歷史訊號表現不代表未來結果。"}

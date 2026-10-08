"""台股即時監控規則 API — 校驗 → 持久化 → 同步 TaiwanMonitorEngine 記憶體態。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/monitor-rules", tags=["monitor-rules"])
logger = logging.getLogger(__name__)


class TaiwanMonitorRuleCreate(BaseModel):
    rule_id: str | None = None
    name: str
    symbol: str
    rule_type: str
    threshold: float
    enabled: bool = True
    cooldown_seconds: int = 300
    hysteresis: float | None = None
    reference_volume: int | None = None
    severity: str = "warning"
    notify_channels: list[str] = []  # 'line' | 'telegram'


class TaiwanMonitorRuleUpdate(BaseModel):
    name: str | None = None
    threshold: float | None = None
    enabled: bool | None = None
    cooldown_seconds: int | None = None
    hysteresis: float | None = None
    reference_volume: int | None = None
    severity: str | None = None
    notify_channels: list[str] | None = None  # 'line' | 'telegram'


@router.get("/taiwan")
def list_taiwan_rules():
    """获取所有台股即时监控规则。"""
    from app.taiwan.realtime.monitor_engine import get_monitor_engine
    engine = get_monitor_engine()
    rules = engine.list_rules()
    return {"rules": [r.to_dict() for r in rules], "total": len(rules)}


@router.post("/taiwan")
def create_taiwan_rule(req: TaiwanMonitorRuleCreate):
    """新增台股即时监控规则 (带 Security Master 与参数合法性校验)。"""
    import uuid
    from app.taiwan.realtime.monitor_engine import get_monitor_engine
    from app.taiwan.realtime.monitor_models import TaiwanMonitorRule, TaiwanRuleType

    rule_id = req.rule_id or f"tw_rule_{uuid.uuid4().hex[:10]}"
    rule = TaiwanMonitorRule(
        rule_id=rule_id,
        name=req.name,
        symbol=req.symbol,
        rule_type=req.rule_type,
        threshold=req.threshold,
        enabled=req.enabled,
        cooldown_seconds=req.cooldown_seconds,
        hysteresis=req.hysteresis,
        reference_volume=req.reference_volume,
        severity=req.severity,
        notify_channels=[c for c in req.notify_channels if c in ("line", "telegram")],
    )
    engine = get_monitor_engine()
    if rule.rule_type == TaiwanRuleType.QUANT_TOP10_EXIT:
        try:
            engine.validate_rule(rule)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        from app.taiwan.quant.live_runner import seed_quant_exit_rule_from_latest_snapshot

        try:
            seed_quant_exit_rule_from_latest_snapshot(rule, engine)
        except OSError as e:
            raise HTTPException(status_code=503, detail="Quant 離開提醒基準儲存失敗") from e

    try:
        engine.add_rule(rule)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return {"ok": True, "rule": rule.to_dict()}


@router.post("/taiwan/sync-plans")
def sync_taiwan_plan_rules():
    """Replace Auto Watch rules from the user's Taiwan watchlist (no request body)."""
    from app.taiwan.auto_watch import sync_watchlist_plans

    try:
        return sync_watchlist_plans()
    except Exception as exc:
        logger.exception("Taiwan Auto Watch sync failed")
        raise HTTPException(status_code=503, detail="自動監控同步失敗，原有規則已保留") from exc  # noqa: RUF001


@router.patch("/taiwan/{rule_id}")
def update_taiwan_rule(rule_id: str, req: TaiwanMonitorRuleUpdate):
    """更新或启用/停用指定台股监控规则。"""
    from app.taiwan.realtime.monitor_engine import get_monitor_engine
    from app.taiwan.realtime.monitor_models import TaiwanMonitorRule, TaiwanRuleType

    engine = get_monitor_engine()
    rule = engine.get_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")
    was_enabled = rule.enabled
    updated_rule = TaiwanMonitorRule.from_dict(rule.to_dict())

    if req.name is not None:
        updated_rule.name = req.name
    if req.threshold is not None:
        updated_rule.threshold = req.threshold
    if req.enabled is not None:
        updated_rule.enabled = req.enabled
    if req.cooldown_seconds is not None:
        updated_rule.cooldown_seconds = req.cooldown_seconds
    if req.hysteresis is not None:
        updated_rule.hysteresis = req.hysteresis
    if req.reference_volume is not None:
        updated_rule.reference_volume = req.reference_volume
    if req.severity is not None:
        updated_rule.severity = req.severity
    if req.notify_channels is not None:
        updated_rule.notify_channels = [c for c in req.notify_channels if c in ("line", "telegram")]

    if (req.enabled is True and not was_enabled
            and updated_rule.rule_type == TaiwanRuleType.QUANT_TOP10_EXIT):
        try:
            engine.validate_rule(updated_rule)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        from app.taiwan.quant.live_runner import seed_quant_exit_rule_from_latest_snapshot

        try:
            seeded = seed_quant_exit_rule_from_latest_snapshot(
                updated_rule, engine, force=True,
            )
            if not seeded:
                engine.seed_quant_exit_rule(updated_rule, [], force=True)
        except OSError as e:
            raise HTTPException(status_code=503, detail="Quant 離開提醒基準儲存失敗") from e

    try:
        engine.add_rule(updated_rule)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return {"ok": True, "rule": updated_rule.to_dict()}


@router.delete("/taiwan/{rule_id}")
def delete_taiwan_rule(rule_id: str):
    """删除指定台股监控规则。"""
    from app.taiwan.realtime.monitor_engine import get_monitor_engine
    engine = get_monitor_engine()
    deleted = engine.delete_rule(rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")
    return {"ok": True, "deleted": rule_id}


@router.post("/taiwan/evaluate")
def evaluate_taiwan_rules(request: Request):
    """执行一轮台股实时规则评估, 并将告警落盘与推送到 SSE。"""
    from app.services import alert_store
    from app.taiwan.realtime.monitor_engine import get_monitor_engine

    engine = get_monitor_engine()
    def persist_events(alerts):
        repo = getattr(request.app.state, "repo", None)
        if repo is None:
            raise HTTPException(status_code=503, detail="提醒儲存尚未就緒")
        alert_store.append_many(
            repo.store.data_dir, [alert.to_dict() for alert in alerts],
        )

    alerts = engine.evaluate_all(persist_events=persist_events)
    if alerts:
        alert_dicts = [alert.to_dict() for alert in alerts]
        quote_svc = getattr(request.app.state, "quote_service", None)
        if quote_svc:
            try:
                quote_svc.push_alerts(alert_dicts)
            except Exception as e:
                logger.warning("Failed to push Taiwan alerts to SSE: %s", e)
            try:
                quote_svc._maybe_send_webhook(alert_dicts, None)
            except Exception as e:
                logger.warning("Failed to dispatch Taiwan external alerts: %s", type(e).__name__)

    return {
        "ok": True,
        "evaluated_rules": len(engine.list_rules()),
        "alerts_count": len(alerts),
        "alerts": [a.to_dict() for a in alerts],
    }


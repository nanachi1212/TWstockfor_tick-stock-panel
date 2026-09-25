"""Read-only live contract. No caller-supplied session, importer or rerank API."""
import logging
import threading
import time
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request

from app.taiwan.quant.live_contract import LiveModel
from app.taiwan.quant.live_runner import CurrentLiveSource
from app.taiwan.quant.live_store import LiveLedger

router = APIRouter(prefix="/api/taiwan/quant/live", tags=["taiwan-live"])
logger = logging.getLogger(__name__)

_SESSION_CACHE_TTL = 120.0
_SESSION_CACHE: tuple[float, str] | None = None
_SESSION_CACHE_LOCK = threading.Lock()


def _expected_session() -> str | None:
    """Bound official-session evidence reads shared by all dashboard polls."""
    global _SESSION_CACHE
    with _SESSION_CACHE_LOCK:
        now = time.monotonic()
        if _SESSION_CACHE and now - _SESSION_CACHE[0] < _SESSION_CACHE_TTL:
            return _SESSION_CACHE[1]
        source = CurrentLiveSource()
        try:
            session = LiveLedger(evidence=source.evidence).current_session().isoformat()
        except Exception:
            return None
        finally:
            source.close()
        _SESSION_CACHE = (now, session)
        return session


@router.get("/models")
def live_models():
    ledger = LiveLedger()
    model = LiveModel()
    expected_session = _expected_session()

    latest_operation = ledger.latest_operation()
    current_run = ledger.read_run(model.key, expected_session) if expected_session else None
    operation = latest_operation.get("freeze", latest_operation) if latest_operation else {}
    operation_is_current = (
        operation.get("status") in {"frozen", "noop"}
        and operation.get("session") == expected_session
    )
    audit_status = current_run.get("audit_status") if current_run else None
    valid = bool(current_run and audit_status == "ok" and operation_is_current)
    reason = (
        "current" if valid else
        "session_unavailable" if expected_session is None else
        "live_run_missing" if current_run is None else
        "audit_conflict" if audit_status != "ok" else
        "operation_not_current_success"
    )
    return {
        "configured_model": model.describe(),
        "activations": ledger.models(),
        "latest_operation": latest_operation,
        "expected_session": expected_session,
        "current_run_valid": valid,
        "current_run_audit_status": audit_status,
        "current_run_reason": reason,
    }


@router.get("/runs")
def live_runs(limit: int = Query(30, ge=1, le=100)):
    return {"runs": LiveLedger().list_runs(limit)}


@router.post("/alerts/evaluate")
def evaluate_quant_alerts(request: Request):
    """Evaluate Top 10 reminders only from the current audited Live snapshot."""
    expected_session = _expected_session()
    if expected_session is None:
        return {"ok": True, "status": "unavailable", "alerts": []}

    ledger = LiveLedger()
    model = LiveModel()
    run = ledger.read_run(model.key, expected_session)
    operation = ledger.latest_operation()
    freeze = operation.get("freeze", operation) if operation else {}
    if (
        run is None
        or run.get("audit_status") != "ok"
        or freeze.get("status") not in {"frozen", "noop"}
        or freeze.get("session") != expected_session
    ):
        return {"ok": True, "status": "unavailable", "alerts": []}

    from app.services import alert_store
    from app.taiwan.realtime.monitor_engine import get_monitor_engine

    quote_service = getattr(request.app.state, "quote_service", None)
    persisted_events = []

    def format_notifications(events):
        formatter = getattr(quote_service, "_format_extension_notifications", None)
        return formatter(events) if callable(formatter) else events

    snapshot = run.get("snapshot") or {}
    signals = snapshot.get("signals")
    if not isinstance(signals, list):
        return {"ok": True, "status": "unavailable", "alerts": []}

    def persist_events(events):
        repo = getattr(request.app.state, "repo", None)
        if repo is None:
            raise HTTPException(status_code=503, detail="提醒儲存尚未就緒")
        formatted = format_notifications(events)
        persisted_events.extend(formatted)
        return alert_store.append_many(repo.store.data_dir, formatted)

    events = get_monitor_engine().evaluate_quant_top10(
        signals,
        expected_session,
        available=True,
        persist_events=persist_events,
    )
    if events:
        output_events = persisted_events or format_notifications(events)
        if quote_service:
            try:
                quote_service.push_alerts(output_events)
            except Exception as exc:
                logger.warning("Failed to push Quant Top 10 alerts to SSE (%s)", type(exc).__name__)
            try:
                quote_service._maybe_send_webhook(output_events, None)
            except Exception as exc:
                logger.warning("Failed to dispatch Quant Top 10 external alerts (%s)", type(exc).__name__)
    return {"ok": True, "status": "available", "alerts": persisted_events or events}


@router.get("/runs/{model_key}/{session}")
def live_run(model_key: str, session: date):
    ledger = LiveLedger()
    run = ledger.read_run(model_key, session.isoformat())
    if run is None:
        raise HTTPException(status_code=404, detail="live_run_not_found")
    return {**run, "outcomes": ledger.outcomes(model_key, session.isoformat())}

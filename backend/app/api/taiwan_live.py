"""Read-only live contract. No caller-supplied session, importer or rerank API."""
import logging
import threading
import time
from datetime import date

from fastapi import APIRouter, HTTPException, Query

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
    fallback_operation_reason = operation.get("reason") or "operation_not_current_success"
    operation_projection = (
        ledger.operation_projection(latest_operation, expected_session)
        if hasattr(ledger, "operation_projection") else {
            "status": (latest_operation or {}).get("status") or "unavailable",
            "reason": fallback_operation_reason,
            "live_readiness": {"status": "unavailable", "source": "current_live_gate", "reasons": [fallback_operation_reason]},
        }
    )
    operation_is_current = (
        operation.get("status") in {"frozen", "noop"}
        and operation.get("session") == expected_session
    )
    audit_status = current_run.get("audit_status") if current_run else None
    valid = bool(current_run and audit_status == "ok" and operation_is_current)
    reason = "current" if valid else (
        "audit_conflict" if audit_status == "conflict" else operation_projection["reason"]
    )
    if expected_session is None:
        reason = "session_unavailable"
    elif current_run is None and reason == "current":
        reason = "live_run_missing"
    projection = ledger.recommendation_projection(current_run) if hasattr(ledger, "recommendation_projection") else {
        "recommendation_status": "formal_available" if valid and current_run.get("snapshot", {}).get("signals")
        else "available_zero_candidates" if valid and current_run is not None else "unavailable",
        "recommendation_reason": reason, "candidate_count": len((current_run or {}).get("snapshot", {}).get("signals", [])),
        "live_readiness": operation_projection["live_readiness"],
    }
    if not valid:
        projection["recommendation_status"] = "conflict" if audit_status == "conflict" else "unavailable"
        projection["recommendation_reason"] = reason
        projection["live_readiness"] = operation_projection["live_readiness"]
    return {
        "configured_model": model.describe(),
        "activations": ledger.models(),
        "latest_operation": latest_operation,
        "expected_session": expected_session,
        "current_run_valid": valid,
        "current_run_audit_status": audit_status,
        "current_run_reason": reason,
        **projection,
    }


@router.get("/runs")
def live_runs(limit: int = Query(30, ge=1, le=100)):
    return {"runs": LiveLedger().list_runs(limit)}


@router.get("/runs/{model_key}/{session}")
def live_run(model_key: str, session: date):
    ledger = LiveLedger()
    run = ledger.read_run(model_key, session.isoformat())
    if run is None:
        raise HTTPException(status_code=404, detail="live_run_not_found")
    outcomes = ledger.outcomes(model_key, session.isoformat())
    projection = ledger.run_projection(model_key, session.isoformat()) if hasattr(ledger, "run_projection") else {}
    return {**run, **projection, "outcomes": outcomes}

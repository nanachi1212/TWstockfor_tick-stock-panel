"""Read-only live contract. No caller-supplied session, importer or rerank API."""
from datetime import date

from fastapi import APIRouter, HTTPException, Query

from app.taiwan.quant.live_contract import LiveModel
from app.taiwan.quant.live_runner import CurrentLiveSource
from app.taiwan.quant.live_store import LiveLedger

router = APIRouter(prefix="/api/taiwan/quant/live", tags=["taiwan-live"])


@router.get("/models")
def live_models():
    source = CurrentLiveSource()
    ledger = LiveLedger(evidence=source.evidence)
    model = LiveModel()
    try:
        expected_session = ledger.current_session().isoformat()
    except Exception:
        expected_session = None
    finally:
        source.close()

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


@router.get("/runs/{model_key}/{session}")
def live_run(model_key: str, session: date):
    ledger = LiveLedger()
    run = ledger.read_run(model_key, session.isoformat())
    if run is None:
        raise HTTPException(status_code=404, detail="live_run_not_found")
    return {**run, "outcomes": ledger.outcomes(model_key, session.isoformat())}

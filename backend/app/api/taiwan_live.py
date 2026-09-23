"""Read-only live contract. No caller-supplied session, importer or rerank API."""
from datetime import date

from fastapi import APIRouter, HTTPException, Query

from app.taiwan.quant.live_contract import LiveModel
from app.taiwan.quant.live_store import LiveLedger

router = APIRouter(prefix="/api/taiwan/quant/live", tags=["taiwan-live"])


@router.get("/models")
def live_models():
    ledger = LiveLedger()
    return {"configured_model": LiveModel().describe(), "activations": ledger.models(),
            "latest_operation": ledger.latest_operation()}


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

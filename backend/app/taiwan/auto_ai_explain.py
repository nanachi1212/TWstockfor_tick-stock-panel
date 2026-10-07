"""After-close AI explanations for the top radar picks, stored in the research history."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.services import preferences
from app.services.ai_provider import snapshot_ai_provider_config
from app.taiwan.ai_research import RESEARCH_PROMPT_VERSION, TaiwanAIResearchService
from app.taiwan.ai_research_history import get_ai_research_history_store
from app.taiwan.beginner_selection import BeginnerSelectionService, selection_evidence
from app.taiwan.realtime.calendar import taipei_now

logger = logging.getLogger(__name__)

AUTO_EXPLAIN_LIMIT = 5
_PREF_ENABLED = "auto_ai_explain_enabled"
_PREF_LAST_RUN = "auto_ai_explain_last_run"


def is_enabled() -> bool:
    return bool(preferences.load().get(_PREF_ENABLED, False))


def set_enabled(enabled: bool) -> bool:
    preferences.save({_PREF_ENABLED: bool(enabled)})
    return is_enabled()


def last_run() -> dict[str, Any] | None:
    value = preferences.load().get(_PREF_LAST_RUN)
    return value if isinstance(value, dict) else None


def stored_explanation(symbol: str, min_as_of: str | None) -> dict[str, Any] | None:
    """Latest successful research for the symbol that is reusable today.

    Reusable = same prompt version and current model, and evidence not older than
    min_as_of, so an old or other-model result is never shown as today's explanation.
    """
    model = snapshot_ai_provider_config().model
    for record in get_ai_research_history_store().list(symbol=symbol, purpose="research", limit=20):
        response = record.get("response")
        if not isinstance(response, dict) or response.get("status") != "success":
            continue
        if (record.get("prompt_versions") or {}).get("research") != RESEARCH_PROMPT_VERSION:
            continue
        if record.get("model") != model:
            continue
        if min_as_of and str(record.get("evidence_as_of") or "") < min_as_of:
            continue
        return response
    return None


async def _explain(symbol: str) -> str:
    """Generate and persist one report; returns 'generated' or the failure code."""
    selection = await asyncio.to_thread(selection_evidence, symbol)
    run = await TaiwanAIResearchService().generate_run(symbol, selection_evidence=selection)
    if run.response.status != "success":
        return run.response.error_code or run.response.status
    get_ai_research_history_store().ensure_report(run)
    return "generated"


def run_auto_explain(limit: int = AUTO_EXPLAIN_LIMIT) -> dict[str, Any]:
    """Explain the top picks that have no stored explanation for their current data date."""
    if not is_enabled():
        return {"status": "disabled"}
    result: dict[str, Any] = {
        "status": "ok", "ran_at": taipei_now().isoformat(),
        "generated": [], "skipped": [], "failed": {},
    }
    try:
        selection = BeginnerSelectionService().build(limit=limit)
        if not selection.candidates:  # a degraded selection with candidates is what the radar shows too
            result.update(status="no_candidates", reason=f"selection status: {selection.status}")
        for candidate in selection.candidates[:limit]:
            if stored_explanation(candidate.symbol, candidate.as_of) is not None:
                result["skipped"].append(candidate.symbol)
                continue
            try:
                outcome = asyncio.run(_explain(candidate.symbol))
            except Exception as exc:  # one failing symbol must not stop the rest
                logger.exception("auto AI explain failed for %s", candidate.symbol)
                outcome = type(exc).__name__
            if outcome == "generated":
                result["generated"].append(candidate.symbol)
            else:
                result["failed"][candidate.symbol] = outcome
    except Exception as exc:
        logger.exception("auto AI explain run failed")
        result.update(status="error", reason=type(exc).__name__)
    preferences.save({_PREF_LAST_RUN: result})
    return result

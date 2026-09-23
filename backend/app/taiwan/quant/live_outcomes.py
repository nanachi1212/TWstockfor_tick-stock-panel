"""Realized labels are appended independently; signal bytes are never changed."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from typing import Any

import polars as pl

from app.taiwan.adjust import forward_adjusted_return
from app.taiwan.providers.corporate_actions import SOURCE_URLS
from app.taiwan.quant.live_contract import HORIZONS
from app.taiwan.quant.live_store import LiveConflictError, LiveLedger


def mature_live_outcomes(ledger: LiveLedger, source, *, recheck_verified: bool = False) -> dict[str, Any]:
    latest = ledger.current_session()
    stats = {"status": "success", "appended": 0, "noop": 0, "conflict": 0}
    for run in ledger.outcome_runs():
        start = date.fromisoformat(run["session"])
        if start >= latest:
            continue
        previous = {(row["symbol"], row["horizon"]): row["status"] for row in
                    ledger.signal_outcomes(run["model_key"], run["session"], run["signals"])}
        for signal in run["signals"]:
            symbol = signal["symbol"]
            horizons = [h for h in HORIZONS if recheck_verified or
                        previous[(symbol, h)] not in ("verified", "conflict")]
            if not horizons:
                continue
            exchange = symbol.split(".")[1]
            sessions = []
            cursor = start + timedelta(days=1)
            unresolved = False
            while cursor <= latest and len(sessions) < max(horizons):
                evidence = source.evidence(cursor, exchange)
                if evidence.status == "unresolved":
                    unresolved = True
                    break
                if evidence.status == "trading":
                    sessions.append(cursor)
                cursor += timedelta(days=1)
            for horizon in horizons:
                if len(sessions) < horizon:
                    outcome = {"status": "pending", "value": None, "end_session": None,
                               "reason": "session_evidence_unresolved" if unresolved else "horizon_not_mature"}
                else:
                    days = sessions[:horizon]
                    end = days[-1]
                    future = source.outcome_prices(symbol, days)
                    if (future["date"].sort().to_list() != days
                            or future["symbol"].unique().to_list() != [symbol]):
                        outcome = {"status": "data_insufficient", "value": None,
                                   "end_session": end, "reason": "missing_session_price"}
                    else:
                        events, coverage = source.actions(start, end)
                        if (coverage.get("status") != "verified" or coverage["start"] > start
                                or coverage["end"] < end
                                or set(coverage.get("sources", ())) != set(SOURCE_URLS)):
                            outcome = {"status": "data_insufficient", "value": None,
                                       "end_session": end, "reason": "corporate_action_coverage_unavailable"}
                        else:
                            entry = pl.DataFrame({"symbol": [symbol], "date": [start],
                                                  "close": [float(signal["reference_close"])]})
                            prices = pl.concat([entry, future], how="vertical_relaxed")
                            realized = forward_adjusted_return(
                                prices, start_session=start, horizon_sessions=horizon, events=events)
                            outcome = {**asdict(realized),
                                       "price_semantics": "pit_price_normalized_close_return_not_total_return"}
                try:
                    status = ledger.observe_outcome(run["model_key"], run["session"], symbol, horizon, outcome)
                except LiveConflictError:
                    status = "conflict"
                stats[status] += 1
    if stats["conflict"]:
        stats["status"] = "conflict"
    return stats

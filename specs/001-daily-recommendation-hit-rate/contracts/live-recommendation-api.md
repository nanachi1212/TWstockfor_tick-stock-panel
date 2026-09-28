# Live Recommendation API Contract

These are additive extensions to the existing `/api/taiwan/quant/live` read APIs. The API is read-only for the frontend; the scheduler remains the only normal writer.

## `GET /api/taiwan/quant/live/models`

Keep all existing fields and add:

```json
{
  "recommendation_status": "formal_available | available_zero_candidates | unavailable | conflict",
  "recommendation_reason": "current | live_run_missing | daily_refresh_not_ready | ...",
  "candidate_count": 10,
  "live_readiness": {
    "status": "verified | unavailable",
    "source": "current_live_gate",
    "reasons": []
  }
}
```

`available_zero_candidates` requires an audited current run whose frozen `signals` array is empty. A missing run or a non-success latest operation is `unavailable`, not zero candidates.

## `GET /api/taiwan/quant/live/runs?limit=30`

Keep existing summary fields and add:

```json
{
  "model_key": "tw-eod-momentum",
  "session": "2026-09-28",
  "snapshot_hash": "...",
  "frozen_at": "2026-09-28T16:31:00+08:00",
  "signal_count": 10,
  "audit_status": "ok | conflict",
  "recommendation_status": "formal_available | available_zero_candidates | tracking | conflict",
  "horizons": {
    "1D": {"evaluated_count": 10, "pending_count": 0, "unavailable_count": 0, "hit_count": 6, "hit_rate_pct": 60.0, "average_return_pct": 0.42},
    "5D": {"evaluated_count": 4, "pending_count": 6, "unavailable_count": 0, "hit_count": 3, "hit_rate_pct": 75.0, "average_return_pct": 1.18},
    "20D": {"evaluated_count": 0, "pending_count": 10, "unavailable_count": 0, "hit_count": 0, "hit_rate_pct": null, "average_return_pct": null}
  }
}
```

The endpoint derives these fields from the frozen run and immutable outcome observations. It must not call an external source or rerun ranking.

## `GET /api/taiwan/quant/live/runs/{model_key}/{session}`

Keep the existing run payload and add:

- `recommendation_status`
- `live_readiness`
- `outcome_summary` keyed by `1D`, `5D`, `20D`
- each `outcomes[]` row may expose `name`, `reference_close`, `rank`, `score`, and `reason_summary` by joining only to the frozen signal row, not current quote data.

The existing `snapshot` remains available for reproducibility. `snapshot.signals[]` is the source of candidate fields; `outcomes[]` is the source of period results.

## Error and status semantics

- `404 live_run_not_found` remains for an absent formal run.
- Current dashboard state uses `/models` to display unavailable reasons when there is no formal run.
- Pending outcomes have no return value and are not sent as zero.
- Unavailable/conflict outcomes retain reason/audit fields and are excluded from hit-rate denominator.
- No endpoint accepts a caller-supplied date to create or overwrite a run.

## Frontend mapping

- `TodaySelection` reads `/models`, `/runs`, and the current `/runs/{model_key}/{session}` detail。
- `SelectionReview` reads `/runs` for history and the same detail endpoint for a selected day。
- Both screens display the same `recommendation_status`, frozen candidate fields, outcome statuses, and horizon summaries。

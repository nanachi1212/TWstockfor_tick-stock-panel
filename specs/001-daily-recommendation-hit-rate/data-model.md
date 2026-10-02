# Data Model: 台股每日推薦與命中率初測

本文件描述實作需重用的既有資料模型與最小新增投影欄位。每日 live recommendation 的權威資料仍是 `LiveLedger`，不是 Selection Review JSON。

## Existing authoritative entities

### Daily recommendation attempt

來源：`LiveLedger.operations` 的 append-only payload。

| Field | Meaning |
|---|---|
| `status` | `frozen`、`noop`、`skipped`、`blocked` 或 `conflict` |
| `session` | 台灣實際交易日，成功 freeze 必須是 current completed session |
| `reason` | unavailable/blocked/skipped 的 machine-readable cause |
| `freeze` | `run_live_cycle()` 中的 freeze result；outcome maturation/alerts 狀態另存同一 operation payload |
| `recorded_at` | operation append time |

不可用 attempt 只代表當次嘗試，不是 formal recommendation，不能被列入 snapshot/hit rate。

### Daily recommendation snapshot

來源：`LiveLedger.runs.snapshot`，identity 是 `(model_key, session)`，digest 是 immutable identity。

現有且必須保留：

- `contract`, `model`, `signal_session`, `data_cutoff`, `feature_price_anchor`
- `verified_universe`, `eligible_universe`, `ranking_universe`, `ranking_exclusions`
- `session_evidence`, `features`, `factor_coverage`, `feature_snapshot_hash`
- `ranking`, `signals`, `raw_history_hash`, `raw_history_source`
- `corporate_action_coverage`, `corporate_actions`, `regime`
- `usage_scope`, `validation_state`, `horizons`

第一階段最小新增或標準化：

| Field | Location | Rule |
|---|---|---|
| `name` | each `signals[]` row | freeze 當下 security master name；不得以後續 master 回填 |
| `reason_codes` | each `signals[]` row | deterministic codes derived from existing feature percentiles and selection policy |
| `reason_summary` | each `signals[]` row | stable human-readable summary generated from codes and frozen values；not AI-authored |
| `live_readiness` | snapshot root | status/source/reasons for the existing current-live gate；formal snapshot must be `verified` |

`symbol`、`reference_close`、`rank`、`score`、`feature_percentiles`、`selected` 已存在，仍保持 immutable。`signal_session` 是推薦/資料日期，`data_cutoff` 是 provenance cutoff。

### Recommendation candidate

Canonical source: `snapshot.signals[]`。

Required UI/API fields:

```json
{
  "symbol": "2330.TWSE",
  "name": "台積電",
  "signal_session": "2026-09-28",
  "reference_close": 1234.0,
  "rank": 1,
  "score": 0.93,
  "feature_percentiles": {"momentum_5d": 0.81, "momentum_20d": 0.94, "momentum_60d": 0.72},
  "reason_codes": ["momentum_20d_percentile_ge_0_7", "momentum_20d_positive", "ranked_top_10"],
  "reason_summary": "20D 動能排名前段且為正，進入既有 Top 10。"
}
```

The example is illustrative; implementation must use existing rank/score and policy values, not hard-code the example.

### Period outcome

Canonical source: `LiveLedger.observations` projected by `signal_outcomes()`。

| Field | Rule |
|---|---|
| `symbol`, `horizon` | frozen signal identity and one of 1/5/20 |
| `status` | `pending`, `verified`, `data_insufficient`, or `conflict` |
| `end_session` | exact H-th verified actual trading session after recommendation date |
| `value` | finite price-normalized close return, only for `verified` |
| `price_semantics` | existing `pit_price_normalized_close_return_not_total_return` |
| `reason` | maturity, missing price, coverage or audit explanation |
| `observations` | append-only audit observations; never replaced |

### Horizon summary

Read-only projection, not a persisted authority:

```text
HorizonSummary {
  horizon: 1 | 5 | 20
  evaluated_count: integer
  pending_count: integer
  unavailable_count: integer
  hit_count: integer
  hit_rate_pct: number | null
  average_return_pct: number | null
}
```

`hit_rate_pct` is null when `evaluated_count == 0`. `unavailable_count` includes data-insufficient/conflict outcomes for display, but neither it nor pending enters the denominator.

## Status projection

| Status | Formal snapshot? | Candidate/hit-rate use |
|---|---:|---|
| `formal_available` | yes | signals and matured outcomes are valid |
| `available_zero_candidates` | yes | valid snapshot, zero signals, no hit-rate candidates |
| `unavailable` | no | do not show as formal recommendation or sample |
| `conflict` | no for the conflicting attempt | original audited snapshot remains visible with conflict warning |
| `tracking` | yes | snapshot valid; one or more horizons pending |

`tracking` is a view state of a formal snapshot, not a new persistence state。

## Compatibility rules

- Old frozen runs may not contain `name`, `reason_codes`, `reason_summary` or `live_readiness`。它們仍可讀；UI 顯示未保存欄位，不 refetch/修改。
- New API fields are additive。Existing `TodaySelection` consumers continue reading `snapshot.signals`, `reference_close`, `rank`, `score` and `feature_percentiles`。
- No existing `selection_snapshots.json` record is converted or duplicated。

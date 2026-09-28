# Implementation Plan: 台股每日推薦與命中率初測

**Branch**: `chore/spec-kit-setup` | **Date**: 2026-09-28 | **Spec**: [spec.md](spec.md)

**Input**: Existing feature specification and repository architecture review.

## Summary

本功能沿用目前 `quant/live_runner.py` 的 current live Quant 作為每日唯一選取權威，沿用 `LiveLedger` 的 append-only SQLite 快照與 outcome ledger，並把既有 `1D/5D/20D` maturation 結果接到 Dashboard 與 Selection Review。第一階段不建立新的選股器、第二份推薦資料庫或新的績效計算框架。

實作重點是三個窄幅串接：補齊 frozen signal 的名稱與客觀原因摘要，將既有 live gate 的結果整理成可見的正式／0 檔／不可用狀態，並在既有 live API 與前端畫面顯示逐檔 outcome 和按期間聚合的命中率。不可用嘗試只保留在既有 `operations` audit log，不建立正式推薦快照；正式快照與後續 outcome 仍由 `LiveLedger` 的不可變資料負責。

## Technical Context

**Language/Version**: Backend Python 3.11+；FastAPI、Pydantic、Polars；Frontend TypeScript/React/Vite。

**Primary Dependencies**: 既有 `TaiwanDailyStore`、`TaiwanTradingCalendar`、`ObservedUniverseCensus`、`CorporateActionProvider`、`build_factor_panel`、`apply_policy_filters`、`LiveModel`、`LiveLedger`、TanStack Query 與既有 Dashboard/Selection Review 元件。

**Storage**: 既有 `<data>/taiwan/live_quant/signals.sqlite3` 與 `outcomes.sqlite3`。推薦快照和 outcome 繼續使用既有 append-only 表與 immutable triggers；不新增資料庫、JSON snapshot authority 或 migration。

**Testing**: Backend pytest，沿用 `test_taiwan_live_*`、`test_taiwan_quant_*`、`test_taiwan_selection_*`；Frontend 既有 Vitest/Testing Library 測試，包括 `TodaySelection.test.tsx`、`Dashboard.test.tsx`、`SelectionReview.test.tsx`。

**Target Platform**: 既有 Windows/Linux 可執行的 FastAPI backend 與瀏覽器 frontend；市場時間使用 `Asia/Taipei`。

**Project Type**: 既有 web application，包含 backend API/job 與 frontend dashboard/review UI。

**Performance Goals**: 不增加每日推薦資料源請求或重算流程；單日詳情只讀一份 frozen run 與其最多 10 檔、3 個 horizon 的 outcome。Dashboard 每分鐘既有 polling 應維持可用，歷史清單限制沿用現有最多 100 筆。

**Constraints**: 推薦執行固定為實際交易日的台灣時間 16:30，必須在既有 daily refresh 成功後才進入 live freeze；只用 cutoff 以前的 point-in-time 資料；Top 10 是上限，不保證 10 檔；命中為未四捨五入價格標準化報酬率 `> 0`；不啟用自動下單或 AI 績效判定。

**Scale/Scope**: 每交易日一個 `tw-eod-momentum` live run，最多 10 檔，固定 horizons `{1,5,20}`；第一階段只提供當日摘要、歷史 live run 清單、單日明細和最小聚合統計。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Result |
|---|---|---|
| User-visible E2E first | Dashboard 顯示當日狀態/候選/結果，Selection Review 顯示歷史 run 與 1D/5D/20D，不先做後台管理頁。 | PASS |
| Minimal reuse, no parallel systems | `LiveLedger` 是推薦快照與 outcome 的唯一 authority；Selection Review 只讀 live API，不把每日推薦再寫入既有 JSON snapshot store。 | PASS |
| Proportional tests | 以既有 live ledger/runner/scheduler/API 測試補 immutable、gate、0 檔與 outcome 聚合；只補受影響的前端元件測試。 | PASS |
| Taiwan PIT/reproducibility | 使用既有 `data_cutoff`、official current evidence、factor/action coverage、actual trading-day horizon 與 frozen reference close。 | PASS |
| Compatibility/failure visible | API 新增欄位保持向後相容；blocked、skipped、conflict、pending、unavailable 以狀態和原因呈現，不轉成 0。 | PASS |
| DataHealth ownership | 現有 `quant_evaluation_readiness()` 是 Primary OOS/A2b gate，不適合作為 current live 每日 gate；計畫不錯誤套用它，改以既有 live gate 統一輸出 readiness 狀態。 | PASS WITH DOCUMENTED CONFLICT |

## Project Structure

### Documentation (this feature)

```text
specs/001-daily-recommendation-hit-rate/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/live-recommendation-api.md
└── tasks.md                 # 由 $speckit-tasks 產生，尚未建立
```

### Source Code (repository root)
```text
backend/app/taiwan/quant/
├── live_contract.py
├── live_runner.py
├── live_store.py
├── live_outcomes.py
└── data_health.py

backend/app/api/taiwan_live.py
backend/app/jobs/daily_pipeline.py

frontend/src/
├── lib/api.ts
├── lib/queryKeys.ts
├── components/quant/TodaySelection.tsx
├── components/selection/LiveRecommendationReview.tsx
├── pages/Dashboard.tsx
└── pages/SelectionReview.tsx

backend/tests/
├── test_taiwan_live_runner.py
├── test_taiwan_live_ledger.py
├── test_taiwan_live_api.py
└── test_taiwan_live_scheduler.py
```

**Structure Decision**: 選擇既有 backend/frontend 分層。推薦產生與評估留在 `quant`，API 只做薄的 read projection，Dashboard 與 Selection Review 共用相同 live API。新增的 `LiveRecommendationReview` 只是既有 Selection Review 的呈現元件，不擁有資料或重新計算排名。

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | 不新增專案、資料庫、migration、平行選股或第二份 snapshot authority。 | N/A |

## Implementation Phases

### Phase 0: research and boundary decisions

已完成，詳見 [research.md](research.md)。研究結果確認 16:30 scheduler、current live runner、LiveLedger、live outcome maturation、Selection Review 既有命中率語意與 DataHealth 的 ownership 邊界。

### Phase 1: design artifacts

已完成，詳見 [data-model.md](data-model.md)、[contracts/live-recommendation-api.md](contracts/live-recommendation-api.md) 與 [quickstart.md](quickstart.md)。這些文件已把實作切成可直接轉換成 `$speckit-tasks` 的 backend、API、frontend、test 工作項目。

### Phase 2: implementation sequencing for `$speckit-tasks`

1. 在 live signal payload 中從 frozen security master 保存 `name`，並以既有 `feature_percentiles`、選取條件和模型 metadata 產生 deterministic `reason_codes`/`reason_summary`；保持既有排序、門檻和 Top 10 不變。
2. 在 current live gate 邊界建立結構化 readiness/status projection，保留 `daily_refresh_not_ready`、current source、factor warmup、trading evidence、corporate-action coverage、audit conflict 等既有阻擋原因；不得呼叫 Primary OOS gate 作為每日 live gate。
3. 在 `LiveLedger` 的 read path 增加單日與清單所需的 outcome summary projection，從 immutable observations 計算每個 horizon 的 evaluated/pending/unavailable/hit/average；不新增表、不改寫 observation。
4. 擴充既有 `/api/taiwan/quant/live/models`、`/runs`、`/runs/{model_key}/{session}` 回應欄位，提供 formal available、available zero candidates、unavailable、conflict，以及 1D/5D/20D 統計；維持既有欄位相容。
5. 在 `TodaySelection` 加入當日推薦狀態與 1D/5D/20D 摘要，沿用 snapshot name/reason/reference close，不以即時 quote 覆寫 snapshot 欄位。
6. 在 `SelectionReview` 增加 live recommendation history/detail 區塊，讀同一份 live API；顯示快照 metadata、逐檔 outcome 與命中率分母，保留既有 research/forward batch 流程不變。
7. 補 backend/frontend targeted tests，涵蓋 immutable retry/conflict、zero vs unavailable、PIT cutoff/name/reason preservation、actual trading-day horizons、hit rate denominator、API status 與兩個 UI 入口。

## Schema / Migration Decision

不需要 schema migration。現有 SQLite tables 已具備 `runs`、`operations`、`observations`、`evaluations`、immutable triggers 與 digest/conflict 設計；新增 name/reason/readiness 是既有 JSON snapshot/API payload 的向後相容欄位。實作時仍需對舊 run 使用缺省值顯示「既有快照未保存此欄位」，不得回頭用最新 master 或行情補寫舊 snapshot。若 repository 的 migration policy 要求明確版本檢查，僅新增讀取相容測試，不建立新 migration。

## Final Constitution Re-check

Phase 1 設計仍符合最小完整解法：16:30 job、Quant 選取、DataHealth/live gate、immutable ledger、outcome maturation 和兩個既有 UI 入口各只有一份權威。沒有新增策略、Primary OOS、portfolio framework、交易執行或獨立 snapshot store。唯一需在 implementation 期間保持的邊界是：`DataHealth` 的 Primary OOS readiness 不得被誤當成每日 current live readiness。

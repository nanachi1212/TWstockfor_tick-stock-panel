---
description: "Implementation tasks for daily recommendation and hit-rate MVP"
---

# Tasks: 台股每日推薦與命中率初測

**Input**: Design documents from `/specs/001-daily-recommendation-hit-rate/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/live-recommendation-api.md`

**Implementation rule**: Reuse the current live Quant runner and `LiveLedger`; do not add a second selector, snapshot store, migration, scheduler, or performance framework.

## Phase 1: Setup

**Purpose**: Establish the existing-code baseline before feature changes.

- [X] T001 Record the current live Quant API, ledger, scheduler, Dashboard, and Selection Review touch-points in `specs/001-daily-recommendation-hit-rate/quickstart.md` without adding a new runtime dependency
- [X] T002 [P] Add feature test fixtures for frozen signals, readiness states, and 1D/5D/20D outcomes in `backend/tests/test_taiwan_live_ledger.py`, `backend/tests/test_taiwan_live_runner.py`, `backend/tests/test_taiwan_live_outcomes.py`, and `frontend/src/components/quant/TodaySelection.test.tsx`

## Phase 2: Foundational

**Purpose**: Define additive, backward-compatible projections shared by all user stories.

- [X] T003 [P] Add typed live recommendation status, horizon summary, frozen signal metadata, and outcome fields to `frontend/src/lib/api.ts`
- [X] T004 [P] Add a shared deterministic reason-code/summary helper using frozen feature values and existing policy metadata in `backend/app/taiwan/quant/live_contract.py`
- [X] T005 Add a read-only live snapshot projection helper for status and horizon summaries in `backend/app/taiwan/quant/live_store.py`, preserving old snapshots and excluding pending/unavailable values from hit-rate denominators

## Phase 3: User Story 1 - 查看今日正式推薦 (Priority: P1) 🎯 MVP

**Goal**: Show the latest audited current-live Top 10 snapshot, including immutable candidate identity and a clear available/zero/unavailable state.

**Independent Test**: Given a frozen run, an audited zero-signal run, a missing run, and a conflicting run, the Dashboard shows the matching status and at most ten snapshot candidates without substituting current quotes for frozen fields.

### Tests for User Story 1

- [X] T006 [P] [US1] Add runner tests proving frozen signal `name`, deterministic `reason_codes`, and `reason_summary` are captured at freeze time in `backend/tests/test_taiwan_live_runner.py`
- [X] T007 [P] [US1] Add scheduler/API tests for the Asia/Taipei 16:30 weekday trigger, daily-refresh-success handoff, actual-session selection, and formal/available-zero/unavailable/conflict status projections in `backend/tests/test_taiwan_live_scheduler.py` and `backend/tests/test_taiwan_live_api.py`
- [X] T008 [P] [US1] Add Dashboard/TodaySelection tests for frozen name, reference close, rank, score, reason, and zero/unavailable states in `frontend/src/components/quant/TodaySelection.test.tsx` and `frontend/src/pages/Dashboard.test.tsx`

### Implementation for User Story 1

- [X] T009 [US1] Preserve security-master names and deterministic reason metadata in each frozen signal produced by `backend/app/taiwan/quant/live_runner.py`, without changing ranking, thresholds, or Top 10 selection
- [X] T010 [US1] Wire the existing `backend/app/jobs/daily_pipeline.py` and `backend/app/taiwan/quant/live_runner.py` orchestration so the Asia/Taipei 16:30 Monday-Friday job runs only after successful daily refresh, resolves an actual completed trading session, records unavailable attempts, and keeps same-session reruns idempotent without adding a second scheduler
- [X] T011 [US1] Extend `GET /api/taiwan/quant/live/models` and `GET /api/taiwan/quant/live/runs` in `backend/app/api/taiwan_live.py`, then update `frontend/src/components/quant/TodaySelection.tsx` to render status, frozen candidate name/reference close/rank/score/reason, recommendation timestamp/data date, and experimental disclaimer while retaining current quote display as a separate market field

**Checkpoint**: Dashboard can independently display formal Top 10, valid zero candidates, or explicit unavailable status from one immutable live snapshot.

## Phase 4: User Story 2 - 追蹤推薦後的客觀結果 (Priority: P1)

**Goal**: Display exact actual-trading-day 1D/5D/20D outcomes and objective hit-rate summaries from immutable observations.

**Independent Test**: Fixed fixtures cover pending, verified positive/zero/negative, data-insufficient, and conflict outcomes; summaries show correct evaluated/pending/unavailable counts, null with no samples, strict-positive hit rate, and average return.

### Tests for User Story 2

- [X] T012 [P] [US2] Add outcome tests for actual-trading-day endpoints, recommendation-close reference pricing, pending/unavailable states, strict-positive hit counting, no-sample null summaries, and identical-rerun deduplication in `backend/tests/test_taiwan_live_outcomes.py` and `backend/tests/test_taiwan_live_ledger.py`
- [X] T013 [P] [US2] Add API contract tests for per-run `outcome_summary` and list-run horizon summaries in `backend/tests/test_taiwan_live_api.py`
- [X] T014 [P] [US2] Add Selection Review UI tests for 1D/5D/20D evaluated/pending/unavailable counts, hit rate denominator, average return, and tracking labels in `frontend/src/pages/SelectionReview.test.tsx`

### Implementation for User Story 2

- [X] T015 [US2] Integrate existing `backend/app/taiwan/quant/live_outcomes.py` maturity results with read-only horizon summaries and frozen signal metadata in `backend/app/taiwan/quant/live_store.py`, preserving actual-trading-day 1D/5D/20D rules, append-only observations, and identical-rerun no-op behavior
- [X] T016 [US2] Expose run-level `outcome_summary`, status, and frozen candidate outcome fields from `backend/app/api/taiwan_live.py` without external requests or ranking reruns; retain the existing `limit`-based history list and do not introduce a new pagination/date-query framework
- [X] T017 [US2] Extend `frontend/src/pages/SelectionReview.tsx` with live recommendation history/detail panels that use the same live API, show 1D/5D/20D outcome states and summaries, and never render pending/unavailable as zero

**Checkpoint**: Selection Review can independently validate a mature, tracking, or unavailable horizon sample from saved observations.

## Phase 5: User Story 3 - 確認推薦是否具備正式資格 (Priority: P1)

**Goal**: Make current-live readiness and failure reasons explicit and fail closed.

**Independent Test**: Refresh-not-ready, unresolved trading evidence, missing current run, stale operation, and conflict fixtures do not create or display a formal recommendation.

### Tests for User Story 3

- [X] T018 [P] [US3] Add fail-closed readiness and operation-state tests for `daily_refresh_not_ready`, session evidence failure, missing run, and conflict in `backend/tests/test_taiwan_live_api.py` and `backend/tests/test_taiwan_live_scheduler.py`
- [X] T019 [P] [US3] Add UI tests ensuring unavailable, conflict, and blocked reasons are visible and never shown as formal zero candidates in `frontend/src/components/quant/TodaySelection.test.tsx` and `frontend/src/pages/SelectionReview.test.tsx`

### Implementation for User Story 3

- [X] T020 [US3] Build the additive current-live readiness projection from existing scheduler/runner gate evidence in `backend/app/taiwan/quant/data_health.py`, `backend/app/taiwan/quant/live_runner.py`, and `backend/app/api/taiwan_live.py`; reuse current market, factor, trading-day, corporate-action, and session-completeness results without adding numeric thresholds or calling Primary OOS readiness for the daily gate
- [X] T021 [US3] Preserve blocked and skipped attempts only as auditable operations and map them to explicit API/UI reasons in `backend/app/taiwan/quant/live_store.py` and `frontend/src/components/quant/TodaySelection.tsx`

## Phase 6: User Story 4 - 重現當時的選擇理由 (Priority: P2)

**Goal**: Let users inspect a historical frozen run and reproduce its original decision context.

**Independent Test**: Refreshing current master/quotes after a saved run does not alter its saved name, reference close, rank, score, reasons, cutoff, version, or outcome status.

### Tests for User Story 4

- [X] T022 [P] [US4] Add immutable historical detail and backward-compatible old-snapshot tests in `backend/tests/test_taiwan_live_ledger.py` and `backend/tests/test_taiwan_live_api.py`
- [X] T023 [P] [US4] Add Selection Review regression tests proving historical rows use saved metadata and link to the existing stock/research flow in `frontend/src/pages/SelectionReview.test.tsx`

### Implementation for User Story 4

- [X] T024 [US4] Return additive frozen model/version, cutoff, readiness, signal reason, and data provenance fields from `backend/app/api/taiwan_live.py` using only the stored snapshot
- [X] T025 [US4] Add the reusable `frontend/src/components/selection/LiveRecommendationReview.tsx` presentation component and integrate it into `frontend/src/pages/SelectionReview.tsx` without changing existing research or forward-batch authority
- [X] T026 [US4] Update `frontend/src/lib/queryKeys.ts` and shared live API types/queries so Dashboard and Selection Review read the same run/detail projection and invalidate only affected queries

## Phase 7: Polish & Cross-Cutting Validation

**Purpose**: Verify the end-to-end MVP and keep the scope bounded.

- [X] T027 [P] Run targeted backend tests in `backend/tests/test_taiwan_live_runner.py`, `backend/tests/test_taiwan_live_ledger.py`, `backend/tests/test_taiwan_live_outcomes.py`, `backend/tests/test_taiwan_live_api.py`, `backend/tests/test_taiwan_live_scheduler.py`, and `backend/tests/test_taiwan_live_smoke.py`, plus Ruff checks for modified Python files; record actual results in `specs/001-daily-recommendation-hit-rate/quickstart.md`
- [X] T028 [P] Run targeted frontend tests in `frontend/src/components/quant/TodaySelection.test.tsx`, `frontend/src/pages/Dashboard.test.tsx`, and `frontend/src/pages/SelectionReview.test.tsx`, plus the existing TypeScript build for modified frontend files
- [X] T029 Run `git diff --check` and the quickstart smoke scenario, verifying no runtime data, secrets, absolute paths, automatic trading, new selector, or new storage authority entered the change

## Dependencies & Execution Order

- Setup (Phase 1) precedes Foundational (Phase 2); no dependency or migration is introduced.
- User Stories 1 and 3 depend on Foundational projections. User Story 2 depends on the same projections and the existing outcome ledger. User Story 4 depends on the additive API fields from Stories 1–3.
- Within each story, tests precede implementation; backend ledger/API changes precede frontend integration.
- MVP is Phases 1–4: formal daily snapshot display plus objective 1D/5D/20D tracking. Phases 5–7 are required before merge for readiness and reproducibility guarantees.

## Parallel Opportunities

- T003/T004, T006/T007/T008, T012/T013/T014, T018/T019, T022/T023, and T027/T028 can run in parallel when their shared target files are not being edited concurrently.
- User Story 1 and User Story 3 tests can be prepared in parallel; User Story 2 implementation follows the shared ledger projection.

## Implementation Strategy

1. Preserve the existing current-live runner, scheduler, `LiveLedger`, and outcome maturation as the only authorities.
2. Implement the additive backend projections and tests first.
3. Deliver Dashboard status/candidates, then Selection Review outcome/history UI.
4. Run targeted validation, converge against the specification, and fix only material gaps.

# Research: 台股每日推薦與命中率初測

## Repository evidence

| Area | Existing implementation | Planning consequence |
|---|---|---|
| Quant recommendation | `backend/app/taiwan/quant/live_contract.py` 定義 `tw-eod-momentum`、v1、features `momentum_5d/20d/60d`、horizons `1/5/20`、Top 10、warmup 61 sessions、ADV20 10M、rank threshold 0.7。`live_runner.py` 以 current official observations + local raw history 建立 panel 和排序。 | 不新增 scorer、權重、門檻或 rerank；只補 frozen signal 的展示欄位。 |
| Fixed execution | `backend/app/jobs/daily_pipeline.py` 已有 `CronTrigger(mon-fri, hour=16, minute=30, timezone="Asia/Taipei")`，daily refresh 成功後呼叫 `run_live_after_refresh`。 | 不新增 scheduler/job；沿用既有 downstream isolation。 |
| Current live gate | `CurrentLiveSource` 和 `build_live_batch` 已檢查 TWSE/TPEx current evidence、current verified universe、cutoff、factor panel、verified corporate action coverage、61-session window、session completeness、finite features 和 eligibility。 | 既有 gate 是每日推薦的可用性來源；把其錯誤轉成正式 status/reason，不把 unavailable 凍結成正式 snapshot。 |
| DataHealth | `backend/app/taiwan/quant/data_health.py` 的 `health_from_stores()`/`quant_evaluation_readiness()`服務於歷史 Primary OOS/A2b，`ready_for_factor_compute` 預設要求 252 sessions，Primary OOS 還要求高 coverage、classification 和 month verification。current live runner 目前沒有呼叫它。 | 直接把 Primary OOS gate 套到每日推薦會與既有 61-session current live contract 衝突。最小調整是建立共用的 live readiness projection，聚合現有 live gate 結果，不新增 252/80%/99% 等第二套門檻；DataHealth 的歷史狀態可作為 provenance/display，但不是 current live blocker。 |
| Provenance / immutability | `LiveLedger` 將 signals/models 與 outcomes 分 SQLite，`runs` 以 model/session primary key、digest、snapshot、frozen_at 保存；UPDATE/DELETE triggers、conflicts、operations 均已存在。`freeze()` 禁止 OOS fields、驗證 contract/session/model/cutoff/feature hash/universe identity。 | `LiveLedger` 直接作為每日推薦快照 authority；重試同 digest noop，不同 digest 保留 conflict，不能寫入第二份正式快照。 |
| Outcome evaluation | `live_outcomes.py` 的 `mature_live_outcomes()` 已依 verified actual trading sessions 找 H=1/5/20，使用 signal `reference_close` 與 end-session close，並以 `forward_adjusted_return` 寫入 immutable outcomes；缺 session price 或 action coverage 會 data_insufficient。 | 不重做 5D/20D evaluator；只增加從 outcome rows 產生顯示統計的純 read projection。 |
| Selection Review | `selection_review_models.py`/`selection_review_service.py` 已有 `HorizonReviewItem`、pending/unavailable、reference-close 口徑和 `ForwardBatchStats`；hit rate 是未四捨五入 return `> 0`，不含 pending/unavailable。其 JSON store 是 research/forward batch authority。 | 複用命中率文字和 UI 呈現語意，但不把 daily live run 複製到 Selection Review JSON；Selection Review 只讀 `LiveLedger` API。 |
| Dashboard | `Dashboard.tsx` 已放置 `TodaySelection`；`TodaySelection.tsx` 已讀 live models/runs/run detail，顯示最多 10 檔、current quote、reference close 和 feature-derived reasons。 | 在既有元件加入 frozen name/reason、正式狀態和 outcome summary；Dashboard 不另開一條資料流。 |
| Persistence / refresh | `TaiwanDailyUpdateService` 是 16:30 market refresh；`run_live_after_refresh` 在 daily 非 success 時記錄 `skipped/daily_refresh_not_ready`，不改寫 refresh result。 | unavailable/blocked 嘗試可由既有 operations read projection 顯示；正式推薦只來自 audited frozen run。 |
| Tests | 已有 `test_taiwan_live_api.py`、`test_taiwan_live_ledger.py`、`test_taiwan_live_runner.py`、`test_taiwan_live_scheduler.py`，以及 selection review/quant/frontend tests。 | 以 targeted regression 擴充既有 fixtures，不引入新測試架構。 |

## Decisions

### Daily timestamp

採台灣時間每日 16:30，沿用現有 scheduler。`LiveLedger.current_session()` 已以 official trading evidence 和 publication boundary 決定最近完成交易日，因此週末、休市日、證據未確認不會被誤當成推薦日。

### Entry/reference price

採推薦日收盤價。現有 `build_live_batch()` 已把當日 `reference_close` 存在 signal，`mature_live_outcomes()` 已使用此欄位計算收盤到收盤的標準化紙上報酬。前端的最新 quote 只能作為目前行情輔助，不可替換此欄位。

### Horizon

採推薦日後第 1、5、20 個已確認實際交易日，沿用 `HORIZONS` 和 outcome maturity；不使用自然日，不遇缺口向前找下一筆行情。

### Zero candidates versus unavailable

- `frozen/audited + signals=[]`：`available_zero_candidates`，顯示資料可用但 0 檔，不作資料失敗。
- 沒有 current audited run、daily refresh 非 success、live source/factor/action/session gate 失敗：`unavailable`，顯示原因，不建立正式推薦樣本。
- 同一 model/session 有不同 digest：`conflict`，原 snapshot 維持，衝突不可選邊。
- 已有 snapshot 但 horizon 尚未成熟：推薦仍是正式 snapshot，單一 outcome 是 `pending`，不進 hit-rate 分母。

### Hit rate

每個 horizon 只聚合 `status=verified` 且 finite 的原始報酬；`hit_count = count(return > 0)`，`evaluable_count = count(verified)`，`hit_rate = hit_count / evaluable_count * 100`。無 evaluable 樣本回傳 `null`，UI 顯示尚無樣本；pending/unavailable 不轉成 0。

### DataHealth conflict resolution

這不是未決產品問題，而是目前 code ownership 與 spec 用語的落差。`quant_evaluation_readiness()` 的 contract 明確是 Primary OOS/A2b，不應用來阻止 current live。Implementation 應保留 `DataHealth` 原有 historical API 和門檻，新增的只是把 current live 已存在的 gate 結果轉成可儲存/顯示的 `live_readiness` 結構，並在文件中標明它不是 Primary OOS readiness。若要在畫面同時呈現 historical DataHealth，讀既有 DataHealth API，標成 context，不得用它改寫當日 live status。

## Look-ahead and revision controls

- `data_cutoff`、`feature_price_anchor`、raw history hash、feature snapshot hash、corporate action coverage 和 source evidence 必須跟既有 snapshot 一起保存。
- `name` 只能從 freeze 當下的 `TaiwanSecurityMaster` frame 寫入；舊 snapshot 缺 name 時顯示 unavailable/未保存，不讀今日 master 回填。
- reason summary 只能由 freeze 當下的 score、rank、feature percentiles、policy eligibility 和既有 rule/model metadata 組成；AI 文字不進 hit-rate calculation。
- outcome recheck 只能追加 observation/evaluation digest；若同一 identity 有不同 verified result，保留 conflict/audit 狀態。

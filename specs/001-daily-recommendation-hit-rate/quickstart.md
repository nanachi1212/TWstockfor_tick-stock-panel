# Quickstart: 驗證每日推薦與命中率初測

本文件供 implementation 完成後做 targeted smoke test，不會建立新的 scheduler、migration 或外部資料流程。

## 1. 確認固定執行與資料邊界

1. 確認 backend scheduler 註冊 `taiwan_daily_update`，trigger 是 `Asia/Taipei` 16:30、週一至週五。
2. 確認該 job 先執行 `TaiwanDailyUpdateService.run_update(refresh_daily=True)`，只有 `daily.status == "success"` 才呼叫 live cycle。
3. 確認 live freeze 使用最近已完成且有 official TWSE/TPEx evidence 的交易日，不接受 caller-supplied date。

## 2. 確認正式快照

以既有測試 fixture 或本地資料啟動 backend 後，讀：

```text
GET /api/taiwan/quant/live/models
GET /api/taiwan/quant/live/runs
GET /api/taiwan/quant/live/runs/tw-eod-momentum/{expected_session}
```

驗證：

- 16:30 後成功 run 狀態為 `formal_available` 或 `available_zero_candidates`。
- 每一 signal 有 symbol、name、signal_session、reference_close、rank、score、reason_codes/reason_summary。
- signal 數量 `0 <= count <= 10`，順序與 frozen rank 一致。
- snapshot hash、feature/raw history hash、cutoff 和 session evidence 存在。

## 3. 確認 unavailable 和 zero 的分離

用既有 runner fixtures 分別模擬：

- `signals=[]` 且所有 live gate verified，API 顯示 `available_zero_candidates`。
- daily refresh partial/failed、current evidence 缺失、corporate action coverage 不完整或 feature window 不足，API 顯示 `unavailable` 和原因，且 `/runs` 不新增 formal run。
- 同一 session 第二次相同 freeze 是 `noop`；變更 digest 會留下 conflict，原 run 不變。

## 4. 確認 outcome 與命中率

使用固定後續行情 fixture，覆蓋三種狀態：

- 未到 H 個交易日：`pending`，value 為 null，不進分母。
- 到期且價格完整：`verified`，以 frozen reference close 計算 return。
- 到期但 session price、交易日 evidence 或 action coverage 缺失：`data_insufficient`，不向前找下一筆價格，不進分母。

對每個 H=1/5/20 驗證：

```text
evaluable = verified outcomes
hit = raw return > 0
hit_rate_pct = hit / evaluable * 100, only when evaluable > 0
```

確認第 H 個交易日而非自然日，且 0% 不算 hit。

## 5. 確認 UI

- Dashboard 顯示當日 formal/unavailable/zero 狀態、最多 Top 10、frozen data date/reference close/rank/score/reason，並顯示 1D/5D/20D 的 evaluated/pending/unavailable/hit rate/average return。
- Selection Review 的 live 區塊可列出歷史 run，選擇一天後看到同一份 frozen snapshot 和 outcomes。
- 後續行情 refresh 後重新整理頁面，原始 name、reference close、rank、score、reason 不變；只有 outcome 狀態依 append-only evaluation 更新。
- UI 不顯示自動下單、資金配置或獲利保證字樣；明確標示 experimental/live、paper price-normalized return。

## Suggested targeted tests

```text
backend/tests/test_taiwan_live_runner.py
backend/tests/test_taiwan_live_ledger.py
backend/tests/test_taiwan_live_api.py
backend/tests/test_taiwan_live_scheduler.py
frontend/src/components/quant/TodaySelection.test.tsx
frontend/src/pages/SelectionReview.test.tsx
frontend/src/pages/Dashboard.test.tsx
```

先執行受影響測試，再依 repository 的既有 CI 命令執行 backend/frontend targeted checks。這個 feature 的 plan 階段不執行 implementation、migration 或 production data refresh。

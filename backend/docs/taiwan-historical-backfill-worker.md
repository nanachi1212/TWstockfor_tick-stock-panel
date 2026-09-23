# Taiwan Historical Backfill Worker（Background Data Lane）

> 這是**背景資料車道**。它的完成**不是**後續程式開發的前置條件。
> Foreground engineering 與這個 worker 並行進行。

---

## 0. 它做什麼

| 階段 | 內容 | 每次請求數 |
| --- | --- | --- |
| **A2a** Observed Membership Census | 2015-01-01 → today，每個待查候選平日各抓 TWSE `ALLBUT0999` 與 TPEx `dailyQuotes` 各 1 次，保存**官方當日快照實際出現的證券** | 1 / 交易所 / 候選日 |
| **A2b** TWSE First-Seen Classification | 依 census 算出每個 TWSE code 的 `first_observed_date`，對尚未分類的日期掃 34 個官方產業表 + 1 個 ETF 表 | **35 / 日期** |

兩個階段共用同一個 CLI、同一把鎖、同一份 checkpoint。

**A2a 不套用 current Security Master allowlist。** 它保存的是 observed market fact，
不是「今天還支援的 universe」——這正是 `taiwan-historical-universe-probe.md` §2 證明會丟掉
8/8 已下市樣本的那個過濾器。

---

## 1. 使用方式

```bash
cd backend

# 看進度（不做任何工作，不發請求）
uv run --frozen python -m scripts.taiwan_historical_backfill --status

# 跑一次（預設 budget）
uv run --frozen python -m scripts.taiwan_historical_backfill

# 自訂 budget
uv run --frozen python -m scripts.taiwan_historical_backfill \
    --daily-session-budget 300 --classification-request-budget 1200

# 只跑 census，不跑分類
uv run --frozen python -m scripts.taiwan_historical_backfill \
    --daily-session-budget 300 --classification-request-budget 0

# 確定沒有其他 worker 在跑時，強制清鎖
uv run --frozen python -m scripts.taiwan_historical_backfill --force-unlock
```

### LongRun（unlimited）

有空時可以讓 worker 連續跑幾小時、甚至一次跑完：

```bash
uv run --frozen python -m scripts.taiwan_historical_backfill --long-run
```

```powershell
.\scripts\run_taiwan_historical_backfill.ps1 -LongRun
```

`--long-run` 等同把兩個 budget 都設為 **0 = unlimited**。
**其餘保證完全不變**：

- rate limit 照舊（`taiwan:twse` / `taiwan:tpex` 各 16 rpm，**unlimited 不會提高 rpm**）
- bounded retry、parking 照舊
- single-instance lock 照舊
- atomic partition write 照舊
- **checkpoint 每 50 個 session flush 一次**，不必等整個 run 結束
- **Ctrl+C 一次 → 做完手上那個 session 後乾淨退出**；下次執行從 checkpoint 續跑

因為 `0` 現在代表 unlimited，要「只跑其中一個階段」改用：

```bash
--skip-census            # 只跑 A2b 分類
--skip-classification    # 只跑 A2a census
```

PowerShell launcher（會自動寫 log 到 `data/logs/`）：

```powershell
.\scripts\run_taiwan_historical_backfill.ps1
.\scripts\run_taiwan_historical_backfill.ps1 -Status
.\scripts\run_taiwan_historical_backfill.ps1 -SessionBudget 600 -ClassificationRequestBudget 2400
```

### Exit code

| code | 意義 |
| --- | --- |
| `0` | 有做完工作 / 達到 budget / 沒事可做 / **另一份 worker 正在跑** |
| `1` | 非預期錯誤 |

**達到每日 budget 是正常結束，不是錯誤。**

---

## 2. Windows Task Scheduler 建議設定

> ⚠️ 以下只是建議設定，**本次未代為修改任何排程工作**。請自行建立。

| 項目 | 建議值 |
| --- | --- |
| 名稱 | `TWstock Taiwan Historical Backfill` |
| 觸發程序 | 每天 **23:30** |
| 動作 | 啟動程式 |
| 程式 | `pwsh.exe`（無 PowerShell 7 則用 `powershell.exe`） |
| 引數 | `-NoProfile -ExecutionPolicy Bypass -File "E:\Git\台灣股票\TWstockfor_tick-stock-panel\scripts\run_taiwan_historical_backfill.ps1"` |
| 起始位置 | `E:\Git\台灣股票\TWstockfor_tick-stock-panel` |
| 使用者已登入時才執行 | 建議勾選（`uv` 需在該使用者 PATH 上） |
| 如果工作執行超過 | `4 小時` → 停止 |
| 若工作已在執行 | **不要啟動新執行個體** |

建立指令（僅供參考，**請自行確認後再執行**）：

```powershell
$repo = "E:\Git\台灣股票\TWstockfor_tick-stock-panel"
$action = New-ScheduledTaskAction -Execute "pwsh.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$repo\scripts\run_taiwan_historical_backfill.ps1`"" `
  -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Daily -At 23:30
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
  -ExecutionTimeLimit (New-TimeSpan -Hours 4) -StartWhenAvailable
Register-ScheduledTask -TaskName "TWstock Taiwan Historical Backfill" `
  -Action $action -Trigger $trigger -Settings $settings
```

`-MultipleInstances IgnoreNew` 與 worker 自己的檔案鎖是兩層保險；任一層都足以避免同時跑兩份。

---

## 3. Budget 與完成時間

實測（2026-09-22，A1 節流 `taiwan:twse` / `taiwan:tpex` 各 16 rpm，兩 bucket 並行）：

| 量測 | 值 |
| --- | --- |
| 候選 session 總數（2015-01-01 → 2026-09-22，已扣除週末） | **3,059** |
| 實測速度 | 6 sessions × 2 交易所 = **19.3 秒** → 約 **3.2 秒 / session** |
| A2b 單一日期 | **35 請求 / 128 秒** |

| 每日 budget | 每晚耗時 | A2a 完成所需天數 |
| --- | --- | --- |
| **300**（預設） | 約 16 分鐘 | **約 11 天** |
| 600 | 約 32 分鐘 | 約 6 天 |
| 1000 | 約 53 分鐘 | 約 4 天 |

A2b 的總量**不預設**，由 census 實際的 `unique_first_seen_dates` 決定：

```
exact_request_count_remaining = pending_jobs × 35
```

`--status` 隨時會告訴你精確數字。下例只有 10 個已處理候選日，樣本太小，
任何 A2b 總量推估都不可信 —— 等 census 推進後再看 `--status`。

---

## 4. Status 輸出範例

```json
{
  "generated_at": "2026-09-22T19:07:41+08:00",
  "census": {
    "TWSE": {
      "candidate_dates": 3059,
      "processed_dates": 10,
      "observed_trading_sessions": 9,
      "confirmed_non_trading_dates": 0,
      "unknown_empty_dates": 1,
      "unresolved_dates": 3050,
      "expected_trading_sessions": 3059,
      "processed_ratio": 0.0032690421706440013,
      "processed_percent": 0.33,
      "trading_coverage_ratio": 0.002942137953579601,
      "earliest_processed": "2015-01-01",
      "latest_processed": "2015-01-14",
      "earliest_trading_session": "2015-01-02",
      "latest_trading_session": "2015-01-14",
      "parked_dates": [],
      "completed_sessions": 10,
      "total_sessions": 3059,
      "percent": 0.33,
      "earliest_completed": "2015-01-01",
      "latest_completed": "2015-01-14",
      "trading_sessions": 9,
      "percent_processed": 0.33,
      "percent_trading_sessions": 0.29
    },
    "TPEX": { "...": "同上" }
  },
  "classification": {
    "twse_codes_observed": 911,
    "unique_first_seen_dates": 1,
    "completed_jobs": 1,
    "pending_jobs": 0,
    "failed_jobs": 0,
    "requests_per_job": 35,
    "exact_request_count_remaining": 0
  },
  "providers": { "census:TPEX": { "failures": 1, "retries": 0 } },
  "last_success_at": "2026-09-22T19:01:45+08:00",
  "last_run_at": "2026-09-22T19:04:13+08:00",
  "runs": 3,
  "estimated_remaining_runs": { "census": 11, "classification": 0 }
}
```

`processed_ratio` / `processed_percent` 只表示候選平日的 worker 處理進度。
`trading_coverage_ratio` 是 `observed_trading_sessions / expected_trading_sessions`，
其中 `expected_trading_sessions = candidate_dates - confirmed_non_trading_dates`；
未處理與無法確認原因的空分區都留在分母。TWSE 與 TPEx 各自計算，
Primary OOS 只使用 TWSE 的交易日覆蓋率與已驗證分類，TPEx 僅供 Secondary / Experimental 顯示。

`completed_sessions`、`percent`、`earliest_completed`、`latest_completed` 是過渡期保留的
**deprecated aliases**，分別對應已處理日期數、已處理百分比、最早與最晚已處理日期。
`trading_sessions`、`total_sessions`、`percent_processed`、`percent_trading_sessions`
也保留給既有檢視流程；新的 Quant readiness 不使用這些別名。全部欄位都可序列化為 JSON。

---

## 5. 儲存與 resume

```
<DATA_DIR>/taiwan/
    observed_universe/exchange=TWSE/date=2015-01-05/part.parquet
    observed_universe/exchange=TPEX/date=2015-01-05/part.parquet
    historical_classification/date=2015-01-05/part.parquet
    backfill_worker_state.json
    backfill_worker.lock
```

- **partition 檔案存在 = 該候選日已處理**，這就是全部的 resume 機制，沒有第二份 manifest 可以走樣。
- 官方回「無資料」→ 寫**空 partition**，屬處理終結狀態，不會被反覆重抓；這本身不證明休市，也不表示任何個別證券已下市。
- 只有經已驗證日曆確認的非交易日才在同一個 Parquet partition 的 metadata 記錄 `confirmed_non_trading`。舊空分區或只因解析結果為 0 筆的空分區一律維持 `empty_unknown`，並留在交易日覆蓋率分母；沒有額外的 completion manifest。
- 網路/傳輸失敗 → **不寫檔**，下次自動重試。
- 寫入使用 `mkstemp` + `os.replace`，**atomic**；中途斷電不會留下半個檔。
- `DATA_DIR` 已被 `.gitignore` 的 `data/**` 涵蓋，**不會進 Git**。

### Retry 與 parking

- 每次 HTTP 本身有 3 次 bounded 重試（TPEx 大 payload 常見 `WinError 10054` 連線中斷）。
- 跨 run 的失敗次數記在 `backfill_worker_state.json`；同一日期累計失敗 **5 次**後
  **parked**，不再消耗 budget，並列在 `--status` 的 `parked_dates` / `failed_jobs`。

### 中斷

Ctrl+C 一次 → 做完手上這一個 session 後乾淨退出（partition 不會半寫）。
再按一次 → 立即中止。

---

## 6. 邊界（不得違反）

1. **Current product snapshot 行為完全不變。**
   `OfficialDailySnapshotAdapter` 一行未改，census 模組不 import 它，
   也不碰 `get_security_master` / `TaiwanSecurityMaster`（有測試守著）。
2. **Census 不寫 `TaiwanDailyStore`。**
   15 個模組消費那個 store（screener、technical_indicators、research_context、
   market_intelligence、watchlist_enrichment …），每一個都把 row 當成已分類的可交易台股。
   TPEx 單日帶約 10,900 檔類型無法驗證的權證，寫進去會直接污染全部下游。
3. **A2a 不猜任何分類。**
   `instrument_type = null`、`instrument_type_status = data_insufficient`。
   沒有「4 位數看起來像股票」這種規則。
4. **A2b 不宣稱 point-in-time 產業別。**
   官方產業標籤是 current 且不唯一（probe §4.3），所以
   `industry = null` / `industry_status = data_insufficient`。
   產業表只當作「當日是否為普通股」的 membership oracle。
5. **TPEx 不得進 Primary Verified OOS。**
   TPEx historical instrument_type = BLOCKED（probe §9.4）。
   observation 照存，但 Primary universe 只收 `exchange=TWSE` +
   `classification_status=verified` + `instrument_type=stock`。

命名規範：

```
Primary   : TWSE Verified OOS
Secondary : TWSE + TPEx Observed Experimental
```

**禁止**把兩者合併宣稱為「全台股 survivorship-free OOS」。

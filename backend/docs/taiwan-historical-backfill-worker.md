# Taiwan Historical Backfill Worker（Background Data Lane）

> 這是**背景資料車道**。它的完成**不是**後續程式開發的前置條件。
> Foreground engineering 與這個 worker 並行進行。

---

## 0. 它做什麼

| 階段 | 內容 | 每次請求數 |
| --- | --- | --- |
| **A2a** Observed Membership Census | 2015-01-01 → today，每個交易日各抓 TWSE `ALLBUT0999` 與 TPEx `dailyQuotes` 各 1 次，保存**官方當日快照實際出現的證券** | 1 / 交易所 / session |
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

`--status` 隨時會告訴你精確數字。目前 census 只跑了 10 個 session，樣本太小，
任何 A2b 總量推估都不可信 —— 等 census 推進後再看 `--status`。

---

## 4. Status 輸出範例

```json
{
  "generated_at": "2026-09-22T19:07:41+08:00",
  "census": {
    "TWSE": {
      "completed_sessions": 10,
      "total_sessions": 3059,
      "percent": 0.33,
      "earliest_completed": "2015-01-01",
      "latest_completed": "2015-01-14",
      "parked_dates": []
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

全部欄位都是 JSON-serialisable，可直接餵給未來的 Dashboard Data Health 面板。

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

- **partition 檔案存在 = 該 session 已完成**，這就是全部的 resume 機制，沒有第二份 manifest 可以走樣。
- 休市日／官方回「無資料」→ 寫**空 partition**，屬終結狀態，不會被反覆重抓。
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

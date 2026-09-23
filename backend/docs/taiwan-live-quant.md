# Live Prediction Ledger

此流程記錄當時實際執行的 EOD 訊號，所有模型均為 **experimental live / unvalidated**。
Historical Primary OOS 仍因普通股 subtype 缺少 authoritative as-of 證據而 blocked；
本流程不解除該限制，也不宣稱已驗證績效。

## 執行與查詢

既有 Asia/Taipei 16:30 daily scheduler 在 market-data refresh 完成後執行 freeze 與
outcome maturation。daily refresh 非 success 時 skip，並記錄原因。Quant 的例外不會
改寫或回滾行情更新結果；freeze blocked 時仍可獨立成熟先前訊號。

從 backend 目錄執行正常補跑：

```powershell
uv run python -m app.taiwan.quant.live_runner
```

此命令使用既有 `settings.data_dir`，沒有日期區間、historical backfill、OOS import
或強制覆寫選項。需先確認應用程式的 DATA_DIR；隔離 worktree 預設資料位置不同。

唯讀 API：

- `GET /api/taiwan/quant/live/models`：已配置模型、首次啟用 session、驗證狀態、最近執行結果。
- `GET /api/taiwan/quant/live/runs?limit=30`：批次摘要（最多 100）。
- `GET /api/taiwan/quant/live/runs/{model_key}/{session}`：完整 frozen snapshot、conflict audit、獨立 outcomes。

無前端變更；不提供績效頁、交易建議 UI 或寫入 API。

## 正確性契約

### Session 與首次啟用

沿用 daily pipeline 的 16:00 Asia/Taipei publication cutoff。隔日 cutoff 前或週末可
重試上一個最新已完成 session；已開始下一個可發布 session 後不能再補寫舊 session。
TWSE、TPEx 必須都有交易證據；unknown weekday 或矛盾證據會 blocked，不能當成休市。
公司行動、交易日與 subtype 的證據各自獨立。

模型第一次在具備 session 證據時啟用，會原子寫入 `first_live_session` 與真實
`activated_at`。這是該版本的首次 live 執行資格，即使當次因 feature readiness 不足
而 blocked，資格仍保留；不宣稱當天已有 signal。呼叫者不能指定更早日期。
更改 weights、features、policy、門檻或 schema 必須換 model version；同版本不同定義拒絕。

### Current universe 與排名

重新讀取官方 current Security Master，與當 session 的官方市場觀測交集。
只准 active、supported ordinary stocks；官方公司代號欄位或普通股 CFI 證據必須有效。
舊快取、inactive、錯誤資產類型、無市場觀測均不得入選；不依股票代碼長度或名稱猜測。
這是 current live contract，不能進入 Historical admission/training 或歷史 factor storage。

重用 B2 Factor Panel 的當日計算、PIT adjustment、eligibility 數值門檻與既有
deterministic percentile 函式。v1 是預先固定的 5/20/60-session momentum 等權排名，
沒有使用未驗證 OOS 的 fitted weights、IC 或 validation threshold。
至少 61 個連續已確認交易 session、ADV20 至少 1 千萬 TWD；三個 ranking features
均須可用。rank score 至少 0.7 且 20-session momentum > 0，最多 10 檔，允許 0 檔。
缺值不參與排名，不製造 confidence、probability、industry 或 risk assessment。

讀取最近 200 個自然日作為有界 raw history；feature horizon 本身仍按已確認交易 session，
不是自然日。無法證明窗口完整時 blocked 或排除該標的。歷史 worker 不是執行先決條件；
既有當期 raw observations 與可選的已確認 calendar evidence 可直接使用。

公司行動五個既有官方來源必須完成所需期間的查詢；空 events store 不算 completeness。
不支援或不完整事件仍由既有復權契約判定 data_insufficient。
缺少 index history 時 regime 只報有證據的 breadth，標記 partial；
全部缺少時 regime 為 null/data_insufficient，不能把預設 neutral 誤報成有效風險判斷。

### 不可變事件與 hash

`<DATA_DIR>/taiwan/live_quant/signals.sqlite3` 保存 models、runs、conflicts、
operations；`outcomes.sqlite3` 保存 outcome observations。兩者均與 backtest/OOS
及 historical factor partitions 實體隔離，沒有通用 origin 欄位匯入器。

每個 model/version/session 只准一個原子批次，包含 0 signal 的批次。
SQLite transaction、唯一鍵及禁止 UPDATE/DELETE 的 triggers 防止併發覆寫。
identical retry 為 no-op；不同 snapshot 留存 audit 並 fail-closed。
已存在 batch 的 operational retry 讀取原始 snapshot，不用最新資料重造。

收集 evidence／snapshot 前以 model key 取得跨程序 OS lock；重疊的排程或手動執行
回報 `skipped/live_run_in_progress`，不建立第二份 snapshot。owner 完成後重試讀取原始
batch 並 no-op；owner 異常退出會由 OS 釋放鎖。持續存在的 `.construction-*.lock`
只是鎖定檔案，不代表執行中，不應刪除。SQLite writer 仍獨立強制內容衝突與 immutable 契約。

Snapshot 保存實際 cutoff、完整 verified/eligible/ranking universe、當時 factor rows、
缺值 coverage、rank/score、模型與 policy 版本、公司行動、session evidence 與 raw history hash。
features 本身完整保存，raw history 不複製進 ledger。SHA-256 使用 canonical JSON：
鍵與 record collections 排序、整數/等值浮點數正規化、拒絕 NaN/Infinity，排序由明確 rank 欄位表示。

### Outcome

1D、5D、20D 依實際 exchange trading sessions 成熟，並確認每個 session 都有該標的價格。
停牌／缺資料不能略過並把下一筆 bar 當作原 horizon。未成熟 pending；
已成熟但缺價格或公司行動證據則 data_insufficient。
使用 frozen signal 的 reference close 及既有 `forward_adjusted_return`：
close(T) 到 close(T+H) 的 PIT price-normalized return，**不是 total return 或可成交策略績效**。
隔日補跑的真實 data cutoff 保留，不能聲稱當時已在前一日收盤前知道訊號。

結果以獨立 observations 追加；相同結果 no-op，不同 verified 結果顯示 conflict，
保留第一筆結果與所有審計紀錄，不覆寫。正常 scheduler 不重算已 verified/conflict 的
horizon；維護程式可呼叫 maturation 的 `recheck_verified=True` 驗證資料修訂，仍只追加。
重驗時暫時缺資料會保留既有值並顯示 `audit_status=recheck_unavailable`；
狀態轉換另存 append-only evaluations，連續相同結果不重複写入。
此流程不提供任意解除 conflict 或修改 signals 的捷徑。

## 驗證與維運

定向測試：

```powershell
uv run pytest tests/test_taiwan_live_ledger.py tests/test_taiwan_live_runner.py tests/test_taiwan_live_scheduler.py -q
```

新 storage 無需遷移既有使用者資料，沒有讀取快取、SSE 或前端 query cache 失效需求。
停止執行新 scheduler step 即可停止累積；保留兩個 SQLite 檔案即可保留已發生的紀錄。
不可為清理或版本升級刪除舊 live events。外部資料不足時查詢 models API 的
latest_operation；不得將 blocked、unavailable 或 0 signal 說成有效模型績效。

2026-09-23 operational follow-up：TWSE 與 TPEx 的 2026-07-10 legacy empty census
partitions 已依政府停班公告及 TWSE 非營業日公告補上 verified closure provenance；原始
0-signal batch 保持不變。隨後用官方 daily snapshot adapter 補齊 2026-09-11、09-14 至
09-18、09-21 至 09-23，共 21,024 筆，涵蓋 TWSE 與 TPEx。抽查既有兩檔候選股的下個
session 所需 60 根前置 bars 均完整；新的 EOD freeze 仍只會由下一個正常 session 觸發。

此事件也揭露 daily refresh 原本會把兩交易所皆空的結果計為 skipped，且 adapter 會吞掉
單一交易所錯誤，讓不完整日期可能被當成已完成。現已改成雙交易所快照完整且非空才寫入；
空回應、provider error 或 schema mismatch 會成為 failed date、留待下次排程重試。現有
交易日、readiness、PIT 與 immutable ledger guards 均未變更。

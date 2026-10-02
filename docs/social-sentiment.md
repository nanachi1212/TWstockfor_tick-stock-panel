# PTT Stock + Dcard 台股社群情緒 MVP

此功能是獨立觀察訊號，不會修改 Quant 推薦、策略權重或排序。

## App 介面

側邊欄的「社群聲量」提供可排序排行榜、AI 情緒篩選、自選股／持倉篩選、盤前與盤後歷史快照，以及個股分析導覽。Dashboard 只顯示 Top 5 小卡；個股分析頁顯示該股的熱度、聲量、來源覆蓋與 AI 情緒。Dcard 若被 HTTP 403 阻擋，介面會顯示「目前來源不可用」，PTT 排行仍可使用，overall 狀態為 `partial`，不會把 Dcard 偽裝成零討論。

側邊欄的「社群即時撈取」使用同一套 collector、股票辨識、AI、聚合與保存流程。按鈕會建立背景工作，頁面輪詢工作狀態，不會讓 HTTP request 等待完整抓取。完成後顯示本次排行、來源狀態、最多三則代表留言，以及可依來源、股票與關鍵字查詢的討論清單；每次最多回傳 100 筆，預設 50 筆。

「複製社群排行榜給 AI」只匯出聚合資料與來源狀態，不包含 credentials、原始文章或留言。提示詞會明確標示社群討論可能有偏誤，不能視為公司基本面或未來股價證據。

## 手動執行

在 repository 根目錄執行：

```powershell
pwsh -File .\scripts\run-social-sentiment.ps1
```

也可以只抓最近 6 小時、限制頁數，或在沒有 AI key 時先完成聲量聚合：

```powershell
pwsh -File .\scripts\run-social-sentiment.ps1 -Hours 6 -Pages 1 -NoAi
```

輸出位於 `data/social_sentiment/`：

- `latest.json`
- `history/YYYY-MM-DD.json`
- `history/snapshots/YYYY-MM-DD-pre_open.json` 與 `history/snapshots/YYYY-MM-DD-after_close.json`（同時段聲量比較基準）
- `history/snapshots/YYYY-MM-DD-HHMMSS-ffffff-manual.json`（不可變的手動執行快照）
- `social_sentiment_history.csv`

資料目錄遵循現有 `DATA_DIR` 設定。排程快照不保存討論原文；手動快照只保存文章節錄與每篇最多三則代表留言，不保存數千則完整留言。

## Windows Task Scheduler

正式排程只有 08:30 與 15:30。兩個 Task 都使用 `Hidden=True`、`StartWhenAvailable=True` 與 `MultipleInstances=IgnoreNew`，並以 `-NoProfile -NonInteractive -WindowStyle Hidden` 執行。若排程時間關機，Task Scheduler 會在下次開機後補跑；pipeline 會把超過正常容許時間的執行標記為 `missed_schedule`。不再建立 09:30 Recovery，以免和 `StartWhenAvailable` 重複。

```powershell
pwsh -NoProfile -File .\scripts\install-social-sentiment-schedule.ps1
```

CLI、排程與 App API 共用 `data/social_sentiment/run.lock` 的跨 process single-flight。已有工作執行時，第二次手動請求回傳 `already_running`，不會再啟動 PTT／Dcard collector。

## API

`GET /api/taiwan/social-sentiment` 讀取 `latest.json`，也可用
`GET /api/taiwan/social-sentiment?target_date=YYYY-MM-DD` 讀取歷史結果，或加上 `snapshot_slot=pre_open|after_close` 指定快照。`GET /api/taiwan/social-sentiment/history` 回傳可選快照的精簡 metadata。API 只讀本地檔案，不會在請求期間抓取外部網站。

`POST /api/taiwan/social-sentiment/run` 以 `{ "mode": "manual" }` 啟動背景工作。`GET /api/taiwan/social-sentiment/jobs/{job_id}` 回傳 `queued|running|completed|partial|failed`、來源與 AI 狀態、數量及本次排行；可加上 `source`、`symbol`、`q`、`offset`、`limit` 查詢有上限的本次討論內容。API 不回傳 AI key、token 或完整 provider 設定。

PTT 使用 `over18=1` cookie 並限制最近頁數；Dcard 使用公開 forum/posts 與 comments 介面。兩個來源分開處理，單一來源失敗會保留錯誤狀態並繼續另一來源。AI 共用正式 AI Key Profiles 的 active provider（`key`、`base_url`、`model`、`provider`），每輪 batch 固定同一份 config snapshot。AI 未設定或單一 batch 回傳不合法 JSON 時，聲量與熱度仍照常保存，受影響股票的 `sentiment_status` 為 `unavailable`。

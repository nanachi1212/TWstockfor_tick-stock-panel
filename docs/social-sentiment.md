# PTT Stock + Dcard 台股社群情緒 MVP

此功能是獨立觀察訊號，不會修改 Quant 推薦、策略權重或排序。

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
- `social_sentiment_history.csv`

資料目錄遵循現有 `DATA_DIR` 設定；原始 PTT/Dcard 文章與留言不保存。

## Windows Task Scheduler

安裝階段不會自動修改 Task Scheduler。可在 PowerShell 以系統管理員或目前使用者權限手動建立兩個任務。背景任務必須同時設定 Task Scheduler 的 `Hidden`，並讓 `pwsh.exe` 使用 `-NoProfile -NonInteractive -WindowStyle Hidden`：

```powershell
$root = (Resolve-Path .).Path
$action = New-ScheduledTaskAction -Execute (Get-Command pwsh.exe).Source -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -File `"$root\scripts\run-social-sentiment.ps1`"" -WorkingDirectory $root
$settings = New-ScheduledTaskSettingsSet -Hidden
Register-ScheduledTask -TaskName "TickStock Social Sentiment PreOpen" -Action $action -Trigger (New-ScheduledTaskTrigger -Daily -At 08:30) -Settings $settings -Description "TickStock pre-open social sentiment refresh" -Force
Register-ScheduledTask -TaskName "TickStock Social Sentiment AfterClose" -Action $action -Trigger (New-ScheduledTaskTrigger -Daily -At 15:30) -Settings $settings -Description "TickStock after-close social sentiment refresh" -Force
```

若另建 recovery 任務，其 `pwsh.exe` action 也必須使用相同三個背景旗標並設定 `Hidden`，避免互動式 PowerShell 視窗短暫顯示。

## API

`GET /api/taiwan/social-sentiment` 讀取 `latest.json`，也可用
`GET /api/taiwan/social-sentiment?target_date=YYYY-MM-DD` 讀取歷史結果。API 只讀本地檔案，不會在請求期間抓取外部網站。

PTT 使用 `over18=1` cookie 並限制最近頁數；Dcard 使用公開 forum/posts 與 comments 介面。兩個來源分開處理，單一來源失敗會保留錯誤狀態並繼續另一來源。AI 未設定時仍會輸出聲量與熱度，情緒欄位為 `unavailable`。

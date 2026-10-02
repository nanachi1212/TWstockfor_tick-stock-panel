# 資料健康中心

側邊欄「資料健康」開啟 `/data-health`。看板只顯示正常資料集數量與連結，完整狀態、來源、資料日期、新鮮度、原因、最後嘗試及最後成功時間都在健康中心。時間以 `Asia/Taipei` 顯示；既有 metadata 未記錄的欄位顯示「未知」，不補成 0 或目前時間。

健康中心只讀取既有 store、快取、快照與驗證結果。GET 不呼叫市場 provider、不生成研究、不建立另一套資料更新流程。

## 資料集與操作

| 資料集 | 聚合來源與判定 | 安全操作 |
| --- | --- | --- |
| Daily OHLC | `TaiwanDailyUpdateService.get_freshness()`、最新有效分區與交易所涵蓋 | 更新／重試、重新驗證 |
| Realtime | 每檔最新一次觀察；只在盤中以報價年齡判過期（與 `get_quotes` 同一規則），盤後最後成交即最新 | 重新驗證 metadata |
| Institutional | 既有 freshness、最新法人分區、來源與 TWSE／TPEx 涵蓋 | 更新／重試、重新驗證 |
| Margin / Short | 同上；官方於當日晚間公布，16:30 後至晚間為「等待官方發布」，21:30／23:00 自動補抓 | 更新／重試、重新驗證 |
| Securities Lending | `FinMindCache` 已快取標的，沿用資料集 TTL；日期為最近一筆借券成交（事件資料） | 重新驗證 metadata |
| Financial Statements | 依法定申報期限判定應已公告期別；ETF 不適用，不計入 | 重新驗證 metadata |
| Monthly Revenue | 依次月 10 日公告期限判定；ETF 不適用，不計入 | 重新驗證 metadata |
| Foreign Shareholding | `FinMindCache` 已快取標的，沿用資料集 TTL | 重新驗證 metadata |
| TAIEX | 官方 `MI_5MINS_HIST` OpenAPI，隨盤後更新存入 `taiwan/benchmark_index.parquet`；OpenAPI 晚一日時為「等待官方發布」 | 更新／重試（同日資料更新）、重新驗證 |
| TPEX Index | 官方 `tpex_index` OpenAPI，同上；即時指數不充當歷史 benchmark | 更新／重試、重新驗證 |
| Trading Calendar | 兩交易所當日 `day_evidence`；官方日行情分區本身也是交易日證據（與 Live Quant 同一規則） | 重新驗證 metadata |
| Security Master | 既有本地證券主檔；未記錄更新頻率時保持 partial | 重新驗證 metadata |
| Quant Live Run | `LiveLedger` 現行交易日、operation projection、run audit | 重新驗證；不手動凍結或改寫 run |
| Selection snapshot | 手動鎖定的快照；今日未鎖定為「尚未執行」，不是過期 | 重新驗證；不自動建立快照 |
| Selection outcome | 未到期 horizon 屬正常；已到期但缺資料時列出阻擋原因 | 重新驗證；不生成缺失價格或報酬 |
| PTT | 社群快照中的來源 availability 與日期 | 更新／重試、重新驗證 |
| Dcard | 原始來源 status 與安全化的 HTTP 失敗原因 | 僅顯示原因，包括 HTTP 403 |
| Social AI | 社群 AI status；0 檔完成不算部分可用；保存安全的 HTTP 狀態碼 | 更新／重試、重新驗證 |
| AI Provider/Profile | 現行設定及既有連線 probe | 重新驗證連線 |

FinMind 為按需快取（開啟個股頁時抓取），狀態只涵蓋已快取標的，不宣稱全市場涵蓋。財報與月營收依申報週期判定，不以快取 TTL 或「是否等於今天」判定。

## 狀態與原因

| 狀態 | 顯示 | 意義 |
| --- | --- | --- |
| `current` | 正常 | 符合該資料集自己的發布週期（唯一計入看板正常數） |
| `partial` | 部分可用 | 部分交易所／標的缺漏，原因列出缺哪一邊 |
| `awaiting_publication` | 等待官方發布 | 更新器已查詢，官方尚未公布（或 OpenAPI 晚一日） |
| `stale` | 過期 | 超過該資料集應有的發布時點仍未取得 |
| `provider_error` | 外部服務失敗 | 外部來源回 HTTP 4xx/5xx（例如 Dcard 403、AI 402） |
| `not_run` | 尚未執行 | 排程尚未到（16:30）或需手動操作（選股鎖定、AI 驗證） |
| `unavailable` | 資料缺失 | 本地沒有可用資料 |
| `config_missing` | 設定缺失 | AI provider/profile 未設定 |
| `updating` / `error` | 更新中 / 錯誤 | 背景任務執行中 / metadata 損壞或 audit 衝突 |

日資料、法人、融資融券的原因來自更新器最後一次執行紀錄（`taiwan/daily_update_last_run.json`）：官方回應為空時記為 pending，不寫分區、下次重試；只有一個交易所發布時同樣不寫入，避免把單邊資料當成完整。最後成功時間取最新分區寫入時間，不以目前時間代替。未知的市場日不能冒充確認的交易日。

## 背景任務與快取

日資料按鈕呼叫既有 `TaiwanDailyUpdateService.run_update()`，一起更新日 K、法人與融資融券，保留其非強制、增量與發布時點規則，不覆寫既有分區。若既有 updater 無法補齊某個部分分區，任務維持 `partial`，不宣告修復成功。

PTT／Social AI 更新呼叫既有 `SocialSentimentJobManager.start_manual()`，沿用原本 PTT、Dcard 與 AI 流程和跨程序 collector lock。Dcard 403 只顯示原因，不提供繞過限制的入口。社群更新依既有設定使用 AI；AI Profile 驗證沿用短連線 probe，可能產生少量 API 用量，Codex CLI 驗證只檢查可執行能力。

任務狀態為 `queued`、`running`、`completed`、`partial`、`failed`。同一組日資料或社群來源的連點返回同一 job；前端也禁止重複送出。API 接受操作後立即回傳 202。重新驗證的 `completed` 代表檢查已完成，資料仍可顯示不可用。

新 job registry 為單一 backend 程序內、最多 60 筆的暫存記錄，服務重啟後清除。它不取代排程器或既有 updater 的鎖，也不提供跨多個 backend worker 的日資料排程協調。AI 驗證只對當時設定有效，設定改變後自動失效。

完成任務後，Layout 的共用 job 查詢使受影響的現有 TanStack Query cache 失效；即使已離開健康頁也會生效。資料寫入與 cache generation 仍由既有 store／服務管理，沒有新增 SSE 資料流。

## 驗證

後端測試涵蓋聚合、狀態映射、stale／partial／unavailable、provider failure、安全重試、併發去重、保留缺失欄位、HTTP 403、安全原因輸出、API 白名單及 AI 設定變更。前端測試涵蓋呈現、搜尋／狀態篩選、禁用、任務、快取失效、看板摘要與導航。

瀏覽器 smoke 使用隔離的合成資料與真實 health API，驗證 19 列、HTTP 403 僅原因、非同步重新驗證、直接刷新，以及深色桌面與淺色窄螢幕。此測試不代表已驗證真實市場 provider 的即時可用性。

合成測試資料畫面：[深色桌面](images/data-health-dark.png)、[淺色窄螢幕](images/data-health-light-narrow.png)。

# 資料健康中心

側邊欄「資料健康」開啟 `/data-health`。看板只顯示正常資料集數量與連結，完整狀態、來源、資料日期、新鮮度、原因、最後嘗試及最後成功時間都在健康中心。時間以 `Asia/Taipei` 顯示；既有 metadata 未記錄的欄位顯示「未知」，不補成 0 或目前時間。

健康中心只讀取既有 store、快取、快照與驗證結果。GET 不呼叫市場 provider、不生成研究、不建立另一套資料更新流程。

## 資料集與操作

| 資料集 | 聚合來源與判定 | 安全操作 |
| --- | --- | --- |
| Daily OHLC | `TaiwanDailyUpdateService.get_freshness()`、最新有效分區與交易所涵蓋 | 更新／重試、重新驗證 |
| Realtime | `TaiwanRealtimeService` 已查詢行情的 provenance 與既有 freshness policy | 重新驗證 metadata |
| Institutional | 既有 freshness、最新法人分區、來源與 TWSE／TPEx 涵蓋 | 更新／重試、重新驗證 |
| Margin / Short | 既有 freshness、最新融資融券分區與交易所涵蓋 | 更新／重試、重新驗證 |
| Securities Lending | `FinMindCache` 已快取標的，沿用資料集 TTL | 重新驗證 metadata |
| Financial Statements | 同上；報表期與抓取時間保持不同意義 | 重新驗證 metadata |
| Monthly Revenue | 同上；月份與抓取時間保持不同意義 | 重新驗證 metadata |
| Foreign Shareholding | `FinMindCache` 已快取標的，沿用資料集 TTL | 重新驗證 metadata |
| TAIEX | 與 Market Intelligence 共用 persisted benchmark metadata | 僅顯示原因 |
| TPEX Index | 同上；即時指數不充當歷史 benchmark | 僅顯示原因 |
| Trading Calendar | 兩交易所當日 `day_evidence`，未確認平日保持未知 | 重新驗證 metadata |
| Security Master | 既有本地證券主檔；未記錄更新頻率時保持 partial | 重新驗證 metadata |
| Quant Live Run | `LiveLedger` 現行交易日、operation projection、run audit | 重新驗證；不手動凍結或改寫 run |
| Selection snapshot | 最新不可變快照與既有目標交易日規則 | 重新驗證；不自動建立快照 |
| Selection outcome | 既有 snapshot review 的 1D／5D／20D 與 benchmark 狀態 | 重新驗證；不生成缺失價格或報酬 |
| PTT | 社群快照中的來源 availability 與日期 | 更新／重試、重新驗證 |
| Dcard | 原始來源 status 與安全化的 HTTP 失敗原因 | 僅顯示原因，包括 HTTP 403 |
| Social AI | 既有社群 AI status，包括 degraded | 更新／重試、重新驗證 |
| AI Provider/Profile | 現行設定及既有連線 probe | 重新驗證連線 |

FinMind 的狀態只涵蓋已快取標的，不宣稱全市場涵蓋。顯示的資料日期為已快取標的中最舊日期；財報與月營收是否為最新已公告期，仍依既有 consumer 契約判定。未快取標的保持未查詢。

## 狀態與原因

六種健康狀態為 `current`、`stale`、`partial`、`unavailable`、`updating`、`error`。只有 `current` 計入看板正常數量。不同標的或交易所狀態不一致時為 `partial`；缺資料保持 `unavailable`，metadata 損壞或 run audit 衝突為 `error`。

過期日資料顯示既有 updater 所認定的目標交易日。無官方發布證據時，不直接斷言「官方尚未發布」，而顯示「官方未發布或前次更新未完成」。未知的市場日不能冒充確認的交易日。

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

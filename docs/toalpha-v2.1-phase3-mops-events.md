# ToAlpha v2.1 Phase 3：MOPS 事件

本階段在既有事件中心與個股詳情顯示重大訊息、法人說明會、內部人持股轉讓事前申報。事件只作為產品事實證據，不接 Historical PIT、Strategy Lab、Quant 因子或事件報酬模型。

## Reuse audit

| 既有實作 | 判斷與重用方式 |
| --- | --- |
| mystocktracer `backend/internal/providers/toalpha/client.go`、`events.go` | 現有 Go adapter 僅查 `material_news`。參考其有界 MCP、SSE、來源狀態與穩定識別碼處理；跨語言不建立服務依賴，不複製 Go 架構或第三方分類。 |
| TWstock `monthly_revenue_evidence.py` | 官方 MOPS 月營收已有 append-only observation 與 PIT 邊界。保持唯一權威實作，未另造月營收 provider，也未把 observation time 當本階段事件公告時間。 |
| TWstock `dividend_events.py` | 已有官方 MOPS `t05st01`／`t05st01_detail` 股利生命週期。重用 `classify_dividend_event` 與官方查詢入口；一般重大訊息分類只提供產品主題，不覆寫股利事件、金額或復權計算。 |
| TWstock `events_service.py` | 重用 `MarketEvent`、事件篩選、既有 cache directory、API 與 Stock Detail 聚合。新增欄位提供向後相容預設值，不新增事件主選單。 |
| TWstock `providers/http.py`、`taiwan_values.py` | 重用 TWSE／TPEx HTTP 節流、台北時區、民國日期與整數股數標準化。 |

目前 TWstock 沒有一般 MOPS 事件 adapter，mystocktracer 也沒有法說會或事前轉讓申報的可重用實作，因此新增一個小型 MOPS adapter 補足能力。資料不透過 mystocktracer 執行，也不新增月營收、股利或行情權威資料流。

## 實際來源與能力邊界

| 事件 | 來源 | 欄位／範圍 |
| --- | --- | --- |
| 上市重大訊息 | [TWSE `t187ap04_L`](https://openapi.twse.com.tw/v1/opendata/t187ap04_L) | 公司、發言日期時間、主旨、條款、事實發生日、說明。 |
| 上櫃重大訊息 | [TPEx `mopsfin_t187ap04_O`](https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O) | 與上市來源相同語意，標準化 TPEx 英文 identity 欄位。 |
| 上市轉讓申報 | [TWSE `t187ap12_L`](https://openapi.twse.com.tw/v1/opendata/t187ap12_L) | 持股轉讓事前申報日報，預定轉讓股數、方式、有效期間。 |
| 上櫃轉讓申報 | [TPEx `mopsfin_t187ap12_O`](https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap12_O) | 相同事前申報語意，兼容「申請人身分」。 |
| 法人說明會 | [ToAlpha MOPS MCP](https://toalpha.tw/mcp/mops) `investor_conferences`，上游 MOPS `t100sb02_1` | 全市場近期／未來 60 日，最多 100 場；日期、舉行時間、地點、說明、中英文官方簡報連結。保留 `toalpha:mops:t100sb02_1` 來源，清楚標示第三方整理。 |

ToAlpha 文件所述 `insider_trades` 是每月持股異動事後申報，與本需求不同，不能拿來當事前轉讓申報或實際成交明細。本階段不呼叫它。

官方日報是滾動快照，不宣稱涵蓋過去完整公告。既有 cache directory 中的 `mops_events.json` 保留近 90 日已取得的事件觀察，並包含每筆的 source、published_at、available_at、retrieved_at、status。最初沒有保存的歷史無法回補或推定。

## 確定性主題分類

按來源明列的「符合條款」映射，版本為 `mops-clause-v1`，不呼叫 AI、不採第三方 category 或 important 欄位。

| 主題 | 第四條款號 |
| --- | --- |
| 財務與財報 | 9、13、30、31 |
| 股利決議 | 14 |
| 增減資與併購 | 4、11、16、36、38 |
| 資產交易與投資 | 15、20、24 |
| 營運與合作 | 3、10、25 |
| 公司治理與人事 | 6、7、8、17、18、21、29、34 |
| 背書保證 | 22 |
| 資金貸與 | 23 |
| 法律與信用風險 | 1、2、5、19、27、28 |
| 重大事故與資安 | 26 |
| 庫藏股 | 35 |
| 法人說明會 | 12 |

2026-10-01 核對 [TWSE 第四條](https://twse-regulation.twse.com.tw/TW/law/DOC01_print.aspx?FLCODE=FL007111&FLNO=4) 與 [TPEx 現行處理程序](https://www.selaw.com.tw/Chinese/RegulatoryInformationResult/Article?isIntegratedSearch=True&sysNumber=LW10812093)。規則是產品主題分組，不取代條款原文或法律判斷。未知、複合或未明列的條款保留「其他／未分類」，尤其不套用市場間不一致的後段條款。

## 時間與申報語意

- `published_at`：僅官方逐筆發言日期及合法發言時間可確認時設定，採 `Asia/Taipei`。官方數字型 HHMMSS 可左補零，非法或未來時間不推定。
- `available_at`：本階段僅重大訊息具有以上官方公告證據才與 `published_at` 相同。這仍未授權將本階段快照接入 PIT。
- `retrieved_at`：資料請求完成後的實際取得時間，保存及降級時不重寫。
- `status`：無法確認公告時間時為 `data_insufficient`，`available_at=null`；完整證據為 `available`。舊 cache 缺少新欄位時同樣保守處理。
- 法說會日期／時間是舉行時間，不能充作公告時間；第三方更新時間也不能充作官方 publication time。
- 轉讓申報日報只有「出表日期」，`event_date_kind=report_snapshot_date` 明確標示資料日期，不當成逐筆申報／成交日。`record_kind=pre_transfer_declaration`、`is_actual_transaction=false`，預定股數不命名為已售股數，缺股數保留 null。
- UI 明示「不代表實際成交或已賣出」，不推定交易完成。有效轉讓期間亦不代表成交期間已有成交。
- 只保留官方回傳的 HTTPS 簡報連結，限制官方 MOPS／TWSE 文件主機，不自動下載。沒有連結明示尚未提供。
- 本階段未新增事件股價表現計算；既有日線僅作價格觀察，不提供「公告後 X 小時」分析或因果宣稱。

## 整合、持久化與快取

`GET /api/taiwan/events` 包含三類事件，既有 `event_types`／`symbol`／`symbols`／scope 在 limit 前篩選。`refresh=true` 明確刷新官方及 MOPS 快照。Stock Detail 透過同一 service 取得事件，保留整體及逐來源查詢狀態。

既有選股、風險快照、研究 context、Historical PIT、Strategy Lab 使用原有路徑。`get_events(include_mops=False)` 為預設，只有產品 API 與個股詳情啟用；`get_pit_events` 及 `get_cached_regulatory_snapshot` 不讀 MOPS cache。

資料取得不持有資料鎖。同時查詢不重複發出 MOPS 請求，刷新中可回傳明確 stale 保存資料。每來源完整驗證後才納入，部分來源失敗保留其舊事件與原取得時間，整體不可用不覆蓋磁碟保存資料。成功空日報與來源失敗分開，跨日仍保存歷史觀察。

MOPS snapshot 在既有資料目錄內原子寫入並更新記憶體 cache，沒有新增 background writer／SSE producer。事件中心手動刷新後精確 invalidate 事件與 Stock Detail 的既有 TanStack Query 前綴。資料只寫 runtime directory，不放 release seed 或提交 Git。

## 驗證方式

離線 targeted tests 覆蓋三類標準化、12 類條款與未知條款、台北時間、無公告時間／未來時間、申報與成交分離、官方簡報 URL、MCP SSE／工具錯誤、空資料／schema 漂移、來源失敗、跨日保存、restart、API 契約與 PIT 排除。前端測試涵蓋事件中心篩選、刷新、錯誤、共享證據與 Stock Detail。

UI smoke 使用可重現的合成資料，檢查深色桌面、淺色窄螢幕、官方簡報、申報提示與切換；不拿使用者資料當截圖 fixture。另以實際 adapter 讀取官方及 ToAlpha 公開資料，僅記錄 counts 與狀態，不提交原始資料。

本次未新增 release seed，release seed 建置 gate 不適用；提交前執行 privacy／secret scan，並跑現有 privacy tests。

2026-10-01 本機驗證：新增 MOPS 測試 35 項、既有事件／股利測試 26 項、個股／研究／Strategy Lab／privacy regression 45 項通過。前端完整測試 44 個檔案、385 項通過，TypeScript 與 production build 通過。新增 MOPS adapter／事件服務／測試的 Ruff 通過，adapter 與事件服務的 targeted mypy 通過，既有 detail model 的 Ruff 告警未增加。

月營收既有 `test_month_end_refresh_covers_next_month_cutoff` 使用固定 2026-10-01 09:00 cutoff，實際 refresh 取得時間超過 cutoff 後會失敗。已在未修改的原始 repository 重現；保留為既有測試 blocker，不變更月營收實作或弱化 PIT 檢查。

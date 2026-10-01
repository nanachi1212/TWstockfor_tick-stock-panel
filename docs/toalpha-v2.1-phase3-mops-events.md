# ToAlpha v2.1 Phase 3：MOPS 事件

本階段在既有事件中心與個股詳情顯示重大訊息、法人說明會、內部人持股轉讓事前申報。事件只作為產品事實證據，不接 Historical PIT、Strategy Lab、Quant 因子或事件報酬模型。

ToAlpha 僅為產品功能參考，不是資料來源。本專案不使用、代理、保存或依賴其資料，沒有 MCP runtime、第三方 fallback 或第三方事件快取讀回路徑。

## Reuse audit

| 既有實作 | 判斷與重用方式 |
| --- | --- |
| mystocktracer 既有公司事件功能 | 僅參考產品功能與既有模組邊界，不移植第三方取數程式、MCP transport、資料或分類。 |
| TWstock `monthly_revenue_evidence.py` | 官方 MOPS 月營收已有 append-only observation 與 PIT 邊界。保持唯一權威實作，未另造月營收 provider，也未把 observation time 當本階段事件公告時間。 |
| TWstock `dividend_events.py` | 已有官方 MOPS `t05st01`／`t05st01_detail` 股利生命週期。重用 `classify_dividend_event` 與官方查詢入口；一般重大訊息分類只提供產品主題，不覆寫股利事件、金額或復權計算。 |
| TWstock `events_service.py` | 重用 `MarketEvent`、事件篩選、既有 cache directory、API 與 Stock Detail 聚合。新增欄位提供向後相容預設值，不新增事件主選單。 |
| TWstock `providers/http.py`、`taiwan_values.py` | 重用 TWSE／TPEx HTTP 節流、台北時區、民國日期與整數股數標準化。 |

Reuse audit 時 TWstock 沒有涵蓋本次三類事件的 adapter，因此新增小型官方 MOPS adapter 補足能力。資料不透過 mystocktracer 執行，也不新增月營收、股利或行情權威資料流。

## 實際來源與能力邊界

| 事件 | 來源 | 欄位／範圍 |
| --- | --- | --- |
| 上市重大訊息 | [TWSE `t187ap04_L`](https://openapi.twse.com.tw/v1/opendata/t187ap04_L) | 公司、發言日期時間、主旨、條款、事實發生日、說明。 |
| 上櫃重大訊息 | [TPEx `mopsfin_t187ap04_O`](https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O) | 與上市來源相同語意，標準化 TPEx 英文 identity 欄位。 |
| 上市轉讓申報 | [TWSE `t187ap12_L`](https://openapi.twse.com.tw/v1/opendata/t187ap12_L) | 持股轉讓事前申報日報，預定轉讓股數、方式、有效期間。 |
| 上櫃轉讓申報 | [TPEx `mopsfin_t187ap12_O`](https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap12_O) | 相同事前申報語意，兼容「申請人身分」。 |
| 法人說明會 | [官方 MOPS `t100sb02_1`](https://mopsov.twse.com.tw/mops/web/t100sb02_1)，直接 POST [官方查詢端點](https://mopsov.twse.com.tw/mops/web/ajax_t100sb02_1) | 上市／上櫃官方表格，來源 `mops:conference:t100sb02_1`。保留舉行日期／多日範圍、時間、地點、擇要訊息及官方簡報。查詢涵蓋近 90 日／未來 60 日涉及的年度，依日期範圍篩選，不宣稱未公告的未來事件。 |

事前轉讓申報只採官方 `t187ap12` 日報，不以事後持股異動或第三方資料替代，也不當成實際成交明細。

2026-10-01 直接取得官方頁面的 form action 與欄位，實際 POST `step=1`、`firstin=ture`（官方表單原值）、`off=1`、`TYPEK=sii/otc`、民國年度、空白月份及公司代號，取得完整年度表格。官方 JavaScript 僅在瀏覽器按每頁 100 筆顯示，原始回應已含全部列；adapter 不執行 JavaScript。核對兩層表頭、每列 12 個欄位、民國日期與日期範圍，schema 不符即 fail closed。每次回應上限 8 MiB、每表最多 10,000 列，沿用 MOPS HTTP 節流，無自動重試或第三方 fallback。

新站舊網址本次導向 `error.html`，採仍直接提供正式表單的官方 `mopsov.twse.com.tw` 入口，未繞過驗證或官方存取限制。部分市場／年度查詢失敗時回傳 `partial`，全部失敗時來源為 `unavailable`；UI 顯示官方法說會資料不可用及官方查詢入口，不虛構空清單代表沒有事件。

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
- 法說會日期／時間是舉行時間，不能充作公告時間；抓取時間也不能充作官方 publication time。
- 轉讓申報日報只有「出表日期」，`event_date_kind=report_snapshot_date` 明確標示資料日期，不當成逐筆申報／成交日。`record_kind=pre_transfer_declaration`、`is_actual_transaction=false`，預定股數不命名為已售股數，缺股數保留 null。
- UI 明示「不代表實際成交或已賣出」，不推定交易完成。有效轉讓期間亦不代表成交期間已有成交。
- 只保留官方回傳的 HTTPS 簡報連結，限制官方 MOPS／TWSE 文件主機，不自動下載。官方表格使用 POST `FileDownLoad` 時，保留已驗證的 action、hidden 欄位與回傳檔名，由使用者點擊官方簡報按鈕後提交原表單；不猜檔名，也不改造未驗證的 GET 連結。沒有連結明示尚未提供。
- 本階段未新增事件股價表現計算；既有日線僅作價格觀察，不提供「公告後 X 小時」分析或因果宣稱。

## 整合、持久化與快取

`GET /api/taiwan/events` 包含三類事件，既有 `event_types`／`symbol`／`symbols`／scope 在 limit 前篩選。`refresh=true` 明確刷新官方及 MOPS 快照。Stock Detail 透過同一 service 取得事件，保留整體及逐來源查詢狀態。

既有選股、風險快照、研究 context、Historical PIT、Strategy Lab 使用原有路徑。`get_events(include_mops=False)` 為預設，只有產品 API 與個股詳情啟用；`get_pit_events` 及 `get_cached_regulatory_snapshot` 不讀 MOPS cache。

資料取得不持有資料鎖。同時查詢不重複發出 MOPS 請求，刷新中可回傳明確 stale 保存資料。每來源完整驗證後才納入，部分來源失敗保留其舊事件與原取得時間，整體不可用不覆蓋磁碟保存資料。成功空日報與來源失敗分開，跨日仍保存歷史觀察。

Cache 使用 `official-only-v1` 來源政策。讀取磁碟或記憶體快取時，先以官方 source／event_type 白名單排除未核准資料，再做 model validation、TTL、stale fallback 或保存。舊政策快取強制刷新，保留官方事件；未核准來源不回傳、不重新保存，也不重新標示成 MOPS 資料。官方來源全失敗時，仍不能讀回未核准事件。

MOPS snapshot 在既有資料目錄內原子寫入並更新記憶體 cache，沒有新增 background writer／SSE producer。事件中心手動刷新後精確 invalidate 事件與 Stock Detail 的既有 TanStack Query 前綴。資料只寫 runtime directory，不放 release seed 或提交 Git。

## 驗證方式

離線 targeted tests 覆蓋三類標準化、12 類條款與未知條款、台北時間、無公告時間／未來時間、申報與成交分離、官方表格／POST 簡報、兩市場、跨年查詢、日期範圍、回應大小／HTTP 限制、空資料／schema 漂移、來源失敗、跨日保存、restart、舊快取來源排除、API 契約與 PIT 排除。前端測試涵蓋事件中心篩選、刷新、來源不可用、官方下載表單、共享證據與 Stock Detail。

UI smoke 使用可重現的合成資料，檢查深色桌面、淺色窄螢幕、官方簡報、申報提示與切換；不拿使用者資料當截圖 fixture。實際 adapter 只讀取官方公開資料，僅記錄 counts 與狀態，不提交原始資料。

本次未新增 release seed，release seed 建置 gate 不適用；提交前執行 privacy／secret scan，並跑現有 privacy tests。

2026-10-01 source-correctness 修正：官方年度 HTML 實際解析上市 2,421 筆、上櫃 924 筆，均保留 `available_at=null`；只解析 metadata／官方 POST 表單，未下載簡報。MOPS 測試 49 項及前端受影響測試 32 項通過，adapter／事件服務 targeted mypy 通過。其餘本次驗證結果列於 PR #61。

月營收既有 `test_month_end_refresh_covers_next_month_cutoff` 使用固定 2026-10-01 09:00 cutoff，實際 refresh 取得時間超過 cutoff 後會失敗。已在未修改的原始 repository 重現；保留為既有測試 blocker，不變更月營收實作或弱化 PIT 檢查。

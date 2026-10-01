# ToAlpha v2.1 Phase 1：法人統計與產業輪動

產品入口是側欄「大盤研究」(`/market-research`)。法人統計與產業輪動可直接使用；大盤寬度與估值僅保留入口，尚未提供計算。此頁讀取既有本機官方資料倉庫，不在 HTTP request 期間抓取外部來源。

## 分頁與 API 契約

| URL tab | 內容 | Phase 1 狀態 |
| --- | --- | --- |
| `institutional` | 法人統計 | 可用 |
| `rotation` | 產業輪動 | 可用 |
| `breadth` | 大盤寬度 | 預留 |
| `valuation` | 估值 | 預留 |

`date=YYYY-MM-DD` 指定目標日期，省略時採台北時間最新應有的已完成交易日，不會偷偷退回最近有資料的日期。法人窗口由 `window=5/10/20/45/60` 指定。日期與窗口都納入 TanStack Query key；資料健康更新完成會使相關查詢失效，頁面另提供「重新讀取」。服務不建立第二套持久化或結果快取。

- `GET /api/taiwan/institutional-statistics?date=YYYY-MM-DD&window=20`
- `GET /api/taiwan/industry-rotation?date=YYYY-MM-DD`

每個指標獨立帶有 `value`、`unit`、`source`、`date`、`as_of`、`status` 和 `coverage`。`date` 是查詢目標，`as_of` 是實際取得資料的最後日期；`coverage_days` 是有觀察值的天數，完整性另由筆數與 `status` 判斷。UI 點選狀態可展開來源、日期、預期／實際筆數與缺日。

## 法人數量與完整性

範圍為目前 security master 支援且有效的上市／上櫃股票與 ETF，不宣稱是交易所公布的法人買賣金額。外資、投信與自營商的 raw 淨買賣超沿用 `institutional_store` 的 shares。衍生計算放在 `enrichment/factors.py`，張數固定為股數除以 1,000，無買賣金額估算。

窗口按交易日／已確認的休市證據決定。未確認且缺資料的平日保留為缺口，不能藉跳過缺日取得完整 N 筆；已觀察到的週六補班交易日亦保留。舊 partition 存在不代表完整，交易日不一致、非官方可用狀態、數值缺失及 discrepancy 資料不納入可靠觀察值。

買賣超可呈現部分觀察值的合計，明確標示 `partial`，完全沒有觀察值時為 null／`unavailable`。完整性需要窗口內每個日期與目前支援標的都有官方記錄，且包含 TWSE 與 TPEx。缺列不補零，包括可能沒有交易或剛上市的標的。

成交量占比為「同一批標的、同一批日期的法人淨買賣超股數／成交股數」。分子、分母先依 symbol＋date 配對；缺成交量不借用其他日期，零分母為不可用。這是帶正負號的淨流量比率。

連買／連賣從目標日期往回看，正值為買、負值為賣、已觀察到的零值終止連續天數。缺日、缺數值或市場資料不完整都中斷；目標日不完整時天數為不可用。達到窗口上限的天數是下界，UI 顯示「至少，達窗口上限」。Screener 支援 `streak_investor`、`streak_direction` 與 `streak_min_days`（1–60），只在使用條件時批次載入，並排除過期行情列。條件可儲存／載入既有選股策略，結果可展開來源與覆蓋。

## 產業輪動口徑

產業分類沿用 security master，範圍為目前有效的支援股票，ETF 排除，未分類另列。RS 延用既有 `industry_intelligence` 定義：產業等權報酬減去全市場股票等權報酬；5D／20D 分別需要 6／21 個交易日價格，缺中間交易日不滑動窗口。使用原始收盤價，未排除除權息影響。

每日產業成交值占比＝該產業成交值／全市場股票成交值。20D 平均採最近 20 個交易日每日占比的算術平均。當日占比減去 20D 平均，再乘以 100，得到 `turnover_share_delta_pp`，單位是百分點。任一標的缺成交值會使當日市場分母不可用，不使用不完整市場作為正常分母；仍可顯示其他完整日期的平均，標示 `partial`。

四象限 X 軸為 20D RS（UI 轉成百分比），Y 軸為成交值占比變化（百分點）。只繪製兩個指標皆 `available` 的產業，其餘數據與覆蓋率保留在表格。

## 限制與相容性

- 本功能使用目前有效的 universe；指定舊日期是目前標的的回看，不是 Historical PIT。Strategy Lab Historical PIT 未修改。
- 對未知休市日與缺失標的採保守完整性判斷，可能呈現 `partial`；需既有資料更新流程補足資料或休市證據。
- 不新增下載／回補流程，不變更既有 Parquet schema、refresh 行為及舊五日因子 consumer。
- Router、導航 metadata、API client、query keys 的修改僅限本功能 wiring。Dashboard 與 TaiwanStockDetail 未修改。
- 回滾可 revert 本 Phase 的程式碼提交，不涉及使用者資料遷移。

## 驗證

固定樣本涵蓋五種窗口、股／張、買／賣／零流量、相同日期成交量分母、缺日、缺交易所、假日與週六、無資料、RS、每日平均占比與百分點差值。API 測試涵蓋參數錯誤、成功、無資料及讀取失敗。前端測試涵蓋窗口／日期／單位切換、來源與狀態、搜尋、錯誤重試、預留 tab、散點排除與 Screener 條件重置。

瀏覽器驗證使用隔離的合成資料及 production build，檢查實際路由、導航、資料 API、深淺色、窄屏與不可用狀態。這些固定資料驗證不代表正式資料已回補完整。

以下是新頁面的合成資料畫面，原產品沒有此頁面。測試用應用外殼未連接即時 SSE，截圖中的連線提示不屬於資料指標狀態。

![法人統計，深色](images/market-research-phase1/institutional-dark.png)

![產業輪動，淺色](images/market-research-phase1/rotation-light.png)

![產業輪動，窄屏](images/market-research-phase1/rotation-narrow.png)

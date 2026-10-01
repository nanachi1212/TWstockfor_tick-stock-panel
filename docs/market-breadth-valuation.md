# ToAlpha v2.1 Phase 2：大盤寬度與估值

此功能提供描述性的大盤研究。價格來自既有 `TaiwanDailyStore`，資格證據來自
`PitUniverse` 的官方 census 與歷史 classification，交易日同時參照既有
`TaiwanTradingCalendar`、官方 census evidence 及 `TaiwanBenchmarkStore`。
不查今天的 security master 回填歷史資格，也不計算 precise index contribution points。

## API 與大盤研究 tab 接入

- `GET /api/taiwan/market-research/breadth-valuation?market=composite&days=20&as_of=2026-09-30`
  僅讀本地資料，`market` 支援 `TWSE`、`TPEX`、`composite`，`days` 為 1–60 個已觀測交易日。
- `POST /api/taiwan/market-research/breadth-valuation/refresh` 明確更新兩個交易所最新個股估值。
  每個交易所一個限流請求，寫入既有 `TaiwanFundamentalStore`；失敗的交易所保留原紀錄。
  改變的估值以內容雜湊 revision 保存，不把抓取時間推定為發布時間。
- React 元件 `MarketBreadthValuationTab` 接受 `{ asOf?: string, market?: 'TWSE' | 'TPEX' | 'composite' }`。
  Agent A 的大盤研究 tab shell 可直接掛載這個元件。未傳入的 context 提供元件內選擇器；
  傳入則跟隨 shell 的 context。沒有新增路由、導航或第二個大盤研究頁。
- 契約版本為 `contract_version: 1`。API client 經 `lib/api.ts`，query key 集中於 `QK`，
  包含日期、市場與歷史長度。官方估值更新後使所有相關 query 失效，日 K 更新沿既有 daily SSE 失效。
  不新增後端長期記憶體快取，計算只用單次請求內的價格索引與公司行動窗口快取。

## 寬度計算與 coverage

- MA20、MA60、MA240：當日 normalization 後收盤價嚴格大於含當日在內的 N 個交易日平均價，
  回傳 0–1 比例。相等不計入上方。
- 52 週新高／新低：涵蓋之前 52 個自然週，以窗口起點之前最近交易日為邊界；
  當日調整後 high／low 嚴格突破之前窗口 high／low。至少有完整年度窗口才可納入。
- A/D Net：當日調整後 close 與前一個交易日 close 比較，漲為 +1、跌為 -1、平為 0。
  A/D Line 累計本次查詢窗口的 A/D Net，`ad_segment_start` 明示區段起點。無法比較的日子
  回傳 null，下一個可比較日重起區段。不同起點的 A/D 絕對水位不可直接比較。
- 每個指標各自回傳 `included_count`、`excluded_count`、`excluded_reason_counts`、`coverage`、`status`。
  同一股票可能納入 MA20、排除 MA240。零樣本的 coverage/value 為 null，合法的 0 則保留。
- 分母是當日可觀測日 K 標的與當日已驗證且出現在 census 的普通股聯集，排除數為分母內
  無法計算的標的數。已驗證的非普通股排除；資格未確認的日 K 樣本仍只能作可觀測研究。
  合併市場缺少一方樣本時透過 `missing_markets` 標記，其指標最多為 partial。
- 新上市／歷史不足、缺交易日日 K、無效價、交易日窗口未確認、非普通股、公司行動證據不足、
  不可比較公司行動，均有獨立原因。未知交易日不以自然日或下一個有效日替換。
- 只呼叫現有 `CorporateActionStore.read_verified_coverage` 與 `adjust_prices_as_of`。
  缺少窗口涵蓋證據不能視為「沒有公司行動」；事件不可靠時標的 excluded/incomparable，
  不自行實作調整公式，也不改寫 raw 日 K。

`historical_eligibility_status=verified` 只表示分母內標的歷史資產資格有既有證據；
**不代表完整歷史普通股 universe**。停牌、已下市或來源漏列標的可能根本不在日 K/census。
因此永遠保留 `universe_complete=false`、`universe_status=observed` 及
「依可觀測日 K 計算的研究統計」。全市場完整涵蓋率未知，畫面中的 coverage 是樣本可計算比例。

## 估值與時間契約

官方來源為 TWSE `exchangeReport/BWIBBU_ALL` 與 TPEx `tpex_mainboard_peratio_analysis`，
重用 `TaiwanOfficialFundamentals` 的個股解析。TWSE、TPEx 分別計算 PE、PB、殖利率中位數；
composite 將**同一交易日**兩個交易所個股合併後取中位數，少一方則不可計算，禁止混日。
這些值不等於市值加權市場估值或官方指數本益比。

PE/PB 缺值、非有限值與非正值排除，虧損或缺 PE 不當成 0；殖利率 0 是合法資料。
估值分母為當日官方估值紀錄與日 K 樣本聯集，明確計數沒有估值紀錄的樣本。
Percentile 比較查詢日以前已保存的每日同口徑中位數，至少需要 20 個交易日；
相同值採 midrank，平坦歷史為 50%。回傳樣本數、起訖日；不含未來日期或當日，
同日多個 revision 只取最後抓取的一筆。歷史樣本組成可變，仍屬描述性研究。

估值紀錄保存 source、source_url、period_end（API as_of）、retrieved_at，available_at
沿用可驗證的來源契約。這兩個官方端點只有交易日，無法證明首次發布時間，因此目前
`available_at=null`、`publication_time_status=unverified`，只提供 `descriptive_history`，
`strategy_lab_eligible=false`，沒有接入 Strategy Lab 或聲稱 Historical PIT。
舊日 K schema 沒有保存來源抓取時間，不能用檔案 mtime、quote_ts、census 抓取時間或
generated_at 假造：價格 retrieved_at 為 null，狀態明示 unavailable；資格證據抓取時間
獨立放在 `eligibility_retrieved_at`。歷史 publication time 未確認不妨礙描述性研究。

首次更新只建立最新官方估值快照，歷史 percentile 會顯示「歷史不足」，不回填猜測的歷史估值。
明確指定日期沒有同日估值時回傳不可用；寬度若只有較早快照，保留實際 as_of 並標 stale。
讀取時不建立網路 fallback、不自動回算或覆蓋既有 runtime data。

## 驗證與限制

固定樣本測試涵蓋 MA 數值、IPO、52 週窗口、缺日、無效價、非普通股、verified 資格不等於
完整 universe、公司行動 normalization 與失敗隔離、A/D 中斷、PE 缺值／非正值、同日 composite、
percentile ties／未來日期／revision、官方來源錯誤與持久化、空／錯 API 及零 HTTP GET。
前端測試涵蓋 context 切換、coverage／排除／provenance、加載、空、錯誤重試、stale、更新禁用與失效。

Agent A 的 shell 尚未存在時，這個 PR 只交付可掛載 tab component/API，最後路由掛載待 Agent A。
已有 price/census/classification/action coverage 決定實際可用範圍；缺少證據會明確降級，不宣稱資料完整。

## 元件畫面證據

以下為後端模型產生的固定測試樣本，並非正式市場數據。未新增產品頁面，暫時預覽 harness 已在驗證後移除。

![桌面淺色](images/breadth-valuation-desktop.png)

![桌面深色與逐日歷史](images/breadth-valuation-dark.png)

![390px 窄螢幕](images/breadth-valuation-mobile.png)

## 本次驗證結果

- 後端 targeted regression：205 項通過，含 PIT universe、census、fundamentals、daily store、market intelligence、corporate actions 與 PIT adjustment。
- 前端完整測試：381 項通過；最後視覺樣式修正後，元件 5 項定向測試再次通過。
- Ruff、8 個受影響後端模組 mypy、TypeScript、production build、git diff --check 與 changed-file privacy／secret scan 通過。
- Headless Edge：桌面淺色、深色、390px、日期／市場切換、更新、歷史展開、無橫向整頁溢出及無 page error 通過。
- 官方 live smoke：2026-09-30 TWSE 1,083 筆、TPEx 887 筆估值，可解析且 available_at 均缺失。
- 既有本地資料只讀 smoke：2026-09-29 的 20 個交易日快照可計算全部寬度，MA20／60／240 樣本涵蓋率約 82.3%／78.3%／69.2%，新高新低約 69.2%，A/D 約 87.4%。

本機這次完整 20 日研究查詢約 207 秒，單日驗證約 22 秒，為實際觀測到的效能限制，沒有宣稱即時查詢速度。
公司行動來源涵蓋證據只到 2026-09-29，因此 2026-09-30 寬度 fail-closed。未保存同日估值時仍不可用，首次更新沒有足夠歷史 percentile。
本次不合併、不觸發額外全面 Codex Review；CI 狀態以 Draft PR 的實際結果為準。

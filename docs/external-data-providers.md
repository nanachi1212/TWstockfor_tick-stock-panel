# External data providers

本文件記錄 2026-10-05 依官方文件與公開端點重新驗證的結果。TWSE、TPEx、MOPS、PIT universe、corporate actions、institutional、margin 與 TradePlan 仍是台股 primary truth；下列來源只補盤中、全球環境或第二來源交叉檢查。

| Provider | Auth / free tier | Coverage and cadence | Product use | Restrictions and decision |
| --- | --- | --- | --- | --- |
| Fugle MarketData | `FUGLE_API_KEY`。官方 WebSocket 文件要求連線後以 API key 驗證。公開文件未列出可依賴的免費訂閱檔數、連線數或再散布額度，因此限制標為未驗證，實作只訂閱目前個股與 Dashboard 少量標的。 | 台股即時 WebSocket。`aggregates` 官方 schema 包含 `lastPrice`、`bids`、`asks`、`total.tradeValue`、`total.tradeVolume`、`total.tradeVolumeAtBid`、`total.tradeVolumeAtAsk`、`total.time` 與 `lastUpdated`。 | Beginner Technical Panel 的盤中價量、五檔與內外盤。 | 不作日線、PIT、法人、MOPS、公司行動或 TradePlan 來源。缺 key 或過期即 unavailable/stale，禁止以 OHLCV 或 tick rule 推估內外盤。 |
| Frankfurter v2 | 公開服務免 key。官方 FAQ 表示沒有月／日 quota，但會防濫用限流；高量使用應快取或自架。 | 223 currencies、104 個央行／官方來源，通常每日更新。CBC provider 標示 1993 至今、19 currencies。公開端點已實測可回 USD/TWD、USD/JPY、USD/EUR，JPY/TWD 與 EUR/TWD 由同日 CBC cross-rate 確定性換算。 | Dashboard、Daily Brief、個股研究的 FX context；每日快取。 | 不是即時交易匯率。官方說商用可用，但各 provider 的原始資料仍受各自條款約束；UI 與 evidence 保留 `Frankfurter/CBC` provenance。 |
| FRED / ALFRED | `FRED_API_KEY`。FRED v1 observations API 要求 key。官方未承諾固定 rate limit，應做全域每日快取。 | 第一版只用 `FEDFUNDS`、`DGS2`、`DGS10`、`T10Y2Y`、`CPIAUCSL`、`PCEPILFE`、`UNRATE`、`INDPRO`。API 支援 `realtime_start`、`realtime_end` 與 `vintage_dates`。 | UI-only macro context；Advanced 顯示少量原始 series。 | 保留 `observed_date`、`realtime_start`、`realtime_end`、`retrieved_at`、`series_id`。未來若進 backtest/OOS，呼叫端必須指定 historical vintage；不得把今日修訂值當成當時已知。產品須顯示 FRED attribution/terms link，且個別 series 可能有額外版權。 |
| FinBridge | `FINBRIDGE_API_KEY`，Bearer。免費帳戶 10 calls/day、最近 4 個 fiscal years、130 個交易日；live upstream lookup 另有 20/day。 | 官方 OpenAPI 提供 `/api/v1/companies/{market}/{symbol}/financials`、`valuation`、`peers`、`prices`，`market=tw` 支援台灣。資料為 nightly snapshot，可能延遲或被重編。 | 只在目前個股頁做 fundamentals/valuation/peers secondary cross-check。 | 不批次掃描股票，不覆寫官方值。FinBridge 來源頁指出不同上游各有授權範圍，第三方再散布／商用權不應從 API 可讀性推定；UI 只顯示比對結果與 provenance。 |
| OpenFIGI | Mapping API。無 key 25 requests/minute、每 request 10 jobs；有 key 25 requests/6 seconds、每 request 100 jobs。 | FIGI / ticker / exchange identifiers；可研究 2330.TWSE、TSM ADR、ETF 對應。 | 第二階段 identifier mapping 候選。 | 本輪不實作。先建立 symbol mapping contract、人工驗證台灣 mappings 與快取，再評估導入。 |
| SEC EDGAR | data.sec.gov 不需 key；需宣告可識別的 User-Agent，SEC fair-access 指引目前上限 10 requests/second。 | 美國 filings、company facts、submissions。 | 第二階段供 TSM ADR 與美國供應鏈官方 filings。 | 本輪不實作。需先定義 CIK/ADR 對應與 filing PIT 語意；遵守 SEC fair-access，不建立美股研究平台。 |
| XOOMAR | 公開 markets 文件標示無 key 10 requests/minute、免費 key 30 requests/minute，回應帶 source attribution。 | 聚合多個美國公開資料集，可補 short interest、CFTC/Fed 等 context。 | SEC 以外的便利 secondary source 候選。 | 本輪不實作；先逐資料集核對原始來源、授權與更新頻率，不能把聚合結果升格為 primary truth。 |
| Econdb | 官方 Swagger 可見 macro、shipping、ports/container traffic API；公開頁未能驗證穩定的免費 quota、商用／再散布條款。 | 全球總經、貿易、港口與航運 context 候選。 | 航運、電子出口與景氣 context 的研究候選。 | 本輪不實作。在 auth、quota、歷史 coverage 與授權取得官方明確證據前不接入產品。 |

## Implementation contracts

每個已實作 provider 回傳 `source`、`status`、`as_of`、`retrieved_at`、`freshness`、`data`、`error_reason`。`status` 使用 `available`、`partial`、`unavailable`、`stale`；unavailable 不得補零。外部來源失敗不得使台股核心 API 失敗。

快取策略為 Frankfurter 每日一次、FRED 全域每日一次、FinBridge 每個目前個股每日一次。Fugle 為 lazy WebSocket cache，只訂閱實際顯示中的少量標的。所有 key 只由環境變數讀取，不寫入日誌、API response、cache 或 repository。

## Official references

- Fugle WebSocket getting started: <https://developer.fugle.tw/docs/data/websocket-api/getting-started/>
- Fugle aggregates schema: <https://developer.fugle.tw/docs/data/websocket-api/market-data-channels/aggregates/>
- Frankfurter v2 and FAQ: <https://frankfurter.dev/>
- Frankfurter provider catalogue: <https://frankfurter.dev/providers/>
- FRED observations and vintages: <https://fred.stlouisfed.org/docs/api/fred/series_observations.html>
- FRED API terms: <https://fred.stlouisfed.org/docs/api/terms_of_use.html>
- FinBridge docs / OpenAPI / sources / terms: <https://www.gronox.kr/docs>, <https://mcp.gronox.kr/api/v1/openapi.json>, <https://www.gronox.kr/sources>, <https://www.gronox.kr/terms>
- OpenFIGI API: <https://www.openfigi.com/api/documentation>
- SEC data APIs and fair access: <https://www.sec.gov/edgar/sec-api-documentation>, <https://www.sec.gov/about/developer-resources>
- XOOMAR markets: <https://xoomar.com/markets>
- Econdb Swagger: <https://developers.econdb.com/swagger/>

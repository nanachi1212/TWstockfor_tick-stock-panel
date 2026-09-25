# Taiwan market data sources

The `taiwan` provider uses one existing provider seam with the following policy:

1. TWSE and TPEx official endpoints are authoritative for the security directory,
   daily close quote, and daily K-line.
2. FinMind and Yahoo remain supplements and fallbacks for coverage unavailable from
   official endpoints. Fallback records are labelled `third_party_fallback`.
3. No third-party value silently overwrites a successful official value.

TWSE Security Master and official quote/history requests use the existing
`httpx` transport with normal certificate verification. This avoids a Windows
Python 3.13 `urllib` system-trust chain incompatibility without weakening TLS.

TPEx valuation remains fail-closed on TLS errors. On 2026-08-31 the same Python
3.13 runtime verified the endpoint with production, plain, and explicit-certifi
`httpx` clients without redirects; no certificate bypass or code change was needed.

TWSE rule profiles for 00631L and 00632R are explicit audited product entries,
not ticker/name heuristics. Official ETF product pages establish domestic-index
leveraged/inverse identity; TWSE trading rules then apply `10% * abs(multiplier)`.
This rule registry does not populate Phase 6E structured multiplier/direction fields.

## Official endpoints

- TWSE companies: `opendata/t187ap03_L`
- TWSE ETFs: `opendata/t187ap47_L`
- TPEx companies: `mopsfin_t187ap03_O`
- TWSE daily quote: `exchangeReport/STOCK_DAY_ALL`
- TPEx daily quote: `tpex_mainboard_quotes`
- TWSE monthly K-line: `exchangeReport/STOCK_DAY`
- TPEx monthly K-line: `afterTrading/tradingStock`
- TWSE institutional: `fund/T86`
- TPEx institutional: `insti/dailyTrade`
- TWSE margin/short: `marginTrading/MI_MARGN`
- TPEx margin/short: `margin/balance`

TPEx ETF identity is retained from the official ISIN directory. Product
classification remains `data_insufficient` until an official structured metadata
source is integrated; code/name heuristics are not used to fill this gap.

## Normalized contract

- `volume` is shares/units. TPEx monthly `成交仟股` is multiplied by 1,000.
- `amount` is TWD. TPEx monthly `成交仟元` is multiplied by 1,000.
- ROC dates such as `115/08/30` and `1150830` normalize to `2026-08-30`.
- Daily `timestamp` is explicitly `13:30 Asia/Taipei`.
- Missing values (`""`, `-`, `--`, `N/A`) become null; explicit zero stays zero;
  malformed numeric input raises a schema/parse error.

Every normalized Taiwan record carries `provider`, `source`, `source_url`,
`retrieved_at`, `trade_date`, and `status`. Supported status values used here are
`official`, `third_party`, `third_party_fallback`, `stale`, and `error`/schema errors
through raised exceptions. Weekend gaps alone do not make Friday's close stale.

Institutional source quantities are already shares and are not multiplied. Margin
and short quantities are official lots and normalize to shares with a 1,000
multiplier. Dealer net follows the existing contract: proprietary net plus hedge
net; official component and total nets are validated and discrepancies remain
visible instead of being overwritten.

Institutional and margin adapters use the same strict value parser as other Taiwan
official providers: missing tokens remain missing errors for required fields,
explicit zero remains zero, and malformed values raise. Each record includes its
official provider and dataset provenance. FinMind currently supplies price data
only, so there is no reliable institutional/margin fallback; official failures are
raised instead of returned as empty success. Rolling flows and ratios remain in
`app.taiwan.enrichment.factors`, outside raw providers.

## TPEx quote and historical daily separation

TPEx publishes multiple official close-style datasets with different coverage.
They must retain separate provenance and must not overwrite each other:

- `tpex_mainboard_quotes` is the latest official close-style quote source used by
  `get_realtime`. Its Swagger description is "上櫃股票收盤行情"; the OpenAPI does
  not provide a historical date parameter or explicitly enumerate every included
  or excluded trading type.
- `afterTrading/tradingStock` is the reconstructable official monthly historical
  daily-bar source used by `get_daily`. TPEx labels its quantity fields as
  `成交仟股`/`成交仟元`, and the official page states that the data excludes TPEx
  block trades. It remains the canonical Taiwan historical source for indicators
  and backtests.
- `tpex_mainboard_daily_close_quotes` is a broader official daily close dataset.
  It is diagnostic here, not an automatic replacement for historical bars.

The datasets are not contractually required to have identical daily volume. On
2026-08-28, TPEx official records for 6488 reconciled as follows (retrieved
2026-08-30 Asia/Taipei):

```text
tpex_mainboard_quotes                         11,084,000 shares
盤中零股                                         410,427 shares
盤後定價                                          26,000 shares
盤後零股                                           2,113 shares
non-block total                                 11,522,540 shares
tradingStock 成交仟股 (rounded)                     11,523 x 1,000 shares
鉅額交易                                         250,000 shares
tpex_mainboard_daily_close_quotes total        11,772,540 shares
```

The corresponding non-block amount was exactly TWD 11,236,724,997, which the
monthly dataset reports as `11,236,725` thousand TWD. This proves an official
dataset coverage and thousand-unit rounding difference for that observation; it
does not justify a hard-coded reconciliation rule for every date or security.
Consumers must continue to distinguish `official_quote` from
`official_daily_kline` through `source`, `source_url`, `retrieved_at`, and
`trade_date`.

## Historical volume consumers

Taiwan official, FinMind, and Yahoo daily adapters all enter storage with
`volume` normalized to shares/units. Parquet persistence and enriched data keep
that number unchanged. Generic consumers resolve the unit from the canonical
symbol: `.TWSE`/`.TPEX` matrices use `shares`; existing CN matrices retain
`lots` (100 shares per lot).

Turnover is a percentage against `float_shares`, which is a share count:

```text
Taiwan: volume(shares) / float_shares(shares) * 100
CN:     volume(lots) * 100 / float_shares(shares) * 100
```

Relative-volume indicators are unit-invariant. VWAP first converts volume to
shares; matrix cache metadata preserves the resolved unit across build, load,
slice, copy, and live append paths. Mixed Taiwan/CN matrices fail closed because
one matrix cannot truthfully expose a single `volume_unit` for both contracts.
The current execution engine has no absolute-volume participation cap; volume is
only used to identify non-trading rows, while slippage remains a configured bps
model.
# Point-in-time fundamentals

Taiwan company fundamentals use official TWSE/TPEx MOPS open-data records and
keep every record's provider, source URL, retrieval time, status, units, and
revision evidence. The time fields are intentionally distinct:

- `period_end` is the accounting period end; it is never an availability date.
- `published_at` is the verified publication timestamp, when a source provides one.
- `available_at` is the first verified timestamp the record could be consumed.
- `retrieved_at` is when this application fetched the record.

Historical queries are strict: a record is visible only when
`query_at > available_at`. A missing `available_at` is never inferred from the
period end or a date-only `出表日期`; the record is retained as
`data_insufficient` and excluded from backtests. Revisions are stored separately
and an as-of query selects the latest eligible revision rather than today's
latest value.

Official financial amounts reported in `仟元` are normalized to TWD. Missing
values remain null, explicit zero remains zero, and malformed values are errors.
Company monthly revenue and statements are `unsupported` for ETFs.

Share-capital fields are not interchangeable: `total_shares`, `issued_shares`,
`float_shares`, and monetary `capital` retain their own meanings. In particular,
issued shares or capital divided by par value are not used as historical float
shares without an authoritative source.

Phase 6H verified that TWSE/TPEx company profiles expose current issued common
shares, paid-in capital, and par value, but only with date-level report metadata.
These fields are retained as a current/reference `share_capital_record` with
`available_at=None`; `total_shares` and `float_shares` remain null. Official
capital-event sources did not provide a uniform exact-time, effective-date, and
revision chain suitable for historical ingest. Taiwan historical turnover now
fails closed when no verified PIT float denominator exists. The source matrix and
live samples are recorded in
[`taiwan-share-capital-phase-6h.md`](taiwan-share-capital-phase-6h.md).

## Official fundamentals availability investigation (Phase 6C.1)

Investigation on 2026-08-31 distinguished an official timestamp from a safe
end-to-end mapping to an OpenAPI record. MOPS historical material information
exposes exact `發言日期` and `發言時間`, and the official financial-report
document service exposes exact `上傳日期` timestamps. The aggregate OpenAPI
datasets used by the provider, however, do not carry a material-information
serial number, document filename, or another stable identifier that proves
which announcement/upload created each value or revision.

| Dataset | Official evidence | Classification | Production policy |
| --- | --- | --- | --- |
| Monthly revenue (`t187ap05_L`, `mopsfin_t187ap05_O`) | Date-only `出表日期`; some issuers separately publish timed material information, but this is not universal | C: insufficient record-level evidence | Keep `published_at`/`available_at` null |
| Financial statement aggregate (`t187ap06/07`) | Exact PDF upload timestamps exist in the MOPS document service; correction history has a separate official query | C for the aggregate record/revision mapping | Keep aggregate values unavailable until a stable report/revision join exists |
| Valuation (`BWIBBU_ALL`, `tpex_mainboard_peratio_analysis`) | Trade date and current HTTP refresh metadata only; no historical per-record finalization timestamp | C | Keep `available_at` null |
| Dividend board resolution | MOPS material information has exact timestamp | A for the announcement event, C for the aggregate-row join | Do not copy the timestamp by subject-text matching |
| Dividend ex-date/basis-date announcement | MOPS material information has exact timestamp | A for the announcement event, C for the aggregate-row join | Preserve lifecycle events separately when a stable key is available |
| Dividend payment | Timed announcements exist for some issuers, not a universal keyed source for current aggregate rows | C | Keep missing lifecycle fields null |

Historical evidence included:

- `2330`: July 2026 revenue material information at `2026-08-10
  13:51:09+08:00`; Q2 financial-report PDF upload at `2026-08-14
  13:59:44+08:00`; board dividend resolution at `2026-08-11
  18:53:34+08:00`; ex-date announcement at `2026-08-11 19:01:29+08:00`.
- `6488`: Q2 financial-report board approval at `2026-08-04
  15:16:23+08:00` and PDF upload at `2026-08-07 15:22:28+08:00`;
  dividend resolution and basis-date announcements at `2026-03-03
  16:05:53+08:00` and `16:06:25+08:00`. No equivalent July-revenue
  material-information event was found.
- `2881`: Q2 financial-report board approval at `2026-08-20
  16:48:04+08:00` and PDF upload at `2026-08-28 14:09:26+08:00`.
  Its July disclosure was a consolidated self-reported profit/loss event, not
  an unambiguous match to the monthly-revenue aggregate.

These timestamps prove that the official event/document streams can express
exact times. They do not yet prove that an aggregate value was public at the
same instant. HTTP `Date` is response time and aggregate `Last-Modified` is a
resource/cache timestamp; neither is historical record-level availability.
Likewise, a statutory filing deadline does not prove that a particular issuer
filed successfully by that time. No date-level next-trading-day fallback is
therefore adopted.

The future integration boundary should accept only an official stable join,
such as issuer + report period + report/revision identifier. Subject-text
matching is not sufficient. Once joined, the existing strict rule remains:
`query_at > available_at`; equality is unavailable. Original and corrected
reports must keep their own upload/announcement timestamps.

Phase 6G re-verified this boundary against the live MOPS financial-report
document service and both official OpenAPI schemas. Exact 2026 Q1/Q2 document
timestamps were reproducible for 2330, 6488, and 2881, but the aggregate rows
still exposed no document or revision identifier. Monthly revenue and valuation
also exposed no new stable exact-time identity. No production availability was
upgraded; the evidence and rejection matrix are recorded in
[`taiwan-fundamentals-availability-phase-6g.md`](taiwan-fundamentals-availability-phase-6g.md).

## Dividend lifecycle stable event ingest (Phase 6D)

The official source is the MOPS historical material-information service:

- search: `https://mops.twse.com.tw/mops/api/t05st01`
- detail: `https://mops.twse.com.tw/mops/api/t05st01_detail`
- user-facing query: `https://mops.twse.com.tw/mops/web/t05st01`

The search result supplies the detail parameters `marketKind`, `companyId`,
`enterDate`, and `serialNumber`. Their deterministic encoding
`marketKind/companyId/enterDate/serialNumber` is the event and revision
identity. The detail response supplies the exact official speech date/time,
subject, and structured description. Therefore these events use
`availability_policy=exact_timestamp`, `available_at=event_timestamp`, and
`availability_confidence=verified`. Point-in-time queries remain strict:
an event is visible only when `query_at > available_at`.

Classification is deliberately conservative. Phase 6D supports an explicit
issuer-level board dividend-resolution subject, an explicit ex-date subject,
and an explicit basis-date/distribution-record subject. Subsidiary and preferred
share subjects are excluded from the ordinary-share stream. Generic shareholder
meeting resolutions, payment announcements, paid status, corrections, and
subjects that do not identify one lifecycle stage remain unresolved; no fuzzy
matching is used.

The material event and the existing TWSE `t187ap45_L` / TPEx
`mopsfin_t187ap39_O` dividend aggregation remain separate records. Aggregation
rows can validate amounts and dates, but their date-only metadata and lack of a
material-event serial identity do not authorize copying an exact timestamp.
Phase 6D parses amounts only when they are stated in the official event detail.
Missing event fields remain null, while explicit zero remains zero.

Live official read-back on 2026-08-31 verified:

- `2330.TWSE`: board resolution
  `sii/2330/1150811/3` at `2026-08-11T18:53:34+08:00`, cash dividend
  `7.0`, stock dividend `0.0`; ex-date announcement
  `sii/2330/1150811/4` at `19:01:29+08:00`, ex-date `2026-12-10`,
  basis date `2026-12-16`, payment date `2027-01-07`.
- `6488.TPEX`: board resolution
  `otc/6488/1150303/1` at `2026-03-03T16:05:53+08:00`, cash dividend
  `5.7`, stock dividend `0.0`; basis-date announcement
  `otc/6488/1150302/2` at `16:06:25+08:00`, ex-date `2026-07-16`,
  basis date `2026-07-22`, payment date `2026-08-14`. The identity retains
  the official `enterDate` even where it differs from the displayed speech date.
- `2881.TWSE`: ordinary-share board resolution
  `sii/2881/1150430/7` at `2026-04-30T18:06:38+08:00`, cash dividend
  `4.25`, stock dividend `0.0`; basis-date announcement
  `sii/2881/1150612/15` at `2026-06-12T17:50:31+08:00`, ex-date
  `2026-07-01`, basis date `2026-07-07`, payment date `2026-07-31`.

ETF company dividend events are outside this stream. For example,
`0050.TWSE` returns no company lifecycle events without making a network
request; ETF distributions remain a separate structured-data phase.

Taiwan Market Contract v1.1 should add `period_start`, `period_end`,
`published_at`, `available_at`, `revision`, `normalized_unit`, `raw_unit`, an
availability evidence URL/identifier, and a policy type (`exact_timestamp`,
`date_level_conservative`, or `insufficient`). Contract v1 remains unchanged.

# ETF structured data foundation (Phase 6E)

ETF data is a separate domain from company fundamentals and company dividend
events. The security master retains identity and stable classification only;
daily NAV, AUM, units, distributions, and holdings must not be stored there.

## Official dataset classification

| Class | Dataset | Fields and units | Frequency / history | Decision |
|---|---|---|---|---|
| A | TWSE OpenAPI `opendata/t187ap47_L` (基金基本資料彙總表) | product name/type, benchmark, inception/listing dates; `發行單位數/轉換數` retained as issued units | current report with date-only `出表日期`; no proven publication time | profile is production-usable; current issued units are retained with `available_at=None` and are historically unavailable; outstanding units remain unknown |
| B | TWSE e添富 NAV chart and `ETF/etfDiv` report | dated NAV/premium-discount observations; ETF ex/basis/payment dates and distribution amount per unit | historical values are queryable, but no record publication timestamp, stable revision identity, or approved availability rule exists | historical reference only; not parsed into PIT history |
| B | TPEx ETF information-center product service and monthly report | recent per-unit NAV, four recent payments, current/month-end scale fields | recent or monthly partial history; no exact record availability or revision identity | reference only; capability remains `data_insufficient` |
| C | Issuer holdings pages or top-holding displays | holdings/weights vary by issuer and may be partial | completeness, cash/derivative coverage, schema, and availability are not consistently proven | no production holdings ingest |

## Semantic boundaries

- `nav` means final per-unit NAV; `estimated_nav`/iNAV is a separate field;
  neither is substituted for market close.
- Premium/discount is calculated as `market_price / nav - 1` only when both
  values use the same date and compatible currency/unit semantics.
- AUM (TWD), outstanding ETF units (units), issued units, and beneficiary units
  are distinct concepts. No value is inferred from `AUM / NAV`.
- Distribution uses TWD per ETF unit and is not a company dividend lifecycle.
- Holdings require an explicit `full`, `partial`, or `unknown` coverage marker;
  a top-N list is never a complete portfolio.
- Missing markers become `None`; numeric zero remains a real zero; malformed
  numbers are schema/parse errors.
- Historical selection is strict: `query_at > available_at`. Date-only source
  metadata does not create an `available_at`, so it cannot enter backtests.
- Leveraged/inverse direction and multiplier remain absent unless an explicit
  official field proves them; ticker/name suffixes are not evidence in this ETF
  structured-data domain.

Phase 6I verified TWSE historical NAV chart responses for `0050`, `00631L`, and
`00632R`, and TWSE distribution rows for `0050` back to 2020. It also verified
the genuine TPEx ETF `006201` through the official ETF information-center API.
These sources remain reference-only because none supplies an exact publication
timestamp, approved `available_at`, and stable revision identity. No ETF history
provider or store was added. The endpoint and rejection evidence is recorded in
[`taiwan-etf-history-phase-6i.md`](taiwan-etf-history-phase-6i.md).

## Current/reference product surface (Phase 6J)

The authoritative machine-readable capability matrix is
`app.taiwan.current_data.CAPABILITIES`. It separates domain status from intended
usage through these values:

- `current_reference`: may be shown as the latest official reference value but
  is not eligible for historical queries;
- `historical_reference`: official historical evidence exists, but there is no
  production ingest or verified point-in-time availability;
- `pit_historical`: the production record has a verified availability seam;
- `unsupported`: no production capability exists.

`status=data_insufficient` and `usage_scope=current_reference` are intentionally
compatible. A missing `available_at` still means historically unavailable; it
does not hide a current/reference value from the dedicated product surface.

The Taiwan-specific API exposes the code matrix without network access at
`GET /api/taiwan/capabilities`. `GET /api/taiwan/data/{symbol}` resolves the
instrument type through Security Master and returns isolated sections:

- stocks: monthly revenue, financial statement, valuation, and share capital;
- ETFs: official profile and the current/reference snapshot.

The API reuses `TaiwanHybridProvider`, `TaiwanOfficialFundamentals`, and
`TaiwanOfficialETFData`; it does not duplicate parsing. Dividend lifecycle events
remain on their explicit time-window seam and are not fetched by every current
request. Phase 6I historical NAV and distribution evidence remains documentation
only. Current/reference sections do not enter verified factors, the historical
screener, turnover history, or backtests.

# Taiwan verified fundamentals factor safety (Phase 6F)

Taiwan company factors are derived on demand from `TaiwanFundamentalStore` and
must pass the strict `query_at > available_at` gate. Current aggregation values
with `available_at=None` are reference-only and never enter screener ranking or
backtests. Later revisions cannot replace earlier revisions before their own
availability time.

| Factor | Dataset / field | Unit | Live official availability | Historical decision |
|---|---|---|---|---|
| EPS | financial statement / `cumulative_eps` | TWD per share | date-level aggregation only | supported only for records carrying verified `available_at`; current source is current-only |
| ROE | `net_income / equity` from one financial-statement revision | ratio | date-level aggregation only | supported only for verified records; zero equity is malformed |
| PE | valuation / `pe` | ratio | trade/report date without proven first-public timestamp | current-only; verified-record seam supported |
| PB | valuation / `pb` | ratio | trade/report date without proven first-public timestamp | current-only; verified-record seam supported |
| Dividend yield | valuation / `dividend_yield` | percent | trade/report date without proven first-public timestamp | current-only; verified-record seam supported |
| Revenue growth | monthly-revenue aggregation | percent | no stable exact availability join | data-insufficient |

Ranking compares only finite `available` values at one shared `query_at`.
`missing`, `data_insufficient`, `unsupported`, and `malformed` observations are
excluded rather than converted to zero or worst rank. Coverage reports totals
for each status. ETFs are always unsupported for company fundamental factors.


# Data Bundle 稽核（2026-09-26）

目的：定義「基本資料快照 + 選用完整歷史包 + 增量更新」的最小配送方案。本輪只在隔離目錄驗證，**未公開上傳**，
也不建立資料發布系統。授權欄是依下列官方頁面的稽核，不是法律意見；`授權未明` 表示不得假設可再散布。

## 資料清單（以 2026-09-26 本機 `data/taiwan` 實測）

| 資料 | 用途層級 | 來源 | 期間／筆數 | 格式 | 大小（實測） | 必要 metadata | 再散布依據 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `daily/` 日 K | 基本看盤／選股 | TWSE／TPEx 官方日行情（每日更新走 OpenAPI，歷史回補走站內月／日查詢） | 2024-01-02→2026-09-10、652 分區、1,416,933 列 | Parquet（zstd）：`symbol,date,open,high,low,close,volume,amount,quote_ts` | 33.2 MB（zip 32.4 MB） | 無 footer 契約；`security_master` 同包 | **混合**：OpenAPI 部分屬政府資料開放授權（見下），站內查詢部分授權未明；逐列來源未標記 |
| `security_master.parquet` | 基本看盤／選股 | TWSE ISIN 一覽表、`t187ap47_L`、`t187ap03_L` | 2,376 檔（現況） | Parquet | 46 KB | 含 `source`、`updated_at` | `t187ap*` 屬 OpenAPI；ISIN 一覽表授權未明 |
| `observed_universe/exchange=TWSE/` | 完整歷史／OOS | TWSE `MI_INDEX?type=ALLBUT0999` 歷史逐日 + 月表證據 | 2015-01-05→2026-09-24、3,070 分區（含 8 個週六交易日）、3,214,591 列 | Parquet（zstd） | 109.9 MB | **footer**：`taiwan_trading_day_evidence_v1`（空分區＝休市證據與來源）；不可只複製資料列 | 授權未明；2017-05 前明確不在開放資料集範圍 |
| `historical_classification/` | 完整歷史／OOS | 由 `MI_INDEX` 類別表 + 官方登錄冊推導 | 478 個首次觀測日、541,615 列 | Parquet | 3.9 MB | **footer**：`taiwan_classification_contract=2`；`classification_source` 逐列記錄證據 | 衍生整理，依其來源；含 ISIN 者授權未明 |
| `instrument_evidence/` | 完整歷史／OOS | ISIN 上市／未上市、`t187ap03_L`、終止上市公司 | 1,422／1,095／265 列 | Parquet | 0.03 MB | **footer**：來源 URL、SHA-256、取得時間 | `t187ap03_L`、終止上市公司屬 OpenAPI；ISIN 授權未明 |
| `adj_factor/events.parquet` + `coverage.json` | 完整歷史／OOS | TWT49U、TWTAUU、TWTB8U、exDailyQ、revivt | 2015-01-05→2026-09-25、22,651 事件 | Parquet + JSON | 1.4 MB | `raw_fields`、`source_url`、`retrieved_at`、`status`；`coverage.json` 記錄涵蓋區間 | 站內查詢，授權未明 |
| `observed_universe/exchange=TPEX/` | 選用（Secondary 實驗，Primary 不需要） | TPEx `dailyQuotes` 歷史逐日 | 2015-01-05→2026-09-24、3,070 分區、20,447,936 列 | Parquet（zstd） | 331.9 MB（zip 316.7 MB） | 同 TWSE 的 footer 證據 | 授權未明 |
| `factors/`（未建立） | 衍生、可重建 | 由上列資料以既有程式計算 | 預估約 270 萬列 | Parquet | 未實測，不配送 | — | — |

壓縮大小為實測：基本包 33.2 MB（deflate 32.4 MB）、TWSE 歷史包 115.2 MB（deflate 103.6 MB）、TPEx 選用包 331.9 MB
（deflate 316.7 MB）；Parquet 已 zstd，再壓縮幾乎無效，所以用 zip STORED／DEFLATE 皆可。

## 排除清單（不得進任何資料包）

`live_quant/signals.sqlite3`（即時訊號帳本）、`monitor_rules*.json`、`backfill_worker_state.json`／`*.lock`／`*.guard`、
`user_data/`（自選、偏好、策略覆寫）、持倉與交易紀錄、提醒、AI 對話與 `ai_cache/`、設定與 `secrets`／`.env`／金鑰、
`logs/`、`backend.log`、`exports/`、`release-assets/`、`.pytest*`。FinMind 取得的任何資料（基本面、籌碼）預設同樣排除。

## 授權核對摘要

- TWSE 使用條款：站內內容未經書面同意不得重製、散布，**但 TWSE 已授權「政府資料開放平臺」提供公眾使用的資料不在此限**；引用須標示來源且不得任意增刪。條款另禁止以自動化程式下載站內資料（[TWSE 使用條款](https://www.twse.com.tw/zh/terms/use.html)）。
- 政府資料開放授權條款第 1 版允許重製、散布、商業利用與再授權，須標示來源（[授權條款](https://data.gov.tw/license)）。TWSE 個股日成交資訊（STOCK_DAY_ALL）為該授權，且註明可用自動程式下載（[資料集 11549](https://data.gov.tw/dataset/11549)）。
- 櫃買中心多數 OpenAPI 資料集採同一授權（[上櫃股票收盤行情](https://data.gov.tw/dataset/11371)）。
- 交易資訊使用管理辦法／收費標準另有契約（[TWSE 說明](https://www.twse.com.tw/zh/products/information/use.html)）；本輪未取得該辦法全文，歷史逐日資料是否適用未確認。
- FinMind：其「免責聲明與資料授權」頁的摘要為使用者取得的是**服務使用權，不含再散布、轉售或鏡像**；該頁為前端渲染，本輪無法取得原文，逐項授權需人工確認。**FinMind 使用權不能視為資料再散布權。**
- 結論：只有明確為 OpenAPI／政府資料開放平臺的資料集可主張再散布；本 repo 的歷史逐日資料多來自站內查詢，**授權未明前不建議公開上傳**。可先以私人／同一使用者的多台電腦搬移，或只發布程式與 manifest 讓使用者自行回補。

## 最小配送方案

1. **程式碼不含資料**（現況）。
2. **基本資料快照**：`daily/`（可縮短為近 250 個交易日，約 12 MB 為估計）+ `security_master.parquet`，附 manifest（路徑、bytes、SHA-256、來源、期間）。
3. **選用完整歷史包**：TWSE 歷史包（`observed_universe/exchange=TWSE/`、`historical_classification/`、`instrument_evidence/`、`adj_factor/`），必須整包含 footer 一起複製並附 manifest 驗雜湊；TPEx 包另選。
4. **增量更新**：解壓到 `<DATA_DIR>/taiwan` 後，用既有 `scripts.taiwan_historical_backfill`（續跑缺漏交易日、`--verify-trading-days`、`--resolve-instrument-types`）與每日 refresh 補到最新；factor panel 由 `--build-factor-panel` 重建，不配送。

隔離目錄驗證（2026-09-26）：把基本包與 TWSE 歷史包解壓到暫存 `DATA_DIR`，4,207 個檔案雜湊與 manifest 全部一致；以既有
`read_primary_oos_preflight()`、`TaiwanDailyStore.read_all()`、`CorporateActionStore().read()`、`InstrumentEvidenceStore().load()`
讀取，資料健康度、分類計數、事件數（22,651）與正式目錄逐項相同（日 K 1,416,933 列）。

# Taiwan market data sources

The `taiwan` provider uses one existing provider seam with the following policy:

1. TWSE and TPEx official endpoints are authoritative for the security directory,
   daily close quote, and daily K-line.
2. FinMind and Yahoo remain supplements and fallbacks for coverage unavailable from
   official endpoints. Fallback records are labelled `third_party_fallback`.
3. No third-party value silently overwrites a successful official value.

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
| B | TWSE e添富 ETF pages and distribution list | displayed NAV/AUM/ranking/distribution information | web presentation and date-level information; no stable historical API and exact publication time verified in this phase | reference/live investigation only; not parsed into production history |
| B | TPEx ETF pages and published media format | per-unit NAV and related declared fields | official specification exists, but a stable historical cross-product API and availability timestamps were not verified | capability remains `data_insufficient` |
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

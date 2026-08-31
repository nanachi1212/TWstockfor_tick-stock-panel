# Phase 6I Taiwan ETF historical NAV and distribution investigation

Investigation date: 2026-08-31 (Asia/Taipei).

## Decision

Official TWSE and TPEx services expose historical or recent ETF NAV and
distribution values, but none of the investigated records carries a verified
publication timestamp, a stable revision identity, or an official rule that can
be converted to an exact `available_at`. They are therefore reference datasets,
not point-in-time-safe production history.

No provider, parser, store, factor, or strategy integration is added in this
phase. Existing current/reference ETF metadata remains unchanged.

## Source inventory and classification

| Dataset | Owner / endpoint | Method and parameters | Response / fields | History and timing | Revision behavior | Class |
| --- | --- | --- | --- | --- | --- | --- |
| ETF product metadata | TWSE `https://openapi.twse.com.tw/v1/opendata/t187ap47_L` | GET | JSON; product metadata and `發行單位數/轉換數` | current report, date-only `出表日期` | none exposed | A for profile; B for current issued-unit reference |
| e添富 NAV chart | TWSE `https://www.twse.com.tw/zh/ETFortune/ajaxEtfInfoChart` | POST: `id`, `startDate`, `endDate`, `type=fundPric` | JSON `netPrice[{date,count}]` and `atmps[{date,count}]` | recent and older dates work; UI limits selection to three years | no publication timestamp, field contract, event ID, or correction chain | B: historical reference only |
| ETF distribution report | TWSE `https://www.twse.com.tw/rwd/zh/ETF/etfDiv` | GET: `stkNo`, `startDate`, `endDate`, `response=json` | JSON table: ex-date, basis date, payment date, amount per unit, distribution standard, announcement year | historical rows from the requested period; dates only | no announcement timestamp, event/document ID, or correction chain | B: historical reference only |
| e添富 product page | TWSE `/zh/ETFortune/etfInfo/{symbol}` | GET, with chart POST calls | HTML plus embedded/current chart data, AUM and four recent payments | presentation layer; some fields are supplied by outside vendors or TDCC | none exposed | C for production ingest |
| ETF information-center product | TPEx `https://info.tpex.org.tw/api/etfProduct` | POST: `lang=zh-tw`, `query={symbol}` | JSON: recent `netPrice`, premium/discount, four payments, current AUM and metadata | page states near-two-month NAV coverage; no arbitrary historical range | no publication timestamp, stable distribution identity, or correction chain | B for recent reference, C for historical ingest |
| ETF list/search | TPEx `https://info.tpex.org.tw/api/etf` | GET/POST: `lang`, `query` | JSON suggestions of genuine TPEx ETFs | current directory | no historical semantics | A for discovery only |
| ETF monthly scale report | TPEx `https://www.tpex.org.tw/zh-tw/product/etf/statistics/monthly.html` | date query; HTML/CSV presentation | month-end close, unit NAV, premium/discount, issued units and fund scale | from ROC 113-01; official note says third business day of following month | date-level rule, not a record timestamp; no revision identity | B: monthly reference only |
| OpenAPI catalogue | TPEx `https://www.tpex.org.tw/openapi/swagger.json` | GET | OpenAPI JSON | no Taiwan-wide historical ETF NAV/distribution endpoint found | not applicable | C for this phase |

## NAV semantics and coverage

The TWSE chart labels `netPrice` as `淨值`; `atmps` is the displayed
premium/discount percentage. The response does not expose estimated NAV or
iNAV, so those concepts must not be populated from this endpoint. It also does
not machine-readably define whether the value is final.

Live requests returned date + NAV for:

- `0050`: August 2026 and January 2024;
- `00631L`: August 2026;
- `00632R`: January 2024.

The page's date picker permits only the preceding three years. This is useful
historical reference coverage, but not a documented stable historical API
contract. No ticker suffix or name heuristic classified the leveraged/inverse
samples.

The TPEx service returned recent dated NAV observations for genuine TPEx ETF
`006201`. Its page describes the display as the most recent two months and has
no date-range parameter, so it is not a general historical source.

## Availability and PIT boundary

None of the NAV responses includes `published_at`, `available_at`, a publication
timestamp, or an official record-level availability rule. NAV date, market close,
the following midnight, HTTP headers, and retrieval time are not substitutes.

Any future reference mapping must therefore use:

```text
available_at = None
status = data_insufficient
historically available = false
```

It cannot enter a backtest. The strict rule remains `query_at > available_at`;
equality is unavailable.

## Distribution semantics and identity

The TWSE report defines the amount as `收益分配金額 (每1受益權益單位)` in TWD
and separately supplies ex-date, distribution basis date, and payment date. It
is an ETF distribution dataset, not a company `DividendLifecycleEvent`.

For `0050`, a 2020-2026 request returned both recent 2026 payments and older
2020 observations. Missing dates or amounts must remain null; no component may
be promoted to a total unless the official field says so.

The report exposes no announcement timestamp, serial/document ID,
distribution-period key, or correction link. `symbol + ex_date + amount` is not
an official identity. Ex-date, basis date, payment date, announcement year and
retrieval time are not publication time. A production distribution store is
therefore not justified.

TPEx product responses expose only four recent payment-date/amount pairs. They
do not expose ex-date, basis date, announcement time, event identity or revision
semantics, so they are display/reference data only.

## AUM and unit semantics

TWSE e添富 and TPEx information-center pages display current AUM, while TPEx also
publishes month-end scale reports. These are reference values with currency/unit
labels but without exact record availability or revision identity. This phase
does not derive AUM from NAV times units.

The existing `t187ap47_L` field remains `issued_units`. It is not relabelled as
`outstanding_units` or beneficiary units. TPEx product data does not provide a
safe outstanding-unit mapping.

## Rejected mappings

- iNAV or estimated NAV as final NAV;
- closing price as NAV;
- retrieval time, HTTP time, market close, next midnight, ex-date, basis date or
  payment date as publication/availability;
- page display data as a stable production API without a documented contract;
- issuer websites or third-party data as a Taiwan-wide exchange-official source;
- `symbol + ex_date + amount` or fuzzy matching as event identity;
- AUM derived from NAV and units;
- `發行單位數` relabelled as outstanding units;
- ETF distributions routed through the company dividend lifecycle.

## Sample summary

| Symbol | Official profile | NAV evidence | Distribution evidence | PIT status |
| --- | --- | --- | --- | --- |
| `0050.TWSE` | TWSE `t187ap47_L` | recent and January 2024 chart values | TWSE dated rows from 2020-2026 | reference only; unavailable to backtests |
| `00631L.TWSE` | TWSE `t187ap47_L` | recent chart values | no production mapping attempted | reference only |
| `00632R.TWSE` | TWSE `t187ap47_L` | January 2024 chart values | no production mapping attempted | reference only |
| `006201.TPEX` | TPEx ETF directory/product service | recent near-two-month values | four recent payment-date/amount pairs | reference only |

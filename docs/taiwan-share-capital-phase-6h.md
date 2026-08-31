# Phase 6H Taiwan historical share-capital investigation

Investigation date: 2026-08-31 (Asia/Taipei).

## Decision

The official company-profile datasets safely support a current/reference record
for issued common shares, paid-in capital, and common-share par value. They do
not provide historical snapshots, exact publication timestamps, effective dates,
or revision chains. `total_shares` and `float_shares` therefore remain null.

No official source investigated in this phase supports a complete production
historical share-capital mapping. Historical Taiwan turnover now fails closed
instead of applying today's instrument `float_shares` to past dates.

## Source inventory and classification

| Source | Fields / meaning | Unit | History and timing | Revision semantics | Class |
| --- | --- | --- | --- | --- | --- |
| TWSE `opendata/t187ap03_L` | `已發行普通股數或TDR原股發行股數`, `實收資本額`, `普通股每股面額`, `出表日期` | shares, TWD, TWD/share | current report; date-level only | none exposed | B: current/reference |
| TPEx `mopsfin_t187ap03_O` | `IssueShares`, `Paidin.Capital.NTDollars`, `ParValueOfCommonStock`, `Date` | shares, TWD, TWD/share | current report; date-level only | none exposed | B: current/reference |
| MOPS company increase/decrease table (`IRB160`) | monthly capital-change report | report fields; not a universal share snapshot | historical monthly query, no exact record availability verified | no stable chain to company-profile snapshots | C |
| MOPS issuance/capital-change announcements | event-specific issuance, reduction, cancellation, conversion or exchange details | varies by form | some events have exact material-information timestamps | no universal structured resulting-share/effective-date schema | C for generic ingest |
| TWSE capital-reduction reference report (`TWTAUU`) | resume date, reduction reason and trading reference prices | dates/prices; detail may include exchange ratio | historical event report | no exact publication/revision chain to profile snapshot | C for share history |
| TWSE/TPEx security directory | stable security identity | identity metadata | current | not a time-varying capital source | C for capital |

Official entry points:

- TWSE OpenAPI schema: `https://openapi.twse.com.tw/v1/swagger.json`
- TPEx OpenAPI schema: `https://www.tpex.org.tw/openapi/swagger.json`
- MOPS: `https://mops.twse.com.tw/`
- MOPS capital increase/decrease query: `https://mops.twse.com.tw/mops/web/IRB160`
- MOPS issuance announcement query: `https://mops.twse.com.tw/mops/web/t59sb09`
- TWSE capital-reduction report: `https://www.twse.com.tw/exchangeReport/TWTAUU?response=html`

## Concept mapping

- `issued_shares` maps only from the explicit issued-common-share field.
- `capital_twd` maps only from paid-in capital, a monetary amount.
- `par_value_twd` preserves the explicit common-share par value as metadata.
- `total_shares` stays null because issued common shares are not necessarily all
  share classes. The 2881 profile demonstrates this boundary: paid-in capital
  includes capital beyond its issued-common-share count.
- `float_shares` stays null because none of the profile fields defines public
  float, free float, circulating shares, or listed tradable shares.

No value is derived by dividing capital by par value. Private shares, preferred
shares, listed shares, issued shares, and ETF units are not substituted for float.

## Effective date and availability

The company-profile `Date` / `出表日期` is a report date. It is neither an exact
publication timestamp nor an event effective date. Current/reference records use:

```text
published_at = None
available_at = None
status = data_insufficient
```

Capital events can be announced before their effective date. A future historical
event model must independently retain both values and must not apply a known
future event before its effective date. The current official source set does not
provide a sufficiently uniform identity and time model to implement that seam.

## Live verification

| Symbol | Raw source fields | issued_shares | total_shares | float_shares | capital_twd | par_value_twd | Historical status |
| --- | --- | ---: | --- | --- | ---: | ---: | --- |
| `2330.TWSE` | TWSE company profile | 25,932,370,067 | null | null | 259,323,700,670 | 10 | unavailable |
| `6488.TPEX` | TPEx company profile | 478,113,725 | null | null | 4,781,137,250 | 10 | unavailable |
| `2881.TWSE` | TWSE company profile | 14,007,364,952 | null | null | 156,073,549,520 | 10 | unavailable |
| `6176.TWSE` | TWSE company profile after an official 2026 capital-reduction event | 348,770,447 | null | null | 3,487,704,470 | 10 | unavailable |
| `0050.TWSE` | ETF control | unsupported | unsupported | unsupported | unsupported | unsupported | unsupported |

The 6176 TWSE reduction report proves that a capital-change event occurred and
that shares are time-varying. It does not by itself prove a complete before/after
share snapshot with an exact publication and revision chain, so it is not ingested
as historical capital.

## Consumer boundary

The existing generic financial-share parquet and current instruments originate
outside this Taiwan official foundation. For `.TWSE` and `.TPEX` rows:

- current-day calculations may retain a current instrument denominator;
- historical calculations require a future verified Taiwan-specific PIT seam;
- date-only `period_end` rows and current instrument values cannot enable
  historical turnover;
- missing verified history produces null turnover rather than a plausible but
  look-ahead-biased value.

The existing non-Taiwan fallback behavior remains unchanged.

## Rejected inference

- current issued or float shares backward-filled across history;
- `capital_twd / par_value_twd` as authoritative shares;
- market cap divided by price;
- issued, listed, private, preferred, or outstanding shares as public float;
- stock-dividend arithmetic as a resulting share count;
- same issuer/date/amount or subject similarity as event identity;
- report date, effective date, retrieval time, or HTTP headers as exact availability;
- ETF issued or beneficiary units as company share capital.

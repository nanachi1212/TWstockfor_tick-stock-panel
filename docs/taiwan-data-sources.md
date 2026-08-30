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

- `volume` is shares/units. TPEx monthly `成交張數` is multiplied by 1,000.
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

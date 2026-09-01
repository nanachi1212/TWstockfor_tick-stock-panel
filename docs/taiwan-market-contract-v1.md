# Taiwan Market Contract v1

Status: v1.0.0, rules verified as of 2026-08-30. The language-neutral golden fixture is `docs/taiwan_market_contract_v1.json`.

## 1. Identity

`symbol` is the source code; `canonical_symbol` is `{code}.{TWSE|TPEX}`. `exchange` is `TWSE` or `TPEX`; `security_type` v1 is `stock` or `etf`; `currency` is `TWD`; ordinary board-lot size is 1,000 shares/units and odd-lot minimum is one. Index is observed in both projects but is outside the v1 tradable-security identity. Warrant, bond, preferred, TDR, REIT and ETN remain deferred until both projects have verified official metadata rather than code heuristics.

## 2. Provenance and status

Every externally sourced record uses `provider`, `source`, `source_url`, `retrieved_at`, and the applicable `trade_date`. `provider` names the supplying organization/adapter; `source` names the dataset; `source_url` is the fetch origin; `retrieved_at` is observation time. Required v1 statuses are `official`, `third_party`, `third_party_fallback`, `unsupported`, `data_insufficient`, `stale`, `schema_changed`, and `error`. `fallback` alone is too ambiguous and is not emitted by new records.

## 3. Missing and time

`""`, `-`, `--`, and `N/A` are null/nil; textual `0` and `0.0` are real zero; malformed text is a parse/schema error. The time fields are distinct: `trade_date`, `period_start`, `period_end`, `published_at`, `available_at`, `retrieved_at`. Missing publication evidence stays null. Point-in-time selection is strict: a record is usable only when `query_at > available_at`; a later revision cannot rewrite an earlier query.

## 4. Market data

Quote fields are canonical symbol, OHLC, share volume, trade date, provenance and status. K-line fields are timestamp, OHLC and share volume. Daily timestamps represent 13:30 Asia/Taipei market close; ROC dates normalize to Gregorian calendar dates. A daily official close can be stale but must not be called realtime. Raw provider lot counts must be converted to shares at the provider seam.

Institutional raw fields are daily `foreign`, `investment_trust`, `dealer`, `dealer_proprietary`, `dealer_hedge`, and `net`, in shares. `5d`, `20d`, and `streak` are derived factors and do not belong to raw records. Margin raw/normalized fields are `margin_balance`, `margin_change`, `short_balance`, `short_change` in shares; `short_margin_ratio` is derived. Raw source unit is retained.

## 5. Trading rules

- Commission rate, discount and minimum are broker configuration. TWSE says brokers set their own rate; Contract v1 has no statutory/default commission rate or statutory minimum.
- Securities tax is sell-side: ordinary stock 0.3%; eligible stock day trade 0.15% through 2027-12-31; ETF 0.1%; passive bond ETF 0% only through the enacted 2026-12-31 window. Dates beyond a legislated window fail closed. A proposed extension is not effective law.
- Stock tick tiers are 0.01/0.05/0.10/0.50/1/5 at 10/50/100/500/1000 boundaries. ETF tick is 0.01 below 50 and 0.05 from 50.
- Domestic-component ordinary ETF and ordinary stock limit is 10%. Foreign-component ETF has no limit. Domestic leveraged/inverse ETF uses 10% times the absolute multiplier. Bond classification alone does not determine the limit; underlying scope does.
- A limit-up is the greatest valid tick not above the raw ceiling; limit-down is the least valid tick not below the raw floor.
- Standard settlement is T+2 business days. Settlement does not itself define day-trade eligibility.

Official references: [TWSE trading system](https://www.twse.com.tw/zh/products/system/trading.html), [TWSE ETF rules](https://www.twse.com.tw/zh/products/securities/etf/overview/rules.html), [TWSE clearing](https://www.twse.com.tw/zh/clearing/clearing/features.html), [TWSE investment guide](https://www.twse.com.tw/zh/about/company/guide.html), [MOF Securities Transaction Tax Act](https://law-out.mof.gov.tw/LawContent.aspx?id=FL006079).

## 6. Fundamentals

Monthly revenue carries period, revenue, prior-month/prior-year values, MoM/YoY, cumulative, provenance and status. Statements keep period, publication/availability, statement type, cumulative EPS and balance fields. Valuation uses trade date, P/E, P/B and dividend yield. Dividend keeps cash/stock amounts, dates, raw status and normalized status. Missing is never zero; `unsupported` means not applicable, while `data_insufficient` means applicable but unavailable.

## 7. Project inventory and adoption

`mystocktracer` owns the deeper official-provider/provenance seam: TWSE/TPEx directory, quote/K-line, institutional, margin and fundamentals, official fallback/stale/discrepancy handling. `TWstock` owns the deeper execution seam: tick/limit/lot/settlement/tax models, backtest semantics and derived factors.

### mystocktracer <- TWstock

1. TickSizeModel — adopt rule now, rewrite in Go.
2. PriceLimitModel — adopt rule now, rewrite in Go.
3. Tax/Lot/Settlement — adopt rule now as small pure functions/constants.
4. TradingCostModel — adopt configurable shape later; do not adopt the 0.1425% default.
5. TaiwanMarketProfile/backtest/factors/position sizing — later; no engine copy.

### TWstock <- mystocktracer

1. Record-level provenance/status and official identity — adopt rule now.
2. Official quote/K-line and directory — rewrite behind the existing provider seam next.
3. Institutional/margin raw units — adopt rule before derived factors.
4. Official/third-party discrepancy and fundamentals — later; do not copy Go.

## 8. Contract violations and deferred work

- `mystocktracer`: prior to this contract it had no executable tick, limit, tax, lot or settlement model. The v1 pure rules close the tested core gap; security-specific no-limit/leveraged classification remains deferred.
- `TWstock`: `TradingCostModel` still defaults to 0.1425% and its docstring calls it an official ceiling. Official guidance says the broker sets the rate; callers must explicitly configure it. Changing the default is deferred because the backtest engine currently consumes it implicitly and needs a separately scoped migration.
- `TWstock`: provenance/status vocabularies are not yet record-level compatible with Phase 4; provider migration is deferred.
- Both: warrant/bond/TDR/REIT/ETN identity v1 and official publication-time completeness are deferred.
- Phase 4 ETF NAV/holdings/premium-discount, Windows portability tests, new UI, strategies and factor integration are explicitly out of scope.

Raw market records and derived factors remain separate modules; only raw semantics and deterministic market rules are cross-project Contract v1.

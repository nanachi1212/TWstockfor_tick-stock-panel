# Taiwan Market Contract v1.1

Version 1.1.0 is an additive extension of Taiwan Market Contract v1.0.0. All
v1 symbol, market-rule, tax, lot, settlement, parser, provider-unit, provenance,
institutional, margin, and commission semantics remain authoritative.

## Fundamental record types

The language-neutral dataset types are `monthly_revenue`,
`financial_statement_revision`, `valuation_snapshot`,
`dividend_lifecycle_event`, and `share_capital_record`. ETF company
fundamentals and ETF structured data are separate domains; unsupported company
fundamentals must not be represented as zero.

Every fundamental record may carry `period_start`, `period_end`,
`published_at`, `available_at`, `retrieved_at`, `revision`,
`revision_identity`, `supersedes_revision`, `raw_unit`, `normalized_unit`, and
`currency`. Missing optional fields remain null and are not inferred.

## Strict availability

A record is historically visible only when `query_at > available_at`.
`query_at == available_at` is unavailable. A null `available_at` is always
unavailable.

Availability policies are:

- `exact_timestamp`: traceable official evidence proves the precise time.
- `date_level_conservative`: an official rule proves a conservative date-level
  boundary. Defining this policy does not authorize its use for any current
  dataset.
- `insufficient`: official evidence cannot prove availability; `available_at`
  must be null.

An available record must retain `availability_evidence_source`,
`availability_evidence_url`, `availability_evidence_identifier`, and
`availability_confidence`. A third-party timestamp cannot become official
evidence.

## Evidence identities

An announcement event, financial-document upload, aggregation record,
aggregation refresh, and report revision are distinct facts.

A material-information stable identity contains at least `market`, `issuer`,
`enter_date`, and `serial_number`. A financial-document stable identity
contains at least `issuer`, `report_period`, `document_identity`, and
`revision_status`. Subject-text similarity is not a stable join.

`published_at` describes the event or document named by the evidence identity;
it must not be copied to a different aggregation record without a traceable
join. HTTP `Last-Modified`, a filing deadline, or a date-only report field does
not prove historical record availability.

## Revisions

The same symbol, dataset, and period may have multiple revisions. Each revision
keeps its own availability. Later revisions never overwrite earlier historical
truth. An as-of query selects the latest eligible revision at the query time.
Current aggregation values must not be paired with an original revision's
timestamp.

## Dividend lifecycle

Lifecycle event types are `board_resolution`, `shareholder_resolution`,
`ex_date_announcement`, `basis_date_announcement`, `payment_announcement`, and
`paid`. Implementations may report `unsupported` or `data_insufficient` when an
official event is unavailable; v1.1 does not require every event to exist.

## Share capital

`total_shares`, `issued_shares`, `float_shares`, and monetary `capital` are
different concepts. Issued shares do not imply float shares, and capital
divided by par value does not imply float shares. Without a reliable source,
`float_shares` is null and status is `data_insufficient`.

## Golden fixture

`taiwan_market_contract_v1_1.json` contains only the new language-neutral
availability boundary, revision, evidence, dividend-lifecycle, unit, and
share-capital cases. The complete v1 fixture remains unchanged and must continue
to pass independently.

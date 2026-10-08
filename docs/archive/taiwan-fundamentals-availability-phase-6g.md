# Phase 6G fundamentals availability investigation

Investigation date: 2026-08-31 (Asia/Taipei).

## Decision

No additional production availability mapping is safe from the currently
verified official interfaces. Financial-report documents expose exact upload
timestamps and stable filenames, but the aggregate financial-statement rows do
not carry that document identity. Monthly-revenue and valuation rows likewise
lack a stable, record-level publication identity and exact timestamp.

All three datasets therefore keep `published_at=None`, `available_at=None`, and
`status=data_insufficient`. This is an intentional fail-closed result, not a
missing fallback.

## Official source inventory

| Dataset | Official source | Identity and time fields observed | Mapping decision |
| --- | --- | --- | --- |
| Financial report document | MOPS `t57sb01_q1` → TWSE document service `t57sb01` | issuer, report year/quarter, filename, file size, upload timestamp to seconds, latest correction flag | Exact document event exists, but cannot be joined to aggregate values without an aggregate-side document/revision identifier |
| Financial statement aggregate | TWSE `t187ap06/07`; TPEx `mopsfin_t187ap06/07` | report date, year, quarter, issuer and values | No filename, document ID, upload ID, or correction/revision ID; production mapping rejected |
| Monthly revenue aggregate | TWSE `t187ap05_L`; TPEx `mopsfin_t187ap05_O` | report date, data month, issuer and values | No material-information serial or exact publication timestamp; production mapping rejected |
| Daily valuation | TWSE `BWIBBU_ALL`; TPEx `tpex_mainboard_peratio_analysis` | trade date, issuer and values | No first-publication timestamp or record revision ID; production mapping rejected |
| Dividend lifecycle | MOPS `t05st01` / `t05st01_detail` | `marketKind/companyId/enterDate/serialNumber` plus exact event timestamp | Already implemented separately in Phase 6D |

Official entry points:

- MOPS financial report query: `https://mops.twse.com.tw/mops/web/t57sb01_q1`
- MOPS API response for that query: `https://mops.twse.com.tw/mops/api/t57sb01_q1`
- TWSE document service: `https://doc.twse.com.tw/server-java/t57sb01`
- TWSE OpenAPI schema: `https://openapi.twse.com.tw/v1/swagger.json`
- TPEx OpenAPI schema: `https://www.tpex.org.tw/openapi/swagger.json`

## Financial-report live evidence

The MOPS query first returns an official TWSE document-service URL. The document
service then exposes the following Chinese consolidated-report (`AI1`) rows:

| Symbol | Period | Document identity | Revision status | Exact upload timestamp |
| --- | --- | --- | --- | --- |
| `2330.TWSE` | 2026 Q1 | `202601_2330_AI1.pdf` | no correction shown | `2026-05-15T14:43:02+08:00` |
| `2330.TWSE` | 2026 Q2 | `202602_2330_AI1.pdf` | no correction shown | `2026-08-14T13:59:44+08:00` |
| `6488.TPEX` | 2026 Q1 | `202601_6488_AI1.pdf` | no correction shown | `2026-05-11T16:07:12+08:00` |
| `6488.TPEX` | 2026 Q2 | `202602_6488_AI1.pdf` | no correction shown | `2026-08-07T15:22:28+08:00` |
| `2881.TWSE` | 2026 Q1 | `202601_2881_AI1.pdf` | no correction shown | `2026-05-29T12:01:13+08:00` |
| `2881.TWSE` | 2026 Q2 | `202602_2881_AI1.pdf` | no correction shown | `2026-08-28T14:09:26+08:00` |
| `0050.TWSE` | 2026 Q1/Q2 | none | ETF company fundamentals unsupported | none |

These rows prove document availability only. They do not prove that a separately
published aggregate income/balance row was first public at that instant. The
filename pattern is not copied into aggregate data, and matching issuer plus
quarter is explicitly insufficient when corrections or multiple document forms
can exist.

Document-native ingestion was considered and rejected for this phase. Extracting
the required values from PDF, or introducing a general XBRL parser and taxonomy
mapping, is not a small reliable change.

## Live aggregate verification

The current official aggregate records for `2330.TWSE`, `6488.TPEX`, and
`2881.TWSE` returned 2026 Q2 statement values, July 2026 monthly revenue, and
2026-08-28 valuation values. Every record correctly retained
`available_at=None`. `0050.TWSE` remained unsupported for company statements and
monthly revenue; its ETF valuation capability remained `data_insufficient`.

## Rejected mappings

The following cannot establish availability:

- issuer plus report period without the same official document identity;
- filename pattern inferred from issuer and quarter;
- values that appear equal across a PDF, XBRL view, and aggregate endpoint;
- a material-information subject or nearby announcement time;
- report date, period end, trade date, retrieval time, HTTP headers, filing
  deadline, market close, end of day, or next trading day;
- choosing the first of multiple documents or revisions.

## Safety matrix

| Dataset | Exact official timestamp source | Stable identity | Production mapping | Historical PIT eligible | Reason |
| --- | --- | --- | --- | --- | --- |
| financial_statement | Document upload timestamp | Yes for document; absent from aggregate row | No | No | No stable document-to-aggregate value identity |
| monthly_revenue | Some material events have exact timestamps | Absent from aggregate row | No | No | No universal structured serial join |
| valuation | None verified | Trade date only | No | No | First-publication time is unknown |
| dividend_lifecycle | MOPS event timestamp | Stable event parameter tuple | Existing Phase 6D mapping | Yes, strictly after timestamp | Separate event-native source |

## PIT and revision policy

The existing rule remains `query_at > available_at`. Equality is unavailable.
Append-only revisions remain independently visible: at revision B's exact
timestamp, revision A is still selected; B becomes visible one second later.
Records with values but no verified `available_at` never fall back to current data.

# Taiwan Market Overview

A developer onboarding guide for the Taiwan (TWSE / TPEx) market module. For
the app's general setup (Dev mode, Docker, `.env`), see the main
[README](../README.md) first — this page only covers what's specific to the
Taiwan module.

## Scope

Covers TWSE and TPEx: common stocks and ETFs (normal, leveraged, and
inverse). At a high level, the module provides:

- Daily market data, institutional (三大法人) flows, and margin/short data
- A batch screener over the full TWSE/TPEx universe
- Market-wide and industry-level intelligence snapshots
- Deterministic, objective abnormal-move diagnostics
- A deterministic per-symbol research context (price, technical, flows,
  fundamentals/ETF metadata, market rules)
- Multi-stock (2–5 symbol) comparison
- Optional, explicitly user-triggered AI research reports (single symbol
  and comparison)

For the exact symbol/market-rule/tax/settlement semantics, see
[taiwan-market-contract-v1.1.md](./taiwan-market-contract-v1.1.md) (and
[v1.md](./taiwan-market-contract-v1.md) it extends). For where each data
field actually comes from, see
[taiwan-data-sources.md](./taiwan-data-sources.md). ETF NAV/distribution and
fundamentals-availability edge cases are covered in
[taiwan-etf-history-phase-6i.md](./taiwan-etf-history-phase-6i.md),
[taiwan-fundamentals-availability-phase-6g.md](./taiwan-fundamentals-availability-phase-6g.md),
and [taiwan-share-capital-phase-6h.md](./taiwan-share-capital-phase-6h.md).

## Running the app

`./dev.sh` (Windows: `.\dev.ps1`) from the repo root starts both backend and
frontend together, as described in the README's Quick Start. The Taiwan
module needs no separate startup step.

For Taiwan-specific backend work (running its tests, or the backend alone),
**commands must be run with `backend/` as the working directory** — Taiwan's
local data store paths are resolved relative to that directory:

```bash
cd backend
uv run uvicorn app.main:app --reload --port 3018
```

Frontend (from the repo root):

```bash
cd frontend
pnpm dev
```

## Taiwan local data

Ingested Taiwan market data (daily OHLCV, institutional, margin/short, etc.)
is stored under `backend/data/taiwan/`. This is local runtime/cache data —
it is `.gitignore`d and must never be committed. This guide does not cover
ingesting/refreshing that data; the module only reads what's already been
ingested locally.

Because of this, a **"latest" comparison** or screener view reflects the
latest *locally ingested* trading date (exposed as `daily_as_of` via the
data-status endpoint) — not necessarily the latest date actually available
upstream. Local data can lag upstream until it's next refreshed.

## Tests

Run from `backend/` (per the working-directory note above):

```bash
cd backend
uv run --frozen pytest -q
```

Frontend, from `frontend/`:

```bash
cd frontend
pnpm run test
pnpm run lint
pnpm run build
```

## AI research (optional)

Deterministic Taiwan features (screener, diagnostics, research context,
comparison) work fully without any AI configuration. AI research is a
separate, optional layer on top:

- It is only generated after an explicit user action (a button click) for
  both single-symbol research and multi-stock comparison reports — never
  triggered automatically by loading a page or by the deterministic
  endpoints.
- It reuses the app's single, global AI configuration — the same
  `AI_PROVIDER` / `AI_BASE_URL` / `AI_API_KEY` / `AI_MODEL` variables
  documented in [docs/configuration.md](./configuration.md) and
  `.env.example` — there is no separate Taiwan-specific AI setting.
- Without a configured `AI_API_KEY`, the AI endpoints respond with a
  graceful "unavailable" status rather than an error, and every other
  Taiwan feature continues to work normally.

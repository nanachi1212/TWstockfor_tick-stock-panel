"""Current official membership evidence, never a historical subtype assertion."""
from __future__ import annotations

from datetime import date, datetime

import polars as pl

from app.taiwan.quant.live_contract import LIVE_CONTRACT
from app.taiwan.realtime.calendar import TAIPEI_TZ


def current_verified_universe(master: pl.DataFrame, observed: pl.DataFrame, *,
                              session: date, cutoff: datetime) -> pl.DataFrame:
    """Intersect fresh official directory and that session's official quotes.

    Directory company-code fields are authoritative CURRENT ordinary-share
    identities. ISIN sources additionally require ordinary equity CFI evidence.
    No code-length, name or historical industry inference is allowed.
    """
    required = {"symbol", "code", "exchange", "instrument_type", "listing_status",
                "is_supported", "source", "updated_at", "cfi_code"}
    if not required <= set(master.columns):
        raise ValueError("current_security_evidence_unavailable")
    if not {"date", "symbol", "exchange", "source", "retrieved_at", "observed"} <= set(observed.columns):
        raise ValueError("current_market_evidence_unavailable")
    if master["symbol"].n_unique() != master.height or observed["symbol"].n_unique() != observed.height:
        raise ValueError("duplicate current security/market evidence")
    if observed.filter(pl.col("date") != session).height:
        raise ValueError("current universe may not contain historical/future observations")
    quotes = {r["symbol"]: r for r in observed.to_dicts()}
    admitted = []
    for row in master.to_dicts():
        stamp = datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None
        if stamp is None or stamp.utcoffset() is None:
            continue
        # An operational next-morning retry may observe the current directory;
        # freeze records the actual cutoff, never claims it was known at close.
        if not session <= stamp.astimezone(TAIPEI_TZ).date() <= cutoff.astimezone(TAIPEI_TZ).date() or stamp > cutoff:
            continue
        exchange = row["exchange"]
        official = row["source"] == f"{exchange}_OPENAPI"
        isin = row["source"] == f"{exchange}_ISIN" and (row["cfi_code"] or "").startswith("ES")
        quote = quotes.get(row["symbol"])
        if (not (official or isin) or exchange not in ("TWSE", "TPEX")
                or row["symbol"] != f'{row["code"]}.{exchange}'
                or row["instrument_type"] != "stock" or row["listing_status"] != "active"
                or row["is_supported"] is not True or quote is None or quote["observed"] is not True
                or quote["exchange"] != exchange):
            continue
        source = "twse:MI_INDEX:ALLBUT0999" if exchange == "TWSE" else "tpex:dailyQuotes"
        retrieved = datetime.fromisoformat(quote["retrieved_at"])
        if (quote["source"] != source or retrieved.utcoffset() is None or retrieved > cutoff
                or retrieved.astimezone(TAIPEI_TZ).date() < session):
            continue
        admitted.append({**row, "market_symbol": row["symbol"], "date": session,
                         "universe_contract": LIVE_CONTRACT,
                         "market_evidence_source": source,
                         "market_retrieved_at": quote["retrieved_at"]})
    if not admitted:
        return pl.DataFrame(schema={**master.schema, "market_symbol": pl.String,
                                    "date": pl.Date, "universe_contract": pl.String})
    return pl.DataFrame(admitted, infer_schema_length=None).sort("symbol")

"""Point-in-time Taiwan universe — **market truth only**.

This layer answers "what did the market actually show on date D, and how well is
it evidenced?".  It answers nothing about whether a symbol is *suitable* for a
model — that is ``app/taiwan/quant_eligibility.py``, deliberately a separate
module so the boundary is structural rather than a naming convention.

Three independent facts, never derived from each other
------------------------------------------------------
``observed_on_market``      the official daily snapshot contained this code on D.
                            Pure observation, from the A2a census.

``listing_metadata_status`` how well the *listing lifecycle* is evidenced.
                            ``verified`` only where an official historical list
                            exists (TWSE), ``unknown`` otherwise (TPEx — the
                            official endpoint only serves the current year, and
                            "終止上櫃" is not even a delisting; probe §5/§10).

``instrument_type_status``  whether the instrument type was established from
                            that date's own official response (A2b, TWSE only).

Forbidden inferences, each with a counter-example in the probe
-------------------------------------------------------------
* ``observed_on_market == false`` ⇒ delisted.
  2358 廷鑫 / 2443 昶虹 were absent from the 2024-06-03 snapshot months before
  their 2024-11-19 delisting, because trading was already suspended (§2.1).
* a termination record ⇒ delisted.
  5236 凌陽創新 appears on TPEx's 終止上櫃 list and trades on TWSE today;
  6423 億而得 appears on TWSE's 終止上市 CSV and trades on TPEx today (§10.1).
  A termination is only a delisting when the opposite market also has no record.

Neither inference is available from this module at all — there is no API that
returns a listing status from an observation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

import polars as pl

from app.taiwan.historical_classification import HistoricalClassificationStore
from app.taiwan.observed_universe import ObservedUniverseStore

EvidenceStatus = Literal["verified", "inferred", "unavailable", "unknown", "data_insufficient"]

#: Exchanges whose historical listing lifecycle has an official archive.
#: TWSE: 終止上市 CSV, 2001-01-20 onwards (audit §6.1).
#: TPEx: current-year only, and 終止上櫃 ≠ delisting (audit §6.2, probe §10).
LISTING_METADATA_STATUS: dict[str, str] = {"TWSE": "verified", "TPEX": "unknown"}

UNIVERSE_COLUMNS: list[str] = [
    "date", "code", "exchange", "market_symbol",
    "observed_on_market", "tradable_source",
    "listing_metadata_status",
    "instrument_type", "instrument_type_status",
]


@dataclass(frozen=True)
class UniverseRow:
    """One (date, security) market-truth record."""

    date: date
    code: str
    exchange: str
    observed_on_market: bool
    tradable_source: str
    listing_metadata_status: str
    instrument_type: str | None
    instrument_type_status: str

    @property
    def market_symbol(self) -> str:
        return f"{self.code}.{self.exchange}"


class PitUniverse:
    """Assembles per-session market truth from the census + classification stores.

    Both inputs are append-only staging datasets produced by the Background Data
    Lane worker; this class only reads them.
    """

    def __init__(
        self,
        census: ObservedUniverseStore | None = None,
        classification: HistoricalClassificationStore | None = None,
    ) -> None:
        self.census = census or ObservedUniverseStore()
        self.classification = classification or HistoricalClassificationStore()

    def _classification_frame(self, day: date) -> pl.DataFrame:
        """Latest classification per code by day, including unresolved revisions."""
        frame = self.classification.read()
        if frame.is_empty():
            return pl.DataFrame(schema={
                "code": pl.Utf8, "exchange": pl.Utf8, "instrument_type": pl.Utf8,
                "classification_effective_from": pl.Date, "classification_status": pl.Utf8,
            })
        return (
            frame.filter(pl.col("classification_effective_from") <= day)
            .sort("classification_effective_from")
            .group_by(["code", "exchange"], maintain_order=True)
            .last()
            .with_columns(pl.col("instrument_type_status").alias("classification_status"))
            .select("code", "exchange", "instrument_type",
                    "classification_effective_from", "classification_status")
        )

    def as_of(self, day: date, exchange: str | None = None) -> pl.DataFrame:
        """Market truth for one session.

        A code is present iff the official snapshot contained it that day.
        Classification is attached where it exists; where it does not, the row
        keeps ``instrument_type_status='data_insufficient'`` rather than a guess.
        """
        observed = self.census.read(exchange)
        if observed.is_empty():
            return pl.DataFrame(schema={c: pl.Utf8 for c in UNIVERSE_COLUMNS})
        observed = observed.filter(pl.col("date") == day)
        if observed.is_empty():
            return pl.DataFrame(schema={c: pl.Utf8 for c in UNIVERSE_COLUMNS})

        truth = observed.select(
            pl.col("date"),
            pl.col("raw_code").alias("code"),
            pl.col("exchange"),
            (pl.col("raw_code") + "." + pl.col("exchange")).alias("market_symbol"),
            pl.lit(True).alias("observed_on_market"),
            pl.lit("official_daily_snapshot").alias("tradable_source"),
            pl.col("exchange")
              .replace_strict(LISTING_METADATA_STATUS, default="unknown")
              .alias("listing_metadata_status"),
        )

        classification = self._classification_frame(day)
        if classification.is_empty():
            return truth.with_columns(
                pl.lit(None, dtype=pl.Utf8).alias("instrument_type"),
                pl.lit("data_insufficient").alias("instrument_type_status"),
            ).select(UNIVERSE_COLUMNS)

        # A classification only counts once it has been established — never
        # back-applied to sessions before the date it was derived from.
        usable = classification.filter(pl.col("classification_effective_from") <= day)
        joined = truth.join(
            usable.select("code", "exchange", "instrument_type", "classification_status"),
            on=["code", "exchange"], how="left",
        )
        return joined.with_columns(
            pl.when(pl.col("classification_status") == "verified")
              .then(pl.lit("verified"))
              .otherwise(pl.lit("data_insufficient"))
              .alias("instrument_type_status"),
        ).select(UNIVERSE_COLUMNS)

    def sessions(self, exchange: str) -> list[date]:
        return sorted(self.census.session_dates(exchange))

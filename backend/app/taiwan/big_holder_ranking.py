"""Big-holder concentration ranking (大戶集中度排行), ported from EquiLens 衡析 (MIT).

Product opinion, not a deterministic fact: the tone thresholds follow EquiLens.
Input is the TDCC weekly snapshot in ``TaiwanShareholdingStore``. A change exists
only when that earlier week is stored and contains the stock; it is never 0 by
default. The 1-week base is the store's adjacent previous week; the 4-week base is
the stored week 24..32 days back that is closest to 28 days.

Current-market only, for the same reason as chip_ranking: TDCC weeks have no
recorded publication time and the universe is today's listed stocks.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

import polars as pl

from app.services import watchlist
from app.taiwan.shareholding_store import TaiwanShareholdingStore, _with_date_column
from app.taiwan.universe import TaiwanSecurityMaster, get_security_master

MIN_HOLDERS = 1000
THRESHOLD_PP = 0.5
TOP_N = 60
FOUR_WEEK_TARGET_DAYS = 28
FOUR_WEEK_MIN_DAYS, FOUR_WEEK_MAX_DAYS = 24, 32
ROW_COLUMNS = [
    "symbol", "name", "exchange", "big_holder_pct", "total_holders",
    "change_1w_pp", "holders_change_1w_pct", "change_4w_pp", "holders_change_4w_pct",
    "tone", "in_watchlist",
]


def _change_vs(frame: pl.DataFrame, data: pl.DataFrame, base: date | None, window: str) -> pl.DataFrame:
    """big_holder_pct change in percentage points and holder-count change in percent vs ``base``.

    A base row below MIN_HOLDERS is not a comparable distribution (e.g. 2323 had one custody
    holder during a share reissue), so that change stays None instead of a -60pp outlier.
    """
    pp, holders = f"change_{window}_pp", f"holders_change_{window}_pct"
    if base is None:
        return frame.with_columns(pl.lit(None, pl.Float64).alias(pp), pl.lit(None, pl.Float64).alias(holders))
    before = data.filter((pl.col("date") == base) & (pl.col("total_holders") >= MIN_HOLDERS)).select(
        "symbol", pl.col("big_holder_pct").alias("_pct"), pl.col("total_holders").alias("_holders"))
    return frame.join(before, on="symbol", how="left").with_columns(
        (pl.col("big_holder_pct") - pl.col("_pct")).alias(pp),
        ((pl.col("total_holders") / pl.col("_holders") - 1) * 100).alias(holders),
    ).drop("_pct", "_holders")


def _tone() -> pl.Expr:
    change, holders = pl.col("change_4w_pp"), pl.col("holders_change_4w_pct")
    return (pl.when(change.is_null()).then(pl.lit("insufficient"))
            .when((change >= THRESHOLD_PP) & (holders.is_null() | (holders <= 0))).then(pl.lit("concentrating"))
            .when((change <= -THRESHOLD_PP) & (holders.is_null() | (holders >= 0))).then(pl.lit("dispersing"))
            .otherwise(pl.lit("flat")))


class TaiwanBigHolderRankingService:
    def __init__(self, shareholding: TaiwanShareholdingStore | None = None,
                 master: TaiwanSecurityMaster | None = None,
                 watchlist_symbols: Iterable[str] | None = None) -> None:
        self.shareholding = shareholding or TaiwanShareholdingStore()
        self.master = master or get_security_master()
        self.watchlist_symbols = watchlist_symbols

    def snapshot(self) -> dict[str, Any]:
        out: dict[str, Any] = {"date": None, "previous_1w_date": None, "previous_4w_date": None,
                               "min_holders": MIN_HOLDERS, "threshold_pp": THRESHOLD_PP, "universe_count": 0,
                               "concentrating": [], "dispersing": [], "watchlist": []}
        current = self.shareholding.latest_with_change()
        latest = current["date"].max() if not current.is_empty() else None
        if latest is None:
            return out
        previous_1w = current["previous_date"].max()
        data = _with_date_column(self.shareholding.read())
        candidates = [week for week in data["date"].drop_nulls().unique().to_list()
                      if FOUR_WEEK_MIN_DAYS <= (latest - week).days <= FOUR_WEEK_MAX_DAYS]
        previous_4w = min(candidates, key=lambda week: (abs((latest - week).days - FOUR_WEEK_TARGET_DAYS), week),
                          default=None)

        universe = self.master.to_dataframe(supported_only=True).filter(
            (pl.col("listing_status") == "active") & (pl.col("instrument_type") == "stock")
        ).select("symbol", "name", "exchange")
        frame = universe.join(current.select("symbol", "big_holder_pct", "total_holders"), on="symbol", how="inner"
                              ).filter(pl.col("total_holders") >= MIN_HOLDERS)
        frame = _change_vs(_change_vs(frame, data, previous_1w, "1w"), data, previous_4w, "4w")
        symbols = (list(self.watchlist_symbols) if self.watchlist_symbols is not None
                   else [entry["symbol"] for entry in watchlist.list_symbols()])
        frame = frame.with_columns(
            _tone().alias("tone"),
            pl.col("symbol").is_in(symbols).alias("in_watchlist"),
            pl.coalesce("change_4w_pp", "change_1w_pp").alias("_sort_key"),
        )
        ranked = frame.filter(pl.col("_sort_key").is_not_null())
        out.update(
            date=str(latest),
            previous_1w_date=str(previous_1w) if previous_1w else None,
            previous_4w_date=str(previous_4w) if previous_4w else None,
            universe_count=frame.height,
            concentrating=ranked.sort(["_sort_key", "symbol"], descending=[True, False])
            .head(TOP_N).select(ROW_COLUMNS).to_dicts(),
            dispersing=ranked.sort(["_sort_key", "symbol"]).head(TOP_N).select(ROW_COLUMNS).to_dicts(),
            watchlist=frame.filter(pl.col("in_watchlist"))
            .sort(["_sort_key", "symbol"], descending=[True, False], nulls_last=True)
            .select(ROW_COLUMNS).to_dicts(),
        )
        return out

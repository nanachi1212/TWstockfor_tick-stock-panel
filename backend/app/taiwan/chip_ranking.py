"""Daily chip-score ranking (籌碼分數排行) and next-day-sell-pressure tags (隔日沖).

Product opinion, not a deterministic fact: weights follow the public
stock_chips method. Inputs come from existing authorities only:
5-session institutional nets from ``TaiwanInstitutionalStatisticsService``,
20-session buy streaks from the same service,
raw daily OHLCV, official margin rows and the TDCC weekly snapshot.

Each component is a percentile rank inside its own exchange (TWSE and TPEx are
ranked separately). A missing component is skipped and the remaining weights
are renormalized; the item lists which components it used.

Current-market only: it always ranks the latest stored session. Past dates are
not offered because TDCC weeks have no recorded publication time and the
universe is today's listed stocks, so a past ranking could not be PIT-correct.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import polars as pl

from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.institutional_statistics import (
    VALID_FLOW_STATUSES,
    TaiwanInstitutionalStatisticsService,
)
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.shareholding_store import TaiwanShareholdingStore

MIN_AVG_VOLUME_5D = 1_000_000  # shares = 1,000 lots
MIN_CLOSE = 10.0
TOP_N = 20
WEIGHTS = {"flow_today": 25, "flow_5d": 25, "streak": 10, "big_holder_change": 25, "margin_drop": 15}
REQUIRED = ("flow_today", "flow_5d")
# 隔日沖 tag (estimate): up >= 5%, volume >= 2x prior-4 average, close within 1% of high, no institutional net buy.
DTP_CHANGE_MIN = 0.05
DTP_VOLUME_MULTIPLE = 2.0
DTP_NEAR_HIGH = 0.01


def score_frame(df: pl.DataFrame) -> pl.DataFrame:
    """Add chip_score (0-100) from component columns; percentile rank per exchange."""
    ranked = df.with_columns([
        ((pl.col(c).rank("average").over("exchange") - 1)
         / (pl.col(c).count().over("exchange") - 1).clip(lower_bound=1)).alias(f"_r_{c}")
        for c in WEIGHTS
    ])
    weighted = pl.sum_horizontal([pl.col(f"_r_{c}").fill_null(0) * w for c, w in WEIGHTS.items()])
    available = pl.sum_horizontal([pl.col(f"_r_{c}").is_not_null().cast(pl.Int64) * w for c, w in WEIGHTS.items()])
    return ranked.with_columns(
        (weighted / available * 100).alias("chip_score"),
        pl.concat_list([pl.when(pl.col(f"_r_{c}").is_not_null()).then(pl.lit(c)) for c in WEIGHTS])
        .list.drop_nulls().alias("components"),
    ).drop([f"_r_{c}" for c in WEIGHTS])


class TaiwanChipRankingService:
    def __init__(self, *, daily: TaiwanDailyStore | None = None,
                 institutional: TaiwanInstitutionalStore | None = None,
                 margin: TaiwanMarginStore | None = None,
                 shareholding: TaiwanShareholdingStore | None = None,
                 statistics: TaiwanInstitutionalStatisticsService | None = None) -> None:
        self.daily = daily or TaiwanDailyStore()
        self.institutional = institutional or TaiwanInstitutionalStore()
        self.margin = margin or TaiwanMarginStore()
        self.shareholding = shareholding or TaiwanShareholdingStore()
        self.statistics = statistics or TaiwanInstitutionalStatisticsService(self.institutional, self.daily)

    def snapshot(self) -> dict[str, Any]:
        # Rank the latest stored institutional session, not a session still awaiting publication.
        stored = self.institutional.available_dates()
        if stored:
            target = stored[-1]
            sessions, window = self.statistics.get_chip_ranking_inputs(target)
        else:
            # Preserve the empty-store date/session behavior of the public statistics path.
            snap5 = self.statistics.get_snapshot(None, 5)
            target = date.fromisoformat(snap5.date)
            sessions = [date.fromisoformat(s) for s in snap5.sessions]
            window = {}
        master = self.statistics.master.to_dataframe(supported_only=True).filter(
            (pl.col("listing_status") == "active") & (pl.col("instrument_type") == "stock")
        ).select("symbol", "name", "exchange")
        symbols = master["symbol"].to_list()
        out: dict[str, Any] = {"date": str(target), "sessions": [str(session) for session in sessions], "weights": WEIGHTS,
                               "min_avg_volume_5d": MIN_AVG_VOLUME_5D, "min_close": MIN_CLOSE,
                               "big_holder_date": None, "big_holder_previous_date": None,
                               "TWSE": [], "TPEX": [], "day_trade_pressure": []}
        if not symbols or len(sessions) < 5:
            return out

        daily = self.daily.read_range(symbols, sessions[0], target).filter(pl.col("date").is_in(sessions))
        prices = daily.group_by("symbol").agg(
            pl.col("date").n_unique().alias("_n"),
            pl.col("volume").sum().alias("volume_5d"),
            pl.col("volume").mean().alias("avg_volume_5d"),
            pl.col("volume").filter(pl.col("date") != target).mean().alias("_prior4_volume"),
            pl.col("close").filter(pl.col("date") == target).first().alias("close"),
            pl.col("high").filter(pl.col("date") == target).first().alias("high"),
            pl.col("volume").filter(pl.col("date") == target).first().alias("volume"),
            # ponytail: raw previous close, so an ex-dividend day understates the gain; adjust if tags look off.
            pl.col("close").filter(pl.col("date") == sessions[-2]).first().alias("_prev_close"),
        ).filter(pl.col("_n") == len(sessions)).with_columns(
            (pl.col("close") / pl.col("_prev_close") - 1).alias("change_pct"))

        flows = self.institutional.read_range(symbols, target, target).filter(
            pl.col("status").is_in(VALID_FLOW_STATUSES) & (pl.col("trade_date") == pl.col("date"))
            & ~pl.col("has_discrepancy").fill_null(False)
        ).select("symbol", (pl.col("foreign_net") + pl.col("investment_trust_net") + pl.col("dealer_net"))
                 .cast(pl.Float64).alias("institutional_net"))

        window_df = pl.DataFrame(
            [{"symbol": symbol, **values} for symbol, values in window.items()],
            schema={"symbol": pl.String, "institutional_net_5d": pl.Float64,
                    "foreign_buy_streak": pl.Float64, "trust_buy_streak": pl.Float64},
        )

        margin = self.margin.read_range(symbols, target, target).filter(
            pl.col("trade_date") == target).select(
            "symbol", pl.col("margin_change").cast(pl.Float64).alias("margin_change"))

        big = self.shareholding.latest_with_change(symbols, before=target + timedelta(days=1))
        if not big.is_empty():
            out["big_holder_date"] = str(big["date"].max())
            prev = big["previous_date"].drop_nulls()
            out["big_holder_previous_date"] = str(prev.max()) if len(prev) else None
        big = big.select("symbol", "big_holder_pct", pl.col("big_holder_pct_change").alias("big_holder_change"))

        frame = (master.join(prices, on="symbol", how="inner")
                 .join(flows, on="symbol", how="left").join(window_df, on="symbol", how="left")
                 .join(margin, on="symbol", how="left").join(big, on="symbol", how="left")
                 .filter((pl.col("avg_volume_5d") >= MIN_AVG_VOLUME_5D) & (pl.col("close") >= MIN_CLOSE)))

        dtp = frame.filter(
            (pl.col("change_pct") >= DTP_CHANGE_MIN)
            & (pl.col("volume") >= pl.col("_prior4_volume") * DTP_VOLUME_MULTIPLE)
            & ((pl.col("high") - pl.col("close")) / pl.col("close") <= DTP_NEAR_HIGH)
            & (pl.col("institutional_net") <= 0)
        ).with_columns((pl.col("volume") / pl.col("_prior4_volume")).alias("volume_multiple"))
        out["day_trade_pressure"] = dtp.sort("volume_multiple", descending=True).head(TOP_N).select(
            "symbol", "name", "exchange", "close", "change_pct", "volume", "volume_multiple",
            "institutional_net").to_dicts()

        candidates = frame.filter(pl.col("institutional_net") > 0).with_columns(
            (pl.col("institutional_net") / pl.col("avg_volume_5d")).alias("flow_today"),
            (pl.col("institutional_net_5d") / pl.col("volume_5d")).alias("flow_5d"),
            (pl.col("foreign_buy_streak") + pl.col("trust_buy_streak")).alias("streak"),
            pl.col("big_holder_change"),
            (-pl.col("margin_change") / pl.col("avg_volume_5d")).alias("margin_drop"),
        ).filter(pl.all_horizontal([pl.col(c).is_not_null() for c in REQUIRED]))
        if candidates.is_empty():
            return out
        scored = score_frame(candidates).sort(["chip_score", "symbol"], descending=[True, False])
        keep = ["symbol", "name", "exchange", "close", "change_pct", "avg_volume_5d", "chip_score",
                "components", "institutional_net", "institutional_net_5d", "foreign_buy_streak",
                "trust_buy_streak", "margin_change", "big_holder_pct", "big_holder_change"]
        for exchange in ("TWSE", "TPEX"):
            out[exchange] = scored.filter(pl.col("exchange") == exchange).head(TOP_N).select(keep).to_dicts()
        return out

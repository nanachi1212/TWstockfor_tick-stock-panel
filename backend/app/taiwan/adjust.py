"""PIT price normalization primitives; never an implicit all-history adjustment.

All callers supply an explicit event snapshot. An empty event sequence means
the caller has verified there are no events in its window, not that an absent
store proves completeness. These primitives do not certify source coverage.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

import polars as pl

from app.taiwan.corporate_actions import (
    PRICE_COLUMNS,
    PRICE_SUPPORT,
    SHARE_COUNT_EVENTS,
    CorporateActionEvent,
    resolve_event_conflicts,
)
from app.taiwan.providers.taiwan_values import TAIPEI, market_close

PROVENANCE_COLUMNS = ("usage_scope", "adjustment_as_of", "adjustment_status")


def _as_of(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        return value.astimezone(TAIPEI)
    return market_close(value)


@dataclass(frozen=True)
class PITAdjustedPrices:
    _frame: pl.DataFrame
    as_of: datetime
    status: str
    blocked_reasons: tuple[str, ...] = ()
    usage_scope: Literal["pit_feature"] = "pit_feature"

    def to_frame(self) -> pl.DataFrame:
        return self._frame.clone()


@dataclass(frozen=True)
class PresentationAdjustedPrices:
    """Distinct type, plus persistent column tags on every exported frame."""

    _frame: pl.DataFrame
    as_of: datetime
    status: str
    blocked_reasons: tuple[str, ...] = ()
    usage_scope: Literal["presentation_only"] = "presentation_only"

    def to_frame(self) -> pl.DataFrame:
        return self._frame.clone()


def reject_presentation(frame: object) -> None:
    if isinstance(frame, PresentationAdjustedPrices):
        raise ValueError("presentation_only series cannot enter a feature/training path")
    if (isinstance(frame, pl.DataFrame) and "usage_scope" in frame.columns
            and "presentation_only" in frame["usage_scope"].to_list()):
        raise ValueError("presentation_only series cannot enter a feature/training path")


def assert_training_safe(frame: object) -> None:
    """Reject presentation data and windows anchored after their feature rows.

    A PIT window anchored at T can compute features for T only. Its historical
    rows cannot be relabelled as features for t<T (even though some ratios happen
    to be invariant). Callers construct a separate window for each feature date.
    """
    reject_presentation(frame)
    if not isinstance(frame, pl.DataFrame):
        raise TypeError("training requires a tagged Polars frame")
    if "usage_scope" not in frame.columns:
        if "price_semantics" in frame.columns and "adjusted" in frame["price_semantics"].to_list():
            raise ValueError("adjusted training data requires PIT provenance")
        return  # Backward compatibility for existing raw feature frames.
    if not set(PROVENANCE_COLUMNS).issubset(frame.columns):
        raise ValueError("adjustment provenance is incomplete")
    for row in frame.select("date", *PROVENANCE_COLUMNS).iter_rows(named=True):
        if row["usage_scope"] != "pit_feature" or row["adjustment_status"] != "verified":
            raise ValueError("data_insufficient adjusted data cannot train")
        anchor = _as_of(datetime.fromisoformat(row["adjustment_as_of"]))
        if row["date"] != anchor.date():
            raise ValueError("historical feature row requires its own as_of anchor")


def validate_feature_window(frame: pl.DataFrame, *, as_of: date | None = None) -> None:
    """A numeric window can have one PIT anchor only, and may never be presentation."""
    reject_presentation(frame)
    if "usage_scope" not in frame.columns:
        return
    if not set(PROVENANCE_COLUMNS) <= set(frame.columns):
        raise ValueError("incomplete adjustment provenance")
    if frame.is_empty():
        return
    if set(frame["usage_scope"].to_list()) != {"pit_feature"}:
        raise ValueError("unverified feature usage scope")
    anchors = frame["adjustment_as_of"].unique().to_list()
    if len(anchors) != 1 or anchors[0] is None:
        raise ValueError("feature window must use a single as_of anchor")
    anchor = _as_of(datetime.fromisoformat(anchors[0]))
    if as_of is not None and anchor.date() != as_of:
        raise ValueError("feature window anchor differs from requested as_of")


def _raw_history(history: pl.DataFrame, price_columns: tuple[str, ...]) -> None:
    if not isinstance(history, pl.DataFrame):
        raise TypeError("normalization requires raw Polars OHLCV")
    if "usage_scope" in history.columns:
        raise ValueError("normalization requires raw prices; double adjustment is forbidden")
    if not price_columns or len(set(price_columns)) != len(price_columns):
        raise ValueError("price_columns must be distinct and nonempty")
    if not set(price_columns) <= set(PRICE_COLUMNS):
        raise ValueError("only audited OHLC price columns may be adjusted; volume is raw")
    if not {"symbol", "date", *price_columns} <= set(history.columns):
        raise ValueError("raw price history is missing required columns")
    if history.schema["date"] != pl.Date:
        raise ValueError("history date must be an explicit trading-session Date")
    if history.select(pl.struct("symbol", "date").is_duplicated().any()).item():
        raise ValueError("ambiguous duplicate symbol/session")
    if history["date"].null_count() or history["symbol"].null_count():
        raise ValueError("history identity cannot be missing")


def _share_change(event: CorporateActionEvent) -> bool:
    return (event.event_type in SHARE_COUNT_EVENTS
            or (event.free_share_ratio is not None and event.free_share_ratio > 0)
            or event.status != "verified")


def adjust_prices_as_of(
    history: pl.DataFrame, *, as_of: date | datetime,
    events: Iterable[CorporateActionEvent],
    price_columns: tuple[str, ...] = PRICE_COLUMNS,
) -> PITAdjustedPrices:
    """For raw row t apply factors only for t < effective_date and effective_at<=T.

    A date anchor denotes regular market close (13:30 Asia/Taipei). An aware
    timestamp anchor is exact; a daily bar is visible only at its session close.
    No raw file or input DataFrame is modified. Amount and volume remain raw.
    """
    _raw_history(history, price_columns)
    anchor = _as_of(as_of)
    last_day = anchor.date()
    cutoff = pl.col("date") <= last_day
    if anchor < market_close(last_day):
        cutoff = pl.col("date") < last_day
    frame = history.filter(cutoff).with_columns(
        pl.lit(1.0).alias("_price_factor"),
        pl.lit(0).alias("_share_count_epoch"),
        pl.lit("verified").alias("adjustment_status"),
    )
    symbols = set(frame["symbol"].to_list())
    selected = resolve_event_conflicts(e for e in events
                                       if e.effective_at <= anchor and e.symbol in symbols)
    reasons: set[str] = set()
    for event in selected:
        symbol = pl.col("symbol") == event.symbol
        before = symbol & (pl.col("date") < event.effective_date)
        supported = set(price_columns) <= PRICE_SUPPORT.get((event.exchange, event.event_type), frozenset())
        if event.status != "verified" or not supported:
            if frame.filter(before).height:
                reasons.add(event.reason or "unsupported_event_price_column")
                frame = frame.with_columns(
                    pl.when(before).then(None).otherwise(pl.col("_price_factor")).alias("_price_factor"),
                    pl.when(before).then(pl.lit("data_insufficient"))
                    .otherwise(pl.col("adjustment_status")).alias("adjustment_status"),
                )
        else:
            frame = frame.with_columns(
                pl.when(before).then(pl.col("_price_factor") * event.factor)
                .otherwise(pl.col("_price_factor")).alias("_price_factor"))
        if _share_change(event):
            frame = frame.with_columns(
                (pl.col("_share_count_epoch")
                 + (symbol & (pl.col("date") >= event.effective_date)).cast(pl.Int32))
                .alias("_share_count_epoch"))
    frame = frame.with_columns(
        [(pl.col(c) * pl.col("_price_factor")).alias(c) for c in price_columns]
    ).with_columns(
        pl.lit("pit_feature").alias("usage_scope"),
        pl.lit(anchor.isoformat()).alias("adjustment_as_of"),
    ).drop("_price_factor")
    if frame.select(pl.any_horizontal(
        [pl.col(c).is_null() | ~pl.col(c).is_finite() | (pl.col(c) <= 0)
         for c in price_columns]).any()).item():
        reasons.add("incomplete_or_invalid_price_window")
    if reasons:
        # Any unusable input in this anchor's window invalidates derived features
        # at the anchor too; a later raw close cannot hide an earlier missing factor.
        frame = frame.with_columns(pl.lit("data_insufficient").alias("adjustment_status"))
    return PITAdjustedPrices(frame, anchor, "data_insufficient" if reasons else "verified",
                             tuple(sorted(reasons)))


def adjust_prices_for_presentation(
    history: pl.DataFrame, *, as_of: date | datetime,
    events: Iterable[CorporateActionEvent],
    price_columns: tuple[str, ...] = PRICE_COLUMNS,
) -> PresentationAdjustedPrices:
    """Today's chart anchor is explicit; output can never enter training_matrix."""
    result = adjust_prices_as_of(history, as_of=as_of, events=events, price_columns=price_columns)
    return PresentationAdjustedPrices(
        result.to_frame().with_columns(pl.lit("presentation_only").alias("usage_scope")),
        result.as_of, result.status, result.blocked_reasons)


@dataclass(frozen=True)
class ForwardAdjustedReturn:
    value: float | None
    status: str
    start_session: date
    end_session: date | None
    reason: str | None = None


def forward_adjusted_return(
    history: pl.DataFrame, *, start_session: date, horizon_sessions: int,
    events: Iterable[CorporateActionEvent],
) -> ForwardAdjustedReturn:
    """Single-symbol realized price-normalized close return, not total return.

    T and T+H are session CLOSES. Select the H-th subsequent supplied trading
    bar (no weekday arithmetic). Events at the start session's open are already
    in its raw close and excluded; events at the end session's open are included.
    The interval is (close(T), close(T+H)]. Callers must provide contiguous,
    verified session history; this primitive does not infer missing sessions.
    """
    _raw_history(history, ("close",))
    if not isinstance(horizon_sessions, int) or isinstance(horizon_sessions, bool) or horizon_sessions <= 0:
        raise ValueError("horizon_sessions must be a positive integer")
    if history["symbol"].n_unique() != 1:
        raise ValueError("forward return requires exactly one symbol")
    ordered = history.sort("date")
    dates = ordered["date"].to_list()
    if start_session not in dates or dates.index(start_session) + horizon_sessions >= len(dates):
        return ForwardAdjustedReturn(None, "data_insufficient", start_session, None, "insufficient_horizon")
    end = dates[dates.index(start_session) + horizon_sessions]
    window = ordered.filter(pl.col("date").is_between(start_session, end))
    # Reuse exactly the feature normalization machinery with the label anchor.
    result = adjust_prices_as_of(window, as_of=market_close(end), events=events,
                                 price_columns=("close",))
    values = result.to_frame()["close"].to_list()
    if (result.status != "verified" or any(v is None or not math.isfinite(v) or v <= 0
                                          for v in (values[0], values[-1]))):
        return ForwardAdjustedReturn(None, "data_insufficient", start_session, end,
                                     ";".join(result.blocked_reasons) or "missing_close")
    return ForwardAdjustedReturn(values[-1] / values[0] - 1, "verified", start_session, end)


def window_crosses_share_count_event(
    events: Iterable[CorporateActionEvent], *, symbol: str,
    start_session: date, end_session: date,
) -> bool:
    """Window crossing is start < event date <= end; start's bar is post-event."""
    if start_session > end_session:
        raise ValueError("window start follows end")
    return any(e.symbol == symbol and start_session < e.effective_date <= end_session
               and _share_change(e) for e in events)


def volume_window_status(
    events: Iterable[CorporateActionEvent], *, symbol: str,
    start_session: date, end_session: date, measure: str = "volume",
) -> str:
    if measure in {"amount", "ADV20_TWD"}:
        return "verified"
    if measure not in {"volume", "relative_volume", "volume_ma", "volume_momentum"}:
        raise ValueError("unknown volume/amount measure")
    return ("data_insufficient" if window_crosses_share_count_event(
        events, symbol=symbol, start_session=start_session, end_session=end_session) else "verified")

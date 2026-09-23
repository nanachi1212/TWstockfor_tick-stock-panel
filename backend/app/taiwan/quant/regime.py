"""Deterministic market-level regime classification.

V1 is split in two because of what the A2 probe found.

A. Market-level components — implemented.
   Index trend, realized volatility, turnover trend, breadth. All computable
   from observed prices, all deterministic: same inputs, same thresholds, same
   verdict. No smoothing surprises, no fitted parameters, no lookahead.

B. Industry-dependent components — deliberately disabled.
   ``docs/taiwan-historical-universe-probe.md`` §4.3 established that the
   official TWSE industry label is the *current* classification, not the
   historical one (a 2015 query returns categories TWSE created in 2021), and
   that 435 of 855 codes sit in more than one industry table. So industry
   breadth, historical industry rank and rotation cannot be computed for a past
   date from any source available today. They are registered here with
   ``status = not_pit_safe`` and contribute nothing to the verdict, rather than
   being silently computed from today's classification.

Every component reports its raw value, threshold, status, as_of and source, so
a regime call is auditable rather than a bare label.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any

import polars as pl

from app.taiwan.adjust import reject_presentation, validate_feature_window


class Regime(StrEnum):
    RISK_ON = "risk_on"
    NEUTRAL = "neutral"
    RISK_OFF = "risk_off"


class ComponentStatus(StrEnum):
    OK = "ok"
    DATA_INSUFFICIENT = "data_insufficient"
    #: Computable only from a non-point-in-time input; refused on principle.
    NOT_PIT_SAFE = "not_pit_safe"


#: Components that need a historical TWSE industry label. Probe §4.3 proved no
#: point-in-time source exists, so they stay disabled until one does.
INDUSTRY_DEPENDENT_COMPONENTS: tuple[str, ...] = (
    "industry_breadth",
    "historical_industry_rank",
    "industry_rotation",
)


@dataclass(frozen=True)
class RegimeComponent:
    name: str
    raw_value: float | None
    threshold: float | None
    status: ComponentStatus
    source: str
    as_of: date | None = None
    #: -1 risk-off, 0 neutral, +1 risk-on. Always 0 when status is not OK.
    vote: int = 0
    note: str | None = None

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "raw_value": self.raw_value,
            "threshold": self.threshold,
            "status": self.status.value,
            "source": self.source,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "vote": self.vote,
            "note": self.note,
        }


@dataclass(frozen=True)
class RegimeVerdict:
    regime: Regime
    as_of: date
    components: tuple[RegimeComponent, ...] = ()
    disabled_components: tuple[RegimeComponent, ...] = ()

    @property
    def score(self) -> int:
        return sum(c.vote for c in self.components)

    def describe(self) -> dict[str, Any]:
        return {
            "regime": self.regime.value,
            "as_of": self.as_of.isoformat(),
            "score": self.score,
            "components": [c.describe() for c in self.components],
            "disabled_components": [c.describe() for c in self.disabled_components],
        }


@dataclass(frozen=True)
class RegimeThresholds:
    """Fixed, inspectable cut points. Policy, not fitted parameters."""

    ma_short: int = 20
    ma_long: int = 60
    #: Annualised realized volatility above this is risk-off.
    high_volatility: float = 0.30
    #: …and below this is risk-on.
    low_volatility: float = 0.15
    volatility_window: int = 20
    #: Turnover 20d mean over 60d mean.
    turnover_expansion: float = 1.10
    turnover_contraction: float = 0.90
    #: Share of stocks above their 20-session MA.
    breadth_strong: float = 0.60
    breadth_weak: float = 0.40
    #: Minimum sessions before the index components can be trusted at all.
    min_sessions: int = 60


def _vote(value: float, *, low: float, high: float, invert: bool = False) -> int:
    """+1 above *high*, -1 below *low*, else 0. ``invert`` flips the sign."""
    if value > high:
        outcome = 1
    elif value < low:
        outcome = -1
    else:
        outcome = 0
    return -outcome if invert else outcome


def _disabled_industry_components(as_of: date) -> tuple[RegimeComponent, ...]:
    return tuple(
        RegimeComponent(
            name=name,
            raw_value=None,
            threshold=None,
            status=ComponentStatus.NOT_PIT_SAFE,
            source="twse:MI_INDEX:industry_tables",
            as_of=as_of,
            vote=0,
            note=(
                "TWSE publishes only the current industry label and it is not "
                "unique (probe §4.3); a historical industry value cannot be "
                "established, so this component contributes nothing."
            ),
        )
        for name in INDUSTRY_DEPENDENT_COMPONENTS
    )


def classify_market_regime(
    index_history: pl.DataFrame,
    *,
    as_of: date | None = None,
    breadth_above_ma20: float | None = None,
    thresholds: RegimeThresholds | None = None,
    close_col: str = "close",
    turnover_col: str = "amount",
    source: str = "twse:index",
) -> RegimeVerdict:
    """Classify the market regime from index history plus optional breadth.

    ``index_history`` needs ``date`` and *close_col*; *turnover_col* enables the
    turnover component when present. ``breadth_above_ma20`` is the share of the
    universe trading above its own 20-session MA, supplied by the caller
    because it depends on which universe tier is in play.

    Deterministic: no random state, no fitting, no hidden smoothing. A component
    that cannot be computed reports ``data_insufficient`` and abstains — it
    never falls back to a made-up number.
    """
    validate_feature_window(index_history, as_of=as_of)
    if ("adjustment_status" in index_history.columns
            and set(index_history["adjustment_status"].to_list()) - {"verified"}):
        raise ValueError("data_insufficient adjustment cannot enter market regime")
    gates = thresholds or RegimeThresholds()
    if index_history.is_empty():
        raise ValueError("regime classification needs index history")

    frame = index_history.sort("date")
    resolved_as_of = as_of or frame["date"].to_list()[-1]
    frame = frame.filter(pl.col("date") <= resolved_as_of)
    if frame.is_empty():
        raise ValueError("regime classification has no history at as_of")
    sessions = frame.height
    components: list[RegimeComponent] = []

    def insufficient(name: str, note: str) -> RegimeComponent:
        return RegimeComponent(name, None, None, ComponentStatus.DATA_INSUFFICIENT,
                               source, resolved_as_of, 0, note)

    # 1. Index trend: close vs MA20 / MA60.
    if sessions < gates.min_sessions:
        components.append(insufficient(
            "index_trend",
            f"{sessions} sessions < {gates.min_sessions} required"))
        components.append(insufficient("realized_volatility", "index trend window short"))
    else:
        closes = frame[close_col].to_list()
        last = closes[-1]
        ma_short = sum(closes[-gates.ma_short:]) / gates.ma_short
        ma_long = sum(closes[-gates.ma_long:]) / gates.ma_long
        trend_vote = 1 if (last > ma_short and ma_short > ma_long) else (
            -1 if (last < ma_short and ma_short < ma_long) else 0)
        components.append(RegimeComponent(
            "index_trend", raw_value=last / ma_long - 1.0,
            threshold=0.0, status=ComponentStatus.OK, source=source,
            as_of=resolved_as_of, vote=trend_vote,
            note=f"close={last:.2f} ma{gates.ma_short}={ma_short:.2f} "
                 f"ma{gates.ma_long}={ma_long:.2f}",
        ))

        # 2. Realized volatility, annualised from daily log-ish returns.
        window = frame.tail(gates.volatility_window + 1)[close_col].to_list()
        returns = [window[i] / window[i - 1] - 1.0 for i in range(1, len(window))]
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / max(len(returns) - 1, 1)
        realized = (variance ** 0.5) * (252 ** 0.5)
        components.append(RegimeComponent(
            "realized_volatility", raw_value=realized,
            threshold=gates.high_volatility, status=ComponentStatus.OK, source=source,
            as_of=resolved_as_of,
            vote=_vote(realized, low=gates.low_volatility,
                       high=gates.high_volatility, invert=True),
            note=f"{gates.volatility_window}-session annualised",
        ))

    # 3. Turnover trend.
    if turnover_col in frame.columns and sessions >= gates.ma_long:
        turnover = frame[turnover_col].to_list()
        short_mean = sum(turnover[-gates.ma_short:]) / gates.ma_short
        long_mean = sum(turnover[-gates.ma_long:]) / gates.ma_long
        ratio = (short_mean / long_mean) if long_mean else 0.0
        components.append(RegimeComponent(
            "turnover_trend", raw_value=ratio, threshold=gates.turnover_expansion,
            status=ComponentStatus.OK, source=source, as_of=resolved_as_of,
            vote=_vote(ratio, low=gates.turnover_contraction,
                       high=gates.turnover_expansion),
            note=f"mean({gates.ma_short}) / mean({gates.ma_long})",
        ))
    else:
        components.append(insufficient(
            "turnover_trend", f"{turnover_col!r} missing or history too short"))

    # 4. Breadth.
    if breadth_above_ma20 is None:
        components.append(insufficient(
            "breadth_above_ma20", "caller supplied no breadth for this universe tier"))
    else:
        components.append(RegimeComponent(
            "breadth_above_ma20", raw_value=breadth_above_ma20,
            threshold=gates.breadth_strong, status=ComponentStatus.OK,
            source="pit_universe", as_of=resolved_as_of,
            vote=_vote(breadth_above_ma20, low=gates.breadth_weak,
                       high=gates.breadth_strong),
            note="share of the tier's symbols above their own 20-session MA",
        ))

    usable = [c for c in components if c.status is ComponentStatus.OK]
    score = sum(c.vote for c in usable)
    if not usable:
        regime = Regime.NEUTRAL
    elif score > 0:
        regime = Regime.RISK_ON
    elif score < 0:
        regime = Regime.RISK_OFF
    else:
        regime = Regime.NEUTRAL

    return RegimeVerdict(
        regime=regime,
        as_of=resolved_as_of,
        components=tuple(components),
        disabled_components=_disabled_industry_components(resolved_as_of),
    )


def market_breadth_above_ma(panel: pl.DataFrame, as_of: date, ma_col: str = "ma20",
                            close_col: str = "close") -> float | None:
    """Share of symbols trading above *ma_col* on *as_of*.

    Returns ``None`` when no symbol has a warmed-up MA that session — an honest
    "unknown" rather than a breadth of 0.0, which would read as maximally
    risk-off.
    """
    reject_presentation(panel)
    if panel.is_empty():
        return None
    day = panel.filter((pl.col("date") == as_of) & pl.col(ma_col).is_not_null())
    validate_feature_window(day, as_of=as_of)
    if "adjustment_status" in day.columns:
        day = day.filter(pl.col("adjustment_status") == "verified")
    if day.is_empty():
        return None
    above = day.filter(pl.col(close_col) > pl.col(ma_col)).height
    return above / day.height


@dataclass(frozen=True)
class RegimeSeries:
    """Regime evaluated across many sessions (for fold-level conditioning)."""

    verdicts: tuple[RegimeVerdict, ...] = field(default_factory=tuple)

    def describe(self) -> list[dict[str, Any]]:
        return [v.describe() for v in self.verdicts]

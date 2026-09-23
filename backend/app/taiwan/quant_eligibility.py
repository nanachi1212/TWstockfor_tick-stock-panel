"""Model policy layer — who may enter a backtest or a training set.

Strictly downstream of ``app/taiwan/pit_universe.py``.  That module reports what
the market showed and how well it is evidenced; this one applies *policy* on top.
Keeping them in separate modules is the point: market truth must never be edited
to make a model's universe look better.

Two tiers, never merged
-----------------------
``PRIMARY_VERIFIED``  ``TWSE Verified OOS`` — exchange is TWSE, the code was
                      observed that session, and its instrument type was
                      verified from that date's own official response as
                      ``stock``.  This is the only tier permitted to carry an
                      out-of-sample performance claim.

``SECONDARY_OBSERVED`` ``TWSE + TPEx Observed Experimental`` — adds TPEx
                      observations, whose instrument type is BLOCKED (probe
                      §9.4).  Experimental only.

Reporting the two together as one "survivorship-free Taiwan universe" is
forbidden; ``describe_tier`` exists so labels stay honest in UI and artifacts.

Availability policy exception
-----------------------------
Taiwan corporate-action tables publish no ``available_at`` (audit §5), but price
normalization consumes ``effective_at``, not ``available_at`` (audit §8).  The
narrow exception below lets corporate-action normalization through while every
other dataset — fundamentals, revenue, institutional flow, news — still needs a
verified ``available_at`` to be usable as a predictive feature.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import polars as pl

Tier = Literal["primary_verified", "secondary_observed"]

PRIMARY_VERIFIED: Tier = "primary_verified"
SECONDARY_OBSERVED: Tier = "secondary_observed"

TIER_LABELS: dict[Tier, str] = {
    PRIMARY_VERIFIED: "TWSE Verified OOS",
    SECONDARY_OBSERVED: "TWSE + TPEx Observed Experimental",
}

#: Only these may carry an out-of-sample performance claim.
OOS_CLAIMABLE_TIERS = frozenset({PRIMARY_VERIFIED})

#: (dataset, availability_policy) pairs allowed to be used without a verified
#: ``available_at``.  Deliberately one entry — see the module docstring.
PIT_POLICY_EXCEPTIONS: frozenset[tuple[str, str]] = frozenset({
    ("corporate_action", "market_mechanism_inferred"),
})


def pit_usable(dataset: str, *, available_at: object, availability_policy: str | None) -> bool:
    """Whether a record may be consumed as of its own timestamp.

    A verified ``available_at`` always qualifies.  Without one, only the narrow
    corporate-action normalization exception does — a fundamentals or revenue
    record carrying the same policy string does **not**.
    """
    if available_at is not None:
        return True
    return (dataset, availability_policy or "") in PIT_POLICY_EXCEPTIONS


def describe_tier(tier: Tier) -> str:
    """The label that must be used wherever this tier's results are shown."""
    return TIER_LABELS[tier]


def assert_oos_claimable(tier: Tier) -> None:
    """Guard for anything that publishes performance numbers."""
    if tier not in OOS_CLAIMABLE_TIERS:
        raise ValueError(
            f"{describe_tier(tier)!r} may not carry an out-of-sample performance "
            "claim; its instrument types are not verified. Report it separately "
            "from " + describe_tier(PRIMARY_VERIFIED)
        )


@dataclass(frozen=True)
class EligibilityPolicy:
    """A versioned, inspectable set of model-admission rules.

    Every knob here is *policy*, not market truth.  Bumping ``version`` is how a
    change becomes auditable against previously produced artifacts.
    """

    version: str = "v1"
    tier: Tier = PRIMARY_VERIFIED
    #: Sessions a symbol must already have been observed for (IPO warm-up).
    min_warmup_sessions: int = 20
    #: Minimum 20-session average turnover in TWD.  Amount, never share volume —
    #: volume adjustment across share-count changes is data_insufficient
    #: (audit §9), so share counts are not comparable across such events.
    min_adv20_twd: float = 0.0
    #: Instrument types admitted.  ETFs are classified but are not the stock
    #: universe, so the default admits stocks only.
    instrument_types: frozenset[str] = field(default_factory=lambda: frozenset({"stock"}))

    def describe(self) -> dict[str, object]:
        return {
            "version": self.version,
            "tier": self.tier,
            "tier_label": describe_tier(self.tier),
            "oos_claimable": self.tier in OOS_CLAIMABLE_TIERS,
            "min_warmup_sessions": self.min_warmup_sessions,
            "min_adv20_twd": self.min_adv20_twd,
            "instrument_types": sorted(self.instrument_types),
        }


def market_truth_gate(universe: pl.DataFrame, policy: EligibilityPolicy) -> pl.DataFrame:
    """Apply only the tier's market-truth requirements.

    Separated from the liquidity/warm-up filters so it is visible exactly where
    truth stops and policy starts.
    """
    if policy.tier not in (PRIMARY_VERIFIED, SECONDARY_OBSERVED):
        raise ValueError("historical universe tier required")
    if "universe_contract" in universe.columns:
        raise ValueError("live universe cannot enter historical admission")
    if universe.is_empty():
        return universe
    gated = universe.filter(pl.col("observed_on_market"))
    if policy.tier == PRIMARY_VERIFIED:
        gated = gated.filter(
            (pl.col("exchange") == "TWSE")
            & (pl.col("instrument_type_status") == "verified")
            & (pl.col("instrument_type").is_in(sorted(policy.instrument_types)))
        )
    return gated


def eligible(
    universe: pl.DataFrame,
    policy: EligibilityPolicy,
    *,
    warmup_sessions: dict[str, int] | None = None,
    adv20_twd: dict[str, float] | None = None,
) -> pl.DataFrame:
    """Market-truth gate, then the policy filters.

    ``warmup_sessions`` / ``adv20_twd`` are keyed by ``market_symbol``.  A symbol
    with no entry fails a non-zero threshold: absent evidence is not a pass.
    """
    gated = market_truth_gate(universe, policy)
    return apply_policy_filters(gated, min_warmup_sessions=policy.min_warmup_sessions,
                                min_adv20_twd=policy.min_adv20_twd,
                                warmup_sessions=warmup_sessions, adv20_twd=adv20_twd)


def apply_policy_filters(
    gated: pl.DataFrame, *, min_warmup_sessions: int, min_adv20_twd: float,
    warmup_sessions: dict[str, int] | None = None,
    adv20_twd: dict[str, float] | None = None,
) -> pl.DataFrame:
    """Shared numerical policy AFTER independent historical/live truth gates."""
    if gated.is_empty():
        return gated

    if min_warmup_sessions > 0:
        counts = warmup_sessions or {}
        gated = gated.filter(
            pl.col("market_symbol")
            .replace_strict(counts, default=0, return_dtype=pl.Int64)
            >= min_warmup_sessions
        )
    if min_adv20_twd > 0:
        values = adv20_twd or {}
        gated = gated.filter(
            pl.col("market_symbol")
            .replace_strict(values, default=0.0, return_dtype=pl.Float64)
            >= min_adv20_twd
        )
    return gated

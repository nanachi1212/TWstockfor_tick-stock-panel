"""Single admission path from factor panel to model-facing training rows."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import polars as pl

from app.taiwan.quant.feature_manifest import (
    DatasetCapability,
    EligibilityResolution,
    FeatureManifest,
    resolve_training_eligibility,
    training_matrix,
)
from app.taiwan.quant.panel import INDUSTRY_DISABLED, FactorPanel
from app.taiwan.quant_eligibility import EligibilityPolicy, eligible


@dataclass(frozen=True)
class TrainingMatrixResult:
    matrix: pl.DataFrame
    resolution: EligibilityResolution
    rejected_symbols: tuple[str, ...]
    reasons: dict[str, tuple[str, ...]]

    @property
    def rejected_features(self) -> tuple[str, ...]:
        return self.resolution.rejected_features


def panel_training_matrix(
    panel: FactorPanel, universe: pl.DataFrame, policy: EligibilityPolicy,
    manifest: FeatureManifest, capabilities: dict[str, DatasetCapability], *,
    warmup_sessions: dict[tuple[date, str], int] | None = None,
    adv20_twd: dict[tuple[date, str], float] | None = None,
) -> TrainingMatrixResult:
    # Historical admission cannot reuse a current/full-sample symbol-only map.
    for name, entries in (("warmup_sessions", warmup_sessions), ("adv20_twd", adv20_twd)):
        if any(not isinstance(key, tuple) or len(key) != 2
               or type(key[0]) is not date or not isinstance(key[1], str)
               for key in entries or {}):
            raise ValueError(f"{name} requires (date, symbol) keys")
    if panel.universe_tier not in ("primary_verified", "secondary_observed"):
        raise ValueError("live universe cannot enter historical training")
    if policy.version != panel.policy_version or policy.tier != panel.universe_tier:
        raise ValueError("panel and eligibility policy identity must match")
    if panel.values.is_empty():
        resolution = resolve_training_eligibility(manifest, capabilities)
        return TrainingMatrixResult(pl.DataFrame(), resolution, (), {})
    if "date" not in universe.columns or "market_symbol" not in universe.columns:
        raise ValueError("universe requires date and market_symbol")
    if universe.select(pl.struct("market_symbol", "date").is_duplicated().any()).item():
        raise ValueError("duplicate universe symbol/date")
    resolution = resolve_training_eligibility(manifest, capabilities)
    if set(resolution.eligible_features) & set(INDUSTRY_DISABLED):
        raise ValueError("not_pit_safe: historical industry factor cannot enter training")
    admitted: list[pl.DataFrame] = []
    rejected: set[str] = set()
    reasons: dict[str, tuple[str, ...]] = {}
    for day in panel.values["date"].unique().sort().to_list():
        same_day = panel.values.filter(pl.col("date") == day)
        candidates = universe.filter(pl.col("date") == day)
        symbols = candidates["market_symbol"].to_list()
        warmup = {symbol: warmup_sessions[(day, symbol)] for symbol in symbols
                  if (day, symbol) in (warmup_sessions or {})}
        liquidity = {symbol: adv20_twd[(day, symbol)] for symbol in symbols
                     if (day, symbol) in (adv20_twd or {})}
        allowed = set(eligible(candidates, policy, warmup_sessions=warmup,
                              adv20_twd=liquidity)["market_symbol"].to_list())
        for symbol in same_day["symbol"].to_list():
            if symbol not in allowed:
                rejected.add(symbol)
                reasons[f"{day}:{symbol}"] = ("universe_or_policy_ineligible",)
        safe = same_day.filter(pl.col("symbol").is_in(sorted(allowed)) &
                               (pl.col("adjustment_status") == "verified"))
        for symbol in same_day.filter(pl.col("symbol").is_in(sorted(allowed)) &
                                      (pl.col("adjustment_status") != "verified"))["symbol"].to_list():
            rejected.add(symbol)
            reasons[f"{day}:{symbol}"] = ("adjustment_unverified",)
        if safe.height:
            admitted.append(safe)
    selected = pl.concat(admitted) if admitted else panel.values.head(0)
    matrix, checked = training_matrix(
        selected, manifest, capabilities,
        keep_columns=("date", "symbol", "factor_version", "policy_version", "universe_tier"),
    )
    assert checked == resolution
    matrix = matrix.with_columns(pl.lit(manifest.model_version).alias("feature_schema_version"))
    for verdict in resolution.verdicts:
        if not verdict.eligible:
            reasons[f"feature:{verdict.feature_name}"] = tuple(reason.value for reason in verdict.reasons)
    return TrainingMatrixResult(matrix.sort(["date", "symbol"]), resolution,
                                tuple(sorted(rejected)), reasons)

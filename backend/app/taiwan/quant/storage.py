"""Immutable, atomically published factor partitions and provenance metadata."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import polars as pl

from app.taiwan.quant.panel import (
    CHIP,
    FACTORS,
    INDUSTRY_DISABLED,
    LIQUIDITY,
    MARGIN,
    RELATIVE,
    TECHNICAL,
    FactorPanel,
)

VALUE_IDENTITY = (
    "date", "symbol", "factor_version", "policy_version", "universe_tier",
    "usage_scope", "adjustment_as_of", "adjustment_status",
)
COVERAGE_COLUMNS = ("symbol", "date", "factor", "status", "as_of", "available_at", "reason", "source")


def _min_history(name: str) -> int:
    fixed = {
        "ma5": 5, "ma10": 10, "ma20": 20, "ma60": 60,
        "ma20_slope": 21, "ma60_slope": 61,
        "rsi_14": 15, "atr_14": 15, "volatility_20d": 21,
        "relative_volume": 20, "adv20_twd": 20, "distance_to_ma20": 20,
        "macd_dif": 35, "macd_dea": 35, "macd_hist": 35, "macd_hist_streak": 35,
    }
    if name in fixed:
        return fixed[name]
    if name.startswith(("foreign_net_", "trust_net_", "dealer_net_")):
        return int(name.rsplit("_", 1)[1].removesuffix("d"))
    if name.startswith(("momentum_", "stock_return_", "market_return_", "relative_to_market_")):
        return int(name.rsplit("_", 1)[1].removesuffix("d")) + 1
    if name.startswith(("margin_change_", "short_change_")):
        return int(name.rsplit("_", 1)[1].removesuffix("d")) + 1
    return 1


def factor_meta() -> dict[str, object]:
    """Published formula and input contracts; eligibility remains dynamic."""
    formulas = {
        "ma5": "mean(close,5)", "ma10": "mean(close,10)",
        "ma20": "mean(close,20)", "ma60": "mean(close,60)",
        "ma20_slope": "ma20(T)/ma20(T-1)-1", "ma60_slope": "ma60(T)/ma60(T-1)-1",
        "rsi_14": "Wilder RSI(14)", "macd_dif": "EMA12-EMA26",
        "macd_dea": "EMA9(macd_dif)", "macd_hist": "2*(macd_dif-macd_dea)",
        "macd_hist_streak": "consecutive same-sign macd_hist sessions; signed",
        "momentum_5d": "close(T)/close(T-5)-1",
        "momentum_20d": "close(T)/close(T-20)-1",
        "momentum_60d": "close(T)/close(T-60)-1",
        "distance_to_ma20": "close/ma20-1",
        "atr_14": "mean(max(high-low,abs(high-prev_close),abs(low-prev_close)),14)",
        "volatility_20d": "population stddev of 20 daily close returns",
        "relative_volume": "volume(T)/mean(volume,20); same share-count epoch",
        "amount": "raw exchange amount", "adv20_twd": "mean(raw amount,20)",
        "margin_balance": "verified balance", "margin_change_1d": "balance(T)-balance(T-1)",
        "margin_change_5d": "balance(T)-balance(T-5)",
        "short_balance": "verified short balance",
        "short_change_5d": "short_balance(T)-short_balance(T-5)",
        "short_margin_ratio": "short_balance/margin_balance",
    }
    result = {}
    for name in FACTORS:
        if name in TECHNICAL:
            group, dataset, semantics = "technical", "corporate_action", "pit_adjusted_ohlc"
        elif name in LIQUIDITY:
            group, dataset, semantics = "liquidity", "daily", "raw_amount_or_volume"
        elif name in CHIP:
            group, dataset, semantics = "chip", "institutional", "not_price"
        elif name in MARGIN:
            group, dataset, semantics = "margin", "margin", "not_price"
        elif name in RELATIVE:
            group = "relative_strength"
            dataset = ("market_index" if name.startswith(("market_return_", "relative_to_market_"))
                       else "corporate_action")
            semantics = "pit_adjusted_close_and_index"
        else:
            group, dataset, semantics = "industry", "historical_industry", "not_pit_safe"
        if name.startswith(("foreign_net", "trust_net", "dealer_net")):
            field, n = name.rsplit("_", 1)
            formulas[name] = f"sum({field},{n})"
        elif name.endswith("streak"):
            formulas[name] = "consecutive signed daily net-flow sessions"
        elif name.startswith("institutional_sync"):
            formulas[name] = "all three daily institutional net flows same sign"
        elif name.startswith("stock_return_"):
            n = name.removeprefix("stock_return_").removesuffix("d")
            formulas[name] = f"adjusted_close(T)/adjusted_close(T-{n})-1"
        elif name.startswith("market_return_"):
            n = name.removeprefix("market_return_").removesuffix("d")
            formulas[name] = f"market_close(T)/market_close(T-{n})-1"
        elif name.startswith("relative_to_market_"):
            n = name.removeprefix("relative_to_market_")
            formulas[name] = f"stock_return_{n}-market_return_{n}"
        if name in INDUSTRY_DISABLED:
            formulas[name] = "disabled until PIT historical industry exists"
        unit = ("twd" if name in ("amount", "adv20_twd") else
                "shares" if name in CHIP or name.endswith("balance") or name.startswith(("margin_change", "short_change")) else
                "ratio_or_native_indicator")
        result[name] = {
            "name": name, "group": group, "formula": formulas[name], "unit": unit,
            "source_dataset": dataset, "min_history": _min_history(name),
            "price_semantics": semantics,
            "training_requirement": {
                "capability_dataset": dataset,
                "resolution": "resolve_training_eligibility(feature_manifest, capabilities)",
            },
        }
    return {"schema_version": 1, "factors": result}


class FactorPanelStore:
    def __init__(self, root: Path | None = None) -> None:
        if root is None:
            from app.taiwan.data_root import taiwan_data_root

            root = taiwan_data_root() / "factors"
        self.root = Path(root)

    def _partition(self, panel: FactorPanel, day: object) -> Path:
        for part in (panel.factor_version, panel.policy_version, panel.universe_tier):
            if not part or part in (".", "..") or "/" in part or "\\" in part:
                raise ValueError("invalid factor storage identity")
        return (self.root / f"factor_version={panel.factor_version}"
                / f"policy_version={panel.policy_version}"
                / f"universe_tier={panel.universe_tier}" / f"date={day}")

    def save(self, panel: FactorPanel) -> list[Path]:
        if panel.universe_tier not in ("primary_verified", "secondary_observed"):
            raise ValueError("live factors belong in the immutable live snapshot")
        if panel.values.is_empty():
            return []
        if not set(VALUE_IDENTITY) <= set(panel.values.columns) or not set(FACTORS) <= set(panel.values.columns):
            raise ValueError("factor values schema is incomplete")
        if not panel.coverage.is_empty() and not set(COVERAGE_COLUMNS) <= set(panel.coverage.columns):
            raise ValueError("coverage schema is incomplete")
        if panel.values.select(pl.struct("symbol", "date").is_duplicated().any()).item():
            raise ValueError("duplicate factor identity")
        if panel.values.filter((pl.col("factor_version") != panel.factor_version) |
                               (pl.col("policy_version") != panel.policy_version) |
                               (pl.col("universe_tier") != panel.universe_tier)).height:
            raise ValueError("mixed factor version, policy or tier")
        metadata = json.dumps(factor_meta(), sort_keys=True, ensure_ascii=False, indent=2).encode("utf-8")
        version_root = self._partition(panel, panel.values["date"][0]).parents[2]
        meta_path = version_root / "_factor_meta.json"
        if meta_path.exists():
            if meta_path.read_bytes() != metadata:
                raise ValueError("factor metadata contract changed; bump version before publishing")
        else:
            # Existing legacy partitions share the old root manifest. Preserve
            # it and require its contract before attaching a version manifest.
            if next(version_root.glob("policy_version=*/universe_tier=*/date=*"), None):
                legacy = self.root / "_factor_meta.json"
                if not legacy.exists() or legacy.read_bytes() != metadata:
                    raise ValueError("legacy factor metadata differs or is missing; bump version")
            version_root.mkdir(parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(dir=version_root, prefix=".factor_meta_")
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(metadata)
                try:
                    os.link(temp, meta_path)  # Atomic publish without replacing another writer.
                except FileExistsError:
                    if meta_path.read_bytes() != metadata:
                        raise ValueError("factor metadata contract changed; bump version") from None
            finally:
                Path(temp).unlink(missing_ok=True)
        saved = []
        for day in panel.values["date"].unique().sort().to_list():
            target = self._partition(panel, day)
            values = panel.values.filter(pl.col("date") == day).sort("symbol")
            coverage = (panel.coverage.filter(pl.col("date") == day).sort(["symbol", "factor"])
                        if not panel.coverage.is_empty() else pl.DataFrame(schema={
                            "symbol": pl.String, "date": pl.Date, "factor": pl.String,
                            "status": pl.String, "as_of": pl.String, "available_at": pl.String,
                            "reason": pl.String, "source": pl.String,
                        }))
            if target.exists():
                if not (target / "values.parquet").exists() or not (target / "coverage.parquet").exists():
                    raise ValueError("incomplete existing factor partition")
                if not pl.read_parquet(target / "values.parquet").equals(values, null_equal=True) or not pl.read_parquet(target / "coverage.parquet").equals(coverage, null_equal=True):
                    raise ValueError("immutable factor partition differs; use a new version")
                saved.append(target)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            temp_dir = Path(tempfile.mkdtemp(dir=target.parent, prefix=".factor_"))
            try:
                values.write_parquet(temp_dir / "values.parquet")
                coverage.write_parquet(temp_dir / "coverage.parquet")
                os.rename(temp_dir, target)
            finally:
                if temp_dir.exists():
                    for child in temp_dir.iterdir():
                        child.unlink()
                    temp_dir.rmdir()
            saved.append(target)
        return saved

    def read(self, panel: FactorPanel, day: object) -> tuple[pl.DataFrame, pl.DataFrame]:
        part = self._partition(panel, day)
        return pl.read_parquet(part / "values.parquet"), pl.read_parquet(part / "coverage.parquet")

"""Taiwan Security Master Service & Universe Orchestrator.

Responsibilities:
  - Orchestrates TWSE & TPEx adapters to build unified securities master
  - Deduplication and conflict resolution (canonical symbol key)
  - Parquet persistence & local disk caching
  - Dynamic universe generation (TAIWAN_ALL, TWSE_ALL, TPEX_ALL, STOCKS, ETFS)
  - Multi-attribute instrument search (code, symbol, Traditional Chinese name)
  - Standardized Polars DataFrame export for MarketDataProvider contract
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import polars as pl

from app.taiwan.universe.adapters import TpexInstrumentAdapter, TwseInstrumentAdapter
from app.taiwan.universe.models import TaiwanInstrument, UniverseType

logger = logging.getLogger(__name__)

class TaiwanSecurityMaster:
    """Orchestrates Taiwan instruments master and universes."""

    def __init__(
        self,
        cache_path: Path | None = None,
        twse_adapter: TwseInstrumentAdapter | None = None,
        tpex_adapter: TpexInstrumentAdapter | None = None,
    ) -> None:
        # Phase 8B-5.0.6: 省略 cache_path 时锚定到 settings.data_dir, 不再是
        # cwd-dependent 的裸相对路径(见 app/taiwan/data_root.py)。显式传入
        # cache_path(既有 deterministic test 都这样做)时行为不变。
        if cache_path is None:
            from app.taiwan.data_root import taiwan_data_root

            cache_path = taiwan_data_root() / "security_master.parquet"
        self.cache_path = Path(cache_path)
        self.twse_adapter = twse_adapter or TwseInstrumentAdapter()
        self.tpex_adapter = tpex_adapter or TpexInstrumentAdapter()
        self._instruments: dict[str, TaiwanInstrument] = {}
        self._loaded = False

    def load_from_adapters(
        self,
        twse_html: str | None = None,
        tpex_html: str | None = None,
    ) -> int:
        """Fetch and merge instruments directly from TWSE and TPEx adapters."""
        twse_items = self.twse_adapter.get_instruments(twse_html)
        tpex_items = self.tpex_adapter.get_instruments(tpex_html)

        merged: dict[str, TaiwanInstrument] = {}

        # 1. Add TWSE items
        for item in twse_items:
            merged[item.symbol] = item

        # 2. Add TPEx items (detect conflicts)
        for item in tpex_items:
            if item.symbol in merged:
                logger.warning("Duplicate canonical symbol conflict: %s. Keeping TWSE entry.", item.symbol)
                continue
            merged[item.symbol] = item

        self._instruments = merged
        self._loaded = True
        logger.info("Loaded %d Taiwan instruments from official adapters.", len(merged))
        return len(merged)

    def save_cache(self, path: Path | None = None) -> Path:
        """Persist current instruments to Parquet."""
        target = Path(path) if path else self.cache_path
        target.parent.mkdir(parents=True, exist_ok=True)
        df = self.to_dataframe()
        df.write_parquet(target)
        logger.info("Persisted %d Taiwan instruments to %s", len(df), target)
        return target

    def load_cache(self, path: Path | None = None) -> bool:
        """Load instruments from Parquet disk cache if present."""
        target = Path(path) if path else self.cache_path
        if not target.exists():
            return False
        try:
            df = pl.read_parquet(target)
            rows = df.to_dicts()
            loaded: dict[str, TaiwanInstrument] = {}
            for r in rows:
                inst = TaiwanInstrument(
                    symbol=r["symbol"],
                    code=r["code"],
                    exchange=r["exchange"],
                    name=r["name"],
                    instrument_type=r["instrument_type"],
                    listing_status=r["listing_status"],
                    listing_date=r.get("listing_date"),
                    isin=r.get("isin"),
                    industry=r.get("industry"),
                    cfi_code=r.get("cfi_code"),
                    raw_category=r.get("raw_category", ""),
                    is_supported=bool(r.get("is_supported", False)),
                    source=r.get("source", "cache"),
                    updated_at=r.get("updated_at", ""),
                    etf_category=r.get("etf_category"),
                    classification_source=r.get("classification_source"),
                    underlying_scope=r.get("underlying_scope"),
                    leverage_multiplier=float(r.get("leverage_multiplier") or 1.0),
                    currency=r.get("currency") or "TWD",
                    lot_size=int(r.get("lot_size") or 1000),
                )
                loaded[inst.symbol] = inst


            self._instruments = loaded
            self._loaded = True
            logger.info("Loaded %d instruments from cache %s", len(loaded), target)
            return True
        except Exception as e:
            logger.warning("Failed to load instruments cache %s: %s", target, e)
            return False

    def ensure_loaded(self) -> None:
        """Ensure security master is populated (cache -> live)."""
        if self._loaded and self._instruments:
            return
        if self.load_cache():
            return
        self.load_from_adapters()
        try:
            self.save_cache()
        except Exception as e:
            logger.warning("Could not auto-save security master cache: %s", e)

    def get_instrument(self, symbol: str) -> TaiwanInstrument | None:
        """Get canonical instrument metadata by symbol (e.g. '2330.TWSE')."""
        self.ensure_loaded()
        return self._instruments.get(symbol)

    def get_universe(self, universe_type: UniverseType) -> list[str]:
        """Generate list of canonical symbols for a specified universe."""
        self.ensure_loaded()
        symbols: list[str] = []
        for inst in self._instruments.values():
            if not inst.is_supported or inst.listing_status != "active":
                continue
            if universe_type == UniverseType.TAIWAN_ALL:
                symbols.append(inst.symbol)
            elif universe_type == UniverseType.TWSE_ALL and inst.exchange == "TWSE":
                symbols.append(inst.symbol)
            elif universe_type == UniverseType.TPEX_ALL and inst.exchange == "TPEX":
                symbols.append(inst.symbol)
            elif universe_type == UniverseType.TAIWAN_STOCKS and inst.instrument_type == "stock":
                symbols.append(inst.symbol)
            elif universe_type == UniverseType.TAIWAN_ETFS and inst.instrument_type == "etf":
                symbols.append(inst.symbol)
        return sorted(symbols)

    def search(self, q: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search instruments by code, canonical symbol, or Traditional Chinese name.

        Prioritizes supported stocks/ETFs and exact/prefix matches over warrants and partial matches.
        """
        self.ensure_loaded()
        query = (q or "").strip().upper()
        if not query:
            return []

        scored_candidates: list[tuple[int, TaiwanInstrument]] = []
        for inst in self._instruments.values():
            code_upper = inst.code.upper()
            name_upper = inst.name.upper()
            sym_upper = inst.symbol.upper()

            score = 0
            if sym_upper == query:
                score = 100
            elif code_upper == query:
                score = 90
            elif name_upper == query:
                score = 85
            elif code_upper.startswith(query):
                score = 70
            elif name_upper.startswith(query):
                score = 60
            elif query in sym_upper:
                score = 50
            elif query in name_upper:
                score = 40
            else:
                continue

            # Prioritize supported securities (stocks/ETFs) over warrants/unsupported
            if inst.is_supported:
                score += 200

            scored_candidates.append((score, inst))

        # Sort descending by score
        scored_candidates.sort(key=lambda x: x[0], reverse=True)

        results: list[dict[str, Any]] = []
        for _, inst in scored_candidates[:limit]:
            results.append({
                "symbol": inst.symbol,
                "code": inst.code,
                "name": inst.name,
                "exchange": inst.exchange,
                "instrument_type": inst.instrument_type,
                "is_supported": inst.is_supported,
            })
        return results


    def to_dataframe(self, supported_only: bool = False) -> pl.DataFrame:
        """Export instruments as a Polars DataFrame."""
        self.ensure_loaded()
        rows = [
            inst.to_dict()
            for inst in self._instruments.values()
            if not supported_only or inst.is_supported
        ]
        schema = {
            "symbol": pl.String,
            "code": pl.String,
            "exchange": pl.String,
            "name": pl.String,
            "instrument_type": pl.String,
            "listing_status": pl.String,
            "listing_date": pl.String,
            "isin": pl.String,
            "industry": pl.String,
            "cfi_code": pl.String,
            "raw_category": pl.String,
            "is_supported": pl.Boolean,
            "source": pl.String,
            "updated_at": pl.String,
            "etf_category": pl.String,
            "classification_source": pl.String,
            "underlying_scope": pl.String,
            "leverage_multiplier": pl.Float64,
            "currency": pl.String,
            "lot_size": pl.Int64,
        }
        if not rows:


            return pl.DataFrame(schema=schema)
        return pl.DataFrame(rows, schema=schema)


    def to_provider_dataframe(self, asset_type: str = "stock") -> pl.DataFrame:
        """Export standardized DataFrame adhering to MarketDataProvider contract.

        Columns: ['symbol', 'name', 'exchange', 'asset_type', 'source', 'list_date', 'status']
        """
        self.ensure_loaded()
        target_type = asset_type.lower()
        rows = []
        for inst in self._instruments.values():
            if not inst.is_supported:
                continue
            if target_type in ("stock", "etf") and inst.instrument_type != target_type:
                continue
            rows.append({
                "symbol": inst.symbol,
                "name": inst.name,
                "code": inst.code,
                "exchange": inst.exchange,
                "asset_type": inst.instrument_type,
                "source": inst.source,
                "list_date": inst.listing_date,
                "status": inst.listing_status,
                "currency": inst.currency,
                "lot_size": inst.lot_size,
            })
        return pl.DataFrame(rows)

"""標的池 (Universe) 定義。支援台股官方宇宙與自選池。"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Literal

import polars as pl
from app.config import settings

logger = logging.getLogger(__name__)

PoolId = Literal["TAIWAN_ALL", "TWSE_ALL", "TPEX_ALL", "STOCKS", "ETFS", "watchlist", "CSI300", "CSI500", "SSE50", "CN_Equity_A", "CN_Index"]

DEMO_SYMBOLS = ["2330.TWSE", "2317.TWSE", "2454.TWSE", "2308.TWSE", "2382.TWSE"]


def _load_watchlist() -> list[str]:
    try:
        from app.services import watchlist
        symbols = watchlist.list_symbols()
        return [str((s or {}).get("symbol") or "") for s in symbols if (s or {}).get("symbol")]
    except Exception as e:
        logger.warning("load watchlist failed: %s", e)
        return []


def _pool_cache_path(pool_id: str) -> Path:
    return settings.data_dir / "pools" / f"{pool_id}.parquet"


def get_pool(pool_id: PoolId, refresh: bool = False) -> list[str]:
    """返回標的池中的 symbol 列表。"""
    if pool_id == "watchlist":
        return _load_watchlist()

    # 台股宇宙優先使用 TaiwanSecurityMaster
    taiwan_pools = {"TAIWAN_ALL", "TWSE_ALL", "TPEX_ALL", "STOCKS", "ETFS"}
    if pool_id in taiwan_pools:
        try:
            from app.taiwan.universe import get_security_master
            master = get_security_master()
            if pool_id == "STOCKS":
                df = master.to_provider_dataframe(asset_type="stock")
            elif pool_id == "ETFS":
                df = master.to_provider_dataframe(asset_type="etf")
            else:
                stocks = master.to_provider_dataframe(asset_type="stock")
                etfs = master.to_provider_dataframe(asset_type="etf")
                df = pl.concat([stocks, etfs], how="diagonal_relaxed")
                if pool_id == "TWSE_ALL":
                    df = df.filter(pl.col("exchange") == "TWSE")
                elif pool_id == "TPEX_ALL":
                    df = df.filter(pl.col("exchange") == "TPEX")
            if not df.is_empty():
                return sorted(df["symbol"].unique().to_list())
        except Exception as e:
            logger.warning("get_pool taiwan failed for %s: %s", pool_id, e)

    cache = _pool_cache_path(pool_id)
    if cache.exists() and not refresh:
        try:
            df = pl.read_parquet(cache)
            return df["symbol"].to_list()
        except Exception:
            pass

    return DEMO_SYMBOLS

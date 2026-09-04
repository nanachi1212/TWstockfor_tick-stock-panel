"""财务数据本地读取 — 独立于 K-line 管道, 自有存储。

Phase 8B-5.3: A 股财务数据同步产品(FinancialScheduler / sync_* / 手动同步 API)
已整体下线, 因为它只服务已删除的 A 股「财务分析」页面。本模块仅保留只读的
get_financial_df() —— 它被 StockPanel/StockInfoBar 的信息条「财务」字段组
(EPS/BPS/ROE/PE/PB 等, 经 /api/financials/metrics) 与 backend/app/backtest/
fundamentals.py(经 data/financials/metrics parquet 直读)复用, 与已下线的
财务分析产品无关, 不能一并删除。data/financials/*.parquet 本身不受影响
(既有文件不会被同步产品下线而清除, 只是不再有代码写入新数据)。

能力门控: Cap.FINANCIAL (Expert 套餐)
"""
from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)


def _financial_is_custom() -> bool:
    """当前财务数据源是否走 custom (用于绕过 TickFlow Expert 套餐门槛)。"""
    from app.services import preferences
    provider = preferences.get_financial_provider()
    if provider == "tickflow":
        return False
    from app.data_providers import custom as custom_sources
    return custom_sources.provider_has_dataset(provider, "financial")


def get_financial_df(data_dir: Path, table: str) -> pl.DataFrame:
    """读取本地财务 Parquet。"""
    path = data_dir / "financials" / table / "part.parquet"
    if not path.exists():
        return pl.DataFrame()
    try:
        return pl.read_parquet(path)
    except Exception as e:
        logger.warning("读取 financials/%s 失败: %s", table, e)
        return pl.DataFrame()

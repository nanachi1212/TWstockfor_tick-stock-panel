"""维度成分股映射 — 概念/行业 symbol → 成员名 展开表。

Phase 8B-5.4: 原「概念/行业涨幅轮动矩阵」产品(build_rps_rotation 及其
API/AI 分析对话框)已整体下线, 因为它只服务已删除的 A 股「概念分析」/
「行业分析」页面。本模块仅保留 _load_concept_map_df() —— 它被
services/market_mainline.py(供市场环境页「概念/行业主线排名」使用)复用,
与已下线的轮动矩阵产品无关, 不能一并删除。

数据来源全部复用现有资产, 不引入新数据源:
  - 概念/行业成分股映射: 复用 market_overview_builder 的 _dimension_field /
    _read_ext_rows / _symbol_keys / _dimension_values, 与看板/复盘的
    概念聚合口径完全一致。
"""
from __future__ import annotations

import logging
import time

import polars as pl

from app.services.market_overview_builder import (
    _dimension_field,
    _dimension_values,
    _read_ext_rows,
    _symbol_keys,
)
from app.services.ext_data import ExtConfigStore

logger = logging.getLogger(__name__)

# 维度映射缓存: {kind: (map_df, count)}。按 kind 隔离(概念/行业分别缓存)。
_map_cache: dict[str, pl.DataFrame] = {}
_map_ts: dict[str, float] = {}


def _load_concept_map_df(repo, kind: str = "concept") -> tuple[pl.DataFrame, int]:
    """构建并缓存 {symbol_upper → 维度成员} 的已展开 polars 映射表。

    kind: "concept"(概念) 或 "industry"(行业)。复用 market_overview_builder 的
    _dimension_field(config, kind) 识别维度 —— 该函数两种维度都支持。

    返回 (map_df, member_count):
      - map_df: 两列 (_sym_up: 大写 symbol, <kind>: 维度成员名), 已 explode。
        无数据时返回空 DataFrame。
      - member_count: 去重维度成员总数。

    缓存: 维度成分股是 snapshot, 进程内不变, 缓存 600s。按 kind 分别缓存。
    """
    now = time.time()
    cached = _map_cache.get(kind)
    if cached is not None and (now - _map_ts.get(kind, 0)) < 600:
        return cached

    data_dir = repo.store.data_dir
    store = ExtConfigStore(data_dir)
    pairs: list[tuple[str, str]] = []
    members_seen: set[str] = set()

    for config in store.load_all():
        field = _dimension_field(config, kind)
        if not field:
            continue
        for ext_row in _read_ext_rows(data_dir, config, field):
            members = _dimension_values(ext_row.get(field))
            if not members:
                continue
            keys = _symbol_keys(ext_row, config)
            for key in keys:
                for m in members:
                    pairs.append((key, m))
                    members_seen.add(m)

    if pairs:
        map_df = pl.DataFrame(
            {"_sym_up": [p[0] for p in pairs], kind: [p[1] for p in pairs]},
            schema={"_sym_up": pl.Utf8, kind: pl.Utf8},
        ).unique()
    else:
        map_df = pl.DataFrame(schema={"_sym_up": pl.Utf8, kind: pl.Utf8})
    _map_cache[kind] = map_df
    _map_ts[kind] = now
    return map_df, len(members_seen)

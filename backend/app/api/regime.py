"""市场环境(regime) API — 轻量查询。

装配逻辑在 app.services.regime_builder(纯函数), API 层薄壳。

Phase 8B-5.7: 移除仅供已刪除的 Regime.tsx 研究頁使用的 /history、/states、
/recompute、/phases、/mainline、/mainline/recompute 六個端點(手動重算按鈕與
歷史時序/主線排行展示皆屬該頁專屬)。保留:
  - GET /latest    — 被 Mining 挖掘頁(MiningWorkbench.tsx)核驗市場環境是否
                      已計算使用。
  - GET /coverage  — 被 Data 資料管理頁的資料畫像卡片使用。
  - invalidate_regime_cache() — 被 app/jobs/daily_pipeline.py 在增量計算
                      regime 後直接呼叫(非經 HTTP), 用以清空查詢快取。
regime_builder 本身的批算/增量計算/主線計算能力對 daily_pipeline、mining、
backtest 仍是真實 consumer, 完全未變更。
"""
from __future__ import annotations

import threading
import time
from typing import Any

from fastapi import APIRouter, Request

from app.services import regime_builder

router = APIRouter(prefix="/api/regime", tags=["regime"])

_CACHE_TTL = 5.0
_cache: dict[str, Any] | None = None
_cache_ts: float = 0.0
_cache_lock = threading.Lock()


def invalidate_regime_cache() -> None:
    """清空 regime 查询缓存。批算/重算后调用。"""
    global _cache, _cache_ts
    with _cache_lock:
        _cache = None
        _cache_ts = 0.0


def _data_dir(request: Request) -> Any:
    return request.app.state.repo.store.data_dir


def _df_to_records(df) -> list[dict]:
    """polars DataFrame → JSON 安全的 list[dict](date 转 ISO 字符串)。"""
    if df is None or df.is_empty():
        return []
    records = []
    for r in df.to_dicts():
        if "date" in r and r["date"] is not None:
            r["date"] = str(r["date"])
        records.append(r)
    return records


@router.get("/latest")
def regime_latest(request: Request):
    """最新一日环境(轻量)。"""
    df = regime_builder.load_regime_history(_data_dir(request))
    if df.is_empty():
        return {"row": None}
    latest = df.sort("date", descending=True).head(1)
    rows = _df_to_records(latest)
    return {"row": rows[0] if rows else None}


@router.get("/coverage")
def regime_coverage(request: Request):
    """regime 数据覆盖元信息(供数据画像)。"""
    return regime_builder.get_regime_coverage(_data_dir(request))

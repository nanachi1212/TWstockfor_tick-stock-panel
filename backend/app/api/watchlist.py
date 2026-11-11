"""自選股 API。"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.services import watchlist

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])

class AddRequest(BaseModel):
    symbol: str
    note: str = ""
    group_id: str | None = None


class BatchAddRequest(BaseModel):
    symbols: list[str]
    note: str = ""
    group_id: str | None = None


class GroupNameRequest(BaseModel):
    name: str
    color: str | None = None


class GroupReorderRequest(BaseModel):
    ordered_ids: list[str]


class GroupAssignRequest(BaseModel):
    group_id: str | None = None


def _with_names(rows: list[dict], request: Request) -> list[dict]:
    from app.taiwan.symbol import is_taiwan_symbol
    from app.taiwan.universe import get_security_master

    result = []
    master = None
    for row in rows:
        symbol = row.get("symbol", "")
        copy = dict(row)
        if is_taiwan_symbol(symbol):
            try:
                master = master or get_security_master()
                inst = master.get_instrument(symbol)
                if inst is not None:
                    copy["name"] = inst.name
            except Exception as exc:
                logger.debug("attach watchlist name failed: %s", exc)
        result.append(copy)
    return result


def _require_taiwan_symbol(symbol: str) -> str:
    from app.taiwan.symbol import parse_symbol

    try:
        return parse_symbol(symbol).canonical
    except ValueError as exc:
        raise HTTPException(422, "僅支援台股代碼 CODE.TWSE / CODE.TPEX") from exc


def _resync_auto_watch() -> None:
    # Membership changed: refresh Auto Watch rules in the background (the request does not wait).
    from app.taiwan.auto_watch import request_sync

    request_sync()


@router.get("")
def list_all(request: Request):
    return {"symbols": _with_names(watchlist.list_symbols(), request)}


@router.post("")
def add_one(req: AddRequest, request: Request):
    symbol = _require_taiwan_symbol(req.symbol)
    try:
        rows = watchlist.add(symbol, req.note, req.group_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    _resync_auto_watch()
    return {"symbols": _with_names(rows, request)}


@router.post("/batch")
def add_batch(req: BatchAddRequest, request: Request):
    symbols = [_require_taiwan_symbol(symbol) for symbol in req.symbols]
    try:
        rows, added = watchlist.add_batch(symbols, req.note, req.group_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    _resync_auto_watch()
    return {"symbols": _with_names(rows, request), "added": added}


@router.get("/groups")
def list_groups():
    return {"groups": watchlist.list_groups()}


@router.post("/groups")
def create_group(req: GroupNameRequest):
    try:
        groups, group = watchlist.create_group(req.name, req.color)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"groups": groups, "group": group}


@router.put("/groups/reorder")
def reorder_groups(req: GroupReorderRequest):
    """重排分組前後順序 (json 陣列順序即定義順序, 側邊欄/標籤欄/分組視圖共用)。"""
    try:
        groups = watchlist.reorder_groups(req.ordered_ids)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"groups": groups}


@router.put("/groups/{group_id}")
def rename_group(group_id: str, req: GroupNameRequest):
    try:
        groups = watchlist.rename_group(group_id, req.name, req.color)
    except KeyError as e:
        raise HTTPException(404, "自選分組不存在") from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"groups": groups}


@router.delete("/groups/{group_id}")
def delete_group(group_id: str, request: Request):
    try:
        groups, rows = watchlist.delete_group(group_id)
    except KeyError as e:
        raise HTTPException(404, "自選分組不存在") from e
    return {"groups": groups, "symbols": _with_names(rows, request)}


@router.post("/groups/{group_id}/clear")
def clear_group(group_id: str, request: Request):
    """清空分組成員:把該分組內所有股票轉為未分組,保留分組定義。"""
    try:
        rows = watchlist.clear_group(group_id)
    except KeyError as e:
        raise HTTPException(404, "自選分組不存在") from e
    return {"symbols": _with_names(rows, request)}


@router.post("/{symbol}/top")
def move_one_to_top(symbol: str, request: Request):
    rows = watchlist.move_to_top(symbol)
    return {"symbols": _with_names(rows, request)}


@router.put("/{symbol}/group")
def assign_group(symbol: str, req: GroupAssignRequest, request: Request):
    """互斥設定分組(僅保留此組; None=移出全部分組)。多組操作用 members 端點。"""
    try:
        rows = watchlist.set_group(symbol, req.group_id)
    except KeyError as e:
        raise HTTPException(404, "自選標的不存在") from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"symbols": _with_names(rows, request)}


@router.post("/groups/{group_id}/members/{symbol}")
def add_member(group_id: str, symbol: str, request: Request):
    """把標的加入分組(多組成員關係: 不影響其他分組)。"""
    try:
        rows = watchlist.add_to_group(symbol, group_id)
    except KeyError as e:
        raise HTTPException(404, "自選標的不存在") from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"symbols": _with_names(rows, request)}


@router.delete("/groups/{group_id}/members/{symbol}")
def remove_member(group_id: str, symbol: str, request: Request):
    """把標的移出分組(僅摘本組標籤; 標的仍在自選, 可能落入未分組)。"""
    try:
        rows = watchlist.remove_from_group(symbol, group_id)
    except KeyError as e:
        raise HTTPException(404, "自選標的不存在") from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"symbols": _with_names(rows, request)}


@router.delete("/{symbol}")
def remove_one(symbol: str, request: Request):
    rows = watchlist.remove(symbol)
    _resync_auto_watch()
    return {"symbols": _with_names(rows, request)}


@router.delete("")
def clear_all():
    """清空自選列表。"""
    count = watchlist.clear()
    _resync_auto_watch()
    return {"removed": count}


# 自選頁需要的列
_WATCHLIST_COLS = [
    "symbol", "close", "open", "high", "low", "prev_close", "change_pct", "change_amount", "amount",
    "turnover_rate",
    "amplitude", "annual_vol_20d",
    "vol_ratio_5d",
    "ma5", "ma10", "ma20", "ma60",
    "vol_ma5", "vol_ma10",
    "high_60d", "low_60d",
    "rsi_6", "rsi_14", "rsi_24",
    "macd_dif", "macd_dea", "macd_hist",
    "kdj_k", "kdj_d", "kdj_j",
    "boll_upper", "boll_lower",
    "atr_14",
    "momentum_5d", "momentum_10d", "momentum_20d", "momentum_30d", "momentum_60d",
    "deviate_3d", "deviate_10d", "deviate_30d",
    "consecutive_limit_ups", "consecutive_limit_downs",
    "signal_limit_up", "signal_limit_down", "signal_volume_surge",
    "signal_ma_golden_5_20", "signal_macd_golden", "signal_n_day_high",
    "signal_boll_breakout_upper", "signal_ma20_breakout",
    "signal_ma_dead_5_20", "signal_macd_dead", "signal_n_day_low",
    "signal_boll_breakdown_lower", "signal_ma20_breakdown",
]


@router.get("/enriched")
def watchlist_enriched(
    request: Request,
    ext_columns: str | None = Query(None, description="相容參數; 舊市場擴充資料已停用"),
):
    """使用既有台股 enrichment; 舊市場自選只保留 metadata, 不再讀取行情。"""
    from app.taiwan.symbol import is_taiwan_symbol
    from app.taiwan.watchlist_enrichment import enrich_taiwan_watchlist_rows

    t0 = time.perf_counter()
    entries = watchlist.list_symbols()
    taiwan_symbols = [row["symbol"] for row in entries if is_taiwan_symbol(row["symbol"])]
    enriched = {}
    if taiwan_symbols:
        try:
            enriched = {row["symbol"]: row for row in enrich_taiwan_watchlist_rows(taiwan_symbols)}
        except Exception as exc:
            logger.warning("taiwan watchlist enrichment failed: %s", exc)

    rows = []
    for entry in entries:
        symbol = entry["symbol"]
        row = {**entry, **enriched.get(symbol, {})}
        if not is_taiwan_symbol(symbol):
            row.update({column: None for column in _WATCHLIST_COLS if column != "symbol"})
            row.update(market="unsupported", is_supported=False,
                       status="unavailable", unavailable_reason="unsupported_market",
                       source_meta=None, quote_time=None)
        elif symbol not in enriched:
            row.update(market="taiwan", status="unavailable",
                       unavailable_reason="taiwan_data_unavailable", source_meta=None, quote_time=None)
        for column in [*_WATCHLIST_COLS, "name", "float_shares", "asset_type"]:
            row.setdefault(column, None)
        rows.append(row)

    # Each row retains its real source date; no synthetic combined freshness date.
    return {"rows": rows, "as_of": None, "elapsed_ms": (time.perf_counter() - t0) * 1000}

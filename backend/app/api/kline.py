"""台股 K 線與自訂分鐘資料 API。"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, time, timedelta
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from app.indicators.pipeline import compute_enriched
from app.price_limits import is_risk_warning_name, price_limit_pct
from app.services import kline_sync
from app.taiwan.realtime.calendar import TAIPEI_TZ, taipei_now, taipei_today
from app.taiwan.symbol import parse_symbol

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/kline", tags=["kline"])


def _require_taiwan_symbol(symbol: str) -> str:
    try:
        if not isinstance(symbol, str):
            raise ValueError("symbol must be a string")
        return parse_symbol(symbol).canonical
    except ValueError as exc:
        raise HTTPException(422, "僅支援台股代碼 CODE.TWSE / CODE.TPEX") from exc


def _require_taiwan_symbols(symbols: list[str]) -> list[str]:
    if not isinstance(symbols, list):
        raise HTTPException(422, "symbols 必須是台股代碼陣列")
    return [_require_taiwan_symbol(symbol) for symbol in symbols]


def _minute_allowed(capset) -> bool:
    """Only a configured custom minute dataset can supply historical minute bars."""
    from app.services import preferences

    provider = preferences.get_minute_data_provider()
    _, unavailable, error = kline_sync._resolve_minute_provider(provider)
    if error is not None:
        logger.warning("minute provider resolution failed while checking access: %s", error)
    return not unavailable


def _expected_minutes(trade_date: date) -> int:
    now = taipei_now()
    if trade_date < now.date():
        return 270
    if trade_date > now.date():
        return 0
    return max(0, min(270, now.hour * 60 + now.minute - 9 * 60))


def _default_minute_date(repo, symbol: str | None = None, asset_type: str = "stock") -> date:
    from app.taiwan.realtime import get_realtime_service

    today = taipei_today()
    # Missing today's daily file is not proof of a holiday; unresolved weekdays stay today.
    if get_realtime_service().trading_calendar.is_trading_day(today) is not False:
        return today
    recent = (
        repo.latest_minute_date(symbol, asset_type=asset_type)
        if symbol is not None else repo.latest_minute_date_global()
    )
    return recent or repo.latest_daily_date() or today


def _search_taiwan_instruments(q: str, limit: int) -> list[dict]:
    """仅查 TaiwanSecurityMaster, 不触碰 repo.get_instruments*() 的 legacy A 股缓存。

    canonical symbol 形如 2330.TWSE / 6488.TPEX; 支持代码/canonical symbol/
    繁体中文名称查询, 排序/评分逻辑在 TaiwanSecurityMaster.search() 内完成。
    只保留 is_supported 的 stock/etf(权证等未支援标的不该出现在自选搜索里)。
    """
    from app.taiwan.universe import get_security_master

    hits = get_security_master().search(q.strip(), limit=limit)
    return [
        {
            "symbol": hit["symbol"],
            "name": hit["name"],
            "code": hit["code"],
            "asset_type": hit["instrument_type"],
            "market": "taiwan",
        }
        for hit in hits
        if hit.get("is_supported") and hit.get("instrument_type") in ("stock", "etf")
    ][:limit]


@router.get("/instruments/search")
def search_instruments(
    request: Request,
    q: str = Query("", min_length=0, max_length=50, description="搜索关键词"),
    limit: int = Query(20, ge=1, le=50),
    asset_types: str = Query("stock", description="逗号分隔的资产类型: stock,etf"),
    market: Annotated[
        Literal["taiwan", "all"] | None,
        Query(description="台股證券主檔; all 為相容別名, 同樣只搜尋台股"),
    ] = "taiwan",
):
    """依代碼或名稱搜尋台股, 不讀取已停用市場的快取。"""
    if market not in (None, "taiwan", "all"):
        raise HTTPException(422, "僅支援台股市場")
    if not q.strip():
        return {"results": []}
    return {"results": _search_taiwan_instruments(q, limit)}


@router.post("/instruments/names")
def instruments_names(request: Request, symbols: list[str]):
    """批量查标的名称 (股票 + ETF + 指数)。传入 symbol 列表, 返回 {symbol: name}。"""
    if not symbols:
        return {"names": {}}
    symbols = _require_taiwan_symbols(symbols)
    from app.taiwan.universe import get_security_master

    master = get_security_master()
    return {"names": {
        symbol: inst.name for symbol in symbols
        if (inst := master.get_instrument(symbol)) is not None
    }}


def _get_stock_info(repo, symbol: str) -> dict:
    """从 instruments 内存缓存查标的名称 + 股本。

    该接口在个股弹窗打开时每秒被调用 (SSE invalidate 触发重拉), 走
    repo.get_instruments() 的 Polars 内存缓存按 symbol 过滤, 不再每请求
    DuckDB 扫 instruments parquet。列缺失时返回空 dict, 与旧 SQL 报错路径一致。
    """
    import polars as pl
    try:
        df = repo.get_instruments()
        needed = ("symbol", "name", "total_shares", "float_shares")
        if df.is_empty() or not all(c in df.columns for c in needed):
            return {}
        hit = df.filter(pl.col("symbol") == symbol).head(1)
        if hit.is_empty():
            return {}
        return {
            "name": hit["name"][0],
            "total_shares": hit["total_shares"][0],
            "float_shares": hit["float_shares"][0],
        }
    except Exception:  # noqa: BLE001
        return {}


def _get_asset_info(repo, symbol: str, asset_type: str) -> dict:
    """非股票标的 (ETF / 指数) 的名称信息 — 从对应 instruments 缓存查, 无股本概念。"""
    import polars as pl
    try:
        df = repo.get_instruments_asset(asset_type)
        if df.is_empty() or "symbol" not in df.columns or "name" not in df.columns:
            return {}
        hit = df.filter(pl.col("symbol") == symbol).head(1)
        if hit.is_empty():
            return {}
        return {"name": hit["name"][0]}
    except Exception:
        return {}


def _get_price_limit_info(
    repo,
    symbol: str,
    trade_date: date,
    asset_type: str,
    instrument_name: str | None,
) -> dict | None:
    """Return the date-aware limit rule and today's authoritative prices."""
    if asset_type == "index":
        return None

    info = {
        "rate": price_limit_pct(
            symbol,
            trade_date,
            is_risk_warning=(
                asset_type == "stock" and is_risk_warning_name(instrument_name)
            ),
        ),
        "limit_up": None,
        "limit_down": None,
        "source": "rule",
    }
    if trade_date != taipei_today():
        return info

    try:
        import polars as pl

        instruments = repo.get_instruments_asset(asset_type)
        available = [
            column
            for column in ("symbol", "limit_up", "limit_down")
            if column in instruments.columns
        ]
        if "symbol" not in available or len(available) == 1:
            return info
        hit = instruments.filter(pl.col("symbol") == symbol).select(available).head(1)
        row = hit.to_dicts()[0] if not hit.is_empty() else None
    except Exception:
        return info
    if row is None:
        return info

    has_authoritative_price = False
    for field in ("limit_up", "limit_down"):
        value = row.get(field)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric) and 0 < numeric < 10_000:
            info[field] = numeric
            has_authoritative_price = True
    if has_authoritative_price:
        info["source"] = "instrument"
    return info


def _get_previous_closes(
    repo,
    symbol: str,
    trade_dates: list[date],
    asset_type: str,
) -> dict[date, float | None]:
    """Return the previous trading day's adjusted close for each session."""
    if not trade_dates:
        return {}
    start = min(trade_dates) - timedelta(days=45)
    end = max(trade_dates)
    try:
        daily = repo.get_daily_asset(
            asset_type,
            symbol,
            start,
            end,
            columns=["date", "close"],
        ).sort("date")
    except Exception:
        daily = None
    if daily is None or daily.is_empty():
        return {trade_date: None for trade_date in trade_dates}

    closes: list[tuple[date, float]] = []
    for daily_date, close in daily.select(["date", "close"]).iter_rows():
        if close is None:
            continue
        numeric = float(close)
        if math.isfinite(numeric) and numeric > 0:
            closes.append((daily_date, numeric))

    result: dict[date, float | None] = {}
    for trade_date in trade_dates:
        result[trade_date] = next(
            (close for daily_date, close in reversed(closes) if daily_date < trade_date),
            None,
        )
    return result


@router.get("/daily")
def get_daily(
    request: Request,
    symbol: str = Query(..., description="台股代碼, 如 2330.TWSE"),
    days: int = Query(120, ge=10, le=2000),
    start_date: Optional[str] = Query(None, description="起始日期 YYYY-MM-DD, 优先于 days"),
    end_date: Optional[str] = Query(None, description="截止日期 YYYY-MM-DD, 默认今天"),
    ext_columns: Optional[str] = Query(None, description="逗号分隔的 ext 列: config_id.field_name"),
):
    """Read canonical raw Taiwan history or the configured custom enriched history.

    The official path is local-only. Custom-provider fallback respects the date range.
    Existing stock_info and extension-hook response contracts are preserved.
    """
    import polars as pl

    symbol = _require_taiwan_symbol(symbol)
    from app.services import preferences

    try:
        end = date.fromisoformat(end_date) if end_date else taipei_today()
        requested_start = date.fromisoformat(start_date) if start_date else None
        if requested_start is not None and requested_start > end:
            raise ValueError("start_date must not be after end_date")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    repo = request.app.state.repo
    asset_type = repo.resolve_asset_type(symbol)
    stock_info = _get_stock_info(repo, symbol) if asset_type == "stock" else _get_asset_info(repo, symbol, asset_type)
    stock_name = stock_info.get("name")
    if preferences.get_daily_data_provider() == "taiwan":
        try:
            result = kline_sync.read_taiwan_daily_history(
                [symbol], days=days, end=end,
                start=requested_start,
                include_indicators=True,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        resp = {"symbol": symbol, "name": stock_name, "stock_info": stock_info,
                "rows": result["data"][symbol], **result["meta"][symbol]}
        return _attach_ext(resp, repo, symbol, ext_columns)

    start = requested_start or end - timedelta(days=days)

    # 从 enriched 表读取 (已含前复权 OHLCV + 技术指标 + 信号); ETF/指数走独立存储
    df = repo.get_daily_asset(asset_type, symbol, start, end)

    if df.is_empty():
        try:
            raw = kline_sync.sync_daily_batch(
                [symbol], count=days + 30,
                start_time=datetime.combine(start - timedelta(days=150), time.min, tzinfo=TAIPEI_TZ),
                end_time=datetime.combine(end, time.max, tzinfo=TAIPEI_TZ),
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"日線資料來源讀取失敗: {e}") from e
        if raw.is_empty():
            return {"symbol": symbol, "name": stock_name, "stock_info": stock_info, "rows": []}
        # 拉除权因子做前复权 (資料來源有支援時), 否则空 df → compute_enriched 退回未复权
        factors = pl.DataFrame()
        capset = getattr(request.app.state, "capabilities", None)
        try:
            from app.tickflow.capabilities import Cap
            if capset and capset.has(Cap.ADJ_FACTOR):
                factors = kline_sync.fetch_adj_factor_single(symbol)
        except Exception as e:  # noqa: BLE001
            logger.debug("单股除权因子拉取失败 %s: %s", symbol, e)
        enriched = compute_enriched(raw, factors=factors)
        enriched = enriched.filter((pl.col("date") >= start) & (pl.col("date") <= end))
        rows = enriched.to_dicts() if start_date else enriched.tail(days).to_dicts()
        if start <= taipei_today() <= end:
            rows = _maybe_inject_live_candle(request, symbol, rows, asset_type)
        resp = {"symbol": symbol, "name": stock_name, "stock_info": stock_info, "rows": rows, "source": "live"}
        return _attach_ext(resp, repo, symbol, ext_columns)

    rows = df.to_dicts()

    # Historical ranges must not acquire a current-session candle.
    if start <= taipei_today() <= end:
        rows = _maybe_inject_live_candle(request, symbol, rows, asset_type)

    resp = {"symbol": symbol, "name": stock_name, "stock_info": stock_info, "rows": rows, "source": "enriched"}
    return _attach_ext(resp, repo, symbol, ext_columns)


def _attach_ext(resp: dict, repo, symbol: str, ext_columns: Optional[str]) -> dict:  # noqa: ARG001
    """ext 擴充欄位為已移除的 A 股功能; 保留簽名, 直接回傳原回應。"""
    return resp


def _maybe_inject_live_candle(request: Request, symbol: str, rows: list[dict], asset_type: str = "stock") -> list[dict]:
    """如果有当日实时 enriched 数据, 用实时数据生成今日蜡烛并追加/覆盖。

    stock 走 QuoteService 的股票实时缓存; etf 走 ETF enriched 缓存 (开启实时 ETF
    拉取时为盘中数据, 否则为磁盘最新日, 由下方"非今日不注入"守卫自然跳过)。
    """
    if asset_type == "stock":
        qs = getattr(request.app.state, "quote_service", None)
        if not qs:
            return rows
        df_today, enriched_date = qs.get_enriched_today()
    elif asset_type == "etf":
        df_today, enriched_date = request.app.state.repo.get_enriched_latest_asset("etf")
    else:
        return rows
    if df_today.is_empty():
        return rows

    # 非交易日（周末/假日）缓存的行情日期 != 今天，跳过注入避免产生重复蜡烛
    if not enriched_date or enriched_date != taipei_today():
        return rows

    # 查找该 symbol 的实时 enriched 行
    import polars as pl
    try:
        q = df_today.filter(pl.col("symbol") == symbol).to_dicts()
        if not q:
            return rows
        q = q[0]
    except Exception:  # noqa: BLE001
        return rows

    close_price = q.get("close")
    if not close_price or close_price <= 0:
        return rows

    today_str = str(enriched_date)

    # enriched 行已包含 OHLCV + 全套指标, 直接用它
    # 修复: API 在非交易时段可能返回 open/high/low=0, 用 close 填充避免异常蜡烛
    raw_open = q.get("open")
    raw_high = q.get("high")
    raw_low = q.get("low")
    live_row: dict = {
        "date": today_str,
        "symbol": symbol,
        "open": raw_open if raw_open and raw_open > 0 else close_price,
        "high": raw_high if raw_high and raw_high > 0 else close_price,
        "low": raw_low if raw_low and raw_low > 0 else close_price,
        "close": close_price,
        "volume": q.get("volume"),
        "amount": q.get("amount"),
        "change_pct": q.get("change_pct"),
        "is_live": True,
    }
    # 补上 enriched 的技术指标字段
    for key in ("ma5", "ma10", "ma20", "ma30", "ma60",
                "macd_dif", "macd_dea", "macd_hist",
                "kdj_k", "kdj_d", "kdj_j",
                "boll_upper", "boll_lower",
                "rsi_6", "rsi_14", "rsi_24",
                "atr_14", "vol_ratio_5d"):
        if key in q and q[key] is not None:
            live_row[key] = q[key]

    # 如果已有今天的 enriched 行, 覆盖; 否则追加
    found = False
    for i, r in enumerate(rows):
        if str(r.get("date")) == today_str:
            r.update(live_row)
            found = True
            break

    if not found:
        rows.append(live_row)

    return rows


class DailyBatchRequest:
    """批量日K请求。"""
    symbols: list[str]
    days: int = 12


@router.post("/daily-batch")
def get_daily_batch(request: Request, body: dict):
    """批量获取多只股票最近 N 天日K (OHLCV)。

    用于自选列表迷你蜡烛图等场景，只返回基础列，不返回全部 enriched 指标。
    """
    symbols = body.get("symbols", [])
    days = body.get("days", 12)
    symbols = _require_taiwan_symbols(symbols)
    if not symbols:
        return {"data": {}}
    days = max(5, min(60, days))

    from app.services import preferences

    if preferences.get_daily_data_provider() == "taiwan":
        return kline_sync.read_taiwan_daily_history(symbols, days=days, end=taipei_today())

    repo = request.app.state.repo
    import polars as pl

    end = taipei_today()
    start = end - timedelta(days=days * 2)  # 多取一些确保交易日够

    cols = ["symbol", "date", "open", "high", "low", "close", "volume"]

    # 按资产类型分组: stock 走批量缓存; etf/index 逐只查独立存储 (数量少, 成本可忽略)
    stock_symbols: list[str] = []
    etf_symbols: list[str] = []
    index_symbols: list[str] = []
    for s in symbols:
        t = repo.resolve_asset_type(s)
        if t == "etf":
            etf_symbols.append(s)
        elif t == "index":
            index_symbols.append(s)
        else:
            stock_symbols.append(s)

    frames: list[pl.DataFrame] = []
    if stock_symbols:
        df_stock = repo.get_daily_batch(stock_symbols, start, end, columns=cols)
        if not df_stock.is_empty():
            frames.append(df_stock)
    for sym in etf_symbols:
        sub = repo.get_etf_daily(sym, start, end, columns=cols)
        if not sub.is_empty():
            frames.append(sub)
    for sym in index_symbols:
        sub = repo.get_index_daily(sym, start, end, columns=cols)
        if not sub.is_empty():
            frames.append(sub)

    if not frames:
        return {"data": {}}
    df = pl.concat(frames, how="diagonal_relaxed")

    # 按 symbol 分组, 每只取最近 N 条。
    # partition_by 一次切分, 避免 N 只自选时对同一批数据做 N 次全帧过滤。
    result: dict[str, list[dict]] = {}
    for part in df.partition_by("symbol", maintain_order=True):
        sub = part.sort("date").tail(days)
        if not sub.is_empty():
            result[sub["symbol"][0]] = sub.to_dicts()

    return {"data": result}


@router.post("/minute-batch")
def get_minute_batch(request: Request, body: dict):
    """批量获取多只股票某天的分钟K (分时图用)。

    - 本地优先: 先从 kline_minute parquet 读, 完整的直接用
    - 缺失补拉: 本地不完整的 symbol 用 sync_minute_batch 批量实时拉 (不落库)
    - 需要已配置的自訂分鐘資料來源
    """
    from datetime import datetime

    import polars as pl

    from app.tickflow.capabilities import Cap

    symbols: list[str] = body.get("symbols", [])
    trade_date_str: str | None = body.get("date")
    symbols = _require_taiwan_symbols(symbols)
    if not symbols:
        return {"data": {}}

    repo = request.app.state.repo
    capset = request.app.state.capabilities

    if not _minute_allowed(capset):
        raise HTTPException(403, "尚未配置可用的台股分鐘資料來源")

    try:
        trade_date = date.fromisoformat(trade_date_str) if trade_date_str else _default_minute_date(repo)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "date 必須為 YYYY-MM-DD") from exc

    # Step 1: 本地优先 — 一次 scan 读全部 symbol 当日分钟K (股票 / ETF 分钟数据分开存储)
    etf_set = repo.get_etf_symbol_set()
    stock_syms = [s for s in symbols if s not in etf_set]
    etf_syms = [s for s in symbols if s in etf_set]
    df_local = repo.get_minute_batch(stock_syms, trade_date)
    if etf_syms:
        df_etf = repo.get_minute_batch(etf_syms, trade_date, asset_type="etf")
        if df_local.is_empty():
            df_local = df_etf
        elif not df_etf.is_empty():
            df_local = pl.concat([df_local, df_etf], how="diagonal_relaxed")

    expected = _expected_minutes(trade_date)

    # 按 symbol 分组, 判定哪些不完整需要补拉 (partition_by 一次切分, 同 daily-batch)
    result: dict[str, list[dict]] = {}
    incomplete: list[str] = []
    local_parts: dict[str, pl.DataFrame] = {}
    if not df_local.is_empty():
        for part in df_local.partition_by("symbol", maintain_order=True):
            local_parts[part["symbol"][0]] = part.sort("datetime")
    for sym in symbols:
        sub = local_parts.get(sym, pl.DataFrame())
        if expected > 0 and (sub.is_empty() or len(sub) < expected * 0.9):
            incomplete.append(sym)
        elif not sub.is_empty():
            result[sym] = sub.to_dicts()

    # Step 2: 缺失的 symbol 批量实时拉取 (不落库)
    if incomplete:
        start_time = datetime(trade_date.year, trade_date.month, trade_date.day, 9, 0, tzinfo=TAIPEI_TZ)
        end_time = datetime(trade_date.year, trade_date.month, trade_date.day, 13, 30, tzinfo=TAIPEI_TZ)
        lim = capset.limits(Cap.KLINE_MINUTE_BATCH)
        # etf_set 已在上方获取, 直接复用 — 按 asset_type 拆分调用 sync_minute_batch
        # (自訂資料來源依賴 asset_type 正確傳遞)
        # 契约: 本端点只接受 stock/ETF (指数分钟K已随 Phase 8B-5.1 一并移除),
        # 故两分支已覆盖全部 incomplete。若未来重新支持指数, 需额外加 index 分支
        # 以避免被误路由为 stock。
        stock_incomplete = [s for s in incomplete if s not in etf_set]
        etf_incomplete = [s for s in incomplete if s in etf_set]
        live_parts: list[pl.DataFrame] = []
        if stock_incomplete:
            df_s = kline_sync.sync_minute_batch(
                stock_incomplete,
                start_time=start_time,
                end_time=end_time,
                batch_size=lim.batch if lim else None,
                rpm=lim.rpm if lim else None,
                asset_type="stock",
            )
            if not df_s.is_empty():
                live_parts.append(df_s)
        if etf_incomplete:
            df_e = kline_sync.sync_minute_batch(
                etf_incomplete,
                start_time=start_time,
                end_time=end_time,
                batch_size=lim.batch if lim else None,
                rpm=lim.rpm if lim else None,
                asset_type="etf",
            )
            if not df_e.is_empty():
                live_parts.append(df_e)
        if live_parts:
            live_df = pl.concat(live_parts, how="diagonal_relaxed")
            live_map: dict[str, pl.DataFrame] = {
                part["symbol"][0]: part.sort("datetime")
                for part in live_df.partition_by("symbol", maintain_order=True)
            }
            for sym in incomplete:
                sub = live_map.get(sym)
                if sub is not None and not sub.is_empty():
                    result[sym] = sub.to_dicts()

    return {"data": result}


@router.get("/minute-range")
def get_minute_range(
    request: Request,
    symbol: str = Query(..., description="标的代码"),
    days: int = Query(10, ge=1, le=20, description="最近交易日数量"),
):
    """读取单只标的最近 N 个已落库交易日的分钟 K。"""
    import polars as pl

    symbol = _require_taiwan_symbol(symbol)
    repo = request.app.state.repo
    asset_type = repo.resolve_asset_type(symbol)
    stock_info = (
        _get_stock_info(repo, symbol)
        if asset_type == "stock"
        else _get_asset_info(repo, symbol, asset_type)
    )
    base_response = {
        "symbol": symbol,
        "name": stock_info.get("name"),
        "asset_type": asset_type,
        "requested_days": days,
    }

    # 指数分钟 K 不落本地仓库 (Phase 8B-5.1: 只读实时端点已随 Indices.tsx 一并删除)。
    if asset_type == "index":
        return {**base_response, "sessions": [], "source": "none"}

    end = taipei_today()
    start = end - timedelta(days=days * 3 + 20)
    minute = repo.get_minute_range([symbol], start, end, asset_type=asset_type)
    if minute.is_empty() or "datetime" not in minute.columns:
        return {**base_response, "sessions": [], "source": "none"}

    minute = minute.with_columns(
        pl.col("datetime").dt.date().alias("_trade_date"),
    )
    trade_dates = sorted(minute["_trade_date"].unique().to_list())[-days:]
    previous_closes = _get_previous_closes(repo, symbol, trade_dates, asset_type)
    row_columns = [
        column
        for column in (
            "datetime", "open", "high", "low", "close", "volume", "amount"
        )
        if column in minute.columns
    ]
    sessions = []
    for trade_date in trade_dates:
        rows = (
            minute.filter(pl.col("_trade_date") == trade_date)
            .sort("datetime")
            .select(row_columns)
            .to_dicts()
        )
        if rows:
            sessions.append({
                "date": trade_date.isoformat(),
                "prev_close": previous_closes.get(trade_date),
                "rows": rows,
            })

    return {
        **base_response,
        "sessions": sessions,
        "source": "local" if sessions else "none",
    }


@router.get("/minute")
def get_minute(
    request: Request,
    symbol: str = Query(..., description="标的代码"),
    trade_date: date | None = Query(None, alias="date", description="交易日期, 默认最新"),
):
    """讀取台股分鐘 K; 本地完整資料優先, 缺漏交由配置的自訂來源。"""
    symbol = _require_taiwan_symbol(symbol)
    if not _minute_allowed(request.app.state.capabilities):
        raise HTTPException(403, "尚未配置可用的台股分鐘資料來源")
    repo = request.app.state.repo
    asset_type = repo.resolve_asset_type(symbol)
    stock_info = _get_stock_info(repo, symbol) if asset_type == "stock" else _get_asset_info(repo, symbol, asset_type)
    stock_name = stock_info.get("name")

    if trade_date is None:
        trade_date = _default_minute_date(repo, symbol, asset_type)

    prev_close = _get_previous_closes(
        repo, symbol, [trade_date], asset_type,
    ).get(trade_date)
    price_limit = _get_price_limit_info(
        repo, symbol, trade_date, asset_type, stock_name,
    )
    df = repo.get_minute(symbol, trade_date, asset_type=asset_type)

    expected = _expected_minutes(trade_date)

    is_complete = not df.is_empty() and len(df) >= expected * 0.9  # 允许 10% 容差

    if is_complete:
        return {
            "symbol": symbol, "name": stock_name, "stock_info": stock_info,
            "date": str(trade_date), "rows": df.to_dicts(), "source": "local",
            "asset_type": asset_type,
            "price_limit": price_limit,
            "prev_close": prev_close,
        }

    # 本地不完整或無資料, 使用配置的自訂分鐘來源
    live_df = kline_sync.fetch_minute_single(symbol, trade_date, asset_type=asset_type)
    return {
        "symbol": symbol, "name": stock_name, "stock_info": stock_info,
        "date": str(trade_date), "rows": live_df.to_dicts(),
        "source": "live" if not live_df.is_empty() else "none",
        "asset_type": asset_type,
        "price_limit": price_limit,
        "prev_close": prev_close,
    }


@router.post("/sync_minute_single")
async def sync_minute_single(request: Request, body: dict):
    """手动拉取单只股票的分钟K并落库 (前复权)。

    body: { "symbol": "2330.TWSE" }
    用于个股分时图"获取数据"按钮: 本地无数据时单独拉取并持久化。
    """
    import asyncio

    from app.services.preferences import get_minute_sync_days

    symbol = body.get("symbol", "").strip()
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol 不能为空")

    symbol = _require_taiwan_symbol(symbol)

    requested_days = body.get("days")
    if requested_days is not None:
        if isinstance(requested_days, bool) or not isinstance(requested_days, int):
            raise HTTPException(status_code=400, detail="days 必须是整数")
        if requested_days < 1 or requested_days > 30:
            raise HTTPException(status_code=400, detail="days 必须在 1 到 30 之间")

    repo = request.app.state.repo
    capset = request.app.state.capabilities

    # 指数分钟K无本地存储, 落库会污染股票分钟表 kline_minute, 此端点显式拒绝。
    # (Phase 8B-5.1: 指数分钟K只读端点 /api/index/minute 已随 Indices.tsx 一并删除,
    # 无实时读取替代路径。)
    if repo.resolve_asset_type(symbol) == "index":
        raise HTTPException(status_code=400, detail="指数分钟K不支持落库同步")

    if not _minute_allowed(capset):
        raise HTTPException(status_code=403, detail="尚未配置可用的台股分鐘資料來源")

    days = requested_days if requested_days is not None else get_minute_sync_days()
    loop = asyncio.get_event_loop()

    def _run():
        return kline_sync.sync_and_persist_minute([symbol], repo, capset, days=days, force_full_days=True)

    written = await loop.run_in_executor(_long_task_executor, _run)

    # 刷新视图
    from app.jobs.daily_pipeline import _refresh_single_view
    _refresh_single_view(repo, "kline_minute")

    return {"status": "ok", "symbol": symbol, "rows": written}


# 长时间任务专用线程池（隔离于 FastAPI 默认线程池，防止阻塞请求处理）
import concurrent.futures as _cf

_long_task_executor = _cf.ThreadPoolExecutor(max_workers=2, thread_name_prefix="long-task")

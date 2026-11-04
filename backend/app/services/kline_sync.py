"""日 K 同步服务(§7.7 Step 1)。

调度器在 capability 允许下,把符号集合的日 K 批量同步到本地 Parquet。
策略:
  - 日 K 仅使用 `kline.daily.batch`
  - 除权因子仅使用 `adj_factor`
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, time, timedelta

import polars as pl

from app.data_providers.base import AssetType
from app.indicators.pipeline import filter_halt_days
from app.services import preferences
from app.taiwan.realtime.calendar import TAIPEI_TZ, taipei_now
from app.tickflow.capabilities import Cap, CapabilitySet
from app.tickflow.rate_limits import resolve_limit
from app.tickflow.repository import KlineRepository

logger = logging.getLogger(__name__)


def _reject_ashare_symbols(symbols: list[str]) -> None:
    if any(str(symbol).upper().endswith((".SH", ".SZ", ".BJ")) for symbol in symbols):
        raise ValueError("A 股行情功能已移除")


def _atomic_write_parquet(df: pl.DataFrame, out) -> None:
    """先写临时文件再原子替换, 避免进程中断留下损坏的 parquet。

    与 repository._atomic_write_parquet 同语义。adj_factor 的 all.parquet 是全市场
    单文件、每次「读→concat→原地写」, 直接 write_parquet(out) 在进程被 kill
    (dev.sh 清端口用 kill -9)、reap 超时或断电时会留下半截文件, 之后复权视图
    scan_parquet 整条链路报错、enriched 全市场重算不出。临时文件后缀 .tmp 不匹配
    *.parquet glob, 不会被扫描误读。
    """
    tmp = out.with_name(out.name + ".tmp")
    df.write_parquet(tmp)
    tmp.replace(out)  # 同目录 rename, POSIX/NTFS 均为原子操作


# 标准列(无论 SDK 返回什么形状,我们把它规范成这套)
CANONICAL_DAILY_COLS = [
    "symbol", "date", "open", "high", "low", "close", "volume", "amount",
]


def _normalize_daily(df_in, default_symbol: str | None = None) -> pl.DataFrame:
    """把 SDK 返回的 pandas/任意 DataFrame 规范成 canonical 列。"""
    if df_in is None or len(df_in) == 0:
        return pl.DataFrame()

    if not isinstance(df_in, pl.DataFrame):
        df = pl.from_pandas(df_in.reset_index() if hasattr(df_in, "reset_index") else df_in)
    else:
        df = df_in

    # 兼容字段名差异
    rename_map = {
        "ts_code": "symbol",
        "trade_date": "date",
        "vol": "volume",
        "amt": "amount",
        "datetime": "date",
    }
    df = df.rename({k: v for k, v in rename_map.items() if k in df.columns})

    if "symbol" not in df.columns and default_symbol is not None:
        df = df.with_columns(pl.lit(default_symbol).alias("symbol"))

    # 类型规范
    if "date" in df.columns and df.schema["date"] != pl.Date:
        df = df.with_columns(pl.col("date").cast(pl.Date, strict=False))

    for col in ("open", "high", "low", "close"):
        if col in df.columns:
            df = df.with_columns(pl.col(col).cast(pl.Float64, strict=False))
    for col in ("volume", "amount"):
        if col in df.columns:
            df = df.with_columns(pl.col(col).cast(pl.Float64, strict=False))

    # 过滤停牌日 (open/high 为 0; close 可能被填充为前收盘价, 不能用全零判断)
    df = filter_halt_days(df)

    # 只保留 canonical 列
    keep = [c for c in CANONICAL_DAILY_COLS if c in df.columns]
    return df.select(keep)


def read_taiwan_daily_history(
    symbols: list[str],
    *,
    days: int,
    end: date,
    start: date | None = None,
    include_indicators: bool = False,
) -> dict:
    """Read one bounded batch from the authoritative raw store; never fetch or copy it.

    ``days`` counts stored trading sessions. Explicit date ranges are inclusive.
    Indicator warm-up uses preceding sessions and is removed before presentation.
    There is no extra cache, so completed daily-store writes are visible immediately.
    """
    from app.taiwan.daily_store import TaiwanDailyStore
    from app.taiwan.observed_universe import ObservedUniverseStore
    from app.taiwan.quant.live_contract import latest_completed_session
    from app.taiwan.realtime.calendar import TaiwanTradingCalendar
    from app.taiwan.technical_indicators import compute_taiwan_indicator_panel

    if start is not None and start > end:
        raise ValueError("start_date must not be after end_date")
    store = TaiwanDailyStore()
    sessions = sorted(d for d in store.available_dates() if d <= end)
    selected = [d for d in sessions if start is None or d >= start]
    if start is None:
        selected = selected[-days:]
    data: dict[str, list[dict]] = {symbol: [] for symbol in symbols}
    if selected and symbols:
        first = sessions.index(selected[0])
        read_start = sessions[max(0, first - 150)] if include_indicators else selected[0]
        history = store.read_range(symbols, read_start, end)
        if not history.is_empty():
            if include_indicators:
                panel = compute_taiwan_indicator_panel(history, price_semantics="raw")
                history = history.join(panel, on=["symbol", "date"], how="left")
            history = history.filter(pl.col("date") >= selected[0]).sort(["symbol", "date"])
            for group in history.partition_by("symbol", maintain_order=True):
                data[group["symbol"][0]] = group.to_dicts()

    # A stalled daily store cannot itself prove freshness. Reuse the verified-session
    # resolver and its 16:00 publication cutoff, bounded by a historical requested end.
    calendar = TaiwanTradingCalendar()
    evidence_store = ObservedUniverseStore()
    reference_now = min(taipei_now(), datetime.combine(end, time.max, tzinfo=TAIPEI_TZ))
    try:
        expected_session = latest_completed_session(
            reference_now,
            lambda day, exchange: evidence_store.day_evidence(exchange, day, calendar=calendar),
        )
    except (ValueError, OSError):
        expected_session = None

    meta = {}
    for symbol, rows in data.items():
        as_of = rows[-1]["date"] if rows else None
        reason = None
        status = "available"
        if not rows:
            status, reason = "unavailable", "no_persisted_daily_history"
        elif (selected and as_of < selected[-1]) or (expected_session and as_of < expected_session):
            status, reason = "stale", "latest_daily_bar_predates_confirmed_session"
        elif expected_session is None:
            status, reason = "partial", "session_freshness_unverified"
        elif len(rows) < (len(selected) if start is not None else days):
            status, reason = "partial", "insufficient_daily_history"
        meta[symbol] = {
            "source": "taiwan_daily_store", "status": status,
            "as_of": as_of, "trade_date": as_of, "reason": reason,
            "expected_session": expected_session,
            "price_semantics": "raw", "volume_unit": "shares", "amount_unit": "TWD",
        }
    return {"data": data, "meta": meta}


def sync_daily_batch(symbols: list[str],
                     count: int | None = None,
                     batch_size: int | None = None,
                     rpm: int | None = None,
                     start_time: datetime | None = None,
                     end_time: datetime | None = None,
                     on_chunk_done: Callable[[int, int], None] | None = None,
                     failed_out: list[str] | None = None) -> pl.DataFrame:
    """Read daily bars through the selected provider; missing capabilities never fall back."""
    from app.data_providers import custom as custom_sources
    from app.data_providers.registry import get_provider

    if not symbols:
        return pl.DataFrame()
    _reject_ashare_symbols(symbols)
    name = preferences.get_daily_data_provider()
    if name != "taiwan" and not custom_sources.provider_has_dataset(name, "daily"):
        if failed_out is not None:
            failed_out.extend(symbols)
        logger.warning("Daily provider %s does not provide daily data", name)
        return pl.DataFrame()
    return get_provider(name).get_daily(
        symbols, start_time=start_time, end_time=end_time, on_chunk_done=on_chunk_done,
    )


def sync_and_persist_daily_batch(
    symbols: list[str],
    repo: KlineRepository,
    capset: CapabilitySet,
    count: int | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    on_chunk_done: Callable[[int, int], None] | None = None,
) -> int:
    """Persist daily bars from the selected provider, preserving the repository boundary."""
    if not symbols:
        return 0
    end_time = end_date or datetime.now()
    start_time = start_date or (end_time - timedelta(days=count or 365))
    df = sync_daily_batch(
        symbols, count=count, start_time=start_time, end_time=end_time,
        on_chunk_done=on_chunk_done,
    )
    if df.is_empty():
        return 0
    repo.append_daily(df)
    try:
        d = repo.store.data_dir.as_posix()
        repo.db.execute(
            f"""CREATE OR REPLACE VIEW kline_daily AS
                SELECT * FROM read_parquet('{d}/kline_daily/**/*.parquet', union_by_name=true)"""
        )
    except Exception as exc:
        logger.warning("refresh view failed: %s", exc)
    return df.height




def _normalize_adj_factor(raw) -> pl.DataFrame:
    """Normalize SDK ex_factors response to symbol/trade_date/ex_factor."""
    if raw is None or len(raw) == 0:
        return pl.DataFrame()
    if isinstance(raw, dict):
        rows: list[dict] = []
        for sym, values in raw.items():
            for item in values or []:
                row = dict(item or {})
                row.setdefault("symbol", sym)
                rows.append(row)
        df = pl.DataFrame(rows) if rows else pl.DataFrame()
    elif isinstance(raw, pl.DataFrame):
        df = raw
    else:
        df = pl.from_pandas(raw.reset_index() if hasattr(raw, "reset_index") else raw)
    if df.is_empty():
        return df
    # rename: timestamp/date → trade_date, adj_factor → ex_factor
    # 注意: 新版 SDK 可能同时返回 timestamp 和 trade_date (或 adj_factor 和 ex_factor),
    # 直接 rename 会产生重复列报错。仅当目标列不存在时才 rename。
    rename_map: dict[str, str] = {}
    for src, dst in (("timestamp", "trade_date"), ("date", "trade_date"), ("adj_factor", "ex_factor")):
        if src in df.columns and dst not in df.columns:
            rename_map[src] = dst
    df = df.rename(rename_map)
    if "trade_date" in df.columns:
        if df.schema["trade_date"] in {pl.Int64, pl.Int32, pl.UInt64, pl.UInt32, pl.Float64, pl.Float32}:
            df = df.with_columns(
                pl.from_epoch(pl.col("trade_date").cast(pl.Int64), time_unit="ms").dt.date().alias("trade_date")
            )
        else:
            df = df.with_columns(pl.col("trade_date").cast(pl.Date, strict=False))
    if "ex_factor" in df.columns:
        df = df.with_columns(pl.col("ex_factor").cast(pl.Float64, strict=False))
    cols = [c for c in ["symbol", "trade_date", "ex_factor"] if c in df.columns]
    if len(cols) < 3:
        return pl.DataFrame()
    return df.select(cols).drop_nulls()


def sync_adj_factor(symbols: list[str], repo: KlineRepository,
                    capset: CapabilitySet,
                    start_time: datetime | None = None,
                    end_time: datetime | None = None,
                    on_chunk_done: Callable[[int, int], None] | None = None,
                    asset_type: str = "stock") -> tuple[int, list[str]]:
    """Persist adjustment factors only when the selected custom provider supports them."""
    from app.data_providers import custom as custom_sources

    if not symbols:
        return 0, []
    _reject_ashare_symbols(symbols)
    name = preferences.get_adj_factor_provider()
    if name == "same_as_daily":
        name = preferences.get_daily_data_provider()
    if name == "taiwan" or not custom_sources.provider_has_dataset(name, "adj_factor"):
        return 0, []
    new_data = custom_sources.get_provider(name).get_adj_factors(
        symbols, start_time=start_time, end_time=end_time,
        asset_type=asset_type, on_chunk_done=on_chunk_done,
    )
    if new_data.is_empty():
        return 0, []
    affected = new_data["symbol"].unique().to_list()
    factor_dir = "adj_factor_etf" if asset_type == "etf" else "adj_factor"
    out = repo.store.data_dir / factor_dir / "all.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        existing = pl.read_parquet(out)
        merged = pl.concat([existing, new_data]).unique(
            subset=["symbol", "trade_date"], keep="last",
        ).sort(["symbol", "trade_date"])
        _atomic_write_parquet(merged, out)
        return merged.height - existing.height, affected
    _atomic_write_parquet(new_data.sort(["symbol", "trade_date"]), out)
    return new_data.height, affected


# ===== 分钟 K 同步 =====

CANONICAL_MINUTE_COLS = [
    "symbol", "datetime", "open", "high", "low", "close", "volume", "amount",
]


def _normalize_minute(df_in, default_symbol: str | None = None) -> pl.DataFrame:
    """把 SDK 返回的分钟 K 数据规范成 canonical 列。"""
    if df_in is None or len(df_in) == 0:
        return pl.DataFrame()

    if not isinstance(df_in, pl.DataFrame):
        df = pl.from_pandas(df_in.reset_index() if hasattr(df_in, "reset_index") else df_in)
    else:
        df = df_in

    rename_map = {
        "ts_code": "symbol",
        "vol": "volume",
        "amt": "amount",
    }
    df = df.rename({k: v for k, v in rename_map.items() if k in df.columns})

    # datetime 列:优先用 timestamp(毫秒精度),其次 trade_time
    if "timestamp" in df.columns:
        df = df.with_columns(
            pl.from_epoch("timestamp", time_unit="ms").alias("datetime"),
        ).drop("timestamp")
        for drop_col in ("trade_time", "trade_date"):
            if drop_col in df.columns:
                df = df.drop(drop_col)
    elif "trade_time" in df.columns:
        df = df.rename({"trade_time": "datetime"})
        if "trade_date" in df.columns:
            df = df.drop("trade_date")
    elif "trade_date" in df.columns:
        df = df.rename({"trade_date": "datetime"})

    if "symbol" not in df.columns and default_symbol is not None:
        df = df.with_columns(pl.lit(default_symbol).alias("symbol"))

    # 类型规范:统一转 Datetime('us')
    if "datetime" in df.columns:
        dt_type = df.schema["datetime"]
        if not isinstance(dt_type, pl.Datetime) or dt_type.time_unit != "us":
            df = df.with_columns(pl.col("datetime").cast(pl.Datetime("us"), strict=False))

    for col in ("open", "high", "low", "close"):
        if col in df.columns:
            df = df.with_columns(pl.col(col).cast(pl.Float64, strict=False))
    for col in ("volume", "amount"):
        if col in df.columns:
            df = df.with_columns(pl.col(col).cast(pl.Float64, strict=False))

    keep = [c for c in CANONICAL_MINUTE_COLS if c in df.columns]
    return df.select(keep)


def _datetime_to_ms(dt: datetime) -> int:
    """datetime → 毫秒时间戳 (供 SDK start_time / end_time 使用)。"""
    return int(dt.timestamp() * 1000)


def _write_minute_partition(df: pl.DataFrame, minute_dir) -> int:
    """按 _trade_date 分区落盘分钟 K (读旧→concat→unique→原子写)。返回写入行数。

    抽自原 sync_and_persist_minute 末尾的循环, 供流式落盘 (每段一次) 与一次性迁移共用。
    """
    if df.is_empty():
        return 0
    df = df.with_columns(pl.col("datetime").dt.date().alias("_trade_date"))
    written = 0
    for day_df in df.partition_by("_trade_date"):
        trade_date = day_df["_trade_date"][0]
        out = minute_dir / f"date={trade_date}" / "part.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            existing = pl.read_parquet(out)
            if "datetime" in existing.columns:
                existing = existing.filter(pl.col("datetime").is_not_null())
            day_df = pl.concat([existing, day_df.drop("_trade_date")]).unique(
                subset=["symbol", "datetime"], keep="last",
            )
        else:
            day_df = day_df.drop("_trade_date")
        day_df = day_df.sort("symbol", "datetime")
        _atomic_write_parquet(day_df, out)
        written += day_df.height
    return written


def _resolve_minute_provider(
    provider_name: str,
) -> tuple[object | None, bool, str | None]:
    """Return (provider, unavailable, error) without an implicit source fallback."""
    from app.data_providers import custom as custom_sources

    if provider_name == "taiwan":
        return None, True, None
    try:
        if not custom_sources.provider_has_dataset(provider_name, "minute"):
            return None, True, None
        return custom_sources.get_provider(provider_name), False, None
    except Exception as exc:
        return None, True, str(exc)


def _try_custom_minute(
    symbols: list[str],
    start_time: datetime | None,
    end_time: datetime | None,
    asset_type: AssetType,
    freq: str = "1m",
    on_chunk_done: Callable[[int, int, str], None] | None = None,
) -> tuple[pl.DataFrame | None, bool]:
    """Read custom minute bars; failures remain unavailable instead of changing sources."""
    _reject_ashare_symbols(symbols)
    name = preferences.get_minute_data_provider()
    provider, unavailable, error = _resolve_minute_provider(name)
    if unavailable:
        if error is not None:
            logger.warning("minute provider %s resolution failed: %s", name, error)
        return None, True
    wrapped_cb = None
    if on_chunk_done is not None:
        def wrapped_cb(cur: int, total: int) -> None:
            on_chunk_done(cur, total, "custom")
    try:
        return provider.get_minute(
            symbols, start_time=start_time, end_time=end_time,
            asset_type=asset_type, freq=freq, on_chunk_done=wrapped_cb,
        ), False
    except Exception as exc:
        logger.warning("minute provider %s fetch failed: %s", name, exc)
        return None, True


def sync_minute_batch(
    symbols: list[str],
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    count: int | None = None,
    batch_size: int | None = None,
    rpm: int | None = None,
    on_chunk_done: Callable[[int, int, str], None] | None = None,
    segment_trading_days: int = 20,
    on_segment: Callable[[pl.DataFrame], None] | None = None,
    asset_type: AssetType = "stock",
) -> pl.DataFrame:
    """Read configured custom minute data and preserve the optional persistence callback."""
    if not symbols:
        return pl.DataFrame()
    df, unavailable = _try_custom_minute(
        symbols, start_time=start_time, end_time=end_time,
        asset_type=asset_type, freq="1m", on_chunk_done=on_chunk_done,
    )
    if unavailable or df is None:
        return pl.DataFrame()
    if on_segment and not df.is_empty():
        on_segment(df)
        return pl.DataFrame()
    return df


def intraday_monitor_support(capset: CapabilitySet | None) -> dict[str, object]:
    """Only a configured minute provider can supply generic intraday signal bars."""
    _, unavailable, error = _resolve_minute_provider(preferences.get_minute_data_provider())
    if error is not None:
        logger.warning("minute provider resolution failed: %s", error)
    return {
        "available": not unavailable,
        "source": None if unavailable else "custom_minute",
        "max_symbols": 0 if unavailable else 100,
        "reason": "尚未配置分鐘資料來源" if unavailable else "使用已配置的分鐘資料來源",
    }


def _normalize_intraday_raw(raw, default_symbol: str | None = None) -> list[pl.DataFrame]:
    frames: list[pl.DataFrame] = []
    if isinstance(raw, dict):
        for symbol, sub in raw.items():
            if sub is not None and len(sub) > 0:
                frames.append(_normalize_minute(sub, default_symbol=str(symbol)))
    elif raw is not None and len(raw) > 0:
        frames.append(_normalize_minute(raw, default_symbol=default_symbol))
    return [frame for frame in frames if not frame.is_empty()]


def fetch_intraday_monitor_batch(
    symbols: list[str], capset: CapabilitySet | None, *, now: datetime | None = None,
) -> pl.DataFrame:
    """Read the Taiwan session through the configured minute provider without persisting."""
    support = intraday_monitor_support(capset)
    if not symbols or not support["available"] or len(symbols) > int(support["max_symbols"]):
        return pl.DataFrame()
    now = now or taipei_now()
    return sync_minute_batch(
        symbols, start_time=now.replace(hour=9, minute=0, second=0, microsecond=0), end_time=now,
    )


def fetch_minute_single(
    symbol: str,
    trade_date: date,
    asset_type: AssetType = "stock",
) -> pl.DataFrame:
    """Read one Taiwan trading session only from the configured minute provider."""
    start_time = datetime.combine(trade_date, datetime.min.time(), tzinfo=TAIPEI_TZ).replace(hour=9)
    end_time = start_time.replace(hour=13, minute=30)
    df, unavailable = _try_custom_minute(
        [symbol], start_time=start_time, end_time=end_time, asset_type=asset_type,
    )
    return df if not unavailable and df is not None else pl.DataFrame()


def fetch_adj_factor_single(symbol: str) -> pl.DataFrame:
    """Read factors only from an explicitly configured provider with that dataset."""
    from app.data_providers import custom as custom_sources

    _reject_ashare_symbols([symbol])
    name = preferences.get_adj_factor_provider()
    if name == "same_as_daily":
        name = preferences.get_daily_data_provider()
    if name == "taiwan" or not custom_sources.provider_has_dataset(name, "adj_factor"):
        return pl.DataFrame()
    try:
        return custom_sources.get_provider(name).get_adj_factors([symbol], start_time=None, end_time=None)
    except Exception as exc:
        logger.warning("adjustment factor fetch failed for %s: %s", symbol, exc)
        return pl.DataFrame()


def _latest_minute_datetime(repo: KlineRepository) -> datetime | None:
    """本地分钟 K 数据的最新时间。"""
    try:
        res = repo.execute_one("SELECT max(datetime) FROM kline_minute")
        if res and res[0]:
            d = res[0]
            if isinstance(d, datetime):
                return d
            return datetime.fromisoformat(str(d))
    except Exception:  # noqa: BLE001
        pass
    return None


def _earliest_minute_datetime(repo: KlineRepository) -> datetime | None:
    """本地分钟 K 数据的最早时间 (用于向前扩展的起点)。"""
    try:
        res = repo.execute_one("SELECT min(datetime) FROM kline_minute")
        if res and res[0]:
            d = res[0]
            if isinstance(d, datetime):
                return d
            return datetime.fromisoformat(str(d))
    except Exception:  # noqa: BLE001
        pass
    return None


def _cleanup_null_datetime_minute(repo: KlineRepository) -> None:
    """检测并清除 datetime 全为 null 的旧版分钟 K 数据(迁移用)。"""
    minute_dir = repo.store.data_dir / "kline_minute"
    if not minute_dir.exists():
        return
    try:
        row = repo.execute_one(
            "SELECT count(*) AS total, count(datetime) AS non_null FROM kline_minute"
        )
        if row and row[0] > 0 and (row[1] is None or row[1] == 0):
            # 全部 datetime 为 null — 清除所有分钟 K parquet
            n = 0
            for f in minute_dir.rglob("*.parquet"):
                f.unlink()
                n += 1
            logger.info("cleaned %d corrupted minute-K parquet files (null datetime)", n)
    except Exception as e:  # noqa: BLE001
        logger.debug("minute cleanup check failed: %s", e)


def _migrate_symbol_to_date_partition(repo: KlineRepository) -> None:
    """将旧版 symbol= 分区迁移为 date= 分区。迁移完成后删除旧目录。"""
    minute_dir = repo.store.data_dir / "kline_minute"
    if not minute_dir.exists():
        return

    old_dirs = [d for d in minute_dir.iterdir() if d.is_dir() and d.name.startswith("symbol=")]
    if not old_dirs:
        return

    logger.info("migrating %d symbol-partitioned minute-K dirs to date partition…", len(old_dirs))

    all_frames: list[pl.DataFrame] = []
    for sym_dir in old_dirs:
        for pq in sym_dir.glob("*.parquet"):
            try:
                df = pl.read_parquet(pq)
                if "datetime" in df.columns:
                    df = df.filter(pl.col("datetime").is_not_null())
                if not df.is_empty():
                    all_frames.append(df)
            except Exception:  # noqa: BLE001
                pass

    if not all_frames:
        # 数据全部不可用，直接删旧目录
        for d in old_dirs:
            d.mkdir(parents=True, exist_ok=True)
            for f in d.rglob("*"):
                if f.is_file():
                    f.unlink()
            d.rmdir()
        return

    combined = pl.concat(all_frames, how="diagonal_relaxed")
    combined = combined.unique(subset=["symbol", "datetime"], keep="last")

    # 按日期写新分区
    combined = combined.with_columns(pl.col("datetime").dt.date().alias("_trade_date"))
    for day_df in combined.partition_by("_trade_date"):
        trade_date = day_df["_trade_date"][0]
        out = minute_dir / f"date={trade_date}" / "part.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        day_df = day_df.drop("_trade_date").sort("symbol", "datetime")
        _atomic_write_parquet(day_df, out)

    # 删旧目录
    for d in old_dirs:
        for f in d.rglob("*"):
            if f.is_file():
                f.unlink()
        # 移除空目录
        try:
            d.rmdir()
        except OSError:
            pass

    logger.info("minute-K migration done: %d rows migrated", combined.height)


def sync_and_persist_minute(
    symbols: list[str],
    repo: KlineRepository,
    capset: CapabilitySet,
    days: int = 5,
    on_chunk_done: Callable[[int, int, str], None] | None = None,
    extend_backward: bool = False,
    force_full_days: bool = False,
) -> int:
    """同步分钟 K 并存到 Parquet(前复权价格, SDK 端 adjust=qfq)。返回写入行数。

    使用 start_time / end_time 区间拉取, 确保所有标的覆盖同一时间段。
    on_chunk_done(current, total) 每个 chunk 完成后回调。
    force_full_days=True 时强制回溯 days 自然日 (不增量补, 用于个股补齐历史)。
    """
    minute_provider = preferences.get_minute_data_provider()
    # resolver 调用统一走 _resolve_minute_provider, 与 _try_custom_minute 共用异常边界。
    # Missing or failed custom sources remain unavailable; no implicit fallback.
    _, fallback, resolve_err = _resolve_minute_provider(minute_provider)
    minute_is_custom = not fallback
    if resolve_err is not None:
        logger.warning("custom minute provider %s resolution failed at sync_and_persist_minute, treating as non-custom: %s",
                       minute_provider, resolve_err)
    if not symbols:
        return 0
    if not minute_is_custom:
        return 0

    # 迁移:旧版 _normalize_minute 未转换 timestamp→datetime,导致全部 datetime 为 null
    # 检测到后直接清除(这些数据无法使用)
    _cleanup_null_datetime_minute(repo)

    # 迁移:旧版按 symbol= 分区转为 date= 分区
    _migrate_symbol_to_date_partition(repo)

    now = datetime.now()

    if extend_backward:
        # 向前扩展模式: 从本地最早数据往前补, 叠加已有数据避免缺口。
        earliest_dt = _earliest_minute_datetime(repo)
        # 按交易日换算自然日 (7/5 系数)。>41 交易日时 +10 天余量覆盖节假日。
        # (分段由 sync_minute_batch 的 segment_trading_days 控制, 与此处的区间天数独立。)
        calendar_days = int(days * 7 / 5) + (10 if days > 41 else 0)
        if earliest_dt:
            end_time = earliest_dt
            start_time = end_time - timedelta(days=calendar_days)
        else:
            # 本地无数据 → 从今天往前拉
            start_time = now - timedelta(days=calendar_days)
            end_time = now
    else:
        # 默认增量模式: 首次拉取回溯 N 天, 已有数据则从最新时间增量补到今天
        # force_full_days=True: 强制回溯 days 自然日 (个股补齐历史, 不增量)
        last_dt = _latest_minute_datetime(repo)
        if force_full_days:
            # 按交易日换算自然日 (7/5 系数), 确保覆盖足够交易日
            calendar_days = int(days * 7 / 5) + 5
            start_time = now - timedelta(days=calendar_days)
        elif last_dt:
            start_time = last_dt
        else:
            start_time = now - timedelta(days=days)
        end_time = now

    limit = resolve_limit(
        capset,
        Cap.KLINE_MINUTE_BATCH,
        default_batch=100,
        default_rpm=30,
        default_rpm_when_unset=False,
    )

    # 流式落盘: 每段拉完立即写盘, 内存峰值 = 单段 (而非全量)。
    # 全量攒内存曾导致 1 年全市场分钟 K OOM 卡死 (3 亿行 / 数十 GB)。
    minute_dir = repo.store.data_dir / "kline_minute"
    written_box = [0]  # list 闭包, 绕过 Python 闭包外层赋值

    def _persist(seg_df: pl.DataFrame) -> None:
        # 单股自动补齐可能与另一个补齐请求同时写同一日期分区。Windows 不允许
        # 替换仍被另一写入占用的临时文件,因此读-改-写必须复用仓库写锁。
        with repo._write_lock:
            written_box[0] += _write_minute_partition(seg_df, minute_dir)

    segment_days = preferences.get_minute_sync_segment_days()
    sync_minute_batch(
        symbols, start_time=start_time, end_time=end_time,
        batch_size=limit.batch, rpm=limit.rpm,
        on_chunk_done=on_chunk_done,
        segment_trading_days=segment_days,
        on_segment=_persist,
        asset_type="stock",
    )

    if written_box[0] == 0:
        return 0
    written = written_box[0]

    # 刷新视图
    try:
        d = repo.store.data_dir.as_posix()
        repo.db.execute(
            f"""CREATE OR REPLACE VIEW kline_minute AS
                SELECT * FROM read_parquet('{d}/kline_minute/**/*.parquet', union_by_name=true)"""
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("refresh kline_minute view failed: %s", e)

    logger.info("minute K synced: %d rows (%d symbols)", written, len(symbols))
    return written

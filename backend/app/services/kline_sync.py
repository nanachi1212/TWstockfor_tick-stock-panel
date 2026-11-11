"""日 K 同步服務(§7.7 Step 1)。

調度器在 capability 允許下,把符號集合的日 K 批次同步到本地 Parquet。
策略:
  - 日 K 僅使用 `kline.daily.batch`
  - 除權因子僅使用 `adj_factor`
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, time, timedelta

import polars as pl

from app.capabilities import Cap, CapabilitySet
from app.data_providers.base import AssetType
from app.rate_limits import resolve_limit
from app.repository import KlineRepository
from app.services import preferences
from app.taiwan.realtime.calendar import TAIPEI_TZ, taipei_now

logger = logging.getLogger(__name__)


def _atomic_write_parquet(df: pl.DataFrame, out) -> None:
    """先寫臨時檔案再原子替換, 避免行程中斷留下損壞的 parquet。

    與 repository._atomic_write_parquet 同語意。adj_factor 的 all.parquet 是全市場
    單檔案、每次「讀→concat→原地寫」, 直接 write_parquet(out) 在行程被 kill
    (dev.sh 清端口用 kill -9)、reap 超時或斷電時會留下半截檔案, 之後復權檢視表
    scan_parquet 整條鏈路出錯、enriched 全市場重算不出。臨時檔案後綴 .tmp 不匹配
    *.parquet glob, 不會被掃描誤讀。
    """
    tmp = out.with_name(out.name + ".tmp")
    df.write_parquet(tmp)
    tmp.replace(out)  # 同目錄 rename, POSIX/NTFS 均為原子操作


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
    name = preferences.get_daily_data_provider()
    if name != "taiwan" and not custom_sources.provider_has_dataset(name, "daily"):
        if failed_out is not None:
            failed_out.extend(symbols)
        logger.warning("Daily provider %s does not provide daily data", name)
        return pl.DataFrame()
    return get_provider(name).get_daily(
        symbols, start_time=start_time, end_time=end_time, on_chunk_done=on_chunk_done,
    )


# ===== 分鐘 K 同步 =====

CANONICAL_MINUTE_COLS = [
    "symbol", "datetime", "open", "high", "low", "close", "volume", "amount",
]


def _normalize_minute(df_in, default_symbol: str | None = None) -> pl.DataFrame:
    """把 SDK 回傳的分鐘 K 資料規範成 canonical 欄。"""
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

    # datetime 欄:優先用 timestamp(毫秒精度),其次 trade_time
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

    # 類型規範:統一轉 Datetime('us')
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


def _write_minute_partition(df: pl.DataFrame, minute_dir) -> int:
    """按 _trade_date 分區寫入磁碟分鐘 K (讀舊→concat→unique→原子寫)。回傳寫入列數。

    抽自原 sync_and_persist_minute 末尾的循環, 供流式寫入磁碟 (每段一次) 與一次性遷移共用。
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
    """本地分鐘 K 資料的最新時間。"""
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
    """本地分鐘 K 資料的最早時間 (用於向前擴展的起點)。"""
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
    """檢測並清除 datetime 全為 null 的舊版分鐘 K 資料(遷移用)。"""
    minute_dir = repo.store.data_dir / "kline_minute"
    if not minute_dir.exists():
        return
    try:
        row = repo.execute_one(
            "SELECT count(*) AS total, count(datetime) AS non_null FROM kline_minute"
        )
        if row and row[0] > 0 and (row[1] is None or row[1] == 0):
            # 全部 datetime 為 null — 清除所有分鐘 K parquet
            n = 0
            for f in minute_dir.rglob("*.parquet"):
                f.unlink()
                n += 1
            logger.info("cleaned %d corrupted minute-K parquet files (null datetime)", n)
    except Exception as e:  # noqa: BLE001
        logger.debug("minute cleanup check failed: %s", e)


def _migrate_symbol_to_date_partition(repo: KlineRepository) -> None:
    """將舊版 symbol= 分區遷移為 date= 分區。遷移完成後刪除舊目錄。"""
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
        # 資料全部不可用，直接刪舊目錄
        for d in old_dirs:
            d.mkdir(parents=True, exist_ok=True)
            for f in d.rglob("*"):
                if f.is_file():
                    f.unlink()
            d.rmdir()
        return

    combined = pl.concat(all_frames, how="diagonal_relaxed")
    combined = combined.unique(subset=["symbol", "datetime"], keep="last")

    # 按日期寫新分區
    combined = combined.with_columns(pl.col("datetime").dt.date().alias("_trade_date"))
    for day_df in combined.partition_by("_trade_date"):
        trade_date = day_df["_trade_date"][0]
        out = minute_dir / f"date={trade_date}" / "part.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        day_df = day_df.drop("_trade_date").sort("symbol", "datetime")
        _atomic_write_parquet(day_df, out)

    # 刪舊目錄
    for d in old_dirs:
        for f in d.rglob("*"):
            if f.is_file():
                f.unlink()
        # 移除空目錄
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
    """同步分鐘 K 並存到 Parquet(前復權價格, SDK 端 adjust=qfq)。回傳寫入列數。

    使用 start_time / end_time 區間取得, 確保所有標的覆蓋同一時間段。
    on_chunk_done(current, total) 每個 chunk 完成後回呼。
    force_full_days=True 時強制回溯 days 自然日 (不增量補, 用於個股補齊歷史)。
    """
    minute_provider = preferences.get_minute_data_provider()
    # resolver 呼叫統一走 _resolve_minute_provider, 與 _try_custom_minute 共用異常邊界。
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

    # 遷移:舊版 _normalize_minute 未轉換 timestamp→datetime,導致全部 datetime 為 null
    # 檢測到後直接清除(這些資料無法使用)
    _cleanup_null_datetime_minute(repo)

    # 遷移:舊版按 symbol= 分區轉為 date= 分區
    _migrate_symbol_to_date_partition(repo)

    now = datetime.now()

    if extend_backward:
        # 向前擴展模式: 從本地最早資料往前補, 疊加已有資料避免缺口。
        earliest_dt = _earliest_minute_datetime(repo)
        # 按交易日換算自然日 (7/5 係數)。>41 交易日時 +10 天餘量覆蓋節假日。
        # (分段由 sync_minute_batch 的 segment_trading_days 控制, 與此處的區間天數獨立。)
        calendar_days = int(days * 7 / 5) + (10 if days > 41 else 0)
        if earliest_dt:
            end_time = earliest_dt
            start_time = end_time - timedelta(days=calendar_days)
        else:
            # 本地無資料 → 從今天往前拉
            start_time = now - timedelta(days=calendar_days)
            end_time = now
    else:
        # 預設增量模式: 首次取得回溯 N 天, 已有資料則從最新時間增量補到今天
        # force_full_days=True: 強制回溯 days 自然日 (個股補齊歷史, 不增量)
        last_dt = _latest_minute_datetime(repo)
        if force_full_days:
            # 按交易日換算自然日 (7/5 係數), 確保覆蓋足夠交易日
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

    # 流式寫入磁碟: 每段拉完立即寫盤, 記憶體峰值 = 單段 (而非全量)。
    # 全量攢記憶體曾導致 1 年全市場分鐘 K OOM 卡死 (3 億行 / 數十 GB)。
    minute_dir = repo.store.data_dir / "kline_minute"
    written_box = [0]  # list 閉包, 繞過 Python 閉包外層賦值

    def _persist(seg_df: pl.DataFrame) -> None:
        # 單股自動補齊可能與另一個補齊請求同時寫同一日期分區。Windows 不允許
        # 替換仍被另一寫入佔用的臨時檔案,因此讀-改-寫必須重用倉庫寫鎖。
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

    # 重新整理檢視表
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

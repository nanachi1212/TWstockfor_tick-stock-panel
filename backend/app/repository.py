"""Repository 層(§7.4)。

資料分層:
  - DuckDB 檢視表: 冷查詢(統計、元資料、使用者自訂SQL)
  - Polars 快取: 熱路徑(enriched 最新日 ~5500行 + instruments ~5500行)
  - Polars scan_parquet: 分鐘K/歷史日K (predicate pushdown)

快取生命週期:
  - startup 時不載入(資料可能為空)
  - pipeline 完成後呼叫 refresh_cache()
  - 服務層透過 get_enriched_latest() / get_instruments() 取得快取
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path

import duckdb
import polars as pl

from app.config import settings
from app.enriched_generation import (
    EnrichedGenerationUnavailableError,
    EnrichedPublication,
    get_enriched_generation,
)
from app.parquet import scan_enriched_parquet
from app.taiwan.realtime.calendar import taipei_today

logger = logging.getLogger(__name__)


def migrate_legacy_desktop_data(data_dir: Path) -> bool:
    """Copy legacy frozen desktop data into the per-user data directory."""
    if not getattr(sys, "frozen", False):
        return False

    import shutil

    target = Path(data_dir)
    target.mkdir(parents=True, exist_ok=True)
    try:
        exe_dir = Path(sys.executable).resolve().parent
        legacy_dirs = (
            exe_dir / "data",
            exe_dir.parent / "TickFlowStockPanel_Data",
        )
        legacy_dir = next((path for path in legacy_dirs if path.exists()), None)
        if legacy_dir is None or legacy_dir.resolve() == target.resolve():
            return False

        meaningful_target_files = [
            path
            for path in target.rglob("*")
            if path.is_file() and path.name not in {"desktop.log", ".write_probe"}
        ]
        if meaningful_target_files:
            logger.info(
                "legacy data dir %s exists but new %s already has data, skip migration",
                legacy_dir,
                target,
            )
            return False

        logger.info("copying legacy data %s -> %s", legacy_dir, target)
        for item in legacy_dir.iterdir():
            dest = target / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
        logger.info("legacy data migration copy done; source kept at %s", legacy_dir)
        return True
    except Exception as exc:
        logger.warning("legacy data migration failed (startup continues): %s", exc)
        return False


def _last_available_rows(df: pl.DataFrame, cutoff: date) -> pl.DataFrame:
    """從已按 symbol/date 排序的資料中取每隻標的最後一條有效狀態。"""
    if df.is_empty():
        return df
    return (
        df.filter(pl.col("date") <= cutoff)
        .group_by("symbol", maintain_order=True)
        .last()
    )


class DataStore:
    """唯一的儲存入口 — 行程啟動時建立。"""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = Path(data_dir or settings.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # 一次性資料遷移: 舊桌面版曾使用 exe 同級 data/ 或兄弟目錄
        # TickFlowStockPanel_Data/; 新版固定使用 %LOCALAPPDATA%。
        self._migrate_legacy_data_dir()

        # 關鍵子目錄(§7.2)
        for sub in (
            "kline_daily",
            "kline_daily_enriched",
            "kline_index_daily",
            "kline_index_enriched",
            "kline_etf_daily",
            "kline_etf_enriched",
            "kline_etf_minute",
            "kline_minute",
            "adj_factor",
            "adj_factor_etf",
            "financials",
            "instruments",
            "instruments_index",
            "instruments_etf",
            "instruments_ext",
            "kline_ext",
            "pools",
            "backtest_results",
            "screener_results",
            "ai_cache",
            "user_data",
            "depth5",
        ):
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)

        # 財務資料子目錄
        for sub in ("metrics", "income", "balance_sheet", "cash_flow", "shares"):
            (self.data_dir / "financials" / sub).mkdir(parents=True, exist_ok=True)

        # DuckDB 記憶體模式 — 不建 .db 檔案(§7.1)
        self.db = duckdb.connect(database=":memory:")
        self._register_views()

    def _migrate_legacy_data_dir(self) -> None:
        """把舊桌面版資料保守複製到新的 per-user data_dir。

        背景: 舊版 data_dir = exe_dir.parent / "TickFlowStockPanel_Data" (兄弟目錄),
        後續版本曾改為 exe_dir / "data"。新版改用 %LOCALAPPDATA%, 若不遷移會
        讓歷史行情、策略、回測與監控看似消失。

        策略 (僅打包桌面版觸發, 開發/Docker 不受影響):
          1. 舊目錄存在且新 data_dir 還基本為空 → 逐項複製, 來源保留。
          2. 新舊目錄都已有資料 → 不自動合併, 避免覆蓋較新的使用者資料。
          3. 舊目錄不存在 → 新裝使用者, 無需遷移。
        所有異常都吞掉只記警告 —— 資料遷移失敗絕不能阻塞應用啟動。
        """
        migrate_legacy_desktop_data(self.data_dir)

    def _register_views(self) -> None:
        """把 Parquet 目錄掛載為 DuckDB 檢視表(§7.3)。"""
        d = self.data_dir.as_posix()
        statements = [
            f"""CREATE OR REPLACE VIEW kline_daily AS
                SELECT * FROM read_parquet('{d}/kline_daily/**/*.parquet', union_by_name=true)""",
            f"""CREATE OR REPLACE VIEW kline_enriched AS
                SELECT * FROM read_parquet('{d}/kline_daily_enriched/**/*.parquet', union_by_name=true)""",
            f"""CREATE OR REPLACE VIEW kline_index_daily AS
                SELECT * FROM read_parquet('{d}/kline_index_daily/**/*.parquet', union_by_name=true)""",
            f"""CREATE OR REPLACE VIEW kline_index_enriched AS
                SELECT * FROM read_parquet('{d}/kline_index_enriched/**/*.parquet', union_by_name=true)""",
            f"""CREATE OR REPLACE VIEW kline_etf_daily AS
                SELECT * FROM read_parquet('{d}/kline_etf_daily/**/*.parquet', union_by_name=true)""",
            f"""CREATE OR REPLACE VIEW kline_etf_enriched AS
                SELECT * FROM read_parquet('{d}/kline_etf_enriched/**/*.parquet', union_by_name=true)""",
            f"""CREATE OR REPLACE VIEW kline_etf_minute AS
                SELECT * FROM read_parquet('{d}/kline_etf_minute/**/*.parquet', union_by_name=true)""",
            f"""CREATE OR REPLACE VIEW kline_minute AS
                SELECT * FROM read_parquet('{d}/kline_minute/**/*.parquet', union_by_name=true)""",
            f"""CREATE OR REPLACE VIEW adj_factor AS
                SELECT * FROM read_parquet('{d}/adj_factor/**/*.parquet', union_by_name=true)""",
            f"""CREATE OR REPLACE VIEW adj_factor_etf AS
                SELECT * FROM read_parquet('{d}/adj_factor_etf/**/*.parquet', union_by_name=true)""",
            f"""CREATE OR REPLACE VIEW instruments AS
                SELECT * FROM read_parquet('{d}/instruments/**/*.parquet', union_by_name=true)""",
            f"""CREATE OR REPLACE VIEW instruments_index AS
                SELECT * FROM read_parquet('{d}/instruments_index/**/*.parquet', union_by_name=true)""",
            f"""CREATE OR REPLACE VIEW instruments_etf AS
                SELECT * FROM read_parquet('{d}/instruments_etf/**/*.parquet', union_by_name=true)""",
        ]
        for sql in statements:
            try:
                self.db.execute(sql)
            except Exception:
                # 空資料目錄(首次啟動)或權限問題時 DuckDB 會拋 IOException;
                # 跨版本/平台也可能拋 CatalogException 等。空目錄缺檢視表不影響啟動
                # (後續同步寫入資料後會重新重新整理檢視表),這裡一律降級為 debug 日誌。
                logger.debug("view registration skipped (no parquet yet): %s", sql[:60])


class KlineRepository:
    """日 K / 分鐘 K 的讀寫入口。"""

    def __init__(self, store: DataStore) -> None:
        self.store = store
        self.db = store.db
        self._lock = threading.Lock()
        # 序列化 parquet 分區的讀-改-寫: 即時輪詢執行緒、手動 refresh、盤後管道
        # 可能並行 merge/flush 同一分區檔案, 無鎖會互相覆蓋丟資料
        self._write_lock = threading.Lock()

        # ---- Polars 快取 ----
        self._enriched_cache: pl.DataFrame | None = None       # 最新一天 (~5500行)
        self._enriched_cache_date: date | None = None
        self._live_agg_cache: pl.DataFrame | None = None       # 預計算聚合表 (~5500行)
        self._live_agg_cache_date: date | None = None
        self._live_agg_check_date: date | None = None          # 上次跨日校驗時的 today (快路徑節流)
        self._instruments_cache: pl.DataFrame | None = None
        # 完整 enriched 歷史 (含所有指標, 供 filter_history 策略使用)
        self._enriched_history_cache: pl.DataFrame | None = None  # ~100万行
        self._enriched_history_start: date | None = None
        self._enriched_history_generation: str | None = None
        self._index_instruments_cache: pl.DataFrame | None = None
        self._etf_enriched_cache: pl.DataFrame | None = None
        self._etf_enriched_cache_date: date | None = None
        self._etf_live_agg_cache: pl.DataFrame | None = None
        self._etf_live_agg_cache_date: date | None = None
        self._etf_instruments_cache: pl.DataFrame | None = None
        # symbol 集合 memo (隨對應 instruments 快取失效): 供每請求資產分流用
        self._index_symbol_set_cache: set[str] | None = None
        self._etf_symbol_set_cache: set[str] | None = None
        self._name_map_cache: dict[str, str] | None = None
        self._index_enriched_cache: pl.DataFrame | None = None
        self._index_enriched_cache_date: date | None = None

        # ---- enriched 背景預熱 ----
        # 啟動時 compute_indicators (107萬行, 低配機 50s+) 移出 lifespan 關鍵路徑,
        # 推到 daemon 執行緒非同步完成。預熱期間 get_enriched_latest / get_live_agg
        # 回傳空表 (上層優雅降級), 不觸發同步重算 (否則會抵消非同步化收益)。
        self._enriched_warming: bool = False
        self._warmup_thread: threading.Thread | None = None
        self._warmup_lock = threading.Lock()
        # 預熱完成後的回呼 (lifespan 注入, 用於設定 app.state.indicators_ready)
        self._on_warmup_done: Callable[[], None] | None = None
        # parquet/instruments 同步重新整理完成後的輕量回呼；用於調度派生快取預熱。
        self._on_refresh_done: Callable[[], None] | None = None

        # parquet glob 路徑
        self._enriched_glob = str(store.data_dir / "kline_daily_enriched" / "**" / "*.parquet")
        self._index_enriched_glob = str(store.data_dir / "kline_index_enriched" / "**" / "*.parquet")
        self._etf_enriched_glob = str(store.data_dir / "kline_etf_enriched" / "**" / "*.parquet")
        self._minute_glob = str(store.data_dir / "kline_minute" / "**" / "*.parquet")
        self._etf_minute_glob = str(store.data_dir / "kline_etf_minute" / "**" / "*.parquet")
        self._inst_glob = str(store.data_dir / "instruments" / "**" / "*.parquet")
        self._index_inst_glob = str(store.data_dir / "instruments_index" / "**" / "*.parquet")
        self._etf_inst_glob = str(store.data_dir / "instruments_etf" / "**" / "*.parquet")

    def execute_all(self, sql: str, params: list | None = None) -> list[tuple]:
        """執行緒安全的 SELECT → fetchall。DuckDB 單 connection 非執行緒安全，所有讀路徑須走此方法。"""
        with self._lock:
            cursor = self.db.cursor()
            try:
                return cursor.execute(sql, params or []).fetchall()
            finally:
                cursor.close()

    def execute_one(self, sql: str, params: list | None = None) -> tuple | None:
        """執行緒安全的 SELECT → fetchone。"""
        with self._lock:
            cursor = self.db.cursor()
            try:
                return cursor.execute(sql, params or []).fetchone()
            finally:
                cursor.close()

    # ================================================================
    # Polars 快取管理
    # ================================================================

    def refresh_cache(self, background: bool = False) -> None:
        """重新整理 Polars 快取。在 pipeline 完成後、服務啟動時呼叫。

        background=True (啟動時): instruments/index/ETF 同步重新整理 (毫秒級),
        enriched 的重計算 (compute_indicators, 107萬行) 推到 daemon 執行緒,
        不阻塞 FastAPI lifespan。預熱期間上層走空表降級。
        background=False (盤後管道/手動重新整理): 全部同步, 保證資料即時一致。
        """
        started = time.perf_counter()
        logger.info("cache refresh start (background=%s)", background)

        step = time.perf_counter()
        logger.info("cache refresh step start: instruments")
        self._refresh_instruments()
        logger.info("cache refresh step done: instruments (%.2fs)", time.perf_counter() - step)

        step = time.perf_counter()
        logger.info("cache refresh step start: index instruments")
        self._refresh_index_instruments()
        logger.info("cache refresh step done: index instruments (%.2fs)", time.perf_counter() - step)

        step = time.perf_counter()
        logger.info("cache refresh step start: ETF instruments")
        self._refresh_etf_instruments()
        logger.info("cache refresh step done: ETF instruments (%.2fs)", time.perf_counter() - step)

        # ETF enriched 只失效不重建: 下次存取時按新資料懶載入,
        # 避免自選無 ETF 的使用者在管道後白付全量重算成本
        self._etf_enriched_cache = None
        self._etf_enriched_cache_date = None
        # 指數 enriched 同樣只失效不重建 (懶載入)
        self._index_enriched_cache = None
        self._index_enriched_cache_date = None

        if background:
            logger.info("cache refresh: enriched 推背景執行緒預熱")
            self._start_enriched_warmup()
        else:
            step = time.perf_counter()
            logger.info("cache refresh step start: enriched")
            self._refresh_enriched()
            logger.info("cache refresh step done: enriched (%.2fs)", time.perf_counter() - step)
            self._notify_refresh_done()

        logger.info("cache refresh done (%.2fs)", time.perf_counter() - started)

    def _start_enriched_warmup(self) -> None:
        """啟動背景 daemon 執行緒預熱 enriched 快取 (compute_indicators)。

        仿 QuoteService 的執行緒模式: 設 warming 標誌 → 起 daemon → 完成後清標誌 +
        觸發回呼。重複呼叫時若已有執行緒在跑則跳過 (避免重複預熱)。
        """
        with self._warmup_lock:
            if self._enriched_warming:
                logger.info("enriched warmup already in progress, skip")
                return
            self._enriched_warming = True

        def _warmup() -> None:
            t0 = time.perf_counter()
            try:
                logger.info("enriched warmup thread started")
                self._refresh_enriched()
                logger.info("enriched warmup thread done (%.1fs)", time.perf_counter() - t0)
                self._notify_refresh_done()
            except Exception:
                logger.exception("enriched warmup thread failed")
            finally:
                with self._warmup_lock:
                    self._enriched_warming = False
                cb = self._on_warmup_done
                if cb is not None:
                    try:
                        cb()
                    except Exception:
                        logger.warning("enriched warmup callback failed", exc_info=True)

        self._warmup_thread = threading.Thread(
            target=_warmup, name="enriched-warmup", daemon=True,
        )
        self._warmup_thread.start()

    def _notify_refresh_done(self) -> None:
        callback = self._on_refresh_done
        if callback is None:
            return
        try:
            callback()
        except Exception:
            logger.warning("repository refresh callback failed", exc_info=True)


    def clear_cache(self) -> None:
        """清空所有 Polars 記憶體快取。

        與 refresh_cache 的區別: refresh_cache 在磁碟無資料時會提前 return,
        導致記憶體裡的舊快取殘留 (clear 資料後看板仍顯示舊資料的根因)。
        本方法無條件清空, 供清除資料/重置場景呼叫。
        """
        self._enriched_cache = None
        self._enriched_cache_date = None
        self._enriched_history_cache = None
        self._enriched_history_start = None
        self._enriched_history_generation = None
        self._live_agg_cache = None
        self._live_agg_cache_date = None
        self._live_agg_check_date = None
        self._instruments_cache = None
        self._index_instruments_cache = None
        self._etf_enriched_cache = None
        self._etf_enriched_cache_date = None
        self._etf_live_agg_cache = None
        self._etf_live_agg_cache_date = None
        self._etf_instruments_cache = None
        self._index_symbol_set_cache = None
        self._etf_symbol_set_cache = None
        self._name_map_cache = None
        self._index_enriched_cache = None
        self._index_enriched_cache_date = None

    def _refresh_enriched(self) -> None:
        """從 parquet 載入 enriched 最新日到記憶體 + 構建聚合表。

        enriched parquet 僅存 14 欄基礎資料。啟動時讀入歷史資料並即時計算完整指標，
        將結果快取在記憶體中供各服務使用。

        最佳化: 擴大歷史讀取範圍, 同時快取完整歷史 (含指標), 供 filter_history 策略直接重用。
        """
        try:
            started = time.perf_counter()
            refresh_generation = self.get_matrix_data_generation("stock")
            logger.info("enriched refresh start")

            step = time.perf_counter()
            logger.info("enriched refresh step start: latest date")
            latest = self._latest_enriched_date_duckdb()
            logger.info("enriched refresh step done: latest date=%s (%.2fs)", latest, time.perf_counter() - step)
            if not latest:
                # 磁碟已無資料: 必須清空記憶體快取, 否則舊資料會殘留
                # (清資料後看板仍顯示舊資料的根因)
                self.clear_cache()
                logger.info("enriched refresh skipped: no latest date (%.2fs)", time.perf_counter() - started)
                return

            # Step 1: 直接讀最新日期的分區檔案 (僅 14 欄)
            enriched_dir = self.store.data_dir / "kline_daily_enriched"
            ds = latest.isoformat() if hasattr(latest, "isoformat") else str(latest)
            target_parquet = enriched_dir / f"date={ds}" / "part.parquet"

            if not target_parquet.exists():
                logger.info("enriched refresh skipped: %s not found (%.2fs)", target_parquet, time.perf_counter() - started)
                return

            step = time.perf_counter()
            logger.info("enriched refresh step start: read latest parquet %s", target_parquet)
            df_latest = pl.read_parquet(target_parquet)
            logger.info("enriched refresh step done: read latest parquet rows=%d (%.2fs)", len(df_latest), time.perf_counter() - step)
            if df_latest.is_empty():
                logger.info("enriched refresh skipped: latest parquet empty (%.2fs)", time.perf_counter() - started)
                return

            # Step 2: 讀近 300 天 14 欄資料 → compute → filter(latest) → 快取
            # 300 日曆天 ≈ 210 交易日, 覆蓋 filter_history 最大 lookback(90) + warmup(60)
            try:
                from datetime import timedelta

                from app.indicators.pipeline import (
                    compute_indicators,
                    compute_limit_signals,
                    compute_signals,
                )
                start_full = latest - timedelta(days=300)
                read_cols = [c for c in ["symbol", "date", "open", "high", "low", "close",
                                         "volume", "amount", "raw_close", "raw_high", "raw_low"]
                             if c in df_latest.columns]
                lf = (
                    scan_enriched_parquet(self._enriched_glob)
                    .filter(pl.col("date") >= start_full)
                    .sort(["symbol", "date"])
                )

                step = time.perf_counter()
                logger.info("enriched refresh step start: collect history from %s", start_full)
                df_hist = lf.select(read_cols).collect()
                logger.info("enriched refresh step done: collect history rows=%d (%.2fs)", len(df_hist), time.perf_counter() - step)
                if not df_hist.is_empty():
                    instruments = self._instruments_cache if self._instruments_cache is not None else pl.DataFrame()

                    step = time.perf_counter()
                    logger.info("enriched refresh step start: compute indicators")
                    df_full = compute_indicators(df_hist)
                    logger.info("enriched refresh step done: compute indicators rows=%d (%.2fs)", len(df_full), time.perf_counter() - step)

                    step = time.perf_counter()
                    logger.info("enriched refresh step start: compute signals")
                    df_full = compute_signals(df_full)
                    logger.info("enriched refresh step done: compute signals (%.2fs)", time.perf_counter() - step)
                    if instruments is not None and not instruments.is_empty():
                        step = time.perf_counter()
                        logger.info("enriched refresh step start: compute limit signals")
                        df_full = compute_limit_signals(df_full, instruments)
                        logger.info("enriched refresh step done: compute limit signals (%.2fs)", time.perf_counter() - step)

                    # JOIN instruments 到完整歷史 (filter_history/basic_filter 需要 name/股本等列)
                    if instruments is not None and not instruments.is_empty():
                        inst_cols = [c for c in ["name", "total_shares", "float_shares"]
                                     if c in instruments.columns and c not in df_full.columns]
                        if inst_cols:
                            step = time.perf_counter()
                            logger.info("enriched refresh step start: join instruments")
                            df_full = df_full.join(
                                instruments.select(["symbol", *inst_cols]).unique(subset=["symbol"]),
                                on="symbol",
                                how="left",
                            )
                            logger.info("enriched refresh step done: join instruments (%.2fs)", time.perf_counter() - step)

                    # 快取完整歷史 (含指標+必要基礎資訊) 供 filter_history/backtest 直接重用
                    if self.get_matrix_data_generation("stock") != refresh_generation:
                        raise EnrichedGenerationUnavailableError(
                            "enriched data changed while refreshing its history cache"
                        )
                    self._enriched_history_cache = df_full
                    self._enriched_history_start = df_full["date"].min()
                    self._enriched_history_generation = refresh_generation
                    logger.info("enriched 歷史快取: %d rows, %s ~ %s",
                                len(df_full), self._enriched_history_start, latest)

                    # 只取最新一天作為 enriched_cache
                    df_today = df_full.filter(pl.col("date") == latest)
                    if not df_today.is_empty():
                        self._enriched_cache = df_today
                        self._enriched_cache_date = latest
                        # 構建盤中遞推基準: 若最新分區是今天的即時盤中資料,
                        # 遞推狀態必須停在上一交易日, 不能把今天作為“昨日”。
                        step = time.perf_counter()
                        logger.info("enriched refresh step start: build live agg")
                        self._build_live_agg(self._live_agg_baseline_date(latest))
                        logger.info("enriched refresh step done: build live agg (%.2fs)", time.perf_counter() - step)
                        repaired_today = self._restore_missing_latest_rows(
                            latest, df_today, df_full,
                        )
                        if len(repaired_today) > len(df_today):
                            df_full = pl.concat(
                                [df_full.filter(pl.col("date") != latest), repaired_today],
                                how="diagonal_relaxed",
                            ).sort(["symbol", "date"])
                            self._enriched_history_cache = df_full
                            self._enriched_cache = repaired_today
                            df_today = repaired_today
                        logger.info("enriched 快取已計算: %d 只, 日期 %s (即時計算)", len(df_today), latest)
                        logger.info("enriched refresh done (%.2fs)", time.perf_counter() - started)
                        return
            except EnrichedGenerationUnavailableError:
                raise
            except Exception as e:
                logger.warning("enriched 即時計算失敗, 使用原始 14 列快取: %s", e)

            # 降級: 直接使用 14 欄資料 + 構建 live_agg
            self._enriched_cache = df_latest
            self._enriched_cache_date = latest
            step = time.perf_counter()
            logger.info("enriched refresh fallback step start: build live agg")
            self._build_live_agg(self._live_agg_baseline_date(latest))
            logger.info("enriched refresh fallback step done: build live agg (%.2fs)", time.perf_counter() - step)

            logger.info("enriched 快取已載入: %d 只, 日期 %s", len(df_latest), latest)
            logger.info("enriched refresh done fallback (%.2fs)", time.perf_counter() - started)
        except Exception as e:
            logger.warning("enriched 快取重新整理失敗: %s", e)

    def _restore_missing_latest_rows(
        self,
        latest: date,
        df_today: pl.DataFrame,
        history: pl.DataFrame,
    ) -> pl.DataFrame:
        """用同日原始日K補齊舊即時快照漏寫的正常成交股票。"""
        if self._live_agg_cache is None or self._live_agg_cache.is_empty():
            return df_today
        daily_path = (
            self.store.data_dir
            / "kline_daily"
            / f"date={latest.isoformat()}"
            / "part.parquet"
        )
        if not daily_path.exists():
            return df_today

        try:
            from app.indicators.pipeline import compute_enriched_today, filter_halt_days

            daily = filter_halt_days(pl.read_parquet(daily_path))
            missing = daily.join(
                df_today.select("symbol").unique(),
                on="symbol",
                how="anti",
            )
            if missing.is_empty():
                return df_today

            missing_symbols = missing.select("symbol").unique()
            previous = history.filter(pl.col("date") < latest).join(
                missing_symbols,
                on="symbol",
                how="semi",
            )
            if not previous.is_empty():
                previous = _last_available_rows(previous, latest)
            recovered = compute_enriched_today(
                self._live_agg_cache,
                previous,
                missing,
                self.get_instruments(),
            )
            if recovered.is_empty():
                return df_today
            recovered = self._with_instrument_metadata("stock", recovered)
            result = pl.concat(
                [df_today, recovered],
                how="diagonal_relaxed",
            ).unique(subset=["symbol", "date"], keep="last").sort("symbol")
            logger.info(
                "enriched latest cache restored from daily: date=%s, rows=%d",
                latest,
                len(result) - len(df_today),
            )
            return result
        except Exception as e:
            logger.warning("enriched latest cache restore skipped: %s", e)
            return df_today

    def _build_live_agg(self, latest: date) -> None:
        """從 OHLCV 即時計算遞推狀態 + 視窗聚合, 構建盤中即時聚合表。

        最佳化: 優先使用 _enriched_history_cache (啟動時已計算), 避免重複 compute_indicators。
        """
        from datetime import timedelta

        from app.indicators.pipeline import _ema_alpha

        started = time.perf_counter()
        logger.info("live agg build start: latest=%s", latest)
        # 遞推狀態 (EMA/RSI 種子) 與全量版同樣用近 300 天, 暖機長度才一致;
        # 歷史不足的股票由 _window_len 讓增量版把對應指標留空, 不會低估。
        start_60d = latest - timedelta(days=90)
        start_state = latest - timedelta(days=300)

        # 優先使用已有的歷史快取 (避免重複 scan_parquet + compute_indicators)
        if self._enriched_history_cache is not None and not self._enriched_history_cache.is_empty():
            hist_all = self._enriched_history_cache
            if "date" in hist_all.columns and hist_all["date"].min() <= start_60d:
                # 從歷史快取中提取所需列 (歷史快取已有指標欄)
                base_cols = [
                    "symbol", "date", "open", "high", "low", "close", "volume",
                    "raw_close", "raw_high", "raw_low",
                    "consecutive_limit_ups", "consecutive_limit_downs",
                ]
                needed = [c for c in base_cols if c in hist_all.columns]
                step = time.perf_counter()
                logger.info("live agg step start: slice history cache")
                df_hist = hist_all.filter(
                    pl.col("date") <= latest
                ).select(needed).sort(["symbol", "date"])
                logger.info("live agg step done: slice history cache rows=%d (%.2fs)", len(df_hist), time.perf_counter() - step)

                state_cols = [
                    "symbol",
                    "ema5", "ema10", "ema20", "ema30", "ema60",
                    "macd_dea",
                    "kdj_k", "kdj_d",
                    "atr_14",
                    "close", "high", "low",
                    "annual_vol_20d",
                ]
                existing_state = [c for c in state_cols if c in hist_all.columns]
                state_source = _last_available_rows(
                    hist_all.select("date", *existing_state), latest,
                )
                agg_a = state_source.select(existing_state)
            else:
                df_hist = pl.DataFrame()
                agg_a = pl.DataFrame()
        else:
            # 降級: 讀 parquet + compute_indicators
            df_hist, agg_a = self._build_live_agg_from_parquet(latest, start_state)

        if df_hist.is_empty():
            self._live_agg_cache = pl.DataFrame()
            self._live_agg_cache_date = None
            logger.info("live agg build skipped: empty history (%.2fs)", time.perf_counter() - started)
            return

        if agg_a.is_empty():
            self._live_agg_cache = pl.DataFrame()
            self._live_agg_cache_date = None
            logger.info("live agg build skipped: empty state (%.2fs)", time.perf_counter() - started)
            return

        # 單獨計算 _ema12 / _ema26 (compute_indicators 內部會 drop 掉)
        step = time.perf_counter()
        logger.info("live agg step start: ema state")
        df_ema = _last_available_rows(
            df_hist.sort(["symbol", "date"]).with_columns([
                pl.col("close").ewm_mean(alpha=_ema_alpha(12), adjust=False).over("symbol").alias("_ema12"),
                pl.col("close").ewm_mean(alpha=_ema_alpha(26), adjust=False).over("symbol").alias("_ema26"),
            ]).select("symbol", "date", "_ema12", "_ema26"),
            latest,
        ).select("symbol", "_ema12", "_ema26")

        agg_a = agg_a.join(df_ema, on="symbol", how="inner")
        logger.info("live agg step done: ema state (%.2fs)", time.perf_counter() - step)

        # 單獨計算 RSI 狀態欄 (compute_indicators 內部會 drop 掉)
        step = time.perf_counter()
        logger.info("live agg step start: rsi state")
        df_rsi_base = df_hist.sort(["symbol", "date"]).with_columns(
            pl.col("close").diff().over("symbol").alias("_daily_delta")
        )
        gain = pl.when(pl.col("_daily_delta") > 0).then(pl.col("_daily_delta")).otherwise(0.0)
        loss = pl.when(pl.col("_daily_delta") < 0).then(-pl.col("_daily_delta")).otherwise(0.0)
        rsi_exprs = []
        for n in (6, 14, 24):
            a = 1.0 / n
            rsi_exprs.append(gain.ewm_mean(alpha=a, adjust=False).over("symbol").alias(f"_rsi_avg_gain_{n}"))
            rsi_exprs.append(loss.ewm_mean(alpha=a, adjust=False).over("symbol").alias(f"_rsi_avg_loss_{n}"))
        df_rsi = (
            _last_available_rows(
                df_rsi_base.with_columns(rsi_exprs), latest,
            )
            .select("symbol", *[f"_rsi_avg_gain_{n}" for n in (6, 14, 24)],
                              *[f"_rsi_avg_loss_{n}" for n in (6, 14, 24)])
        )
        agg_a = agg_a.join(df_rsi, on="symbol", how="inner")
        logger.info("live agg step done: rsi state (%.2fs)", time.perf_counter() - step)

        # 前復權因子: adj_factor = close(復權) / raw_close(原始)
        if "raw_close" in df_hist.columns:
            step = time.perf_counter()
            logger.info("live agg step start: adj factor state")
            adj_factor_df = (
                _last_available_rows(
                    df_hist.select("symbol", "date", "close", "raw_close"), latest,
                )
                .select("symbol", (pl.col("close") / pl.col("raw_close")).alias("_adj_factor"))
            )
            agg_a = agg_a.join(adj_factor_df, on="symbol", how="left")
            if "_adj_factor" in agg_a.columns:
                agg_a = agg_a.with_columns(pl.col("_adj_factor").fill_null(1.0))
            logger.info("live agg step done: adj factor state (%.2fs)", time.perf_counter() - step)

        # annual_vol_20d 遞推狀態: 最近 19 天日報酬率的部分和 / 平方和
        step = time.perf_counter()
        logger.info("live agg step start: annual vol state")
        df_daily_pct = (
            df_hist.sort(["symbol", "date"])
            .with_columns(
                pl.col("close").pct_change().over("symbol").alias("_daily_pct")
            )
        )
        df_vol = df_daily_pct.group_by("symbol").agg([
            pl.col("_daily_pct").tail(19).sum().alias("_vol_19d_pct_sum"),
            (pl.col("_daily_pct") ** 2).tail(19).sum().alias("_vol_19d_pct_sq_sum"),
        ])
        agg_a = agg_a.join(df_vol, on="symbol", how="left")
        logger.info("live agg step done: annual vol state (%.2fs)", time.perf_counter() - step)

        # 昨日連續漲停天數: 使用每隻股票最後一個有效交易日狀態 (用於增量計算同向 +1)
        step = time.perf_counter()
        logger.info("live agg step start: consecutive state")
        consec_cols = [c for c in ["symbol", "consecutive_limit_ups", "consecutive_limit_downs"]
                       if c in df_hist.columns]
        consec_source = df_hist
        if len(consec_cols) != 3:
            lf = (
                scan_enriched_parquet(self._enriched_glob)
                .filter((pl.col("date") >= start_60d) & (pl.col("date") <= latest))
                .sort(["symbol", "date"])
            )
            consec_cols = [
                c for c in ["symbol", "consecutive_limit_ups", "consecutive_limit_downs"]
                if c in lf.collect_schema().names()
            ]
            consec_source = lf.select("date", *consec_cols).collect()
        if len(consec_cols) == 3:
            consec_df = _last_available_rows(
                consec_source.select("date", *consec_cols), latest,
            )
            if not consec_df.is_empty():
                consec = consec_df.select(
                    "symbol",
                    pl.col("consecutive_limit_ups").alias("_prev_consec_up"),
                    pl.col("consecutive_limit_downs").alias("_prev_consec_down"),
                )
                agg_a = agg_a.join(consec, on="symbol", how="left")
        logger.info("live agg step done: consecutive state (%.2fs)", time.perf_counter() - step)

        # B類: 按 symbol 分組聚合 — 視窗統計
        step = time.perf_counter()
        logger.info("live agg step start: rolling windows")
        agg_b = (
            df_hist.sort(["symbol", "date"])
            .group_by("symbol")
            .agg([
                pl.col("close").tail(4).sum().alias("_ma5_partial_sum"),
                pl.col("close").tail(9).sum().alias("_ma10_partial_sum"),
                pl.col("close").tail(19).sum().alias("_ma20_partial_sum"),
                pl.col("close").tail(29).sum().alias("_ma30_partial_sum"),
                pl.col("close").tail(59).sum().alias("_ma60_partial_sum"),

                pl.col("close").tail(19).sum().alias("_boll_partial_sum"),
                (pl.col("close").tail(19) ** 2).sum().alias("_boll_partial_sq_sum"),

                # 60 日高低用收盤價口徑, 與全量版 rolling_max/min(close) 一致
                pl.col("close").tail(59).max().alias("_high_59d"),
                pl.col("close").tail(59).min().alias("_low_59d"),

                pl.col("close").tail(5).first().alias("_close_5d_ago"),
                pl.col("close").tail(10).first().alias("_close_10d_ago"),
                pl.col("close").tail(20).first().alias("_close_20d_ago"),
                pl.col("close").tail(30).first().alias("_close_30d_ago"),
                pl.col("close").tail(60).first().alias("_close_60d_ago"),

                pl.col("volume").tail(4).sum().alias("_vol_ma5_partial_sum"),
                pl.col("volume").tail(9).sum().alias("_vol_ma10_partial_sum"),
                # 標準量比分母: 前5日成交量之和(不含當天), 用於 vol_ratio_5d
                pl.col("volume").tail(5).sum().alias("_vol_ma5_prev_sum"),

                pl.col("low").tail(8).min().alias("_kdj_8d_low"),
                pl.col("high").tail(8).max().alias("_kdj_8d_high"),

                # 該股票在視窗內的歷史根數, 增量版據此判斷暖機是否足夠
                pl.len().alias("_window_len"),
            ])
        )

        self._live_agg_cache = agg_a.join(agg_b, on="symbol", how="inner")
        self._live_agg_cache_date = latest
        logger.info("live agg step done: rolling windows (%.2fs)", time.perf_counter() - step)
        logger.info("live agg build done: rows=%d (%.2fs)", len(self._live_agg_cache), time.perf_counter() - started)

    def _live_agg_baseline_date(self, latest: date) -> date:
        """盤中遞推基準日期。當天即時分區存在時使用上一可用交易日。"""
        if latest != taipei_today():
            return latest
        try:
            row = self.execute_one(
                "SELECT max(date) FROM kline_enriched WHERE date < ?",
                [latest],
            )
            if row and row[0]:
                d = row[0]
                return d if isinstance(d, date) else date.fromisoformat(str(d))
        except Exception:
            pass
        return latest

    def _build_live_agg_from_parquet(self, latest: date, start_60d: date) -> tuple[pl.DataFrame, pl.DataFrame]:
        """降級路徑: 從 parquet 讀取資料並計算指標 (當 _enriched_history_cache 不可用時)。"""
        from app.indicators.pipeline import compute_indicators

        lf = (
            scan_enriched_parquet(self._enriched_glob)
            .filter(pl.col("date") >= start_60d)
            .filter(pl.col("date") <= latest)
            .sort(["symbol", "date"])
        )

        read_cols = [c for c in ["symbol", "date", "open", "high", "low", "close", "volume",
                                 "raw_close", "raw_high", "raw_low",
                                 "consecutive_limit_ups", "consecutive_limit_downs"]
                     if c in lf.collect_schema().names()]
        df_hist = lf.select(read_cols).collect()

        if df_hist.is_empty():
            return df_hist, pl.DataFrame()

        df_with_indicators = compute_indicators(df_hist)

        state_cols = [
            "symbol",
            "ema5", "ema10", "ema20", "ema30", "ema60",
            "macd_dea",
            "kdj_k", "kdj_d",
            "atr_14",
            "close", "high", "low",
            "annual_vol_20d",
        ]
        existing_state = [c for c in state_cols if c in df_with_indicators.columns]
        agg_a = _last_available_rows(
            df_with_indicators.select("date", *existing_state), latest,
        ).select(existing_state)

        return df_hist, agg_a

    def _refresh_etf_enriched(self) -> None:
        """從 ETF enriched parquet 載入最新日到記憶體快取。"""
        try:
            enriched_dir = self.store.data_dir / "kline_etf_enriched"
            dates = sorted(
                p.name[5:] for p in enriched_dir.glob("date=*")
                if p.is_dir() and p.name.startswith("date=")
            ) if enriched_dir.exists() else []
            if not dates:
                self._etf_enriched_cache = None
                self._etf_enriched_cache_date = None
                return
            latest = date.fromisoformat(dates[-1])
            target_parquet = enriched_dir / f"date={dates[-1]}" / "part.parquet"
            df_latest = pl.read_parquet(target_parquet)
            if df_latest.is_empty():
                return

            from datetime import timedelta

            from app.indicators.pipeline import compute_indicators, compute_signals
            start_full = latest - timedelta(days=300)
            read_cols = [c for c in ["symbol", "date", "open", "high", "low", "close",
                                     "volume", "amount", "raw_close", "raw_high", "raw_low"]
                         if c in df_latest.columns]
            df_hist = (
                scan_enriched_parquet(self._etf_enriched_glob,
                                cast_options=pl.ScanCastOptions(integer_cast="allow-float"))
                .filter(pl.col("date") >= start_full)
                .select(read_cols)
                .sort(["symbol", "date"])
                .collect()
            )
            if df_hist.is_empty():
                self._etf_enriched_cache = df_latest.sort(["symbol"])
            else:
                df_full = compute_signals(compute_indicators(df_hist))
                self._etf_enriched_cache = df_full.filter(pl.col("date") == latest).sort(["symbol"])
            self._etf_enriched_cache_date = latest
        except Exception as e:
            logger.debug("ETF enriched 快取重新整理跳過: %s", e)

    def _refresh_index_enriched(self) -> None:
        """從指數 enriched parquet 載入最新日到記憶體快取 (300天重算通用指標)。

        磁碟窄表無指標欄, 必須 scan 近 300 天重算, 否則監控訊號規則無列可評估。
        指數無復權需求, 不讀 raw_close/raw_high/raw_low。
        """
        try:
            enriched_dir = self.store.data_dir / "kline_index_enriched"
            dates = sorted(
                p.name[5:] for p in enriched_dir.glob("date=*")
                if p.is_dir() and p.name.startswith("date=")
            ) if enriched_dir.exists() else []
            if not dates:
                self._index_enriched_cache = None
                self._index_enriched_cache_date = None
                return
            latest = date.fromisoformat(dates[-1])
            target_parquet = enriched_dir / f"date={dates[-1]}" / "part.parquet"
            df_latest = pl.read_parquet(target_parquet)
            if df_latest.is_empty():
                return

            from datetime import timedelta
            start_full = latest - timedelta(days=300)
            read_cols = [c for c in ["symbol", "date", "open", "high", "low", "close",
                                     "volume", "amount"]
                         if c in df_latest.columns]
            df_hist = (
                scan_enriched_parquet(self._index_enriched_glob,
                                cast_options=pl.ScanCastOptions(integer_cast="allow-float"))
                .filter(pl.col("date") >= start_full)
                .select(read_cols)
                .sort(["symbol", "date"])
                .collect()
            )
            if df_hist.is_empty():
                self._index_enriched_cache = df_latest.sort(["symbol"])
            else:
                df_full = self._compute_index_enriched_range(df_hist)
                self._index_enriched_cache = df_full.filter(pl.col("date") == latest).sort(["symbol"])
            self._index_enriched_cache_date = latest
        except Exception as e:
            logger.debug("指數 enriched 快取重新整理跳過: %s", e)

    def _refresh_instruments(self) -> None:
        """載入 instruments 到記憶體。"""
        try:
            df = pl.scan_parquet(self._inst_glob).collect()
            if not df.is_empty():
                self._instruments_cache = df
                self._name_map_cache = None
                logger.info("instruments 快取已載入: %d 只", len(df))
        except Exception as e:
            logger.warning("instruments 快取重新整理失敗: %s", e)

    def _refresh_index_instruments(self) -> None:
        """載入指數 instruments 到記憶體。"""
        try:
            df = pl.scan_parquet(self._index_inst_glob).collect()
            if not df.is_empty():
                self._index_instruments_cache = df
                self._index_symbol_set_cache = None
                self._name_map_cache = None
                logger.info("index instruments 快取已載入: %d 只", len(df))
        except Exception as e:
            logger.debug("index instruments 快取重新整理跳過: %s", e)

    def _refresh_etf_instruments(self) -> None:
        """載入 ETF instruments 到記憶體；相容舊版 instruments_index 中的 ETF。"""
        parts: list[pl.DataFrame] = []
        try:
            df = pl.scan_parquet(self._etf_inst_glob).collect()
            if not df.is_empty():
                parts.append(df)
        except Exception as e:
            logger.debug("etf instruments 快取重新整理跳過(new): %s", e)
        try:
            legacy = self.get_index_instruments()
            if not legacy.is_empty() and "asset_type" in legacy.columns:
                legacy = legacy.filter(pl.col("asset_type") == "etf")
                if not legacy.is_empty():
                    parts.append(legacy)
        except Exception as e:
            logger.debug("etf instruments legacy fallback skipped: %s", e)
        if parts:
            df_all = pl.concat(parts, how="diagonal_relaxed").unique(subset=["symbol"], keep="last").sort("symbol")
            self._etf_instruments_cache = df_all
            self._etf_symbol_set_cache = None
            self._name_map_cache = None
            logger.info("ETF instruments 快取已載入: %d 只", len(df_all))

    def get_enriched_latest(self) -> tuple[pl.DataFrame, date | None]:
        """回傳快取的 enriched 最新日 DataFrame + 日期。如無快取則懶載入。

        背景預熱期間 (_enriched_warming=True) 回傳空表, 不觸發同步重算 ——
        否則首次存取會把非同步化想避免的 50s+ 計算拉回同步路徑。
        """
        if self._enriched_cache is None:
            if self._enriched_warming:
                return pl.DataFrame(), None
            self._refresh_enriched()
        if self._enriched_cache is None:
            return pl.DataFrame(), self._enriched_cache_date
        return self._enriched_cache, self._enriched_cache_date

    def get_enriched_latest_asset(self, asset_type: str, refresh: bool = True) -> tuple[pl.DataFrame, date | None]:
        """按資產類型回傳最新 enriched 快取。stock 保持舊快取語意。

        refresh=False: 快取冷時不觸發同步 _refresh_etf_enriched(300 天 scan+compute)。
        供行情輪詢執行緒使用 —— 避免在熱路徑上做重活阻塞股票行情/告警;快取由 ETF 即時
        flush 焐熱, 未焐熱(無 ETF 即時資料)時回傳空表, 本輪跳過 ETF 評估即可。
        """
        if asset_type == "stock":
            return self.get_enriched_latest()
        if asset_type == "etf":
            if self._etf_enriched_cache is None and refresh:
                self._refresh_etf_enriched()
            if self._etf_enriched_cache is None:
                return pl.DataFrame(), self._etf_enriched_cache_date
            return self._etf_enriched_cache, self._etf_enriched_cache_date
        if asset_type == "index":
            if self._index_enriched_cache is None and refresh:
                self._refresh_index_enriched()
            if self._index_enriched_cache is None:
                return pl.DataFrame(), self._index_enriched_cache_date
            return self._index_enriched_cache, self._index_enriched_cache_date
        return pl.DataFrame(), None


    def get_live_agg(self) -> pl.DataFrame:
        """回傳盤中即時指標預計算聚合表。如無快取則懶載入。

        live_agg 的核心欄 _prev_consec_up/down (昨日連續漲停天數) 取自基準日 enriched。
        基準日由 _live_agg_baseline_date 決定: 盤中(today 有即時分區) 取上一交易日,
        非盤中(磁碟最新日 < today) 取該最新日本身。一旦跨日, 期望基準日會前移,
        舊快取會把連續漲停天數整體少算一檔, 故這裡除首次懶載入外還要校驗基準日是否仍
        符合當前預期, 不符則重建 (無需等盤後管道刷快取)。

        性能: get_live_agg 被每輪即時行情呼叫 (expert 檔 1s 一次)。跨日只在
        date.today() 翻天時發生, 故先用 today 做廉價的 fast-path (μs 級),
        僅當 today 變化時才查磁碟確認 (DuckDB 掃 132 萬行約 100ms+) 並按需重建。
        """
        if self._live_agg_cache is None:
            if self._enriched_warming:
                # 背景預熱中: 回傳空表, 不觸發同步重算 (同 get_enriched_latest 守衛)
                return pl.DataFrame()
            self._refresh_enriched()
            self._live_agg_check_date = taipei_today()  # 剛建過, 當天不必再查磁碟
        else:
            today = taipei_today()
            if self._live_agg_check_date != today:
                # today 翻天了 (次日開盤首次輪詢): 校驗基準日是否需要前移重建。
                # 同一天內多次呼叫直接跳過, 避免每輪都掃 parquet。
                self._live_agg_check_date = today
                disk_latest = self._latest_enriched_date_duckdb()
                if disk_latest is not None:
                    expected = self._live_agg_baseline_date(disk_latest)
                    if self._live_agg_cache_date != expected:
                        logger.info(
                            "live_agg 跨日失效, 重建: 快取基準=%s, 期望基準=%s",
                            self._live_agg_cache_date, expected,
                        )
                        self._refresh_enriched()
        if self._live_agg_cache is None:
            return pl.DataFrame()
        return self._live_agg_cache

    def get_instruments(self) -> pl.DataFrame:
        """回傳快取的 instruments DataFrame。如無快取則懶載入。"""
        if self._instruments_cache is None:
            self._refresh_instruments()
        if self._instruments_cache is None:
            return pl.DataFrame()
        return self._instruments_cache

    def get_index_instruments(self) -> pl.DataFrame:
        """回傳快取的指數 instruments DataFrame。如無快取則懶載入。"""
        if self._index_instruments_cache is None:
            self._refresh_index_instruments()
        if self._index_instruments_cache is None:
            return pl.DataFrame()
        return self._index_instruments_cache

    def get_etf_instruments(self) -> pl.DataFrame:
        """回傳快取的 ETF instruments DataFrame；相容舊版 instruments_index 中的 ETF。"""
        if self._etf_instruments_cache is None:
            self._refresh_etf_instruments()
        if self._etf_instruments_cache is None:
            return pl.DataFrame()
        return self._etf_instruments_cache

    def get_instruments_asset(self, asset_type: str) -> pl.DataFrame:
        """按資產類型回傳 instruments；老 stock 路徑保持原樣。"""
        if asset_type == "stock":
            return self.get_instruments()
        if asset_type == "index":
            df = self.get_index_instruments()
            if not df.is_empty() and "asset_type" in df.columns:
                return df.filter(pl.col("asset_type") != "etf")
            return df
        if asset_type == "etf":
            return self.get_etf_instruments()
        return pl.DataFrame()

    def get_index_symbol_set(self) -> set[str]:
        """回傳已快取指數 symbol 集合 (memo, 隨 instruments 快取失效)。"""
        if self._index_symbol_set_cache is None:
            df = self.get_index_instruments()
            if df.is_empty() or "symbol" not in df.columns:
                return set()
            self._index_symbol_set_cache = set(df["symbol"].cast(pl.Utf8).to_list())
        return self._index_symbol_set_cache

    def get_etf_symbol_set(self) -> set[str]:
        """回傳已快取 ETF symbol 集合 (memo, 隨 instruments 快取失效)。"""
        if self._etf_symbol_set_cache is None:
            df = self.get_etf_instruments()
            if df.is_empty() or "symbol" not in df.columns:
                return set()
            self._etf_symbol_set_cache = set(df["symbol"].cast(pl.Utf8).to_list())
        return self._etf_symbol_set_cache

    def resolve_asset_type(self, symbol: str) -> str:
        """按 symbol 判定資產類型: etf / index / stock(預設)。

        供 API 層對單標的查詢做資產分流 (get_daily_asset 等)。
        ETF/指數集合為 memo, 每請求查詢成本可忽略。
        """
        if symbol in self.get_etf_symbol_set():
            return "etf"
        if symbol in self.get_index_symbol_set():
            return "index"
        return "stock"

    def get_name_map(self, symbols: list[str] | None = None) -> dict[str, str]:
        """回傳 {symbol: name} 對應, 合併股票 + ETF + 指數 instruments (股票優先去重)。

        自選列表/名稱批查等場景的統一名稱解析入口, 避免各呼叫方自行合併兩份快取。
        symbols 非 None 時只回傳命中的條目。
        全量結果快取在 _name_map_cache (隨三份 instruments 維表重新整理失效),
        避免每請求對 ~7000 行維表做 iter_rows 重建。
        """
        if self._name_map_cache is not None:
            if symbols is None:
                return dict(self._name_map_cache)
            wanted = set(symbols)
            return {s: n for s, n in self._name_map_cache.items() if s in wanted}
        # 只構建並快取全量對應; symbols 過濾只作用於回傳值。
        # 若把過濾後的結果寫入快取, 後續不同 symbols 的查詢會命中殘缺快取,
        # 導致新加入自選的標的查不到名稱。
        name_map: dict[str, str] = {}
        for df in (self.get_instruments(), self.get_etf_instruments(), self.get_instruments_asset("index")):
            if df.is_empty() or "symbol" not in df.columns or "name" not in df.columns:
                continue
            for symbol, name in df.select(["symbol", "name"]).iter_rows():
                name_map.setdefault(symbol, name)
        self._name_map_cache = name_map
        if symbols is None:
            return dict(name_map)
        wanted = set(symbols)
        return {s: n for s, n in name_map.items() if s in wanted}


    # ================================================================
    # 熱路徑: Polars 查詢 (Chart / Screener / Signals / Intraday)
    # ================================================================

    def get_daily(
        self,
        symbol: str,
        start: date,
        end: date,
        columns: list[str] | None = None,
    ) -> pl.DataFrame:
        """單股日K查詢 — 從14欄parquet讀取後即時計算指標。"""
        from datetime import timedelta

        # 快路徑: 請求的欄全是 parquet 直接儲存的列 (如迷你蠟燭圖只要 OHLCV) →
        # scan + 欄下推直接回傳, 跳過 warmup(150天) 與 _compute_enriched_range 全套指標計算。
        # 仍用 enriched_latest 快取覆蓋最新日 (盤中更準), 只保留請求欄。
        # 試探 scan 僅讀請求的欄; 缺欄時回退到下方完整計算路徑 (代價僅一次輕量 scan)。
        if columns:
            df = self._scan_daily_symbol(symbol, start, end, columns)
            if not df.is_empty() and all(c in df.columns for c in columns):
                cached, cache_date = self.get_enriched_latest()
                if cached is not None and not cached.is_empty() and cache_date and start <= cache_date <= end:
                    cached_part = self._filter_cached(cached, symbol, columns)
                    if not cached_part.is_empty():
                        df = df.filter(pl.col("date") != cache_date)
                        common_cols = [c for c in df.columns if c in cached_part.columns]
                        df = pl.concat([df.select(common_cols), cached_part.select(common_cols)])
                return df

        # 擴展範圍用於指標預熱 (MA60 需要 ~60 交易日 ≈ 120 日曆日)
        warmup_start = start - timedelta(days=150)

        # 優先重用預計算 enriched 歷史快取 (300 天全指標, 與回測引擎同源):
        # 個股對話框打開時本介面每個行情 tick 被調一次, 逐請求 150 天掃描 + 全套
        # 指標重算是熱路徑上最大的重複計算。快取最新日可能不含當日即時行,
        # 由下方 get_enriched_latest 覆蓋邏輯補齊; 覆蓋不足時回退單股計算路徑。
        df = pl.DataFrame()
        hist = self._enriched_history_cache
        if hist is not None and not hist.is_empty() and "date" in hist.columns:
            hist_min = self._enriched_history_start
            hist_max = hist["date"].max()
            if hist_min is not None and hist_min <= start and hist_max >= start:
                df = hist.filter(
                    (pl.col("symbol") == symbol)
                    & (pl.col("date") >= start)
                    & (pl.col("date") <= end)
                )
        if df.is_empty():
            # 掃描14欄 parquet
            df = self._scan_daily_symbol(symbol, warmup_start, end, None)
            if not df.is_empty():
                df = self._compute_enriched_range(df)

        # 嘗試用快取資料覆蓋最新日 (盤中更準確)
        cached, cache_date = self.get_enriched_latest()
        if (
            not df.is_empty()
            and cached is not None
            and not cached.is_empty()
            and cache_date
            and start <= cache_date <= end
        ):
            cached_part = self._filter_cached(cached, symbol, None)
            if not cached_part.is_empty():
                df = df.filter(pl.col("date") != cache_date)
                common_cols = [c for c in df.columns if c in cached_part.columns]
                df = pl.concat([df.select(common_cols), cached_part.select(common_cols)])

        # 裁剪到請求範圍
        if not df.is_empty():
            df = df.filter((pl.col("date") >= start) & (pl.col("date") <= end))

        if columns and not df.is_empty():
            existing = [c for c in columns if c in df.columns]
            df = df.select(existing)

        return df

    def get_daily_batch(
        self,
        symbols: list[str],
        start: date,
        end: date,
        columns: list[str] | None = None,
    ) -> pl.DataFrame:
        """批次日K查詢。"""
        cached, cache_date = self.get_enriched_latest()
        if cached is not None and not cached.is_empty() and cache_date and start >= cache_date:
            return self._filter_cached_batch(cached, symbols, columns)

        # 回退 scan_parquet
        return self._scan_daily_batch(symbols, start, end, columns)

    def get_index_daily(
        self,
        symbol: str,
        start: date,
        end: date,
        columns: list[str] | None = None,
    ) -> pl.DataFrame:
        """指數日K查詢 — 從獨立指數 enriched parquet 讀取後即時計算通用指標。"""
        from datetime import timedelta

        # 快路徑: 若請求的欄全部是 parquet 直接儲存的列 (如迷你蠟燭圖只要 OHLCV),
        # 直接 scan + 欄下推回傳, 跳過 warmup(150天) 與 _compute_index_enriched_range 全套指標計算。
        # 試探 scan 僅讀請求的欄, 缺欄時回退到下方完整計算路徑 (代價僅一次輕量 scan)。
        if columns:
            df = self._scan_index_daily_symbol(symbol, start, end, columns)
            if not df.is_empty() and all(c in df.columns for c in columns):
                return df

        warmup_start = start - timedelta(days=150)
        df = self._scan_index_daily_symbol(symbol, warmup_start, end, None)
        if not df.is_empty():
            df = self._compute_index_enriched_range(df)
            df = df.filter((pl.col("date") >= start) & (pl.col("date") <= end))
        if columns and not df.is_empty():
            existing = [c for c in columns if c in df.columns]
            df = df.select(existing)
        return df

    def get_etf_daily(
        self,
        symbol: str,
        start: date,
        end: date,
        columns: list[str] | None = None,
    ) -> pl.DataFrame:
        """ETF 日K查詢 — 優先讀獨立 ETF enriched，相容舊版 index enriched 中的 ETF。"""
        from datetime import timedelta

        # 快路徑: 獨立 ETF 資料 + 請求欄全是 parquet 儲存欄 → 直接 scan 欄下推, 跳過 warmup + compute。
        # 無獨立 ETF 資料 (舊版存入 index enriched) 時 df 為空, 自然回退到下方完整路徑。
        if columns:
            df = self._scan_etf_daily_symbol(symbol, start, end, columns)
            if not df.is_empty() and all(c in df.columns for c in columns):
                return df

        warmup_start = start - timedelta(days=150)
        df = self._scan_etf_daily_symbol(symbol, warmup_start, end, None)
        if df.is_empty():
            # 舊版 ETF 曾存入 kline_index_enriched；沒有獨立資料時回退讀取。
            df = self._scan_index_daily_symbol(symbol, warmup_start, end, None)
        if not df.is_empty():
            df = self._compute_index_enriched_range(df)
            df = df.filter((pl.col("date") >= start) & (pl.col("date") <= end))
        if columns and not df.is_empty():
            existing = [c for c in columns if c in df.columns]
            df = df.select(existing)
        return df

    def get_daily_asset(
        self,
        asset_type: str,
        symbol: str,
        start: date,
        end: date,
        columns: list[str] | None = None,
    ) -> pl.DataFrame:
        if asset_type == "stock":
            return self.get_daily(symbol, start, end, columns)
        if asset_type == "index":
            return self.get_index_daily(symbol, start, end, columns)
        if asset_type == "etf":
            return self.get_etf_daily(symbol, start, end, columns)
        return pl.DataFrame()

    def _minute_glob_for(self, asset_type: str) -> str:
        """按資產類型選擇分鐘K parquet glob。ETF 分鐘資料獨立儲存於 kline_etf_minute。"""
        return self._etf_minute_glob if asset_type == "etf" else self._minute_glob

    def get_minute(
        self,
        symbol: str,
        trade_date: date,
        asset_type: str = "stock",
    ) -> pl.DataFrame:
        """分鐘K查詢 — Polars scan_parquet + predicate pushdown。"""
        try:
            return pl.scan_parquet(self._minute_glob_for(asset_type)).filter(
                (pl.col("symbol") == symbol)
                & (pl.col("datetime").dt.date() == trade_date)
            ).sort("datetime").collect()
        except Exception as e:
            logger.warning("分鐘K查詢失敗: %s", e)
            return pl.DataFrame()

    def get_minute_batch(
        self,
        symbols: list[str],
        trade_date: date,
        asset_type: str = "stock",
    ) -> pl.DataFrame:
        """批次分鐘K查詢 — 多 symbol 一次 scan_parquet。

        用於自選列表分時圖: 一次 predicate pushdown 讀多隻股票當日分鐘K,
        避免逐只查詢的 N 次 I/O。
        """
        if not symbols:
            return pl.DataFrame()
        try:
            return pl.scan_parquet(self._minute_glob_for(asset_type)).filter(
                pl.col("symbol").is_in(symbols)
                & (pl.col("datetime").dt.date() == trade_date)
            ).sort(["symbol", "datetime"]).collect()
        except Exception as e:
            logger.warning("批次分鐘K查詢失敗: %s", e)
            return pl.DataFrame()

    # ================================================================
    # Polars 查詢內部方法
    # ================================================================

    def _compute_enriched_range(self, df: pl.DataFrame) -> pl.DataFrame:
        """對14欄enriched資料即時計算完整指標+訊號。輸入應含足夠預熱列數。"""
        from app.indicators.pipeline import (
            compute_indicators,
            compute_limit_signals,
            compute_signals,
            filter_halt_days,
        )
        if df.is_empty() or df.height < 2:
            return df
        # 保底過濾歷史髒資料中的停牌日 (close 可能被填充為前收盤價)
        df = filter_halt_days(df)
        if df.is_empty() or df.height < 2:
            return df
        try:
            df = compute_indicators(df)
            df = compute_signals(df)
            instruments = self.get_instruments()
            df = compute_limit_signals(df, instruments)
        except Exception as e:
            logger.warning("on-demand compute failed: %s", e)
        return df

    def _compute_index_enriched_range(self, df: pl.DataFrame) -> pl.DataFrame:
        """指數只計算通用技術指標和通用訊號，跳過漲跌停/股本/市值邏輯。"""
        from app.indicators.pipeline import compute_indicators, compute_signals
        if df.is_empty() or df.height < 2:
            return df
        try:
            df = compute_indicators(df)
            df = compute_signals(df)
        except Exception as e:
            logger.warning("index on-demand compute failed: %s", e)
        return df

    def _filter_cached(self, cached: pl.DataFrame, symbol: str, columns: list[str] | None) -> pl.DataFrame:
        df = cached.filter(pl.col("symbol") == symbol)
        if columns and not df.is_empty():
            existing = [c for c in columns if c in df.columns]
            df = df.select(existing)
        return df

    def _filter_cached_batch(self, cached: pl.DataFrame, symbols: list[str], columns: list[str] | None) -> pl.DataFrame:
        df = cached.filter(pl.col("symbol").is_in(symbols))
        if columns and not df.is_empty():
            existing = [c for c in columns if c in df.columns]
            df = df.select(existing)
        return df.sort(["symbol", "date"])

    def _scan_daily_symbol(self, symbol: str, start: date, end: date, columns: list[str] | None) -> pl.DataFrame:
        try:
            lf = scan_enriched_parquet(self._enriched_glob,
                                 cast_options=pl.ScanCastOptions(integer_cast="allow-float")).filter(
                (pl.col("symbol") == symbol)
                & (pl.col("date") >= start)
                & (pl.col("date") <= end)
            ).sort("date")
            if columns:
                schema_names = lf.collect_schema().names()
                existing = [c for c in columns if c in schema_names]
                lf = lf.select(existing)
            return lf.collect()
        except Exception as e:
            logger.warning("日K查詢失敗: %s", e)
            return pl.DataFrame()

    def _scan_daily_batch(self, symbols: list[str], start: date, end: date, columns: list[str] | None) -> pl.DataFrame:
        try:
            lf = scan_enriched_parquet(self._enriched_glob,
                                 cast_options=pl.ScanCastOptions(integer_cast="allow-float")).filter(
                (pl.col("symbol").is_in(symbols))
                & (pl.col("date") >= start)
                & (pl.col("date") <= end)
            ).sort(["symbol", "date"])
            if columns:
                schema_names = lf.collect_schema().names()
                existing = [c for c in columns if c in schema_names]
                lf = lf.select(existing)
            return lf.collect()
        except Exception as e:
            logger.warning("日K批次查詢失敗: %s", e)
            return pl.DataFrame()

    def _scan_index_daily_symbol(self, symbol: str, start: date, end: date, columns: list[str] | None) -> pl.DataFrame:
        try:
            lf = scan_enriched_parquet(self._index_enriched_glob,
                                 cast_options=pl.ScanCastOptions(integer_cast="allow-float")).filter(
                (pl.col("symbol") == symbol)
                & (pl.col("date") >= start)
                & (pl.col("date") <= end)
            ).sort("date")
            if columns:
                schema_names = lf.collect_schema().names()
                existing = [c for c in columns if c in schema_names]
                lf = lf.select(existing)
            return lf.collect()
        except Exception as e:
            logger.warning("指數日K查詢失敗: %s", e)
            return pl.DataFrame()

    def _scan_etf_daily_symbol(self, symbol: str, start: date, end: date, columns: list[str] | None) -> pl.DataFrame:
        try:
            lf = scan_enriched_parquet(self._etf_enriched_glob,
                                 cast_options=pl.ScanCastOptions(integer_cast="allow-float")).filter(
                (pl.col("symbol") == symbol)
                & (pl.col("date") >= start)
                & (pl.col("date") <= end)
            ).sort("date")
            if columns:
                schema_names = lf.collect_schema().names()
                existing = [c for c in columns if c in schema_names]
                lf = lf.select(existing)
            return lf.collect()
        except Exception as e:
            logger.debug("ETF 日K查詢跳過: %s", e)
            return pl.DataFrame()


    # ================================================================
    # DuckDB 查詢 (冷路徑: 統計/元資料/自訂SQL)
    # ================================================================

    def latest_minute_date(self, symbol: str, asset_type: str = "stock") -> date | None:
        table = "kline_etf_minute" if asset_type == "etf" else "kline_minute"
        try:
            with self._lock:
                row = self.db.execute(
                    f"SELECT max(CAST(datetime AS DATE)) FROM {table} WHERE symbol = ?",
                    [symbol],
                ).fetchone()
            if row and row[0]:
                return row[0] if isinstance(row[0], date) else date.fromisoformat(str(row[0]))
        except duckdb.CatalogException:
            pass
        return None

    def latest_minute_date_global(self) -> date | None:
        """全市場最近分鐘K日期 (不分 symbol)。用於非交易日回退到上一交易日。"""
        try:
            with self._lock:
                row = self.db.execute(
                    "SELECT max(CAST(datetime AS DATE)) FROM kline_minute",
                ).fetchone()
            if row and row[0]:
                return row[0] if isinstance(row[0], date) else date.fromisoformat(str(row[0]))
        except Exception:
            return None


    def latest_daily_date(self) -> date | None:
        """本地日K資料的最新日期。"""
        try:
            with self._lock:
                res = self.db.execute(
                    "SELECT max(date) FROM kline_daily",
                ).fetchone()
            if res and res[0]:
                d = res[0]
                return d if isinstance(d, date) else date.fromisoformat(str(d))
        except Exception:
            return None
        return None


    def get_matrix_data_generation(self, asset_type: str = "stock") -> str:
        """Return the stable generation for managed enriched readers."""
        return get_enriched_generation(self.store.data_dir, asset_type)


    def _latest_enriched_date_duckdb(self) -> date | None:
        try:
            with self._lock:
                res = self.db.execute(
                    "SELECT max(date) FROM kline_enriched",
                ).fetchone()
            if res and res[0]:
                d = res[0]
                return d if isinstance(d, date) else date.fromisoformat(str(d))
        except Exception:
            return None
        return None

    # ================================================================
    # 寫入 (Pipeline / Sync)
    # ================================================================

    @staticmethod
    def _atomic_write_parquet(df: pl.DataFrame, out: Path) -> None:
        """先寫臨時檔案再原子替換, 避免行程中斷留下損壞的 parquet。

        直接 write_parquet(out) 在行程被 kill (dev.sh 清連接埠用 kill -9)
        或斷電時會留下半截檔案, 之後 scan_parquet glob 整條鏈路出錯。
        臨時檔案後綴 .tmp 不匹配 *.parquet glob, 不會被掃描誤讀。
        """
        tmp = out.with_name(out.name + ".tmp")
        df.write_parquet(tmp)
        tmp.replace(out)  # 同目錄 rename, POSIX/NTFS 均為原子操作

    def _with_instrument_metadata(self, asset_type: str, df: pl.DataFrame) -> pl.DataFrame:
        """補齊即時記憶體快取所需的維表欄位；這些欄位不會寫入 enriched 分區。"""
        if asset_type not in {"stock", "etf"} or df.is_empty():
            return df
        instruments = self.get_instruments_asset(asset_type)
        if instruments.is_empty() or "symbol" not in instruments.columns:
            return df
        metadata_cols = [
            c for c in ("name", "total_shares", "float_shares")
            if c in instruments.columns and c not in df.columns
        ]
        if not metadata_cols:
            return df
        metadata = instruments.select(["symbol", *metadata_cols]).unique(
            subset=["symbol"], keep="last",
        )
        return df.join(metadata, on="symbol", how="left")

    def merge_live_enriched_asset(self, asset_type: str, df: pl.DataFrame) -> None:
        """按 symbol 合併當天 enriched 分區和記憶體快取。用於少量自選即時。"""
        if df.is_empty() or "date" not in df.columns:
            return
        dt = df["date"][0]
        if asset_type == "stock":
            table = "kline_daily_enriched"
            existing_cache = self._enriched_cache if self._enriched_cache_date == dt else pl.DataFrame()
        elif asset_type == "etf":
            table = "kline_etf_enriched"
            existing_cache = self._etf_enriched_cache if self._etf_enriched_cache_date == dt else pl.DataFrame()
        elif asset_type == "index":
            table = "kline_index_enriched"
            existing_cache = self._index_enriched_cache if self._index_enriched_cache_date == dt else pl.DataFrame()
        else:
            return

        cache_df = self._with_instrument_metadata(asset_type, df)
        merged_cache = cache_df
        if existing_cache is not None and not existing_cache.is_empty():
            merged_cache = pl.concat([existing_cache, cache_df], how="diagonal_relaxed").unique(
                subset=["symbol", "date"], keep="last"
            )
        merged_cache = merged_cache.sort(["symbol"])

        from app.indicators.pipeline import ENRICHED_STORAGE_COLS
        storage_cols = [c for c in ENRICHED_STORAGE_COLS if c in df.columns]
        df_storage = df.select(storage_cols).sort(["symbol"])
        base = self.store.data_dir / table
        ds = dt.isoformat() if hasattr(dt, "isoformat") else str(dt)
        out = base / f"date={ds}" / "part.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        publication = (
            EnrichedPublication(self.store.data_dir, asset_type, recover=True)
            if asset_type in {"stock", "etf"}
            else None
        )
        with self._write_lock:
            existing = pl.DataFrame()
            if out.exists():
                existing = pl.read_parquet(out)
                df_storage = pl.concat([existing, df_storage], how="diagonal_relaxed").unique(
                    subset=["symbol", "date"], keep="last"
                )
            df_storage = df_storage.sort(["symbol"])
            if existing.is_empty() or not existing.equals(df_storage):
                if publication is None:
                    self._atomic_write_parquet(df_storage, out)
                else:
                    publication.write_parquet(df_storage, out)
            if publication is not None:
                publication.commit()

        if asset_type == "stock":
            self._enriched_cache = merged_cache
            self._enriched_cache_date = dt
        elif asset_type == "etf":
            self._etf_enriched_cache = merged_cache
            self._etf_enriched_cache_date = dt
        elif asset_type == "index":
            self._index_enriched_cache = merged_cache
            self._index_enriched_cache_date = dt

    def flush_live_daily(self, df: pl.DataFrame) -> None:
        """覆寫當天 kline_daily 分區 (即時行情寫入磁碟, 非merge)。"""
        if df.is_empty() or "date" not in df.columns:
            return
        self.flush_live_daily_asset("stock", df)

    def flush_live_daily_asset(self, asset_type: str, df: pl.DataFrame) -> None:
        """覆寫當天指定資產日K分區 (即時行情寫入磁碟, 非merge)。"""
        if df.is_empty() or "date" not in df.columns:
            return
        table = {
            "stock": "kline_daily",
            "index": "kline_index_daily",
            "etf": "kline_etf_daily",
        }.get(asset_type)
        if not table:
            return
        base = self.store.data_dir / table
        dt = df["date"][0]
        ds = dt.isoformat() if hasattr(dt, "isoformat") else str(dt)
        out = base / f"date={ds}" / "part.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        with self._write_lock:
            self._atomic_write_parquet(df.sort(["symbol", "date"]), out)


    def flush_live_enriched_asset(self, asset_type: str, df: pl.DataFrame) -> None:
        """覆寫當天指定資產 enriched 分區 (即時 enriched 寫入磁碟, 非merge)。"""
        if df.is_empty() or "date" not in df.columns:
            return
        dt = df["date"][0]
        cache_df = self._with_instrument_metadata(asset_type, df).sort(["symbol"])
        if asset_type == "stock":
            table = "kline_daily_enriched"
        elif asset_type == "etf":
            table = "kline_etf_enriched"
        elif asset_type == "index":
            table = "kline_index_enriched"
        else:
            return

        from app.indicators.pipeline import ENRICHED_STORAGE_COLS
        storage_cols = [c for c in ENRICHED_STORAGE_COLS if c in df.columns]
        df_storage = df.select(storage_cols).sort(["symbol"])
        base = self.store.data_dir / table
        ds = dt.isoformat() if hasattr(dt, "isoformat") else str(dt)
        out = base / f"date={ds}" / "part.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        publication = (
            EnrichedPublication(self.store.data_dir, asset_type, recover=True)
            if asset_type in {"stock", "etf"}
            else None
        )
        with self._write_lock:
            existing = pl.read_parquet(out) if out.exists() else pl.DataFrame()
            if existing.is_empty() or not existing.equals(df_storage):
                if publication is None:
                    self._atomic_write_parquet(df_storage, out)
                else:
                    publication.write_parquet(df_storage, out)
            if publication is not None:
                publication.commit()

        if asset_type == "stock":
            self._enriched_cache = cache_df
            self._enriched_cache_date = dt
        elif asset_type == "etf":
            self._etf_enriched_cache = cache_df
            self._etf_enriched_cache_date = dt
        elif asset_type == "index":
            self._index_enriched_cache = cache_df
            self._index_enriched_cache_date = dt

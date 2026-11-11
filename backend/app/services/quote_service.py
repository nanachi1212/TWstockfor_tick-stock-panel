"""全域即時行情服務。

集中管理全市場行情拉取 + enriched 快取，供盤中選股、自選股等所有模組複用。

架構:
  - 台股官方模式輪詢監控規則; 自訂來源依 realtime 能力取數
  - 拉取行情 → 寫 kline_daily (不復權) + 增量計算 enriched → 寫盤 + 更新快取
  - _enriched_cache 是唯一的盤中資料來源 (OHLCV + 全套技術指標)
  - _live_agg_cache 是遞推狀態 (只載入一次, 盤中不變)

資料流 (每輪 ~15s):
  1. API 拉取 → raw_records (臨時變數)
  2. raw_records → 寫 kline_daily (不復權原始價格)
  3. raw_records → 更新 _enriched_cache 的 OHLCV
  4. 增量計算 enriched 指標 (~50ms)
  5. 寫 kline_daily_enriched + 替換 _enriched_cache
  6. 通知 SSE

生命周期:
  - 服務啟動時讀取 preferences，若 enabled 則自動啟動執行緒
  - 執行中可通過 API 切換開關
  - 關閉時停止執行緒
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date

import polars as pl

from app.market_time import cn_today
from app.parquet import scan_daily_parquet
from app.taiwan.realtime.calendar import get_market_status, taipei_now
from app.taiwan.realtime.models import MarketStatus

logger = logging.getLogger(__name__)

# 外部推播專用執行緒池 —— 與行情輪詢執行緒隔離。
# 若在 _poll_loop 上同步投遞, API 慢/宕機會逐條累加,拖垮整條即時行情+告警輪詢。
# 這裡 fire-and-forget,
# 失敗由 webhook_adapter 記 WARNING(可見), 但絕不阻塞熱路徑。
_WEBHOOK_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="notification-push")
_WEBHOOK_DISPATCH_LOCK = threading.Lock()
_WEBHOOK_DISPATCHED: set[str] = set()


def _claim_external_delivery(event_id: str, channel: str) -> bool:
    dispatch_id = f"{event_id}:{channel}"
    with _WEBHOOK_DISPATCH_LOCK:
        if dispatch_id in _WEBHOOK_DISPATCHED:
            return False
        if len(_WEBHOOK_DISPATCHED) >= 20_000:
            _WEBHOOK_DISPATCHED.clear()
        _WEBHOOK_DISPATCHED.add(dispatch_id)
    return True


class QuoteSubscriber:
    """一個 SSE 連接對應一個訂閱者: 獨立事件 + 獨立佇列。

    此前四個通道共用服務級 Event + pending 列表, pop 是「取走」語義:
    多客戶端 (多標籤頁/多設備) 時告警只會被先醒來的連接消費, 其餘永遠
    收不到; 共享 Event 的 clear/wait 也存在互相吞信號的競態。
    改為每連接獨立訂閱者後, 事件對所有客戶端廣播。
    """

    def __init__(self, max_alerts: int = 1000, max_reviews: int = 200) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._max_alerts = max_alerts
        self._max_reviews = max_reviews
        self._quote_updated = False
        self._strategy_results_updated = False
        self._depth_updated = False
        self._daily_data_updated = False
        self._alerts: list[dict] = []
        self._reviews: list[str] = []

    # ── 消費側 (SSE generator 執行緒) ──────────────────────
    def wait(self, timeout: float = 5.0) -> bool:
        """阻塞等待任一通道有新信號。"""
        return self._event.wait(timeout=timeout)

    def pop(self) -> dict:
        """原子取走全部待推送內容並復位事件。"""
        with self._lock:
            out = {
                "quote_updated": self._quote_updated,
                "strategy_results_updated": self._strategy_results_updated,
                "depth_updated": self._depth_updated,
                "daily_data_updated": self._daily_data_updated,
                "alerts": self._alerts,
                "reviews": self._reviews,
            }
            self._quote_updated = False
            self._strategy_results_updated = False
            self._depth_updated = False
            self._daily_data_updated = False
            self._alerts = []
            self._reviews = []
            self._event.clear()
            return out

    # ── 生產側 (行情輪詢 / depth / 覆盤執行緒) ─────────────
    def push_alerts(self, alerts: list[dict]) -> None:
        with self._lock:
            self._alerts.extend(alerts)
            if len(self._alerts) > self._max_alerts:  # 背壓: 丟棄最舊
                self._alerts = self._alerts[-self._max_alerts:]
            self._event.set()

    def push_review(self, event_json: str) -> None:
        with self._lock:
            self._reviews.append(event_json)
            if len(self._reviews) > self._max_reviews:
                self._reviews = self._reviews[-self._max_reviews:]
            self._event.set()

    def clear_alerts(self) -> None:
        with self._lock:
            self._alerts = []
            if (
                not self._quote_updated
                and not self._strategy_results_updated
                and not self._depth_updated
                and not self._daily_data_updated
                and not self._reviews
            ):
                self._event.clear()

    def notify_quote(self) -> None:
        with self._lock:
            self._quote_updated = True
            self._event.set()

    def notify_strategy_results(self) -> None:
        with self._lock:
            self._strategy_results_updated = True
            self._event.set()

    def notify_depth(self) -> None:
        with self._lock:
            self._depth_updated = True
            self._event.set()

    def notify_daily_data(self) -> None:
        with self._lock:
            self._daily_data_updated = True
            self._event.set()


# 落盤節流間隔: last_fetch_ms 僅在進程重啟後用於顯示"最後獲取時間"(執行中讀記憶體值),
# 每 30s 持久化一次足夠, 避免 expert 檔每秒一輪的全量 preferences 重寫磁盤。
_LAST_FETCH_WRITE_INTERVAL_MS = 30_000.0
_last_fetch_written_at_ms: float = 0.0


def _persist_last_fetch(fetched_at_ms: float) -> None:
    """把"最後獲取"時間戳持久化到 preferences, 使進程重啟後仍可顯示。

    放在鎖外呼叫 (IO); 失敗不影響主流程 (記憶體值已更新, 下次 fetch 再寫)。
    距上次成功落盤不足 30s 時跳過 (節流只影響落盤頻率, 記憶體值不受影響)。
    """
    global _last_fetch_written_at_ms
    if (fetched_at_ms - _last_fetch_written_at_ms) < _LAST_FETCH_WRITE_INTERVAL_MS:
        return
    try:
        from app.services import preferences
        preferences.save({"last_fetch_ms": round(fetched_at_ms, 0)})
        _last_fetch_written_at_ms = fetched_at_ms
    except Exception as e:  # noqa: BLE001
        logger.debug("last_fetch_ms 持久化失敗 (不影響行情): %s", e)



class QuoteService:
    """全域即時行情服務 — 單例。"""

    CORE_INDEX_SYMBOLS: tuple[str, ...] = ()

    DEFAULT_INTERVAL = 6.0
    MAX_INTERVAL = 60.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # 串行化行情拉取: 手動 POST /refresh 與後台輪詢執行緒可能併發呼叫
        # _fetch_quotes, 兩者同時寫同一批 parquet/快取會互相覆蓋
        self._fetch_lock = threading.Lock()
        self._running = False
        self._enabled = False      # 全域開關 (持久化到 preferences)
        # 暫停態: 盤後管道/資料修正執行期間臨時暫停取數, 防止與管道寫同一批 parquet 競態。
        # 與 _enabled 不同 — pause 不改 preferences、不 stop 執行緒, 僅讓輪詢循環跳過取數;
        # 進程重啟後 _paused 歸零, 從 preferences 恢復真實開關態, 無"假關閉"副作用。
        self._paused = False
        self._interval = self.DEFAULT_INTERVAL
        self._thread: threading.Thread | None = None
        self._repo = None          # 延遲注入, 避免循環匯入
        # SSE 訂閱者集合: 每個 /stream 連接一個 QuoteSubscriber, 事件廣播到所有訂閱者
        self._subscribers: set[QuoteSubscriber] = set()
        self._app_state = None                   # 延遲注入 (FastAPI app.state)

        # 拉取元資訊 (給 SSE / status 用)
        self._fetch_time: float = 0.0       # perf_counter (用於計算 quote_age_ms)
        self._fetch_ms: float = 0.0         # 拉取耗時 (毫秒)
        # _fetched_at 持久化到 preferences: 進程重啟後仍能顯示"最後獲取"時間,
        # 不因關閉開關/重啟而歸零 (資料頁卡片常駐顯示, 方便判斷上次拉取時刻)。
        try:
            from app.services import preferences as _prefs
            self._fetched_at: float = float(_prefs.load().get("last_fetch_ms", 0.0))
        except Exception:  # noqa: BLE001
            self._fetched_at = 0.0      # 拉取完成的 Unix 時間戳 (毫秒)
        self._symbol_count: int = 0
        self._index_symbol_count: int = 0
        self._etf_symbol_count: int = 0
        self._index_quotes_cache: pl.DataFrame | None = None

    # ================================================================
    # 生命周期
    # ================================================================

    def start(self, interval: float = 0.0) -> None:
        """啟動後台行情輪詢執行緒。"""
        if self._running:
            return
        if interval <= 0:
            from app.services import preferences
            interval = preferences.get_realtime_quote_interval()
        self._interval = self._clamp_interval(interval)
        self._running = True
        self._enabled = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        self._save_enabled(True)
        logger.info("行情服務已啟動, 輪詢間隔 %.1fs", self._interval)

    def stop(self) -> None:
        """停止後台行情輪詢執行緒。"""
        self._running = False
        self._enabled = False
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None
        self._save_enabled(False)
        logger.info("行情服務已停止")

    def enable(self) -> bool:
        """開啟自動行情 (不立即啟動執行緒，等下一個交易時段)。

        none 檔無即時行情權限,拒絕開啟並返回 False;
        free 檔開啟自選股即時,starter+ 開啟全市場即時。返回值表示是否真正開啟。
        """
        if not self.is_realtime_allowed():
            logger.warning("即時行情開啟被拒:當前檔位(none)無即時行情權限")
            return False
        self._enabled = True
        self._save_enabled(True)
        if not self._running:
            from app.services import preferences
            self._interval = self._clamp_interval(preferences.get_realtime_quote_interval())
            self._running = True
            self._thread = threading.Thread(target=self._poll_loop, daemon=True)
            self._thread.start()
        logger.info("行情服務已啟用, 輪詢間隔 %.1fs", self._interval)
        return True

    def disable(self) -> None:
        """關閉自動行情。"""
        self.stop()
        logger.info("行情服務已關閉")

    # ================================================================
    # 臨時暫停 (盤後管道/資料修正期間, 防止寫盤競態)
    # ================================================================

    def pause(self) -> None:
        """臨時暫停行情輪詢取數 (不關閉執行緒、不改 preferences)。

        用於盤後管道/資料修正執行期間, 防止即時行情覆寫管道正在寫的 parquet。
        與 stop() 的區別: 執行緒繼續存活但跳過 _fetch_quotes; preferences 開關態不變,
        管道結束呼叫 resume() 即恢復。執行緒級檢查, 即時生效, 無 join 等待。
        """
        self._paused = True
        logger.info("行情輪詢已臨時暫停 (管道/修正執行中)")

    def resume(self) -> None:
        """恢復暫停的行情輪詢取數 (對應 pause)。"""
        self._paused = False
        logger.info("行情輪詢已恢復")

    def is_paused(self) -> bool:
        """是否處於臨時暫停態 (管道執行期間)。"""
        return self._paused

    @contextmanager
    def paused(self):
        """上下文管理器: 進入時暫停輪詢取數, 退出時(含異常)自動恢復。

        供盤後管道/資料修正複用:
            with quote_service.paused():
                run_pipeline(...)
        無論正常結束還是異常/crash, finally 都會 resume (除非進程直接被 kill)。
        """
        self.pause()
        try:
            yield
        finally:
            self.resume()

    def boot_check(self) -> None:
        """啟動時檢查 preferences，若 enabled 則自動啟動。

        none 檔無即時行情權限:即使 preferences 標記為 enabled,
        也不啟動,並同步 preferences 為關閉(避免 UI 誤顯示已開啟)。
        """
        from app.services import preferences
        if not self.is_realtime_allowed():
            if preferences.get_realtime_quotes_enabled():
                self._save_enabled(False)
            logger.info("即時行情未啟動:當前檔位(none)無即時行情權限")
            return
        if preferences.get_realtime_quotes_enabled():
            self.start()

    def set_repo(self, repo) -> None:
        """注入 KlineRepository, 用於即時落盤。"""
        self._repo = repo

    def set_app_state(self, app_state) -> None:
        """注入 FastAPI app.state (repo / extension registry 等單例)。"""
        self._app_state = app_state

    def set_interval(self, interval: float) -> float:
        """執行時更新輪詢間隔（立即生效）。"""
        clamped = self._clamp_interval(interval)
        self._interval = clamped
        from app.services import preferences
        preferences.set_realtime_quote_interval(clamped)
        logger.info("輪詢間隔已更新為 %.1fs", clamped)
        return clamped

    def get_min_interval(self) -> float:
        """返回當前檔位允許的最小間隔。"""
        return self._tier_min_interval()

    # ================================================================
    # SSE 訂閱管理 — 每個 /stream 連接一個訂閱者, 事件廣播
    # ================================================================

    def subscribe(self) -> QuoteSubscriber:
        """註冊一個 SSE 訂閱者 (連接建立時呼叫)。"""
        sub = QuoteSubscriber()
        with self._lock:
            self._subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: QuoteSubscriber) -> None:
        """註銷訂閱者 (連接斷開時呼叫)。"""
        with self._lock:
            self._subscribers.discard(sub)

    def _snapshot_subscribers(self) -> list[QuoteSubscriber]:
        with self._lock:
            return list(self._subscribers)

    def _broadcast_quote_updated(self) -> None:
        for sub in self._snapshot_subscribers():
            sub.notify_quote()

    def notify_strategy_results_updated(self) -> None:
        """策略監控完成即時結果更新後呼叫，僅刷新策略頁結果快取。"""
        for sub in self._snapshot_subscribers():
            sub.notify_strategy_results()

    def notify_depth_updated(self) -> None:
        """五檔盤口修正完成後呼叫: 通知 SSE 推送 depth_updated, 觸發連板梯隊刷新。

        與行情/告警通道獨立 — 只刷新連板梯隊, 不連帶刷新 watchlist 等。
        """
        for sub in self._snapshot_subscribers():
            sub.notify_depth()

    def notify_daily_data_updated(self) -> None:
        """盤後日線/法人/融資/集保寫入後呼叫: 前端刷新依賴日線的查詢 (含持股分析)。"""
        for sub in self._snapshot_subscribers():
            sub.notify_daily_data()

    def _broadcast_alerts(self, alerts: list[dict]) -> None:
        for sub in self._snapshot_subscribers():
            sub.push_alerts(alerts)

    def push_alerts(self, alerts: list[dict]) -> None:
        self._broadcast_alerts(alerts)

    def clear_pending_alerts(self) -> None:
        for sub in self._snapshot_subscribers():
            sub.clear_alerts()

    def push_review_event(self, event_json: str) -> None:
        """廣播一條覆盤進度事件(JSON 字串), 喚醒所有 SSE generator。

        事件格式與 recap_market_stream 的產出一致(meta/delta/error/done),
        前端 reviewStore 直接消費。背壓在訂閱者佇列內做 (丟棄最舊)。
        """
        for sub in self._snapshot_subscribers():
            sub.push_review(event_json)

    # ================================================================
    # 檔位感知間隔限制
    # ================================================================


    @classmethod
    def realtime_mode(cls) -> str:
        """當前即時行情模式: 台灣官方模式下全功能開放。"""
        return "full_market"

    @classmethod
    def is_realtime_allowed(cls) -> bool:
        """當前檔位是否允許使用即時行情。"""
        return cls.realtime_mode() != "none"

    @classmethod
    def _tier_min_interval(cls) -> float:
        return cls.DEFAULT_INTERVAL

    def _clamp_interval(self, interval: float) -> float:
        return max(self._tier_min_interval(), min(self.MAX_INTERVAL, interval))

    # ================================================================
    # 行情資料訪問
    # ================================================================

    def get_enriched_today(self) -> tuple[pl.DataFrame, date | None]:
        """返回今天 enriched 資料 + 日期 (執行緒安全)。

        所有頁面統一通過此方法獲取即時行情 + 技術指標。
        """
        if not self._repo:
            return pl.DataFrame(), None
        return self._repo.get_enriched_latest()

    def get_quotes_compat(self) -> pl.DataFrame:
        """相容接口: 返回行情 DataFrame (用於盤中選股等需要 last_price/prev_close 的場景)。

        從 _enriched_cache 取 today 的資料, 只選行情基礎列, 補上 last_price 別名。
        不返回指標列, 避免 JOIN live_agg 時列名衝突。
        """
        df, _ = self.get_enriched_today()
        if df.is_empty():
            return df

        # 只取盤中選股需要的行情基礎列
        keep = [c for c in [
            "symbol", "close", "open", "high", "low", "volume", "amount",
            "prev_close", "change_pct", "change_amount", "amplitude", "turnover_rate",
        ] if c in df.columns]
        df = df.select(keep)

        # enriched 的 close 等價於 last_price
        if "close" in df.columns and "last_price" not in df.columns:
            df = df.with_columns(pl.col("close").alias("last_price"))
        return df

    def get_index_quotes(self, symbols: list[str] | None = None) -> pl.DataFrame:
        """返回即時指數行情快取。不會觸發外部請求。"""
        with self._lock:
            df = self._index_quotes_cache.clone() if self._index_quotes_cache is not None else pl.DataFrame()
        if df.is_empty():
            return df
        if symbols:
            return df.filter(pl.col("symbol").is_in(symbols))
        return df

    def status(self) -> dict:
        """返回行情服務狀態。"""
        from app.services import preferences
        age = (time.perf_counter() - self._fetch_time) * 1000 if self._fetch_time else -1
        mode = self.realtime_mode()
        phase = self._market_phase()
        return {
            "enabled": self._enabled,
            "running": self._running,
            "paused": self._paused,
            "mode": mode,
            "realtime_allowed": mode != "none",
            "watchlist_symbol_count": len(preferences.get_realtime_watchlist_symbols()),
            "interval_s": self._interval,
            "symbol_count": self._symbol_count,
            "index_symbol_count": self._index_symbol_count,
            "etf_symbol_count": self._etf_symbol_count,
            "quote_age_ms": round(age, 0) if age >= 0 else None,
            # 台股盤中 (09:00-13:30) 才輪詢; market_phase 即 MarketStatus 值。
            "is_trading_hours": phase == MarketStatus.OPEN.value,
            "is_polling_window": phase == MarketStatus.OPEN.value,
            "market_phase": phase,
            "last_fetch_ms": round(self._fetched_at, 0) if self._fetched_at else None,
        }

    def refresh(self) -> dict:
        """手動觸發一次行情拉取。"""
        self._fetch_quotes()
        return self.status()

    # ================================================================
    # 後台輪詢
    # ================================================================

    def _poll_loop(self) -> None:
        while self._running and self._enabled:
            try:
                # 管道/資料修正執行期間臨時暫停取數, 防止與管道寫同一批 parquet 競態。
                # 執行緒繼續存活 + 分片 sleep, resume() 後即時恢復, 無需重啟執行緒。
                if not self._paused:
                    if self._is_continuous_trading():
                        self._fetch_quotes()
                    else:
                        logger.debug("非台股盤中, 跳過行情輪詢")
            except Exception as e:  # noqa: BLE001
                logger.warning("行情輪詢異常: %s", e)

            waited = 0.0
            while self._running and self._enabled and waited < self._interval:
                time.sleep(0.5)
                waited += 0.5

    def _fetch_quotes(self) -> None:
        """按當前檔位拉取行情。加鎖串行化 (後台輪詢 vs 手動 refresh)。"""
        with self._fetch_lock:
            self._fetch_full_market_quotes()

    def _fetch_full_market_quotes(self) -> None:
        """拉取全市場行情 → 寫 daily + 計算 enriched + 更新快取。"""
        from app.services import preferences

        provider_name = preferences.get_realtime_data_provider()
        if provider_name == "taiwan":
            # 台股官方來源沒有全市場盤中快照 (STOCK_DAY_ALL 是盤後日資料, 不可冒充今日即時 K
            # 寫進 kline_daily)。monitor_engine 自己向 MIS 取規則標的的即時報價, 這裡只觸發評估。
            self._evaluate_monitors()
            return

        from app.data_providers import custom as custom_sources
        if custom_sources.provider_has_dataset(provider_name, "realtime"):
            try:
                t0 = time.perf_counter()
                now_ts = time.perf_counter()
                records = custom_sources.get_provider(provider_name).get_realtime()
                self._process_full_market_records(records, t0=t0, now_ts=now_ts)
                return
            except Exception as e:  # noqa: BLE001
                logger.warning("自定義即時行情拉取失敗: %s", e)
                return
        logger.warning("即時資料來源 %s 未提供 realtime 資料集, 略過行情更新", provider_name)

    def _process_full_market_records(self, records: list[dict], *, t0: float, now_ts: float) -> None:
        """把全市場 records 寫盤並增量計算 enriched。"""
        from app.services import preferences
        from app.taiwan.symbol import is_taiwan_symbol

        if any(not is_taiwan_symbol(str(record.get("symbol") or "")) for record in records):
            raise ValueError("Realtime providers must return canonical Taiwan symbols")
        all_index_symbols = set(self._repo.get_index_symbol_set()) if self._repo else set()
        core_index_symbols = set(preferences.get_realtime_index_symbols() or self.CORE_INDEX_SYMBOLS)
        all_index_symbols.update(core_index_symbols)
        all_etf_symbols = set()
        if self._repo:
            etf_inst = self._repo.get_etf_instruments()
            if not etf_inst.is_empty() and "symbol" in etf_inst.columns:
                all_etf_symbols = set(etf_inst["symbol"].cast(pl.Utf8).to_list())

        if not records:
            logger.warning("行情資料為空")
            return

        index_records = [r for r in records if r.get("symbol") in all_index_symbols]
        etf_records = [r for r in records if r.get("symbol") in all_etf_symbols]
        stock_records = [
            r for r in records
            if r.get("symbol") not in all_index_symbols and r.get("symbol") not in all_etf_symbols
        ]

        fetch_ms = (time.perf_counter() - t0) * 1000
        fetched_at = time.time() * 1000

        # ---- 更新元信息 ----
        with self._lock:
            self._fetch_time = now_ts
            self._fetch_ms = fetch_ms
            self._fetched_at = fetched_at
            self._symbol_count = len(stock_records)
            self._index_symbol_count = len(index_records)
            self._etf_symbol_count = len(etf_records)
            self._index_quotes_cache = self._build_index_quotes(index_records)

        _persist_last_fetch(fetched_at)
        logger.info("行情刷新: %d 只股票, %d 只ETF, %d 只指數, 耗時 %.0fms", len(stock_records), len(etf_records), len(index_records), fetch_ms)

        # ---- 寫 kline_daily (不復權原始價格, 只有 OHLCV) ----
        daily_df = self._build_daily(stock_records)
        if not daily_df.is_empty() and self._repo:
            try:
                self._repo.flush_live_daily(daily_df)
            except Exception as e:  # noqa: BLE001
                logger.warning("日K寫盤失敗: %s", e)

        etf_daily_df = self._build_daily(etf_records)
        if not etf_daily_df.is_empty() and self._repo:
            try:
                self._repo.flush_live_daily_asset("etf", etf_daily_df)
            except Exception as e:  # noqa: BLE001
                logger.warning("ETF 日K寫盤失敗: %s", e)

        # ---- 構建 API 直接值的補充表 (不寫 daily, 只用於 enriched 計算) ----
        quote_extra = self._build_quote_extra(stock_records)
        etf_quote_extra = self._build_quote_extra(etf_records)

        # ---- 增量計算 enriched + 寫盤 + 更新快取 ----
        if not daily_df.is_empty() and self._repo:
            self._flush_live_enriched(daily_df, quote_extra, asset_type="stock")
        if not etf_daily_df.is_empty() and self._repo:
            self._flush_live_enriched(etf_daily_df, etf_quote_extra, asset_type="etf")
        # ---- 通知 SSE ----
        self._broadcast_quote_updated()

        # ---- 策略監控 + 告警評估 ----
        self._evaluate_monitors()


    # ================================================================
    # 工具
    # ================================================================

    @staticmethod
    def _split_records_by_asset(
        records: list[dict], index_set: set[str], etf_set: set[str],
    ) -> tuple[list[dict], list[dict], list[dict]]:
        """把行情 records 按資產拆成 (index, etf, stock)。判定順序與 resolve_asset_type 一致: 先 ETF 後指數。"""
        index_records: list[dict] = []
        etf_records: list[dict] = []
        stock_records: list[dict] = []
        for r in records:
            sym = r.get("symbol")
            if sym in etf_set:
                etf_records.append(r)
            elif sym in index_set:
                index_records.append(r)
            else:
                stock_records.append(r)
        return index_records, etf_records, stock_records

    @staticmethod
    def _build_daily(records: list[dict]) -> pl.DataFrame:
        """將 API records 轉為日K格式 DataFrame (OHLCV + quote_ts, 寫 kline_daily 用)。"""
        if not records:
            return pl.DataFrame()
        df = pl.DataFrame(records)
        cols_map = {
            "symbol": "symbol",
            "last_price": "close",
            "open": "open",
            "high": "high",
            "low": "low",
            "volume": "volume",
            "amount": "amount",
            "timestamp": "quote_ts",
        }
        select_exprs = []
        for src, dst in cols_map.items():
            if src in df.columns:
                select_exprs.append(pl.col(src).cast(pl.Int64, strict=False).alias(dst)
                                     if dst == "quote_ts" else pl.col(src).alias(dst))
        if not select_exprs:
            return pl.DataFrame()
        result = df.select(select_exprs).with_columns(
            pl.lit(cn_today()).cast(pl.Date).alias("date"),
        )
        # 修復: API 在非交易時段可能返回 open/high/low=0 或 null,
        # 導致蠟燭從 0 開始。用 close 填充這些異常值。
        for col in ("open", "high", "low"):
            if col in result.columns:
                result = result.with_columns(
                    pl.when((pl.col(col) == 0) | pl.col(col).is_null())
                    .then(pl.col("close"))
                    .otherwise(pl.col(col))
                    .alias(col)
                )
        return result

    @staticmethod
    def _build_quote_extra(records: list[dict]) -> pl.DataFrame:
        """構建 API 直接提供的補充字段 (不寫 daily, 只傳給 enriched 計算)。

        包含: prev_close, change_pct, change_amount, amplitude, turnover_rate。
        """
        if not records:
            return pl.DataFrame()
        df = pl.DataFrame(records)
        keep = [c for c in [
            "symbol", "prev_close", "change_pct", "change_amount",
            "amplitude", "turnover_rate",
        ] if c in df.columns]
        if not keep or "symbol" not in keep:
            return pl.DataFrame()
        out = df.select(keep)
        # 即時 API 的 turnover_rate 入口契約為小數制(0.05 = 5%).
        # enriched 內部統一存百分數值(5 = 5%), 後續頁面/篩選直接展示和比較。
        if "turnover_rate" in out.columns:
            out = out.with_columns((pl.col("turnover_rate").cast(pl.Float64, strict=False) * 100).alias("turnover_rate"))
        return out

    @staticmethod
    def _build_index_quotes(records: list[dict]) -> pl.DataFrame:
        """構建指數即時行情快取，不落股票 parquet。

        注意: API 返回的 change_pct/amplitude 是小數 (0.0366 = 3.66%),
        統一轉成百分比輸出, 與 _fallback_index_quotes_from_daily 口徑一致
        (前端指數側不×100, 直接 toFixed(2)% 展示)。
        """
        if not records:
            return pl.DataFrame()
        df = pl.DataFrame(records)
        keep = [c for c in [
            "symbol", "name", "last_price", "prev_close", "open", "high", "low",
            "volume", "amount", "change_pct", "change_amount", "amplitude", "timestamp", "session",
        ] if c in df.columns]
        if not keep or "symbol" not in keep:
            return pl.DataFrame()
        df = df.select(keep)
        # 自定義源可能不提供 change_pct/change_amount, 按 last_price/prev_close 補算
        # (資料源可能已計算漲跌幅, 此處只補缺失值)
        if "change_pct" not in df.columns and "last_price" in df.columns and "prev_close" in df.columns:
            # prev_close=0 → inf (非合法 JSON), prev_close=null → null; 用 when 守護
            df = df.with_columns(
                pl.when(pl.col("prev_close") != 0)
                .then((pl.col("last_price") - pl.col("prev_close")) / pl.col("prev_close"))
                .otherwise(None)
                .alias("change_pct")
            )
        if "change_amount" not in df.columns and "last_price" in df.columns and "prev_close" in df.columns:
            df = df.with_columns(
                (pl.col("last_price") - pl.col("prev_close")).alias("change_amount")
            )
        # change_pct / amplitude: 小數 → 百分比 (統一指數展示口徑)
        for col in ("change_pct", "amplitude"):
            if col in df.columns:
                df = df.with_columns((pl.col(col).cast(pl.Float64) * 100).alias(col))
        if "last_price" in df.columns and "close" not in df.columns:
            df = df.with_columns(pl.col("last_price").alias("close"))
        return df

    @staticmethod
    def _market_phase() -> str:
        """台股市場時段 (Asia/Taipei), 回傳 MarketStatus 值; 時段規則唯一來源是 taiwan.realtime.calendar。

        不要求「已確認交易日」: 颱風假/國定假日由 monitor_engine 的報價品質閘門
        (MIS 無今日新鮮成交 → scheduled_open_unverified → 不告警) fail-closed 兜底。
        """
        return get_market_status(taipei_now()).value

    @classmethod
    def _is_continuous_trading(cls) -> bool:
        """台股盤中 09:00-13:30 (含邊界), 僅此時段輪詢並評估監控。"""
        return cls._market_phase() == MarketStatus.OPEN.value

    @staticmethod
    def _save_enabled(enabled: bool) -> None:
        from app.services import preferences
        preferences.save({"realtime_quotes_enabled": enabled})

    # ================================================================
    # 策略監控
    # ================================================================

    def _evaluate_monitors(self) -> None:
        """行情更新後評估台股即時監控規則, 落盤、SSE 廣播並送外部提醒。"""
        try:
            # 僅在「交易日 + 連續競價時段」評估監控, 避開集合競價與盤後緩衝。
            if not self._is_continuous_trading():
                return
            if not self._app_state:
                return

            persisted_taiwan_events: list[dict] = []
            try:
                from app.taiwan.realtime.monitor_engine import get_monitor_engine
                tw_engine = get_monitor_engine()
                if tw_engine.list_rules():
                    def persist_taiwan_alerts(alerts):
                        events = self._format_extension_notifications(
                            [alert.to_dict() for alert in alerts],
                        )
                        from app.services import alert_store
                        alert_store.append_many(
                            self._app_state.repo.store.data_dir, events,
                        )
                        persisted_taiwan_events.extend(events)

                    tw_engine.evaluate_all(persist_events=persist_taiwan_alerts)
            except Exception as e:  # noqa: BLE001
                logger.warning("台股監控評估失敗: %s", e)

            if not persisted_taiwan_events:
                return

            all_alerts: list[dict] = []
            for ev in persisted_taiwan_events:
                all_alerts.append({
                    "source": ev["source"],
                    "type": ev["type"],
                    "rule_id": ev.get("rule_id"),
                    "strategy_id": None,
                    "symbol": ev["symbol"],
                    "name": ev["name"],
                    "message": ev["message"],
                    "price": ev["price"],
                    "change_pct": ev["change_pct"],
                    "signals": ev.get("signals") or [],
                    "severity": ev.get("severity", "info"),
                    "conditions": ev.get("conditions") or [],
                    "logic": ev.get("logic") or "and",
                })

            # 廣播到所有 SSE 訂閱者 (背壓保護在訂閱者佇列內做)
            self._broadcast_alerts(all_alerts)
            logger.info("監控評估完成: %d 條通知", len(all_alerts))
            # 系統通知 (可選通道, 由 preferences 開關控制)。
            self._maybe_send_system_notifications(all_alerts)
            # 外部推播 (LINE / Telegram)。
            self._maybe_send_webhook(persisted_taiwan_events, None)
            # AI follow-up for plan alerts (default off); after the rule-based push, never blocking it.
            try:
                from app.taiwan import event_ai_explain

                event_ai_explain.submit(persisted_taiwan_events)
            except Exception as e:  # noqa: BLE001
                logger.warning("事件 AI 解讀提交失敗 (%s)", type(e).__name__)

        except Exception as e:  # noqa: BLE001
            logger.warning("監控評估失敗: %s", e)

    def _format_extension_notifications(self, events: list[dict]) -> list[dict]:
        """Apply optional copy formatters after evaluation and before every output channel."""
        registry = (
            getattr(self._app_state, "extension_registry", None)
            if self._app_state is not None
            else None
        )
        if registry is None or not registry.has_notification_formatters:
            return events

        from app.extensions.contracts import (
            BACKEND_EXTENSION_API_VERSION,
            NotificationFormatContext,
        )

        formatted_events: list[dict] = []
        for event in events:
            formatted = dict(event)
            context = NotificationFormatContext(
                api_version=BACKEND_EXTENSION_API_VERSION,
            )
            for registered in registry.notification_formatters():
                try:
                    message = registered.implementation.format_message(dict(formatted), context)
                    if not isinstance(message, str):
                        raise TypeError("notification formatter must return str")
                    formatted["message"] = message
                except Exception as exc:
                    logger.warning(
                        "notification formatter failed %s: %s",
                        registered.implementation_id,
                        exc,
                    )
            formatted_events.append(formatted)
        return formatted_events

    def _maybe_send_webhook(self, rule_events: list[dict], engine) -> None:
        """Dispatch persisted alert events; global channels override legacy rule opt-ins."""
        try:
            from app.services import preferences
            from app.services import webhook_adapter

            global_channels = preferences.get_external_notification_channels()

            rules = engine.rules if engine is not None else {}
            pending = []
            for ev in rule_events:
                rule = rules.get(ev.get("rule_id"))
                if global_channels is not None:
                    # Global setting always overrides per-rule config
                    channels = global_channels
                elif rule is not None:
                    # Generic monitor rule: read from rule dict
                    channels = rule.get("webhook_channels", [])
                else:
                    # Taiwan monitor rule: channels embedded in alert event dict
                    channels = [c for c in ev.get("notify_channels", []) if c in ("line", "telegram")]
                if channels:
                    pending.append((ev, channels))

            needs_line = any("line" in channels for _ev, channels in pending)
            needs_telegram = any("telegram" in channels for _ev, channels in pending)
            line_token = preferences.get_line_channel_access_token() if needs_line else ""
            line_target = preferences.get_line_target_id() if needs_line else ""
            telegram_token = preferences.get_telegram_bot_token() if needs_telegram else ""
            telegram_chat = preferences.get_telegram_chat_id() if needs_telegram else ""

            enqueued = 0
            for ev, channels in pending:
                body = webhook_adapter.alert_message(ev)
                event_id = str(ev.get("alert_id") or ev.get("dedup_key") or "|".join(
                    str(ev.get(key) or "") for key in ("rule_id", "symbol", "sector_key", "type", "ts")
                ))
                # The alert is already durable and in SSE before provider I/O begins.
                if "line" in channels:
                    if _claim_external_delivery(event_id, "line"):
                        if line_token and line_target:
                            _WEBHOOK_EXECUTOR.submit(webhook_adapter.send_line, line_token, line_target, "", body)
                        else:
                            webhook_adapter.record_delivery_status("line", "not_configured")
                        enqueued += 1
                if "telegram" in channels:
                    if _claim_external_delivery(event_id, "telegram"):
                        if telegram_token and telegram_chat:
                            _WEBHOOK_EXECUTOR.submit(webhook_adapter.send_telegram, telegram_token, telegram_chat, "", body)
                        else:
                            webhook_adapter.record_delivery_status("telegram", "not_configured")
                        enqueued += 1
            if enqueued:
                logger.info("Webhook 已提交 %d 條 (非同步投遞, 按渠道獨立投遞, 失敗記 WARNING)", enqueued)
        except Exception as e:  # noqa: BLE001
            logger.warning("外部提醒提交失敗 (%s)", type(e).__name__)

    def _maybe_send_system_notifications(self, all_alerts: list[dict]) -> None:
        """把告警轉發到操作系統通知中心 (由 preferences 開關控制)。

        - 開關關閉: 直接返回
        - 開關開啟: 逐條發系統通知; 失敗靜默, 不阻斷主流程
        - 去重: 複用 MonitorRuleEngine 的 cooldown, 此處不重複去重
        - 批量策略事件 (symbol="") 聚合為一條通知, 避免刷屏
        """
        try:
            from app.services import preferences
            from app.services import notify_adapter

            if not preferences.get_system_notify_enabled():
                return

            for ev in all_alerts:
                # 通知標題: 用 source 分類 (策略/信號/價格/異動)
                source = ev.get("source", "")
                source_label = {
                    "strategy": "策略", "signal": "訊號",
                    "price": "價格", "market": "異動",
                }.get(source, source or "通知")

                name = ev.get("name") or ""
                symbol = ev.get("symbol") or ""
                message = ev.get("message") or ""

                # 正文: 優先用現成 message, 拼上 symbol/name 讓使用者一眼定位
                if symbol:
                    body = f"{symbol} {name} {message}".strip()
                else:
                    body = message or name

                title = f"台股監控 · {source_label}"
                notify_adapter.notify(title, body)
        except Exception as e:  # noqa: BLE001
            logger.debug("系統通知發送異常 (不影響告警主流程): %s", e)

    # ================================================================
    # enriched 增量計算
    # ================================================================

    def _flush_live_enriched(self, daily_df: pl.DataFrame, quote_extra: pl.DataFrame = None, asset_type: str = "stock", merge: bool = False) -> None:
        """增量計算今天的 enriched: 用昨天的遞推狀態 + 今天 OHLCV → 只算今天 5500 行。

        quote_extra: API 直接提供的補充字段 (prev_close, change_pct 等),
                     不寫 daily, 直接傳給 compute_enriched_today 避免重複計算。
        """
        try:
            today = cn_today()
            t0 = time.perf_counter()

            # ---- 嘗試增量路徑 ----
            live_agg = self._repo.get_live_agg() if asset_type == "stock" else pl.DataFrame()
            prev_enriched, prev_date = (
                self._repo.get_enriched_latest()
                if asset_type == "stock"
                else self._repo.get_enriched_latest_asset(asset_type)
            )

            use_incremental = (
                asset_type == "stock"
                and not live_agg.is_empty()
                and not prev_enriched.is_empty()
                and prev_date is not None
            )

            if use_incremental:
                from app.indicators.pipeline import compute_enriched_today
                from app.market_time import trading_minutes_elapsed_from_ts, trading_minutes_elapsed
                instruments = self._repo.get_instruments()
                # 將 API 直接提供的補充字段 JOIN 到 daily_df
                today_ohlcv = daily_df
                if quote_extra is not None and not quote_extra.is_empty():
                    today_ohlcv = daily_df.join(quote_extra, on="symbol", how="left")
                # 量比時間折算: 優先用行情 quote_ts (真實成交時間), 缺失則兜底服務端時間
                elapsed_minutes: float | None = None
                if "quote_ts" in daily_df.columns and not daily_df.is_empty():
                    valid_ts = daily_df["quote_ts"].drop_nulls()
                    if not valid_ts.is_empty():
                        elapsed_minutes = trading_minutes_elapsed_from_ts(valid_ts.median())
                if elapsed_minutes is None:
                    elapsed_minutes = trading_minutes_elapsed()
                enriched_today = compute_enriched_today(
                    live_agg=live_agg,
                    prev_enriched=prev_enriched,
                    today_ohlcv=today_ohlcv,
                    instruments=instruments,
                    elapsed_minutes=elapsed_minutes,
                )
                if enriched_today.is_empty():
                    logger.warning("增量計算結果為空, 回退到全量計算")
                    use_incremental = False

            # ---- 全量回退路徑 ----
            if not use_incremental:
                from datetime import timedelta
                from app.indicators.pipeline import compute_enriched

                logger.info("enriched 全量計算 (live_agg=%s, 上次日期=%s)",
                            "ok" if not live_agg.is_empty() else "空", prev_date)

                cutoff = today - timedelta(days=90)
                table = {"etf": "kline_etf_daily", "index": "kline_index_daily"}.get(asset_type, "kline_daily")
                daily_glob = str(self._repo.store.data_dir / table / "**" / "*.parquet")
                ohlcv_cols = ["symbol", "date", "open", "high", "low", "close", "volume", "amount", "quote_ts"]
                hist_df = (
                    scan_daily_parquet(daily_glob)
                    .filter(pl.col("date") >= cutoff)
                    .sort(["symbol", "date"])
                    .collect()
                )
                if hist_df.is_empty():
                    return

                hist_cols = [c for c in ohlcv_cols if c in hist_df.columns]
                hist_df = hist_df.select(hist_cols).filter(pl.col("date") != today)
                daily_ohlcv = daily_df.select([c for c in ohlcv_cols if c in daily_df.columns])
                full_df = pl.concat([hist_df, daily_ohlcv], how="diagonal_relaxed")
                full_df = full_df.sort(["symbol", "date"])

                factor_dir = {"stock": "adj_factor", "etf": "adj_factor_etf"}.get(asset_type)
                factor_path = self._repo.store.data_dir / factor_dir / "all.parquet" if factor_dir else None
                factors = pl.DataFrame()
                if factor_path and factor_path.exists():
                    try:
                        factors = pl.read_parquet(factor_path)
                    except Exception:
                        pass
                instruments = self._repo.get_instruments() if asset_type == "stock" else None

                enriched_full = compute_enriched(
                    full_df,
                    factors=factors,
                    instruments=instruments,
                    historical_shares=(
                        self._repo.get_historical_shares()
                        if asset_type == "stock"
                        else None
                    ),
                )
                # momentum_3d 不在指標全集裡, 但 deviate_3d 需要; 多日幀上 shift 補算
                enriched_full = enriched_full.sort(["symbol", "date"]).with_columns(
                    (pl.col("close") / pl.col("close").shift(3).over("symbol") - 1).alias("momentum_3d")
                )
                enriched_today = enriched_full.filter(pl.col("date") == today)

            if enriched_today.is_empty():
                return

            # 異動偏離列: 盤中路徑不經過 _refresh_enriched 冷刷新,
            # 需在此附著 (基準 = 歷史幀 + 指數即時外推), 否則盤中異動列表為空
            if asset_type == "stock":
                from app.indicators.pipeline import attach_deviation_columns_today
                try:
                    index_quotes = self.get_index_quotes()
                except Exception:
                    index_quotes = None
                enriched_today = attach_deviation_columns_today(
                    enriched_today, self._repo.store.data_dir, index_quotes
                )

            # ---- 寫盤 + 更新快取 ----
            if merge:
                self._repo.merge_live_enriched_asset(asset_type, enriched_today)
            else:
                self._repo.flush_live_enriched_asset(asset_type, enriched_today)

            elapsed = time.perf_counter() - t0
            mode_label = "增量" if use_incremental else "全量"
            logger.info("enriched %s: %d 只, %s, 耗時 %.0fms",
                        mode_label, len(enriched_today), today, elapsed * 1000)
        except Exception as e:  # noqa: BLE001
            logger.warning("enriched 計算失敗: %s", e)

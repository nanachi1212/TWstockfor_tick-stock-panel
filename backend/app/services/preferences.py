"""使用者偏好設定持久化。

儲存位置: data/user_data/preferences.json
沿用 secrets_store 的 merge-write 模式,但不做 chmod 0600 (非敏感資料)。
"""
from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# 行程內快取: 行情輪詢執行緒一輪會呼叫 8~12 次 getter, 每次讀盤+parse 是純重複;
# 檔案僅在使用者改設定時變化, 以 (mtime_ns, size) 簽名判斷是否重讀。
_cache: dict | None = None
_cache_sig: tuple[int, int] | None = None
_SAVE_LOCK = threading.RLock()


# 監控規則可選的外部推播管道。多選: 不推播 = 空陣列, 而非 'none'。
REVIEW_PUSH_CHANNELS = {"line", "telegram"}

# 頁面 SSE 重新整理設定: { "watchlist": true, "monitor": true, ... }
SSE_REFRESH_PAGES_DEFAULT = {
    "overview-market": True,
    "watchlist": True,
}


def _path() -> Path:
    from app.config import settings
    p = settings.data_dir / "user_data" / "preferences.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _invalidate_cache() -> None:
    global _cache, _cache_sig
    with _SAVE_LOCK:
        _cache = None
        _cache_sig = None


def load() -> dict:
    """讀取 preferences.json (帶 mtime 簽名快取)。回傳深拷貝, 呼叫方可自由修改。"""
    with _SAVE_LOCK:
        return _load_unlocked()


def _load_unlocked() -> dict:
    global _cache, _cache_sig
    p = _path()
    try:
        sig = (p.stat().st_mtime_ns, p.stat().st_size)
    except OSError:
        return {}
    if _cache is not None and sig == _cache_sig:
        return copy.deepcopy(_cache)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning("preferences.json malformed: %s", e)
        return {}
    _cache = data
    _cache_sig = sig
    return copy.deepcopy(_cache)


def save(updates: dict) -> dict:
    """合併寫入。回傳新內容。"""
    with _SAVE_LOCK:
        p = _path()
        current = load()
        if not current and p.exists():
            # 檔案存在卻讀不出內容 = 損壞; 覆寫前留一份, 避免一次存檔抹掉全部設定。
            try:
                json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                shutil.copy2(p, p.with_name(p.name + ".corrupt"))
        current.update(updates)
        # 先寫暫存檔再原子替換: 寫到一半當機不會留下半截 JSON。
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
        _invalidate_cache()
        return current


def get_realtime_quotes_enabled() -> bool:
    return load().get("realtime_quotes_enabled", False)


def get_indices_nav_pinned() -> bool:
    """側欄指數報價卡片是否固定顯示。預設 True（常駐）。
    關閉後，卡片跟隨即時行情開關（僅即時開時顯示）。"""
    return load().get("indices_nav_pinned", True)


def get_watchlist_groups_in_nav() -> bool:
    """自選分組是否顯示在側邊欄（可展開二級子選單）。預設 False。"""
    return load().get("watchlist_groups_in_nav", False)


def get_realtime_quote_interval() -> float:
    return load().get("realtime_quote_interval", 6.0)


def get_realtime_watchlist_symbols() -> list[str]:
    """Free 檔自選即時監控標的:直接取自選頁前 5 個。"""
    try:
        from app.services import watchlist
        rows = watchlist.list_symbols()
    except Exception as e:
        logger.warning("load watchlist for realtime failed: %s", e)
        return []
    out: list[str] = []
    for row in rows:
        symbol = str((row or {}).get("symbol") or "").strip().upper()
        if symbol and symbol not in out:
            out.append(symbol)
        if len(out) >= 5:
            break
    return out


def set_realtime_quote_interval(interval: float) -> float:
    """儲存行情輪詢間隔（不在此做 min/max 校驗，由呼叫方按檔位限制）。"""
    save({"realtime_quote_interval": interval})
    return interval


def get_minute_sync_enabled() -> bool:
    return load().get("minute_sync_enabled", False)


def get_minute_intraday_refresh() -> bool:
    """自選列表分時圖是否跟隨即時行情重新整理; 使用者沒設定過時預設開啟。"""
    return bool(load().get("minute_intraday_refresh", True))


# 分時圖即時重新整理間隔允許範圍 (秒)。下限 3s, 上限 60s。
_INTRADAY_REFRESH_INTERVAL_MIN = 3
_INTRADAY_REFRESH_INTERVAL_MAX = 60


def get_minute_intraday_refresh_interval() -> int:
    """分時圖即時重新整理輪詢間隔 (秒)。預設 6s, 範圍 [3, 60]。"""
    return max(_INTRADAY_REFRESH_INTERVAL_MIN,
               min(_INTRADAY_REFRESH_INTERVAL_MAX,
                   int(load().get("minute_intraday_refresh_interval", 6))))


def get_minute_sync_days() -> int:
    return max(1, min(30, load().get("minute_sync_days", 5)))


def get_minute_sync_segment_days() -> int:
    """分鐘 K 取得的單段大小(交易日)。預設 20,範圍 [5, 30]。

    每段拉完後立即寫入磁碟(流式),避免全量攢記憶體導致 OOM。
    段越小記憶體峰值越低但總耗時越長(限速 sleep 隨段數線性增加);
    最大 30 個交易日; 實際取得範圍仍受當前資料來源能力限制。
    """
    return max(5, min(30, load().get("minute_sync_segment_days", 20)))


# ===== 資料來源選擇 (台灣官方或已設定的自訂來源) =====

_ALLOWED_DATA_PROVIDERS = {"taiwan"}


def _allowed_data_providers() -> set[str]:
    try:
        from app.data_providers import custom as custom_sources
        return _ALLOWED_DATA_PROVIDERS | custom_sources.names()
    except Exception:
        return set(_ALLOWED_DATA_PROVIDERS)


def get_daily_data_provider() -> str:
    provider = str(load().get("daily_data_provider", "taiwan") or "taiwan").lower()
    return provider if provider in _allowed_data_providers() else "taiwan"


def get_adj_factor_provider() -> str:
    provider = str(load().get("adj_factor_provider", "same_as_daily") or "same_as_daily").lower()
    if provider == "same_as_daily":
        return provider
    return provider if provider in _allowed_data_providers() else "same_as_daily"


def get_minute_data_provider() -> str:
    provider = str(load().get("minute_data_provider", "taiwan") or "taiwan").lower()
    return provider if provider in _allowed_data_providers() else "taiwan"


def get_realtime_data_provider() -> str:
    provider = str(load().get("realtime_data_provider", "taiwan") or "taiwan").lower()
    return provider if provider in _allowed_data_providers() else "taiwan"


def get_financial_provider() -> str:
    provider = str(load().get("financial_data_provider", "taiwan") or "taiwan").lower()
    return provider if provider in _allowed_data_providers() else "taiwan"


# ===== 盤後管道取得內容開關 =====


def get_enriched_batch_size() -> int:
    """回傳 enriched 全量計算每批 symbol 數量。"""
    return max(1, min(10000, load().get("enriched_batch_size", 1000)))


def get_index_daily_batch_size() -> int:
    """回傳指數日 K 同步每批 symbol 數量。"""
    return max(1, min(10000, load().get("index_daily_batch_size", 100)))


# ── 五檔報價 sealed(真假漲停) 設定 ──────────────────────


def get_realtime_pull_stock() -> bool:
    return load().get("realtime_pull_stock", True)


def get_realtime_pull_etf() -> bool:
    # 老使用者相容: ETF 即時預設關閉，避免升級後請求量/寫盤量突然增加。
    return load().get("realtime_pull_etf", False)


def get_realtime_pull_index() -> bool:
    """The retired index feed cannot be enabled by historical preferences."""
    return False


def get_realtime_index_mode() -> str:
    mode = str(load().get("realtime_index_mode", "core") or "core").lower()
    return mode if mode in {"core", "all"} else "core"


def get_realtime_index_symbols() -> list[str]:
    """No index feed is configured; leave old persisted preferences untouched."""
    return []


def get_realtime_quote_scope() -> dict:
    return {
        "realtime_pull_stock": get_realtime_pull_stock(),
        "realtime_pull_etf": get_realtime_pull_etf(),
        "realtime_pull_index": get_realtime_pull_index(),
        "realtime_index_mode": get_realtime_index_mode(),
        "realtime_index_symbols": get_realtime_index_symbols(),
    }


def get_sse_refresh_pages() -> dict[str, bool]:
    """回傳每個頁面的 SSE 重新整理開關。"""
    stored = load().get("sse_refresh_pages", {})
    # 合併預設值 (新增頁面自動出現)
    result = dict(SSE_REFRESH_PAGES_DEFAULT)
    result.update({key: value for key, value in stored.items() if key in result})
    return result


def get_sidebar_index_symbols() -> list[str]:
    """The retired sidebar index feed has no active symbols."""
    return []


def get_system_notify_enabled() -> bool:
    """系統通知開關 — 開啟後監控告警同時推播到作業系統通知中心。"""
    return load().get("system_notify_enabled", False)


def get_line_target_id() -> str:
    return str(load().get("line_target_id", "") or "").strip()


def set_line_target_id(target_id: str) -> str:
    save({"line_target_id": str(target_id or "").strip()})
    return get_line_target_id()


def get_telegram_chat_id() -> str:
    return str(load().get("telegram_chat_id", "") or "").strip()


def set_telegram_chat_id(chat_id: str) -> str:
    save({"telegram_chat_id": str(chat_id or "").strip()})
    return get_telegram_chat_id()


def _get_notification_secret(key: str) -> str:
    from app import secrets_store
    return str(secrets_store.load().get(key, "") or "").strip()


def _set_notification_secret(key: str, value: str) -> str:
    from app import secrets_store
    value = str(value or "").strip()
    if value:
        secrets_store.save({key: value})
    else:
        secrets_store.clear(key)
    return _get_notification_secret(key)


def get_line_channel_access_token() -> str:
    return _get_notification_secret("line_channel_access_token")


def set_line_channel_access_token(token: str) -> str:
    return _set_notification_secret("line_channel_access_token", token)


def get_telegram_bot_token() -> str:
    return _get_notification_secret("telegram_bot_token")


def set_telegram_bot_token(token: str) -> str:
    return _set_notification_secret("telegram_bot_token", token)


def get_finmind_token() -> str:
    from app import secrets_store
    val = secrets_store.load().get("finmind_token")
    if val:
        return str(val).strip()
    import os
    return os.environ.get("FINMIND_TOKEN", "").strip()


def set_finmind_token(token: str) -> str:
    from app import secrets_store
    token = str(token or "").strip()
    if token:
        secrets_store.save({"finmind_token": token})
    else:
        secrets_store.clear("finmind_token")
    return get_finmind_token()


def get_finmind_enabled() -> bool:
    return bool(load().get("finmind_enabled", False))


def set_finmind_enabled(enabled: bool) -> bool:
    save({"finmind_enabled": bool(enabled)})
    return get_finmind_enabled()


def get_webhook_enabled_default() -> bool:
    """新增監控規則時是否預設勾選推播 (老布林, 已由 webhook_default_channels 取代)。

    保留向後相容: 讀取 webhook_default_channels 非空時回傳 True。
    """
    return bool(get_webhook_default_channels())


def get_webhook_default_channels() -> list[str]:
    """新增監控規則時預設勾選的推播管道 (多選)。

    空列表 = 新增規則預設不推播; ['line'] = 預設推播 LINE。
    此預設值供規則編輯器新增規則時預填, 單條規則仍可獨立修改。

    舊版飛書/企業微信管道與布林值只做安全過濾,不刪除原始偏好資料,
    也不自動對應到新的收件者。
    """
    d = load()
    raw = d.get("webhook_default_channels")
    if isinstance(raw, list):
        return [c for c in raw if c in REVIEW_PUSH_CHANNELS]
    return []


def get_external_notification_channels() -> list[str] | None:
    """Return globally enabled external channels, or None for legacy per-rule mode."""
    data = load()
    if "external_notification_channels" not in data:
        return None
    raw = data.get("external_notification_channels")
    if not isinstance(raw, list):
        return []
    return list(dict.fromkeys(c for c in raw if c in REVIEW_PUSH_CHANNELS))


def set_external_notification_channels(channels: list[str]) -> list[str]:
    """Save the global external alert destinations; an empty list means App only."""
    cleaned = list(dict.fromkeys(c for c in (channels or []) if c in REVIEW_PUSH_CHANNELS))
    save({"external_notification_channels": cleaned})
    return cleaned


def set_realtime_monitor_config(cfg: dict) -> dict:
    """批次更新即時監控設定。"""
    updates = {}
    if "sse_refresh_pages" in cfg:
        updates["sse_refresh_pages"] = cfg["sse_refresh_pages"]
    if "minute_intraday_refresh" in cfg:
        updates["minute_intraday_refresh"] = bool(cfg["minute_intraday_refresh"])
    if "minute_intraday_refresh_interval" in cfg:
        # clamp 到 [5, 60], 與 getter 一致, 防前端傳越界值
        updates["minute_intraday_refresh_interval"] = max(
            _INTRADAY_REFRESH_INTERVAL_MIN,
            min(_INTRADAY_REFRESH_INTERVAL_MAX, int(cfg["minute_intraday_refresh_interval"])))
    if updates:
        save(updates)
    return get_realtime_monitor_config()


def get_realtime_monitor_config() -> dict:
    """回傳完整的即時監控設定。"""
    return {
        "sse_refresh_pages": get_sse_refresh_pages(),
        "sidebar_index_symbols": get_sidebar_index_symbols(),
        "minute_intraday_refresh": get_minute_intraday_refresh(),
        "minute_intraday_refresh_interval": get_minute_intraday_refresh_interval(),
    }


def get_nav_order() -> list[str]:
    """回傳左側選單的自訂排序（內建頁面 path + 擴展分析選單 id）。"""
    return load().get("nav_order", [])


def set_nav_order(order: list[str]) -> list[str]:
    """儲存左側選單排序。"""
    save({"nav_order": order})
    return get_nav_order()


def get_nav_hidden() -> list[str]:
    """回傳左側選單中隱藏的項 id 列表。"""
    return load().get("nav_hidden", [])


def set_nav_hidden(hidden: list[str]) -> list[str]:
    """儲存左側選單隱藏項。"""
    save({"nav_hidden": hidden})
    return get_nav_hidden()


def get_watchlist_columns() -> list[dict] | None:
    """回傳自選列表列設定。"""
    return load().get("watchlist_columns")


def set_watchlist_columns(columns: list[dict]) -> list[dict]:
    """儲存自選列表列設定。"""
    save({"watchlist_columns": columns})
    return columns


def get_onboarding_completed() -> bool:
    """是否已完成首次使用嚮導。預設 False（新使用者）。"""
    return bool(load().get("onboarding_completed", False))


def set_onboarding_completed(done: bool = True) -> bool:
    """標記首次使用嚮導完成狀態。"""
    save({"onboarding_completed": bool(done)})
    return bool(done)


# ===== 財務資料同步時間(持久化,重啟不丟失) =====
# 結構: { "metrics": "2026-06-25T10:00:00+08:00", "income": ..., ... }

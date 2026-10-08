"""用户偏好设置持久化。

存储位置: data/user_data/preferences.json
沿用 secrets_store 的 merge-write 模式,但不做 chmod 0600 (非敏感数据)。
"""
from __future__ import annotations

import copy
import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# 进程内缓存: 行情轮询线程一轮会调用 8~12 次 getter, 每次读盘+parse 是纯重复;
# 文件仅在用户改设置时变化, 以 (mtime_ns, size) 签名判断是否重读。
_cache: dict | None = None
_cache_sig: tuple[int, int] | None = None
_SAVE_LOCK = threading.RLock()



# 监控规则可选的外部推播渠道。多选: 不推播 = 空数组, 而非 'none'。
REVIEW_PUSH_CHANNELS = {"line", "telegram"}

# 页面 SSE 刷新配置: { "watchlist": true, "monitor": true, ... }
SSE_REFRESH_PAGES_DEFAULT = {
    "overview-market": True,
    "watchlist": True,
}

SIDEBAR_INDEX_SYMBOLS_DEFAULT = ["000001.SH", "399001.SZ", "399006.SZ", "000680.SH"]


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
    """读取 preferences.json (带 mtime 签名缓存)。返回深拷贝, 调用方可自由修改。"""
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
    """合并写入。返回新内容。"""
    with _SAVE_LOCK:
        current = load()
        current.update(updates)
        _path().write_text(
            json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8",
        )
        _invalidate_cache()
        return current


def get_realtime_quotes_enabled() -> bool:
    return load().get("realtime_quotes_enabled", False)


def get_indices_nav_pinned() -> bool:
    """侧栏指数报价卡片是否固定显示。默认 True（常驻）。
    关闭后，卡片跟随实时行情开关（仅实时开时显示）。"""
    return load().get("indices_nav_pinned", True)


def get_watchlist_groups_in_nav() -> bool:
    """自选分组是否显示在侧边栏（可展开二级子菜单）。默认 False。"""
    return load().get("watchlist_groups_in_nav", False)



def get_realtime_quote_interval() -> float:
    return load().get("realtime_quote_interval", 6.0)


def get_realtime_watchlist_symbols() -> list[str]:
    """Free 档自选实时监控标的:直接取自选页前 5 个。"""
    try:
        from app.services import watchlist
        rows = watchlist.list_symbols()
    except Exception as e:  # noqa: BLE001
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


def set_realtime_watchlist_symbols(symbols: list[str]) -> list[str]:  # noqa: ARG001
    """兼容旧接口: Free 实时标的现在由自选页前 5 个决定。"""
    return get_realtime_watchlist_symbols()


def set_realtime_quote_interval(interval: float) -> float:
    """保存行情轮询间隔（不在此做 min/max 校验，由调用方按档位限制）。"""
    save({"realtime_quote_interval": interval})
    return interval


def get_minute_sync_enabled() -> bool:
    return load().get("minute_sync_enabled", False)


def get_minute_intraday_refresh() -> bool:
    """自选列表分时图是否跟随实时行情刷新。

    默认值随权限: 有实时行情权限 (Pro+) 的用户默认开启, 否则关闭。
    用户主动设置过的 (key 存在) 以用户选择为准, 即使是 False 也尊重。
    """
    data = load()
    if "minute_intraday_refresh" in data:
        return bool(data["minute_intraday_refresh"])
    # 未设置过: 有权限默认开, 无权限默认关。
    try:
        from app.services.quote_service import QuoteService
        return QuoteService.is_realtime_allowed()
    except Exception:
        return False


# 分时图实时刷新间隔允许范围 (秒)。下限 3s, 上限 60s。
_INTRADAY_REFRESH_INTERVAL_MIN = 3
_INTRADAY_REFRESH_INTERVAL_MAX = 60


def get_minute_intraday_refresh_interval() -> int:
    """分时图实时刷新轮询间隔 (秒)。默认 6s, 范围 [3, 60]。"""
    return max(_INTRADAY_REFRESH_INTERVAL_MIN,
               min(_INTRADAY_REFRESH_INTERVAL_MAX,
                   int(load().get("minute_intraday_refresh_interval", 6))))


# 监控中心个股通知 ext 字段默认配置 (与 ext_presets 内置预设对齐)
_MONITOR_EXT_FIELDS_DEFAULT = {
    "concept": "ext_gn_ths.所属概念",
    "industry": "ext_hy_ths.所属同话顺行业",
}



def get_minute_sync_days() -> int:
    return max(1, min(30, load().get("minute_sync_days", 5)))


def get_minute_sync_segment_days() -> int:
    """分钟 K 拉取的单段大小(交易日)。默认 20,范围 [5, 30]。

    每段拉完后立即落盘(流式),避免全量攒内存导致 OOM。
    段越小内存峰值越低但总耗时越长(限速 sleep 随段数线性增加);
    物理上限 ~41 交易日(TickFlow 单次 10000 根 / 一天 241 根 ≈ 41 天),max=30 留出余量。
    """
    return max(5, min(30, load().get("minute_sync_segment_days", 20)))


# ===== 数据源选择 (默认 TickFlow；第一阶段仅日K切换入口) =====

_ALLOWED_DATA_PROVIDERS = {"taiwan", "tickflow"}
DATA_SOURCE_JOB_TIMEOUT_MIN_S = 60


def get_data_source_job_timeout_s() -> int:
    """返回普通数据后台任务的卡死判定时间(秒)。"""
    from app.services.pipeline_jobs import DEFAULT_JOB_TIMEOUT_S
    raw = load().get("data_source_job_timeout_s", DEFAULT_JOB_TIMEOUT_S)
    try:
        timeout_s = int(raw)
    except (TypeError, ValueError):
        timeout_s = DEFAULT_JOB_TIMEOUT_S
    return max(DATA_SOURCE_JOB_TIMEOUT_MIN_S, timeout_s)


def get_data_source_long_job_timeout_s() -> int:
    """返回分钟 K 全市场等长任务的卡死判定时间(秒)。"""
    from app.services.pipeline_jobs import LONG_JOB_TIMEOUT_S
    raw = load().get(
        "data_source_long_job_timeout_s",
        LONG_JOB_TIMEOUT_S,
    )
    try:
        timeout_s = int(raw)
    except (TypeError, ValueError):
        timeout_s = LONG_JOB_TIMEOUT_S
    return max(DATA_SOURCE_JOB_TIMEOUT_MIN_S, timeout_s)


def _allowed_data_providers() -> set[str]:
    try:
        from app.data_providers import custom as custom_sources
        return _ALLOWED_DATA_PROVIDERS | custom_sources.names()
    except Exception:  # noqa: BLE001
        return set(_ALLOWED_DATA_PROVIDERS)


def get_daily_data_provider() -> str:
    provider = str(load().get("daily_data_provider", "taiwan") or "taiwan").lower()
    if provider == "tickflow":
        return "taiwan"
    return provider if provider in _allowed_data_providers() else "taiwan"


def get_adj_factor_provider() -> str:
    provider = str(load().get("adj_factor_provider", "same_as_daily") or "same_as_daily").lower()
    if provider == "same_as_daily":
        return provider
    return provider if provider in _allowed_data_providers() else "same_as_daily"


def get_minute_data_provider() -> str:
    provider = str(load().get("minute_data_provider", "taiwan") or "taiwan").lower()
    if provider == "tickflow":
        return "taiwan"
    return provider if provider in _allowed_data_providers() else "taiwan"


def get_realtime_data_provider() -> str:
    provider = str(load().get("realtime_data_provider", "taiwan") or "taiwan").lower()
    if provider == "tickflow":
        return "taiwan"
    return provider if provider in _allowed_data_providers() else "taiwan"


def get_financial_provider() -> str:
    provider = str(load().get("financial_data_provider", "taiwan") or "taiwan").lower()
    if provider == "tickflow":
        return "taiwan"
    return provider if provider in _allowed_data_providers() else "taiwan"


# ===== 盘后管道拉取内容开关 (A股 / ETF / 指数 独立控制) =====






















def get_enriched_batch_size() -> int:
    """返回 enriched 全量计算每批 symbol 数量。"""
    return max(1, min(10000, load().get("enriched_batch_size", 1000)))


def set_enriched_batch_size(size: int) -> int:
    """保存 enriched 全量计算批次大小。"""
    size = max(10, min(6000, size))
    save({"enriched_batch_size": size})
    return size


def get_index_daily_batch_size() -> int:
    """返回指数日 K 同步每批 symbol 数量。"""
    return max(1, min(10000, load().get("index_daily_batch_size", 100)))


def set_index_daily_batch_size(size: int) -> int:
    """保存指数日 K 同步批次大小。"""
    size = max(1, min(10000, size))
    save({"index_daily_batch_size": size})
    return size


# ── 五档盘口 sealed(真假涨停) 配置 ──────────────────────







def get_realtime_pull_stock() -> bool:
    return load().get("realtime_pull_stock", True)


def get_realtime_pull_etf() -> bool:
    # 老用户兼容: ETF 实时默认关闭，避免升级后请求量/写盘量突然增加。
    return load().get("realtime_pull_etf", False)


def get_realtime_pull_index() -> bool:
    return load().get("realtime_pull_index", True)


def get_realtime_index_mode() -> str:
    mode = str(load().get("realtime_index_mode", "core") or "core").lower()
    return mode if mode in {"core", "all"} else "core"


def get_realtime_index_symbols() -> list[str]:
    stored = load().get("realtime_index_symbols", SIDEBAR_INDEX_SYMBOLS_DEFAULT)
    if isinstance(stored, str):
        import re
        stored = [s.strip() for s in re.split(r"[,\s]+", stored) if s.strip()]
    return [str(s) for s in stored if str(s).strip()]


def set_realtime_quote_scope(cfg: dict) -> dict:
    updates = {}
    for key in ("realtime_pull_stock", "realtime_pull_etf", "realtime_pull_index"):
        if key in cfg and cfg[key] is not None:
            updates[key] = bool(cfg[key])
    if "realtime_index_mode" in cfg and cfg["realtime_index_mode"] in {"core", "all"}:
        updates["realtime_index_mode"] = cfg["realtime_index_mode"]
    if "realtime_index_symbols" in cfg and cfg["realtime_index_symbols"] is not None:
        updates["realtime_index_symbols"] = cfg["realtime_index_symbols"]
    if updates:
        save(updates)
    return get_realtime_quote_scope()


def get_realtime_quote_scope() -> dict:
    return {
        "realtime_pull_stock": get_realtime_pull_stock(),
        "realtime_pull_etf": get_realtime_pull_etf(),
        "realtime_pull_index": get_realtime_pull_index(),
        "realtime_index_mode": get_realtime_index_mode(),
        "realtime_index_symbols": get_realtime_index_symbols(),
    }


def get_sse_refresh_pages() -> dict[str, bool]:
    """返回每个页面的 SSE 刷新开关。"""
    stored = load().get("sse_refresh_pages", {})
    # 合并默认值 (新增页面自动出现)
    result = dict(SSE_REFRESH_PAGES_DEFAULT)
    result.update({key: value for key, value in stored.items() if key in result})
    return result


def set_sse_refresh_pages(pages: dict[str, bool]) -> dict[str, bool]:
    """保存页面 SSE 刷新配置。"""
    save({"sse_refresh_pages": pages})
    return get_sse_refresh_pages()


def get_sidebar_index_symbols() -> list[str]:
    """返回左侧菜单显示的指数代码。"""
    stored = load().get("sidebar_index_symbols", SIDEBAR_INDEX_SYMBOLS_DEFAULT)
    allowed = set(SIDEBAR_INDEX_SYMBOLS_DEFAULT)
    return [s for s in stored if s in allowed]



def get_system_notify_enabled() -> bool:
    """系统通知开关 — 开启后监控告警同时推送到操作系统通知中心。"""
    return load().get("system_notify_enabled", False)


def set_system_notify_enabled(enabled: bool) -> bool:
    """保存系统通知开关。"""
    save({"system_notify_enabled": bool(enabled)})
    return bool(enabled)


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
    """新建监控规则时是否默认勾选推送 (老布尔, 已由 webhook_default_channels 取代)。

    保留向后兼容: 读取 webhook_default_channels 非空时返回 True。
    """
    return bool(get_webhook_default_channels())


def set_webhook_enabled_default(enabled: bool) -> bool:
    """保存推送默认勾选态 (老布尔兼容入口)。

    舊布林值無法安全推斷新的 LINE/Telegram 收件者; True/False 都不自動啟用新渠道。
    """
    set_webhook_default_channels([])
    return get_webhook_enabled_default()


def get_webhook_default_channels() -> list[str]:
    """新建监控规则时默认勾选的推送渠道 (多选)。

    空列表 = 新建规则默认不推送; ['line'] = 默认推播 LINE。
    此默认值供规则编辑器新建规则时预填, 单条规则仍可独立修改。

    舊版飛書/企業微信渠道與布林值只做安全過濾,不刪除原始偏好資料,
    也不自動映射到新的收件者。
    """
    d = load()
    raw = d.get("webhook_default_channels")
    if isinstance(raw, list):
        return [c for c in raw if c in REVIEW_PUSH_CHANNELS]
    return []


def set_webhook_default_channels(channels: list[str]) -> list[str]:
    """保存新建规则默认推送渠道 (多选)。过滤白名单外、去重、保序。空列表 = 不推送。"""
    seen: set[str] = set()
    cleaned: list[str] = []
    for c in channels or []:
        if c in REVIEW_PUSH_CHANNELS and c not in seen:
            seen.add(c)
            cleaned.append(c)
    save({"webhook_default_channels": cleaned})
    return cleaned


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
    """批量更新实时监控配置。"""
    updates = {}
    if "sse_refresh_pages" in cfg:
        updates["sse_refresh_pages"] = cfg["sse_refresh_pages"]
    if "sidebar_index_symbols" in cfg:
        allowed = set(SIDEBAR_INDEX_SYMBOLS_DEFAULT)
        updates["sidebar_index_symbols"] = [s for s in cfg["sidebar_index_symbols"] if s in allowed]
    if "minute_intraday_refresh" in cfg:
        updates["minute_intraday_refresh"] = bool(cfg["minute_intraday_refresh"])
    if "minute_intraday_refresh_interval" in cfg:
        # clamp 到 [5, 60], 与 getter 一致, 防前端传越界值
        updates["minute_intraday_refresh_interval"] = max(
            _INTRADAY_REFRESH_INTERVAL_MIN,
            min(_INTRADAY_REFRESH_INTERVAL_MAX, int(cfg["minute_intraday_refresh_interval"])))
    if updates:
        save(updates)
    return get_realtime_monitor_config()


def get_realtime_monitor_config() -> dict:
    """返回完整的实时监控配置。"""
    return {
        "sse_refresh_pages": get_sse_refresh_pages(),
        "sidebar_index_symbols": get_sidebar_index_symbols(),
        "minute_intraday_refresh": get_minute_intraday_refresh(),
        "minute_intraday_refresh_interval": get_minute_intraday_refresh_interval(),
    }


def get_nav_order() -> list[str]:
    """返回左侧菜单的自定义排序（内置页面 path + 扩展分析菜单 id）。"""
    return load().get("nav_order", [])


def set_nav_order(order: list[str]) -> list[str]:
    """保存左侧菜单排序。"""
    save({"nav_order": order})
    return get_nav_order()


def get_nav_hidden() -> list[str]:
    """返回左侧菜单中隐藏的项 id 列表。"""
    return load().get("nav_hidden", [])


def set_nav_hidden(hidden: list[str]) -> list[str]:
    """保存左侧菜单隐藏项。"""
    save({"nav_hidden": hidden})
    return get_nav_hidden()


def get_watchlist_columns() -> list[dict] | None:
    """返回自选列表列配置。"""
    return load().get("watchlist_columns")


def set_watchlist_columns(columns: list[dict]) -> list[dict]:
    """保存自选列表列配置。"""
    save({"watchlist_columns": columns})
    return columns




def get_onboarding_completed() -> bool:
    """是否已完成首次使用向导。默认 False（新用户）。"""
    return bool(load().get("onboarding_completed", False))


def set_onboarding_completed(done: bool = True) -> bool:
    """标记首次使用向导完成状态。"""
    save({"onboarding_completed": bool(done)})
    return bool(done)


# ===== 财务数据同步时间(持久化,重启不丢失) =====
# 结构: { "metrics": "2026-06-25T10:00:00+08:00", "income": ..., ... }

def get_financial_sync_times() -> dict[str, str]:
    """返回各财务表的最后同步时间(ISO 字符串)。未同步过的表不在返回值中。"""
    return load().get("financial_sync_times", {}) or {}


def set_financial_sync_time(table: str, iso_ts: str) -> None:
    """更新单张财务表的最后同步时间(合并写入,不清除其他表)。"""
    times = get_financial_sync_times()
    times[table] = iso_ts
    save({"financial_sync_times": times})

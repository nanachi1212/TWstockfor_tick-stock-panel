"""自選股盤中「異常摘要」: 不用自己設規則，盤中定期掃一次自選股。

條件 (純規則、只用即時報價事實):
- 漲跌幅絕對值 ≥ 3% (可調)。
- 同一股票同一天只提醒一次 (方向改變才會再提醒一次)。

結果走既有提醒管線: alert_store 落盤 → SSE 廣播到看板「提醒」區 → 依全域通道推播。
預設關閉；資料來源不是盤中即時報價時 (收盤快照、延遲) 不觸發，避免誤報。
"""
# ruff: noqa: RUF001, RUF002 -- user-facing Traditional Chinese text is intentional.
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from typing import Any

from app.services import alert_store, preferences, webhook_adapter

logger = logging.getLogger(__name__)

_PREF_ENABLED = "watchlist_anomaly_enabled"
_PREF_THRESHOLD = "watchlist_anomaly_threshold_pct"  # 單位: 百分比數值, 3 = 3%
_DEFAULT_THRESHOLD_PCT = 3.0
_MIN_THRESHOLD_PCT, _MAX_THRESHOLD_PCT = 1.0, 10.0
# (trade_date, symbol) → direction already notified today (重啟後由已落盤提醒重建)
_NOTIFIED: dict[tuple[date, str], str] = {}
_RESTORED_DATE: date | None = None
_LIVE_SOURCES = {"twse_mis", "mis", "fugle"}


def is_enabled() -> bool:
    return bool(preferences.load().get(_PREF_ENABLED, False))


def set_enabled(enabled: bool) -> bool:
    preferences.save({_PREF_ENABLED: bool(enabled)})
    return is_enabled()


def threshold_pct() -> float:
    raw = preferences.load().get(_PREF_THRESHOLD, _DEFAULT_THRESHOLD_PCT)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = _DEFAULT_THRESHOLD_PCT
    return max(_MIN_THRESHOLD_PCT, min(_MAX_THRESHOLD_PCT, value))


def set_threshold_pct(value: float) -> float:
    clamped = max(_MIN_THRESHOLD_PCT, min(_MAX_THRESHOLD_PCT, float(value)))
    preferences.save({_PREF_THRESHOLD: clamped})
    return threshold_pct()


def reset_memory() -> None:
    global _RESTORED_DATE
    _NOTIFIED.clear()
    _RESTORED_DATE = None


def _restore_today(data_dir: Any, today: date, tz: Any) -> None:
    """重啟後從 alerts.jsonl 重建當日已提醒 (symbol, direction), 避免重複推播。"""
    try:
        for ev in alert_store.list_recent(data_dir, days=1, source="watchlist_anomaly"):
            ts = ev.get("ts")
            if not isinstance(ts, (int, float)):
                continue
            if datetime.fromtimestamp(ts / 1000, tz=tz).date() != today:
                continue
            symbol = str(ev.get("symbol") or "")
            pct = ev.get("change_pct")
            if symbol and isinstance(pct, (int, float)):
                _NOTIFIED.setdefault((today, symbol), "up" if pct > 0 else "down")
    except Exception:
        logger.debug("watchlist anomaly: restore of today's dedup state failed", exc_info=True)


def _session_status(service: Any, now: datetime) -> str:
    """用即時服務自己的交易日曆 (會被第一方報價驗證), 不用從未填入的模組預設日曆。"""
    from app.taiwan.realtime import get_market_status

    cal = getattr(service, "trading_calendar", None)
    if cal is not None and hasattr(cal, "get_market_status"):
        return str(cal.get_market_status(now, require_verified_trading_day=True).value)
    return str(get_market_status(now, require_verified_trading_day=True).value)


def _is_live(quote: Any) -> bool:
    meta = getattr(quote, "source_meta", None)
    source = str(getattr(meta, "source", "") or getattr(meta, "provider", "") or "").lower()
    if getattr(meta, "is_stale", False):
        return False
    return any(key in source for key in _LIVE_SOURCES)


def _event(quote: Any, pct: float, direction: str, now_iso: str, ts_ms: int) -> dict[str, Any]:
    arrow = "大漲" if direction == "up" else "大跌"
    return {
        "alert_id": f"alert_{uuid.uuid4().hex}",
        "source": "watchlist_anomaly",
        "type": "watchlist_anomaly",
        "rule_id": None,
        "rule_name": "自選異常摘要",
        "symbol": quote.symbol,
        "name": quote.name,
        "message": f"自選股{arrow} {pct:+.2f}%，現價 {quote.last_price}",
        "price": quote.last_price,
        "change_pct": quote.change_pct,
        "trigger_value": quote.last_price,
        "threshold": threshold_pct(),
        "signals": [],
        "severity": "warn" if abs(pct) >= threshold_pct() * 2 else "info",
        "triggered_at": now_iso,
        "quote_time": quote.quote_time.isoformat() if getattr(quote, "quote_time", None) else None,
        "notify_channels": [],
        # alert_store 契約: ts 為毫秒 epoch (list_recent/prune 以此比較)
        "ts": ts_ms,
    }


def scan(app_state: Any | None = None) -> dict[str, Any]:
    """掃一輪自選股；回傳摘要供 log 與測試使用。"""
    global _RESTORED_DATE
    from app.services import watchlist
    from app.taiwan.realtime import get_realtime_service, taipei_now

    if not is_enabled():
        return {"status": "disabled"}
    now = taipei_now()
    service = get_realtime_service()
    # 開盤前/收盤後/非交易日直接略過; 「排定開盤但尚未驗證」要先取報價才能驗證, 所以放行到下一步。
    if _session_status(service, now) not in {"open", "scheduled_open_unverified"}:
        return {"status": "market_closed"}
    symbols = [
        str(row.get("symbol") or "")
        for row in watchlist.list_symbols()
        if str(row.get("symbol") or "").upper().endswith((".TWSE", ".TPEX"))
    ]
    if not symbols:
        return {"status": "no_watchlist"}
    quotes = service.get_quotes(symbols)
    # 第一方報價進來後日曆才會把今天標成已驗證交易日; 假日/臨時休市在此 fail-closed。
    if _session_status(service, now) != "open":
        return {"status": "market_unverified"}

    data_dir = getattr(getattr(getattr(app_state, "repo", None), "store", None), "data_dir", None)
    if data_dir is None:
        from app.config import settings

        data_dir = settings.data_dir
    today = now.date()
    if today != _RESTORED_DATE:
        _NOTIFIED.clear()
        _restore_today(data_dir, today, now.tzinfo)
        _RESTORED_DATE = today

    limit = threshold_pct()
    now_iso = now.isoformat(timespec="seconds")
    ts_ms = int(now.timestamp() * 1000)
    pending: list[tuple[tuple[date, str], str, dict[str, Any]]] = []
    for symbol in symbols:
        q = quotes.get(symbol)
        if q is None or q.change_pct is None or not _is_live(q):
            continue
        # 即時報價契約: change_pct 已是百分點 (4.5 = 4.5%), 不再換算
        pct = float(q.change_pct)
        if abs(pct) < limit:
            continue
        direction = "up" if pct > 0 else "down"
        key = (today, symbol)
        if _NOTIFIED.get(key) == direction:
            continue
        pending.append((key, direction, _event(q, pct, direction, now_iso, ts_ms)))
    if not pending:
        return {"status": "ok", "flagged": 0}

    events = [ev for _, _, ev in pending]
    alert_store.append_many(data_dir, events)
    # 落盤成功後才記為「今天已提醒」; 寫入失敗則下一輪重試
    for key, direction, _ in pending:
        _NOTIFIED[key] = direction
    qs = getattr(app_state, "quote_service", None)
    if qs is not None and hasattr(qs, "_broadcast_alerts"):
        try:
            qs._broadcast_alerts([
                {
                    "source": ev["source"], "type": ev["type"], "rule_id": None, "strategy_id": None,
                    "symbol": ev["symbol"], "name": ev["name"], "message": ev["message"],
                    "price": ev["price"], "change_pct": ev["change_pct"], "signals": [],
                    "severity": ev["severity"], "conditions": [], "logic": "and",
                }
                for ev in events
            ])
        except Exception:
            logger.debug("watchlist anomaly SSE broadcast failed", exc_info=True)
    _push(events)
    return {"status": "ok", "flagged": len(events), "symbols": [e["symbol"] for e in events]}


def _push(events: list[dict[str, Any]]) -> None:
    channels = [c for c in (preferences.get_external_notification_channels() or []) if c in ("line", "telegram")]
    if not channels:
        return
    lines = ["【自選異常摘要】"]
    for ev in events[:10]:
        lines.append(f"• {ev['name']}（{str(ev['symbol']).split('.')[0]}）{ev['message'].split('，')[0].replace('自選股', '')}，現價 {ev['price']}")
    if len(events) > 10:
        lines.append(f"…另有 {len(events) - 10} 檔")
    lines.append("規則整理，非投資建議。")
    body = "\n".join(lines)
    if "line" in channels:
        webhook_adapter.send_line(preferences.get_line_channel_access_token(), preferences.get_line_target_id(), "", body)
    if "telegram" in channels:
        webhook_adapter.send_telegram(preferences.get_telegram_bot_token(), preferences.get_telegram_chat_id(), "", body)

"""早晚各一則「今日一句話」推播 (LINE / Telegram)。

- 盤前 (預設 08:45): 今天大盤偏強／偏弱、今日候選前幾檔與其計畫價位。
- 盤後 (16:30 資料更新成功後): 自選股今天怎麼了 (漲跌幅排序)。

只重組既有規則結果 (BeginnerSelectionService、台股即時報價、全域外送通道)，
不呼叫 AI、不產生新價位；資料不足時如實說明，不補 0。預設關閉。
"""
# ruff: noqa: RUF001, RUF002 -- user-facing Traditional Chinese text is intentional.
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from app.services import preferences, webhook_adapter

logger = logging.getLogger(__name__)

_PREF_ENABLED = "push_digest_enabled"
_PREF_LAST = "push_digest_last_run"
_MAX_PICKS = 3
_MAX_WATCHLIST = 10
_FOOTER = "規則整理，非投資建議。"


def is_enabled() -> bool:
    return bool(preferences.load().get(_PREF_ENABLED, False))


def set_enabled(enabled: bool) -> bool:
    preferences.save({_PREF_ENABLED: bool(enabled)})
    return is_enabled()


def last_run() -> dict[str, Any] | None:
    value = preferences.load().get(_PREF_LAST)
    return value if isinstance(value, dict) else None


def _channels() -> list[str]:
    channels = preferences.get_external_notification_channels() or []
    return [c for c in channels if c in ("line", "telegram")]


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}" if value < 1000 else f"{value:.0f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:+.2f}%"


_STATE_LABEL = {
    "observable": "可觀察",
    "wait_pullback": "等拉回",
    "wait_breakout": "等突破",
    "avoid": "不追",
}


def build_morning_text() -> str:
    """盤前一句話: 市場強弱 + 今日候選前幾檔。"""
    from app.taiwan.beginner_selection import BeginnerSelectionService

    response = BeginnerSelectionService().build(limit=_MAX_PICKS)
    market = response.market
    lines = [f"【今日一句話】{market.as_of or ''}".rstrip(), market.headline]
    if market.advance_count is not None and market.decline_count is not None:
        lines.append(f"漲 {market.advance_count} 家／跌 {market.decline_count} 家")
    if market.strongest_industries:
        lines.append("最強產業：" + "、".join(market.strongest_industries[:3]))
    if response.status == "unavailable":
        lines.append("今日選股資料不足，暫無候選。")
    elif not response.candidates:
        lines.append("今日沒有符合規則的候選。")
    else:
        lines.append("今日候選：")
        for c in response.candidates[:_MAX_PICKS]:
            state = _STATE_LABEL.get(str(c.selection_state), str(c.selection_state))
            plan = c.trade_plan
            if plan is None:
                levels = "計畫價位不足"
            elif plan.entry_semantics == "breakout_stop":
                levels = f"突破 {_fmt_price(plan.breakout_trigger)}／失效 {_fmt_price(plan.stop_price)}"
            else:
                levels = (
                    f"承接 {_fmt_price(plan.entry_zone_low)}～{_fmt_price(plan.entry_zone_high)}"
                    f"／失效 {_fmt_price(plan.stop_price)}"
                )
            lines.append(f"• {c.name}（{c.symbol.split('.')[0]}）{state}，{levels}")
    lines.append(_FOOTER)
    return "\n".join(lines)


def build_evening_text(as_of: str | None = None) -> str:
    """盤後一句話: 自選股今天怎麼了 (以官方收盤快照為準)。"""
    from app.services import watchlist
    from app.taiwan.realtime import get_realtime_service

    symbols = [
        str(row.get("symbol") or "")
        for row in watchlist.list_symbols()
        if str(row.get("symbol") or "").upper().endswith((".TWSE", ".TPEX"))
    ]
    lines = [f"【收盤一句話】{as_of or date.today().isoformat()}"]
    if not symbols:
        lines.append("你還沒有加入自選股。到「今日選股」卡片一鍵加入後，明天收盤就會收到摘要。")
        lines.append(_FOOTER)
        return "\n".join(lines)
    quotes = get_realtime_service().get_quotes(symbols[:_MAX_WATCHLIST * 3])
    rows: list[tuple[str, str, float | None, float | None]] = []
    missing: list[str] = []
    for symbol in symbols:
        q = quotes.get(symbol)
        if q is None or q.last_price is None:
            missing.append(symbol.split(".")[0])
            continue
        rows.append((q.name or symbol, symbol.split(".")[0], q.change_pct, q.last_price))
    rows.sort(key=lambda r: (r[2] is None, -(r[2] or 0.0)))
    if rows:
        up = sum(1 for r in rows if (r[2] or 0) > 0)
        down = sum(1 for r in rows if (r[2] or 0) < 0)
        lines.append(f"自選 {len(rows)} 檔：漲 {up}、跌 {down}")
        for name, code, pct, price in rows[:_MAX_WATCHLIST]:
            lines.append(f"• {name}（{code}）{_fmt_pct(pct)}，收 {_fmt_price(price)}")
        if len(rows) > _MAX_WATCHLIST:
            lines.append(f"…其餘 {len(rows) - _MAX_WATCHLIST} 檔請開看板查看")
    if missing:
        lines.append("今日無報價：" + "、".join(missing[:8]))
    lines.append(_FOOTER)
    return "\n".join(lines)


def _send(text: str) -> list[str]:
    channels = _channels()
    sent: list[str] = []
    if "line" in channels and webhook_adapter.send_line(
        preferences.get_line_channel_access_token(), preferences.get_line_target_id(), "", text,
    ):
        sent.append("line")
    if "telegram" in channels and webhook_adapter.send_telegram(
        preferences.get_telegram_bot_token(), preferences.get_telegram_chat_id(), "", text,
    ):
        sent.append("telegram")
    return sent


def run(kind: str, as_of: str | None = None) -> dict[str, Any]:
    """kind = 'morning' | 'evening'。回傳執行結果並記錄到偏好供設定頁顯示。"""
    from app.taiwan.realtime import taipei_now

    result: dict[str, Any] = {"kind": kind, "ran_at": taipei_now().isoformat(timespec="seconds")}
    if not is_enabled():
        result["status"] = "disabled"
        return result
    if not _channels():
        result["status"] = "no_channels"
        preferences.save({_PREF_LAST: result})
        return result
    try:
        text = build_morning_text() if kind == "morning" else build_evening_text(as_of)
        sent = _send(text)
        result.update(status="sent" if sent else "not_sent", channels=sent, preview=text[:120])
    except Exception as exc:
        logger.warning("push digest %s failed: %s", kind, exc)
        result.update(status="error", reason=type(exc).__name__)
    preferences.save({_PREF_LAST: result})
    return result

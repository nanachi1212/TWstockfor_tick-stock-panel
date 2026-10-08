"""Telegram 反向查詢: 在手機輸入股票代號，回傳現價、今日計畫價位與已保存的 AI 一句話。

- 背景執行緒用 Bot API getUpdates 長輪詢；只回應「設定 → 即時監控」填的 chat_id。
- 指令: 代號 (如 2330)、/picks 今日候選、/watch 自選股、/help。
- 只讀既有資料 (即時報價、BeginnerSelectionService、已保存 AI 研究)；不會因為訊息呼叫 LLM。
- 預設關閉；Token 或 chat_id 未設定時不啟動。
"""
# ruff: noqa: RUF001, RUF002 -- user-facing Traditional Chinese text is intentional.
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

import httpx

from app.services import preferences, webhook_adapter

logger = logging.getLogger(__name__)

_PREF_ENABLED = "telegram_query_enabled"
_CODE_RE = re.compile(r"^\s*/?([0-9]{4,6}[A-Za-z]?)\s*$")
_POLL_TIMEOUT_S = 25
_HELP = (
    "可用指令：\n"
    "• 輸入代號 (例 2330) → 現價、今日計畫價位、AI 一句話\n"
    "• /picks → 今日候選前 5 檔\n"
    "• /watch → 自選股現價\n"
    "• /help → 這份說明\n"
    "規則整理，非投資建議。"
)

_STATE_LABEL = {"observable": "可觀察", "wait_pullback": "等拉回", "wait_breakout": "等突破", "avoid": "不追"}


def is_enabled() -> bool:
    return bool(preferences.load().get(_PREF_ENABLED, False))


def set_enabled(enabled: bool) -> bool:
    preferences.save({_PREF_ENABLED: bool(enabled)})
    return is_enabled()


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}" if value < 1000 else f"{value:.0f}"


def _fmt_pct(value: float | None) -> str:
    # 即時報價契約: change_pct 已是百分點 (1.2 = +1.20%), 直接格式化
    return "—" if value is None else f"{value:+.2f}%"


def _resolve_symbol(code: str) -> str | None:
    from app.taiwan.universe import get_security_master

    hits = get_security_master().search(code, limit=5)
    for hit in hits:
        if str(hit.get("code") or "").upper() == code.upper():
            return str(hit["symbol"])
    return str(hits[0]["symbol"]) if hits else None


def answer_symbol(code: str) -> str:
    """單一股票: 現價 + 新手計畫 + 已保存 AI 一句話。"""
    from app.taiwan.auto_ai_explain import stored_explanation
    from app.taiwan.beginner_selection import BeginnerSelectionService
    from app.taiwan.realtime import get_realtime_service

    symbol = _resolve_symbol(code)
    if symbol is None:
        return f"找不到代號 {code}。請確認是上市或上櫃股票／ETF。"
    lines: list[str] = []
    candidate_as_of: str | None = None
    quote = get_realtime_service().get_quotes([symbol]).get(symbol)
    if quote is not None and quote.last_price is not None:
        when = quote.quote_time.strftime("%m/%d %H:%M") if quote.quote_time else ""
        lines.append(f"{quote.name}（{code}）現價 {_fmt_price(quote.last_price)}（{_fmt_pct(quote.change_pct)}）{when}")
    else:
        lines.append(f"{symbol}：目前沒有報價資料。")
    try:
        candidate = BeginnerSelectionService().evaluate_symbol(symbol).candidate
        candidate_as_of = getattr(candidate, "as_of", None)
        state = _STATE_LABEL.get(str(candidate.selection_state), str(candidate.selection_state))
        lines.append(f"規則判斷：{state}。{candidate.action_summary}")
        plan = candidate.trade_plan
        if plan is not None:
            if plan.entry_semantics == "breakout_stop":
                lines.append(f"突破價 {_fmt_price(plan.breakout_trigger)}／失效位 {_fmt_price(plan.stop_price)}")
            else:
                lines.append(
                    f"承接區 {_fmt_price(plan.entry_zone_low)}～{_fmt_price(plan.entry_zone_high)}"
                    f"／失效位 {_fmt_price(plan.stop_price)}"
                )
    except Exception as exc:
        logger.debug("telegram query: selection unavailable for %s: %s", symbol, type(exc).__name__)
        lines.append("今日計畫資料不足。")
    try:
        # 只接受證據日期不早於今日規則結果的 AI 說明; 沒有規則結果就不附 AI (避免舊研究配新報價)
        stored = stored_explanation(symbol, candidate_as_of) if candidate_as_of else None
        report = (stored or {}).get("report") if isinstance(stored, dict) else None
        if isinstance(report, dict):
            answer = report.get("beginner_answer") or {}
            one_liner = (answer.get("can_buy") if isinstance(answer, dict) else None) or report.get("overview")
            if one_liner:
                as_of = str(report.get("evidence_as_of") or candidate_as_of or "")
                lines.append(f"AI 一句話（資料截至 {as_of}）：{str(one_liner)[:120]}" if as_of else f"AI 一句話：{str(one_liner)[:120]}")
    except Exception:
        pass
    lines.append("規則整理，非投資建議。")
    return "\n".join(lines)


def answer_picks() -> str:
    from app.taiwan.beginner_selection import BeginnerSelectionService

    response = BeginnerSelectionService().build(limit=5)
    lines = [response.market.headline]
    if not response.candidates:
        lines.append("今日沒有符合規則的候選。")
    for c in response.candidates[:5]:
        state = _STATE_LABEL.get(str(c.selection_state), str(c.selection_state))
        lines.append(f"• {c.name}（{c.symbol.split('.')[0]}）{state}")
    lines.append("輸入代號可看計畫價位。")
    return "\n".join(lines)


def answer_watchlist() -> str:
    from app.services import watchlist
    from app.taiwan.realtime import get_realtime_service

    symbols = [
        str(row.get("symbol") or "") for row in watchlist.list_symbols()
        if str(row.get("symbol") or "").upper().endswith((".TWSE", ".TPEX"))
    ][:20]
    if not symbols:
        return "你還沒有加入自選股。"
    quotes = get_realtime_service().get_quotes(symbols)
    lines = ["自選股現價："]
    for symbol in symbols:
        q = quotes.get(symbol)
        if q is None or q.last_price is None:
            lines.append(f"• {symbol.split('.')[0]} 無報價")
        else:
            lines.append(f"• {q.name}（{symbol.split('.')[0]}）{_fmt_price(q.last_price)}（{_fmt_pct(q.change_pct)}）")
    return "\n".join(lines)


def handle_text(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return _HELP
    lowered = text.lower()
    if lowered in {"/help", "/start", "help", "幫助", "說明"}:
        return _HELP
    if lowered in {"/picks", "今日選股", "候選"}:
        return answer_picks()
    if lowered in {"/watch", "/watchlist", "自選", "自選股"}:
        return answer_watchlist()
    match = _CODE_RE.match(text)
    if match:
        return answer_symbol(match.group(1).upper())
    return "看不懂這個指令。\n" + _HELP


class TelegramQueryBot:
    """單執行緒長輪詢；start()/stop() 由 lifespan 控制。"""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._offset: int | None = None
        self._offset_token: str | None = None

    def start(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            if not self._stop.is_set():
                return False
            # 剛 stop() 又 start(): 等舊的長輪詢結束再換新執行緒, 否則新設定永遠不會被接起
            self._thread.join(timeout=_POLL_TIMEOUT_S + 15)
        if not (preferences.get_telegram_bot_token() and preferences.get_telegram_chat_id()):
            logger.info("telegram query bot not started: token or chat_id missing")
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="telegram-query-bot", daemon=True)
        self._thread.start()
        logger.info("telegram query bot started")
        return True

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        backoff = 2.0
        while not self._stop.is_set():
            if not is_enabled():
                time.sleep(5.0)
                continue
            token = preferences.get_telegram_bot_token()
            chat_id = str(preferences.get_telegram_chat_id() or "").strip()
            if not token or not chat_id:
                time.sleep(10.0)
                continue
            if token != self._offset_token:
                # 換了 bot token 就是另一個 bot, 舊 offset 無意義 (會跳過新 bot 的訊息)
                self._offset = None
                self._offset_token = token
            try:
                params: dict[str, Any] = {"timeout": _POLL_TIMEOUT_S, "allowed_updates": '["message"]'}
                if self._offset is not None:
                    params["offset"] = self._offset
                response = httpx.get(
                    f"{webhook_adapter.TELEGRAM_API_ROOT}/bot{token}/getUpdates",
                    params=params, timeout=_POLL_TIMEOUT_S + 10,
                )
                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}")
                payload = response.json()
                for update in payload.get("result", []) or []:
                    self._offset = int(update.get("update_id", 0)) + 1
                    message = update.get("message") or {}
                    sender_chat = str((message.get("chat") or {}).get("id") or "")
                    if sender_chat != chat_id:
                        continue  # 只回應設定的對象
                    reply = handle_text(str(message.get("text") or ""))
                    webhook_adapter.send_telegram(token, chat_id, "", reply)
                backoff = 2.0
            except Exception as exc:
                logger.warning("telegram query bot poll failed: %s", type(exc).__name__)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 60.0)


_BOT: TelegramQueryBot | None = None


def get_bot() -> TelegramQueryBot:
    global _BOT
    if _BOT is None:
        _BOT = TelegramQueryBot()
    return _BOT

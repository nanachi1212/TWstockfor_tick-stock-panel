"""External notification adapters for LINE Messaging API and Telegram Bot API.

Delivery failures are logged and returned as ``False`` so notification outages never
interrupt alert persistence or SSE delivery.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from contextlib import suppress
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
TELEGRAM_API_ROOT = "https://api.telegram.org"
_MAX_TEXT_LEN = 4096
_STATUS_LOCK = threading.Lock()
_DELIVERY_STATUS: dict[str, str] = {}


def _retry_after(response, *, telegram: bool = False) -> bool:
    """Wait and allow one retry only when the rate-limit delay fits the bound."""
    try:
        header_delay = response.headers.get("Retry-After")
    except (AttributeError, TypeError, ValueError):
        header_delay = None
    delay_value = header_delay
    if delay_value is None and telegram:
        try:
            payload = response.json()
            parameters = payload.get("parameters") if isinstance(payload, dict) else None
            if isinstance(parameters, dict):
                delay_value = parameters.get("retry_after")
        except (AttributeError, TypeError, ValueError):
            pass
    try:
        delay = float(delay_value if delay_value is not None else 1)
    except (TypeError, ValueError):
        delay = 1
    if not math.isfinite(delay) or delay > 2:
        return False
    time.sleep(max(0, delay))
    return True


def _message(title: str, body: str) -> str:
    text = f"{title}\n{body}".strip()
    return text if len(text) <= _MAX_TEXT_LEN else text[:_MAX_TEXT_LEN - 1] + "…"


def alert_message(event: dict) -> str:
    """Format an already-triggered App alert without inventing missing quote data."""
    lines = ["【TWStock 提醒】"]
    symbol = str(event.get("symbol") or "").strip()
    name = str(event.get("name") or "").strip()
    if symbol or name:
        lines.append(" ".join(part for part in (symbol, name) if part))
    reason = str(event.get("message") or event.get("rule_name") or "提醒條件已觸發").strip()
    lines.append(reason)

    price = event.get("price")
    if price is None and "price" in str(event.get("rule_type") or ""):
        price = event.get("trigger_value")
    price_number = math.nan
    if price is not None:
        with suppress(TypeError, ValueError):
            price_number = float(price)
    if math.isfinite(price_number) and price_number > 0:
        lines.append(f"目前價格: {price_number:g}")

    threshold = event.get("threshold")
    if threshold is not None:
        try:
            threshold_number = float(threshold)
            if math.isfinite(threshold_number):
                lines.append(f"設定門檻: {threshold_number:g}")
        except (TypeError, ValueError):
            pass

    quant_score = event.get("quant_score", event.get("score"))
    if quant_score is not None:
        try:
            score_number = float(quant_score)
            if math.isfinite(score_number):
                if event.get("quant_score") is not None:
                    if "quant 分數" not in str(event.get("message") or "").casefold():
                        lines.append(f"Quant 分數: {score_number * 100:.1f}%")
                else:
                    lines.append(f"Quant: {score_number:g}")
        except (TypeError, ValueError):
            pass

    triggered_at = event.get("triggered_at")
    if not triggered_at and event.get("ts"):
        try:
            triggered_at = datetime.fromtimestamp(float(event["ts"]) / 1000, timezone(timedelta(hours=8))).isoformat()
        except (TypeError, ValueError, OSError):
            triggered_at = None
    if triggered_at:
        try:
            parsed = datetime.fromisoformat(str(triggered_at))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone(timedelta(hours=8)))
            lines.append(f"觸發時間: {parsed.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')}")
        except ValueError:
            pass
    return "\n".join(lines)


def record_delivery_status(channel: str, status: str) -> None:
    with _STATUS_LOCK:
        _DELIVERY_STATUS[channel] = status


def delivery_status() -> dict[str, str]:
    with _STATUS_LOCK:
        return dict(_DELIVERY_STATUS)


def send_line(channel_access_token: str, target_id: str, title: str, body: str) -> bool:
    token = str(channel_access_token or "").strip()
    target = str(target_id or "").strip()
    text = _message(title, body)
    if not token or not target or not text:
        return False

    try:
        import httpx

        response = httpx.post(
            LINE_PUSH_URL,
            headers={"Authorization": f"Bearer {token}"},
            json={"to": target, "messages": [{"type": "text", "text": text}]},
            timeout=5.0,
        )
        if response.status_code == 429 and _retry_after(response):
            response = httpx.post(
                LINE_PUSH_URL,
                headers={"Authorization": f"Bearer {token}"},
                json={"to": target, "messages": [{"type": "text", "text": text}]},
                timeout=5.0,
            )
        if response.status_code == 200:
            record_delivery_status("line", "sent")
            return True
        logger.warning("LINE Messaging API push failed: HTTP %s", response.status_code)
    except Exception as exc:
        logger.warning("LINE Messaging API push failed (%s)", type(exc).__name__)
    record_delivery_status("line", "failed")
    return False


def send_telegram(bot_token: str, chat_id: str, title: str, body: str) -> bool:
    token = str(bot_token or "").strip()
    chat = str(chat_id or "").strip()
    text = _message(title, body)
    if not token or not chat or not text:
        return False

    try:
        import httpx

        response = httpx.post(
            f"{TELEGRAM_API_ROOT}/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text},
            timeout=5.0,
        )
        if response.status_code == 429 and _retry_after(response, telegram=True):
            response = httpx.post(
                f"{TELEGRAM_API_ROOT}/bot{token}/sendMessage",
                json={"chat_id": chat, "text": text},
                timeout=5.0,
            )
        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if not isinstance(payload, dict) or payload.get("ok", True) is True:
                record_delivery_status("telegram", "sent")
                return True
        logger.warning("Telegram Bot API push failed: HTTP %s", response.status_code)
    except Exception as exc:
        # Telegram embeds the token in the request URL, so never log the exception text.
        logger.warning("Telegram Bot API push failed (%s)", type(exc).__name__)
    record_delivery_status("telegram", "failed")
    return False

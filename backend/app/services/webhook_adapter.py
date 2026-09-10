"""External notification adapters for LINE Messaging API and Telegram Bot API.

Delivery failures are logged and returned as ``False`` so notification outages never
interrupt alert persistence or SSE delivery.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
TELEGRAM_API_ROOT = "https://api.telegram.org"
_MAX_TEXT_LEN = 4096


def _message(title: str, body: str) -> str:
    text = f"{title}\n{body}".strip()
    return text if len(text) <= _MAX_TEXT_LEN else text[:_MAX_TEXT_LEN - 1] + "…"


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
        if response.status_code == 200:
            return True
        logger.warning("LINE Messaging API push failed: HTTP %s", response.status_code)
    except Exception as exc:  # noqa: BLE001
        logger.warning("LINE Messaging API push failed (%s)", type(exc).__name__)
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
        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if not isinstance(payload, dict) or payload.get("ok", True) is True:
                return True
        logger.warning("Telegram Bot API push failed: HTTP %s", response.status_code)
    except Exception as exc:  # noqa: BLE001
        # Telegram embeds the token in the request URL, so never log the exception text.
        logger.warning("Telegram Bot API push failed (%s)", type(exc).__name__)
    return False

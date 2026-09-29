"""External notification adapters for LINE Messaging API and Telegram Bot API.

Delivery failures are logged and returned as ``False`` so notification outages never
interrupt alert persistence or SSE delivery.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
TELEGRAM_API_ROOT = "https://api.telegram.org"
_MAX_TEXT_LEN = 4096
_TAIPEI = ZoneInfo("Asia/Taipei")
_MIN_ALERT_TIME = datetime(2000, 1, 1, tzinfo=_TAIPEI)
_MAX_FUTURE_SKEW = timedelta(days=1)
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


def _finite_number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _format_number(value) -> str | None:
    number = _finite_number(value)
    if number is None:
        return None
    if abs(number) < 5e-11:
        number = 0.0
    return f"{number:,.10f}".rstrip("0").rstrip(".")


def _event_type(event: dict) -> str:
    return str(event.get("type") or event.get("rule_type") or "").strip().casefold()


def _alert_kind(event: dict) -> str:
    event_type = _event_type(event)
    source = str(event.get("source") or "").strip().casefold()
    if event_type == "buy_point" or (source == "strategy" and event.get("buy_point_strategy_id")):
        return "buy_point"
    if source == "strategy" or event_type == "strategy" or event.get("strategy_id"):
        return "strategy"
    if source in {"price"} or event_type.startswith(("price_", "change_pct_", "volume_", "near_")):
        return "price"
    if source in {"signal", "quant"} or event_type.startswith(("signal_", "quant_")):
        return "signal"
    if source in {"market", "sector", "ladder"} or event_type.startswith(("market_", "sector_")):
        return "market"
    if source in {"event", "event_center"} or event_type.startswith("event_"):
        return "event"
    return "default"


def _alert_title(kind: str) -> str:
    return {
        "strategy": "【TWStock 策略提醒】",
        "buy_point": "【TWStock 買點提醒】",
        "price": "【TWStock 價格提醒】",
        "signal": "【TWStock 訊號提醒】",
        "market": "【TWStock 市場異動】",
        "event": "【TWStock 事件提醒】",
    }.get(kind, "【TWStock 提醒】")


def _stock_label(event: dict) -> str:
    symbol = str(event.get("symbol") or "").strip()
    name = str(event.get("name") or "").strip()
    if name and symbol and name != symbol:
        return f"{name}（{symbol}）"
    return name or symbol


def _clean_message(value) -> str:
    message = str(value or "").strip()
    if message.startswith("【TWStock") and "】" in message:
        message = message.split("】", 1)[1].lstrip("\r\n ")
    return message


def _list_items(value, *, limit: int = 3) -> list[str]:
    values = value if isinstance(value, (list, tuple)) else [value]
    result: list[str] = []
    for item in values:
        if isinstance(item, dict):
            item = next((item.get(key) for key in ("reason", "message", "label", "name") if item.get(key)), "")
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _validated_trigger_time(event: dict) -> datetime | None:
    """Return a plausible alert instant, preferring ISO ``triggered_at`` over legacy ``ts``."""
    now_utc = datetime.now(UTC)

    def plausible(candidate: datetime | None) -> datetime | None:
        if candidate is None:
            return None
        candidate_utc = candidate.astimezone(UTC)
        if candidate_utc < _MIN_ALERT_TIME.astimezone(UTC):
            return None
        if candidate_utc > now_utc + _MAX_FUTURE_SKEW:
            return None
        return candidate.astimezone(_TAIPEI)

    raw_iso = event.get("triggered_at")
    if raw_iso:
        try:
            parsed: datetime | None = datetime.fromisoformat(str(raw_iso).strip())
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=_TAIPEI)
        except (TypeError, ValueError):
            parsed = None
        validated = plausible(parsed)
        if validated is not None:
            return validated

    raw_ts = _finite_number(event.get("ts"))
    if raw_ts is None or raw_ts <= 0:
        return None
    seconds = raw_ts / 1000 if raw_ts >= 1e12 else raw_ts if raw_ts >= 1e9 else None
    if seconds is None:
        return None
    try:
        return plausible(datetime.fromtimestamp(seconds, UTC))
    except (OSError, OverflowError, ValueError):
        return None


def _threshold_line(event: dict, kind: str) -> str | None:
    value = _format_number(event.get("threshold"))
    if value is None:
        return None
    event_type = _event_type(event)
    units = {
        "price_above": "元", "price_below": "元",
        "change_pct_above": "%", "change_pct_below": "%",
        "volume_above": _volume_unit(event), "volume_spike": "倍",
        "near_upper_limit": "%", "near_lower_limit": "%",
    }
    unit = units.get(event_type)
    if unit is None and kind == "strategy":
        unit = "元"
    if not unit:
        return None
    separator = "" if unit == "%" else " "
    return f"設定門檻：{value}{separator}{unit}"


def _volume_unit(event: dict) -> str:
    raw_unit = str(event.get("volume_unit") or "shares").strip().casefold()
    return "張" if raw_unit in {"lot", "lots", "張"} else "股"


def _metric_lines(event: dict, kind: str) -> list[str]:
    lines: list[str] = []
    price = event.get("price")
    if price is None and _event_type(event).startswith("price_"):
        price = event.get("trigger_value")
    price_text = _format_number(price)
    if price_text is not None and _finite_number(price) > 0:
        lines.append(f"目前價格：{price_text} 元")

    threshold = _threshold_line(event, kind)
    if threshold:
        lines.append(threshold)

    if kind not in {"strategy", "buy_point"}:
        change_pct = _format_number(event.get("change_pct"))
        if change_pct is not None:
            lines.append(f"漲跌幅：{change_pct}%")
        volume = _format_number(event.get("volume"))
        if volume is not None:
            lines.append(f"成交量：{volume} {_volume_unit(event)}")

    quant_score = event.get("quant_score", event.get("score"))
    score = _finite_number(quant_score)
    if score is not None and kind != "buy_point":
        if event.get("quant_score") is not None:
            if "quant 分數" not in _clean_message(event.get("message")).casefold():
                lines.append(f"Quant 分數：{score * 100:.1f}%")
        else:
            score_text = _format_number(score)
            if score_text is not None:
                lines.append(f"Quant：{score_text}")
    return lines


def alert_message(event: dict) -> str:
    """Format one complete LINE/Telegram alert without inventing missing data."""
    kind = _alert_kind(event)
    blocks = [_alert_title(kind)]
    stock = _stock_label(event)
    if stock:
        blocks.append(stock)

    strategy_name = _clean_message(event.get("rule_name") or event.get("strategy_name"))
    message = _clean_message(event.get("message"))
    if kind == "buy_point":
        strategy_name = strategy_name or str(event.get("buy_point_strategy_id") or "").strip()
        detail_lines = [f"策略：{strategy_name}" if strategy_name else "策略：買點條件", "狀態：已觸發"]
        detail_lines.extend(_metric_lines(event, kind))
        blocks.append("\n".join(detail_lines))
        reasons = _list_items(event.get("trigger_reasons") or event.get("reasons"))
        if not reasons and message and message != strategy_name:
            reasons = [message]
        if reasons:
            blocks.append("觸發原因：\n" + "\n".join(f"• {reason}" for reason in reasons))
        risks = _list_items(event.get("risk_flags"))
        if risks:
            blocks.append("風險提示：\n" + "\n".join(f"• {risk}" for risk in risks))
    elif kind == "strategy":
        strategy_name = strategy_name or message or "策略條件已觸發"
        detail_lines = [f"策略：{strategy_name}"]
        if message and message != strategy_name:
            detail_lines.append(f"原因：{message}")
        detail_lines.extend(_metric_lines(event, kind))
        blocks.append("\n".join(detail_lines))
    else:
        detail_lines = []
        reason = message or strategy_name
        if reason:
            detail_lines.append(f"原因：{reason}")
        detail_lines.extend(_metric_lines(event, kind))
        if detail_lines:
            blocks.append("\n".join(detail_lines))

    triggered_at = _validated_trigger_time(event)
    if triggered_at is not None:
        blocks.append(f"觸發時間：{triggered_at.strftime('%Y-%m-%d %H:%M')}")
    return "\n\n".join(block for block in blocks if block)


def build_test_alert_event(now: datetime | None = None) -> dict:
    """Build the settings-page smoke event using the production alert contract."""
    current = now or datetime.now(_TAIPEI)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_TAIPEI)
    current = current.astimezone(_TAIPEI)
    return {
        "type": "strategy",
        "source": "strategy",
        "symbol": "A",
        "name": "測試股票",
        "strategy_name": "測試買入策略",
        "price": 10,
        "triggered_at": current.isoformat(),
    }


def record_delivery_status(channel: str, status: str) -> None:
    with _STATUS_LOCK:
        _DELIVERY_STATUS[channel] = status


def clear_delivery_status(channel: str) -> None:
    with _STATUS_LOCK:
        _DELIVERY_STATUS.pop(channel, None)


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

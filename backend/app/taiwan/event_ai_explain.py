"""Follow-up AI reading for Auto Watch plan alerts; the rule-based alert is always sent first."""
from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.services import preferences, webhook_adapter
from app.services.ai_provider import (
    current_ai_request_timeout,
    generate_ai_text,
    generate_structured_ai_text,
    snapshot_ai_provider_config,
)
from app.strategy.custom_signals_ai import _extract_json_object
from app.taiwan.beginner_selection import selection_evidence

logger = logging.getLogger(__name__)

_PREF_ENABLED = "event_ai_explain_enabled"
# One worker: LLM calls are serialized and can never delay the rule-based alert pipeline.
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="event-ai-explain")
_FIELDS = (("what_happened", "發生什麼"), ("why_it_matters", "為什麼重要"), ("watch_next", "接下來看"))
_FOOTER = "AI 依上方事實與既有計畫解讀，只是輔助說明，不是買賣指示。"


def is_enabled() -> bool:
    return bool(preferences.load().get(_PREF_ENABLED, False))


def set_enabled(enabled: bool) -> bool:
    preferences.save({_PREF_ENABLED: bool(enabled)})
    return is_enabled()


def _plan_alert(event: dict[str, Any]) -> bool:
    from app.taiwan.realtime.monitor_engine import get_monitor_engine

    rule = get_monitor_engine().get_rule(str(event.get("rule_id") or ""))
    return rule is not None and rule.source == "trade_plan"


def _channels(event: dict[str, Any]) -> list[str]:
    """Same routing as the rule-based alert: the global setting wins, else the rule's own channels."""
    global_channels = preferences.get_external_notification_channels()
    if global_channels is not None:
        return list(global_channels)
    return [c for c in event.get("notify_channels", []) if c in ("line", "telegram")]


def _prompt(event: dict[str, Any], plan: dict[str, Any] | None) -> list[dict[str, str]]:
    facts = {key: event.get(key) for key in (
        "symbol", "name", "rule_name", "message", "trigger_value", "threshold",
        "severity", "quote_time", "market_status",
    )}
    return [
        {"role": "system", "content": "你是台股提醒的白話解讀助手。只能依提供的事實與既有計畫說明，不可預測漲跌、"
                                      "不可給買賣指示、不可自行產生新的價位。提到現價時只能用提醒事實的 trigger_value，"
                                      "計畫資料裡的收盤價是前一日資料，不可當作現價。每個欄位不超過 60 字。"},
        {"role": "user", "content": (
            f"提醒事實：{json.dumps(facts, ensure_ascii=False, default=str)}\n"
            f"既有計畫與規則判斷：{json.dumps(plan, ensure_ascii=False, default=str)}\n\n"
            '輸出純 JSON：{"what_happened":"...","why_it_matters":"...","watch_next":"..."}'
        )},
    ]


async def _explain(event: dict[str, Any]) -> dict[str, str]:
    plan = await asyncio.to_thread(selection_evidence, str(event.get("symbol") or ""))
    raw = await generate_structured_ai_text(
        _prompt(event, plan),
        truncated_retry_message="前次 JSON 被截斷，請以相同結構重新輸出，每個欄位不超過 40 字。",
        temperature=0.1,
        max_tokens=600,
        timeout=current_ai_request_timeout(),
        config_snapshot=snapshot_ai_provider_config(),
        generate=generate_ai_text,
    )
    payload = _extract_json_object(raw)
    if not isinstance(payload, dict):
        raise ValueError("event explanation is not an object")
    return {key: str(payload.get(key) or "").strip() for key, _ in _FIELDS}


def format_followup(event: dict[str, Any], parts: dict[str, str]) -> str | None:
    lines = [f"{label}：{parts[key]}" for key, label in _FIELDS if parts.get(key)]
    if not lines:
        return None
    stock = " ".join(str(event.get(k) or "") for k in ("symbol", "name")).strip()
    return "\n".join(["【AI 解讀】" + stock, *lines, _FOOTER])


def _send(event: dict[str, Any]) -> str:
    from app.services.quote_service import _claim_external_delivery

    channels = _channels(event)
    if not channels:
        return "no_channels"
    try:
        body = format_followup(event, asyncio.run(_explain(event)))
    except Exception as exc:  # LLM down/slow: the rule-based alert already went out
        logger.warning("event AI explain failed for %s (%s)", event.get("symbol"), type(exc).__name__)
        return f"failed:{type(exc).__name__}"
    if body is None:
        return "empty"
    event_id = f"{event.get('alert_id') or event.get('dedup_key')}:ai"
    sent = []
    if "line" in channels and _claim_external_delivery(event_id, "line"):
        webhook_adapter.send_line(preferences.get_line_channel_access_token(), preferences.get_line_target_id(), "", body)
        sent.append("line")
    if "telegram" in channels and _claim_external_delivery(event_id, "telegram"):
        webhook_adapter.send_telegram(preferences.get_telegram_bot_token(), preferences.get_telegram_chat_id(), "", body)
        sent.append("telegram")
    return "sent:" + ",".join(sent) if sent else "duplicate"


def submit(events: list[dict[str, Any]]) -> None:
    """Queue follow-ups for freshly emitted plan alerts; returns immediately."""
    if not events or not is_enabled():
        return
    for event in events:
        try:
            if not _plan_alert(event):
                continue
        except Exception:
            logger.exception("event AI explain: rule lookup failed")
            continue
        _EXECUTOR.submit(_run_one, event)


def _run_one(event: dict[str, Any]) -> None:
    try:
        logger.info("event AI explain %s: %s", event.get("symbol"), _send(event))
    except Exception:
        logger.exception("event AI explain crashed")

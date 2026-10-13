"""Settle matured Advice TradePlans and write short LLM reflections.

Pattern adapted from TradingAgents: once an outcome is known, one quick-model
call writes 2-4 sentences plus one lesson. ``select_reflections`` already feeds
matured reflections back into the next Advice for the same plan identity.
Settlement is deterministic and always runs. The reflection call is an
automatic AI call, so it runs only when the user turns it on (default off)
and AI is configured; a failed call is retried on the next run.
"""
# ruff: noqa: RUF001 -- user-facing Traditional Chinese prompts and messages.
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from typing import Any

from app.services import preferences
from app.services.ai_json import _extract_json_object
from app.services.ai_provider import (
    ai_configured,
    current_ai_request_timeout,
    generate_ai_text,
    generate_structured_ai_text,
    snapshot_ai_provider_config,
)
from app.taiwan.advice_tracking import ReflectionCreate, get_advice_tracking_service
from app.taiwan.benchmark_store import latest_benchmark
from app.taiwan.realtime.calendar import taipei_now

logger = logging.getLogger(__name__)

# ponytail: fixed per-run cap so a backlog cannot stall the evening pipeline; leftovers run next day.
REFLECTION_LIMIT = 10
_PREF_ENABLED = "auto_advice_reflection_enabled"
_PREF_LAST_RUN = "auto_advice_reflection_last_run"


def is_enabled() -> bool:
    return bool(preferences.load().get(_PREF_ENABLED, False))


def set_enabled(enabled: bool) -> bool:
    preferences.save({_PREF_ENABLED: bool(enabled)})
    return is_enabled()


def last_run() -> dict[str, Any] | None:
    value = preferences.load().get(_PREF_LAST_RUN)
    return value if isinstance(value, dict) else None


def taiex_return_pct(entry: date, exit_: date) -> float | None:
    """TAIEX close-to-close return over the same sessions, or None without exact closes."""
    start = latest_benchmark("TAIEX", on_or_before=entry)
    end = latest_benchmark("TAIEX", on_or_before=exit_)
    if not start or not end or start["date"] != entry or end["date"] != exit_ or not start["close"]:
        return None
    return (end["close"] / start["close"] - 1) * 100


def reflection_facts(advice: dict[str, Any], outcome: dict[str, Any]) -> dict[str, Any]:
    result = outcome["outcome"]
    facts: dict[str, Any] = {
        "status": result.get("status"),
        "exit_reason": result.get("exit_reason"),
        "entry_session": result.get("entry_session"),
        "exit_session": result.get("exit_session"),
        "gross_return_pct": result.get("gross_return_pct"),
        "taiex_return_pct": None,
        "alpha_pct": None,
    }
    if result.get("status") == "triggered" and result.get("entry_session") and result.get("exit_session"):
        bench = taiex_return_pct(
            date.fromisoformat(result["entry_session"]), date.fromisoformat(result["exit_session"])
        )
        gross = result.get("gross_return_pct")
        if bench is not None and isinstance(gross, (int, float)):
            facts["taiex_return_pct"] = round(bench, 2)
            facts["alpha_pct"] = round(gross - bench, 2)
    response = advice.get("response") or {}
    artifact = response.get("advice") or {}
    facts["advice"] = {
        "action": artifact.get("action"),
        "summary": artifact.get("summary"),
        "rationale": [item.get("text") for item in artifact.get("rationale") or []],
    }
    verdict = (response.get("debate") or {}).get("verdict")
    if verdict:
        facts["debate_verdict"] = {
            "stronger_side": verdict.get("stronger_side"),
            "margin": verdict.get("margin"),
        }
    return facts


async def write_reflection(advice: dict[str, Any], outcome: dict[str, Any]) -> ReflectionCreate:
    facts = reflection_facts(advice, outcome)
    raw = await generate_structured_ai_text(
        [
            {
                "role": "system",
                "content": "你是台股交易計畫的事後檢討者，正在回顧自己過去的 Advice；結果已經知道。",
            },
            {
                "role": "user",
                "content": f"""結果與當時的 Advice：{json.dumps(facts, ensure_ascii=False, default=str)}

評估窗口是交易計畫本身（停損、停利或最長持有天數）；單一結果不等於策略好壞。
summary 寫 2 到 4 句純文字（不要條列、標題或 markdown），依序：
1. 引用報酬數字；有 alpha_pct 時一併引用相對加權指數的超額報酬。not_triggered 時說明沒進場代表什麼。
2. 指出 Advice 的哪個理由被結果支持，哪個被推翻。
3. 若結果主要來自大盤而非個股理由，直接講。
lessons 只寫 1 條具體教訓，下次同一策略可以直接用。
這段文字會原文存檔，並在下次分析時重讀，每個字都要有用。不可給價格、目標價或信心分數。

輸出純 JSON：{{"summary":"...","lessons":["..."]}}""",
            },
        ],
        truncated_retry_message="前次 JSON 輸出已截斷。summary 不超過 150 字，lessons 只寫 1 條。",
        temperature=0.2,
        max_tokens=800,
        timeout=current_ai_request_timeout(),
        config_snapshot=snapshot_ai_provider_config(),
        generate=generate_ai_text,
    )
    parsed = _extract_json_object(raw)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("summary"), str) or not parsed["summary"].strip():
        raise ValueError("reflection provider did not return a summary")
    lessons = [item.strip()[:200] for item in parsed.get("lessons") or [] if isinstance(item, str) and item.strip()]
    return ReflectionCreate(
        outcome_id=outcome["id"], summary=parsed["summary"].strip()[:2000], lessons=lessons[:2],
    )


def run_auto_reflections(evaluated_as_of: date | str | None, *, limit: int = REFLECTION_LIMIT) -> dict[str, Any]:
    if evaluated_as_of is None:
        return {"status": "skipped", "reason": "no_daily_as_of"}
    as_of = date.fromisoformat(evaluated_as_of) if isinstance(evaluated_as_of, str) else evaluated_as_of
    svc = get_advice_tracking_service()
    due = svc.settle_pending(as_of)
    result: dict[str, Any] = {
        "status": "ok", "ran_at": taipei_now().isoformat(), "due": len(due), "reflected": [], "failed": {},
    }
    if due and not is_enabled():
        result.update(status="settled_only", reason="disabled")
    elif due and not ai_configured():
        result.update(status="settled_only", reason="ai_not_configured")
    else:
        _reflect(svc, due[:limit], result)
    preferences.save({_PREF_LAST_RUN: result})
    return result


def _reflect(svc: Any, due: list[tuple[dict[str, Any], dict[str, Any]]], result: dict[str, Any]) -> None:
    for advice, outcome in due:
        try:
            payload = asyncio.run(write_reflection(advice, outcome))
            svc.create_reflection(str(advice["id"]), payload)
            result["reflected"].append(advice["id"])
        except Exception as exc:  # one failing reflection must not stop the rest
            logger.warning("Advice reflection failed for %s", advice.get("id"), exc_info=True)
            result["failed"][advice["id"]] = type(exc).__name__

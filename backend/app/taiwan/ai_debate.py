"""Bull / bear debate with a judge, run before Taiwan AI Advice.

Pattern adapted from TradingAgents (bull researcher -> bear researcher ->
research manager).  It stays inside the Advice closed-evidence boundary:
every point must cite allowed evidence keys, and no side may emit prices,
targets, or confidence scores.  The verdict only weighs evidence; the
deterministic buy-point signal and server TradePlan remain authoritative.

Calls: bull and bear use the quick model, the judge uses the deep model.
"""
# ruff: noqa: RUF001 -- user-facing Traditional Chinese prompts and messages.
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.services.ai_json import _extract_json_object
from app.services.ai_provider import (
    AIProviderConfigSnapshot,
    current_ai_request_timeout,
    generate_ai_text,
    generate_structured_ai_text,
)

DEBATE_PROMPT_VERSION = "taiwan_stock_debate_v1"
_MAX_POINTS = 4

StrongerSide = Literal["bull", "bear", "balanced", "insufficient"]

_FORBIDDEN_KEYS = {
    "confidence", "confidence_score", "target_price", "entry_price", "stop_price",
    "planned_entry_price", "trigger_price", "rating",
}
_RULES = (
    "只能使用封閉證據並引用允許的鍵；不可預測股價、不可給任何價格、目標價、"
    "評等或信心分數；不可改寫伺服器交易計畫。"
)


class TaiwanAIDebatePoint(BaseModel):
    text: str
    evidence_refs: list[str]


class TaiwanAIDebateVerdict(BaseModel):
    stronger_side: StrongerSide
    margin: Literal["decisive", "narrow"] | None = None
    decisive_points: list[TaiwanAIDebatePoint] = Field(default_factory=list)
    what_would_change: list[str] = Field(default_factory=list)


class TaiwanAIDebate(BaseModel):
    bull_points: list[TaiwanAIDebatePoint]
    bear_points: list[TaiwanAIDebatePoint]
    bear_rebuttals: list[TaiwanAIDebatePoint] = Field(default_factory=list)
    verdict: TaiwanAIDebateVerdict
    quick_model: str | None = None
    deep_model: str | None = None
    prompt_version: str = DEBATE_PROMPT_VERSION


def _has_forbidden(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(_FORBIDDEN_KEYS & set(value)) or any(_has_forbidden(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_forbidden(v) for v in value)
    return False


def _points(value: Any, allowed: set[str]) -> list[TaiwanAIDebatePoint]:
    """Keep only points with text and at least one allowed evidence key."""
    output: list[TaiwanAIDebatePoint] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            continue
        text = item["text"].strip()
        refs = item.get("evidence_refs")
        refs = list(dict.fromkeys(r for r in refs if isinstance(r, str) and r in allowed)) if isinstance(refs, list) else []
        if text and refs:
            output.append(TaiwanAIDebatePoint(text=text, evidence_refs=refs))
    return output[:_MAX_POINTS]


def parse_verdict(payload: dict[str, Any], allowed: set[str]) -> TaiwanAIDebateVerdict:
    if _has_forbidden(payload):
        raise ValueError("debate verdict must not supply prices, ratings, or confidence")
    side = payload.get("stronger_side")
    if side not in {"bull", "bear", "balanced", "insufficient"}:
        raise ValueError("invalid stronger_side")
    margin = payload.get("margin")
    if side in {"bull", "bear"}:
        if margin not in {"decisive", "narrow"}:
            raise ValueError("a winning side needs margin decisive|narrow")
    else:
        margin = None
    decisive = _points(payload.get("decisive_points"), allowed)
    if side in {"bull", "bear"} and not decisive:
        raise ValueError("a winning side needs at least one evidenced decisive point")
    changes = [
        text.strip() for text in payload.get("what_would_change") or []
        if isinstance(text, str) and text.strip()
    ][:_MAX_POINTS]
    return TaiwanAIDebateVerdict(
        stronger_side=side, margin=margin, decisive_points=decisive, what_would_change=changes,
    )


async def _ask(system: str, user: str, config: AIProviderConfigSnapshot) -> dict[str, Any]:
    raw = await generate_structured_ai_text(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        truncated_retry_message=(
            "前次 JSON 輸出已截斷。請以相同 JSON 結構重新輸出，每段文字不超過 60 字，每個清單最多 3 項。"
        ),
        temperature=0.2,
        max_tokens=2000,
        timeout=current_ai_request_timeout(),
        config_snapshot=config,
        generate=generate_ai_text,
    )
    parsed = _extract_json_object(raw)
    if not isinstance(parsed, dict):
        raise ValueError("debate provider did not return an object")
    if _has_forbidden(parsed):
        raise ValueError("debate output must not supply prices, ratings, or confidence")
    return parsed


async def run_debate(
    *,
    evidence_payload: dict[str, Any],
    registry_keys: set[str],
    quick_config: AIProviderConfigSnapshot,
    deep_config: AIProviderConfigSnapshot,
) -> TaiwanAIDebate:
    """One round: bull opens, bear answers, judge weighs. Raises on unusable output."""
    keys = json.dumps(sorted(registry_keys), ensure_ascii=False)
    evidence = json.dumps(evidence_payload, ensure_ascii=False, default=str)
    point_schema = '{"text":"...","evidence_refs":["..."]}'

    bull = await _ask(
        f"你是台股多方研究員，替「這檔現在值得關注」建立最強論點。{_RULES}",
        f"""允許引用鍵：{keys}
證據：{evidence}

列出最多 {_MAX_POINTS} 個多方論點，每點須引用證據鍵。輸出純 JSON：{{"points":[{point_schema}]}}""",
        quick_config,
    )
    bull_points = _points(bull.get("points"), registry_keys)

    bear = await _ask(
        f"你是台股空方研究員，指出風險並逐點反駁多方。{_RULES}",
        f"""允許引用鍵：{keys}
證據：{evidence}
多方論點：{json.dumps([p.model_dump() for p in bull_points], ensure_ascii=False)}

列出最多 {_MAX_POINTS} 個空方論點，並針對多方論點提出反駁，每點須引用證據鍵。
輸出純 JSON：{{"points":[{point_schema}],"rebuttals":[{point_schema}]}}""",
        quick_config,
    )
    bear_points = _points(bear.get("points"), registry_keys)
    bear_rebuttals = _points(bear.get("rebuttals"), registry_keys)
    if not bull_points and not bear_points:
        raise ValueError("debate produced no evidenced points")

    judge = await _ask(
        "你是台股研究裁判，只判斷哪一方的證據較強。" + _RULES,
        f"""允許引用鍵：{keys}
證據：{evidence}
多方：{json.dumps([p.model_dump() for p in bull_points], ensure_ascii=False)}
空方：{json.dumps([p.model_dump() for p in bear_points], ensure_ascii=False)}
空方反駁：{json.dumps([p.model_dump() for p in bear_rebuttals], ensure_ascii=False)}

裁判規則：
1. 多空衝突是常態。選出證據較強的一方，依勝出幅度標 decisive 或 narrow。
2. balanced 只用在權衡後雙方仍真正平衡；insufficient 只用在證據太薄無法判斷。不可因為有衝突就判 balanced。
3. 忽略發言順序，不因誰先講或後講而加權。
4. decisive_points 只列真正決定結果的論點，須引用證據鍵。
5. what_would_change 列出什麼新證據會推翻這個判斷。

輸出純 JSON：{{"stronger_side":"bull|bear|balanced|insufficient","margin":"decisive|narrow|null",
"decisive_points":[{point_schema}],"what_would_change":["..."]}}""",
        deep_config,
    )
    return TaiwanAIDebate(
        bull_points=bull_points,
        bear_points=bear_points,
        bear_rebuttals=bear_rebuttals,
        verdict=parse_verdict(judge, registry_keys),
        quick_model=quick_config.model,
        deep_model=deep_config.model,
    )

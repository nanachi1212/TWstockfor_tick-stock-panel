"""Grounded Taiwan stock Advice and independent Review artifacts."""
# ruff: noqa: RUF001 -- user-facing Traditional Chinese prompts and messages.
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from datetime import time as dt_time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.services.ai_provider import (
    AIProviderConfigSnapshot,
    generate_ai_text,
    generate_structured_ai_text,
    snapshot_ai_provider_config,
)
from app.strategy.custom_signals_ai import _extract_json_object
from app.taiwan.abnormal_diagnostics import TaiwanAbnormalDiagnosticsService
from app.taiwan.ai_research import (
    ADVICE_PROMPT_VERSION,
    REVIEW_PROMPT_VERSION,
    _evidence_digest,
    _generation_config_metadata,
    build_evidence_registry,
)
from app.taiwan.beginner_selection import BEGINNER_EVIDENCE_REGISTRY_KEYS
from app.taiwan.buy_point import BuyPointSignal
from app.taiwan.buy_point_service import (
    BuyPointCandidate,
    BuyPointService,
    strategy_definition_payload,
)
from app.taiwan.realtime.calendar import TAIPEI_TZ, TaiwanTradingCalendar, taipei_now
from app.taiwan.research_context import TaiwanStockResearchContextService
from app.taiwan.trade_plan import TradePlan

logger = logging.getLogger(__name__)

AdviceAction = Literal["buy", "wait", "no_chase", "hold", "reduce", "exit", "no_view"]
RationaleKind = Literal["fact", "inference", "assumption"]

_CACHE_TTL_SECONDS = 600
_CACHE_MAX_ENTRIES = 128
_ADVICE_CACHE: dict[str, tuple[float, TaiwanAIAdviceRun]] = {}
_REVIEW_CACHE: dict[str, tuple[float, TaiwanAIReviewRun]] = {}
_ADVICE_INFLIGHT: dict[str, asyncio.Future[TaiwanAIAdviceRun]] = {}
_REVIEW_INFLIGHT: dict[str, asyncio.Future[TaiwanAIReviewRun]] = {}
_CACHE_LOCK = threading.Lock()


class TaiwanAIRationale(BaseModel):
    kind: RationaleKind
    text: str
    evidence_refs: list[str] = Field(default_factory=list)


class TaiwanAIAdviceCondition(BaseModel):
    text: str
    evidence_refs: list[str]


class TaiwanAIAdvice(BaseModel):
    symbol: str
    strategy_id: str
    action: AdviceAction
    summary: str
    rationale: list[TaiwanAIRationale] = Field(default_factory=list)
    conditions: list[TaiwanAIAdviceCondition] = Field(default_factory=list)
    invalidation: list[TaiwanAIAdviceCondition] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)
    buy_point_signal: BuyPointSignal
    selected_trade_plan: TradePlan | None = None
    evidence_as_of: str
    prompt_version: str = ADVICE_PROMPT_VERSION


class TaiwanAIReviewIssue(BaseModel):
    kind: Literal["blocking", "note"]
    text: str
    evidence_refs: list[str] = Field(default_factory=list)


class TaiwanAIReview(BaseModel):
    advice_run_id: str
    issues: list[TaiwanAIReviewIssue] = Field(default_factory=list)
    no_material_issues: bool
    prompt_version: str = REVIEW_PROMPT_VERSION


class TaiwanAIAdviceResponse(BaseModel):
    status: Literal["success", "unavailable", "error"]
    error_code: str | None = None
    error_message: str | None = None
    advice: TaiwanAIAdvice | None = None
    review: TaiwanAIReview | None = None
    review_status: Literal["not_requested", "success", "unavailable"] = "not_requested"
    review_error_code: str | None = None
    review_error_message: str | None = None
    provider: str | None = None
    model: str | None = None
    prompt_version: str = ADVICE_PROMPT_VERSION
    evidence_as_of: str | None = None
    run_id: str
    review_run_id: str | None = None
    started_at: str
    completed_at: str
    generated_at: str
    evidence_registry_keys: list[str] = Field(default_factory=list)


class TaiwanAIAdviceRun(BaseModel):
    run_id: str
    response: TaiwanAIAdviceResponse
    evidence_payload: dict[str, Any] = Field(default_factory=dict)
    evidence_registry_keys: list[str] = Field(default_factory=list)
    evidence_digest: str
    evidence_cutoff: str | None = None
    evidence_admission_provenance: dict[str, Any] = Field(default_factory=dict)
    source_snapshot_id: str | None = None
    source_snapshot_digest: str | None = None
    strategy_definition_digest: str | None = None
    strategy_definition: dict[str, Any] = Field(default_factory=dict)
    trade_plan: TradePlan | None = None
    plan_identity: str | None = None
    plan_instance_id: str | None = None
    selected_plan_instance_id: str | None = None
    forward_cutoff: str | None = None
    generation_config: dict[str, Any] = Field(default_factory=dict)


class TaiwanAIReviewRun(BaseModel):
    run_id: str
    advice_run_id: str
    review: TaiwanAIReview
    provider: str | None = None
    model: str | None = None
    started_at: str
    completed_at: str
    generation_config: dict[str, Any] = Field(default_factory=dict)


def _cache_get(cache: dict[str, tuple[float, Any]], key: str) -> Any | None:
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = cache.get(key)
        if cached is None:
            return None
        if now - cached[0] >= _CACHE_TTL_SECONDS:
            cache.pop(key, None)
            return None
        return cached[1].model_copy(deep=True)


def _cache_put(cache: dict[str, tuple[float, Any]], key: str, value: Any) -> None:
    with _CACHE_LOCK:
        if len(cache) >= _CACHE_MAX_ENTRIES:
            oldest = min(cache, key=lambda item: cache[item][0])
            cache.pop(oldest, None)
        cache[key] = (time.monotonic(), value.model_copy(deep=True))


async def _coalesced_run(
    *,
    cache: dict[str, tuple[float, Any]],
    inflight: dict[str, asyncio.Future[Any]],
    key: str,
    refresh: bool,
    build: Callable[[], Awaitable[Any]],
    cacheable: Callable[[Any], bool] | None = None,
) -> Any:
    """Return one frozen run for concurrent misses of the same cache key."""
    if not refresh:
        cached = _cache_get(cache, key)
        if cached is not None:
            return cached
    loop = asyncio.get_running_loop()
    with _CACHE_LOCK:
        pending = inflight.get(key)
        leader = pending is None
        if pending is None:
            pending = loop.create_future()
            inflight[key] = pending
    if not leader:
        shared = await asyncio.shield(pending)
        return shared.model_copy(deep=True)
    try:
        result = await build()
        if cacheable is None or cacheable(result):
            _cache_put(cache, key, result)
    except Exception as exc:
        with _CACHE_LOCK:
            if inflight.get(key) is pending:
                inflight.pop(key, None)
            if not pending.done():
                pending.set_exception(exc)
                pending.exception()
        raise
    except BaseException:
        with _CACHE_LOCK:
            if inflight.get(key) is pending:
                inflight.pop(key, None)
            if not pending.done():
                pending.cancel()
        raise
    with _CACHE_LOCK:
        if inflight.get(key) is pending:
            inflight.pop(key, None)
        if not pending.done():
            pending.set_result(result.model_copy(deep=True))
    return result


def _advice_cache_key(
    *,
    config: AIProviderConfigSnapshot,
    generation_config: dict[str, Any],
    strategy_id: str,
    strategy_definition_digest: str,
    strategy_payload: dict[str, Any],
    evidence_digest: str,
    missing_items: list[str],
    candidate_plan_ids: list[str],
    reflection_digest: str | None = None,
) -> str:
    material = {
        "prompt_version": ADVICE_PROMPT_VERSION,
        "provider": config.provider,
        "model": config.model,
        "credential_fingerprint": hashlib.sha256(config.api_key.encode()).hexdigest(),
        "strategy_id": strategy_id,
        "strategy_definition_digest": strategy_definition_digest,
        "strategy_prompt_digest": _evidence_digest(strategy_payload),
        "evidence_digest": evidence_digest,
        "missing_items": missing_items,
        "candidate_plan_ids": candidate_plan_ids,
        "reflection_digest": reflection_digest,
        "generation_config": generation_config,
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()


async def _call_provider(
    messages: list[dict[str, str]],
    config_snapshot: AIProviderConfigSnapshot,
) -> str:
    return await generate_structured_ai_text(
        messages,
        truncated_retry_message=(
            "前次 JSON 輸出已超出 token 上限而截斷。"
            "請以相同 JSON 結構重新輸出完整 Advice，每個文字欄位不超過 60 字，"
            "rationale / conditions / invalidation 各限 3 項。"
        ),
        temperature=0.1,
        max_tokens=3000,
        timeout=55.0,
        config_snapshot=config_snapshot,
        generate=generate_ai_text,
    )


def _valid_refs(value: Any, allowed: set[str]) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(ref for ref in value if isinstance(ref, str) and ref in allowed))


def _clean_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _contains_forbidden_advice_fields(value: Any) -> bool:
    forbidden = {
        "confidence",
        "confidence_score",
        "target_price",
        "entry_price",
        "stop_price",
        "planned_entry_price",
        "trigger_price",
    }
    if isinstance(value, dict):
        return bool(forbidden & set(value)) or any(
            _contains_forbidden_advice_fields(child) for child in value.values()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_advice_fields(child) for child in value)
    return False


def _has_position(evidence_payload: dict[str, Any]) -> bool:
    portfolio = evidence_payload.get("personal_context", {}).get("portfolio", {})
    shares = portfolio.get("shares") if isinstance(portfolio, dict) else None
    return isinstance(shares, (int, float)) and not isinstance(shares, bool) and shares > 0


def _parse_advice(
    payload: dict[str, Any],
    *,
    symbol: str,
    strategy_id: str,
    evidence_as_of: str,
    registry_keys: set[str],
    candidate: BuyPointCandidate,
    has_position: bool,
) -> TaiwanAIAdvice:
    if _contains_forbidden_advice_fields(payload):
        raise ValueError("AI advice must not supply confidence or executable prices")
    try:
        action: AdviceAction = payload["action"]
        if action not in {"buy", "wait", "no_chase", "hold", "reduce", "exit", "no_view"}:
            raise ValueError("invalid advice action")
    except (KeyError, TypeError) as exc:
        raise ValueError("advice action is required") from exc

    rationale: list[TaiwanAIRationale] = []
    for item in payload.get("rationale") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        text = _clean_text(item.get("text"))
        refs = _valid_refs(item.get("evidence_refs"), registry_keys)
        if kind not in {"fact", "inference", "assumption"} or not text:
            continue
        if kind in {"fact", "inference"} and not refs:
            continue
        rationale.append(TaiwanAIRationale(kind=kind, text=text, evidence_refs=refs))

    def conditions(name: str) -> list[TaiwanAIAdviceCondition]:
        output: list[TaiwanAIAdviceCondition] = []
        for item in payload.get(name) or []:
            if not isinstance(item, dict):
                continue
            text = _clean_text(item.get("text"))
            refs = _valid_refs(item.get("evidence_refs"), registry_keys)
            if text and refs:
                output.append(TaiwanAIAdviceCondition(text=text, evidence_refs=refs))
        return output

    data_gaps = [
        text
        for text in (_clean_text(value) for value in (payload.get("data_gaps") or []))
        if text
    ]
    selected_id = _clean_text(payload.get("selected_plan_instance_id")) or None
    selected_plan = (
        candidate.trade_plan
        if candidate.trade_plan is not None
        and selected_id == candidate.trade_plan.plan_instance_id
        else None
    )

    signal_status = candidate.signal.status
    if action == "buy" and (signal_status != "triggered" or selected_plan is None):
        action = "no_view" if signal_status in {"blocked", "unavailable"} else "wait"
        selected_plan = None
        data_gaps.append(
            candidate.plan_unavailable_reason or f"buy action rejected for {signal_status} signal"
        )
    if action in {"hold", "reduce", "exit"} and not has_position:
        action = "no_view"
        selected_plan = None
        data_gaps.append("no registered holding for a position-management action")
    if action != "buy":
        selected_plan = None

    return TaiwanAIAdvice(
        symbol=symbol,
        strategy_id=strategy_id,
        action=action,
        summary=_clean_text(payload.get("summary")),
        rationale=rationale,
        conditions=conditions("conditions"),
        invalidation=conditions("invalidation"),
        data_gaps=list(dict.fromkeys(data_gaps)),
        buy_point_signal=candidate.signal,
        selected_trade_plan=selected_plan,
        evidence_as_of=evidence_as_of,
    )


def _parse_review(payload: dict[str, Any], advice_run_id: str, allowed: set[str]) -> TaiwanAIReview:
    issues: list[TaiwanAIReviewIssue] = []
    for item in payload.get("issues") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        text = _clean_text(item.get("text"))
        refs = _valid_refs(item.get("evidence_refs"), allowed)
        if kind in {"blocking", "note"} and text and refs:
            issues.append(TaiwanAIReviewIssue(kind=kind, text=text, evidence_refs=refs))
    no_material = payload.get("no_material_issues")
    if not isinstance(no_material, bool):
        raise ValueError("no_material_issues must be boolean")
    if not issues and not no_material:
        raise ValueError("empty review must explicitly state no_material_issues")
    if issues and no_material:
        raise ValueError("review cannot report issues and no_material_issues together")
    return TaiwanAIReview(
        advice_run_id=advice_run_id,
        issues=issues,
        no_material_issues=no_material,
    )


class TaiwanAIAdviceService:
    def __init__(
        self,
        data_dir: Path,
        *,
        research_svc: TaiwanStockResearchContextService | None = None,
        diag_svc: TaiwanAbnormalDiagnosticsService | None = None,
        calendar: TaiwanTradingCalendar | None = None,
        buy_point_svc: BuyPointService | None = None,
        tracking_svc: Any | None = None,
    ) -> None:
        self.calendar = calendar or TaiwanTradingCalendar()
        self.research_svc = research_svc or TaiwanStockResearchContextService(calendar=self.calendar)
        self.diag_svc = diag_svc or TaiwanAbnormalDiagnosticsService(calendar=self.calendar)
        self.buy_point_svc = buy_point_svc or BuyPointService(data_dir)
        self.tracking_svc = tracking_svc

    def _unavailable(self, code: str, message: str) -> TaiwanAIAdviceRun:
        now = taipei_now().isoformat()
        run_id = uuid.uuid4().hex
        response = TaiwanAIAdviceResponse(
            status="unavailable",
            error_code=code,
            error_message=message,
            run_id=run_id,
            started_at=now,
            completed_at=now,
            generated_at=now,
        )
        return TaiwanAIAdviceRun(
            run_id=run_id,
            response=response,
            evidence_digest=_evidence_digest({}),
        )

    async def generate(
        self,
        symbol: str,
        *,
        strategy_id: str,
        target_date: date | None = None,
        personal_context: dict[str, Any] | None = None,
        review: bool = False,
        refresh: bool = False,
        selection_evidence: dict[str, Any] | None = None,
    ) -> tuple[TaiwanAIAdviceRun, TaiwanAIReviewRun | None]:
        if target_date is not None:
            return self._unavailable(
                "historical_advice_unsupported",
                "買點策略尚無完整歷史版本，因此歷史 Advice 暫不可用。",
            ), None
        started_at = taipei_now().isoformat()
        try:
            ctx = self.research_svc.get_research_context(symbol)
        except Exception:
            logger.warning("Advice research context unavailable for %s", symbol, exc_info=True)
            return self._unavailable(
                "context_assembly_failed", "目前無法組裝該標的 Advice 證據。"
            ), None

        diag_item = None
        try:
            snap = self.diag_svc.get_diagnostics(
                target_date=date.fromisoformat(ctx.as_of_date),
                include_all=True,
                include_etfs=True,
            )
            diag_item = next((item for item in snap.items if item.symbol == ctx.symbol), None)
        except Exception:
            logger.debug("Advice diagnostics unavailable for %s", symbol, exc_info=True)
        evidence_payload, registry_keys, missing_items = build_evidence_registry(
            ctx, diag_item, personal_context
        )
        try:
            candidate = self.buy_point_svc.candidate(
                ctx.symbol,
                strategy_id,
                instrument_type=ctx.identity.instrument_type,
                evidence_as_of=ctx.as_of_date,
            )
        except ValueError as exc:
            return self._unavailable("strategy_unavailable", str(exc)), None

        signal_evidence = candidate.signal.model_dump(mode="json")
        signal_evidence.pop("detected_at", None)
        evidence_payload["buy_point"] = signal_evidence
        if selection_evidence is not None:
            evidence_payload["beginner_selection"] = selection_evidence
            registry_keys.update(BEGINNER_EVIDENCE_REGISTRY_KEYS)
        evidence_payload["missing_items"] = list(missing_items)
        registry_keys.update(
            {
                "buy_point.status",
                "buy_point.freshness",
                "buy_point.triggered_conditions",
                "buy_point.failed_conditions",
                "buy_point.risk_flags",
                "buy_point.data_as_of",
            }
        )
        candidates: list[dict[str, Any]] = []
        if candidate.trade_plan is not None:
            candidates.append(candidate.trade_plan.model_dump(mode="json"))
        config = snapshot_ai_provider_config()
        generation_config = _generation_config_metadata(config)
        digest = _evidence_digest(evidence_payload)
        strategy_payload = candidate.strategy.model_dump(
            mode="json", exclude={"created_at", "updated_at"}
        )
        reflections: list[dict[str, Any]] = []
        if candidate.trade_plan is not None:
            try:
                if self.tracking_svc is None:
                    from app.taiwan.advice_tracking import get_advice_tracking_service

                    tracking_svc = get_advice_tracking_service()
                else:
                    tracking_svc = self.tracking_svc
                reflections = tracking_svc.select_reflections(
                    candidate.trade_plan.plan_identity,
                    decision_cutoff=started_at,
                    view="raw",
                    limit=2,
                )
            except Exception:
                logger.warning("Advice reflections unavailable for %s", symbol, exc_info=True)
        reflection_context = [
            {
                "summary": item.get("summary"),
                "lessons": item.get("lessons") or [],
                "completed_at": item.get("completed_at"),
                "outcome_digest": item.get("outcome_digest"),
            }
            for item in reflections
        ]
        reflection_digest = _evidence_digest({"reflections": reflection_context})
        cache_key = _advice_cache_key(
            config=config,
            generation_config=generation_config,
            strategy_id=strategy_id,
            strategy_definition_digest=candidate.strategy_definition_digest,
            strategy_payload=strategy_payload,
            evidence_digest=digest,
            missing_items=missing_items,
            candidate_plan_ids=[item["plan_instance_id"] for item in candidates],
            reflection_digest=reflection_digest,
        )

        async def build_base() -> TaiwanAIAdviceRun:
            prompt = f"""請依封閉證據與伺服器候選交易計畫產出 Advice JSON，不得自行計算或改寫任何價格。

允許引用鍵：{json.dumps(sorted(registry_keys), ensure_ascii=False)}
證據：{json.dumps(evidence_payload, ensure_ascii=False, default=str)}
策略：{json.dumps(strategy_payload, ensure_ascii=False)}
伺服器候選計畫：{json.dumps(candidates, ensure_ascii=False, default=str)}
同一交易計畫定義的已成熟歷史反思（最多 2 筆，僅供校準，不可改寫策略或價格）：{json.dumps(reflection_context, ensure_ascii=False)}
已知缺口：{json.dumps(missing_items, ensure_ascii=False)}

輸出純 JSON：
{{"action":"buy|wait|no_chase|hold|reduce|exit|no_view","summary":"...",
"rationale":[{{"kind":"fact|inference|assumption","text":"...","evidence_refs":["..."]}}],
"conditions":[{{"text":"...","evidence_refs":["..."]}}],
"invalidation":[{{"text":"...","evidence_refs":["..."]}}],
"data_gaps":["..."],"selected_plan_instance_id":"只能是伺服器候選 ID 或 null"}}"""
            messages = [
                {
                    "role": "system",
                    "content": (
                        "你是台股證據導向 Advice 引擎。不可漲跌預測、"
                        "不可產生信心分數或價格，不可改寫伺服器交易計畫。"
                    ),
                },
                {"role": "user", "content": prompt},
            ]
            try:
                parsed = _extract_json_object(await _call_provider(messages, config))
                if not isinstance(parsed, dict):
                    raise ValueError("Advice provider did not return an object")
                advice = _parse_advice(
                    parsed,
                    symbol=ctx.symbol,
                    strategy_id=strategy_id,
                    evidence_as_of=ctx.as_of_date,
                    registry_keys=registry_keys,
                    candidate=candidate,
                    has_position=_has_position(evidence_payload),
                )
            except Exception as exc:
                logger.warning("Advice generation failed for %s (%s)", symbol, type(exc).__name__)
                return self._unavailable(
                    "invalid_advice_response", "AI Advice 目前無法產生可驗證的結構化結果。"
                )
            completed_at = taipei_now().isoformat()
            run_id = uuid.uuid4().hex
            response = TaiwanAIAdviceResponse(
                status="success",
                advice=advice,
                provider=config.provider,
                model=config.model,
                evidence_as_of=ctx.as_of_date,
                run_id=run_id,
                started_at=started_at,
                completed_at=completed_at,
                generated_at=completed_at,
                evidence_registry_keys=sorted(registry_keys),
            )
            evidence_day = date.fromisoformat(ctx.as_of_date)
            evidence_cutoff = datetime.combine(
                evidence_day, dt_time(13, 30), tzinfo=TAIPEI_TZ
            ).isoformat()
            forward_day = self.calendar.next_potential_session(evidence_day)
            forward_cutoff = datetime.combine(
                forward_day, dt_time(9, 0), tzinfo=TAIPEI_TZ
            ).isoformat()
            selected = advice.selected_trade_plan
            base_run = TaiwanAIAdviceRun(
                run_id=run_id,
                response=response,
                evidence_payload=evidence_payload,
                evidence_registry_keys=sorted(registry_keys),
                evidence_digest=digest,
                evidence_cutoff=evidence_cutoff,
                evidence_admission_provenance={
                    "market_evidence_as_of": ctx.as_of_date,
                    "buy_point_evidence_as_of": candidate.signal.data_as_of,
                    "buy_point_status": candidate.signal.status,
                    "candidate_plan_source": "server",
                    "historical_strategy_supported": False,
                },
                source_snapshot_id=f"taiwan_research:{ctx.symbol}:{ctx.as_of_date}:{digest[:16]}",
                source_snapshot_digest=digest,
                strategy_definition_digest=candidate.strategy_definition_digest,
                strategy_definition=strategy_definition_payload(candidate.strategy),
                trade_plan=candidate.trade_plan,
                plan_identity=(candidate.trade_plan.plan_identity if candidate.trade_plan else None),
                plan_instance_id=(candidate.trade_plan.plan_instance_id if candidate.trade_plan else None),
                selected_plan_instance_id=selected.plan_instance_id if selected else None,
                forward_cutoff=forward_cutoff,
                generation_config=generation_config,
            )
            return base_run

        base_run = await _coalesced_run(
            cache=_ADVICE_CACHE,
            inflight=_ADVICE_INFLIGHT,
            key=cache_key,
            refresh=refresh,
            build=build_base,
            cacheable=lambda run: run.response.status == "success",
        )

        if not review or base_run.response.status != "success" or base_run.response.advice is None:
            return base_run, None
        review_key = hashlib.sha256(
            json.dumps(
                {
                    "advice_run_id": base_run.run_id,
                    "prompt_version": REVIEW_PROMPT_VERSION,
                    "generation_config": generation_config,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        async def build_review() -> TaiwanAIReviewRun:
            review_started = taipei_now().isoformat()
            review_prompt = f"""複核下列 frozen Advice，只回傳 issues/no_material_issues，不可重寫 Advice。
允許引用鍵：{json.dumps(sorted(base_run.evidence_registry_keys), ensure_ascii=False)}
凍結證據：{json.dumps(base_run.evidence_payload, ensure_ascii=False, default=str)}
Advice：{json.dumps(base_run.response.advice.model_dump(mode='json'), ensure_ascii=False)}
輸出純 JSON：{{"issues":[{{"kind":"blocking|note","text":"...","evidence_refs":["..."]}}],"no_material_issues":true|false}}"""
            try:
                parsed_review = _extract_json_object(
                    await _call_provider(
                        [
                            {
                                "role": "system",
                                "content": "你是獨立 Advice reviewer，只找可驗證問題，不產生第三份綜合建議。",
                            },
                            {"role": "user", "content": review_prompt},
                        ],
                        config,
                    )
                )
                if not isinstance(parsed_review, dict):
                    raise ValueError("Review provider did not return an object")
                parsed_artifact = _parse_review(
                    parsed_review,
                    base_run.run_id,
                    set(base_run.evidence_registry_keys),
                )
            except Exception as exc:
                logger.warning("Advice review failed for %s (%s)", symbol, type(exc).__name__)
                raise ValueError("invalid_review_response") from exc
            review_completed = taipei_now().isoformat()
            review_run = TaiwanAIReviewRun(
                run_id=uuid.uuid4().hex,
                advice_run_id=base_run.run_id,
                review=parsed_artifact,
                provider=config.provider,
                model=config.model,
                started_at=review_started,
                completed_at=review_completed,
                generation_config=generation_config,
            )
            return review_run

        try:
            review_run = await _coalesced_run(
                cache=_REVIEW_CACHE,
                inflight=_REVIEW_INFLIGHT,
                key=review_key,
                refresh=refresh,
                build=build_review,
            )
        except Exception:
            failed = base_run.model_copy(deep=True)
            failed.response.review_status = "unavailable"
            failed.response.review_error_code = "invalid_review_response"
            failed.response.review_error_message = "Advice 已保留，但獨立複核目前不可用。"
            return failed, None
        result = base_run.model_copy(deep=True)
        result.response.review = review_run.review.model_copy(deep=True)
        result.response.review_status = "success"
        result.response.review_run_id = review_run.run_id
        return result, review_run

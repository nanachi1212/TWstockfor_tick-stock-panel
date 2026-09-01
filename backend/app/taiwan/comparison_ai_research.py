"""Optional AI Multi-Stock Objective Research Comparison Service (Phase 7G).

Extends the Phase 7E closed-evidence-boundary AI interpretation layer to 2-5
Taiwan instruments at once. Reuses `build_evidence_registry()` per symbol,
namespaces keys by canonical symbol, and sends exactly ONE LLM call for the
whole comparison (never N calls). Enforces the same no-recommendation /
no-target-price / no-confidence-score boundary as Phase 7E, PLUS a dedicated
runtime guard against investment-ranking/preference language (Chinese and
English) — because objective comparison necessarily invites the model to
compare, and prompt instructions alone cannot guarantee it never slips into
"A is better than B" framing.

STRICT BOUNDARIES (inherited + extended from Phase 7E):
- Closed evidence boundary: AI only sees the server-assembled evidence payload.
- Evidence-ref whitelist enforcement, namespaced per symbol (no collisions).
- No recommendation / rating / target price / fair value / confidence score.
- NEW: no investment-preference/ranking language ("best pick", "更具吸引力",
  "首選", "should outperform", ...) — enforced by a deterministic runtime
  guard (`detect_ranking_language`) applied ONLY to AI-generated prose
  fields, never to raw evidence values, company/industry names, or symbol
  metadata (those remain untrusted data, not validated for "ranking words").
- On any guard hit: the WHOLE response is rejected as controlled
  `invalid_output` — never a silent partial rewrite.
- The deterministic `/compare` endpoint and this AI endpoint remain fully
  separate; loading the comparison page never triggers this service.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any, Literal
from pydantic import BaseModel, Field

from app.services.ai_provider import (
    current_ai_model,
    current_ai_provider,
    generate_ai_text,
)
from app.strategy.custom_signals_ai import _extract_json_object
from app.taiwan.ai_research import ObservationItem, build_evidence_registry
from app.taiwan.comparison import (
    MAX_COMPARE_SYMBOLS,
    MIN_COMPARE_SYMBOLS,
    ComparisonInstrumentResult,
    TaiwanStockComparisonService,
)
from app.taiwan.realtime.calendar import taipei_now

logger = logging.getLogger(__name__)

COMPARISON_PROMPT_VERSION = "taiwan_stock_comparison_v1"
COMPARISON_DISCLAIMER_TEXT = (
    "本報告僅依系統中可取得的結構化市場資料進行客觀比較與整理，不構成任何投資建議、買賣建議、"
    "優劣排序、價格預測或報酬保證。"
)


# ── Ranking/Recommendation Runtime Guard ──────────────────────
#
# Applied ONLY to AI-generated free-text prose fields (overview, per-dimension
# interpretations, key_observations[].text, risk_factors[].text) — NEVER to
# raw evidence values, company/industry names, or symbol metadata, which
# remain untrusted data (per Phase 7E's existing prompt-injection stance) and
# are not subject to this content check.
#
# Deliberately a curated MULTI-WORD PHRASE blocklist (mirrors the existing
# `_FOCUS_BLOCKLIST` pattern in app/services/ai_provider.py), not single
# comparative adjectives — so legitimate objective statements such as
# "報酬率較高" / "RSI 較高" / "相對強度較強" / "higher 5D return" are never
# flagged. This is a lightweight backstop against clear violations, not a
# general NLP/sentiment validator (consistent with the project's existing
# "no heavyweight NLP validator" decision).
_RANKING_LANGUAGE_BLOCKLIST = re.compile(
    r"最佳選擇|最值得投資|最具投資價值|更具投資吸引力|較佳投資機會|首選|勝出|"
    r"更具吸引力|投資價值較高|應該會表現更好|值得優先考慮|"
    r"優於.{0,20}(值得|建議|推薦|買進|投資)|"  # "優於" alone is objective; only flag when paired with preference verbs
    r"best pick|top pick|better investment opportunity|more attractive investment|"
    r"highest investment value|\bwinner\b|should outperform|top choice|best choice|"
    r"more attractive than|better opportunity",
    re.IGNORECASE,
)


def detect_ranking_language(text: str) -> str | None:
    """Return the first matched forbidden ranking/preference phrase, or None if clean.

    Scans only AI-generated comparison PROSE — callers must never apply this to
    raw evidence values, symbol/company/industry names, or other untrusted
    deterministic metadata strings.
    """
    if not text:
        return None
    match = _RANKING_LANGUAGE_BLOCKLIST.search(text)
    return match.group(0) if match else None


# ── Strong Typing & Pydantic Schemas ──────────────────────────


class ComparisonObservationItem(BaseModel):
    """A comparative observation with namespaced evidence citations (may span symbols)."""

    text: str = Field(..., description="客觀比較解讀文字 (無優劣排序、無投資建議)")
    evidence_refs: list[str] = Field(default_factory=list, description="所引用之有效命名空間證據鍵清單 (格式: {symbol}.{key})")


class TaiwanComparisonAIStockResearchReport(BaseModel):
    """Strongly-typed grounded AI multi-stock comparison report."""

    symbols: list[str]
    comparison_date: str
    generated_at: str
    prompt_version: str = COMPARISON_PROMPT_VERSION
    provider: str | None = None
    model: str | None = None

    comparison_overview: str = Field("", description="客觀綜合比較摘要 (陳述已知證據事實，非優劣判斷)")
    price_technical_comparison: str | None = Field(None, description="價格動能與技術位階客觀比較")
    institutional_comparison: str | None = Field(None, description="法人籌碼動向客觀比較")
    margin_comparison: str | None = Field(None, description="融資融券與信用交易客觀比較")
    fundamentals_comparison: str | None = Field(None, description="基本面或ETF屬性客觀比較 (ETF 標註不適用)")
    abnormal_diagnostics_comparison: str | None = Field(None, description="異常異動訊號客觀比較")

    key_observations: list[ComparisonObservationItem] = Field(default_factory=list)
    risk_factors: list[ComparisonObservationItem] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)

    disclaimer: str = Field(default=COMPARISON_DISCLAIMER_TEXT)


class TaiwanComparisonAIRequest(BaseModel):
    """Request payload for POST /api/taiwan/stocks/compare/ai-research."""

    symbols: list[str] = Field(..., min_length=MIN_COMPARE_SYMBOLS, max_length=MAX_COMPARE_SYMBOLS)
    date: str | None = Field(None, description="指定交易日 (YYYY-MM-DD)，預設為最新完成交易日")


class TaiwanComparisonAIResearchResponse(BaseModel):
    """API response envelope for /api/taiwan/stocks/compare/ai-research."""

    status: Literal["success", "unavailable", "error"]
    error_code: str | None = None
    error_message: str | None = None
    report: TaiwanComparisonAIStockResearchReport | None = None
    provider: str | None = None
    model: str | None = None
    prompt_version: str = COMPARISON_PROMPT_VERSION
    comparison_date: str | None = None
    generated_at: str
    evidence_registry_keys: list[str] = Field(default_factory=list)


# ── Namespaced Evidence Registry Builder ──────────────────────


def build_comparison_evidence_registry(
    instruments: list[ComparisonInstrumentResult],
) -> tuple[dict[str, Any], set[str], list[str]]:
    """Builds a namespaced multi-symbol evidence payload + combined whitelist.

    Reuses `build_evidence_registry()` per instrument unchanged, then prefixes
    every key with the instrument's canonical symbol (e.g.
    "2330.TWSE.price_context.return_5d"). Canonical symbols always include the
    exchange suffix, so two symbols can never collide on their key prefix.
    """
    combined_payload: dict[str, Any] = {}
    combined_keys: set[str] = set()
    combined_missing: list[str] = []

    for instrument in instruments:
        payload, keys, missing_items = build_evidence_registry(instrument.context, instrument.diagnostic_item)
        combined_payload[instrument.symbol] = payload
        for key in keys:
            combined_keys.add(f"{instrument.symbol}.{key}")
        for item in missing_items:
            combined_missing.append(f"{instrument.symbol}.{item}")

    return combined_payload, combined_keys, combined_missing


# ── System Prompt & Guidelines ────────────────────────────────


SYSTEM_PROMPT_COMPARISON = """你是一個客觀、確定性導向的「台股多標的比較研究證據解讀引擎」。
你的唯一任務是：依據系統所提供的多檔標的結構化研究證據（Evidence JSON，以標的代碼分組），
生成客觀、嚴謹、陳述事實的「比較」分析報告。這是比較多個標的的客觀資料差異，不是評選投資標的。

【核心安全紅線與硬性規範 - 違反將導致系統駁回】：
1. 嚴禁任何投資推薦、排序與預測：
   - 絕對禁止給予 Buy / Sell / Hold、加碼、減碼、買進、賣出評級。
   - 絕對禁止宣稱某標的「最佳選擇」「最值得投資」「首選」「勝出」「更具投資吸引力」「較佳投資機會」。
   - 絕對禁止使用英文投資偏好詞彙：best pick、top pick、winner、should outperform、
     more attractive investment、better investment opportunity、top choice。
   - 絕對禁止預測未來股價、給予目標價、公允價值、上漲空間或勝率評分。
   - 允許客觀比較敘述：「A 的 5 日報酬率高於 B」「A 呈現外資買超，B 呈現外資賣超」
     「A 成交量能高於 B」「A 為槓桿型 ETF，B 為一般型 ETF」——這些是資料事實比較，非投資偏好。
2. 封閉事實邊界：只能使用傳入的 JSON 數據進行解讀，嚴禁捏造。若欄位為 null 或 missing，
   必須如實說明「該標的目前資料不足」，不得私自推估。
3. 證據引用規定：
   - key_observations 與 risk_factors 中的每一條觀察，必須附帶 1 個以上「合法命名空間證據鍵」
     (格式: {symbol}.{key}，例如 "2330.TWSE.price_context.return_5d")。
   - 一條觀察可以只引用單一標的之證據（例如僅描述 A 有觸發某訊號、B 無此訊號的資料狀態）。
   - 禁止自創未定義的字串或鍵值，禁止使用不在白名單中的標的代碼前綴。
4. 數值正負方向保真：每一標的的正負報酬、買超賣超、站上/跌破均線等方向敘述，必須嚴格依據
   該標的自身數據，不得混淆或反轉任何一方的方向。
5. 零數值非缺失：數值為 0 代表「客觀數值為零 / 當日無變動」，不得描述為缺失。
6. ETF 與個股混合比較：若比較對象包含 ETF，其「不適用個別公司基本面」是類別性差異
   （ETF 本質使然），絕對不得將其描述為「資料缺失」、「較差」或「基本面較弱於個股」。
   槓桿/反向 ETF 之乘數僅可如實描述數字，不得暗示投資方向。
7. 輸出格式：必須嚴格輸出純 JSON 物件，不得包含任何 Markdown 外框或閒聊文字。
"""


# ── AI Comparison Service ─────────────────────────────────────


class TaiwanComparisonAIResearchService:
    """Coordinates deterministic multi-symbol evidence assembly, one AI call, and validation."""

    def __init__(self, comparison_svc: TaiwanStockComparisonService | None = None) -> None:
        self.comparison_svc = comparison_svc or TaiwanStockComparisonService()

    async def generate_comparison(
        self,
        symbols: list[str],
        target_date: date | None = None,
    ) -> TaiwanComparisonAIResearchResponse:
        """Assembles deterministic multi-symbol evidence and generates a grounded AI comparison."""
        now_iso = taipei_now().isoformat()

        # 1. Independently (re-)derive deterministic evidence server-side — never trusts a
        #    client-supplied deterministic payload, exactly like Phase 7E's ai-research.
        try:
            comparison = self.comparison_svc.compare(symbols, target_date=target_date)
        except Exception as e:
            logger.error("Failed to assemble comparison context for %s: %s", symbols, e)
            return TaiwanComparisonAIResearchResponse(
                status="unavailable",
                error_code="context_assembly_failed",
                error_message=f"無法組裝比較研究上下文: {e}",
                generated_at=now_iso,
            )

        # 2. Namespaced Evidence Registry
        evidence_payload, registry_keys, missing_items = build_comparison_evidence_registry(comparison.instruments)
        resolved_symbols = [i.symbol for i in comparison.instruments]

        # 3. Construct LLM Prompts
        user_prompt = f"""請依據以下封閉多標的研究證據 JSON，為以下標的產出結構化客觀「比較」解讀報告：
{json.dumps(resolved_symbols, ensure_ascii=False)}

【合法引用鍵白名單 (Allowed evidence_refs, 格式 {{symbol}}.{{key}})】:
{json.dumps(sorted(registry_keys), ensure_ascii=False)}

【結構化多標的證據資料 (Evidence Data, 依標的代碼分組)】:
{json.dumps(evidence_payload, ensure_ascii=False, indent=2)}

【已知缺失或未覆蓋項目 (Deterministic Missing Items)】:
{json.dumps(missing_items, ensure_ascii=False)}

請輸出符合以下綱要之純 JSON 物件：
{{
  "comparison_overview": "客觀綜合比較摘要，陳述各標的當日與近期表現資料差異",
  "price_technical_comparison": "量價與技術位階客觀比較",
  "institutional_comparison": "三大法人動向客觀比較",
  "margin_comparison": "融資融券變化客觀比較",
  "fundamentals_comparison": "基本面或ETF屬性客觀比較",
  "abnormal_diagnostics_comparison": "異常訊號客觀比較",
  "key_observations": [
    {{"text": "客觀比較觀察說明", "evidence_refs": ["合法的命名空間白名單鍵"]}}
  ],
  "risk_factors": [
    {{"text": "資料所呈現之風險特徵", "evidence_refs": ["合法的命名空間白名單鍵"]}}
  ],
  "missing_information": ["需涵蓋上述之缺失項目說明"]
}}"""

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_COMPARISON},
            {"role": "user", "content": user_prompt},
        ]

        # 4. Invoke AI Provider — exactly ONE call for the whole comparison.
        try:
            raw_text = await generate_ai_text(
                messages,
                temperature=0.1,
                max_tokens=2000,
                timeout=60.0,
            )
        except Exception as e:
            logger.warning("AI provider failed in stock comparison for %s: %s", symbols, e)
            return TaiwanComparisonAIResearchResponse(
                status="unavailable",
                error_code="provider_error",
                error_message=f"AI 比較分析服務調用失敗: {e}",
                provider=current_ai_provider(),
                model=current_ai_model(),
                prompt_version=COMPARISON_PROMPT_VERSION,
                comparison_date=comparison.comparison_date,
                generated_at=now_iso,
                evidence_registry_keys=sorted(registry_keys),
            )

        # 5. Extract and Validate JSON
        try:
            parsed = _extract_json_object(raw_text)
            if not isinstance(parsed, dict):
                raise ValueError("LLM did not return a valid JSON object dictionary.")
        except Exception as e:
            logger.error("Failed to parse JSON from AI comparison response: %s; raw: %s", e, raw_text)
            return TaiwanComparisonAIResearchResponse(
                status="unavailable",
                error_code="invalid_output",
                error_message="AI 回傳內容無法解析為合法 JSON 格式。",
                provider=current_ai_provider(),
                model=current_ai_model(),
                prompt_version=COMPARISON_PROMPT_VERSION,
                comparison_date=comparison.comparison_date,
                generated_at=now_iso,
                evidence_registry_keys=sorted(registry_keys),
            )

        # 6. Validate Evidence References
        validated_observations: list[ComparisonObservationItem] = []
        for obs in parsed.get("key_observations") or []:
            if not isinstance(obs, dict):
                continue
            text = str(obs.get("text") or "").strip()
            if not text:
                continue
            refs = [r for r in obs.get("evidence_refs") or [] if r in registry_keys]
            if refs:
                validated_observations.append(ComparisonObservationItem(text=text, evidence_refs=refs))

        validated_risks: list[ComparisonObservationItem] = []
        for rsk in parsed.get("risk_factors") or []:
            if not isinstance(rsk, dict):
                continue
            text = str(rsk.get("text") or "").strip()
            if not text:
                continue
            refs = [r for r in rsk.get("evidence_refs") or [] if r in registry_keys]
            if refs:
                validated_risks.append(ComparisonObservationItem(text=text, evidence_refs=refs))

        ai_missing = parsed.get("missing_information") or []
        combined_missing = sorted(set(missing_items + [str(m).strip() for m in ai_missing if m]))

        # 7. Deterministic Ranking/Recommendation Language Guard — applied ONLY to
        #    AI-generated prose fields, never to raw evidence/symbol/company data.
        prose_fields: list[str] = [
            str(parsed.get("comparison_overview") or ""),
            str(parsed.get("price_technical_comparison") or ""),
            str(parsed.get("institutional_comparison") or ""),
            str(parsed.get("margin_comparison") or ""),
            str(parsed.get("fundamentals_comparison") or ""),
            str(parsed.get("abnormal_diagnostics_comparison") or ""),
        ]
        prose_fields.extend(obs.text for obs in validated_observations)
        prose_fields.extend(rsk.text for rsk in validated_risks)

        for field_text in prose_fields:
            hit = detect_ranking_language(field_text)
            if hit:
                logger.warning(
                    "AI comparison rejected: ranking/preference language detected (%r) for symbols=%s",
                    hit,
                    symbols,
                )
                return TaiwanComparisonAIResearchResponse(
                    status="unavailable",
                    error_code="invalid_output",
                    error_message="AI 回傳內容包含禁止之投資排序/偏好用語，已拒絕輸出。",
                    provider=current_ai_provider(),
                    model=current_ai_model(),
                    prompt_version=COMPARISON_PROMPT_VERSION,
                    comparison_date=comparison.comparison_date,
                    generated_at=now_iso,
                    evidence_registry_keys=sorted(registry_keys),
                )

        # 8. Construct Final Grounded Comparison Report
        curr_provider = current_ai_provider()
        curr_model = current_ai_model()

        report = TaiwanComparisonAIStockResearchReport(
            symbols=resolved_symbols,
            comparison_date=comparison.comparison_date,
            generated_at=now_iso,
            prompt_version=COMPARISON_PROMPT_VERSION,
            provider=curr_provider,
            model=curr_model,
            comparison_overview=str(parsed.get("comparison_overview") or "").strip(),
            price_technical_comparison=parsed.get("price_technical_comparison"),
            institutional_comparison=parsed.get("institutional_comparison"),
            margin_comparison=parsed.get("margin_comparison"),
            fundamentals_comparison=parsed.get("fundamentals_comparison"),
            abnormal_diagnostics_comparison=parsed.get("abnormal_diagnostics_comparison"),
            key_observations=validated_observations[:8],
            risk_factors=validated_risks,
            missing_information=combined_missing,
            disclaimer=COMPARISON_DISCLAIMER_TEXT,
        )

        return TaiwanComparisonAIResearchResponse(
            status="success",
            report=report,
            provider=curr_provider,
            model=curr_model,
            prompt_version=COMPARISON_PROMPT_VERSION,
            comparison_date=comparison.comparison_date,
            generated_at=now_iso,
            evidence_registry_keys=sorted(registry_keys),
        )

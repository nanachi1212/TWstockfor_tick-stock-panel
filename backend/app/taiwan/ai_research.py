"""Deterministic & Grounded AI Taiwan Stock Research Report Service (Phase 7E).

Strict Boundaries & Design Directives:
- Interpretation Layer ONLY: The AI model NEVER fetches its own market data, browses the web,
  or invents facts. All input facts come exclusively from TaiwanStockResearchContext and
  Phase 7D abnormal diagnostics.
- Evidence Distinction:
    * Facts: KNOWN, DERIVED, MISSING (derived deterministically from local stores).
    * AI output: INTERPRETATION only.
- Strict No-Recommendation Policy: Absolutely NO Buy/Sell/Hold ratings, target prices,
  fair values, expected returns, upside/downside targets, or investment confidence scores.
- Evidence Reference Enforcement: Every important observation must reference valid keys in the
  deterministic evidence registry. Unsupported claims or hallucinated evidence keys are stripped.
- Provider-Agnostic & Error Resilience: Uses app.services.ai_provider.generate_ai_text.
  If the AI provider fails, times out, or returns invalid JSON, the service gracefully returns
  a structured error status without crashing or fabricating prose.
- Zero-HTTP at Request Time for Market Data: All evidence is assembled locally.
- Historical Point-in-Time: Supports target_date D without look-ahead.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Literal
from pydantic import BaseModel, Field

from app.services.ai_provider import (
    current_ai_model,
    current_ai_provider,
    generate_ai_text,
)
from app.strategy.custom_signals_ai import _extract_json_object
from app.taiwan.abnormal_diagnostics import (
    TaiwanAbnormalDiagnosticsService,
    TaiwanAbnormalDiagnosticItem,
)
from app.taiwan.realtime.calendar import TaiwanTradingCalendar, taipei_now
from app.taiwan.research_context import (
    TaiwanStockResearchContext,
    TaiwanStockResearchContextService,
)

logger = logging.getLogger(__name__)

PROMPT_VERSION = "taiwan_stock_research_v1"
DISCLAIMER_TEXT = "本報告僅依系統中可取得的結構化市場資料進行整理與客觀解讀，不構成任何投資建議、買賣建議、價格預測或報酬保證。"


# ── Strong Typing & Pydantic Schemas ──────────────────────────


class ObservationItem(BaseModel):
    """An individual grounded observation with its verified evidence citations."""

    text: str = Field(..., description="客觀解讀文字 (無買賣推薦、無目標價、無預測)")
    evidence_refs: list[str] = Field(default_factory=list, description="所引用之有效證據鍵清單")


class TaiwanAIStockResearchReport(BaseModel):
    """Strongly-typed grounded AI stock research report structure."""

    symbol: str
    code: str
    name: str
    industry: str | None = None
    instrument_type: str = "stock"
    evidence_as_of: str
    generated_at: str
    prompt_version: str = PROMPT_VERSION
    provider: str | None = None
    model: str | None = None

    # Sectional interpretations
    overview: str = Field("", description="客觀綜合摘要 (陳述已知證據事實，非後市看好看空)")
    market_interpretation: str | None = Field(None, description="大盤整體環境與寬度解讀")
    industry_interpretation: str | None = Field(None, description="產業輪動與相對強弱解讀")
    price_technical_interpretation: str | None = Field(None, description="價格動能、均線與技術位階解讀")
    institutional_interpretation: str | None = Field(None, description="外資、投信等法人籌碼動向解讀")
    margin_interpretation: str | None = Field(None, description="融資融券與信用交易變化解讀")
    fundamentals_interpretation: str | None = Field(None, description="基本面估值與營收解讀 (ETF 標註不適用)")
    abnormal_diagnostics_interpretation: str | None = Field(None, description="異常異動量能與資金流向訊號解讀")

    # Structured insights and evidence validation
    key_observations: list[ObservationItem] = Field(default_factory=list, description="重點客觀觀察清單 (最多 5 項，均需引證)")
    risk_factors: list[ObservationItem] = Field(default_factory=list, description="客觀數據所揭示之風險特徵 (均需引證)")
    missing_information: list[str] = Field(default_factory=list, description="確定性揭示之系統缺失或未覆蓋項目")

    disclaimer: str = Field(default=DISCLAIMER_TEXT, description="固定免責聲明")


class TaiwanAIResearchRequest(BaseModel):
    """Request payload for generating AI stock research report."""

    date: str | None = Field(None, description="指定交易日 (YYYY-MM-DD)，預設為最新完成交易日")


class TaiwanAIResearchResponse(BaseModel):
    """API response envelope for /api/taiwan/stocks/{symbol}/ai-research."""

    status: Literal["success", "unavailable", "error"]
    error_code: str | None = None
    error_message: str | None = None
    report: TaiwanAIStockResearchReport | None = None
    provider: str | None = None
    model: str | None = None
    prompt_version: str = PROMPT_VERSION
    evidence_as_of: str | None = None
    generated_at: str
    evidence_registry_keys: list[str] = Field(default_factory=list)


# ── Evidence Registry Builder & Payload Sanitizer ─────────────


def build_evidence_registry(
    ctx: TaiwanStockResearchContext,
    diag_item: TaiwanAbnormalDiagnosticItem | None,
) -> tuple[dict[str, Any], set[str], list[str]]:
    """Builds a compact serialized evidence payload, valid registry keys, and missing items."""
    registry_keys: set[str] = set()
    payload: dict[str, Any] = {}
    missing_items: list[str] = list(ctx.evidence_summary.missing_sections)

    # 1. Identity
    payload["identity"] = {
        "symbol": ctx.symbol,
        "code": ctx.identity.code,
        "name": ctx.identity.name,
        "exchange": ctx.identity.exchange,
        "instrument_type": ctx.identity.instrument_type,
        "industry": ctx.identity.industry,
    }
    registry_keys.update([
        "identity.symbol", "identity.code", "identity.name",
        "identity.exchange", "identity.instrument_type", "identity.industry",
    ])

    # 2. Market Context
    if ctx.market_context.status in ("current", "complete"):
        tot_adv = ctx.market_context.advance_count
        tot_dec = ctx.market_context.decline_count
        tot_comp = tot_adv + tot_dec + ctx.market_context.flat_count
        mkt_adv_ratio = round(tot_adv / tot_comp, 4) if tot_comp > 0 else None

        payload["market_context"] = {
            "advance_ratio": mkt_adv_ratio,
            "advance_count": ctx.market_context.advance_count,
            "decline_count": ctx.market_context.decline_count,
            "market_turnover": ctx.market_context.market_turnover,
        }
        registry_keys.update([
            "market_context.advance_ratio", "market_context.advance_count",
            "market_context.decline_count", "market_context.market_turnover",
        ])
    else:
        if "market_context" not in missing_items:
            missing_items.append("market_context")

    # 3. Industry Context
    if ctx.industry_context.status in ("current", "complete") and ctx.industry_context.industry:
        payload["industry_context"] = {
            "industry": ctx.industry_context.industry,
            "turnover_share": ctx.industry_context.turnover_share,
            "advance_ratio": ctx.industry_context.advance_ratio,
            "relative_strength_5d": ctx.industry_context.relative_strength_5d,
            "relative_strength_20d": ctx.industry_context.relative_strength_20d,
        }
        registry_keys.update([
            "industry_context.industry", "industry_context.turnover_share",
            "industry_context.advance_ratio", "industry_context.relative_strength_5d",
            "industry_context.relative_strength_20d",
        ])
    else:
        if "industry_context" not in missing_items:
            missing_items.append("industry_context")

    # 4. Price Context
    payload["price_context"] = {
        "trade_date": ctx.price_context.trade_date,
        "close": ctx.price_context.close,
        "change_pct": ctx.price_context.change_pct,
        "return_5d": ctx.price_context.return_5d,
        "return_20d": ctx.price_context.return_20d,
        "distance_from_20d_high": ctx.price_context.distance_from_20d_high,
        "distance_from_20d_low": ctx.price_context.distance_from_20d_low,
    }
    registry_keys.update([
        "price_context.trade_date", "price_context.close", "price_context.change_pct",
        "price_context.return_5d", "price_context.return_20d",
        "price_context.distance_from_20d_high", "price_context.distance_from_20d_low",
    ])

    # 5. Technical Context
    payload["technical_context"] = {
        "ma5": ctx.technical_context.ma5,
        "ma20": ctx.technical_context.ma20,
        "above_ma20": ctx.technical_context.above_ma20,
        "distance_to_ma20": ctx.technical_context.distance_to_ma20,
        "rsi14": ctx.technical_context.rsi14,
        "vol_ratio_5d": ctx.technical_context.vol_ratio_5d,
    }
    registry_keys.update([
        "technical_context.ma5", "technical_context.ma20", "technical_context.above_ma20",
        "technical_context.distance_to_ma20", "technical_context.rsi14", "technical_context.vol_ratio_5d",
    ])

    # 6. Institutional Context
    if ctx.institutional_context.status == "current":
        payload["institutional_context"] = {
            "foreign_net_1d": ctx.institutional_context.foreign_net_1d,
            "foreign_net_5d": ctx.institutional_context.foreign_net_5d,
            "foreign_net_20d": ctx.institutional_context.foreign_net_20d,
            "investment_trust_net_1d": ctx.institutional_context.investment_trust_net_1d,
            "investment_trust_net_5d": ctx.institutional_context.investment_trust_net_5d,
            "coverage_days_5d": ctx.institutional_context.coverage_days_5d,
        }
        registry_keys.update([
            "institutional_context.foreign_net_1d", "institutional_context.foreign_net_5d",
            "institutional_context.foreign_net_20d", "institutional_context.investment_trust_net_1d",
            "institutional_context.investment_trust_net_5d", "institutional_context.coverage_days_5d",
        ])
    else:
        if "institutional_context" not in missing_items:
            missing_items.append("institutional_context")

    # 7. Margin Context
    if ctx.margin_context.status == "current":
        payload["margin_context"] = {
            "margin_balance_change_1d": ctx.margin_context.margin_balance_change_1d,
            "margin_balance_change_5d": ctx.margin_context.margin_balance_change_5d,
            "short_balance_change_1d": ctx.margin_context.short_balance_change_1d,
            "short_margin_ratio": ctx.margin_context.short_margin_ratio,
        }
        registry_keys.update([
            "margin_context.margin_balance_change_1d", "margin_context.margin_balance_change_5d",
            "margin_context.short_balance_change_1d", "margin_context.short_margin_ratio",
        ])
    else:
        if "margin_context" not in missing_items:
            missing_items.append("margin_context")

    # 8. Fundamentals vs ETF
    if ctx.identity.instrument_type == "etf":
        payload["etf_context"] = {
            "status": ctx.etf_context.status,
            "etf_type": ctx.etf_context.etf_type,
            "underlying_scope": ctx.etf_context.underlying_scope,
            "leverage_multiplier": ctx.etf_context.leverage_multiplier,
            "inverse": ctx.etf_context.inverse,
            "benchmark": ctx.etf_context.benchmark,
        }
        registry_keys.update([
            "etf_context.status", "etf_context.etf_type", "etf_context.underlying_scope",
            "etf_context.leverage_multiplier", "etf_context.inverse", "etf_context.benchmark",
        ])
        missing_items.append("fundamentals_not_applicable_for_etf")
    else:
        if ctx.fundamentals_context.status == "available":
            payload["fundamentals_context"] = {
                "as_of_period": ctx.fundamentals_context.as_of_period,
                "pe": ctx.fundamentals_context.pe,
                "pb": ctx.fundamentals_context.pb,
                "dividend_yield": ctx.fundamentals_context.dividend_yield,
                "monthly_revenue_yoy": ctx.fundamentals_context.monthly_revenue_yoy,
                "latest_eps": ctx.fundamentals_context.latest_eps,
            }
            registry_keys.update([
                "fundamentals_context.as_of_period", "fundamentals_context.pe",
                "fundamentals_context.pb", "fundamentals_context.dividend_yield",
                "fundamentals_context.monthly_revenue_yoy", "fundamentals_context.latest_eps",
            ])
        else:
            if "fundamentals_context" not in missing_items:
                missing_items.append("fundamentals_context")

    # 9. Market Rules
    payload["market_rules"] = {
        "price_limit_pct": ctx.market_rules_context.price_limit_pct,
        "is_no_limit": ctx.market_rules_context.is_no_limit,
    }
    registry_keys.update(["market_rules.price_limit_pct", "market_rules.is_no_limit"])

    # 10. Abnormal Diagnostics (Phase 7D)
    if diag_item and diag_item.signals:
        sig_summaries = []
        for s in diag_item.signals:
            sig_key = f"abnormal.{s.type.lower()}"
            registry_keys.add(sig_key)
            sig_summaries.append({
                "key": sig_key,
                "type": s.type,
                "subtype": s.subtype,
                "observed": s.observed,
                "baseline": s.baseline,
                "ratio": s.ratio,
                "delta": s.delta,
            })
        payload["abnormal_signals"] = sig_summaries
        registry_keys.add("abnormal.signal_count")
    else:
        payload["abnormal_signals"] = []

    # Structurally not-applicable signal types (Phase 7J) — e.g. RELATIVE_STRENGTH_OUTLIER
    # for ETFs. Single-source serialization: build_comparison_evidence_registry() reuses
    # this function per-instrument and namespaces every key it returns as "{symbol}.{key}",
    # so this one addition automatically produces "{symbol}.abnormal.not_applicable_signals"
    # in comparisons with no separate comparison-side implementation.
    if diag_item and diag_item.not_applicable_signals:
        payload["abnormal_not_applicable_signals"] = diag_item.not_applicable_signals
        registry_keys.add("abnormal.not_applicable_signals")

    return payload, registry_keys, missing_items


# ── System Prompt & Guidelines ────────────────────────────────


SYSTEM_PROMPT = """你是一個客觀、確定性導向的「台股個股研究證據解讀引擎」。
你的唯一任務是：依據系統所提供的結構化研究證據（Evidence JSON），生成客觀、嚴謹、陳述事實的分析報告。

【核心安全紅線與硬性規範 - 違反將導致系統駁回】：
1. 嚴禁任何投資推薦與預測：
   - 絕對禁止給予 Buy / Sell / Hold、加碼、減碼、買進、賣出評級。
   - 絕對禁止預測未來股價、給予目標價 (Target Price)、公允價值 (Fair Value)、上漲空間 (Upside) 或勝率評分。
   - 禁用煽動性詞彙：必漲、看多、看空、強烈推薦、逢低買進、散戶進場、主力洗盤、大戶吃貨。
   - 允許用詞：顯示、反映、目前資料可觀察到、目前證據支持、數據呈現。
2. 封閉事實邊界 (Closed Evidence Boundary)：
   - 你只能使用傳入的 JSON 數據進行解讀。
   - 嚴禁捏造新聞、法說會內容、非數據列出的財務指標、產業未驗證傳言或未來催化劑 (Catalysts)。
   - 若欄位為 null 或 missing，必須如實說明「目前資料不足」或「未提供」，不得私自推估數值。
3. 證據引用規定 (Evidence Refs)：
   - key_observations 與 risk_factors 中的每一條觀察，必須附帶 1~3 個「合法證據鍵 (evidence_refs)」。
   - 只能引用在提供的證據鍵白名單中的鍵值（例如 price_context.return_5d, industry_context.relative_strength_5d 等）。
   - 禁止自創未定義的字串或鍵值。
4. 數值正負方向保真 (Numerical Direction Preservation)：
   - 必須嚴格保持數值的正負語意與方向：
     * 正報酬 (return > 0) / 漲幅 (change_pct > 0) 必須描述為上漲或正報酬，絕對不得描述為下跌或負報酬。
     * 負報酬 (return < 0) / 跌幅 (change_pct < 0) 必須描述為下跌或負報酬，絕對不得描述為上漲或正報酬。
     * 外資/投信淨買超 (net > 0) 必須描述為買超，絕對不得描述為賣超；淨賣超 (net < 0) 必須描述為賣超，絕對不得描述為買超。
     * 站上均線 (above_ma20 == true) 必須描述為站上或位於均線之上；跌破 (above_ma20 == false) 必須描述為位於均線之下。
     * 融資融券增加 (> 0) 或減少 (< 0) 必須忠實陳述方向，禁止顛倒。
5. 零數值非缺失 (Zero Is Not Missing)：
   - 數值為 0（例如 foreign_net_1d=0, margin_change=0）代表「客觀數值為零 / 當日無變動」，絕對不得描述為資料缺失、遺失或未提供。
   - abnormal.signal_count 代表「已觸發且適用之異常訊號數量」，並非「已評估之診斷種類總數」。signal_count=0 僅代表「無適用之異常訊號被觸發」，不得延伸解讀為「所有診斷類型皆已檢查且正常」。
   - 若證據中出現 abnormal.not_applicable_signals（或比較情境下之 {symbol}.abnormal.not_applicable_signals），其中列出之訊號類型代表「該類型訊號因標的類別而結構性不適用，並未被評估」，絕對不得描述為零、缺失、正常或「已檢查但未觸發」。例如 ETF 的 RELATIVE_STRENGTH_OUTLIER，僅可陳述為「此類型訊號不適用於此標的類別」。
6. ETF 標的處理：
   - 若為 ETF 標的，個別公司基本面 (fundamentals) 狀態為 not_applicable，必須陳述為「ETF 不適用個別公司財務指標」，絕對不得描述為「資料缺失」、「資料品質不佳」或「基本面惡化」。
   - 槓桿 (leveraged) 或反向 (inverse) 乘數必須如實依據提供之數字陳述，不得給予投資方向建議。
7. 輸出格式：
   - 必須嚴格輸出純 JSON 物件，符合指定之綱要結構，不得包含任何 Markdown 外框或閒聊文字。
"""


# ── AI Research Service ───────────────────────────────────────


class TaiwanAIResearchService:
    """Coordinates deterministic evidence assembly, AI interpretation, and evidence ref validation."""

    def __init__(
        self,
        research_svc: TaiwanStockResearchContextService | None = None,
        diag_svc: TaiwanAbnormalDiagnosticsService | None = None,
        calendar: TaiwanTradingCalendar | None = None,
    ) -> None:
        self.calendar = calendar or TaiwanTradingCalendar()
        self.research_svc = research_svc or TaiwanStockResearchContextService(calendar=self.calendar)
        self.diag_svc = diag_svc or TaiwanAbnormalDiagnosticsService(calendar=self.calendar)

    async def generate_report(
        self,
        symbol: str,
        target_date: date | None = None,
    ) -> TaiwanAIResearchResponse:
        """Assembles deterministic evidence and generates a grounded AI research report."""
        now_iso = taipei_now().isoformat()

        # 1. Assemble Deterministic Evidence Context (Phase 7C & 7D)
        try:
            ctx = self.research_svc.get_research_context(symbol, target_date=target_date)
        except Exception as e:
            logger.error("Failed to assemble research context for %s: %s", symbol, e)
            return TaiwanAIResearchResponse(
                status="unavailable",
                error_code="context_assembly_failed",
                error_message=f"無法組裝該標的之研究上下文: {e}",
                generated_at=now_iso,
            )

        # Retrieve Phase 7D diagnostic item if available
        diag_item = None
        try:
            # include_etfs=True (Phase 7J): single-symbol AI research already branches
            # ETF-vs-stock correctly throughout (fundamentals not_applicable, etc.) — one
            # of the two internal consumers proven safe to opt in.
            diag_snap = self.diag_svc.get_diagnostics(
                target_date=date.fromisoformat(ctx.as_of_date),
                include_all=True,
                include_etfs=True,
            )
            for item in diag_snap.items:
                if item.symbol == ctx.symbol:
                    diag_item = item
                    break
        except Exception as e:
            logger.warning("Diagnostics lookup failed for %s on %s: %s", symbol, ctx.as_of_date, e)

        # 2. Build Flattened Evidence Registry and Compact Payload
        evidence_payload, registry_keys, missing_items = build_evidence_registry(ctx, diag_item)

        # 3. Construct LLM Prompts
        user_prompt = f"""請依據以下封閉研究證據 JSON，為 {ctx.identity.name} ({ctx.identity.code}) 產出結構化客觀解讀報告。

【合法引用鍵白名單 (Allowed evidence_refs)】:
{json.dumps(sorted(list(registry_keys)), ensure_ascii=False)}

【結構化證據資料 (Evidence Data)】:
{json.dumps(evidence_payload, ensure_ascii=False, indent=2)}

【已知缺失或未覆蓋項目 (Deterministic Missing Items)】:
{json.dumps(missing_items, ensure_ascii=False)}

請輸出符合以下綱要之純 JSON 物件：
{{
  "overview": "客觀綜合摘要，陳述當日與近期表現事實",
  "market_interpretation": "大盤環境客觀解讀",
  "industry_interpretation": "產業輪動客觀解讀",
  "price_technical_interpretation": "量價與技術位階客觀解讀",
  "institutional_interpretation": "三大法人動向客觀解讀",
  "margin_interpretation": "融資融券變化客觀解讀",
  "fundamentals_interpretation": "基本面或ETF屬性解讀",
  "abnormal_diagnostics_interpretation": "異常訊號客觀解讀",
  "key_observations": [
    {{"text": "觀察重點說明", "evidence_refs": ["合法的白名單鍵"]}}
  ],
  "risk_factors": [
    {{"text": "數據所呈現之風險特徵", "evidence_refs": ["合法的白名單鍵"]}}
  ],
  "missing_information": ["需涵蓋上述之缺失項目說明"]
}}"""

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        # 4. Invoke AI Provider
        try:
            raw_text = await generate_ai_text(
                messages,
                temperature=0.1,
                max_tokens=1600,
                timeout=45.0,
            )
        except Exception as e:
            logger.warning("AI provider failed in stock research report for %s: %s", symbol, e)
            return TaiwanAIResearchResponse(
                status="unavailable",
                error_code="provider_error",
                error_message=f"AI 分析服務調用失敗: {e}",
                provider=current_ai_provider(),
                model=current_ai_model(),
                prompt_version=PROMPT_VERSION,
                evidence_as_of=ctx.as_of_date,
                generated_at=now_iso,
                evidence_registry_keys=sorted(list(registry_keys)),
            )

        # 5. Extract and Validate JSON
        try:
            parsed = _extract_json_object(raw_text)
            if not isinstance(parsed, dict):
                raise ValueError("LLM did not return a valid JSON object dictionary.")
        except Exception as e:
            logger.error("Failed to parse JSON from AI response: %s; raw: %s", e, raw_text)
            return TaiwanAIResearchResponse(
                status="unavailable",
                error_code="invalid_output",
                error_message="AI 回傳內容無法解析為合法 JSON 格式。",
                provider=current_ai_provider(),
                model=current_ai_model(),
                prompt_version=PROMPT_VERSION,
                evidence_as_of=ctx.as_of_date,
                generated_at=now_iso,
                evidence_registry_keys=sorted(list(registry_keys)),
            )

        # 6. Validate Evidence References & Strip Forbidden Fields
        validated_observations: list[ObservationItem] = []
        for obs in parsed.get("key_observations") or []:
            if not isinstance(obs, dict):
                continue
            text = str(obs.get("text") or "").strip()
            if not text:
                continue
            refs = [r for r in obs.get("evidence_refs") or [] if r in registry_keys]
            if refs:  # Only keep observation if it has at least one valid evidence ref
                validated_observations.append(ObservationItem(text=text, evidence_refs=refs))

        validated_risks: list[ObservationItem] = []
        for rsk in parsed.get("risk_factors") or []:
            if not isinstance(rsk, dict):
                continue
            text = str(rsk.get("text") or "").strip()
            if not text:
                continue
            refs = [r for r in rsk.get("evidence_refs") or [] if r in registry_keys]
            if refs:
                validated_risks.append(ObservationItem(text=text, evidence_refs=refs))

        # Merge deterministic missing items with AI reported missing items
        ai_missing = parsed.get("missing_information") or []
        combined_missing = sorted(list(set(missing_items + [str(m).strip() for m in ai_missing if m])))

        # 7. Construct Final Grounded Report
        curr_provider = current_ai_provider()
        curr_model = current_ai_model()

        report = TaiwanAIStockResearchReport(
            symbol=ctx.symbol,
            code=ctx.identity.code,
            name=ctx.identity.name,
            industry=ctx.identity.industry,
            instrument_type=ctx.identity.instrument_type,
            evidence_as_of=ctx.as_of_date,
            generated_at=now_iso,
            prompt_version=PROMPT_VERSION,
            provider=curr_provider,
            model=curr_model,
            overview=str(parsed.get("overview") or "").strip(),
            market_interpretation=parsed.get("market_interpretation"),
            industry_interpretation=parsed.get("industry_interpretation"),
            price_technical_interpretation=parsed.get("price_technical_interpretation"),
            institutional_interpretation=parsed.get("institutional_interpretation"),
            margin_interpretation=parsed.get("margin_interpretation"),
            fundamentals_interpretation=parsed.get("fundamentals_interpretation"),
            abnormal_diagnostics_interpretation=parsed.get("abnormal_diagnostics_interpretation"),
            key_observations=validated_observations[:5],
            risk_factors=validated_risks,
            missing_information=combined_missing,
            disclaimer=DISCLAIMER_TEXT,
        )

        return TaiwanAIResearchResponse(
            status="success",
            report=report,
            provider=curr_provider,
            model=curr_model,
            prompt_version=PROMPT_VERSION,
            evidence_as_of=ctx.as_of_date,
            generated_at=now_iso,
            evidence_registry_keys=sorted(list(registry_keys)),
        )

"""Taiwan Market Domain Specific Endpoints.

Covers:
  - GET /api/taiwan/stocks/{symbol}: Unified Taiwan Stock Research Workspace Detail API.
"""
# ruff: noqa: RUF001, RUF002 -- user-facing Traditional Chinese API messages.
from __future__ import annotations

import logging
from datetime import date as dt_date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.market_breadth import router as market_breadth_router
from app.api.market_research import router as market_research_router
from app.taiwan.abnormal_diagnostics import (
    TaiwanAbnormalDiagnosticsService,
    TaiwanAbnormalDiagnosticsSnapshot,
)
from app.taiwan.advice_tracking import (
    AdviceView,
    ReflectionCreate,
    get_advice_tracking_service,
)
from app.taiwan.ai_advice import TaiwanAIAdviceResponse, TaiwanAIAdviceService
from app.taiwan.ai_research import (
    TaiwanAIResearchRequest,
    TaiwanAIResearchResponse,
    TaiwanAIResearchService,
)
from app.taiwan.ai_research_history import (
    AIResearchHistoryError,
    get_ai_research_history_store,
)
from app.taiwan.bootstrap import (
    BootstrapJobState,
    TaiwanBootstrapService,
    TaiwanHistoryStatus,
    get_history_status,
)
from app.taiwan.comparison import (
    TaiwanStockCompareRequest,
    TaiwanStockComparisonResponse,
    TaiwanStockComparisonService,
)
from app.taiwan.comparison_ai_research import (
    TaiwanComparisonAIRequest,
    TaiwanComparisonAIResearchResponse,
    TaiwanComparisonAIResearchService,
)
from app.taiwan.current_data import (
    TaiwanCurrentDataResponse,
    TaiwanDatasetCapability,
    capability_matrix,
    get_taiwan_current_data_service,
)
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.daily_update import FreshnessStatus, TaiwanDailyUpdateService
from app.taiwan.detail_models import TaiwanStockDetailResponse
from app.taiwan.detail_service import get_taiwan_stock_detail_service
from app.taiwan.industry_intelligence import (
    TaiwanIndustryIntelligenceService,
    TaiwanIndustryIntelligenceSnapshot,
)
from app.taiwan.market_intelligence import (
    TaiwanMarketIntelligenceService,
    TaiwanMarketIntelligenceSnapshot,
)
from app.taiwan.market_rules import SecuritiesTaxModel
from app.taiwan.realtime import get_realtime_service
from app.taiwan.realtime.calendar import TaiwanTradingCalendar, taipei_today
from app.taiwan.research_context import (
    TaiwanStockResearchContext,
    TaiwanStockResearchContextService,
)
from app.taiwan.screener import TaiwanScreenerRequest, TaiwanScreenerResponse
from app.taiwan.screener_nl import (
    TaiwanScreenerTranslateQuery,
    TaiwanScreenerTranslation,
    TaiwanScreenerTranslator,
)
from app.taiwan.screener_strategy_store import (
    TaiwanScreenerStrategy,
    get_screener_strategy_store,
)
from app.taiwan.social_sentiment import (
    list_social_sentiment_history,
    load_social_sentiment,
    load_social_sentiment_date,
    load_social_sentiment_snapshot,
)
from app.taiwan.social_sentiment_jobs import get_social_sentiment_job_manager
from app.taiwan.symbol import parse_symbol
from app.taiwan.universe import MarketProfileBridge, get_security_master

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/taiwan", tags=["taiwan"])
router.include_router(market_breadth_router)
router.include_router(market_research_router)


class AdviceOutcomeEvaluateRequest(BaseModel):
    evaluated_as_of: dt_date
    view: AdviceView = "raw"


@router.get("/social-sentiment")
def get_taiwan_social_sentiment(
    target_date: str | None = Query(default=None),
    snapshot_slot: str | None = Query(default=None),
):
    """讀取本地社群情緒結果，不在 request time 連線抓取外部來源。"""
    if target_date:
        try:
            parsed_date = dt_date.fromisoformat(target_date)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"無效的日期格式: {target_date}") from exc
        if snapshot_slot is not None and snapshot_slot not in {"pre_open", "after_close"}:
            raise HTTPException(status_code=400, detail=f"無效的快照時段: {snapshot_slot}")
        payload = (
            load_social_sentiment_snapshot(parsed_date, snapshot_slot)
            if snapshot_slot
            else load_social_sentiment_date(parsed_date)
        )
    else:
        payload = load_social_sentiment()
    if payload is None:
        raise HTTPException(status_code=404, detail="尚未產生社群情緒結果")
    return payload


@router.get("/social-sentiment/history")
def get_taiwan_social_sentiment_history(limit: int = Query(default=30, ge=1, le=120)):
    return {"items": list_social_sentiment_history(limit)}


class SocialSentimentRunRequest(BaseModel):
    mode: Literal["manual"] = "manual"


@router.post("/social-sentiment/run")
def run_taiwan_social_sentiment(req: SocialSentimentRunRequest):
    """Start the shared pipeline in a background thread and return immediately."""
    return get_social_sentiment_job_manager().start_manual()


@router.get("/social-sentiment/jobs/{job_id}")
def get_taiwan_social_sentiment_job(
    job_id: str,
    source: str | None = Query(default=None),
    symbol: str | None = Query(default=None, max_length=32),
    q: str | None = Query(default=None, max_length=200),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
):
    if source not in {None, "ptt", "dcard"}:
        raise HTTPException(status_code=400, detail=f"無效的社群來源: {source}")
    job = get_social_sentiment_job_manager().get_job(
        job_id,
        source=source,
        symbol=symbol,
        keyword=q,
        offset=offset,
        limit=limit,
    )
    if job is None:
        raise HTTPException(status_code=404, detail=f"找不到任務 {job_id}")
    return job


def _resolve_portfolio_instrument(symbol: str, trade_date: dt_date):
    try:
        canonical = parse_symbol(symbol).canonical
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid Taiwan symbol: {symbol}") from exc

    instrument = get_security_master().get_instrument(canonical)
    if instrument is None or not instrument.is_supported or instrument.instrument_type not in {"stock", "etf"}:
        raise HTTPException(status_code=404, detail=f"找不到可交易的台股股票或 ETF: {canonical}")

    if trade_date > taipei_today():
        raise HTTPException(status_code=422, detail="成交日期不可晚於今日")
    calendar = TaiwanTradingCalendar()
    trading_day = calendar.is_trading_day(trade_date)
    if trading_day is False:
        raise HTTPException(status_code=422, detail=f"{trade_date.isoformat()} 是台灣市場休市日，不可記錄成交")
    if trading_day is None:
        trading_day = False
        if trade_date == taipei_today():
            try:
                quote = get_realtime_service().get_quotes([canonical]).get(canonical)
                meta = quote.source_meta if quote else None
                trading_day = bool(
                    quote
                    and quote.trade_date == trade_date
                    and meta
                    and meta.trade_date == trade_date
                    and meta.source_type == "first_party_web_endpoint"
                    and not meta.is_stale
                )
            except Exception:
                trading_day = False
        if not trading_day:
            try:
                trading_day = TaiwanDailyStore().has_symbol_date(canonical, trade_date)
            except Exception:
                trading_day = False
        if not trading_day:
            raise HTTPException(status_code=422, detail="目前無法以台灣市場日資料確認成交日期，請確認日資料已更新後再記錄")

    try:
        tax_class = MarketProfileBridge.get_tax_class(instrument)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return canonical, instrument, tax_class, trading_day


@router.get("/portfolio-instrument")
def get_taiwan_portfolio_instrument(symbol: str, trade_date: dt_date):
    """Validate a portfolio symbol and trade date using Taiwan's security master and calendar."""
    canonical, instrument, tax_class, _ = _resolve_portfolio_instrument(symbol, trade_date)
    return {
        "symbol": canonical,
        "instrument_type": instrument.instrument_type,
        "is_supported": True,
        "tax_class": tax_class.value,
        "trading_day_status": "verified",
    }


@router.get("/transaction-tax")
def get_taiwan_transaction_tax(
    symbol: str,
    trade_date: dt_date,
    trade_value: float = Query(..., gt=0),
    is_day_trade: bool = False,
):
    """Estimate sell-side securities tax using the canonical Taiwan market rules."""
    canonical, _, tax_class, _ = _resolve_portfolio_instrument(symbol, trade_date)
    try:
        if is_day_trade:
            raise ValueError("目前沒有可驗證此標的當沖資格與可配對股數的資料，暫不套用當沖優惠稅率")
        tax_model = SecuritiesTaxModel()
        rate = tax_model.get_tax_rate(tax_class=tax_class, is_day_trade=is_day_trade, trade_date=trade_date)
        tax_amount = tax_model.calc_tax(
            trade_value,
            tax_class=tax_class,
            is_day_trade=is_day_trade,
            trade_date=trade_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "symbol": canonical,
        "tax_class": tax_class.value,
        "tax_rate": rate,
        "tax_amount": tax_amount,
    }


@router.get("/data-status", response_model=FreshnessStatus)
def get_taiwan_data_status():
    """獲取台股市場三大本地數據集 (日線、三大法人、融資券) 之最新落盤日期與市場時效狀況。"""
    svc = TaiwanDailyUpdateService()
    return svc.get_freshness()


@router.get("/history-status", response_model=TaiwanHistoryStatus)
def get_taiwan_history_status():
    """獲取台股歷史日 K 資料狀態 (是否已落盤、最早/最新日期、交易日數、是否需初始化)。"""
    return get_history_status()


@router.post("/bootstrap/run")
def run_taiwan_bootstrap():
    """觸發從 GitHub Release 一鍵下載官方歷史日 K 資料包並自動解壓與補齊最新日。"""
    svc = TaiwanBootstrapService()
    job_id = svc.start_bootstrap()
    job = svc.get_job(job_id)
    return {"job_id": job_id, "status": job.status if job else "pending"}


@router.get("/bootstrap/jobs/{job_id}", response_model=BootstrapJobState)
def get_taiwan_bootstrap_job(job_id: str):
    """查詢台股歷史資料 Bootstrap 下載與解壓進度。"""
    svc = TaiwanBootstrapService()
    job = svc.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"找不到任務 {job_id}")
    return job


@router.get("/bootstrap/stream/{job_id}")
async def stream_taiwan_bootstrap_job(job_id: str):
    """SSE 即時推播 Bootstrap 任務進度。"""
    import asyncio

    from sse_starlette.sse import EventSourceResponse

    svc = TaiwanBootstrapService()
    job = svc.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"找不到任務 {job_id}")

    async def event_generator():
        while True:
            cur = svc.get_job(job_id)
            if not cur:
                break
            payload = cur.model_dump_json()
            yield {"event": "progress", "data": payload}
            if cur.status in ("success", "failed"):
                break
            await asyncio.sleep(0.5)

    return EventSourceResponse(event_generator())


@router.post("/bootstrap/update-latest")
def update_taiwan_history_to_latest():
    """已有歷史資料時，直接增量補齊至當前最新交易日（不重新下載整包）。"""
    svc = TaiwanBootstrapService()
    try:
        return svc.update_to_latest()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Update to latest failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"更新至最新交易日失敗: {exc}") from exc


@router.get("/capabilities", response_model=list[TaiwanDatasetCapability])
def get_taiwan_data_capabilities():
    """Expose product usage boundaries without fetching network data."""
    return capability_matrix()


@router.post("/stocks/compare", response_model=TaiwanStockComparisonResponse)
def compare_taiwan_stocks(request: TaiwanStockCompareRequest):
    """台股多標的 (2-5檔) 確定性客觀比較 (純本地計算，0 AI 調用，0 執行期外部請求)。

    NOTE: 此路由必須註冊於任何 /stocks/{symbol} 動態路由之前，
    避免 FastAPI 依註冊順序將 'compare' 誤判為 {symbol} 路徑參數。
    """
    from datetime import date as dt_date
    target_dt = None
    if request.date:
        try:
            target_dt = dt_date.fromisoformat(request.date)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"無效的日期格式: {request.date}，請使用 YYYY-MM-DD") from e

    svc = TaiwanStockComparisonService()
    try:
        return svc.compare(request.symbols, target_date=target_dt)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Taiwan stock comparison failed for %s: %s", request.symbols, e)
        raise HTTPException(
            status_code=500,
            detail=f"台股標的比較失敗: {e}",
        ) from e


@router.post("/stocks/compare/ai-research", response_model=TaiwanComparisonAIResearchResponse)
async def compare_taiwan_stocks_ai_research(request: TaiwanComparisonAIRequest):
    """依據本地確定性事實證據生成台股多標的客觀 AI 比較報告 (封閉事實邊界、無優劣排序、需使用者主動觸發)。

    NOTE: 此路由必須註冊於任何 /stocks/{symbol}/... 動態路由之前 (見上方 /stocks/compare 說明)。
    """
    from datetime import date as dt_date
    target_dt = None
    if request.date:
        try:
            target_dt = dt_date.fromisoformat(request.date)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"無效的日期格式: {request.date}，請使用 YYYY-MM-DD") from e

    svc = TaiwanComparisonAIResearchService()
    try:
        return await svc.generate_comparison(request.symbols, target_date=target_dt)
    except Exception as e:
        logger.exception("Taiwan stock comparison AI research failed for %s: %s", request.symbols, e)
        raise HTTPException(
            status_code=500,
            detail=f"AI 比較報告生成失敗: {e}",
        ) from e


@router.get("/data/{symbol}", response_model=TaiwanCurrentDataResponse)
def get_taiwan_current_data(
    symbol: str,
):
    """Return isolated current/reference sections for one Taiwan security."""
    try:
        parse_symbol(symbol)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid Taiwan symbol: {symbol}") from exc
    try:
        return get_taiwan_current_data_service().get_current_data(symbol)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/stocks/{symbol}", response_model=TaiwanStockDetailResponse)
def get_taiwan_stock_detail(
    request: Request,
    symbol: str,
    days: int = Query(120, ge=10, le=1000, description="歷史日 K 線根數"),
):
    """獲取單一台股標的完整研究工作台資料 (一站式聚合，不因單一來源失敗中斷)。"""
    try:
        # Validate symbol format (e.g. 2330.TWSE, 8069.TPEX)
        parse_symbol(symbol)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"無效的台股代碼格式: {symbol}。請使用標準規範代碼，例如 2330.TWSE, 8069.TPEX",
        ) from e

    svc = get_taiwan_stock_detail_service()
    try:
        return svc.get_stock_detail(symbol, days=days, repo=getattr(request.app.state, "repo", None))
    except Exception as e:
        logger.exception("Failed to aggregate Taiwan stock detail for %s: %s", symbol, e)
        raise HTTPException(
            status_code=500,
            detail=f"台股個股資訊聚合失敗: {e}",
        ) from e


@router.get("/stocks/{symbol}/research-context", response_model=TaiwanStockResearchContext)
def get_taiwan_stock_research_context(
    symbol: str,
    date: str | None = Query(None, description="指定交易日 (YYYY-MM-DD)，預設為最新已完成交易日"),
):
    """取得單一台股/ETF 標的強型別確定性研究證據上下文 (提供 AI 分析或量化研究，0 執行期外部請求)。"""
    from datetime import date as dt_date
    target_dt = None
    if date:
        try:
            target_dt = dt_date.fromisoformat(date)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"無效的日期格式: {date}，請使用 YYYY-MM-DD") from e

    svc = TaiwanStockResearchContextService()
    try:
        return svc.get_research_context(symbol, target_date=target_dt)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.exception("Failed to compute Taiwan stock research context for %s: %s", symbol, e)
        raise HTTPException(
            status_code=500,
            detail=f"台股個股研究上下文生成失敗: {e}",
        ) from e


@router.get("/stocks/{symbol}/ai-explain")
def get_stored_ai_explain(symbol: str, min_as_of: str | None = None) -> dict:
    """Latest stored (e.g. after-close auto) research for a radar card; no LLM call."""
    from app.taiwan.auto_ai_explain import stored_explanation

    try:
        canonical = parse_symbol(symbol).canonical
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"response": stored_explanation(canonical, min_as_of)}


@router.post(
    "/stocks/{symbol}/ai-research",
    response_model=TaiwanAIResearchResponse | TaiwanAIAdviceResponse,
)
async def generate_taiwan_stock_ai_research(
    symbol: str,
    request: Request,
    payload: TaiwanAIResearchRequest | None = None,
):
    """依本地封閉證據生成 Research，或伺服器買點/交易計畫約束的 Advice。"""
    from datetime import date as dt_date
    target_dt = None
    if payload and payload.date:
        try:
            target_dt = dt_date.fromisoformat(payload.date)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"無效的日期格式: {payload.date}，請使用 YYYY-MM-DD") from e

    selection = None
    if target_dt is None:
        import asyncio

        from app.taiwan.beginner_selection import selection_evidence

        selection = await asyncio.to_thread(selection_evidence, symbol)
    try:
        if payload is not None and payload.purpose == "advice":
            assert payload.strategy_id is not None
            data_dir = request.app.state.repo.store.data_dir
            advice_svc = TaiwanAIAdviceService(data_dir)
            advice_run, review_run = await advice_svc.generate(
                symbol,
                strategy_id=payload.strategy_id,
                target_date=target_dt,
                personal_context=payload.personal_context,
                review=payload.review,
                refresh=payload.refresh,
                selection_evidence=selection,
            )
            if advice_run.response.status == "success":
                history = get_ai_research_history_store()
                history.ensure_advice(advice_run)
                if review_run is not None:
                    history.ensure_review(advice_run, review_run)
            return advice_run.response

        svc = TaiwanAIResearchService()
        run = await svc.generate_run(
            symbol,
            target_date=target_dt,
            personal_context=payload.personal_context if payload else None,
            purpose=payload.purpose if payload else "research",
            refresh=payload.refresh if payload else False,
            selection_evidence=selection,
        )
        if run.response.status == "success":
            get_ai_research_history_store().ensure_report(run)
        return run.response
    except Exception as e:
        logger.exception("Failed to generate AI stock research report for %s: %s", symbol, e)
        raise HTTPException(
            status_code=500,
            detail=f"AI 研究報告生成失敗: {e}",
        ) from e


@router.get("/ai-research/history")
def list_taiwan_ai_research_history(
    symbol: str | None = Query(None),
    from_date: Annotated[dt_date | None, Query(alias="from")] = None,
    to_date: Annotated[dt_date | None, Query(alias="to")] = None,
    purpose: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    """List locally persisted research artifacts without invoking AI."""
    return get_ai_research_history_store().list(
        symbol=symbol,
        from_date=from_date,
        to_date=to_date,
        purpose=purpose,
        limit=limit,
    )


@router.get("/ai-research/history/{record_id}")
def get_taiwan_ai_research_history(record_id: str):
    record = get_ai_research_history_store().get(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="找不到指定的 AI 研究歷史")
    return record


@router.get("/ai-research/history/{record_id}/compare/{other_id}")
def compare_taiwan_ai_research_history(record_id: str, other_id: str):
    """Compare frozen evidence, generation configuration, and interpretation deterministically."""
    try:
        return get_ai_research_history_store().compare(record_id, other_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="找不到指定的 AI 研究歷史") from exc
    except AIResearchHistoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/ai-research/history/{advice_id}/forward-status")
def get_taiwan_advice_forward_status(
    advice_id: str,
    view: Annotated[AdviceView, Query()] = "raw",
):
    try:
        return get_advice_tracking_service().forward_status(advice_id, view=view)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="找不到指定的 Advice") from exc


@router.post("/ai-research/history/{advice_id}/evaluate-outcome")
def evaluate_taiwan_advice_outcome(
    advice_id: str,
    payload: AdviceOutcomeEvaluateRequest,
):
    try:
        return get_advice_tracking_service().evaluate_outcome(
            advice_id,
            evaluated_as_of=payload.evaluated_as_of,
            view=payload.view,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="找不到指定的 Advice") from exc
    except (AIResearchHistoryError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/ai-research/history/{advice_id}/reflection")
def create_taiwan_advice_reflection(advice_id: str, payload: ReflectionCreate):
    try:
        return get_advice_tracking_service().create_reflection(advice_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="找不到指定的 Advice") from exc
    except AIResearchHistoryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/ai-research/advice-statistics")
def get_taiwan_advice_statistics(
    plan_identity: Annotated[str, Query(min_length=1)],
    view: Annotated[AdviceView, Query()] = "raw",
    minimum_sample: Annotated[int, Query(ge=5, le=1000)] = 5,
):
    return get_advice_tracking_service().statistics(
        plan_identity,
        view=view,
        minimum_sample=minimum_sample,
    )


@router.get("/abnormal-diagnostics", response_model=TaiwanAbnormalDiagnosticsSnapshot)
def get_taiwan_abnormal_diagnostics(
    date: str | None = Query(None, description="指定交易日 (YYYY-MM-DD)，預設為最新已完成交易日"),
    include_all: bool = Query(False, description="是否包含無觸發異常訊號的標的"),
    signal_type: str | None = Query(None, description="依訊號類型篩選 (如 VOLUME_SPIKE, FOREIGN_FLOW_SPIKE 等)"),
    industry: str | None = Query(None, description="依產業篩選"),
    exchange: str | None = Query(None, description="依交易所篩選 (TWSE 或 TPEX)"),
    include_context_snapshots: bool = Query(False, description="是否一併回傳市場與產業快照，供 Dashboard 共用同一批計算"),
):
    """取得台股全市場確定性異常異動與資金流向診斷快照 (純本地客觀計算，0 執行期外部請求)。"""
    from datetime import date as dt_date
    target_dt = None
    if date:
        try:
            target_dt = dt_date.fromisoformat(date)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"無效的日期格式: {date}，請使用 YYYY-MM-DD") from e

    svc = TaiwanAbnormalDiagnosticsService()
    try:
        return svc.get_diagnostics(
            target_date=target_dt,
            include_all=include_all,
            signal_filter=signal_type,
            industry_filter=industry,
            exchange_filter=exchange,
            include_context_snapshots=include_context_snapshots,
        )
    except Exception as e:
        logger.exception("Failed to compute Taiwan abnormal diagnostics: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"台股異常異動診斷快照生成失敗: {e}",
        ) from e


@router.post("/screener/run", response_model=TaiwanScreenerResponse)
def run_taiwan_screener(request: TaiwanScreenerRequest):
    """執行台股批次選股 (基於本地 Parquet 持久化資料庫與 Security Master)。"""
    from app.taiwan.screener import TaiwanScreenerService
    svc = TaiwanScreenerService()
    try:
        return svc.run(request)
    except Exception as e:
        logger.exception("Taiwan screener execution failed: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"台股選股執行失敗: {e}",
        ) from e


@router.post("/screener/translate", response_model=TaiwanScreenerTranslation)
async def translate_taiwan_screener_query(payload: TaiwanScreenerTranslateQuery):
    """將自然語言選股描述轉換為強型別 TaiwanScreenerRequest 條件 (純翻譯層，不直出股票)。"""
    translator = TaiwanScreenerTranslator()
    return await translator.translate(payload.query)


class ScreenerStrategyCreateRequest(BaseModel):
    name: str
    conditions: dict[str, Any]
    description: str | None = None


class ScreenerStrategyUpdateRequest(BaseModel):
    name: str
    conditions: dict[str, Any]
    description: str | None = None


@router.get("/screener/strategies", response_model=list[TaiwanScreenerStrategy])
def list_taiwan_screener_strategies():
    """取得所有台股自訂與內建選股策略。"""
    store = get_screener_strategy_store()
    return store.list_strategies()


@router.post("/screener/strategies", response_model=TaiwanScreenerStrategy)
def create_taiwan_screener_strategy(payload: ScreenerStrategyCreateRequest):
    """建立自訂台股選股策略。"""
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="策略名稱不可為空")
    store = get_screener_strategy_store()
    return store.save_strategy(
        name=payload.name,
        conditions=payload.conditions,
        description=payload.description,
    )


@router.put("/screener/strategies/{strategy_id}", response_model=TaiwanScreenerStrategy)
def update_taiwan_screener_strategy(strategy_id: str, payload: ScreenerStrategyUpdateRequest):
    """更新自訂台股選股策略。"""
    if strategy_id.startswith("preset_"):
        raise HTTPException(status_code=400, detail="內建預設策略不可修改")
    store = get_screener_strategy_store()
    existing = store.get_strategy(strategy_id)
    if not existing:
        raise HTTPException(status_code=404, detail="找不到指定的策略")
    return store.save_strategy(
        name=payload.name,
        conditions=payload.conditions,
        description=payload.description,
        strategy_id=strategy_id,
    )


@router.delete("/screener/strategies/{strategy_id}")
def delete_taiwan_screener_strategy(strategy_id: str):
    """刪除自訂台股選股策略。"""
    if strategy_id.startswith("preset_"):
        raise HTTPException(status_code=400, detail="內建預設策略不可刪除")
    store = get_screener_strategy_store()
    success = store.delete_strategy(strategy_id)
    if not success:
        raise HTTPException(status_code=404, detail="找不到指定的策略或該策略不可刪除")
    return {"ok": True, "deleted_id": strategy_id}



@router.get("/market-intelligence", response_model=TaiwanMarketIntelligenceSnapshot)
def get_taiwan_market_intelligence(
    date: str | None = Query(None, description="指定交易日 (YYYY-MM-DD)，預設為最新已完成交易日"),
):
    """取得台股全市場量化統計快照 (包含漲跌家數、漲跌停家數、成交量能、三大法人與資券總計，純本地確定性計算)。"""
    from datetime import date as dt_date
    target_dt = None
    if date:
        try:
            target_dt = dt_date.fromisoformat(date)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"無效的日期格式: {date}，請使用 YYYY-MM-DD") from e

    svc = TaiwanMarketIntelligenceService()
    try:
        return svc.get_snapshot(target_date=target_dt)
    except Exception as e:
        logger.exception("Failed to compute Taiwan market intelligence snapshot: %s", e)
        raise HTTPException(status_code=500, detail=f"市場情報快照生成失敗: {e}") from e


@router.get("/industry-intelligence", response_model=TaiwanIndustryIntelligenceSnapshot)
def get_taiwan_industry_intelligence(
    date: str | None = Query(None, description="指定交易日 (YYYY-MM-DD)，預設為最新已完成交易日"),
    sort_by: str = Query("turnover", description="排序欄位: turnover, median_change_pct, relative_strength_5d, relative_strength_20d, advance_ratio, foreign_net, investment_trust_net"),
    order: str = Query("desc", description="排序順序: desc 或 asc"),
):
    """取得台股各產業類股量化統計快照 (包含類股漲跌中位數、5D/20D 相對強弱 RS、成交占比與三大法人動向，純本地確定性計算)。"""
    from datetime import date as dt_date
    target_dt = None
    if date:
        try:
            target_dt = dt_date.fromisoformat(date)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"無效的日期格式: {date}，請使用 YYYY-MM-DD") from e

    svc = TaiwanIndustryIntelligenceService()
    try:
        return svc.get_snapshot(target_date=target_dt, sort_by=sort_by, order=order)
    except Exception as e:
        logger.exception("Failed to compute Taiwan industry intelligence snapshot: %s", e)
        raise HTTPException(status_code=500, detail=f"產業情報快照生成失敗: {e}") from e


# ── A11 Event Center, News & Sentiment Endpoints ───────────────


@router.get("/events")
def get_taiwan_events(
    scope: str = Query("all", description="事件範圍: today, week, portfolio, watchlist, all"),
    symbols: str | None = Query(None, description="逗號分隔的股票代號清單 (用於 portfolio / watchlist)"),
    symbol: str | None = Query(None, description="單一股票代號"),
    event_types: str | None = Query(None, description="逗號分隔的事件類型"),
    severity: str | None = Query(None, description="嚴重等級過濾: info, attention, risk"),
    date: str | None = Query(None, description="基準日期 (YYYY-MM-DD)"),
    limit: int = Query(100, ge=1, le=500),
    refresh: bool = Query(False, description="重新取得事件來源；失敗保留 stale 資料"),
):
    """取得台股重大事件清單 (支援今日、本週、持股、自選與全市場範圍)。"""
    from app.taiwan.events_service import get_event_service
    from app.taiwan.realtime.calendar import taipei_today

    target_dt = None
    if date:
        try:
            target_dt = dt_date.fromisoformat(date)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"無效的日期格式: {date}") from e

    sym_list = [s.strip() for s in symbols.split(",") if s.strip()] if symbols else None
    ev_type_list = [t.strip() for t in event_types.split(",") if t.strip()] if event_types else None

    # Cast scope
    valid_scopes = {"today", "week", "portfolio", "watchlist", "all"}
    scope_cast = scope if scope in valid_scopes else "all"

    # Cast severity
    sev_cast = severity if severity in ("info", "attention", "risk") else None

    svc = get_event_service()
    try:
        items = svc.get_events(
            scope=scope_cast,  # type: ignore[arg-type]
            symbols=sym_list,
            symbol=symbol,
            event_types=ev_type_list,
            severity=sev_cast,  # type: ignore[arg-type]
            target_date=target_dt,
            limit=limit,
            include_mops=True,
            force_refresh=refresh,
        )
        status, sources_status = svc.get_product_sources_status()
        return {
            "events": items,
            "total": len(items),
            "as_of_date": (target_dt or taipei_today()).isoformat(),
            "status": status,
            "sources_status": sources_status,
        }
    except Exception as e:
        logger.exception("Failed to get market events: %s", e)
        raise HTTPException(status_code=500, detail=f"事件取得失敗: {e}") from e


@router.get("/events/candidates")
def get_taiwan_event_candidates(
    limit: int = Query(10, ge=1, le=50),
):
    """取得事件驅動值得查看標的 (如處置、注意、除權息、營收公布、重大風險)。"""
    from app.taiwan.events_service import get_event_service
    from app.taiwan.realtime.calendar import taipei_today

    svc = get_event_service()
    try:
        candidates = svc.get_event_candidates(limit=limit)
        return {
            "candidates": candidates,
            "as_of_date": taipei_today().isoformat(),
            "total": len(candidates),
        }
    except Exception as e:
        logger.exception("Failed to get event candidates: %s", e)
        raise HTTPException(status_code=500, detail=f"事件候選股取得失敗: {e}") from e


class CheckEventAlertsRequest(BaseModel):
    symbols: list[str] = []


@router.post("/alerts/check-events")
def check_taiwan_event_alerts(
    body: CheckEventAlertsRequest | None = None,
):
    """檢查並觸發持股或自選重大事件提醒 (處置、暫停、恢復、減資、下市、除權息)。"""
    from app.taiwan.events_service import get_event_service

    svc = get_event_service()
    target_symbols = body.symbols if body else []
    try:
        triggered = svc.trigger_event_alerts(target_symbols)
        return {
            "triggered_count": len(triggered),
            "triggered_alerts": triggered,
            "status": "success",
        }
    except Exception as e:
        logger.exception("Failed to check and trigger event alerts: %s", e)
        raise HTTPException(status_code=500, detail=f"事件提醒檢查失敗: {e}") from e


@router.get("/news/{symbol}")
def get_taiwan_stock_news(
    symbol: str,
    limit: int = Query(15, ge=1, le=50),
    refresh: bool = Query(False, description="是否強制重取"),
):
    """取得個股近期新聞 (使用 FinMind TaiwanStockNews，支援確定性去重與快取保護)。"""
    from app.taiwan.news_service import get_news_service

    svc = get_news_service()
    try:
        return svc.get_recent_news(symbol_or_code=symbol, limit=limit, force_refresh=refresh)
    except Exception as e:
        logger.exception("Failed to fetch stock news for %s: %s", symbol, e)
        raise HTTPException(status_code=500, detail=f"新聞取得失敗: {e}") from e


@router.get("/market-sentiment")
def get_taiwan_market_sentiment(
    date: str | None = Query(None, description="指定交易日 (YYYY-MM-DD)，預設為最新交易日"),
):
    """取得台股市場情緒綜合評估與客觀依據清單 (綜合現貨指數、市場廣度、三大法人與台指期貨選擇權籌碼)。"""
    from app.taiwan.market_sentiment_service import get_market_sentiment_service

    target_dt = None
    if date:
        try:
            target_dt = dt_date.fromisoformat(date)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"無效的日期格式: {date}") from e

    svc = get_market_sentiment_service()
    try:
        return svc.get_market_sentiment(target_date=target_dt)
    except Exception as e:
        logger.exception("Failed to calculate market sentiment: %s", e)
        raise HTTPException(status_code=500, detail=f"市場情緒計算失敗: {e}") from e


# ── A12: Selection Review (選股復盤) ─────────────────────────────

class ForwardBatchLockRequest(BaseModel):
    strategy_id: str = "trend_liquidity_v1"


@router.post("/selection-review/forward-batches")
def lock_selection_forward_batch(body: ForwardBatchLockRequest | None = None):
    """由後端選股並鎖定當日正式前瞻批次；重送回傳同一批次。"""
    from app.taiwan.selection_review_service import get_selection_review_service

    try:
        strategy_id = body.strategy_id if body else "trend_liquidity_v1"
        return get_selection_review_service().lock_forward_batch(strategy_id=strategy_id).model_dump()
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/selection-review/forward-batches/stats")
def get_selection_forward_batch_stats(strategy_id: str | None = Query(default=None)):
    from app.taiwan.selection_review_service import get_selection_review_service

    try:
        return get_selection_review_service().get_forward_batch_stats(strategy_id=strategy_id).model_dump()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/selection-review/forward-batches")
def list_selection_forward_batches(strategy_id: str | None = Query(default=None)):
    from app.taiwan.selection_review_service import get_selection_review_service

    return [s.model_dump() for s in get_selection_review_service().list_snapshots("forward_batch", strategy_id)]


@router.get("/selection-review/forward-batches/{batch_id}")
def get_selection_forward_batch_detail(batch_id: str):
    from app.taiwan.selection_review_service import get_selection_review_service

    svc = get_selection_review_service()
    snapshot = svc.get_snapshot(batch_id)
    if snapshot is None or snapshot.record_type != "forward_batch":
        raise HTTPException(status_code=404, detail="找不到指定的正式前瞻批次")
    detail = svc.get_snapshot_review(batch_id)
    return detail.model_dump()

@router.post("/selection-review/snapshots")
def save_selection_snapshot(body: dict[str, Any]):
    """保存本次選股結果為不可變快照 (Point-in-time snapshot)。"""
    from app.taiwan.selection_review_models import SaveSelectionSnapshotRequest
    from app.taiwan.selection_review_service import get_selection_review_service

    try:
        req = SaveSelectionSnapshotRequest(**body)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"選股快照格式錯誤: {e}") from e

    svc = get_selection_review_service()
    try:
        saved = svc.save_snapshot(req)
        return saved.model_dump()
    except Exception as e:
        logger.exception("Failed to save selection snapshot: %s", e)
        raise HTTPException(status_code=500, detail=f"快照儲存失敗: {e}") from e


@router.get("/selection-review/snapshots")
def list_selection_snapshots():
    """取得所有已儲存的選股快照清單及 5D/20D 復盤進度。"""
    from app.taiwan.selection_review_service import get_selection_review_service

    svc = get_selection_review_service()
    try:
        return [s.model_dump() for s in svc.list_snapshots("research")]
    except Exception as e:
        logger.exception("Failed to list selection snapshots: %s", e)
        raise HTTPException(status_code=500, detail=f"快照清單讀取失敗: {e}") from e


@router.get("/selection-review/snapshots/{snapshot_id}")
def get_selection_snapshot_detail(snapshot_id: str):
    """取得特定快照的 1D/5D/20D 復盤評估明細與 Benchmark 比較。"""
    from app.taiwan.selection_review_service import get_selection_review_service

    svc = get_selection_review_service()
    try:
        snapshot = svc.get_snapshot(snapshot_id)
        if snapshot is None or snapshot.record_type != "research":
            raise HTTPException(status_code=404, detail=f"找不到指定的研究快照: {snapshot_id}")
        detail = svc.get_snapshot_review(snapshot_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get snapshot review for %s: %s", snapshot_id, e)
        raise HTTPException(status_code=500, detail=f"復盤計算失敗: {e}") from e
    if not detail:
        raise HTTPException(status_code=404, detail=f"找不到指定的選股快照: {snapshot_id}")
    try:
        return detail.model_dump()
    except Exception as e:
        logger.exception("Failed to serialize snapshot review for %s: %s", snapshot_id, e)
        raise HTTPException(status_code=500, detail=f"復盤資料序列化失敗: {e}") from e


@router.delete("/selection-review/snapshots/{snapshot_id}")
def delete_selection_snapshot(snapshot_id: str):
    """刪除指定的選股快照。"""
    from app.taiwan.selection_review_service import get_selection_review_service

    svc = get_selection_review_service()
    try:
        success = svc.delete_snapshot(snapshot_id)
    except PermissionError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if not success:
        raise HTTPException(status_code=404, detail=f"找不到或無法刪除快照: {snapshot_id}")
    return {"ok": True, "deleted_id": snapshot_id}


@router.get("/selection-review/strategy-stats")
def get_strategy_review_stats():
    """取得各已儲存策略聚合之 5D/20D 勝率與平均報酬統計。"""
    from app.taiwan.selection_review_service import get_selection_review_service

    svc = get_selection_review_service()
    try:
        return [s.model_dump() for s in svc.get_strategy_reviews()]
    except Exception as e:
        logger.exception("Failed to get strategy review stats: %s", e)
        raise HTTPException(status_code=500, detail=f"策略統計讀取失敗: {e}") from e


@router.get("/selection-review/condition-stats")
def get_condition_review_stats():
    """依篩選條件/入選理由聚合之 5D/20D 敘述性統計 (不作因果推論)。"""
    from app.taiwan.selection_review_service import get_selection_review_service

    svc = get_selection_review_service()
    try:
        return [c.model_dump() for c in svc.get_condition_reviews()]
    except Exception as e:
        logger.exception("Failed to get condition review stats: %s", e)
        raise HTTPException(status_code=500, detail=f"條件統計讀取失敗: {e}") from e


# ── Beginner Stock Picker v1 (今日選股) ─────────────────────────

@router.get("/beginner-selection")
def get_beginner_selection(limit: Annotated[int, Query(ge=1, le=20)] = 20):
    """確定性、可解釋的初學者今日選股；排序不是報酬預測，AI 不參與排名。"""
    from app.taiwan.beginner_selection import BeginnerSelectionService

    try:
        return BeginnerSelectionService().build(limit=limit).model_dump(mode="json")
    except Exception as e:
        logger.exception("Failed to build beginner selection: %s", e)
        raise HTTPException(status_code=500, detail="今日選股資料彙整失敗") from e


class BeginnerRadarRequest(BaseModel):
    # Holdings are user-sensitive: they travel in the body, never in the URL or access log.
    holdings: list[str] = Field(default_factory=list, max_length=200)


@router.post("/beginner-selection/radar")
def get_beginner_entry_radar(req: BeginnerRadarRequest):
    """每日承接雷達：今日選股＋持股＋自選股，比對盤中現價與計畫價位。"""
    from app.services import watchlist
    from app.taiwan.beginner_radar import build_radar, parse_holdings, taiwan_symbols
    from app.taiwan.beginner_selection import BeginnerSelectionService
    from app.taiwan.realtime import get_market_status, get_realtime_service, taipei_now

    try:
        held = parse_holdings(req.holdings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return build_radar(
            BeginnerSelectionService(),
            holdings=held,
            watchlist=taiwan_symbols(row.get("symbol") for row in watchlist.list_symbols()),
            get_quotes=lambda symbols: get_realtime_service().get_quotes(symbols),
            # Without a quote, "open" needs a verified trading day (holidays, closures).
            market_session=lambda: get_market_status(taipei_now(), require_verified_trading_day=True).value,
        ).model_dump(mode="json")
    except Exception as exc:
        logger.exception("Failed to build entry radar: %s", exc)
        raise HTTPException(status_code=500, detail="承接雷達資料彙整失敗") from exc


@router.get("/beginner-selection/compare")
def get_beginner_selection_comparison(symbols: str = Query(...)):
    """比較 2 至 5 檔股票既有確定性證據，保留今日選股原排名。"""
    from app.taiwan.beginner_comparator import build_comparison, validate_symbols

    try:
        canonical = validate_symbols(symbols)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return build_comparison(canonical).model_dump(mode="json")
    except Exception as exc:
        logger.exception("Failed to compare beginner selection: %s", exc)
        raise HTTPException(status_code=500, detail="股票比較資料彙整失敗") from exc


@router.get("/beginner-selection/stocks/{symbol}")
def get_beginner_selection_symbol(symbol: str):
    """單一股票的初學者結論：為什麼值得看、或為什麼沒被選。"""
    from app.taiwan.beginner_selection import BeginnerSelectionService

    try:
        canonical = parse_symbol(symbol).canonical
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"無效的台股代號: {symbol}") from exc
    try:
        return BeginnerSelectionService().evaluate_symbol(canonical).model_dump(mode="json")
    except Exception as e:
        logger.exception("Failed to evaluate beginner selection for %s: %s", symbol, e)
        raise HTTPException(status_code=500, detail="個股初學者結論彙整失敗") from e


@router.get("/external-context")
def get_external_context(symbol: str | None = None):
    """Optional market context. Provider failures never fail Taiwan core APIs."""
    from app.taiwan.external_context import ExternalContextService

    canonical = None
    if symbol:
        try:
            canonical = parse_symbol(symbol).canonical
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"無效的台股代號: {symbol}") from exc
    official: dict[str, float | None] = {}
    if canonical:
        try:
            from app.taiwan.beginner_selection import BeginnerSelectionService

            panel = BeginnerSelectionService().evaluate_symbol(canonical).candidate.technical_panel
            if panel is not None:
                official = {"pe": panel.fundamentals.pe, "eps": panel.fundamentals.eps}
        except Exception as exc:
            logger.debug("External cross-check official evidence unavailable: %s", type(exc).__name__)
    return ExternalContextService().get(canonical, official=official).model_dump(mode="json")


# ── A12: Daily Brief (每日 AI 摘要) ──────────────────────────────

@router.get("/daily-brief")
def get_daily_brief(target_date: str | None = None):
    """取得或建構今日台股客觀確定性簡報 (涵蓋大盤、強弱族群、持股與自選、新候選股、事件與新聞)。"""
    from app.taiwan.daily_brief_service import get_daily_brief_service

    svc = get_daily_brief_service()

    target_dt = None
    if target_date:
        try:
            target_dt = dt_date.fromisoformat(target_date)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"無效的日期格式: {target_date}") from e

    try:
        brief = svc.build_deterministic_brief(target_date=target_dt)
        return brief.model_dump()
    except Exception as e:
        logger.exception("Failed to build daily brief: %s", e)
        raise HTTPException(status_code=500, detail=f"每日簡報彙整失敗: {e}") from e


@router.post("/daily-brief/ai-summary")
async def generate_daily_brief_ai_summary(body: dict[str, Any]):
    """由使用者主動觸發，呼叫現有 AI Provider 生成嚴格 7 段式的每日市場摘要。"""
    from app.taiwan.daily_brief_models import DeterministicDailyBrief
    from app.taiwan.daily_brief_service import get_daily_brief_service

    try:
        brief = DeterministicDailyBrief(**body)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"結構化簡報資料格式不符: {e}") from e

    svc = get_daily_brief_service()
    try:
        summary = await svc.generate_ai_summary(brief)
        return summary.model_dump()
    except Exception as e:
        logger.warning("Daily brief AI summary generation failed: %s", e)
        raise HTTPException(status_code=503, detail=f"AI 摘要生成暫時不可用，請確認 AI 設定或網路: {e}") from e


class SaveDailyBriefPayload(BaseModel):
    brief_id: str | None = None
    brief_date: str | None = None
    generated_at: str | None = None
    data_as_of: str | None = None
    structured_brief: dict[str, Any]
    ai_summary: dict[str, Any] | None = None
    ai_status: str = "not_generated"
    ai_error: str | None = None


@router.post("/daily-brief/save")
def save_daily_brief_history(payload: SaveDailyBriefPayload):
    """保存使用者確認的每日摘要歷史至本機 user_data。"""
    from app.taiwan.daily_brief_models import DailyBriefAISummary, DeterministicDailyBrief
    from app.taiwan.daily_brief_service import get_daily_brief_service

    try:
        brief = DeterministicDailyBrief(**payload.structured_brief)
        ai_summary = DailyBriefAISummary(**payload.ai_summary) if payload.ai_summary else None
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"儲存資料格式錯誤: {e}") from e

    svc = get_daily_brief_service()
    try:
        saved = svc.save_brief(
            brief=brief,
            ai_summary=ai_summary,
            ai_status=payload.ai_status,
            ai_error=payload.ai_error,
        )
        return saved.model_dump()
    except Exception as e:
        logger.exception("Failed to persist daily brief: %s", e)
        raise HTTPException(status_code=500, detail=f"每日摘要儲存失敗: {e}") from e


@router.get("/daily-brief/history")
def list_daily_brief_history(limit: int | None = None):
    """取得歷史保存的每日摘要清單。"""
    from app.taiwan.daily_brief_service import get_daily_brief_service

    svc = get_daily_brief_service()
    try:
        briefs = svc.list_saved_briefs()
        if limit is not None:
            briefs = briefs[:limit]
        return [b.model_dump() for b in briefs]
    except Exception as e:
        logger.exception("Failed to list daily brief history: %s", e)
        raise HTTPException(status_code=500, detail=f"每日摘要歷史讀取失敗: {e}") from e


@router.get("/daily-brief/history/{brief_id}")
def get_daily_brief_history_detail(brief_id: str):
    """讀取特定歷史每日摘要明細。"""
    from app.taiwan.daily_brief_service import get_daily_brief_service

    svc = get_daily_brief_service()
    brief = svc.get_saved_brief(brief_id)
    if not brief:
        raise HTTPException(status_code=404, detail=f"找不到指定的每日摘要歷史: {brief_id}")
    return brief.model_dump()




# ── AI 說明回饋 (準／不準) ─────────────────────────────────────


class AIFeedbackIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    helpful: bool
    record_id: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=200)


@router.post("/ai-research/feedback")
def post_ai_feedback(req: AIFeedbackIn):
    """記錄使用者對 AI 白話說明的「準／不準」回饋；只做統計，不影響任何 AI 結果。"""
    from app.taiwan import ai_feedback

    try:
        return ai_feedback.record(
            symbol=req.symbol, helpful=req.helpful, record_id=req.record_id,
            model=req.model, note=req.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail="回饋儲存失敗") from exc


@router.get("/ai-research/feedback-summary")
def get_ai_feedback_summary():
    """各模型準／不準統計與最近回饋。"""
    from app.taiwan import ai_feedback

    return ai_feedback.summary()

"""行情狀態 / SSE 推送 API。

盤中選股相關端點已遷移至策略頁面，此處僅保留全域行情基礎設施。
SSE 推送四種事件 (使用標準 SSE event 字段):
  - quotes_updated: 行情資料刷新，前端 invalidate 對應 query
  - strategy_results_updated: 策略監控已寫入最新結果，前端刷新策略個股列表
  - strategy_alert: 策略監控/告警觸發，前端彈通知
  - depth_updated: 五檔盤口修正完成，前端刷新連板梯隊/看板封單資料
"""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from app.taiwan.realtime import get_market_status, get_realtime_service, taipei_now

router = APIRouter(prefix="/api/intraday", tags=["quotes"])



def _get_quote_service(request: Request):
    """獲取全域 QuoteService。"""
    return getattr(request.app.state, "quote_service", None)


@router.get("/status")
def status(request: Request):
    """行情狀態 (整合全域 QuoteService 與 TaiwanRealtimeService)。"""
    res: dict = {"enabled": False, "running": False, "symbol_count": 0, "index_symbol_count": 0,
                 "quote_age_ms": None, "is_trading_hours": False, "last_fetch_ms": None}
    qs = _get_quote_service(request)
    if qs:
        res = qs.status()

    # Enrich with Taiwan real-time layer state
    tw_svc = get_realtime_service()
    now_tpe = taipei_now()
    res["taiwan_market"] = {
        "status": get_market_status(now_tpe).value,
        "taipei_time": now_tpe.isoformat(),
        "cache_hits": tw_svc.cache_hits,
        "cache_misses": tw_svc.cache_misses,
        "provider_requests": tw_svc.provider_requests,
    }
    return res


@router.get("/quotes")
def get_quotes(
    symbols: str = Query(..., description="逗號分隔的台股或標準 symbol (例如 2330.TWSE,8069.TPEX,0050.TWSE)"),
    force: bool = Query(False, description="是否強制穿透快取向外部來源請求"),
):
    """批次獲取台股盤中即時行情 (支援 4 級 Fallback 與五檔盤口)。"""
    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        return {"quotes": [], "count": 0}

    tw_svc = get_realtime_service()
    quote_map = tw_svc.get_quotes(symbol_list, force_refresh=force)
    
    from app.taiwan.universe import get_security_master
    from app.taiwan.universe.models import MarketProfileBridge
    sec_master = get_security_master()

    rows = []
    for q in quote_map.values():
        d = q.to_dict()
        inst = sec_master.get_instrument(q.symbol)
        limit_up, limit_down = (None, None)
        limit_pct = None
        is_no_limit = False
        if inst:
            try:
                limit_pct = MarketProfileBridge.get_price_limit_pct(inst)
                if limit_pct is None:
                    is_no_limit = True
                elif q.prev_close is not None:
                    limit_up, limit_down = MarketProfileBridge.calc_limits(q.prev_close, inst)
            except ValueError:
                pass  # unconfirmed ETF metadata: limits unknown, not "no limit"
        d["limit_up"] = limit_up
        d["limit_down"] = limit_down
        d["price_limit_pct"] = limit_pct
        d["is_no_limit"] = is_no_limit
        # TAIWAN_LOCALIZATION_POLISH: 顯示名稱一律以 TaiwanSecurityMaster 的
        # 正體中文簡稱為準 (與 Screener/Watchlist 一致)，而不是 q.to_dict() 原樣
        # 帶出的 provider name —— MIS 通常已是中文, 但 Yahoo fallback
        # (yahoo_provider.py: name=meta.get("shortName")) 給的是英文公司全名,
        # 兩者混用會讓同一檔股票在不同頁面顯示不同名稱。inst 在本迴圈已為了
        # 漲跌停試算取得, 這裡直接重用, 不多一次查詢。優先序:
        # canonical Taiwan 中文簡稱 → provider name → symbol。
        d["name"] = inst.name if inst else (d.get("name") or q.symbol)
        rows.append(d)

    return {"quotes": rows, "count": len(rows)}


@router.get("/taiwan/search")
def search_taiwan_instruments(
    q: str = Query(..., description="代號、全名或代號簡稱，如 2330, 台積電, 0050"),
    limit: int = Query(20, ge=1, le=100),
):
    """查詢台灣證券主檔 (Security Master)。支援股票、ETF、權證搜尋與支援狀態判斷。"""
    from app.taiwan.universe import get_security_master
    from app.taiwan.universe.models import MarketProfileBridge

    sec_master = get_security_master()
    results = sec_master.search(q, limit=limit)
    
    # 附帶法規限制資訊 (方便前端即時判斷是否支援接近漲跌停規則)
    enriched = []
    for item in results:
        sym = item.get("symbol")
        inst = sec_master.get_instrument(sym) if sym else None
        limit_pct = None
        is_no_limit = False
        if inst and inst.is_supported:
            try:
                limit_pct = MarketProfileBridge.get_price_limit_pct(inst)
                is_no_limit = (limit_pct is None)
            except ValueError:
                # Unconfirmed ETF metadata: the limit is unknown, not "no limit"; keep it in results.
                pass
        item["price_limit_pct"] = limit_pct
        item["is_no_limit"] = is_no_limit
        enriched.append(item)

    return {"results": enriched, "count": len(enriched)}





@router.get("/indices")
def index_quotes():
    """Explicitly retire the old market index endpoint without reading its caches."""
    raise HTTPException(410, "舊市場指數端點已停用")


@router.get("/stream")
async def quote_stream(request: Request):
    """SSE 端點: 行情更新 + 告警推送 + 五檔修正 + 覆盤進度。

    使用 sse-starlette EventSourceResponse:
    - 標準 SSE event 字段，前端按 event name 監聽
    - 內置斷線檢測，客戶端斷開立即終止 generator
    - 內置 ping 心跳，保持連接活躍

    每個連接註冊一個獨立訂閱者 (QuoteSubscriber: 獨立事件 + 獨立佇列),
    事件由 QuoteService 廣播 — 多客戶端 (多標籤頁/設備) 各自收到全量事件。
    此前四通道共用服務級 Event + pop 取走語義, 告警只會被先醒的連接消費。
    """
    qs = _get_quote_service(request)

    async def event_generator():
        if qs is None:
            # 無行情服務: 保持連接 (EventSourceResponse 自帶 ping), 不推事件
            while True:
                await asyncio.sleep(30)

        sub = qs.subscribe()
        try:
            # 連接建立時立刻發送一條握手事件，確保 HTTP response headers 立即 flush 到客戶端觸發 onopen
            yield {
                "event": "connected",
                "data": json.dumps({"ts": int(time.time() * 1000), "status": "ok"}),
            }

            while True:
                # 等待任一通道有新信號 (5s 超時保持循環, 便於斷線時儘快退出)
                await asyncio.to_thread(sub.wait, 5.0)
                data = sub.pop()


                # 告警 (分片推送, 避免單條 SSE 過大)
                alerts = data["alerts"]
                for chunk_start in range(0, len(alerts), 20):
                    chunk = alerts[chunk_start:chunk_start + 20]
                    yield {
                        "event": "strategy_alert",
                        "data": json.dumps({
                            "ts": int(time.time() * 1000),
                            "alerts": chunk,
                        }, ensure_ascii=False),
                    }

                # 覆盤進度 (定時覆盤流式生成時) — 前端 reviewStore 直接消費
                # 事件已是 recap_market_stream 產出的 JSON 字串, 逐條轉發
                for evt_json in data["reviews"]:
                    yield {
                        "event": "review_progress",
                        "data": evt_json,
                    }

                # 行情更新
                if data["quote_updated"]:
                    yield {
                        "event": "quotes_updated",
                        "data": json.dumps({
                            "ts": int(time.time() * 1000),
                            "symbol_count": qs._symbol_count,
                        }),
                    }

                # 策略監控完成後, 結果已寫入記憶體快取; 獨立通知只刷新策略個股列表。
                if data["strategy_results_updated"]:
                    yield {
                        "event": "strategy_results_updated",
                        "data": json.dumps({"ts": int(time.time() * 1000)}),
                    }

                # 五檔修正完成 — 前端刷新連板梯隊封單資料
                if data["depth_updated"]:
                    yield {
                        "event": "depth_updated",
                        "data": json.dumps({
                            "ts": int(time.time() * 1000),
                        }),
                    }

                # 盤後資料寫入 (排程/啟動補跑) — 前端刷新依賴日線的查詢
                if data["daily_data_updated"]:
                    yield {
                        "event": "daily_data_updated",
                        "data": json.dumps({"ts": int(time.time() * 1000)}),
                    }
        finally:
            qs.unsubscribe(sub)

    return EventSourceResponse(event_generator())


@router.post("/refresh")
def refresh_quotes(request: Request):
    """手動刷新一次行情資料。"""
    qs = _get_quote_service(request)
    if qs:
        return qs.refresh()
    return {"error": "QuoteService not available"}

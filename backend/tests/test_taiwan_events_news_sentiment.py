# ruff: noqa: RUF001
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import polars as pl
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.taiwan.events_service import (
    MarketEvent,
    TaiwanEventService,
    parse_taiwan_date,
)
from app.taiwan.market_intelligence import (
    DataQualityReport,
    DatasetQualityMeta,
    IndexSnapshot,
    InstitutionalMarketAggregate,
    MarginMarketAggregate,
    MarketBreadthStats,
    MarketExchangeBreakdown,
    MarketIndexesSnapshot,
    MarketInstrumentBreakdown,
    TaiwanMarketIntelligenceSnapshot,
)
from app.taiwan.market_sentiment_service import (
    TaiwanMarketSentimentResponse,
    TaiwanMarketSentimentService,
)
from app.taiwan.news_service import (
    TaiwanNewsService,
    deduplicate_news,
    normalize_news_title,
    normalize_news_url,
)
from app.taiwan.screener import TaiwanScreenerRequest, TaiwanScreenerService


@pytest.fixture
def client():
    # Pass localhost client IP to satisfy auth middleware in testing
    return TestClient(app, client=("127.0.0.1", 50000))


# ---------------------------------------------------------------------------
# 1. Date parsing tests
# ---------------------------------------------------------------------------
def test_parse_taiwan_date():
    assert parse_taiwan_date("1150917").isoformat() == "2026-09-17"
    assert parse_taiwan_date("115/09/17").isoformat() == "2026-09-17"
    assert parse_taiwan_date("115.09.17").isoformat() == "2026-09-17"
    assert parse_taiwan_date("2026-09-17").isoformat() == "2026-09-17"
    assert parse_taiwan_date("2026/09/17").isoformat() == "2026-09-17"


# ---------------------------------------------------------------------------
# 2. Events Service tests
# ---------------------------------------------------------------------------
def test_events_service_severity_and_types():
    ev_disp = MarketEvent(
        id="disp_1",
        symbol="2330.TWSE",
        code="2330",
        name="台積電",
        exchange="TWSE",
        event_date="2026-09-26",
        event_type="disposition",
        event_type_label="處置證券",
        severity="attention",
        title="處置有價證券",
        summary="每五分鐘撮合一次",
        source="twse:punish",
        retrieved_at="2026-09-26T10:00:00",
    )
    assert ev_disp.severity == "attention"

    ev_susp = MarketEvent(
        id="susp_1",
        symbol="2330.TWSE",
        code="2330",
        name="台積電",
        exchange="TWSE",
        event_date="2026-09-26",
        event_type="suspended_trading",
        event_type_label="暫停交易",
        severity="risk",
        title="暫停交易公告",
        summary="有重大訊息待公布",
        source="twse:trading",
        retrieved_at="2026-09-26T10:00:00",
    )
    assert ev_susp.severity == "risk"

    ev_div = MarketEvent(
        id="div_1",
        symbol="2330.TWSE",
        code="2330",
        name="台積電",
        exchange="TWSE",
        event_date="2026-09-26",
        event_type="cash_dividend",
        event_type_label="除息",
        severity="info",
        title="現金股利除息",
        summary="每股配發4.0元",
        source="corporate_actions",
        retrieved_at="2026-09-26T10:00:00",
    )
    assert ev_div.severity == "info"


def test_events_service_filter_scopes():
    service = TaiwanEventService()
    today_str = datetime.now().strftime("%Y-%m-%d")

    events = [
        MarketEvent(
            id="e1",
            symbol="2330.TWSE",
            code="2330",
            name="台積電",
            exchange="TWSE",
            event_date=today_str,
            event_type="warning",
            event_type_label="注意股票",
            severity="attention",
            title="注意股票",
            summary="週轉率異常",
            source="twse:notice",
            retrieved_at="2026-09-26T10:00:00",
        ),
        MarketEvent(
            id="e2",
            symbol="2317.TWSE",
            code="2317",
            name="鴻海",
            exchange="TWSE",
            event_date="2020-01-01",
            event_type="cash_dividend",
            event_type_label="除息",
            severity="info",
            title="除息",
            summary="除息5元",
            source="corporate_actions",
            retrieved_at="2026-09-26T10:00:00",
        ),
    ]

    with patch.object(service, "get_all_regulatory_and_official_events", return_value=events):
        today_evs = service.get_events(scope="today")
        assert len(today_evs) == 1
        assert today_evs[0].code == "2330"

        # Portfolio scope (passing symbol filter)
        port_evs = service.get_events(scope="portfolio", symbols=["2317.TWSE"])
        assert len(port_evs) == 1
        assert port_evs[0].code == "2317"

        # Watchlist scope
        wl_evs = service.get_events(scope="watchlist", symbols=["2330"])
        assert len(wl_evs) == 1
        assert wl_evs[0].code == "2330"


def test_events_service_candidates_and_risk_check():
    service = TaiwanEventService()
    today_str = datetime.now().strftime("%Y-%m-%d")

    events = [
        MarketEvent(
            id="e1",
            symbol="2330.TWSE",
            code="2330",
            name="台積電",
            exchange="TWSE",
            event_date=today_str,
            event_type="cash_dividend",
            event_type_label="除息",
            severity="info",
            title="除權息",
            summary="每股配息4.0元",
            source="corporate_actions",
            retrieved_at="2026-09-26T10:00:00",
        ),
        MarketEvent(
            id="e2",
            symbol="3008.TWSE",
            code="3008",
            name="大立光",
            exchange="TWSE",
            event_date=today_str,
            event_type="disposition",
            event_type_label="處置證券",
            severity="attention",
            title="處置有價證券",
            summary="每五分鐘撮合一次",
            source="twse:punish",
            retrieved_at="2026-09-26T10:00:00",
            details={"period": f"{today_str}～{today_str}"},
        ),
        MarketEvent(
            id="e3",
            symbol="2454.TWSE",
            code="2454",
            name="聯發科",
            exchange="TWSE",
            event_date=today_str,
            event_type="suspended_trading",
            event_type_label="暫停交易",
            severity="risk",
            title="暫停交易公告",
            summary="重大訊息待公布",
            source="twse:trading",
            retrieved_at="2026-09-26T10:00:00",
        ),
    ]

    with patch.object(service, "get_all_regulatory_and_official_events", return_value=events):
        candidates = service.get_event_candidates()
        assert len(candidates) > 0

        # Risk check
        status_3008 = service.check_symbol_risk_status("3008")
        assert status_3008["is_disposition"] is True
        assert status_3008["is_suspended"] is False

        status_2454 = service.check_symbol_risk_status("2454")
        assert status_2454["is_disposition"] is False
        assert status_2454["is_suspended"] is True
        assert status_2454["has_risk_event"] is True

        status_2330 = service.check_symbol_risk_status("2330")
        assert status_2330["is_disposition"] is False
        assert status_2330["is_suspended"] is False
        assert status_2330["has_risk_event"] is False


def test_events_service_trigger_alerts(tmp_path):
    service = TaiwanEventService()
    today_str = datetime.now().strftime("%Y-%m-%d")

    events = [
        MarketEvent(
            id="e_disp_1",
            symbol="2330.TWSE",
            code="2330",
            name="台積電",
            exchange="TWSE",
            event_date=today_str,
            event_type="disposition",
            event_type_label="處置證券",
            severity="attention",
            title="處置有價證券",
            summary="撮合管制措施",
            source="twse:punish",
            retrieved_at="2026-09-26T10:00:00",
        )
    ]

    with patch.object(service, "get_all_regulatory_and_official_events", return_value=events):
        alerts = service.trigger_event_alerts(symbols=["2330.TWSE"], data_dir=tmp_path)
        assert len(alerts) == 1
        assert alerts[0]["symbol"] == "2330.TWSE"
        assert alerts[0]["rule_type"] == "event_disposition"


# ---------------------------------------------------------------------------
# 3. News Service tests
# ---------------------------------------------------------------------------
def test_news_deduplication_and_normalization():
    assert normalize_news_url("https://example.com/item?utm_source=fb&id=123") == "https://example.com/item?id=123"
    assert normalize_news_title("台積電法說會即將登場 - 經濟日報") == "台積電法說會即將登場"

    raw_items = [
        {
            "date": "2026-09-26 10:00:00",
            "title": "台積電法說會即將登場 - 經濟日報",
            "source": "經濟日報",
            "link": "https://example.com/news/1?utm_source=rss&ref=twstock",
            "description": "台積電法說會內容...",
        },
        {
            "date": "2026-09-26 10:05:00",
            "title": "台積電法說會即將登場 | 鉅亨網",
            "source": "鉅亨網",
            "link": "https://example.com/news/1?fbclid=xyz",
            "description": "台積電法說會內容重複...",
        },
        {
            "date": "2026-09-26 11:00:00",
            "title": "台積電先進製程滿載 展望樂觀",
            "source": "工商時報",
            "link": "https://example.com/news/2",
            "description": "台積電先進製程...",
        },
    ]

    deduped = deduplicate_news(raw_items, "2330.TWSE", "2330")
    assert len(deduped) == 2
    titles = [x.title for x in deduped]
    assert "台積電法說會即將登場 | 鉅亨網" in titles
    assert "台積電先進製程滿載 展望樂觀" in titles


def test_news_service_error_degradation():
    mock_adapter = MagicMock()
    mock_adapter.fetch_dataset.side_effect = Exception("403 Forbidden: Tier Limit Exceeded")
    mock_cache = MagicMock()
    mock_cache.get.return_value = None

    service = TaiwanNewsService(finmind_adapter=mock_adapter, finmind_cache=mock_cache)
    res = service.get_recent_news("2330", limit=5)
    assert res.status == "unavailable"
    assert res.items == []
    assert res.status_message is not None


# ---------------------------------------------------------------------------
# 4. Market Sentiment Service tests
# ---------------------------------------------------------------------------
def _build_test_snapshot(
    taiex_change: float = 150.0,
    taiex_change_pct: float = 0.006,
    advance_count: int = 700,
    decline_count: int = 200,
    foreign_net: float = 15000000.0,  # 15,000 lots
    total_net: float = 20000000.0,
    margin_change: float = 5000000.0,
) -> TaiwanMarketIntelligenceSnapshot:
    breadth = MarketBreadthStats(
        supported_count=1000,
        traded_count=950,
        advance_count=advance_count,
        decline_count=decline_count,
        flat_count=50,
        turnover=350000000000.0,
    )
    twse_breadth = MarketExchangeBreakdown(twse=breadth, tpex=breadth)
    inst_breakdown = MarketInstrumentBreakdown(stock=breadth, etf=breadth)

    indexes = MarketIndexesSnapshot(
        taiex=IndexSnapshot(
            symbol="IX0001.TWSE",
            name="加權指數",
            trade_date="2026-09-26",
            close=23000.0,
            change=taiex_change,
            change_pct=taiex_change_pct,
            status="available",
        )
    )

    institutional = InstitutionalMarketAggregate(
        trade_date="2026-09-26",
        row_count=1000,
        foreign_net=foreign_net,
        investment_trust_net=3000000.0,
        dealer_net=2000000.0,
        total_net=total_net,
        status="current",
    )

    margin = MarginMarketAggregate(
        trade_date="2026-09-26",
        margin_balance=300000000.0,
        margin_balance_change=margin_change,
        short_balance=30000000.0,
        short_balance_change=-500000.0,
        status="current",
    )

    dq_meta = DatasetQualityMeta(dataset="test", as_of="2026-09-26", status="current")
    dq = DataQualityReport(
        target_trade_date="2026-09-26",
        overall_status="complete",
        daily=dq_meta,
        institutional=dq_meta,
        margin=dq_meta,
        indexes=dq_meta,
    )

    return TaiwanMarketIntelligenceSnapshot(
        trade_date="2026-09-26",
        generated_at="2026-09-26T16:00:00",
        market_totals=breadth,
        by_exchange=twse_breadth,
        by_instrument=inst_breakdown,
        institutional=institutional,
        margin=margin,
        indexes=indexes,
        data_quality=dq,
    )


def test_market_sentiment_bullish():
    mock_intel_svc = MagicMock()
    mock_intel_svc.get_snapshot.return_value = _build_test_snapshot(
        taiex_change=250.0,
        taiex_change_pct=0.011,
        advance_count=750,
        decline_count=150,
        foreign_net=25000000.0,
        total_net=30000000.0,
    )

    mock_finmind = MagicMock()
    mock_finmind.fetch_dataset.side_effect = lambda dataset, **kwargs: (
        [
            {
                "date": "2026-09-25",
                "institutional_investors": "外資及陸資",
                "futures_id": "TX",
                "long_open_interest_balance_volume": 40000,
                "short_open_interest_balance_volume": 30000,
            },
            {
                "date": "2026-09-26",
                "institutional_investors": "外資及陸資",
                "futures_id": "TX",
                "long_open_interest_balance_volume": 48000,
                "short_open_interest_balance_volume": 28000,
            },
        ]
        if dataset == "TaiwanFuturesInstitutionalInvestors"
        else [
            {
                "date": "2026-09-26",
                "institutional_investors": "外資及陸資",
                "option_id": "TXO",
                "call_put": "買權Call",
                "long_open_interest_balance_volume": 30000,
                "short_open_interest_balance_volume": 20000,
            },
            {
                "date": "2026-09-26",
                "institutional_investors": "外資及陸資",
                "option_id": "TXO",
                "call_put": "賣權Put",
                "long_open_interest_balance_volume": 15000,
                "short_open_interest_balance_volume": 20000,
            },
        ]
    )

    mock_cache = MagicMock()
    mock_cache.get.return_value = None

    service = TaiwanMarketSentimentService(
        market_intel_svc=mock_intel_svc,
        finmind_adapter=mock_finmind,
        finmind_cache=mock_cache,
    )

    resp = service.get_market_sentiment(target_date=date(2026, 9, 26))
    assert isinstance(resp, TaiwanMarketSentimentResponse)
    assert resp.sentiment == "bullish"
    assert resp.bullish_count >= 2
    assert any("加權指數" in e.label for e in resp.evidence)


def test_market_sentiment_bearish():
    mock_intel_svc = MagicMock()
    mock_intel_svc.get_snapshot.return_value = _build_test_snapshot(
        taiex_change=-350.0,
        taiex_change_pct=-0.015,
        advance_count=120,
        decline_count=800,
        foreign_net=-30000000.0,
        total_net=-35000000.0,
    )

    mock_finmind = MagicMock()
    mock_finmind.fetch_dataset.side_effect = lambda dataset, **kwargs: (
        [
            {
                "date": "2026-09-25",
                "institutional_investors": "外資及陸資",
                "futures_id": "TX",
                "long_open_interest_balance_volume": 25000,
                "short_open_interest_balance_volume": 45000,
            },
            {
                "date": "2026-09-26",
                "institutional_investors": "外資及陸資",
                "futures_id": "TX",
                "long_open_interest_balance_volume": 20000,
                "short_open_interest_balance_volume": 55000,
            },
        ]
        if dataset == "TaiwanFuturesInstitutionalInvestors"
        else [
            {
                "date": "2026-09-26",
                "institutional_investors": "外資及陸資",
                "option_id": "TXO",
                "call_put": "買權Call",
                "long_open_interest_balance_volume": 10000,
                "short_open_interest_balance_volume": 20000,
            },
            {
                "date": "2026-09-26",
                "institutional_investors": "外資及陸資",
                "option_id": "TXO",
                "call_put": "賣權Put",
                "long_open_interest_balance_volume": 35000,
                "short_open_interest_balance_volume": 15000,
            },
        ]
    )

    mock_cache = MagicMock()
    mock_cache.get.return_value = None

    service = TaiwanMarketSentimentService(
        market_intel_svc=mock_intel_svc,
        finmind_adapter=mock_finmind,
        finmind_cache=mock_cache,
    )

    resp = service.get_market_sentiment(target_date=date(2026, 9, 26))
    assert resp.sentiment == "bearish"
    assert resp.bearish_count >= 2


def test_market_sentiment_mixed():
    mock_intel_svc = MagicMock()
    mock_intel_svc.get_snapshot.return_value = _build_test_snapshot(
        taiex_change=120.0,
        taiex_change_pct=0.005,
        advance_count=650,
        decline_count=250,
        foreign_net=-15000000.0,
        total_net=-10000000.0,
    )

    mock_finmind = MagicMock()
    mock_finmind.fetch_dataset.side_effect = lambda dataset, **kwargs: (
        [
            {
                "date": "2026-09-26",
                "institutional_investors": "外資及陸資",
                "futures_id": "TX",
                "long_open_interest_balance_volume": 15000,
                "short_open_interest_balance_volume": 50000,
            },
        ]
        if dataset == "TaiwanFuturesInstitutionalInvestors"
        else []
    )

    mock_cache = MagicMock()
    mock_cache.get.return_value = None

    service = TaiwanMarketSentimentService(
        market_intel_svc=mock_intel_svc,
        finmind_adapter=mock_finmind,
        finmind_cache=mock_cache,
    )

    resp = service.get_market_sentiment(target_date=date(2026, 9, 26))
    assert resp.sentiment == "mixed"


def test_market_sentiment_derivatives_degradation():
    mock_intel_svc = MagicMock()
    mock_intel_svc.get_snapshot.return_value = _build_test_snapshot(
        taiex_change=100.0,
        taiex_change_pct=0.004,
        advance_count=600,
        decline_count=300,
        foreign_net=10000000.0,
        total_net=12000000.0,
    )

    mock_finmind = MagicMock()
    mock_finmind.fetch_dataset.side_effect = Exception("403 Forbidden")

    mock_cache = MagicMock()
    mock_cache.get.return_value = None

    service = TaiwanMarketSentimentService(
        market_intel_svc=mock_intel_svc,
        finmind_adapter=mock_finmind,
        finmind_cache=mock_cache,
    )

    resp = service.get_market_sentiment(target_date=date(2026, 9, 26))
    assert resp.derivatives_status == "unavailable"
    assert resp.sentiment == "bullish"


# ---------------------------------------------------------------------------
# 5. Screener filter integration tests
# ---------------------------------------------------------------------------
def test_screener_risk_event_filters():
    df = pl.DataFrame({
        "symbol": ["2330", "3008", "2454"],
        "name": ["台積電", "大立光", "聯發科"],
        "close": [1000.0, 2500.0, 1200.0],
        "change_pct": [1.5, -0.5, 0.2],
        "volume": [30000, 1500, 8000],
        "turnover_rate": [0.3, 0.2, 0.4],
        "sector": ["半導體", "光電", "半導體"],
        "pe": [22.0, 18.0, 25.0],
        "pb": [4.5, 3.2, 5.0],
        "dividend_yield": [2.5, 3.0, 4.0],
        "revenue_growth": [20.0, -5.0, 15.0],
        "operating_margin": [42.0, 30.0, 35.0],
        "roe": [25.0, 15.0, 20.0],
        "debt_ratio": [30.0, 25.0, 35.0],
        "foreign_net_buy": [1000, -200, 500],
        "trust_net_buy": [200, -50, 100],
        "dealer_net_buy": [50, -10, 20],
        "margin_change": [10, -5, 20],
        "short_change": [0, 5, 0],
    })

    service = TaiwanScreenerService()

    mock_events = MagicMock()
    mock_events.check_symbol_risk_status.side_effect = lambda sym: {
        "2330": {"is_disposition": False, "is_suspended": False, "has_risk_event": False},
        "3008": {"is_disposition": True, "is_suspended": False, "has_risk_event": True},
        "2454": {"is_disposition": False, "is_suspended": False, "has_risk_event": False},
    }[sym]

    with patch("app.taiwan.events_service.get_event_service", return_value=mock_events):
        # Test exclude_disposition
        req = TaiwanScreenerRequest(exclude_disposition=True)
        filtered = service._apply_filters(df, req)
        symbols = filtered["symbol"].to_list()
        assert "3008" not in symbols
        assert "2330" in symbols
        assert "2454" in symbols

        # Test exclude_risk_events
        req_risk = TaiwanScreenerRequest(exclude_risk_events=True)
        filtered_risk = service._apply_filters(df, req_risk)
        symbols_risk = filtered_risk["symbol"].to_list()
        assert "3008" not in symbols_risk


# ---------------------------------------------------------------------------
# 6. API Endpoints tests
# ---------------------------------------------------------------------------
def test_api_events_endpoints(client):
    # 1. GET /api/taiwan/events
    resp = client.get("/api/taiwan/events?scope=today")
    assert resp.status_code == 200
    data = resp.json()
    assert "events" in data
    assert "total" in data
    assert "as_of_date" in data

    # 2. GET /api/taiwan/events/candidates
    resp_cand = client.get("/api/taiwan/events/candidates")
    assert resp_cand.status_code == 200
    cand_data = resp_cand.json()
    assert "candidates" in cand_data

    # 3. POST /api/taiwan/alerts/check-events
    resp_alerts = client.post("/api/taiwan/alerts/check-events")
    assert resp_alerts.status_code == 200
    alert_data = resp_alerts.json()
    assert "triggered_alerts" in alert_data

    # 4. GET /api/taiwan/news/2330
    resp_news = client.get("/api/taiwan/news/2330?limit=3")
    assert resp_news.status_code == 200
    news_data = resp_news.json()
    assert "items" in news_data
    assert "symbol" in news_data
    assert "2330" in news_data["symbol"]

    # 5. GET /api/taiwan/market-sentiment
    resp_sent = client.get("/api/taiwan/market-sentiment")
    assert resp_sent.status_code == 200
    sent_data = resp_sent.json()
    assert "sentiment" in sent_data
    assert "evidence" in sent_data

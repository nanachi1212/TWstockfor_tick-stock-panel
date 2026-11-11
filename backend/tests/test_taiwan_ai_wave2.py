"""Regression tests for the wave-2 review items in the AI / events / research modules."""

from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import polars as pl
import pytest

from app.taiwan import events_service as es
from app.taiwan.events_service import MarketEvent, TaiwanEventService, disposition_period_active
from app.taiwan.providers.taiwan_values import TAIPEI

# ── events: push channels, period parsing, ids, single flight ────────────────


def _disposition(period: str = "") -> MarketEvent:
    today = es.taipei_now().date().isoformat()
    return MarketEvent(
        id="e_disp_wave2", symbol="2330.TWSE", code="2330", name="台積電", exchange="TWSE",
        event_date=today, event_type="disposition", event_type_label="處置證券", severity="attention",
        title="處置有價證券", summary="撮合管制", source="twse:punish", retrieved_at="2026-09-26T10:00:00",
        details={"period": period},
    )


def test_event_alert_push_uses_global_channels_and_reports_only_real_delivery(tmp_path, monkeypatch):
    from app.services import preferences, webhook_adapter

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(preferences, "load", lambda: {})
    monkeypatch.setattr(preferences, "get_external_notification_channels", lambda: ["telegram"])
    monkeypatch.setattr(preferences, "get_telegram_bot_token", lambda: "tg-token")
    monkeypatch.setattr(preferences, "get_telegram_chat_id", lambda: "42")
    monkeypatch.setattr(webhook_adapter, "send_telegram", lambda token, chat, title, body: sent.append((token, chat)) or True)
    monkeypatch.setattr(webhook_adapter, "send_line", lambda *a: pytest.fail("LINE is not an enabled channel"))

    service = TaiwanEventService()
    with patch.object(service, "get_all_regulatory_and_official_events", return_value=[_disposition()]):
        alerts = service.trigger_event_alerts(["2330.TWSE"], data_dir=tmp_path)
    assert len(alerts) == 1
    assert sent == [("tg-token", "42")]


def test_disposition_period_parsing_is_shared_and_tri_state():
    assert disposition_period_active("2026-10-01~2026-10-14", "2026-10-08") is True
    assert disposition_period_active("115/10/01～115/10/14", "2026-10-08") is True
    assert disposition_period_active("115/10/01～115/10/07", "2026-10-08") is False
    assert disposition_period_active("", "2026-10-08") is None
    assert disposition_period_active("not a period", "2026-10-08") is None


def test_unparseable_source_date_keeps_event_id_stable_across_days(monkeypatch):
    row = {"Code": "2330", "Date": "???", "DispositionPeriod": "115/10/01～115/10/14", "DispositionMeasures": "撮合"}
    monkeypatch.setattr(es, "fetch_json", lambda *a, **k: [row])
    service = TaiwanEventService()
    ids = []
    for day in (date(2026, 10, 8), date(2026, 10, 9)):
        monkeypatch.setattr(es, "taipei_now", lambda day=day: datetime(day.year, day.month, day.day, 9, 0, tzinfo=TAIPEI))
        ids.append(service.fetch_twse_punish_events()[0].id)
    assert ids[0] == ids[1]


def test_unknown_corporate_action_type_is_not_shown_as_ex_dividend(monkeypatch):
    action = SimpleNamespace(
        symbol="2330.TWSE", exchange="TWSE", effective_date=date(2026, 10, 8), event_type="mystery",
        cash_dividend=None, reference_price=100.0, previous_close=99.0, free_share_ratio=None, factor=1.0,
        status="confirmed", source="twse", source_url=None, retrieved_at=None,
    )
    monkeypatch.setattr(es, "CorporateActionStore", lambda: SimpleNamespace(read=lambda: [action]))
    event = TaiwanEventService().get_corporate_action_events()[0]
    assert event.event_type == "mystery"
    assert "除息" not in event.title


def test_cold_official_event_fetch_is_single_flight(tmp_path, monkeypatch):
    monkeypatch.setattr(es, "_cache_path", lambda: tmp_path / "regulatory_events.json")
    service = TaiwanEventService()
    calls = []

    def slow_fetch():
        calls.append(threading.get_ident())
        time.sleep(0.2)
        return []

    service.fetch_twse_punish_events = slow_fetch  # type: ignore[method-assign]
    for name in ("fetch_tpex_disposal_events", "fetch_twse_warning_events", "fetch_tpex_warning_events",
                 "fetch_twse_delisting_events", "fetch_tpex_cmode_events", "get_corporate_action_events"):
        setattr(service, name, list)
    threads = [threading.Thread(target=service.get_all_regulatory_and_official_events) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1


# ── news: a failed refresh must not wipe valid cache; rate limit is short-lived ──


def test_news_rate_limit_is_cached_with_short_ttl_status_and_keeps_valid_cache():
    from app.taiwan.news_service import TaiwanNewsService
    from app.taiwan.providers.finmind_provider import FinMindRateLimitError

    adapter = MagicMock()
    adapter.fetch_dataset.side_effect = FinMindRateLimitError("slow down")
    cache = MagicMock()
    cache.get.return_value = None
    resp = TaiwanNewsService(finmind_adapter=adapter, finmind_cache=cache).get_recent_news("2330", force_refresh=True)
    assert resp.status == "rate_limited"
    assert cache.set.call_args.kwargs["status"] == "unavailable"  # NEGATIVE_TTL applies, not 6 hours

    cache = MagicMock()
    cache.get.return_value = {"status": "available", "data": [{"title": "x"}]}
    TaiwanNewsService(finmind_adapter=adapter, finmind_cache=cache).get_recent_news("2330", force_refresh=True)
    cache.set.assert_not_called()


# ── research context: never present older rows as the target day ──────────────


def _stock_master():
    inst = MagicMock()
    inst.symbol, inst.code, inst.name, inst.exchange = "TEST.TWSE", "TEST", "測試股", "TWSE"
    inst.instrument_type, inst.industry, inst.currency = "stock", "半導體業", "TWD"
    inst.listing_status, inst.listing_date, inst.updated_at = "active", "2020-01-01", "2026-08-28"
    master = MagicMock()
    master.get_instrument.return_value = inst
    master.to_dataframe.return_value = pl.DataFrame({
        "symbol": ["TEST.TWSE"], "name": ["測試股"], "exchange": ["TWSE"], "instrument_type": ["stock"],
        "industry": ["半導體業"], "listing_status": ["active"],
    })
    return master


def test_missing_target_day_does_not_publish_stale_technicals():
    from app.taiwan.research_context import TaiwanStockResearchContextService

    dates = [date(2026, 8, d) for d in (3, 4, 5, 6, 7, 10, 11, 12, 13, 14, 17, 18, 19, 20, 21, 24, 25)]
    daily = MagicMock()
    daily.available_dates.return_value = dates
    daily.read_range.return_value = pl.DataFrame({
        "symbol": ["TEST.TWSE"] * len(dates), "date": dates,
        "open": [100.0] * len(dates), "high": [101.0] * len(dates), "low": [99.0] * len(dates),
        "close": [100.0 + i for i in range(len(dates))], "volume": [1000.0] * len(dates),
        "amount": [1e6] * len(dates),
    })
    ctx = TaiwanStockResearchContextService(daily_store=daily, security_master=_stock_master()).get_research_context(
        "TEST.TWSE", date(2026, 8, 28)
    )
    assert ctx.price_context.close is None
    assert ctx.technical_context.ma5 is None and ctx.technical_context.rsi14 is None
    assert ctx.price_context.high_20d is None
    # The reference close for the (missing) target day is the last stored close, not the one before it.
    assert ctx.price_context.previous_close == 116.0


def test_unknown_etf_limit_class_is_not_reported_as_no_limit():
    from app.taiwan.research_context import TaiwanStockResearchContextService

    master = _stock_master()
    etf = master.get_instrument.return_value
    etf.instrument_type, etf.etf_category, etf.underlying_scope, etf.leverage_multiplier = "etf", None, None, None
    daily = MagicMock()
    daily.available_dates.return_value = []
    with patch("app.taiwan.research_context.MarketProfileBridge.get_price_limit_pct", side_effect=ValueError("unknown")):
        ctx = TaiwanStockResearchContextService(daily_store=daily, security_master=master).get_research_context(
            "TEST.TWSE", date(2026, 8, 28)
        )
    assert ctx.market_rules_context.is_no_limit is False
    assert ctx.market_rules_context.price_limit_pct is None
    assert ctx.market_rules_context.meta.classification == "MISSING"


# ── AI evidence registry / AI output shape tolerance ───────────────────────────


def test_evidence_registry_lists_exactly_the_fields_that_were_sent():
    from app.taiwan.ai_research import build_evidence_registry
    from app.taiwan.research_context import TaiwanStockResearchContextService

    master = _stock_master()
    daily = MagicMock()
    daily.available_dates.return_value = []
    ctx = TaiwanStockResearchContextService(daily_store=daily, security_master=master).get_research_context(
        "TEST.TWSE", date(2026, 8, 28)
    )
    ctx.fundamentals_context.status = "available"
    ctx.fundamentals_context.latest_monthly_revenue = 1000.0
    ctx.fundamentals_context.gross_profit = 400.0
    payload, keys, _ = build_evidence_registry(ctx, None)
    assert "fundamentals_context.latest_monthly_revenue" in keys
    assert "fundamentals_context.gross_profit" in keys
    sent = {f"{s}.{k}" for s, v in payload.items() if isinstance(v, dict) for k in v if k != "units"}
    checked = {"fundamentals_context", "price_context", "technical_context"}
    assert {k for k in keys if k.split(".")[0] in checked} == {k for k in sent if k.split(".")[0] in checked}


def test_comparison_ai_item_validation_survives_odd_json_shapes():
    from app.taiwan.comparison_ai_research import _text_or_none, _validated_items

    registry = {"a.b"}
    assert _validated_items("oops", registry) == []
    assert _validated_items([{"text": "t", "evidence_refs": "a.b"}, {"text": "t", "evidence_refs": [["a.b"], "a.b"]}], registry)[0].evidence_refs == ["a.b"]
    assert _validated_items([{"text": 5, "evidence_refs": ["a.b"]}], registry) == []
    assert _text_or_none({"x": 1}) is None and _text_or_none("  hi ") == "hi"


@pytest.mark.asyncio
async def test_screener_translation_survives_odd_json_shapes():
    from app.taiwan.screener_nl import TaiwanScreenerTranslator

    raw = '{"request_fields": ["x"], "recognized_conditions": "a", "unsupported_conditions": null, "clarification_needed": "yes", "clarification_message": 3}'
    with patch("app.taiwan.screener_nl.generate_ai_text", new_callable=AsyncMock) as ai:
        ai.return_value = raw
        res = await TaiwanScreenerTranslator().translate("隨便")
    assert res.recognized_conditions == []
    assert res.clarification_needed is False


# ── daily brief ─────────────────────────────────────────────────────────────────


def _empty_brief():
    from app.taiwan.daily_brief_models import (
        CandidateFactBrief,
        DeterministicDailyBrief,
        EventFactBrief,
        MarketFactBrief,
        NewsFactBrief,
        PortfolioFactBrief,
        WatchlistFactBrief,
    )

    return DeterministicDailyBrief(
        schema_version=2, brief_date="2026-10-08", generated_at="2026-10-08T00:00:00+00:00",
        market=MarketFactBrief(trade_date="2026-10-08"), portfolio=PortfolioFactBrief(),
        watchlist=WatchlistFactBrief(), candidates=CandidateFactBrief(), events=EventFactBrief(), news=NewsFactBrief(),
    )


@pytest.mark.asyncio
async def test_daily_brief_ai_with_only_blank_sections_is_a_failure(tmp_path):
    from app.taiwan.daily_brief_service import TaiwanDailyBriefService

    service = TaiwanDailyBriefService(path=tmp_path / "briefs.json", daily_store=MagicMock())
    with patch("app.taiwan.daily_brief_service.generate_ai_text", new_callable=AsyncMock) as ai:
        ai.return_value = '{"section_a_market": "  ", "section_b_key_changes": []}'
        with pytest.raises(ValueError):
            await service.generate_ai_summary(_empty_brief())


def test_daily_brief_id_uses_taipei_date(tmp_path, monkeypatch):
    from app.taiwan import daily_brief_service as dbs

    monkeypatch.setattr(dbs, "taipei_now", lambda: datetime(2026, 10, 8, 7, 0, 0, tzinfo=TAIPEI))
    service = dbs.TaiwanDailyBriefService(path=tmp_path / "briefs.json", daily_store=MagicMock())
    saved = service.save_brief(_empty_brief())
    assert saved.brief_id.startswith("brief_20261008_070000_")


# ── detail section quality ─────────────────────────────────────────────────────


def test_detail_daily_rows_drop_bars_with_missing_prices():
    from app.taiwan.detail_service import TaiwanStockDetailService

    history = pl.DataFrame({
        "date": ["2026-10-07", "2026-10-08"], "open": [100.0, None], "high": [101.0, 102.0],
        "low": [99.0, 98.0], "close": [100.5, None], "volume": [10, 10], "amount": [1.0, 1.0],
    })
    daily = TaiwanStockDetailService._aggregate_daily(MagicMock(), history, "taiwan_daily_store", 10)
    assert [r.date for r in daily.rows] == ["2026-10-07"]


# ── fundamentals / ownership: insufficient history is "unavailable", not flat/normal ──


def test_shareholding_and_lending_report_insufficient_history():
    from app.taiwan.fundamental_chips_service import TaiwanFundamentalChipsService

    svc = TaiwanFundamentalChipsService.__new__(TaiwanFundamentalChipsService)
    shareholding = svc._process_shareholding(
        [{"date": f"2026-10-0{i}", "ForeignInvestmentSharesRatio": 70.0 + i} for i in range(1, 4)],
        None, "now",
    )
    assert shareholding.change_5d is None and shareholding.change_20d is None
    assert shareholding.trend == "unavailable"

    lending = svc._process_securities_lending(
        [{"date": f"2026-10-0{i}", "volume": 1000, "fee_rate": 0.5} for i in range(1, 4)], None, "now",
    )
    assert lending.anomaly_status == "unavailable"


def test_monthly_revenue_uses_calendar_month_yoy():
    from app.taiwan.fundamental_chips_service import TaiwanFundamentalChipsService

    rows = [
        {"date": "2025-09-10", "revenue_year": 2025, "revenue_month": 9, "revenue": 100.0},
        {"date": "2026-09-10", "revenue_year": 2026, "revenue_month": 9, "revenue": 150.0},
    ]  # months in between are missing: index -13 would be wrong, the calendar month is not
    data = TaiwanFundamentalChipsService._process_month_revenue(rows, None, "now")
    assert data.yoy == 50.0


# ── securities lending: 5 / 20 day windows are trading-day windows ─────────────


def _weekdays(end: date, count: int) -> list[date]:
    days: list[date] = []
    cur = end
    while len(days) < count:
        if cur.weekday() < 5:
            days.append(cur)
        cur -= timedelta(days=1)
    return days[::-1]


def _lend(day: date, volume: int) -> dict:
    return {"date": day.isoformat(), "volume": volume, "fee_rate": 0.5}


def test_lending_windows_count_trading_days_not_lending_days():
    from app.taiwan.fundamental_chips_service import TaiwanFundamentalChipsService as Svc

    sessions = _weekdays(date(2026, 10, 2), 25)
    # One row before the 20-session window (proves coverage), ten lending days inside it,
    # none in the newest ten sessions.
    rows = [_lend(sessions[0], 500), *(_lend(d, 1000) for d in sessions[5:15])]

    quiet = Svc._process_securities_lending(rows, None, "", trading_dates=sessions)
    assert (quiet.volume_5d, quiet.volume_20d) == (0, 10_000)  # lending-day windows gave 5,000 / 10,500
    assert quiet.anomaly_status == "drop"

    active = Svc._process_securities_lending([*rows, _lend(sessions[-2], 4000)], None, "", trading_dates=sessions)
    assert (active.volume_5d, active.volume_20d) == (4000, 14_000)
    assert active.anomaly_status == "normal"  # 800 / session over 5 against 700 / session over 20

    # Rows that start inside the 20-session window cannot prove the older sessions were quiet.
    short = Svc._process_securities_lending([_lend(d, 1000) for d in sessions[-8:]], None, "", trading_dates=sessions)
    assert (short.volume_5d, short.volume_20d, short.anomaly_status) == (5000, None, "unavailable")

    # Sessions newer than the fetch are not covered by the rows: they are not counted as zero.
    stale = Svc._process_securities_lending(
        [_lend(d, 1000) for d in sessions[:-3]], None, f"{sessions[-3].isoformat()}T10:00:00+00:00",
        trading_dates=sessions,
    )
    assert (stale.volume_5d, stale.volume_20d) == (5000, 20_000)

    # No trading-day source: the windows cannot be placed at all.
    unknown = Svc._process_securities_lending([_lend(d, 1000) for d in sessions], None, "", trading_dates=[])
    assert (unknown.volume_5d, unknown.volume_20d, unknown.anomaly_status) == (None, None, "unavailable")
    assert unknown.latest_volume == 1000


def _research_service(sessions: list[date], cached: dict[str, dict]):
    from app.taiwan.research_context import TaiwanStockResearchContextService

    daily = MagicMock()
    daily.available_dates.return_value = sessions
    daily.read_range.return_value = pl.DataFrame({
        "symbol": ["TEST.TWSE"] * len(sessions), "date": sessions,
        "open": [100.0] * len(sessions), "high": [101.0] * len(sessions), "low": [99.0] * len(sessions),
        "close": [100.0] * len(sessions), "volume": [1000.0] * len(sessions), "amount": [1e6] * len(sessions),
    })
    cache = MagicMock()
    cache.get.side_effect = lambda dataset, symbol: cached.get(dataset)
    return TaiwanStockResearchContextService(daily_store=daily, security_master=_stock_master(), finmind_cache=cache)


def test_research_context_lending_windows_use_trading_days():
    sessions = _weekdays(date(2026, 8, 28), 25)
    rows = [_lend(sessions[0], 500), *(_lend(d, 1000) for d in sessions[5:15])]
    service = _research_service(sessions, {"TaiwanStockSecuritiesLending": {"data": rows, "status": "available"}})
    own = service.get_research_context("TEST.TWSE", date(2026, 8, 28)).ownership_context
    assert own.securities_lending_latest_volume == 1000
    assert (own.securities_lending_volume_5d, own.securities_lending_volume_20d) == (0, 10_000)
    assert own.securities_lending_anomaly == "drop"


# ── research context: valuation comes from the locally cached official observation ──


def test_research_context_fills_valuation_point_in_time():
    from app.taiwan.ai_research import build_evidence_registry

    sessions = _weekdays(date(2026, 8, 28), 25)
    valuation = {"data": {"pe": 18.5, "pb": None, "dividend_yield": 2.1}, "data_date": "2026-08-27",
                 "status": "available"}
    service = _research_service(sessions, {"TaiwanValuation": valuation})

    ctx = service.get_research_context("TEST.TWSE", date(2026, 8, 28))
    fund = ctx.fundamentals_context
    assert (fund.pe, fund.pb, fund.dividend_yield, fund.valuation_as_of) == (18.5, None, 2.1, "2026-08-27")
    assert fund.status == "available"
    payload, keys, _ = build_evidence_registry(ctx, None)
    assert payload["fundamentals_context"]["pe"] == 18.5
    assert payload["fundamentals_context"]["valuation_as_of"] == "2026-08-27"
    assert "fundamentals_context.pe" in keys

    # An observation newer than the report date is future data for that report.
    past = service.get_research_context("TEST.TWSE", date(2026, 8, 26)).fundamentals_context
    assert (past.pe, past.dividend_yield, past.valuation_as_of) == (None, None, None)
    assert past.status == "unavailable"

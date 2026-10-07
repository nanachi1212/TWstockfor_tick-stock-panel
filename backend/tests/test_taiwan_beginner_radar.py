"""Daily Entry Radar v1: live status rules, quote gate reuse and source merge."""
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from test_taiwan_beginner_selection import AS_OF, FAVORABLE, _facts

from app.main import app
from app.taiwan import beginner_radar as radar
from app.taiwan.beginner_radar import (
    build_radar,
    live_plan_status,
    parse_holdings,
    radar_live,
    taiwan_symbols,
)
from app.taiwan.beginner_selection import BeginnerSelectionResponse, PlanLevels, evaluate_candidate
from app.taiwan.enrichment.models import SourceMeta
from app.taiwan.realtime.calendar import TAIPEI_TZ, MarketStatus
from app.taiwan.realtime.models import RealtimeStatus, TaiwanRealtimeQuote
from app.taiwan.symbol import Exchange


def _plan(**overrides) -> PlanLevels:
    base = {
        "rule_version": "trade_plan_v1", "entry_semantics": "pullback_limit",
        "entry_zone_low": 94.0, "entry_zone_high": 97.0, "reference_high": 100.0,
        "breakout_trigger": None, "stop_price": 90.0,
        "evidence_as_of": AS_OF, "plan_identity": "test",
    }
    base.update(overrides)
    return PlanLevels(**base)


def _quote(price: float | None = 96.0, *, symbol: str = "2330.TWSE", market_status: str = MarketStatus.OPEN.value,
           is_stale: bool = False, status: str = RealtimeStatus.REALTIME.value,
           source_type: str = "first_party_web_endpoint",
           freshness_class: str = "best_effort_near_realtime") -> TaiwanRealtimeQuote:
    now = datetime.now(TAIPEI_TZ)
    meta = SourceMeta(
        source="twse:mis", source_url="", fetched_at=now.isoformat(), trade_date=now.date(),
        status=status, is_realtime=not is_stale, is_stale=is_stale, source_type=source_type,
        freshness_class=freshness_class, is_best_effort=True,
    )
    return TaiwanRealtimeQuote(
        symbol=symbol, name="台積電", exchange=Exchange.TWSE, last_price=price, prev_close=95.0,
        open=95.0, high=price, low=95.0, change=None, change_pct=None, volume=1000, amount=1.0,
        quote_time=now, trade_date=now.date(), market_status=market_status, source_meta=meta,
    )


# ── live_plan_status: pullback zone boundaries ──

@pytest.mark.parametrize(("price", "expected"), [
    (97.0, "in_zone"),          # zone high is inside
    (94.0, "in_zone"),          # zone low is inside
    (98.4, "near_zone"),        # 97 * 1.015 = 98.455
    (98.5, "waiting"),
    (92.0, "below_zone"),       # under the zone, stop not broken
    (90.0, "below_zone"),       # touching the stop is not a break
    (89.9, "below_stop"),
])
def test_pullback_zone_boundaries(price, expected):
    assert live_plan_status(_plan(), price) == expected


@pytest.mark.parametrize(("price", "expected"), [
    (100.0, "breakout"),        # trigger itself counts as breakout
    (98.5, "near_breakout"),    # 100 * 0.985
    (98.4, "waiting"),
    (89.0, "below_stop"),
])
def test_breakout_trigger_boundaries(price, expected):
    plan = _plan(entry_semantics="breakout_stop", entry_zone_low=None, entry_zone_high=None, breakout_trigger=100.0)
    assert live_plan_status(plan, price) == expected


def test_incomplete_plan_levels_are_unavailable_not_guessed():
    assert live_plan_status(_plan(entry_zone_low=None), 95.0) == "unavailable"
    assert live_plan_status(_plan(entry_semantics="breakout_stop", breakout_trigger=None), 95.0) == "unavailable"


# ── radar_live: same quote gate as the monitor engine ──

def _pick():
    candidate, _ = evaluate_candidate(_facts(close=99.0), FAVORABLE)
    assert candidate.selection_state == "wait_pullback" and candidate.trade_plan is not None
    return candidate


@pytest.mark.parametrize("quote", [
    None,
    _quote(market_status=MarketStatus.CLOSED.value),
    _quote(market_status=MarketStatus.SCHEDULED_OPEN_UNVERIFIED.value),
    _quote(is_stale=True),
    _quote(status=RealtimeStatus.DAILY_FALLBACK.value, source_type="local_store"),
    _quote(freshness_class="delayed_15m"),
    _quote(price=None),
    _quote(price=float("nan")),
])
def test_refused_quotes_never_get_a_live_status(quote):
    live = radar_live(_pick(), quote)
    assert live.status == "unavailable" and live.note


def test_fresh_quote_inside_plan_zone():
    candidate = _pick()
    plan = candidate.trade_plan
    live = radar_live(candidate, _quote(price=plan.entry_zone_high))
    assert live.status == "in_zone" and live.label == "已進承接區"
    assert live.price == plan.entry_zone_high and live.quote_time
    assert (live.source, live.source_status, live.freshness_class) == (
        "twse:mis", RealtimeStatus.REALTIME.value, "best_effort_near_realtime")


def test_refused_quote_keeps_its_provenance_and_missing_quote_has_none():
    live = radar_live(_pick(), _quote(freshness_class="delayed_15m"))
    assert live.status == "unavailable" and live.freshness_class == "delayed_15m" and live.source == "twse:mis"
    assert radar_live(_pick(), None).source is None


def test_candidate_without_plan_keeps_price_but_no_status():
    candidate, _ = evaluate_candidate(_facts(close=110.0, adjusted_close=110.0, ma20=95.0), FAVORABLE)
    assert candidate.selection_state == "no_chase" and candidate.trade_plan is None
    live = radar_live(candidate, _quote(price=109.0))
    assert live.status == "unavailable" and live.price == 109.0


# ── symbol parsing ──

def test_parse_holdings_dedupes_and_rejects_invalid():
    assert parse_holdings([" 2330.twse", "2330.TWSE", "", "8069.TPEX "]) == ["2330.TWSE", "8069.TPEX"]
    assert parse_holdings([]) == []
    with pytest.raises(ValueError):
        parse_holdings(["2330.TWSE", "600519.SH"])


def test_watchlist_keeps_taiwan_symbols_only():
    assert taiwan_symbols(["600519.SH", "2330.twse", None, "2330.TWSE", "00631L.TWSE"]) == ["2330.TWSE", "00631L.TWSE"]


# ── build_radar: picks first, own stocks evaluated without rank ──

class _FakeService:
    def __init__(self, picks):
        self.picks = picks
        self.evaluated: list[list[str]] = []

    def _response(self, candidates):
        return BeginnerSelectionResponse(
            status="ready", as_of=AS_OF, generated_at=datetime.now(UTC).isoformat(),
            market=FAVORABLE, candidates=candidates, universe_count=2, eligible_count=2,
        )

    def build(self, limit=20):
        return self._response(self.picks)

    def evaluate_symbols(self, symbols):
        self.evaluated.append(list(symbols))
        return self._response([evaluate_candidate(_facts(symbol=s), FAVORABLE)[0] for s in symbols])


def _picks():
    candidate = _pick().model_copy(update={"rank": 1})
    return [candidate]


def test_sources_merge_and_only_own_stocks_are_evaluated():
    service = _FakeService(_picks())
    result = build_radar(
        service, holdings=["2317.TWSE", "2330.TWSE"], watchlist=["2317.TWSE", "2454.TWSE"],
        get_quotes=lambda symbols: {s: _quote(symbol=s) for s in symbols},
        market_session=lambda: "closed",
    )
    assert [item.candidate.symbol for item in result.items] == ["2330.TWSE", "2317.TWSE", "2454.TWSE"]
    assert [item.sources for item in result.items] == [["pick", "holding"], ["holding", "watchlist"], ["watchlist"]]
    assert service.evaluated == [["2317.TWSE", "2454.TWSE"]]
    assert result.items[1].candidate.rank is None
    assert result.market_session == MarketStatus.OPEN.value


def test_own_stock_cap_prefers_holdings_and_reports_gap(monkeypatch):
    monkeypatch.setattr(radar, "MAX_OWN_SYMBOLS", 2)
    service = _FakeService(_picks())
    result = build_radar(
        service, holdings=["2317.TWSE", "2454.TWSE"], watchlist=["2603.TWSE"],
        get_quotes=lambda symbols: {}, market_session=lambda: "closed",
    )
    assert service.evaluated == [["2317.TWSE", "2454.TWSE"]]
    assert any("只顯示前 2 檔" in gap for gap in result.data_gaps)


def test_quote_failure_degrades_to_unavailable_with_gap():
    def boom(symbols):
        raise RuntimeError("network")

    result = build_radar(_FakeService(_picks()), holdings=[], watchlist=[], get_quotes=boom,
                         market_session=lambda: "closed")
    assert result.items[0].live.status == "unavailable"
    assert "即時報價暫時不可用" in result.data_gaps
    assert result.market_session == "closed"
    assert result.status == "degraded", "a ready selection without live quotes is a degraded radar"


@pytest.mark.parametrize(("quotes", "gap"), [
    (lambda symbols: {}, "即時報價暫時不可用"),
    (lambda symbols: {symbols[0]: _quote(symbol=symbols[0])}, "1 檔股票沒有即時報價"),
])
def test_missing_or_partial_quotes_degrade_status(quotes, gap):
    result = build_radar(_FakeService(_picks()), holdings=["2317.TWSE"], watchlist=[], get_quotes=quotes,
                         market_session=lambda: "closed")
    assert result.status == "degraded" and gap in result.data_gaps


def test_full_quote_coverage_keeps_selection_status():
    result = build_radar(_FakeService(_picks()), holdings=[], watchlist=[],
                         get_quotes=lambda symbols: {s: _quote(symbol=s) for s in symbols},
                         market_session=lambda: "closed")
    assert result.status == "ready"


# ── API ──

def test_api_rejects_invalid_holdings():
    response = TestClient(app).post("/api/taiwan/beginner-selection/radar", json={"holdings": ["abc"]})
    assert response.status_code == 400


def test_api_does_not_accept_holdings_in_the_query_string():
    response = TestClient(app).get("/api/taiwan/beginner-selection/radar", params={"holdings": "2330.TWSE"})
    assert "entry-radar-v1" not in response.text  # GET never reaches the radar (SPA fallback or 405)


def test_api_session_fallback_requires_a_verified_trading_day(monkeypatch):
    calls = []

    def fake_status(now, **kwargs):
        calls.append(kwargs)
        return MarketStatus.SCHEDULED_OPEN_UNVERIFIED

    def fake_build(service, *, holdings, watchlist, get_quotes, market_session):
        return build_radar(_FakeService(_picks()), holdings=[], watchlist=[],
                           get_quotes=lambda symbols: {}, market_session=market_session)

    monkeypatch.setattr(radar, "build_radar", fake_build)
    monkeypatch.setattr("app.taiwan.realtime.get_market_status", fake_status)
    monkeypatch.setattr("app.services.watchlist.list_symbols", lambda: [])
    body = TestClient(app).post("/api/taiwan/beginner-selection/radar", json={}).json()
    assert calls == [{"require_verified_trading_day": True}]
    assert body["market_session"] == "scheduled_open_unverified"


def test_api_passes_parsed_holdings_and_taiwan_watchlist(monkeypatch):
    seen = {}

    def fake_build(service, *, holdings, watchlist, get_quotes, market_session):
        seen.update(holdings=holdings, watchlist=watchlist)
        return build_radar(_FakeService(_picks()), holdings=[], watchlist=[],
                           get_quotes=lambda symbols: {}, market_session=lambda: "closed")

    monkeypatch.setattr(radar, "build_radar", fake_build)
    monkeypatch.setattr("app.services.watchlist.list_symbols",
                        lambda: [{"symbol": "2454.TWSE"}, {"symbol": "600519.SH"}])
    response = TestClient(app).post("/api/taiwan/beginner-selection/radar", json={"holdings": ["2317.twse"]})
    assert response.status_code == 200
    assert seen == {"holdings": ["2317.TWSE"], "watchlist": ["2454.TWSE"]}
    body = response.json()
    assert body["version"] == "entry-radar-v1" and body["items"][0]["live"]["status"] == "unavailable"

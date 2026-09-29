from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api import buy_points
from app.api.buy_points import SnapshotRequest
from app.taiwan.buy_point import (
    BuyPointConditions,
    BuyPointMarketData,
    BuyPointRiskFilters,
    BuyPointStrategy,
    BuyPointStrategyStore,
    builtin_presets,
    evaluate_buy_point,
    strategy_catalog,
)


def strategy(**kwargs):
    now = "2026-09-29T00:00:00+00:00"
    return BuyPointStrategy(
        id="test", name="測試", description="", category="test",
        conditions=BuyPointConditions(**kwargs),
        risk_filters=BuyPointRiskFilters(exclude_regulatory_unknown=False),
        created_at=now, updated_at=now,
    )


def data(**kwargs):
    return BuyPointMarketData(
        symbol="2330.TWSE", name="台積電", risk_data_available=True,
        quant_universe=True, **kwargs,
    )


def test_catalog_has_ten_presets():
    assert len(builtin_presets()) == 10
    assert {item.id for item in builtin_presets()} == {
        "quant_pullback", "breakout_high", "volume_breakout", "institutional_turn",
        "foreign_holding", "revenue_pullback", "earnings_confirmed", "value_growth",
        "event_confirmed", "resonance",
    }


def test_quant_triggered_and_explained():
    signal = evaluate_buy_point(
        strategy(quant_min=70),
        data(quant_score=78),
    )
    assert signal.status == "triggered"
    assert "Quant >= 70" in signal.triggered_conditions
    assert signal.failed_conditions == []


def test_missing_is_unavailable_not_false():
    signal = evaluate_buy_point(strategy(quant_min=70), data())
    assert signal.status == "unavailable"
    assert "Quant >= 70" in signal.failed_conditions


def test_approaching_breakout_is_explicit():
    closes = [{"close": value} for value in [95, 96, 97, 98, 99, 99.5]]
    signal = evaluate_buy_point(
        strategy(quant_min=60, breakout_window=5),
        data(quant_score=70, daily=closes, price=98.5),
    )
    assert signal.status == "approaching"
    assert "接近突破價" in signal.triggered_conditions


def test_near_price_does_not_override_unrelated_failure():
    closes = [{"close": value} for value in [95, 96, 97, 98, 99, 99.5]]
    signal = evaluate_buy_point(
        strategy(quant_min=60, breakout_window=5),
        data(quant_score=20, daily=closes, price=98.5),
    )
    assert signal.status == "waiting"


def test_resonance_accepts_either_price_branch():
    preset = next(item for item in builtin_presets() if item.id == "resonance")
    closes = [{"close": 100.0}] * 20 + [{"close": 105.0}]
    signal = evaluate_buy_point(
        preset,
        data(
            quant_score=80,
            daily=closes,
            price=105,
            institutional_trend=[-10, 5],
            regulatory_unknown=False,
            disposition=False,
            suspended=False,
            delisted=False,
            capital_reduction_critical=False,
            severe_event_risk=False,
        ),
    )
    assert signal.status == "triggered"
    assert "共振類別 3/3" in signal.triggered_conditions


def test_risk_gate_blocks_even_when_conditions_pass():
    signal = evaluate_buy_point(
        strategy(quant_min=60),
        data(quant_score=80, severe_event_risk=True),
    )
    assert signal.status == "blocked"
    assert signal.risk_flags == ["重大事件風險"]


def test_default_risk_gate_requires_verified_symbol_status():
    strict = strategy(quant_min=60).model_copy(update={"risk_filters": BuyPointRiskFilters()})
    unknown = evaluate_buy_point(strict, data(quant_score=80))
    clear = evaluate_buy_point(
        strict,
        data(
            quant_score=80, regulatory_unknown=False, disposition=False,
            suspended=False, delisted=False, capital_reduction_critical=False,
            severe_event_risk=False,
        ),
    )
    assert unknown.status == "unavailable"
    assert clear.status == "triggered"
    assert clear.risk_status == "clear"


def test_builtin_override_preserves_ten_strategy_catalog(tmp_path: Path):
    store = BuyPointStrategyStore(tmp_path / "strategies.json")
    disabled = builtin_presets()[0].model_copy(update={"enabled": False})
    store.save(disabled)
    catalog = strategy_catalog(store)
    assert len(catalog) == 10
    assert next(item for item in catalog if item.id == disabled.id).enabled is False


def test_market_data_uses_prior_twenty_days_and_populates_preset_fields(monkeypatch):
    rows = [
        {"date": f"2026-09-{day:02d}", "close": 100, "volume": 100}
        for day in range(1, 21)
    ] + [{"date": "2026-09-21", "close": 110, "volume": 500}]
    monkeypatch.setattr(buy_points, "_daily_data", lambda _symbol: (rows, "2026-09-21"))

    class EventService:
        @staticmethod
        def check_symbol_risk_status(_symbol, target_date=None, events=None):
            return {
                "is_disposition": False,
                "is_suspended": False,
                "has_risk_event": False,
                "risk_reason": "",
            }

    event = SimpleNamespace(
        symbol="2330.TWSE", code="2330", event_date="2026-09-15",
        event_type="dividend", severity="info",
    )
    item = SimpleNamespace(
        name="台積電", ma20=100, quant_score=80,
        foreign_net=50, foreign_net_5d=100,
        investment_trust_net=10, investment_trust_net_5d=50,
        foreign_shareholding_change_20d=1, revenue_yoy=10, revenue_mom=2,
        latest_eps=5, pe=18, pb=3,
    )
    result = buy_points._market_data(
        "2330.TWSE", item=item, event_service=EventService(), events=[event],
        risk_source_status="available", load_screen=False,
    )
    assert result.volume_average_20d == 100
    assert result.price_extension_pct == 10
    assert result.institutional_trend == [30, 60]
    assert result.positive_event is True
    assert result.regulatory_unknown is False


def test_signal_enrichment_covers_every_assigned_symbol(monkeypatch):
    symbols = [f"{index:04d}.TWSE" for index in range(201)]
    scopes: list[list[str]] = []

    class Store:
        @staticmethod
        def list_custom():
            return []

        @staticmethod
        def assignments():
            return {symbol: ["quant_pullback"] for symbol in symbols}

    def fake_run(_self, request):
        scopes.append(request.symbol_scope)
        return SimpleNamespace(items=[SimpleNamespace(symbol=symbol) for symbol in request.symbol_scope])

    monkeypatch.setattr("app.taiwan.screener.TaiwanScreenerService.run", fake_run)
    monkeypatch.setattr(
        "app.taiwan.events_service.get_event_service",
        lambda: SimpleNamespace(get_cached_regulatory_snapshot=lambda: ([], "available", "2026-09-29")),
    )
    monkeypatch.setattr(
        buy_points,
        "_market_data",
        lambda symbol, **_kwargs: BuyPointMarketData(
            symbol=symbol, quant_score=80, quant_universe=True,
            risk_data_available=True, regulatory_unknown=False,
        ),
    )
    signals = buy_points._signals_for_symbols(Store(), symbols)
    assert [len(scope) for scope in scopes] == [200, 1]
    assert len(signals) == 201


def _triggered_signal(strategy_id: str = "quant_pullback"):
    return buy_points.BuyPointSignal(
        strategy_id=strategy_id, symbol="2330.TWSE", name="台積電",
        detected_at="2026-09-29T00:00:00+00:00", data_as_of="2026-09-29",
        status="triggered", triggered_conditions=["條件成立"], price=100,
        quant_score=80, explanation="條件已成立", freshness="daily_cached",
        risk_status="clear",
    )


def test_alert_requires_rearm_and_routes_through_existing_adapters(tmp_path: Path, monkeypatch):
    store = BuyPointStrategyStore(tmp_path / "strategies.json")
    appended: list[list[dict]] = []
    monkeypatch.setattr(
        buy_points.alert_store,
        "append_many",
        lambda _data_dir, events: appended.append(events) or [event["alert_id"] for event in events],
    )

    class QuoteService:
        pushed = 0
        external = 0
        system = 0

        def push_alerts(self, _events):
            self.pushed += 1

        def _maybe_send_webhook(self, _events, _engine):
            self.external += 1

        def _maybe_send_system_notifications(self, _events):
            self.system += 1

    quote = QuoteService()
    signal = _triggered_signal()
    assert len(buy_points._dispatch_alerts(tmp_path, quote, store, [signal])) == 1
    assert buy_points._dispatch_alerts(tmp_path, quote, store, [signal]) == []
    assert quote.pushed == quote.external == quote.system == 1

    waiting = signal.model_copy(update={"status": "waiting"})
    buy_points._dispatch_alerts(tmp_path, quote, store, [waiting])
    assert buy_points._dispatch_alerts(tmp_path, quote, store, [signal]) == []
    store.mark_triggered("quant_pullback:2330.TWSE", buy_points.time.time() - 3601)
    assert len(buy_points._dispatch_alerts(tmp_path, quote, store, [signal])) == 1
    assert len(appended) == 2


def test_alert_state_is_not_advanced_when_persistence_fails(tmp_path: Path, monkeypatch):
    store = BuyPointStrategyStore(tmp_path / "strategies.json")
    monkeypatch.setattr(buy_points.alert_store, "append_many", lambda _data_dir, _events: [])
    with pytest.raises(RuntimeError, match="未完整持久化"):
        buy_points._dispatch_alerts(tmp_path, None, store, [_triggered_signal()])
    assert store.state("quant_pullback:2330.TWSE") is None
    assert store.last_triggered_at("quant_pullback:2330.TWSE") is None


def test_snapshot_rejects_stale_market_data(tmp_path: Path, monkeypatch):
    store = BuyPointStrategyStore(tmp_path / "strategies.json")
    monkeypatch.setattr(buy_points, "_store", lambda _request: store)
    monkeypatch.setattr(buy_points, "_signals_for_symbols", lambda _store, _symbols: [_triggered_signal()])
    monkeypatch.setattr(
        "app.taiwan.daily_update.resolve_target_latest_trading_date",
        lambda: date(2026, 9, 30),
    )
    with pytest.raises(buy_points.HTTPException) as exc:
        buy_points.save_snapshot(
            SnapshotRequest(strategy_id="quant_pullback", symbol="2330.TWSE"),
            SimpleNamespace(),
        )
    assert exc.value.status_code == 409
    assert "最新交易日" in exc.value.detail


def test_snapshot_preserves_unknown_risk_without_inventing_clear(tmp_path: Path, monkeypatch):
    store = BuyPointStrategyStore(tmp_path / "strategies.json")
    captured = []
    signal = _triggered_signal().model_copy(update={"risk_status": "unknown", "risk_flags": []})
    monkeypatch.setattr(buy_points, "_store", lambda _request: store)
    monkeypatch.setattr(buy_points, "_signals_for_symbols", lambda _store, _symbols: [signal])
    monkeypatch.setattr(
        "app.taiwan.daily_update.resolve_target_latest_trading_date",
        lambda: date(2026, 9, 29),
    )

    class ReviewService:
        @staticmethod
        def save_snapshot(request):
            captured.append(request)
            return SimpleNamespace(model_dump=lambda: {"snapshot_id": "saved"})

    monkeypatch.setattr(
        "app.taiwan.selection_review_service.get_selection_review_service",
        lambda: ReviewService(),
    )
    result = buy_points.save_snapshot(
        SnapshotRequest(strategy_id="quant_pullback", symbol="2330.TWSE"),
        SimpleNamespace(),
    )
    assert result == {"snapshot_id": "saved"}
    assert captured[0].items[0].risk_status == "unknown"
    assert captured[0].items[0].event_risk_summary is None

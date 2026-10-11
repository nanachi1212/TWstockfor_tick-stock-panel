"""Comparator invariants over the authoritative selection and snapshot cache."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_taiwan_beginner_selection import AS_OF, FAVORABLE, _facts

from app.main import app
from app.taiwan import beginner_selection as bs
from app.taiwan.beginner_comparator import build_comparison, comparison_group
from app.taiwan.beginner_selection import BeginnerSelectionService, evaluate_candidate


@pytest.fixture
def service(monkeypatch):
    bs.clear_beginner_selection_snapshot()
    key = [bs.BEGINNER_SELECTION_VERSION, AS_OF, ("comparator-test",)]
    pool = [_facts(symbol="2330.TWSE"), _facts(symbol="2317.TWSE", close=95.5, ma20=92.0)]
    calls = []
    builds = []
    monkeypatch.setattr(BeginnerSelectionService, "_cache_key", lambda self: tuple(key))
    monkeypatch.setattr(BeginnerSelectionService, "_eligible_date", staticmethod(lambda: AS_OF))
    monkeypatch.setattr(BeginnerSelectionService, "_request_intraday", staticmethod(lambda symbols: None))
    monkeypatch.setattr(BeginnerSelectionService, "_with_external", staticmethod(lambda c, **kwargs: c))

    def collect(self, scope, *, eligible_date=None):
        calls.append((scope, eligible_date))
        facts = pool if scope is None else [_facts(symbol=symbol) for symbol in scope if symbol != "9999.TWSE"]
        return FAVORABLE, facts, {fact.symbol: fact.amount for fact in facts}, [], len(facts)

    original = BeginnerSelectionService._build_snapshot

    def counted(self, snapshot_key):
        builds.append(snapshot_key)
        return original(self, snapshot_key)

    monkeypatch.setattr(BeginnerSelectionService, "_collect", collect)
    monkeypatch.setattr(BeginnerSelectionService, "_build_snapshot", counted)
    instance = BeginnerSelectionService(screener=object())
    yield instance, pool, calls, builds, key
    bs.clear_beginner_selection_snapshot()


def test_five_symbols_build_once_and_cache_scoped_evidence(service):
    instance, _pool, calls, builds, _key = service
    symbols = ["2330.TWSE", "2317.TWSE", "1101.TWSE", "2881.TWSE", "9999.TWSE"]
    cold = build_comparison(symbols, service=instance)
    warm = build_comparison(list(reversed(symbols)), service=instance)
    assert len(builds) == 1
    assert calls == [(None, AS_OF), (["1101.TWSE", "2881.TWSE", "9999.TWSE"], AS_OF)]
    assert cold.groups == warm.groups
    assert [c.model_dump() for c in cold.candidates] == [c.model_dump() for c in warm.candidates]
    scoped = next(c for c in cold.candidates if c.symbol == "1101.TWSE")
    assert scoped.rank is None
    missing = next(c for c in cold.candidates if c.symbol == "9999.TWSE")
    assert missing.close is None and missing.selection_state == "skip"
    assert comparison_group(missing) == "insufficient"
    assert missing.signal_strength == "weak"
    assert all(d.higher_symbol != missing.symbol for d in cold.differences)
    assert "9999.TWSE" not in {c.symbol for c in instance.build().candidates}


def test_batch_cache_invalidation_discards_scoped_evidence(service):
    instance, _pool, calls, builds, key = service
    symbols = ["2330.TWSE", "1101.TWSE"]
    build_comparison(symbols, service=instance)
    key[2] = ("changed-source",)
    build_comparison(symbols, service=instance)
    assert len(builds) == 2 and len(calls) == 4
    assert calls[1] == calls[3] == (["1101.TWSE"], AS_OF)


def test_batch_single_flight_reuses_same_scoped_misses(service):
    instance, _pool, calls, builds, _key = service
    with ThreadPoolExecutor(max_workers=3) as executor:
        responses = list(executor.map(
            lambda _: build_comparison(["2330.TWSE", "1101.TWSE"], service=instance), range(3),
        ))
    assert len(builds) == 1 and len(calls) == 2
    assert all(response.groups == responses[0].groups for response in responses)


def test_returned_candidate_mutation_does_not_mutate_snapshot(service):
    instance, _pool, _calls, _builds, _key = service
    first = build_comparison(["2330.TWSE", "2317.TWSE"], service=instance)
    first.candidates[0].close = 0
    first.candidates[0].dimensions.clear()
    second = build_comparison(["2330.TWSE", "2317.TWSE"], service=instance)
    assert second.candidates[0].close > 0 and second.candidates[0].dimensions


def test_uncacheable_batch_still_builds_selection_only_once(service, monkeypatch):
    instance, _pool, calls, builds, _key = service
    monkeypatch.setattr(BeginnerSelectionService, "_cache_key", lambda self: None)
    build_comparison(["2330.TWSE", "2317.TWSE", "1101.TWSE", "2881.TWSE", "9999.TWSE"], service=instance)
    assert len(builds) == 1 and len(calls) == 2


def test_action_groups_preserve_rank_despite_strong_no_chase(service, monkeypatch):
    instance, pool, _calls, _builds, _key = service
    no_chase, _ = evaluate_candidate(_facts(close=115.0, ma20=100.0), FAVORABLE)
    watch, _ = evaluate_candidate(_facts(symbol="2317.TWSE", close=95.5, ma20=92.0), FAVORABLE)
    # Adversarial contract fixture: group safety must hold for any strength.
    no_chase = no_chase.model_copy(update={"rank": 1, "signal_strength": "strong"})
    watch = watch.model_copy(update={"rank": 2, "signal_strength": "weak"})
    assert no_chase.selection_state == "no_chase" and watch.selection_state == "watch"
    base = instance.build()
    monkeypatch.setattr(instance, "evaluate_symbols", lambda symbols: base.model_copy(update={"candidates": [no_chase, watch]}))
    response = build_comparison([fact.symbol for fact in pool], service=instance)
    assert [(c.symbol, c.rank) for c in response.candidates] == [("2330.TWSE", 1), ("2317.TWSE", 2)]
    assert response.groups[0].symbols == ["2317.TWSE"]
    assert response.groups[2].symbols == ["2330.TWSE"]
    assert response.differences[0].higher_symbol == "2317.TWSE"
    assert "暫時不要追" in response.differences[0].reasons[0]


def test_factual_exclusion_is_skip_and_missing_is_data_insufficient():
    illiquid, _ = evaluate_candidate(_facts(amount=10_000_000), FAVORABLE)
    flagged, _ = evaluate_candidate(_facts(risk_status="flagged", risk_reason="處置"), FAVORABLE)
    missing, _ = evaluate_candidate(_facts(risk_status="unavailable"), FAVORABLE)
    assert comparison_group(illiquid) == comparison_group(flagged) == "avoid"
    assert comparison_group(missing) == "insufficient"


def test_unavailable_does_not_become_a_priority_reason(service):
    instance, pool, _calls, _builds, _key = service
    pool[:] = [
        _facts(symbol="2330.TWSE", revenue_yoy=None, revenue_status="unavailable"),
        _facts(symbol="2317.TWSE", revenue_yoy=-20),
    ]
    response = build_comparison(["2330.TWSE", "2317.TWSE"], service=instance)
    assert next(d for d in response.candidates[0].dimensions if d.key == "fundamental_context").status == "unavailable"
    assert response.differences == []
    assert "score" not in response.model_dump() and "priority" not in response.model_dump()


def test_supported_differences_are_plain_and_at_most_three(service):
    instance, pool, _calls, _builds, _key = service
    pool[:] = [_facts(symbol="2330.TWSE"), _facts(symbol="2317.TWSE", flow_ratio_5d=-0.02, revenue_yoy=-20)]
    response = build_comparison(["2330.TWSE", "2317.TWSE"], service=instance)
    assert response.differences and 1 <= len(response.differences[0].reasons) <= 3
    assert all("%" not in reason for reason in response.differences[0].reasons)


@pytest.mark.parametrize("symbols", [
    "", "2330.TWSE", "2330.TWSE,2330.TWSE", "2330.TWSE,bad.TWSE",
    "2330.TWSE,2317.twse", "2330.TWSE,../2317.TWSE", "2330.TWSE,2317.TW",
    ",".join(f"{1101 + i}.TWSE" for i in range(11)),  # over MAX_COMPARE_SYMBOLS (10)
])
def test_api_rejects_invalid_comparison_symbols_before_collecting(symbols, service):
    _instance, _pool, calls, builds, _key = service
    response = TestClient(app).get("/api/taiwan/beginner-selection/compare", params={"symbols": symbols})
    assert response.status_code == 400
    assert not calls and not builds


def test_api_contract_preserves_shared_market_and_price_evidence(service):
    instance, _pool, _calls, _builds, _key = service
    original = instance.build()
    response = TestClient(app).get("/api/taiwan/beginner-selection/compare", params={"symbols": "2330.TWSE,2317.TWSE"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == "beginner-comparator-v1"
    assert payload["market"] == original.market.model_dump(mode="json")
    by_symbol = {c.symbol: c for c in original.candidates}
    for candidate in payload["candidates"]:
        source = by_symbol[candidate["symbol"]]
        assert candidate["rank"] == source.rank
        assert candidate["technical_panel"] == source.technical_panel.model_dump(mode="json")


@pytest.mark.parametrize("live", [False, True])
def test_fugle_overlay_cannot_change_frozen_rank_prices_or_states(monkeypatch, live):
    candidates = bs.rank_candidates([
        evaluate_candidate(_facts(symbol="2330.TWSE"), FAVORABLE),
        evaluate_candidate(_facts(symbol="2317.TWSE", close=95.5, ma20=92.0), FAVORABLE),
    ])
    frozen = bs.BeginnerSelectionResponse(
        status="ready", generated_at=AS_OF, as_of=AS_OF, market=FAVORABLE, candidates=candidates,
    )
    instance = BeginnerSelectionService(screener=object())
    monkeypatch.setattr(instance, "evaluate_symbols", lambda symbols: frozen.model_copy(deep=True))
    monkeypatch.setattr(instance, "_request_intraday", lambda symbols: None)
    snapshot = SimpleNamespace(
        trade_volume_at_bid=20.0, trade_volume_at_ask=80.0,
        observed_at=datetime.now(UTC), last_price=9999.0, trade_volume=100.0,
        trade_value=999900.0, bids=[], asks=[], is_close=False,
    ) if live else None
    provider = SimpleNamespace(observe=lambda symbol: SimpleNamespace(
        status="available" if live else "disabled", snapshot=snapshot,
    ))
    monkeypatch.setattr("app.taiwan.realtime.fugle_provider.get_fugle_aggregates_provider", lambda: provider)
    monkeypatch.setattr("app.taiwan.external_context.intraday_context", lambda symbol: bs.ExternalProviderResult.unavailable(
        "fugle_marketdata:websocket:aggregates", "fixture_context",
    ))
    result = build_comparison([c.symbol for c in candidates], service=instance)
    for before, after in zip(candidates, result.candidates, strict=True):
        assert (after.rank, after.selection_state, after.signal_strength) == (
            before.rank, before.selection_state, before.signal_strength,
        )
        assert after.close == before.close and after.trade_plan == before.trade_plan
        assert after.dimensions == before.dimensions
        assert after.technical_panel.support == before.technical_panel.support
        assert before.technical_panel.inner_outer.outer_pct is None
        if live:
            assert after.technical_panel.inner_outer.outer_pct == 80.0
        else:
            assert after.technical_panel.inner_outer.status == "data_insufficient"
            assert after.technical_panel.inner_outer.outer_pct is None


def test_api_collection_failure_has_explicit_error(monkeypatch):
    def fail(symbols):
        raise RuntimeError("fixture provider failure")

    monkeypatch.setattr("app.taiwan.beginner_comparator.build_comparison", fail)
    response = TestClient(app).get("/api/taiwan/beginner-selection/compare", params={"symbols": "2330.TWSE,2317.TWSE"})
    assert response.status_code == 500
    assert response.json()["detail"] == "股票比較資料彙整失敗"

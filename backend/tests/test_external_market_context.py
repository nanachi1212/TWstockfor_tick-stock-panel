from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.taiwan import external_context
from app.taiwan.beginner_technical import fugle_inner_outer_evidence
from app.taiwan.providers.fx_context import (
    FRANKFURTER_SOURCE,
    FrankfurterFxProvider,
    normalize_cbc_rates,
)
from app.taiwan.realtime.calendar import TAIPEI_TZ
from app.taiwan.realtime.fugle_provider import (
    FugleAggregatesProvider,
    parse_fugle_aggregates_message,
)


def _fugle_payload(*, inner=40, outer=60, observed_at: datetime | None = None):
    stamp = observed_at or datetime.now(TAIPEI_TZ)
    micros = int(stamp.timestamp() * 1_000_000)
    return {
        "event": "data",
        "channel": "aggregates",
        "data": {
            "date": stamp.date().isoformat(),
            "exchange": "TWSE",
            "symbol": "2330",
            "lastPrice": 1200,
            "bids": [{"price": 1195, "size": 10}],
            "asks": [{"price": 1200, "size": 20}],
            "total": {
                "tradeVolumeAtBid": inner,
                "tradeVolumeAtAsk": outer,
                "tradeVolume": 1_000,
                "tradeValue": 1_200_000,
                "time": micros,
            },
            "isClose": False,
            "lastUpdated": micros,
        },
    }


def _cbc_rows(days=85, *, end: datetime | None = None):
    final = (end or datetime.now(UTC)).date()
    rows = []
    for offset in range(days):
        day = final - timedelta(days=days - 1 - offset)
        for quote, rate in (
            ("TWD", 30 + offset * 0.02),
            ("JPY", 145 + offset * 0.1),
            ("EUR", 0.9 + offset * 0.0002),
        ):
            rows.append({"date": day.isoformat(), "base": "USD", "quote": quote, "rate": rate})
    return rows


def test_fugle_key_missing_is_unavailable_and_never_connects():
    calls = []
    provider = FugleAggregatesProvider(api_key="", connect_factory=lambda *a, **k: calls.append(1))
    provider.request_symbols(["2330.TWSE"])
    assert provider.observe("2330.TWSE").status == "disabled"
    assert provider.health_metadata()["status"] == "config_missing"
    assert calls == []


def test_fugle_valid_payload_uses_official_inner_outer_and_market_depth():
    snapshot = parse_fugle_aggregates_message(_fugle_payload())
    assert snapshot is not None
    assert snapshot.symbol == "2330.TWSE"
    assert snapshot.trade_volume_at_bid == 40_000
    assert snapshot.trade_volume_at_ask == 60_000
    assert snapshot.last_price == 1200
    assert snapshot.trade_volume == 1_000_000
    assert snapshot.trade_value == 1_200_000
    assert snapshot.bids == ((1195.0, 10_000.0),)
    evidence = fugle_inner_outer_evidence(type("Observation", (), {"status": "available", "snapshot": snapshot})())
    assert evidence.status == "available"
    assert evidence.inner_pct == 40
    assert evidence.outer_pct == 60
    assert evidence.last_price == 1200


def test_lightweight_context_reuses_beginner_inner_outer_evidence(monkeypatch):
    snapshot = parse_fugle_aggregates_message(_fugle_payload())
    observation = SimpleNamespace(status="available", snapshot=snapshot)
    provider = SimpleNamespace(
        enabled=True,
        request_symbols=lambda _symbols: None,
        observe=lambda _symbol: observation,
    )
    monkeypatch.setattr(external_context, "get_fugle_aggregates_provider", lambda: provider)

    result = external_context.intraday_context("2330.TWSE")

    assert result.status == "available"
    assert result.data["inner_outer"]["inner_pct"] == 40
    assert result.data["inner_outer"]["outer_pct"] == 60


def test_fugle_zero_volume_and_stale_timestamp_fail_closed():
    provider = FugleAggregatesProvider(api_key="test")
    zero = provider.ingest_message(_fugle_payload(inner=0, outer=0))
    assert zero is not None
    assert provider.observe("2330.TWSE").status == "waiting"

    old = datetime.now(TAIPEI_TZ) - timedelta(minutes=10)
    stale_provider = FugleAggregatesProvider(api_key="test")
    stale_provider.ingest_message(_fugle_payload(observed_at=old))
    assert stale_provider.observe("2330.TWSE").status == "stale"


def test_fugle_disconnect_retries_without_leaking_error_details():
    attempts = []

    def disconnected(*args, **kwargs):
        attempts.append((args, kwargs))
        raise ConnectionError("secret-token")

    class StopAfterTwo:
        waits = 0

        def is_set(self):
            return self.waits >= 2

        def wait(self, _delay):
            self.waits += 1
            return False

    provider = FugleAggregatesProvider(api_key="test", connect_factory=disconnected)
    provider._stop = StopAfterTwo()  # type: ignore[assignment]
    provider._run_forever()
    health = provider.health_metadata()
    assert len(attempts) == 2
    assert health["status"] == "provider_error"
    assert health["error"] == "ConnectionError"
    assert "secret-token" not in str(health)


def test_frankfurter_history_cross_rates_changes_and_provenance():
    result = normalize_cbc_rates(_cbc_rows(), retrieved_at=datetime.now(UTC).isoformat())
    assert result.status == "available"
    assert result.source == FRANKFURTER_SOURCE
    assert result.data["pairs"]["USD/TWD"]["change_5d_pct"] is not None
    assert result.data["pairs"]["JPY/TWD"]["current"] is not None
    assert result.data["pairs"]["EUR/TWD"]["change_60d_pct"] is not None


def test_frankfurter_missing_dates_do_not_become_zero_and_old_data_is_stale():
    rows = _cbc_rows(days=2, end=datetime(2020, 1, 2, tzinfo=UTC))
    rows = [row for row in rows if not (row["date"] == "2020-01-01" and row["quote"] == "JPY")]
    result = normalize_cbc_rates(rows, retrieved_at=datetime.now(UTC).isoformat())
    assert result.status == "stale"
    assert result.data["pairs"]["USD/TWD"]["change_5d_pct"] is None
    assert result.data["pairs"]["JPY/TWD"]["change_5d_pct"] is None


def test_frankfurter_uses_daily_cache_and_provider_failure_returns_stale(tmp_path):
    calls = []

    def fetcher(*_args, **_kwargs):
        calls.append(1)
        return _cbc_rows()

    provider = FrankfurterFxProvider(cache_path=tmp_path / "fx.json", fetcher=fetcher)
    first = provider.get_context()
    second = provider.get_context()
    assert first.status == second.status == "available"
    assert len(calls) == 1

    broken_calls = []

    def broken(*_args, **_kwargs):
        broken_calls.append(1)
        raise TimeoutError("offline")

    fallback = FrankfurterFxProvider(cache_path=tmp_path / "fx.json", fetcher=broken)
    future = datetime.now(UTC) + timedelta(days=2)
    result = fallback.get_context(now=future)
    assert result.status == "stale"
    assert result.data is not None
    assert result.error_reason == "provider_unavailable"
    assert result.retrieved_at == first.retrieved_at
    assert fallback.health_metadata()["last_attempt"] == future.isoformat()
    assert fallback.health_metadata()["last_success"] == first.retrieved_at
    assert fallback.get_context(now=future).status == "stale"
    assert len(broken_calls) == 1

    invalid = FrankfurterFxProvider(cache_path=tmp_path / "fx.json", fetcher=lambda *_a, **_k: [])
    invalid_result = invalid.get_context(now=future + timedelta(days=1))
    assert invalid_result.status == "stale"
    assert invalid_result.data == first.data
    assert invalid_result.error_reason == "missing_rates"


def test_frankfurter_cached_context_never_fetches_on_core_request_path(tmp_path):
    calls = []
    provider = FrankfurterFxProvider(
        cache_path=tmp_path / "missing.json",
        fetcher=lambda *_args, **_kwargs: calls.append(1),
    )
    result = provider.cached_context()
    assert result.status == "unavailable"
    assert result.error_reason == "not_queried"
    assert calls == []

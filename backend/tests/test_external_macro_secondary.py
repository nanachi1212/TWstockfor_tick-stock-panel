from __future__ import annotations

import threading
from datetime import UTC, date, datetime, timedelta

from app.taiwan.providers.finbridge import FinBridgeProvider, build_cross_check
from app.taiwan.providers.fred_macro import FRED_SERIES, FredMacroProvider, normalize_fred_series


def _fred_payload(series_id: str, *, realtime_start: str = "2026-10-01"):
    return {
        "realtime_start": realtime_start,
        "realtime_end": realtime_start,
        "observations": [
            {
                "date": "2026-09-30",
                "value": "4.25" if series_id == "DGS10" else "3.50",
                "realtime_start": realtime_start,
                "realtime_end": realtime_start,
            },
            {
                "date": "2026-10-01",
                "value": ".",
                "realtime_start": realtime_start,
                "realtime_end": realtime_start,
            },
        ],
    }


def test_fred_key_missing_is_unavailable_without_calls(tmp_path):
    calls = []
    provider = FredMacroProvider(api_key="", cache_dir=tmp_path, requester=lambda *args: calls.append(args))
    result = provider.get_context()
    assert result.status == "unavailable"
    assert result.error_reason == "config_missing"
    assert result.data is None
    assert calls == []


def test_fred_cached_context_never_fetches_on_core_request_path(tmp_path):
    calls = []
    provider = FredMacroProvider(
        api_key="test", cache_dir=tmp_path, requester=lambda *args: calls.append(args),
    )
    result = provider.cached_context()
    assert result.status == "unavailable"
    assert result.error_reason == "not_queried"
    assert calls == []


def test_fred_observations_preserve_vintage_metadata_and_missing_values():
    series = normalize_fred_series("DGS10", _fred_payload("DGS10", realtime_start="2020-04-01"))
    assert series is not None
    assert series["latest"]["observed_date"] == "2026-09-30"
    assert series["latest"]["value"] == 4.25
    assert series["latest"]["realtime_start"] == "2020-04-01"
    assert len(series["observations"]) == 1


def test_fred_context_supports_vintage_and_global_daily_cache(tmp_path):
    calls = []

    def requester(series_id, vintage_date):
        calls.append((series_id, vintage_date))
        return _fred_payload(series_id, realtime_start=(vintage_date or date(2026, 10, 5)).isoformat())

    provider = FredMacroProvider(api_key="test", cache_dir=tmp_path, requester=requester)
    current = provider.get_context()
    cached = provider.get_context()
    assert current.status == cached.status == "available"
    assert len(calls) == len(FRED_SERIES)
    assert current.data["summary"].startswith("全球環境")
    assert all(item["series_id"] in FRED_SERIES for item in current.data["series"])

    vintage = provider.get_context(vintage_date=date(2020, 4, 1))
    assert vintage.data["vintage_date"] == "2020-04-01"
    assert all(vintage_date == date(2020, 4, 1) for _series, vintage_date in calls[-len(FRED_SERIES):])
    assert all(
        item["latest"]["realtime_start"] == "2020-04-01"
        for item in vintage.data["series"]
    )


def test_fred_missing_series_is_partial_not_zero(tmp_path):
    def requester(series_id, _vintage_date):
        return {"observations": []} if series_id == "INDPRO" else _fred_payload(series_id)

    result = FredMacroProvider(api_key="test", cache_dir=tmp_path, requester=requester).get_context()
    assert result.status == "partial"
    assert result.data["missing_series"] == ["INDPRO"]
    assert all(item["series_id"] != "INDPRO" for item in result.data["series"])


def test_fred_summary_fails_closed_without_rate_inputs(tmp_path):
    def requester(series_id, _vintage_date):
        if series_id in {"DGS10", "T10Y2Y"}:
            return {"observations": []}
        return _fred_payload(series_id)

    result = FredMacroProvider(api_key="test", cache_dir=tmp_path, requester=requester).get_context()
    assert result.status == "partial"
    assert "資料不足" in result.data["summary"]


def test_finbridge_key_missing_is_unavailable_without_calls(tmp_path):
    calls = []
    provider = FinBridgeProvider(api_key="", cache_dir=tmp_path, requester=lambda *args: calls.append(args))
    result = provider.cross_check("2330.TWSE", official={"pe": 20, "eps": 10})
    assert result.status == "unavailable"
    assert result.data is None
    assert result.error_reason == "config_missing"
    assert calls == []


def test_finbridge_cached_cross_check_never_fetches_on_core_request_path(tmp_path):
    calls = []
    provider = FinBridgeProvider(
        api_key="test", cache_dir=tmp_path, requester=lambda *args: calls.append(args),
    )
    result = provider.cached_cross_check("2330.TWSE", official={"pe": 20, "eps": 10})
    assert result.status == "unavailable"
    assert result.error_reason == "not_queried"
    assert calls == []


def test_finbridge_official_match_and_mismatch_never_overwrite_primary(tmp_path):
    def requester(_code, endpoint):
        if endpoint == "valuation":
            return {"per": 20.5, "data_as_of": "2026-10-03"}
        return {"rows": [{"diluted_eps": 9.8, "filing_date": "2026-08-14"}]}

    provider = FinBridgeProvider(api_key="test", cache_dir=tmp_path, requester=requester)
    matched = provider.cross_check("2330.TWSE", official={"pe": 20, "eps": 10})
    assert matched.data["secondary"]["provider"] == "finbridge"
    assert matched.data["cross_check"]["status"] == "partial"
    assert matched.data["cross_check"]["metrics"]["pe"]["status"] == "matched"
    assert matched.data["cross_check"]["metrics"]["eps"]["status"] == "unavailable"
    assert matched.data["official"] == {"pe": 20, "eps": 10}
    assert matched.data["authority"] == "official_values_are_not_overwritten"

    mismatch = provider.cross_check("2330.TWSE", official={"pe": 10, "eps": 5})
    assert mismatch.data["cross_check"]["status"] == "mismatch"
    assert mismatch.data["official"] == {"pe": 10, "eps": 5}
    assert mismatch.data["secondary"]["pe"] == 20.5


def test_finbridge_unavailable_values_remain_null():
    result = build_cross_check({"pe": None, "eps": None}, {})
    assert result["cross_check"]["status"] == "unavailable"
    assert result["official"]["pe"] is None
    assert result["secondary"]["pe"] is None


def test_finbridge_provider_failure_is_unavailable_not_exception(tmp_path):
    calls = []

    def broken(_code, _endpoint):
        calls.append(1)
        raise TimeoutError("offline")

    provider = FinBridgeProvider(api_key="test", cache_dir=tmp_path, requester=broken)
    result = provider.cross_check(
        "2330.TWSE", official={"pe": 20, "eps": 10}, now=datetime.now(UTC),
    )
    assert result.status == "unavailable"
    assert result.data is None
    assert result.error_reason == "provider_unavailable"
    assert provider.cross_check("2330.TWSE", official={"pe": 20, "eps": 10}).status == "unavailable"
    assert len(calls) == 1


def test_provider_requests_do_not_block_cached_reads(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def blocked_fred(series_id, _vintage_date):
        if series_id == "FEDFUNDS":
            started.set()
            assert release.wait(2)
        return _fred_payload(series_id)

    fred = FredMacroProvider(api_key="test", cache_dir=tmp_path, requester=blocked_fred)
    refresh = threading.Thread(target=fred.get_context)
    refresh.start()
    assert started.wait(1)
    assert fred.cached_context().error_reason == "not_queried"
    release.set()
    refresh.join(2)
    assert not refresh.is_alive()

    started.clear()
    release.clear()

    def blocked_finbridge(_code, _endpoint):
        started.set()
        assert release.wait(2)
        return {"per": 20.0, "data_as_of": "2026-10-03"}

    finbridge = FinBridgeProvider(api_key="test", cache_dir=tmp_path, requester=blocked_finbridge)
    refresh = threading.Thread(
        target=finbridge.cross_check,
        args=("2330.TWSE",),
        kwargs={"official": {"pe": 20, "eps": 10}},
    )
    refresh.start()
    assert started.wait(1)
    assert finbridge.cached_cross_check(
        "2330.TWSE", official={"pe": 20, "eps": 10},
    ).error_reason == "not_queried"
    release.set()
    refresh.join(2)
    assert not refresh.is_alive()


def test_refresh_failures_preserve_success_and_attempt_times(tmp_path):
    now = datetime(2026, 10, 5, tzinfo=UTC)
    fred = FredMacroProvider(
        api_key="test", cache_dir=tmp_path,
        requester=lambda series_id, _vintage: _fred_payload(series_id),
    )
    fred_success = fred.get_context(now=now)
    fred.requester = lambda *_args: (_ for _ in ()).throw(TimeoutError("offline"))
    fred_failed = fred.get_context(now=now + timedelta(days=2))
    assert fred_failed.status == "stale"
    assert fred_failed.retrieved_at == fred_success.retrieved_at
    assert fred.health_metadata()["last_attempt"] == (now + timedelta(days=2)).isoformat()
    assert fred.health_metadata()["last_success"] == fred_success.retrieved_at

    finbridge = FinBridgeProvider(
        api_key="test", cache_dir=tmp_path,
        requester=lambda _code, endpoint: (
            {"per": 20.0, "data_as_of": "2026-10-03"}
            if endpoint == "valuation" else {"rows": []}
        ),
    )
    finbridge_success = finbridge.cross_check(
        "2330.TWSE", official={"pe": 20, "eps": 10}, now=now,
    )
    finbridge.requester = lambda *_args: (_ for _ in ()).throw(TimeoutError("offline"))
    finbridge_failed = finbridge.cross_check(
        "2330.TWSE", official={"pe": 20, "eps": 10}, now=now + timedelta(days=2),
    )
    assert finbridge_failed.status == "stale"
    assert finbridge_failed.retrieved_at == finbridge_success.retrieved_at
    assert finbridge.health_metadata()["last_attempt"] == (now + timedelta(days=2)).isoformat()
    assert finbridge.health_metadata()["last_success"] == finbridge_success.retrieved_at

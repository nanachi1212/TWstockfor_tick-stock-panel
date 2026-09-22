"""Taiwan provider throttling on the shared app.rate_limits slot table.

The Taiwan path uses namespaced buckets (taiwan:twse ...) so it can never share
accounting with the legacy TickFlow / A-share buckets, which stay keyed by rpm
value.  The last test in this module pins that legacy behaviour.
"""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from itertools import pairwise

import pytest

from app.rate_limits import _next_slot, _slot_lock, apply_safety_rpm, sleep_between_batches
from app.taiwan.providers import http as taiwan_http

TWSE_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=20250102"
TPEX_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date=114/01/02"
MOPS_URL = "https://mops.twse.com.tw/mops/api/t05st01"
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/2330.TW"

# Fast enough for a unit test, slow enough to measure on Windows' clock.
RPM = 480
INTERVAL = 60.0 / apply_safety_rpm(RPM)


def _reset_slots() -> None:
    with _slot_lock:
        _next_slot.clear()


def test_hosts_map_to_namespaced_taiwan_keys() -> None:
    assert taiwan_http.rate_limit_key(TWSE_URL) == "taiwan:twse"
    assert taiwan_http.rate_limit_key(TPEX_URL) == "taiwan:tpex"
    # mops.twse.com.tw is a twse.com.tw subdomain but must get its own budget.
    assert taiwan_http.rate_limit_key(MOPS_URL) == "taiwan:mops"
    assert taiwan_http.rate_limit_key(FINMIND_URL) == "taiwan:finmind"
    assert taiwan_http.rate_limit_key(YAHOO_URL) == "taiwan:yahoo"
    assert taiwan_http.rate_limit_key("https://smart.tdcc.com.tw/x") == "taiwan:tdcc"
    assert set(taiwan_http.SOURCE_RPM) == {
        "taiwan:twse", "taiwan:tpex", "taiwan:mops",
        "taiwan:tdcc", "taiwan:finmind", "taiwan:yahoo",
    }


def test_unregistered_host_is_not_throttled() -> None:
    """Unregistered keys keep the current behaviour: straight through, no wait."""
    _reset_slots()
    assert taiwan_http.rate_limit_key("http://example.com/test") is None
    t0 = time.perf_counter()
    for _ in range(5):
        assert taiwan_http.throttle("http://example.com/test", rpm=RPM) == 0.0
    assert time.perf_counter() - t0 < 0.05
    assert _next_slot == {}


def test_requests_beyond_rpm_are_blocked() -> None:
    _reset_slots()
    t0 = time.perf_counter()
    for _ in range(4):
        taiwan_http.throttle(TWSE_URL, rpm=RPM)
    elapsed = time.perf_counter() - t0
    # 4 requests = 1 free cold-start slot + 3 paced slots.
    assert elapsed >= 3 * INTERVAL * 0.9, elapsed


def test_provider_budgets_are_independent() -> None:
    """Spending TWSE's budget must not delay TPEx, FinMind or Yahoo."""
    _reset_slots()
    for _ in range(4):
        taiwan_http.throttle(TWSE_URL, rpm=RPM)
    t0 = time.perf_counter()
    for url in (TPEX_URL, MOPS_URL, FINMIND_URL, YAHOO_URL):
        assert taiwan_http.throttle(url, rpm=RPM) == 0.0
    assert time.perf_counter() - t0 < 0.05


def test_cold_start_releases_exactly_one_request() -> None:
    """A cold bucket must hand out 1 token, not a full burst-sized bucket."""
    _reset_slots()
    assert taiwan_http.throttle(TWSE_URL, rpm=RPM) == 0.0
    for _ in range(3):
        assert taiwan_http.throttle(TWSE_URL, rpm=RPM) >= INTERVAL * 0.9


def test_thread_pool_first_batch_is_spaced_not_simultaneous() -> None:
    """DEFAULT_CONCURRENCY=5 workers starting together must not all fire at once."""
    _reset_slots()
    sent: list[float] = []
    lock = threading.Lock()

    def work(_: int) -> None:
        taiwan_http.throttle(TWSE_URL, rpm=RPM)
        with lock:
            sent.append(time.perf_counter())

    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(work, range(5)))

    sent.sort()
    gaps = [b - a for a, b in pairwise(sent)]
    assert len(gaps) == 4
    for gap in gaps:
        assert gap >= INTERVAL * 0.85, gaps


def test_high_concurrency_never_exceeds_the_budget() -> None:
    """check-and-decrement atomicity: releases must fit the elapsed window."""
    _reset_slots()
    threads = 12
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=threads) as pool:
        list(pool.map(lambda _: taiwan_http.throttle(TWSE_URL, rpm=RPM), range(threads)))
    elapsed = time.perf_counter() - t0
    # One free cold-start slot plus one slot per elapsed interval.
    assert threads <= elapsed / INTERVAL + 1.5, (threads, elapsed, INTERVAL)


def test_429_retry_path_still_consumes_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every retry attempt takes its own slot, so a 429 storm cannot bypass rpm."""
    _reset_slots()
    calls = {"count": 0}

    def mock_urlopen(req, timeout=None):
        calls["count"] += 1
        if calls["count"] < 3:
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)
        import io
        return io.BytesIO(b'{"stat": "OK"}')

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    started = time.monotonic()
    payload = taiwan_http.fetch_json(
        TWSE_URL, rpm=RPM, max_attempts=3, initial_backoff=0.0
    )

    assert calls["count"] == 3
    assert payload == {"stat": "OK"}
    # 3 attempts advanced the taiwan:twse timeline by 3 slots, not 1.
    with _slot_lock:
        advanced = _next_slot["taiwan:twse"] - started
    assert advanced >= 3 * INTERVAL * 0.95, advanced


def test_legacy_ashare_buckets_are_untouched() -> None:
    """Taiwan keys must not alias the rpm-keyed buckets the A-share path uses."""
    _reset_slots()
    taiwan_http.throttle(TWSE_URL, rpm=RPM)
    taiwan_http.throttle(TWSE_URL, rpm=RPM)

    effective = apply_safety_rpm(RPM)
    with _slot_lock:
        keys = set(_next_slot)
    assert keys == {"taiwan:twse"}
    assert effective not in keys and RPM not in keys

    # Legacy contract unchanged: index=0 reserves without sleeping.
    t0 = time.perf_counter()
    sleep_between_batches(0, rpm=effective)
    sleep_between_batches(0, rpm=effective)
    assert time.perf_counter() - t0 < 0.05

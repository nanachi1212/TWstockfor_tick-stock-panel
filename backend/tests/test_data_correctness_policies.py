"""Per-dataset freshness policies: timing gaps are not failures, failures keep real reasons."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from app.taiwan import data_health_center as dhc
from app.taiwan.data_health_center import (
    DATASETS,
    DataHealthService,
    daily_policy,
    expected_financial_period,
    expected_revenue_date,
    finmind_policy,
)
from app.taiwan.realtime.calendar import TAIPEI_TZ

TARGET = "2026-09-30"
AFTER_CLOSE = datetime(2026, 9, 30, 16, 20, tzinfo=TAIPEI_TZ)
EVENING = datetime(2026, 9, 30, 19, 0, tzinfo=TAIPEI_TZ)


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)


def policy(key="daily", **overrides):
    args = dict(
        freshness_status="stale", days_behind=1, target=TARGET, exchanges={"TWSE", "TPEX"},
        row_statuses=["current"], pending=[], failed=[], attempted=False, now=AFTER_CLOSE,
    )
    args.update(overrides)
    return daily_policy(key, **args)


# ── Daily / Institutional / Margin ───────────────────────────────────────────


def test_daily_current():
    assert policy(freshness_status="current", days_behind=0)[0] == "current"


def test_daily_before_scheduled_run_is_not_run_not_stale():
    status, reason = policy()
    assert status == "not_run" and "16:30" in reason


def test_daily_publication_delay_is_awaiting_publication():
    status, _ = policy(attempted=True, failed=["empty_official_daily_snapshot"])
    assert status == "awaiting_publication"


def test_daily_real_failure_after_attempt_is_stale_with_safe_reason():
    status, reason = policy(
        attempted=True, now=EVENING,
        failed=["official_daily_snapshot_incomplete:2026-09-30:TPEX:ReadError https://x/?t=secret"],
    )
    assert status == "stale" and "連線中斷" in reason and "secret" not in reason


def test_daily_not_attempted_after_grace_is_stale():
    assert policy(now=EVENING)[0] == "stale"


def test_institutional_one_exchange_partition_is_partial_with_named_exchange():
    status, reason = policy("institutional", freshness_status="current", days_behind=0,
                            exchanges={"TWSE"})
    assert status == "partial" and "TWSE" in reason and "TPEX" in reason


def test_complete_but_older_partition_is_not_reported_as_exchange_missing():
    status, reason = policy("institutional", days_behind=2)
    assert status == "stale" and "缺少" not in reason


def test_margin_publication_delay_before_evening():
    status, reason = policy("margin", attempted=True)
    assert status == "awaiting_publication" and "晚間" in reason


def test_margin_pending_from_updater_names_the_missing_exchange():
    status, reason = policy("margin", attempted=True, pending=["TPEX_not_published"])
    assert status == "awaiting_publication" and "TPEx" in reason


def test_refresh_never_persists_one_exchange(tmp_path):
    from app.taiwan.institutional_margin_refresh import TaiwanMarginRefreshService
    from app.taiwan.margin_store import TaiwanMarginStore

    store = TaiwanMarginStore(data_dir=tmp_path / "margin")
    row = SimpleNamespace(symbol="2330.TWSE", meta=None, unit="shares", **{
        k: 1 for k in ("margin_previous_balance", "margin_buy", "margin_sell",
                       "margin_cash_redemption", "margin_balance", "margin_change",
                       "short_previous_balance", "short_sell", "short_cover",
                       "short_stock_redemption", "short_balance", "short_change")
    }, short_margin_ratio=1.0)
    exchange = lambda rows: SimpleNamespace(  # noqa: E731
        build_url=lambda d: "mock://", parse_payload=lambda payload, d, url: rows)
    provider = SimpleNamespace(twse=exchange([row]), tpex=exchange([]))
    service = TaiwanMarginRefreshService(store=store, provider=provider)
    service._fetch_json = lambda url: {}
    day = date(2026, 9, 29)
    res = service.refresh_dates(day, day)
    assert res["pending_dates"] == [{"date": "2026-09-29", "reason": "TPEX_not_published"}]
    assert res["dates_fetched"] == 0 and res["failed_dates"] == []
    assert day not in store.available_dates()  # retried next run, never marked complete

    provider.tpex = exchange([])
    provider.twse = exchange([])
    assert service.refresh_dates(day, day)["pending_dates"][0]["reason"] == "official_not_published"


def test_updater_records_pending_and_health_reads_it(tmp_path, monkeypatch):
    from app.taiwan.daily_store import TaiwanDailyStore
    from app.taiwan.daily_update import TaiwanDailyUpdateService, read_last_run
    from app.taiwan.institutional_store import TaiwanInstitutionalStore
    from app.taiwan.margin_store import TaiwanMarginStore

    day = date(2026, 9, 30)
    daily = TaiwanDailyStore(data_dir=tmp_path / "taiwan" / "daily")
    ok = {"dates_requested": 1, "dates_fetched": 1, "dates_skipped": 0,
          "total_rows_written": 2, "failed_dates": []}
    svc = TaiwanDailyUpdateService(
        daily_store=daily,
        inst_store=TaiwanInstitutionalStore(data_dir=tmp_path / "taiwan" / "inst"),
        margin_store=TaiwanMarginStore(data_dir=tmp_path / "taiwan" / "margin"),
        daily_service=SimpleNamespace(refresh_dates=lambda **k: ok),
        inst_service=SimpleNamespace(refresh_dates=lambda *a, **k: ok),
        margin_service=SimpleNamespace(refresh_dates=lambda *a, **k: {
            **ok, "dates_fetched": 0, "total_rows_written": 0,
            "pending_dates": [{"date": "2026-09-30", "reason": "official_not_published"}]}),
    )
    assert svc.benchmark_store is None  # injected wiring never hits the network
    result = svc.run_update(target_date=day)
    assert result.margin.pending_dates[0]["reason"] == "official_not_published"
    recorded = read_last_run(daily)
    assert recorded["target_latest_trading_date"] == "2026-09-30"
    assert recorded["margin"]["pending_dates"][0]["date"] == "2026-09-30"


# ── Trading date evidence & Quant ────────────────────────────────────────────


def test_confirmed_trading_date_from_official_daily_partition(tmp_path):
    from app.taiwan.daily_store import TaiwanDailyStore

    day = date(2026, 9, 30)
    evidence = dhc.session_evidence()
    assert evidence(day, "TWSE").status == "unresolved"
    TaiwanDailyStore().write_batch(pl.DataFrame({
        "symbol": ["2330.TWSE", "8069.TPEX"], "date": [day, day], "open": [1.0, 1.0],
        "high": [1.0, 1.0], "low": [1.0, 1.0], "close": [1.0, 1.0], "volume": [1.0, 1.0],
        "amount": [1.0, 1.0], "quote_ts": [None, None],
    }), partition_date=day)
    fresh = dhc.session_evidence()
    assert {fresh(day, ex).status for ex in ("TWSE", "TPEX")} == {"trading"}


def test_quant_upstream_daily_missing_is_downstream_not_run(monkeypatch):
    from app.taiwan.quant import live_store

    monkeypatch.setattr(dhc, "taipei_now", lambda: AFTER_CLOSE)
    monkeypatch.setattr(dhc, "session_evidence", lambda: None)

    def unresolved():
        raise ValueError("session_evidence_unresolved:2026-09-30")

    ledger = SimpleNamespace(current_session=unresolved, latest_operation=lambda: {})
    monkeypatch.setattr(live_store, "LiveLedger", lambda **kwargs: ledger)
    row = DataHealthService()._quant()
    assert row["status"] == "not_run"
    assert "Daily" in row["reason"] and row["data_date"] == TARGET


# ── Realtime ─────────────────────────────────────────────────────────────────


def _quote(stamp, fetched):
    from app.taiwan.enrichment.models import SourceMeta
    from app.taiwan.realtime.models import TaiwanRealtimeQuote

    meta = SourceMeta(source="twse:mis", source_url="", fetched_at=fetched,
                      trade_date=stamp.date(), status="realtime")
    return TaiwanRealtimeQuote(
        symbol="2330.TWSE", name="台積電", exchange="TWSE", last_price=1.0, prev_close=1.0,
        open=1.0, high=1.0, low=1.0, change=0.0, change_pct=0.0, volume=1, amount=1.0,
        quote_time=stamp, trade_date=stamp.date(), market_status="closed", source_meta=meta)


@pytest.mark.parametrize(("now", "stale"), [
    (datetime(2026, 9, 30, 16, 8, tzinfo=TAIPEI_TZ), False),   # after close: last match is latest
    (datetime(2026, 9, 30, 10, 0, tzinfo=TAIPEI_TZ), True),    # in session: 60s MIS threshold
])
def test_realtime_freshness_is_session_aware(monkeypatch, now, stale):
    from app.taiwan.realtime import service as rt

    svc = rt.TaiwanRealtimeService(mis_provider=SimpleNamespace(), yahoo_provider=SimpleNamespace(),
                                   official_close_provider=SimpleNamespace())
    monkeypatch.setattr(rt, "taipei_now", lambda: now)
    close = now.replace(hour=13, minute=30) if not stale else now - timedelta(minutes=5)
    svc._cache[("2330.TWSE", "closed")] = (0.0, _quote(close, now.isoformat()))
    assert svc.health_metadata()[0]["is_stale"] is stale


def test_realtime_health_uses_newest_observation_per_symbol(monkeypatch):
    from app.taiwan.realtime import service as rt

    now = datetime(2026, 9, 30, 16, 8, tzinfo=TAIPEI_TZ)
    svc = rt.TaiwanRealtimeService(mis_provider=SimpleNamespace(), yahoo_provider=SimpleNamespace(),
                                   official_close_provider=SimpleNamespace())
    monkeypatch.setattr(rt, "taipei_now", lambda: now)
    morning = _quote(now.replace(hour=10), "2026-09-30T10:05:00+08:00")
    morning.source_meta = replace(morning.source_meta, is_stale=True)
    svc._cache[("2330.TWSE", "open")] = (0.0, morning)
    svc._cache[("2330.TWSE", "closed")] = (0.0, _quote(now.replace(hour=13, minute=30),
                                                        "2026-09-30T16:08:00+08:00"))
    assert [m["is_stale"] for m in svc.health_metadata()] == [False]


# ── Reporting-cycle datasets (FinMind) ───────────────────────────────────────


def test_financial_reporting_cycle_expected_period():
    assert expected_financial_period(date(2026, 9, 30)) == date(2026, 6, 30)
    assert expected_financial_period(date(2026, 11, 14)) == date(2026, 6, 30)
    assert expected_financial_period(date(2026, 11, 15)) == date(2026, 9, 30)
    assert expected_financial_period(date(2026, 4, 1)) == date(2025, 12, 31)
    assert expected_financial_period(date(2026, 2, 1)) == date(2025, 9, 30)


def test_monthly_revenue_reporting_cycle_expected_date():
    assert expected_revenue_date(date(2026, 9, 30)) == date(2026, 9, 1)   # August revenue
    assert expected_revenue_date(date(2026, 10, 5)) == date(2026, 9, 1)   # Sept not due yet
    assert expected_revenue_date(date(2026, 10, 11)) == date(2026, 10, 1)


def _rec(symbol, status="available", day=None, error=None):
    return {"symbol": symbol, "status": status, "data_date": day,
            "fetched_at": "2026-09-30T07:58:00+00:00", "error_msg": error}


def test_financial_statements_current_quarter_is_current_not_stale():
    status, reason = finmind_policy(
        "financial", [_rec("2330.TWSE", day="2026-06-30")], date(2026, 6, 30), 7 * 86400)
    assert status == "current" and "2026-06-30" in reason


def test_financial_reader_excludes_etfs_as_not_applicable(monkeypatch):
    from app.taiwan.finmind_cache import FinMindCache

    monkeypatch.setattr(dhc, "taipei_now", lambda: AFTER_CLOSE)
    monkeypatch.setattr(dhc, "etf_symbols", lambda: {"0050.TWSE"})
    monkeypatch.setattr(FinMindCache, "health_metadata", lambda self, dataset: [
        _rec("2330.TWSE", day="2026-06-30" if dataset.endswith("Statements") else "2026-09-01"),
        _rec("0050.TWSE", "unavailable", error=f"{dataset} returned no rows"),
        _rec("2330", day=None),  # legacy key without exchange suffix
    ])
    rows = DataHealthService()._finmind()
    assert rows["financial"]["status"] == "current"
    assert rows["monthly_revenue"]["status"] == "current"
    assert "資料源回報失敗" not in rows["financial"]["reason"]


def test_monthly_revenue_behind_due_month_is_stale():
    status, _ = finmind_policy(
        "monthly_revenue", [_rec("2330.TWSE", day="2026-08-01")], date(2026, 9, 1), 86400)
    assert status == "stale"


def test_securities_lending_ttl_stale_and_event_date_is_latest(monkeypatch):
    from app.taiwan.finmind_cache import FinMindCache

    monkeypatch.setattr(dhc, "etf_symbols", lambda: set())
    monkeypatch.setattr(FinMindCache, "health_metadata", lambda self, dataset: [
        _rec("00646.TWSE", "stale", "2026-08-28"), _rec("2330.TWSE", "available", "2026-09-29"),
    ])
    row = DataHealthService()._finmind()["securities_lending"]
    assert row["status"] == "stale" and "TTL" in row["reason"]
    assert row["data_date"] == "2026-09-29"  # last lending trade is an event, not staleness


# ── Benchmark store ──────────────────────────────────────────────────────────


def test_benchmark_refresh_keeps_missing_as_missing_and_derives_change():
    from app.taiwan.benchmark_store import TaiwanBenchmarkStore

    payloads = {
        "twse": [
            {"Date": "1150929", "OpeningIndex": "", "HighestIndex": "48045.13",
             "LowestIndex": "47573.09", "ClosingIndex": "47631.96"},
            {"Date": "1150930", "OpeningIndex": "47700.00", "HighestIndex": "48100.00",
             "LowestIndex": "47600.00", "ClosingIndex": "48000.00"},
        ],
        "tpex": [{"Date": "20260930", "Open": "414.21", "High": "419.03", "Low": "414.21",
                  "Close": "417.07", "Change": "4.82"}],
    }
    store = TaiwanBenchmarkStore()
    stats = store.refresh(fetch_json=lambda url: payloads["twse" if "twse" in url else "tpex"])
    assert stats["failed"] == [] and stats["latest"] == {"TAIEX": "2026-09-30", "TPEX_INDEX": "2026-09-30"}
    rows = store.read().filter(pl.col("symbol") == "TAIEX").sort("date")
    assert rows["open"][0] is None  # missing never becomes 0
    taiex = store.latest("TAIEX")
    assert taiex["change"] == pytest.approx(368.04)
    assert taiex["change_pct"] == pytest.approx(368.04 / 47631.96)
    # First persisted point has no previous official close: no fabricated change.
    assert store.latest("TPEX_INDEX")["change"] is None


def test_benchmark_one_exchange_failure_is_isolated():
    from app.taiwan.benchmark_store import TaiwanBenchmarkStore

    def fetch(url):
        if "twse" in url:
            raise OSError("HTTP 503")
        return [{"Date": "20260930", "Open": "1", "High": "1", "Low": "1", "Close": "417.07"}]

    stats = TaiwanBenchmarkStore().refresh(fetch_json=fetch)
    assert [f["symbol"] for f in stats["failed"]] == ["TAIEX"]
    assert stats["latest"]["TPEX_INDEX"] == "2026-09-30"


def test_market_intelligence_indexes_come_from_persisted_official_benchmark():
    from app.taiwan.benchmark_store import TaiwanBenchmarkStore
    from app.taiwan.market_intelligence import persisted_index_snapshot

    assert persisted_index_snapshot(date(2026, 9, 30)).taiex.status == "unavailable"
    TaiwanBenchmarkStore().write(pl.DataFrame({
        "symbol": ["TAIEX", "TAIEX"], "date": [date(2026, 9, 29), date(2026, 9, 30)],
        "open": [None, None], "high": [None, None], "low": [None, None],
        "close": [100.0, 101.0], "source": ["twse:MI_5MINS_HIST"] * 2, "source_url": ["u"] * 2,
        "retrieved_at": ["x"] * 2,
    }))
    snap = persisted_index_snapshot(date(2026, 9, 30)).taiex
    assert snap.status == "official_close" and snap.close == 101.0
    assert snap.change_pct == pytest.approx(0.01)  # fraction, as the dashboard expects
    assert persisted_index_snapshot(date(2026, 10, 1)).taiex.status == "stale"


# ── Social AI ────────────────────────────────────────────────────────────────


def _run_ai(monkeypatch, generate, rankings=24):
    from app.services import ai_provider
    from app.taiwan.social_sentiment import SocialSentimentService

    monkeypatch.setattr(ai_provider, "ai_configured", lambda: True)
    monkeypatch.setattr(ai_provider, "snapshot_ai_provider_config", lambda: None)
    monkeypatch.setattr(ai_provider, "generate_ai_text", generate)
    rows = [{"code": f"{1000 + i}", "company_name": "x", "_texts": ["t"]} for i in range(rankings)]
    service = SocialSentimentService.__new__(SocialSentimentService)
    return asyncio.run(service._apply_ai(rows, {}))


def test_social_ai_provider_402_stops_batches_and_keeps_safe_status(monkeypatch):
    calls = []

    async def payment_required(messages, **kwargs):
        calls.append(1)
        class APIStatusError(Exception):
            status_code = 402

        raise RuntimeError("Insufficient Balance key=sk-secret") from APIStatusError()

    info = _run_ai(monkeypatch, payment_required)
    assert len(calls) == 1  # not 16 identical billing failures
    assert info["status"] == "unavailable" and info["analyzed_symbols"] == 0
    assert info["errors"][0] == "batch 1: HTTP 402"
    assert "sk-secret" not in str(info)


def test_social_ai_truncation_uses_one_controlled_retry(monkeypatch):
    from app.services.ai_provider import AIOutputTruncated

    calls = []

    async def truncate_then_ok(messages, **kwargs):
        calls.append(kwargs.get("request_attempt"))
        assert kwargs.get("structured_output") is True
        if len(calls) % 2 == 1:
            raise AIOutputTruncated(partial_content='{"items":[')
        return '{"items":[]}'

    info = _run_ai(monkeypatch, truncate_then_ok, rankings=1)
    assert calls == [0, 1]
    assert info["errors"] == []


def test_social_ai_health_reports_provider_failure(monkeypatch):
    from app.taiwan import social_sentiment

    monkeypatch.setattr(social_sentiment, "load_social_sentiment", lambda: {
        "as_of": date.today().isoformat(), "sources": {"ptt": {"status": "available"}},
        "ai": {"status": "unavailable", "batches": 1, "analyzed_symbols": 0,
               "errors": ["batch 1: HTTP 402", "remaining batches skipped after HTTP 402"]},
    })
    row = DataHealthService()._social()["social_ai"]
    assert row["status"] == "provider_error"
    assert "402" in row["reason"] and "額度" in row["reason"] and "0 檔" in row["reason"]


# ── Summary ──────────────────────────────────────────────────────────────────


def test_healthy_count_counts_only_current():
    readers = {key: (lambda: {"status": "awaiting_publication"}) for key in DATASETS}
    readers["daily"] = lambda: {"status": "current"}
    readers["ptt"] = lambda: {"status": "not_run"}
    report = DataHealthService(readers).snapshot()
    assert (report.current_count, report.total_count) == (1, len(DATASETS))


def test_market_intelligence_fallback_keeps_true_target_and_stale_status(tmp_path, monkeypatch):
    from app.taiwan import market_intelligence
    from app.taiwan.daily_store import TaiwanDailyStore
    from app.taiwan.market_intelligence import TaiwanMarketIntelligenceService

    day = date(2026, 9, 29)
    store = TaiwanDailyStore(data_dir=tmp_path / "daily")
    store.write_batch(pl.DataFrame({
        "symbol": ["2330.TWSE"], "date": [day], "open": [1.0], "high": [1.0], "low": [1.0],
        "close": [1.0], "volume": [1.0], "amount": [1.0], "quote_ts": [None],
    }), partition_date=day)
    monkeypatch.setattr(market_intelligence, "resolve_target_latest_trading_date",
                        lambda *a, **k: date(2026, 9, 30))
    svc = TaiwanMarketIntelligenceService(daily_store=store)
    snap = svc.get_snapshot()
    assert snap.trade_date == "2026-09-29"                      # figures: latest available day
    assert snap.data_quality.target_trade_date == "2026-09-30"  # reference: the real target
    assert snap.data_quality.daily.status == "stale"


def test_utc_timestamps_are_normalized():
    assert dhc.timestamp("2026-09-30T16:30:00+08:00") == datetime(2026, 9, 30, 8, 30, tzinfo=UTC).isoformat()


def test_dcard_httpx_403_and_legacy_zero_analyzed_ai(monkeypatch):
    from app.taiwan import social_sentiment

    monkeypatch.setattr(social_sentiment, "load_social_sentiment", lambda: {
        "as_of": date.today().isoformat(),
        "sources": {"dcard": {"status": "unavailable", "errors": [
            "forum stock: HTTPStatusError: Client error '403 Forbidden' for url 'https://x/?t=1'"]}},
        # Snapshot written before safe HTTP codes were kept: all batches failed.
        "ai": {"status": "degraded", "batches": 16, "analyzed_symbols": 0,
               "errors": ["batch 1: RuntimeError"]},
    })
    rows = DataHealthService()._social()
    assert rows["dcard"]["status"] == "provider_error" and "HTTP 403" in rows["dcard"]["reason"]
    assert "https" not in rows["dcard"]["reason"]
    assert rows["social_ai"]["status"] == "unavailable" and "0 檔" in rows["social_ai"]["reason"]


def test_selection_outcome_reason_names_the_blocking_evidence(monkeypatch):
    from app.taiwan import selection_review_service

    monkeypatch.setattr(selection_review_service, "get_selection_review_service", lambda: SimpleNamespace(
        health_metadata=lambda: {
            "selection_snapshot": {"status": "current", "reason": "ok"},
            "selection_outcome": {"status": "unavailable", "reason": "已到期 horizon 缺少價格或基準資料",
                                  "reason_codes": ["corporate_action_coverage_unavailable"]},
        }))
    row = DataHealthService()._selection()["selection_outcome"]
    assert "公司行動" in row["reason"] and "reason_codes" not in row


def test_social_ai_only_analyses_hottest_symbols(monkeypatch):
    import json as _json

    from app.taiwan.social_sentiment import AI_SENTIMENT_MAX_SYMBOLS

    seen = []

    async def ok(messages, **kwargs):
        data = _json.loads(messages[-1]["content"].split("資料：", 1)[1])
        seen.extend(item["symbol"] for item in data)
        return _json.dumps({"items": [
            {"symbol": item["symbol"], "sentiment": "neutral", "score": 0.0, "confidence": 0.5,
             "bullish_count": 0, "neutral_count": 1, "bearish_count": 0, "reason": "r"}
            for item in data]})

    info = _run_ai(monkeypatch, ok, rankings=AI_SENTIMENT_MAX_SYMBOLS + 5)
    assert seen == [f"{1000 + i}" for i in range(AI_SENTIMENT_MAX_SYMBOLS)]
    assert info["analyzed_symbols"] == AI_SENTIMENT_MAX_SYMBOLS
    assert info["skipped_symbols"] == 5 and info["status"] == "available"

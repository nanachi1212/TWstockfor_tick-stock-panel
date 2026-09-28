"""Official monthly-revenue PIT evidence (MOPS t21sc03 observations)."""
# ruff: noqa: RUF001 -- fixtures mirror official full-width page text.
from __future__ import annotations

import inspect
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import httpx
import polars as pl
import pytest

from app.taiwan import selection_v2
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.finmind_cache import FinMindCache
from app.taiwan.monthly_revenue_evidence import (
    MonthlyRevenueEvidenceStore,
    RevenuePageSchemaError,
    decode_page,
    expected_page_keys,
    page_url,
    parse_revenue_page,
    refresh_monthly_revenue_evidence,
    window_periods,
)
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.screener import TaiwanScreenerRequest, TaiwanScreenerService
from app.taiwan.selection_v2 import apply_strategy, rank_strategy, strategy_readiness

T0 = datetime(2026, 9, 22, 18, 0, tzinfo=TAIPEI)
CUTOFF = datetime(2026, 9, 25, 9, 0, tzinfo=TAIPEI)


def _page(period: str, rows: list[tuple[str, str, str, str]], *, report="115/09/20") -> str:
    year, month = int(period[:4]) - 1911, int(period[5:7])
    body = "".join(
        f"<tr><td>{code}</td><td>{name}</td><td>{cur}</td><td>{prev}</td><td>1</td>"
        f"<td>0.1</td><td>0.2</td><td>9</td><td>8</td><td>0.3</td><td>-</td></tr>"
        for code, name, cur, prev in rows
    )
    return (
        f"<html><body><b>上市公司{year}年{month}月份(累計與當月)營業收入統計表</b>"
        f"<table><tr><td>出表日期：{report}</td></tr>"
        "<tr><th>公司代號</th><th>公司名稱</th><th>當月營收</th><th>上月營收</th>"
        "<th>去年當月營收</th><th>上月比較增減(%)</th><th>去年同月增減(%)</th>"
        "<th>當月累計營收</th><th>去年累計營收</th><th>前期比較增減(%)</th><th>備註</th></tr>"
        f"{body}</table></body></html>"
    )


# 2330: Aug YoY +20%, Jul YoY +10% -> improving.  2454: YoY negative.
PAGES = {
    "2026-08": [("2330", "台積電", "1,200", "1,100"), ("2454", "聯發科", "800", "900")],
    "2026-07": [("2330", "台積電", "1,100", "1,000"), ("2454", "聯發科", "900", "950")],
    "2025-08": [("2330", "台積電", "1,000", "1,000"), ("2454", "聯發科", "1,000", "1,000")],
    "2025-07": [("2330", "台積電", "1,000", "1,000"), ("2454", "聯發科", "1,000", "1,000")],
}


def _record(store, period, rows, at, *, market="TWSE", kind="0", run_id="r1"):
    raw = _page(period, rows).encode("utf-8")
    page = parse_revenue_page(decode_page(raw), period=period)
    return store.record_fetch(
        run_id=run_id, market=market, kind=kind, period=period,
        retrieved_at=at, status=page.status, raw=raw, page=page,
    )


FILLER = [("9999", "填充", "1", "1")]


def _seed(store, at=T0, pages=PAGES, run_id="r1", extra=None):
    """Record the complete expected page set for CUTOFF (24 pages)."""
    extra = extra or {}
    for market, kind, period in expected_page_keys(CUTOFF):
        rows = extra.get((market, kind, period))
        if rows is None:
            rows = pages.get(period, FILLER) if (market, kind) == ("TWSE", "0") else FILLER
        _record(store, period, rows, at, market=market, kind=kind, run_id=run_id)


# ── parsing ──────────────────────────────────────────────────────────────


def test_parser_keeps_units_codes_and_null_not_zero():
    page = parse_revenue_page(
        _page("2026-08", [("2330", "台積電", "514,805,337", "-"), ("1256", "鮮活果汁-KY", "", "12")]),
        period="2026-08",
    )
    rows = {row.raw_code: row for row in page.rows}
    assert page.report_date == "2026-09-20"
    assert rows["2330"].revenue_thousand_twd == 514_805_337
    assert rows["2330"].previous_month_thousand_twd is None
    assert rows["1256"].revenue_thousand_twd is None  # blank is missing, not 0


def test_parser_rejects_wrong_period_and_reports_unpublished_month():
    with pytest.raises(RevenuePageSchemaError):
        parse_revenue_page(_page("2026-07", [("2330", "台積電", "1", "1")]), period="2026-08")
    empty = parse_revenue_page(
        "<b>上市公司115年9月份(累計與當月)營業收入統計表</b> 查無資料", period="2026-09"
    )
    assert empty.status == "not_published" and empty.rows == ()


def test_urls_are_board_and_issuer_kind_specific():
    assert page_url("TWSE", "0", "2026-08").endswith("/sii/t21sc03_115_8_0.html")
    assert page_url("TPEX", "1", "2025-12").endswith("/otc/t21sc03_114_12_1.html")
    assert window_periods(date(2026, 9, 28)) == [
        "2026-08", "2026-07", "2026-06", "2025-08", "2025-07", "2025-06",
    ]


# ── PIT semantics ────────────────────────────────────────────────────────


def test_observed_before_cutoff_is_usable_after_is_not(tmp_path):
    store = MonthlyRevenueEvidenceStore(tmp_path)
    assert store.evidence_as_of(CUTOFF).status == "missing"
    _seed(store, at=CUTOFF)  # equality is not "before"
    late = store.evidence_as_of(CUTOFF)
    assert late.status == "not_observed_before_cutoff"
    assert late.rows_by_symbol == {}
    assert late.first_observed_at == CUTOFF.isoformat()

    _seed(store, at=T0, run_id="r0")
    usable = store.evidence_as_of(CUTOFF)
    assert usable.status == "available"
    latest = usable.rows_by_symbol["2330.TWSE"][-1]
    assert latest["revenue"] == 1_200_000  # thousand TWD -> TWD
    assert latest["evidence_observed_at"] == T0.isoformat()
    assert latest["source_url"].endswith("/sii/t21sc03_115_8_0.html")


def test_stale_observations_do_not_count_as_current(tmp_path):
    store = MonthlyRevenueEvidenceStore(tmp_path)
    _seed(store, at=CUTOFF - timedelta(days=10))
    stale = store.evidence_as_of(CUTOFF)
    assert stale.status == "stale" and stale.rows_by_symbol == {}


def test_correction_is_visible_only_after_its_observation(tmp_path):
    store = MonthlyRevenueEvidenceStore(tmp_path)
    _seed(store)
    corrected_at = T0 + timedelta(days=2)
    _record(store, "2026-08", [("2330", "台積電", "1,300", "1,100")], corrected_at, run_id="r2")

    before = store.evidence_as_of(corrected_at)
    assert before.rows_by_symbol["2330.TWSE"][-1]["revenue_thousand_twd"] == 1_200
    assert before.rows_by_symbol["2330.TWSE"][-1]["revision_seq"] == 1

    after = store.evidence_as_of(CUTOFF)
    row = after.rows_by_symbol["2330.TWSE"][-1]
    assert row["revenue_thousand_twd"] == 1_300
    assert row["revision_seq"] == 2
    assert row["revision_status"] == "changed_after_first_observation"
    assert row["evidence_observed_at"] == corrected_at.isoformat()
    assert before.digest != after.digest


def test_historical_revision_chronology_is_append_only(tmp_path):
    store = MonthlyRevenueEvidenceStore(tmp_path)
    _seed(store)
    _record(store, "2026-08", [("2330", "台積電", "1,200", "1")], T0 + timedelta(days=1), run_id="r2")
    _record(store, "2026-08", [("2330", "台積電", "1,250", "1")], T0 + timedelta(days=2), run_id="r3")
    history = store.revisions("2330.TWSE", "2026-08")
    assert [(r["revision_seq"], r["revenue_thousand_twd"]) for r in history] == [(1, 1200), (2, 1250)]
    assert history[0]["last_observed_at"] == (T0 + timedelta(days=1)).isoformat()
    # Querying a past cutoff still yields the value visible then.
    assert store.evidence_as_of(T0 + timedelta(days=1, hours=1)).rows_by_symbol[
        "2330.TWSE"][-1]["revenue_thousand_twd"] == 1200


def test_same_refresh_official_mismatch_fails_closed(tmp_path):
    store = MonthlyRevenueEvidenceStore(tmp_path)
    pages = dict(PAGES)
    # August page states July = 1,111 while the July page says 1,100.
    pages["2026-08"] = [("2330", "台積電", "1,200", "1,111"), ("2454", "聯發科", "800", "900")]
    _seed(store, pages=pages)
    evidence = store.evidence_as_of(CUTOFF)
    assert evidence.mismatches == {"2330.TWSE": ["2026-07"]}
    assert all(r["date"] != "2026-07-01" for r in evidence.rows_by_symbol["2330.TWSE"])

    # Across refreshes, a difference is an ordinary later correction.
    other = MonthlyRevenueEvidenceStore(tmp_path / "other")
    _seed(other)
    _record(other, "2026-08", [("2330", "台積電", "1,200", "1,111")], T0 + timedelta(hours=1), run_id="r2")
    assert other.evidence_as_of(CUTOFF).mismatches == {}


def test_canonical_symbols_follow_board_not_code_shape(tmp_path):
    store = MonthlyRevenueEvidenceStore(tmp_path)
    _seed(store, extra={
        ("TPEX", "0", "2026-08"): [("6488", "環球晶", "10", "9")],
        ("TWSE", "1", "2026-08"): [("1256", "鮮活果汁-KY", "5", "4")],
    })
    evidence = store.evidence_as_of(CUTOFF)
    assert {"6488.TPEX", "1256.TWSE", "2330.TWSE", "9999.TPEX"} <= set(evidence.rows_by_symbol)
    assert "6488.TWSE" not in evidence.rows_by_symbol and "1256.TPEX" not in evidence.rows_by_symbol


def test_partial_page_set_is_incomplete_not_available(tmp_path):
    store = MonthlyRevenueEvidenceStore(tmp_path)
    for period, rows in PAGES.items():  # only one board / issuer kind
        _record(store, period, rows, T0)
    evidence = store.evidence_as_of(CUTOFF)
    assert evidence.status == "incomplete"
    assert evidence.rows_by_symbol == {}
    assert len(evidence.missing_pages) == 20
    assert "TPEX/0/2026-08" in evidence.missing_pages
    _readiness, reasons, _ = strategy_readiness(
        pl.DataFrame({"revenue_status": ["evidence_incomplete"]}), "growth_trend_v1",
        quote_coverage_status="verified", risk_source_status="available",
        revenue_evidence_status=evidence.status,
    )
    assert reasons == ["月營收官方 evidence 不完整: 缺少應觀測頁面"]


def test_evidence_resolution_is_deterministic(tmp_path):
    store = MonthlyRevenueEvidenceStore(tmp_path)
    _seed(store)
    first, second = store.evidence_as_of(CUTOFF), store.evidence_as_of(CUTOFF)
    assert first.digest == second.digest
    assert first.rows_by_symbol == second.rows_by_symbol


# ── refresh ──────────────────────────────────────────────────────────────


class _Client:
    def __init__(self, fail: set[str] = frozenset()):
        self.urls: list[str] = []
        self.fail = fail

    def get(self, url):
        self.urls.append(url)
        if url in self.fail:
            raise httpx.ConnectError("offline")
        name = url.rsplit("/", 1)[-1]
        roc_year, month = name.split("_")[1:3]
        period = f"{int(roc_year) + 1911:04d}-{int(month):02d}"
        html = _page(period, PAGES.get(period, [("2330", "台積電", "1", "1")]))
        return httpx.Response(200, content=html.encode("utf-8"), request=httpx.Request("GET", url))


def test_refresh_is_market_batched_incremental_and_records_failures(tmp_path):
    store = MonthlyRevenueEvidenceStore(tmp_path)
    now = datetime(2026, 9, 21, 20, 0, tzinfo=TAIPEI)
    failing = page_url("TPEX", "1", "2026-08")
    client = _Client(fail={failing})
    summary = refresh_monthly_revenue_evidence(store, now=now, client=client)
    assert len(client.urls) == 2 * 2 * 6  # boards x issuer kinds x window months
    assert summary["failed"] == 1 and summary["status"] == "partial"
    failed = [r for r in store.fetches() if r["status"] == "error"]
    assert [r["source_url"] for r in failed] == [failing]

    again = _Client()
    second = refresh_monthly_revenue_evidence(store, now=now + timedelta(hours=1), client=again)
    assert again.urls == [failing]  # only the page without a fresh success
    assert second["skipped_fresh"] == 23 and second["status"] == "available"


# ── strategy definitions and screener integration ───────────────────────


def test_strategy_definitions_unchanged():
    assert selection_v2.STRATEGY_IDS == (
        "trend_liquidity_v1", "institutional_momentum_v1", "growth_trend_v1",
        "breakout_v1", "multi_factor_consensus_v1",
    )
    assert (selection_v2.V2_MIN_AMOUNT_TWD, selection_v2.INSTITUTIONAL_FLOW_RATIO_MIN,
            selection_v2.BREAKOUT_VOLUME_RATIO_MIN, selection_v2.CONSENSUS_MIN_HITS) == (
        50_000_000, 0.01, 1.2, 2)
    assert {k: v["version"] for k, v in selection_v2._METADATA.items()} == dict.fromkeys(
        selection_v2.STRATEGY_IDS, "v1")
    growth_source = inspect.getsource(apply_strategy)
    for clause in (
        '(pl.col("revenue_yoy") > 0)', '(pl.col("revenue_yoy_improving") == True)',
        '(pl.col("close") > pl.col("ma60"))', '(pl.col("momentum_20d") > 0)',
    ):
        assert clause in growth_source
    assert '["revenue_yoy", "revenue_yoy_improvement", "momentum_20d", "momentum_5d", "amount"]' in (
        inspect.getsource(rank_strategy))


def test_readiness_names_the_revenue_evidence_problem():
    frame = pl.DataFrame({"revenue_status": ["publication_unknown"]})
    readiness, reasons, _ = strategy_readiness(
        frame, "growth_trend_v1", quote_coverage_status="verified", risk_source_status="available",
        revenue_evidence_status="not_observed_before_cutoff",
    )
    assert readiness == "degraded"
    assert reasons == ["月營收官方公告時間無法證明早於選股 cutoff"]
    for status, text in (("missing", "evidence 缺失"), ("stale", "已過期")):
        _r, reasons, _ = strategy_readiness(
            frame, "growth_trend_v1", quote_coverage_status="verified",
            risk_source_status="available", revenue_evidence_status=status,
        )
        assert text in reasons[0]
    _r, reasons, _ = strategy_readiness(
        pl.DataFrame({"revenue_status": ["available"]}), "growth_trend_v1",
        quote_coverage_status="verified", risk_source_status="available",
        revenue_evidence_status="available", revenue_mismatch_count=2,
    )
    assert reasons == ["月營收官方來源數值不一致 2 檔"]


def _screener(tmp_path, evidence_store, *, symbols=("2330.TWSE", "2454.TWSE")):
    universe = pl.DataFrame({
        "symbol": list(symbols), "name": [s[:4] for s in symbols],
        "exchange": ["TWSE"] * len(symbols), "instrument_type": ["stock"] * len(symbols),
        "listing_status": ["active"] * len(symbols), "industry": ["半導體業"] * len(symbols),
    })
    master = MagicMock()
    master.to_dataframe.return_value = universe
    master.get_instrument.return_value = None
    daily = TaiwanDailyStore(tmp_path / "daily")
    day, sessions = date(2026, 6, 1), []
    while len(sessions) < 70:
        if day.weekday() < 5:
            sessions.append(day)
        day += timedelta(days=1)
    daily.write_batch(pl.DataFrame([{
        "symbol": symbol, "date": session, "open": 100.0 + i, "high": 101.0 + i,
        "low": 99.0 + i, "close": 100.0 + i, "volume": 1_000_000.0,
        "amount": 90_000_000.0, "quote_ts": 0,
    } for i, session in enumerate(sessions) for symbol in symbols]))
    empty_store = MagicMock()
    empty_store.read_latest_per_symbol.return_value = pl.DataFrame()
    empty_store.read_range.return_value = pl.DataFrame()
    calendar = MagicMock()
    calendar.next_potential_session.return_value = date(2026, 9, 25)
    fm_cache = FinMindCache(tmp_path / "finmind")
    # A conflicting FinMind cache must not influence the official evidence path.
    fm_cache.set("TaiwanStockMonthRevenue", "2454.TWSE", [
        {"date": "2025-08-01", "revenue": 1, "revenue_year": 2025, "revenue_month": 8},
        {"date": "2026-08-01", "revenue": 999_999_999, "revenue_year": 2026, "revenue_month": 8},
    ])
    return TaiwanScreenerService(
        security_master=master, daily_store=daily, institutional_store=empty_store,
        margin_store=empty_store, finmind_cache=fm_cache, calendar=calendar,
        census_store=MagicMock(), revenue_evidence_store=evidence_store,
    ), sessions[-1]


@pytest.fixture
def _no_network(monkeypatch):
    class Risk:
        def get_cached_regulatory_snapshot(self):
            return [], "available", T0.isoformat()

        def check_symbol_risk_status(self, symbol, target_date=None, events=None):
            return {"is_disposition": False, "is_suspended": False}

    monkeypatch.setattr("app.taiwan.events_service.get_event_service", lambda: Risk())

    def forbidden(*_args, **_kwargs):
        pytest.fail("screener must not perform HTTP")

    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)


def test_growth_run_uses_official_evidence_without_http(tmp_path, _no_network):
    store = MonthlyRevenueEvidenceStore(tmp_path / "evidence")
    _seed(store)
    service, _last = _screener(tmp_path, store)
    first = service.run(TaiwanScreenerRequest(preset="growth_trend_v1"))
    second = service.run(TaiwanScreenerRequest(preset="growth_trend_v1"))
    assert first.strategy_readiness == "ready", first.strategy_readiness_reasons
    assert [item.symbol for item in first.items] == ["2330.TWSE"]
    item = first.items[0]
    assert item.revenue_yoy == 20.0 and item.revenue_yoy_improving is True
    assert first.revenue_evidence["cutoff"] == CUTOFF.isoformat()
    assert first.revenue_evidence["latest_period"] == "2026-08"
    assert first.revenue_evidence["revenue_available_count"] == 2
    # 2454's official YoY is negative even though its FinMind cache would not be.
    assert first.model_dump() == second.model_dump()


def test_available_evidence_with_zero_candidates_is_ready(tmp_path, _no_network):
    store = MonthlyRevenueEvidenceStore(tmp_path / "evidence")
    _seed(store)
    service, _ = _screener(tmp_path, store, symbols=("2454.TWSE",))
    response = service.run(TaiwanScreenerRequest(preset="growth_trend_v1"))
    assert response.strategy_readiness == "ready"
    assert response.total == 0 and response.items == []


def test_evidence_observed_after_cutoff_keeps_growth_degraded(tmp_path, _no_network):
    store = MonthlyRevenueEvidenceStore(tmp_path / "evidence")
    _seed(store, at=CUTOFF + timedelta(days=3))
    service, _ = _screener(tmp_path, store)
    response = service.run(TaiwanScreenerRequest(preset="growth_trend_v1"))
    assert response.strategy_readiness == "degraded"
    assert "月營收官方公告時間無法證明早於選股 cutoff" in response.strategy_readiness_reasons
    assert response.items == []
    assert response.revenue_evidence["status_counts"] == {"publication_unknown": 2}

    consensus = service.run(TaiwanScreenerRequest(preset="multi_factor_consensus_v1"))
    assert consensus.strategy_readiness == "degraded"
    assert "月營收官方公告時間無法證明早於選股 cutoff" in consensus.strategy_readiness_reasons


def test_refresh_is_picked_up_without_restart(tmp_path, _no_network):
    store = MonthlyRevenueEvidenceStore(tmp_path / "evidence")
    service, _ = _screener(tmp_path, store)
    assert service.run(TaiwanScreenerRequest(preset="growth_trend_v1")).strategy_readiness == "degraded"
    _seed(store)
    assert service.run(TaiwanScreenerRequest(preset="growth_trend_v1")).strategy_readiness == "ready"

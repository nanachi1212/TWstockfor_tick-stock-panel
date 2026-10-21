"""Fixed samples for the 資金熱浪 heatmap: turnover shares, weighted change, missing data and census coverage."""
from datetime import date, timedelta
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.market_heatmap import TaiwanMarketHeatmapService, session_changes
from app.taiwan.observed_universe import ObservedUniverseStore
from app.taiwan.realtime.calendar import TaiwanTradingCalendar

D1, D2, D3 = date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)
MASTER = pl.DataFrame({
    "symbol": ["2330.TWSE", "2303.TWSE", "2882.TWSE", "0050.TWSE", "2454.TWSE", "6666.TPEX", "1101.TWSE", "1102.TWSE"],
    "name": ["台積電", "聯電", "國泰金", "元大台灣50", "聯發科", "新股", "台泥", "亞泥"],
    "industry": ["半導體業", "半導體業", "金融保險業", None, "半導體業", " ", "水泥工業", "水泥工業"],
    "instrument_type": ["stock", "stock", "stock", "etf", "stock", "stock", "stock", "stock"],
    "listing_status": ["active"] * 8,
})
# symbol -> (D1 close, D1 amount, D2 close, D2 amount); amounts in TWD.
BASE = {
    "2330.TWSE": (100.0, 5000.0, 103.0, 6000.0),
    "2303.TWSE": (50.0, 1000.0, 49.0, 2000.0),
    "2882.TWSE": (40.0, 1000.0, 40.0, 1000.0),
    "0050.TWSE": (200.0, 1000.0, 202.0, 1000.0),
}


def _row(symbol, day, close, amount):
    return {"symbol": symbol, "date": day, "open": close, "high": close, "low": close, "close": close,
            "volume": 10.0, "amount": amount, "quote_ts": None}


def _rows(prices):
    return [r for s, (c1, a1, c2, a2) in prices.items() for r in (_row(s, D1, c1, a1), _row(s, D2, c2, a2))]


def write_census(store, rows, extra=None):
    """Census of every (exchange, day) in ``rows``: the codes with a daily row, a non-universe code
    9999 (so an exchange without universe rows is still an observed session) and ``extra[(exchange, day)]``."""
    extra = extra or {}
    for day in {r["date"] for r in rows}:
        for exchange in ("TWSE", "TPEX"):
            codes = {r["symbol"].split(".")[0] for r in rows if r["date"] == day and r["symbol"].endswith(f".{exchange}")}
            codes |= {"9999", *extra.get((exchange, day), ())}
            store.write(exchange, day, [{"date": day, "raw_code": code, "exchange": exchange, "observed": True}
                                        for code in sorted(codes)])
    return store


def _service(tmp_path, rows, calendar=None, census_extra=None):
    daily = TaiwanDailyStore(tmp_path / "daily")
    daily.write_batch(pl.DataFrame(rows, schema_overrides={"amount": pl.Float64, "quote_ts": pl.Int64}))
    master = MagicMock()
    master.to_dataframe.return_value = MASTER
    census = write_census(ObservedUniverseStore(tmp_path / "census"), rows, census_extra)
    return TaiwanMarketHeatmapService(daily, calendar or TaiwanTradingCalendar(known_trading_days={D1, D2}),
                                      master, census)


def _by_industry(snap):
    return {row["industry"]: row for row in snap["industries"]}


def test_shares_weighted_change_order_and_breadth(tmp_path):
    snap = _service(tmp_path, _rows(BASE)).snapshot(D2)
    assert (snap["date"], snap["previous_date"], snap["status"], snap["reason"]) == ("2026-10-20", "2026-10-19", "available", None)
    assert snap["total_amount"] == 10000.0
    assert snap["previous_total_amount"] == 8000.0
    assert (snap["advance"], snap["decline"], snap["flat"], snap["uncompared"]) == (2, 1, 1, 0)
    assert [row["industry"] for row in snap["industries"]] == ["半導體業", "ETF", "金融保險業"]
    semis, etf, finance = (_by_industry(snap)[k] for k in ("半導體業", "ETF", "金融保險業"))
    assert (semis["amount"], semis["share"]) == (8000.0, 0.8)
    assert semis["change_pct"] == pytest.approx((0.03 * 6000 - 0.02 * 2000) / 8000)
    assert (semis["advance"], semis["decline"], semis["flat"], semis["count"]) == (1, 1, 0, 2)
    assert [s["symbol"] for s in semis["stocks"]] == ["2330.TWSE", "2303.TWSE"]
    assert semis["stocks"][0] == {"symbol": "2330.TWSE", "name": "台積電", "close": 103.0,
                                  "change_pct": pytest.approx(0.03), "amount": 6000.0, "volume": 10.0}
    assert (etf["share"], etf["change_pct"], etf["advance"]) == (0.1, pytest.approx(0.01), 1)
    assert (finance["change_pct"], finance["flat"]) == (0.0, 1)
    assert sum(row["share"] for row in snap["industries"]) == pytest.approx(1.0)
    # The census code 9999 is not in the universe, so it is neither expected nor missing.
    assert snap["coverage"] == {
        "status": "complete", "previous_status": "complete", "missing_symbols": [], "missing_count": 0,
        "exchanges": {"TWSE": {"census": "observed", "expected": 4, "observed": 4, "missing": 0},
                      "TPEX": {"census": "observed", "expected": 0, "observed": 0, "missing": 0}},
    }


def test_census_listed_stock_without_daily_row_makes_the_day_partial(tmp_path):
    snap = _service(tmp_path, _rows(BASE), census_extra={("TWSE", D2): ["2454"]}).snapshot(D2)
    assert (snap["status"], snap["reason"]) == ("partial", "incomplete_daily_coverage")
    coverage = snap["coverage"]
    assert (coverage["status"], coverage["missing_count"], coverage["missing_symbols"]) == ("partial", 1, ["2454.TWSE"])
    assert coverage["exchanges"]["TWSE"] == {"census": "observed", "expected": 5, "observed": 4, "missing": 1}
    # The numbers are still the rows that exist, reported as a subset rather than hidden.
    assert snap["total_amount"] == 10000.0
    assert sum(row["share"] for row in snap["industries"]) == pytest.approx(1.0)


@pytest.mark.parametrize("tpex_census", ["empty_unknown", "missing"])
def test_unproven_census_makes_the_day_partial_not_available(tmp_path, tpex_census):
    service = _service(tmp_path, _rows(BASE))
    path = service.evidence_store.partition_path("TPEX", D2)
    if tpex_census == "missing":
        path.unlink()
    else:
        service.evidence_store.write("TPEX", D2, [])
    snap = service.snapshot(D2)
    assert (snap["status"], snap["reason"], snap["coverage"]["status"]) == ("partial", "census_unknown", "unknown")
    assert snap["coverage"]["exchanges"]["TPEX"] == {"census": tpex_census, "expected": None, "observed": 0, "missing": None}
    assert snap["coverage"]["exchanges"]["TWSE"]["missing"] == 0
    assert snap["total_amount"] == 10000.0


def test_incomplete_previous_session_has_no_previous_total(tmp_path):
    snap = _service(tmp_path, _rows(BASE), census_extra={("TWSE", D1): ["2454"]}).snapshot(D2)
    assert (snap["status"], snap["coverage"]["status"], snap["coverage"]["previous_status"]) == ("available", "complete", "partial")
    assert snap["previous_total_amount"] is None
    # Per-stock changes only need each stock's own previous row.
    assert snap["advance"] == 2


def test_missing_previous_row_is_uncompared_not_zero(tmp_path):
    rows = [*_rows(BASE), _row("2454.TWSE", D2, 1000.0, 4000.0), _row("6666.TPEX", D2, 10.0, 500.0)]
    snap = _service(tmp_path, rows).snapshot(D2)
    semis, unclassified = _by_industry(snap)["半導體業"], _by_industry(snap)["UNCLASSIFIED"]
    assert snap["uncompared"] == 2
    assert semis["amount"] == 12000.0
    assert semis["change_pct"] == pytest.approx((0.03 * 6000 - 0.02 * 2000) / 8000)
    assert [s["symbol"] for s in semis["stocks"]] == ["2330.TWSE", "2454.TWSE", "2303.TWSE"]
    assert semis["stocks"][1]["change_pct"] is None
    assert unclassified["change_pct"] is None
    assert (unclassified["advance"], unclassified["decline"], unclassified["flat"], unclassified["count"]) == (0, 0, 0, 1)


def test_untraded_stocks_are_excluded_everywhere(tmp_path):
    rows = [*_rows(BASE), _row("1101.TWSE", D1, 29.0, 100.0), _row("1101.TWSE", D2, 30.0, 0.0),
            _row("1102.TWSE", D1, 29.0, 100.0), _row("1102.TWSE", D2, 30.0, None)]
    snap = _service(tmp_path, rows).snapshot(D2)
    assert "水泥工業" not in _by_industry(snap)
    assert snap["total_amount"] == 10000.0
    assert snap["previous_total_amount"] == 8200.0
    assert (snap["advance"], snap["decline"], snap["flat"], snap["uncompared"]) == (2, 1, 1, 0)


def test_target_without_partition_is_unavailable_and_does_not_slide(tmp_path):
    snap = _service(tmp_path, _rows(BASE)).snapshot(D3)
    assert snap["date"] == "2026-10-21"
    assert (snap["status"], snap["reason"], snap["industries"], snap["total_amount"]) == ("unavailable", "no_daily_data", [], 0.0)
    assert snap["coverage"] is None


def test_single_session_has_no_previous_or_changes(tmp_path):
    rows = [r for r in _rows(BASE) if r["date"] == D2]
    holidays = {D2 - timedelta(days=n) for n in range(1, 60)}
    snap = _service(tmp_path, rows, TaiwanTradingCalendar(known_trading_days={D2}, known_holidays=holidays)).snapshot(D2)
    assert (snap["previous_date"], snap["previous_total_amount"]) == (None, None)
    assert (snap["advance"], snap["decline"], snap["flat"], snap["uncompared"]) == (0, 0, 0, 4)
    assert all(row["change_pct"] is None for row in snap["industries"])
    assert all(s["change_pct"] is None for row in snap["industries"] for s in row["stocks"])

    # An expected previous session without a partition is named but contributes no amount.
    snap = _service(tmp_path / "gap", rows).snapshot(D2)
    assert (snap["previous_date"], snap["previous_total_amount"], snap["uncompared"]) == ("2026-10-19", None, 4)
    assert snap["coverage"]["previous_status"] == "unavailable"


def test_session_changes_uses_only_the_immediately_previous_session(tmp_path):
    daily = TaiwanDailyStore(tmp_path / "daily")
    daily.write_batch(pl.DataFrame([
        _row("A", D1, 10.0, 1.0), _row("A", D3, 12.0, 1.0),
        _row("B", D1, 10.0, 1.0), _row("B", D2, 11.0, 1.0), _row("B", D3, 9.9, 1.0),
        _row("C", D1, 0.0, 1.0), _row("C", D2, 10.0, 1.0),
    ], schema_overrides={"quote_ts": pl.Int64}))
    frame = session_changes(daily, ["A", "B", "C"], [D1, D2, D3])
    assert frame.columns == ["symbol", "date", "close", "volume", "amount", "change_pct"]
    changes = {(r["symbol"], r["date"]): r["change_pct"] for r in frame.to_dicts()}
    assert changes[("A", D1)] is None
    assert changes[("A", D3)] is None
    assert changes[("B", D2)] == pytest.approx(0.1)
    assert changes[("B", D3)] == pytest.approx(-0.1)
    assert changes[("C", D2)] is None


def test_api_passes_date_and_validates_it(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import market_research
    service = _service(tmp_path, _rows(BASE))
    monkeypatch.setattr(market_research, "TaiwanMarketHeatmapService", lambda: service)
    app = FastAPI()
    app.include_router(market_research.router, prefix="/api/taiwan")
    client = TestClient(app)
    response = client.get("/api/taiwan/market-heatmap?date=2026-10-20")
    assert response.status_code == 200
    assert response.json()["industries"][0]["stocks"][0]["symbol"] == "2330.TWSE"
    assert client.get("/api/taiwan/market-heatmap?date=bad").status_code == 422

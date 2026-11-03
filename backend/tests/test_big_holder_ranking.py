from datetime import date
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.taiwan.big_holder_ranking import TaiwanBigHolderRankingService
from app.taiwan.shareholding_store import TaiwanShareholdingStore

LATEST, W1, W4 = date(2026, 10, 16), date(2026, 10, 8), date(2026, 9, 18)
# symbol -> {week: (big_holder_pct, total_holders)}
HISTORY = {
    "1101.TWSE": {W4: (50.0, 10_000), W1: (50.5, 9_900), LATEST: (50.8, 9_800)},
    "2002.TWSE": {W4: (40.0, 10_000), W1: (39.5, 10_100), LATEST: (39.1, 10_300)},
    "2330.TWSE": {W4: (80.0, 10_000), W1: (80.0, 10_000), LATEST: (80.1, 10_000)},
    "3008.TWSE": {W4: (30.0, 900), W1: (32.0, 900), LATEST: (35.0, 900)},
    "0050.TWSE": {W4: (30.0, 50_000), W1: (32.0, 50_000), LATEST: (35.0, 50_000)},
    "8069.TPEX": {W4: (60.0, 5_000), LATEST: (60.6, 5_050)},
    "6488.TPEX": {LATEST: (70.0, 2_000)},
    # Share reissue: one custody holder in the base weeks is not a comparable distribution.
    "2323.TWSE": {W4: (100.0, 1), W1: (100.0, 1), LATEST: (39.3, 104_053)},
}


def _service(tmp_path, weeks, watch=()):
    """``weeks`` maps a HISTORY week to the date it is stored under; unmapped weeks are not stored."""
    store = TaiwanShareholdingStore(tmp_path / "s.parquet")
    store.write(pl.DataFrame([
        {"symbol": symbol, "date": weeks[week], "total_holders": holders, "total_shares": 1_000_000,
         "big_holders": 10, "big_shares": int(pct * 10_000), "big_holder_pct": pct,
         "source_url": "tdcc", "retrieved_at": "t"}
        for symbol, history in HISTORY.items() for week, (pct, holders) in history.items() if week in weeks
    ]))
    master = MagicMock()
    master.to_dataframe.return_value = pl.DataFrame({
        "symbol": list(HISTORY), "name": [f"N{symbol[:4]}" for symbol in HISTORY],
        "exchange": [symbol.split(".")[1] for symbol in HISTORY],
        "instrument_type": ["etf" if symbol == "0050.TWSE" else "stock" for symbol in HISTORY],
        "listing_status": ["active"] * len(HISTORY),
    })
    return TaiwanBigHolderRankingService(store, master, watch)


def _by_symbol(rows):
    return {row["symbol"]: row for row in rows}


def test_four_week_tone_and_ranking(tmp_path):
    snap = _service(tmp_path, {W4: W4, W1: W1, LATEST: LATEST}).snapshot()
    assert (snap["date"], snap["previous_1w_date"], snap["previous_4w_date"]) == ("2026-10-16", "2026-10-08", "2026-09-18")
    # 3008 has < 1,000 holders, 0050 is an ETF; 6488 and 2323 are in the universe but unranked.
    assert snap["universe_count"] == 6
    assert [r["symbol"] for r in snap["concentrating"]] == ["1101.TWSE", "8069.TPEX", "2330.TWSE", "2002.TWSE"]
    assert [r["symbol"] for r in snap["dispersing"]] == ["2002.TWSE", "2330.TWSE", "8069.TPEX", "1101.TWSE"]
    rows = _by_symbol(snap["concentrating"])
    assert {s: r["tone"] for s, r in rows.items()} == {
        "1101.TWSE": "concentrating", "2002.TWSE": "dispersing", "2330.TWSE": "flat",
        "8069.TPEX": "flat",  # +0.6pp but holders rose 1%, so not concentration
    }
    assert rows["1101.TWSE"]["change_4w_pp"] == pytest.approx(0.8)
    assert rows["1101.TWSE"]["holders_change_4w_pct"] == pytest.approx(-2.0)
    assert rows["1101.TWSE"]["change_1w_pp"] == pytest.approx(0.3)
    assert rows["2002.TWSE"]["holders_change_4w_pct"] == pytest.approx(3.0)
    # 8069 is absent from the 1-week base: missing, not 0.
    assert rows["8069.TPEX"]["change_1w_pp"] is None
    assert rows["8069.TPEX"]["holders_change_1w_pct"] is None
    assert rows["8069.TPEX"]["change_4w_pp"] == pytest.approx(0.6)


def test_two_adjacent_weeks_rank_by_one_week_change(tmp_path):
    snap = _service(tmp_path, {W1: W1, LATEST: LATEST}).snapshot()
    assert snap["previous_1w_date"] == "2026-10-08" and snap["previous_4w_date"] is None
    assert [r["symbol"] for r in snap["concentrating"]] == ["1101.TWSE", "2330.TWSE", "2002.TWSE"]
    assert [r["symbol"] for r in snap["dispersing"]] == ["2002.TWSE", "2330.TWSE", "1101.TWSE"]
    assert {r["tone"] for r in snap["concentrating"]} == {"insufficient"}
    assert all(r["change_4w_pp"] is None and r["holders_change_4w_pct"] is None for r in snap["concentrating"])


def test_skipped_week_has_no_change_and_empty_rankings(tmp_path):
    snap = _service(tmp_path, {W1: date(2026, 10, 1), LATEST: LATEST}).snapshot()
    assert snap["previous_1w_date"] is None and snap["previous_4w_date"] is None
    assert snap["concentrating"] == [] and snap["dispersing"] == []
    assert snap["universe_count"] == 6
    empty = _service(tmp_path / "empty", {}).snapshot()
    assert empty["date"] is None and empty["universe_count"] == 0 and empty["watchlist"] == []


def test_watchlist_lists_universe_members_with_unranked_last(tmp_path):
    snap = _service(tmp_path, {W4: W4, W1: W1, LATEST: LATEST},
                    watch=["6488.TPEX", "0050.TWSE", "2323.TWSE", "2002.TWSE", "1101.TWSE"]).snapshot()
    assert [r["symbol"] for r in snap["watchlist"]] == ["1101.TWSE", "2002.TWSE", "2323.TWSE", "6488.TPEX"]
    assert all(r["in_watchlist"] for r in snap["watchlist"])
    reissued = _by_symbol(snap["watchlist"])["2323.TWSE"]
    assert reissued["change_1w_pp"] is None and reissued["change_4w_pp"] is None and reissued["tone"] == "insufficient"
    assert _by_symbol(snap["concentrating"])["2330.TWSE"]["in_watchlist"] is False


def test_api_rejects_date_and_serves_snapshot(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import market_research
    from app.taiwan import big_holder_ranking

    monkeypatch.setattr(big_holder_ranking.TaiwanBigHolderRankingService, "__init__", lambda self: None)
    monkeypatch.setattr(big_holder_ranking.TaiwanBigHolderRankingService, "snapshot", lambda self: {"date": "2026-10-16"})
    app = FastAPI()
    app.include_router(market_research.router, prefix="/api/taiwan")
    client = TestClient(app)
    assert client.get("/api/taiwan/big-holder-ranking?date=2026-10-01").status_code == 422
    assert client.get("/api/taiwan/big-holder-ranking").json() == {"date": "2026-10-16"}

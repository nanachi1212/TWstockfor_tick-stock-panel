"""Fixed samples for 族群排名: √turnover weighting, low-turnover exclusion, ordering and rank moves."""
from datetime import date, timedelta
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.group_ranking import TaiwanGroupRankingService, rank_groups
from app.taiwan.observed_universe import ObservedUniverseStore
from app.taiwan.realtime.calendar import TaiwanTradingCalendar

D1, D2, D3, D4 = date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22)
GROUPS = [{"id": "g1", "name": "散熱", "color": "rose"}, {"id": "g2", "name": "機器人", "color": "teal"}]
ENTRIES = [
    {"symbol": "3017.TWSE", "group_ids": ["g1"]},
    {"symbol": "2049.TWSE", "group_ids": ["g2"]},
    {"symbol": "2359.TWSE", "group_ids": ["g2"]},
]
# symbol -> closes on D1, D2, D3: 散熱 leads on D2 (+2% vs -1%), 機器人 leads on D3 (+3% vs -1%).
CLOSES = {"3017.TWSE": (100.0, 102.0, 100.98), "2049.TWSE": (50.0, 49.5, 50.985)}


def _quotes(rows):
    return pl.DataFrame([{"symbol": s, "name": s, "close": 10.0, "change_pct": c, "amount": a} for s, c, a in rows])


def _group(group_id, symbols, name=None):
    return {"id": group_id, "name": name or group_id, "color": "sky", "symbols": symbols}


def test_sqrt_turnover_score_weights_and_low_amount_exclusion():
    quotes = _quotes([("A1", 0.02, 4e8), ("A2", -0.01, 1e8), ("A3", 0.10, 5e6)])
    [row] = rank_groups([_group("A", ["A1", "A2", "A3", "A4"])], quotes)
    assert row["rank"] == 1
    assert row["score"] == pytest.approx((0.02 * 20000 - 0.01 * 10000) / 30000, abs=1e-9)
    assert row["score"] == pytest.approx(0.01, abs=1e-9)
    assert row["weighted_avg_change"] == pytest.approx((0.02 * 4e8 - 0.01 * 1e8) / 5e8)
    assert row["average_change"] == pytest.approx(0.11 / 3)
    assert row["median_change"] == pytest.approx(0.02)
    assert (row["members"], row["quoted"], row["used"], row["excluded"]) == (4, 3, 2, 1)
    assert (row["advance"], row["decline"], row["flat"]) == (2, 1, 0)
    assert row["breadth"] == pytest.approx(2 / 3)
    assert row["amount"] == 5.05e8
    assert row["top_symbol"] == "A1"
    assert [(i["symbol"], i["excluded"]) for i in row["items"]] == [("A3", True), ("A1", False), ("A2", False)]
    weights = {i["symbol"]: i["weight"] for i in row["items"]}
    assert (weights["A1"], weights["A2"], weights["A3"]) == (pytest.approx(2 / 3), pytest.approx(1 / 3), 0.0)
    assert row["items"][0]["share"] == pytest.approx(5e6 / 5.05e8)


def test_turnover_share_below_half_percent_is_excluded_even_above_ten_million():
    [row] = rank_groups([_group("B", ["B1", "B2"])], _quotes([("B1", 0.01, 5e9), ("B2", 0.05, 2e7)]))
    assert {i["symbol"]: i["excluded"] for i in row["items"]} == {"B1": False, "B2": True}
    assert row["score"] == pytest.approx(0.01)
    assert row["top_symbol"] == "B1"


def test_ties_break_on_breadth_and_unscored_groups_go_last():
    quotes = _quotes([("X1", 0.25, 1e8), ("Y1", 0.5, 1e8), ("Y2", 0.0, 1e8), ("Z1", 0.3, 1e6), ("Z2", -0.1, 1e6)])
    rows = rank_groups([_group("z", ["Z1", "Z2"], "C族群"), _group("y", ["Y1", "Y2"], "A族群"),
                        _group("x", ["X1"], "B族群")], quotes)
    assert [(r["id"], r["rank"]) for r in rows] == [("x", 1), ("y", 2), ("z", None)]
    assert rows[0]["score"] == rows[1]["score"] == 0.25
    assert (rows[0]["breadth"], rows[1]["breadth"]) == (1.0, 0.5)
    unscored = rows[2]
    assert (unscored["score"], unscored["weighted_avg_change"], unscored["top_symbol"]) == (None, None, None)
    assert (unscored["quoted"], unscored["used"], unscored["excluded"]) == (2, 0, 2)
    assert unscored["breadth"] == 0.5


def _row(symbol, day, close, amount=1e8):
    return {"symbol": symbol, "date": day, "open": close, "high": close, "low": close, "close": close,
            "volume": 10.0, "amount": amount, "quote_ts": None}


def _rows(days):
    return [_row(s, day, closes[i]) for s, closes in CLOSES.items() for i, day in enumerate((D1, D2, D3)) if day in days]


def _service(tmp_path, rows, calendar=None, groups=GROUPS, entries=ENTRIES):
    daily = TaiwanDailyStore(tmp_path / "daily")
    daily.write_batch(pl.DataFrame(rows, schema_overrides={"amount": pl.Float64, "quote_ts": pl.Int64}))
    master = MagicMock()
    master.to_dataframe.return_value = pl.DataFrame({"symbol": ["3017.TWSE", "2359.TWSE"], "name": ["奇鋐", "所羅門"]})
    return TaiwanGroupRankingService(daily, calendar or TaiwanTradingCalendar(known_trading_days={D1, D2, D3}),
                                     master, ObservedUniverseStore(tmp_path / "census"), groups, entries)


def test_rank_moves_against_the_previous_session_with_current_membership(tmp_path):
    snap = _service(tmp_path, _rows({D1, D2, D3})).snapshot(D3)
    assert (snap["date"], snap["previous_date"], snap["status"], snap["reason"]) == ("2026-10-21", "2026-10-20", "available", None)
    assert snap["rules"] == {"min_share": 0.005, "min_amount": 1e7}
    robots, cooling = snap["groups"]
    assert (robots["id"], robots["rank"], robots["previous_rank"], robots["rank_change"]) == ("g2", 1, 2, 1)
    assert (cooling["id"], cooling["rank"], cooling["previous_rank"], cooling["rank_change"]) == ("g1", 2, 1, -1)
    assert robots["score"] == pytest.approx(0.03)
    assert cooling["score"] == pytest.approx(-0.01)
    assert (robots["members"], robots["quoted"], robots["color"]) == (2, 1, "teal")
    assert robots["items"][0]["name"] == "2049.TWSE"
    assert cooling["items"][0]["name"] == "奇鋐"


def test_two_sessions_rank_today_without_previous_rank(tmp_path):
    holidays = {D2 - timedelta(days=n) for n in range(1, 60)}
    calendar = TaiwanTradingCalendar(known_trading_days={D2, D3}, known_holidays=holidays)
    snap = _service(tmp_path, _rows({D2, D3}), calendar).snapshot(D3)
    assert snap["previous_date"] == "2026-10-20"
    assert [(g["id"], g["rank"], g["previous_rank"], g["rank_change"]) for g in snap["groups"]] == [
        ("g2", 1, None, None), ("g1", 2, None, None)]


def test_target_without_partition_is_unavailable(tmp_path):
    snap = _service(tmp_path, _rows({D1, D2, D3})).snapshot(D4)
    assert (snap["date"], snap["status"], snap["reason"], snap["groups"]) == ("2026-10-22", "unavailable", "no_daily_data", [])


def test_no_groups_is_available_and_empty(tmp_path):
    snap = _service(tmp_path, _rows({D1, D2, D3}), groups=[], entries=[]).snapshot(D3)
    assert (snap["status"], snap["groups"]) == ("available", [])


def test_api_passes_date_and_validates_it(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import market_research
    service = _service(tmp_path, _rows({D1, D2, D3}))
    monkeypatch.setattr(market_research, "TaiwanGroupRankingService", lambda: service)
    app = FastAPI()
    app.include_router(market_research.router, prefix="/api/taiwan")
    client = TestClient(app)
    response = client.get("/api/taiwan/group-ranking?date=2026-10-21")
    assert response.status_code == 200
    assert [(g["id"], g["rank_change"]) for g in response.json()["groups"]] == [("g2", 1), ("g1", -1)]
    assert client.get("/api/taiwan/group-ranking?date=bad").status_code == 422

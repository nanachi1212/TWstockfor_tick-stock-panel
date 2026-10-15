from datetime import date, datetime

import polars as pl
import pytest

from app.taiwan import chip_ranking_tracking as tracking
from app.taiwan.chip_ranking_tracking import BENCHMARK_SYMBOL, ChipRankingTracker
from app.taiwan.corporate_actions import CorporateActionEvent, event_market_open
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.providers.taiwan_values import TAIPEI

RANKED = date(2026, 10, 5)
SESSIONS = [RANKED, date(2026, 10, 6), date(2026, 10, 7), date(2026, 10, 8), date(2026, 10, 9), date(2026, 10, 12)]
FULL = (date(2026, 1, 1), date(2026, 12, 31), ())


class _Actions:
    def __init__(self, coverage):
        self.coverage = coverage

    def read_verified_coverage(self):
        return self.coverage


def _bars(tmp_path, *, drop=()):
    """(open, close) per session; A and 0050 trade every day, B is missing on 10-12."""
    prices = {
        "A.TWSE": [(99, 99), (100, 103), (103, 104), (104, 106), (106, 108), (108, 110)],
        "B.TPEX": [(50, 50), (50, 49), (49, 48), (48, 48), (48, 47), None],
        BENCHMARK_SYMBOL: [(199, 199), (200, 202), (202, 202), (202, 203), (203, 203), (203, 204)],
    }
    store = TaiwanDailyStore(data_dir=tmp_path)
    for i, d in enumerate(SESSIONS):
        rows = [(s, p[i]) for s, p in prices.items() if p[i] is not None and (s, d) not in drop]
        store.write_batch(pl.DataFrame({
            "symbol": [s for s, _ in rows], "date": [d] * len(rows),
            "open": [float(p[0]) for _, p in rows], "high": [float(p[1]) for _, p in rows],
            "low": [float(p[0]) for _, p in rows], "close": [float(p[1]) for _, p in rows],
            "volume": [1e6] * len(rows), "amount": [1e8] * len(rows), "quote_ts": [0] * len(rows),
        }), partition_date=d)
    return store


def _snapshot(day=RANKED):
    return {"date": day.isoformat(), "weights": {"flow_today": 25},
            "TWSE": [{"symbol": "A.TWSE", "name": "甲", "chip_score": 90.0}],
            "TPEX": [{"symbol": "B.TPEX", "name": "乙", "chip_score": 80.0}]}


def _tracker(tmp_path, *, coverage=FULL, drop=()):
    return ChipRankingTracker(path=tmp_path / "tracking.jsonl", daily=_bars(tmp_path, drop=drop),
                              actions=_Actions(coverage))


AFTER_CLOSE = datetime(2026, 10, 5, 16, 40, tzinfo=TAIPEI)


def test_freeze_once_per_date_and_skip_empty(tmp_path):
    t = _tracker(tmp_path)
    assert t.freeze(_snapshot(), recorded_at=AFTER_CLOSE)["status"] == "frozen"
    again = t.freeze({**_snapshot(), "TWSE": []}, recorded_at=AFTER_CLOSE)
    assert again == {"status": "skipped", "reason": "already_frozen", "date": "2026-10-05"}
    assert [p["symbol"] for p in t.records()[0]["picks"]] == ["A.TWSE", "B.TPEX"]  # first freeze wins
    assert t.freeze({"date": "2026-10-06", "TWSE": [], "TPEX": []})["reason"] == "empty_ranking"


def test_entry_is_next_session_open_and_compares_with_0050(tmp_path):
    t = _tracker(tmp_path)
    t.freeze(_snapshot(), recorded_at=AFTER_CLOSE)
    day = t.report()["days"][0]
    assert day["status"] == "tracked" and day["entry_date"] == "2026-10-06"
    a, b = day["picks"]
    # 1 session: open 10-06 -> close 10-06.
    assert a["return_1"] == pytest.approx(103 / 100 - 1)
    assert a["excess_1"] == pytest.approx(0.03 - 0.01)
    assert b["return_1"] == pytest.approx(49 / 50 - 1)
    assert day["h1"]["benchmark_return"] == pytest.approx(0.01)
    assert day["h1"]["avg_return"] == pytest.approx((0.03 - 0.02) / 2)
    # 5 sessions: open 10-06 -> close 10-12; B has no bar on 10-12, so no return (not 0).
    assert day["h5"]["exit_date"] == "2026-10-12"
    assert a["return_5"] == pytest.approx(110 / 100 - 1)
    assert a["excess_5"] == pytest.approx(0.10 - (204 / 200 - 1))
    assert b["return_5"] is None and b["excess_5"] is None
    assert day["h5"]["picks"] == 1 and day["h5"]["avg_return"] == pytest.approx(0.10)


def test_cash_dividend_inside_window_uses_adjusted_entry(tmp_path):
    ex = date(2026, 10, 8)
    event = CorporateActionEvent(
        "A.TWSE", "TWSE", ex, event_market_open(ex), "cash_dividend",
        104, 100, 100 / 104, 4, None, None, "TWT49U", "https://www.twse.com.tw",
        datetime(2026, 10, 1, tzinfo=TAIPEI), status="verified")
    t = _tracker(tmp_path, coverage=(date(2026, 1, 1), date(2026, 12, 31), (event,)))
    t.freeze(_snapshot(), recorded_at=AFTER_CLOSE)
    a = t.report()["days"][0]["picks"][0]
    assert a["return_1"] == pytest.approx(0.03)  # ex-date is after the 1-session exit
    assert a["return_5"] == pytest.approx(110 / (100 * 100 / 104) - 1)  # dividend is not a loss


def test_snapshot_recorded_after_entry_open_is_late(tmp_path):
    t = _tracker(tmp_path)
    t.freeze(_snapshot(), recorded_at=datetime(2026, 10, 6, 9, 0, tzinfo=TAIPEI))
    report = t.report()
    assert report["days"][0]["status"] == "late"
    assert report["summary"]["h1"]["days"] == 0


def test_unverified_coverage_and_pending_horizon_fail_closed(tmp_path):
    t = _tracker(tmp_path, coverage=None)
    t.freeze(_snapshot(), recorded_at=AFTER_CLOSE)
    t.freeze(_snapshot(date(2026, 10, 9)), recorded_at=datetime(2026, 10, 9, 17, tzinfo=TAIPEI))
    t.freeze(_snapshot(date(2026, 10, 12)), recorded_at=datetime(2026, 10, 12, 17, tzinfo=TAIPEI))
    by_date = {d["date"]: d for d in t.report()["days"]}
    assert by_date["2026-10-05"]["h1"]["reason"] == "corporate_action_coverage"
    assert by_date["2026-10-09"]["h5"]["status"] == "pending"  # only one stored session after it
    assert by_date["2026-10-12"]["status"] == "pending" and by_date["2026-10-12"]["entry_date"] is None


def test_missing_benchmark_bar_makes_day_unavailable(tmp_path):
    t = _tracker(tmp_path, drop={(BENCHMARK_SYMBOL, date(2026, 10, 6))})
    t.freeze(_snapshot(), recorded_at=AFTER_CLOSE)
    day = t.report()["days"][0]
    assert day["h1"] == {"status": "unavailable", "reason": "price_data", "exit_date": "2026-10-06"}


def test_summary_needs_minimum_days(tmp_path, monkeypatch):
    t = _tracker(tmp_path)
    t.freeze(_snapshot(), recorded_at=AFTER_CLOSE)
    assert t.report()["summary"]["h1"]["avg_return"] is None
    monkeypatch.setattr(tracking, "MIN_DAYS", 1)
    h1 = t.report()["summary"]["h1"]
    assert h1["sample_sufficient"] and h1["days"] == 1 and h1["picks"] == 2
    assert h1["avg_excess"] == pytest.approx(0.005 - 0.01)
    assert h1["win_rate"] == pytest.approx(0.5) and h1["beat_rate"] == pytest.approx(0.5)


def test_api_returns_report(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import market_research

    monkeypatch.setattr(ChipRankingTracker, "report", lambda self: {"days": [], "min_days": 20})
    app = FastAPI()
    app.include_router(market_research.router, prefix="/api/taiwan")
    response = TestClient(app).get("/api/taiwan/chip-ranking/tracking")
    assert response.status_code == 200 and response.json()["days"] == []

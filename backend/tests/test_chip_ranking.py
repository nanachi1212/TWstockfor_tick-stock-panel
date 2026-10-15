from datetime import date

import polars as pl
import pytest

from app.taiwan.chip_ranking import TaiwanChipRankingService, score_frame


def _frame(**overrides):
    base = {
        "symbol": ["A", "B", "C", "X"], "exchange": ["TWSE", "TWSE", "TWSE", "TPEX"],
        "flow_today": [0.1, 0.2, 0.3, 0.01], "flow_5d": [0.1, 0.2, 0.3, 0.01],
        "streak": [1.0, 2.0, 3.0, 0.0], "big_holder_change": [None, None, None, None],
        "margin_drop": [0.0, 0.1, 0.2, 0.0],
    }
    base.update(overrides)
    return pl.DataFrame(base, schema_overrides={"big_holder_change": pl.Float64})


def test_ranks_inside_each_exchange_and_renormalizes_missing():
    out = {r["symbol"]: r for r in score_frame(_frame()).to_dicts()}
    assert out["C"]["chip_score"] == pytest.approx(100)  # best on every available component
    assert out["A"]["chip_score"] == pytest.approx(0)
    assert out["B"]["chip_score"] == pytest.approx(50)
    # A lone TPEx stock is not compared with TWSE values.
    assert out["X"]["chip_score"] == pytest.approx(0)
    assert "big_holder_change" not in out["C"]["components"]


def test_big_holder_change_counts_when_present():
    out = {r["symbol"]: r for r in score_frame(_frame(big_holder_change=[0.5, 0.0, -0.5, 0.1])).to_dicts()}
    assert "big_holder_change" in out["C"]["components"]
    # C is last on big-holder change (weight 25 of 100) and first on the other 75.
    assert out["C"]["chip_score"] == pytest.approx(75)


def test_api_rejects_past_date_instead_of_returning_latest():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import market_research

    app = FastAPI()
    app.include_router(market_research.router, prefix="/api/taiwan")
    response = TestClient(app).get("/api/taiwan/chip-ranking?date=2026-10-01")
    assert response.status_code == 422


def test_snapshot_matches_fixed_baseline_and_keeps_missing_metric_none():
    sessions = [date.fromisoformat(value) for value in (
        "2026-10-07", "2026-10-08", "2026-10-12", "2026-10-13", "2026-10-14",
    )]
    target = sessions[-1]

    class Master:
        def to_dataframe(self, *, supported_only):
            assert supported_only is True
            return pl.DataFrame({
                "symbol": ["2330.TWSE"], "name": ["台積電"], "exchange": ["TWSE"],
                "listing_status": ["active"], "instrument_type": ["stock"],
            })

    class Statistics:
        master = Master()

        def get_chip_ranking_inputs(self, requested_target):
            assert requested_target == target
            return sessions, {"2330.TWSE": {
                "institutional_net_5d": 400.0,
                "foreign_buy_streak": 2.0,
                "trust_buy_streak": None,
            }}

        def get_snapshot(self, *_args, **_kwargs):
            raise AssertionError("chip ranking should use the lightweight shared statistics path")

    class Institutional:
        def available_dates(self):
            return [target]

        def read_range(self, symbols, start, end):
            assert symbols == ["2330.TWSE"] and start == end == target
            return pl.DataFrame({
                "symbol": ["2330.TWSE"], "date": [target], "trade_date": [target],
                "status": ["official"], "has_discrepancy": [False],
                "foreign_net": [100], "investment_trust_net": [200], "dealer_net": [100],
            })

    class Daily:
        def read_range(self, symbols, start, end):
            assert symbols == ["2330.TWSE"] and start == sessions[0] and end == target
            return pl.DataFrame({
                "symbol": ["2330.TWSE"] * 5, "date": sessions,
                "open": [100.0] * 5, "high": [100.0] * 5, "low": [100.0] * 5,
                "close": [100.0] * 5, "volume": [1_000_000.0] * 5,
                "amount": [100_000_000.0] * 5, "quote_ts": [None] * 5,
            })

    class Margin:
        def read_range(self, symbols, start, end):
            return pl.DataFrame({"symbol": symbols, "trade_date": [target], "margin_change": [-5_000]})

    class Shareholding:
        def latest_with_change(self, symbols, before):
            return pl.DataFrame({
                "symbol": symbols, "date": [date(2026, 10, 8)], "previous_date": [None],
                "big_holder_pct": [40.0], "big_holder_pct_change": [None],
            }, schema_overrides={"previous_date": pl.Date, "big_holder_pct_change": pl.Float64})

    statistics = Statistics()
    service = TaiwanChipRankingService(
        daily=Daily(), institutional=Institutional(), margin=Margin(),
        shareholding=Shareholding(), statistics=statistics,
    )

    result = service.snapshot()

    assert result == {
        "date": "2026-10-14",
        "sessions": [str(session) for session in sessions],
        "weights": {"flow_today": 25, "flow_5d": 25, "streak": 10,
                    "big_holder_change": 25, "margin_drop": 15},
        "min_avg_volume_5d": 1_000_000,
        "min_close": 10.0,
        "big_holder_date": "2026-10-08",
        "big_holder_previous_date": None,
        "TWSE": [{
            "symbol": "2330.TWSE", "name": "台積電", "exchange": "TWSE",
            "close": 100.0, "change_pct": 0.0, "avg_volume_5d": 1_000_000.0,
            "chip_score": 0.0,
            "components": ["flow_today", "flow_5d", "margin_drop"],
            "institutional_net": 400.0, "institutional_net_5d": 400.0,
            "foreign_buy_streak": 2.0, "trust_buy_streak": None,
            "margin_change": -5_000.0, "big_holder_pct": 40.0,
            "big_holder_change": None,
        }],
        "TPEX": [],
        "day_trade_pressure": [],
    }

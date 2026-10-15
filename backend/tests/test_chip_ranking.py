import polars as pl
import pytest

from app.taiwan.chip_ranking import score_frame


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

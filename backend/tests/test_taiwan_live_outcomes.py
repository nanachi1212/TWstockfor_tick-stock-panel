from datetime import date, datetime, timedelta

import polars as pl
import pytest

from app.taiwan.providers.corporate_actions import SOURCE_URLS
from app.taiwan.quant.data_health import current_live_readiness
from app.taiwan.quant.live_outcomes import mature_live_outcomes
from app.taiwan.quant.live_store import LiveLedger
from app.taiwan.realtime.calendar import TAIPEI_TZ, TradingDayEvidence


class OutcomeLedger:
    def __init__(self):
        self.latest = date(2026, 9, 28)
        self.saved = {}

    def current_session(self):
        return self.latest

    def outcome_runs(self):
        return [{
            "model_key": "live-model",
            "session": "2026-09-23",
            "signals": [{"symbol": "2330.TWSE", "reference_close": 100.0}],
        }]

    def signal_outcomes(self, _key, _session, signals):
        result = []
        for signal in signals:
            for horizon in (1, 5, 20):
                result.append({
                    "symbol": signal["symbol"], "horizon": horizon,
                    **self.saved.get((signal["symbol"], horizon), {
                        "status": "pending", "value": None,
                    }),
                })
        return result

    def observe_outcome(self, _key, _session, symbol, horizon, outcome):
        key = (symbol, horizon)
        if key in self.saved and self.saved[key] == outcome:
            return "noop"
        self.saved[key] = dict(outcome)
        return "appended"


class OutcomeSource:
    def __init__(self, latest: date):
        self.latest = latest
        self.trading_days = {
            date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 28),
            date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1),
        }

    def evidence(self, day, exchange):
        status = "trading" if day in self.trading_days and day <= self.latest else "non_trading"
        return TradingDayEvidence(
            day, exchange, status, "fixture", "fixture",
            datetime.combine(day, datetime.min.time(), tzinfo=TAIPEI_TZ),
        )

    def outcome_prices(self, symbol, sessions):
        return pl.DataFrame({
            "symbol": [symbol] * len(sessions),
            "date": sessions,
            "close": [110.0 + index for index, _ in enumerate(sessions)],
        })

    def actions(self, start, end):
        return (), {
            "status": "verified", "start": start - timedelta(days=1),
            "end": end, "sources": sorted(SOURCE_URLS),
        }


def test_horizon_summary_counts_only_verified_raw_positive_returns():
    rows = [
        {"horizon": 1, "status": "verified", "value": 0.01},
        {"horizon": 1, "status": "verified", "value": 0.0},
        {"horizon": 1, "status": "verified", "value": -0.02},
        {"horizon": 1, "status": "pending", "value": None},
        {"horizon": 1, "status": "data_insufficient", "value": None},
    ]

    summary = LiveLedger.horizon_summary(rows, 1)

    assert summary["evaluated_count"] == 3
    assert summary["pending_count"] == 1
    assert summary["unavailable_count"] == 1
    assert summary["hit_count"] == 1
    assert summary["hit_rate_pct"] == pytest.approx(100 / 3)
    assert summary["average_return_pct"] == pytest.approx(-1 / 3)


def test_current_live_readiness_projects_existing_gate_evidence_without_oos_thresholds():
    readiness = current_live_readiness(
        "blocked", ["factor_data_unavailable", "factor_data_unavailable"],
        checks={"market_data": "verified", "factor_data": "unavailable"},
    )

    assert readiness == {
        "status": "unavailable", "source": "current_live_gate",
        "reasons": ["factor_data_unavailable"],
        "checks": {"market_data": "verified", "factor_data": "unavailable"},
    }


def test_maturity_uses_actual_trading_sessions_and_identical_rerun_is_noop():
    ledger = OutcomeLedger()
    source = OutcomeSource(date(2026, 9, 28))

    first = mature_live_outcomes(ledger, source)
    assert first["appended"] == 3
    assert ledger.saved[("2330.TWSE", 1)]["end_session"] == date(2026, 9, 24)
    assert ledger.saved[("2330.TWSE", 5)]["status"] == "pending"

    source.latest = date(2026, 10, 1)
    ledger.latest = source.latest
    second = mature_live_outcomes(ledger, source)
    assert second["appended"] == 1
    assert second["noop"] == 1
    assert ledger.saved[("2330.TWSE", 5)]["end_session"] == date(2026, 9, 30)

    retry = mature_live_outcomes(ledger, source)
    assert retry["noop"] == 1
    assert ledger.saved[("2330.TWSE", 5)]["end_session"] == date(2026, 9, 30)

from datetime import date

import pytest

from app.taiwan import options_flows
from app.taiwan.options_flows import (
    TxoOpenInterestStore,
    options_flows_snapshot,
    parse_txo_open_interest,
)


def _row(d, contract, strike, side, oi, session="一般", product="TXO"):
    return {"Date": d, "Contract": product, "ContractMonth(Week)": contract, "StrikePrice": strike,
            "CallPut": side, "OpenInterest": oi, "TradingSession": session}


@pytest.fixture(autouse=True)
def _no_refetch_window(monkeypatch):
    monkeypatch.setattr(options_flows, "_last_fetch_mono", None)


def test_snapshot_compares_with_previous_stored_day_and_finds_peaks(tmp_path):
    store = TxoOpenInterestStore(tmp_path)
    store.write(*parse_txo_open_interest([_row("20261111", "202611", "20000", "買權", "100")]))
    payload = [
        _row("20261112", "202611", "20000", "買權", "150"), _row("20261112", "202611", "20000", "賣權", "80"),
        _row("20261112", "202611", "20100", "買權", "90"), _row("20261112", "202611W3", "20100", "賣權", "500"),
        _row("20261112", "202611", "20000", "買權", "9", session="盤後"),   # after-hours session ignored
        _row("20261112", "202611", "20000", "買權", "7", product="TEO"),   # other product ignored
    ]
    out = options_flows_snapshot(store=store, fetch=lambda *_a, **_k: payload, foreign=lambda _d: {})

    assert (out["date"], out["previous_date"], out["status"]) == ("2026-11-12", "2026-11-11", "available")
    monthly = next(c for c in out["contracts"] if c["contract"] == "202611")
    assert monthly["strikes"][0] == {"strike": 20000.0, "call": 150, "put": 80, "call_change": 50, "put_change": None}
    assert monthly["strikes"][1]["call_change"] is None  # strike not in the previous file: no change, not +90
    assert (monthly["call_peak"], monthly["put_peak"], monthly["total_oi"]) == (20000.0, 20000.0, 320)
    assert out["default_contract"] == "202611"  # monthly preferred over a larger weekly


def test_mixed_dates_or_failed_fetch_never_invent_a_day(tmp_path):
    assert parse_txo_open_interest([_row("20261111", "202611", "1", "買權", "1"),
                                    _row("20261112", "202611", "1", "買權", "1")]) is None

    def boom(*_a, **_k):
        raise TimeoutError

    out = options_flows_snapshot(store=TxoOpenInterestStore(tmp_path), fetch=boom, foreign=lambda _d: {})
    assert out["status"] == "unavailable" and out["date"] is None and "TimeoutError" in out["message"]
    past = options_flows_snapshot(date(2026, 1, 5), store=TxoOpenInterestStore(tmp_path), foreign=lambda _d: {"ok": 1})
    assert past["status"] == "unavailable" and past["foreign"] == {"ok": 1}

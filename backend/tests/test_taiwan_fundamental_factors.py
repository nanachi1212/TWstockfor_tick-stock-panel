from __future__ import annotations

from datetime import datetime

from app.taiwan.fundamental_factors import TaiwanVerifiedFundamentalFactors
from app.taiwan.fundamentals import FundamentalRecord, TaiwanFundamentalStore
from app.taiwan.providers.taiwan_values import TAIPEI


def _record(symbol: str, available: str | None, revision: str, values: dict, *, dataset: str = "financial_statement") -> FundamentalRecord:
    return FundamentalRecord(symbol, dataset, "2026-01-01", "2026-03-31", None,
        datetime.fromisoformat(available) if available else None,
        datetime(2026, 5, 30, tzinfo=TAIPEI), revision, "TWSE", "fixture",
        "https://example.invalid", "official" if available else "data_insufficient",
        "TWD", "thousand_TWD", values=values)


def test_pit_boundary_revision_and_no_lookahead(tmp_path):
    store = TaiwanFundamentalStore(tmp_path)
    store.save([
        _record("2330.TWSE", "2026-05-10T18:46:58+08:00", "A", {"cumulative_eps": 10.0}),
        _record("2330.TWSE", "2026-05-20T18:46:58+08:00", "B", {"cumulative_eps": 12.0}),
    ])
    factors = TaiwanVerifiedFundamentalFactors(store)
    assert factors.evaluate("2330.TWSE", "eps", datetime.fromisoformat("2026-05-10T18:46:58+08:00")).status == "data_insufficient"
    first = factors.evaluate("2330.TWSE", "eps", datetime.fromisoformat("2026-05-15T00:00:00+08:00"))
    assert (first.value, first.revision_identity) == (10.0, "A")
    assert factors.evaluate("2330.TWSE", "eps", datetime.fromisoformat("2026-05-21T00:00:00+08:00")).value == 12.0


def test_unproven_availability_missing_zero_and_etf_fail_closed(tmp_path):
    store = TaiwanFundamentalStore(tmp_path)
    store.save([
        _record("2330.TWSE", None, "U", {"cumulative_eps": 99.0}),
        _record("6488.TPEX", "2026-05-10T00:00:00+08:00", "Z", {"cumulative_eps": 0.0}),
        _record("2881.TWSE", "2026-05-10T00:00:00+08:00", "M", {"cumulative_eps": None}),
    ])
    factors = TaiwanVerifiedFundamentalFactors(store)
    query = datetime.fromisoformat("2026-05-11T00:00:00+08:00")
    assert factors.evaluate("2330.TWSE", "eps", query).status == "data_insufficient"
    zero = factors.evaluate("6488.TPEX", "eps", query)
    assert zero.status == "available" and zero.value == 0.0
    assert factors.evaluate("2881.TWSE", "eps", query).status == "missing"
    assert factors.evaluate("0050.TWSE", "eps", query, security_type="etf").status == "unsupported"


def test_roe_units_ranking_and_coverage(tmp_path):
    store = TaiwanFundamentalStore(tmp_path)
    available = "2026-05-10T00:00:00+08:00"
    store.save([
        _record("2330.TWSE", available, "A", {"net_income": 200_000, "equity": 1_000_000}),
        _record("6488.TPEX", available, "A", {"net_income": 50_000, "equity": 1_000_000}),
        _record("2881.TWSE", None, "A", {"net_income": 900_000, "equity": 1_000_000}),
    ])
    factors = TaiwanVerifiedFundamentalFactors(store)
    ranked, coverage = factors.rank(
        ["2330.TWSE", "6488.TPEX", "2881.TWSE", "0050.TWSE"], "roe",
        datetime.fromisoformat("2026-05-11T00:00:00+08:00"),
        security_types={"0050.TWSE": "etf"},
    )
    assert [(row.symbol, row.value) for row in ranked] == [("2330.TWSE", 0.2), ("6488.TPEX", 0.05)]
    assert coverage.to_dict() == {"total_securities": 4, "eligible_securities": 3, "available_values": 2, "missing_values": 0, "data_insufficient": 1, "unsupported": 1, "malformed": 0}

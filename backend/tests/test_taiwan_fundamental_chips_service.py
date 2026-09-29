from __future__ import annotations

from datetime import date

from app.services import preferences
from app.taiwan.finmind_cache import FinMindCache
from app.taiwan.fundamental_chips_service import TaiwanFundamentalChipsService


class _Adapter:
    token = "test"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, bool]] = []

    def fetch_financial_statements(self, symbol, start_date=None, raise_for_status=False):
        self.calls.append(("financial", start_date, raise_for_status))
        return [
            {"date": "2026-06-30", "type": "EPS", "value": 1.25},
            {"date": "2026-06-30", "type": "Revenue", "value": 1000000},
        ]

    def fetch_shareholding(self, symbol, start_date=None, raise_for_status=False):
        self.calls.append(("shareholding", start_date, raise_for_status))
        return [
            {"date": "2026-09-23", "ForeignInvestmentSharesRatio": 12.5, "ForeignInvestmentShares": 1000},
            {"date": "2026-09-24", "ForeignInvestmentSharesRatio": 12.7, "ForeignInvestmentShares": 1100},
        ]

    def fetch_securities_lending(self, symbol, start_date=None, raise_for_status=False):
        self.calls.append(("lending", start_date, raise_for_status))
        return [{"date": "2026-09-24", "volume": 3000, "fee_rate": 0.5}]


def test_finmind_detail_datasets_use_bounded_start_dates(tmp_path, monkeypatch):
    monkeypatch.setattr(preferences, "get_finmind_enabled", lambda: True)
    adapter = _Adapter()
    service = TaiwanFundamentalChipsService(
        finmind_adapter=adapter,
        cache=FinMindCache(tmp_path),
    )
    monkeypatch.setattr(service, "_cutoff_date", lambda _as_of: date(2026, 9, 29))

    financial = service.get_financial_statements("8358.TPEX")
    shareholding = service.get_foreign_shareholding("8358.TPEX")
    lending = service.get_securities_lending("8358.TPEX")

    assert financial.latest_eps == 1.25
    assert shareholding.ratio == 12.7
    assert lending.latest_volume == 3000
    assert adapter.calls == [
        ("financial", "2021-09-25", True),
        ("shareholding", "2026-04-02", True),
        ("lending", "2026-04-02", True),
    ]


def test_missing_eps_and_shareholding_expose_reasons(tmp_path, monkeypatch):
    monkeypatch.setattr(preferences, "get_finmind_enabled", lambda: True)
    service = TaiwanFundamentalChipsService(
        finmind_adapter=_Adapter(),
        cache=FinMindCache(tmp_path),
    )

    financial = service._process_financial_statements(
        [{"date": "2026-06-30", "type": "NetIncome", "value": 100}],
        "2026-06-30",
        "2026-09-29T00:00:00+08:00",
    )
    shareholding = service._process_shareholding(
        [{"date": "2026-09-24", "ForeignInvestmentShares": 1000}],
        "2026-09-24",
        "2026-09-29T00:00:00+08:00",
    )

    assert financial.meta.status == "available"
    assert financial.latest_eps is None
    assert financial.meta.fallback_reason == "latest financial statement contains no EPS"
    assert shareholding.meta.status == "unavailable"
    assert shareholding.meta.fallback_reason == "shareholding rows contain no usable foreign ratio"

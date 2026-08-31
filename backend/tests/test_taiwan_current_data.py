from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
from app.taiwan.current_data import TaiwanCurrentDataService, capability_matrix
from app.taiwan.etf_data import TaiwanETFProfile, TaiwanETFSnapshot
from app.taiwan.fundamentals import FundamentalRecord
from app.taiwan.providers.hybrid_provider import TaiwanHybridProvider
from app.taiwan.providers.taiwan_values import TAIPEI


class FakeMaster:
    def __init__(self, instrument_type: str) -> None:
        self.instrument_type = instrument_type

    def get_instrument(self, symbol: str):
        return SimpleNamespace(
            symbol=symbol,
            name="fixture",
            instrument_type=self.instrument_type,
        )


class FakeProvider:
    def __init__(self, *, fail_dataset: str | None = None) -> None:
        self.fail_dataset = fail_dataset
        self.calls: list[tuple[str, str]] = []

    def get_fundamentals(self, symbol, dataset, *, exchange, security_type, **kwargs):
        self.calls.append(("company", dataset))
        if dataset == self.fail_dataset:
            raise RuntimeError("official schema changed")
        return FundamentalRecord(
            symbol=symbol,
            dataset=dataset,
            period_start=None,
            period_end="2026-08-31",
            published_at=None,
            available_at=None,
            retrieved_at=datetime(2026, 8, 31, 12, tzinfo=TAIPEI),
            revision="1150831",
            provider=exchange,
            source=f"{exchange.lower()}:{dataset}",
            source_url="https://example.invalid/official",
            status="data_insufficient",
            normalized_unit="fixture",
            values={"value": 1},
        )

    def get_etf_data(self, symbol, dataset, *, exchange):
        self.calls.append(("etf", dataset))
        now = datetime(2026, 8, 31, 12, tzinfo=TAIPEI)
        if dataset == "profile":
            return TaiwanETFProfile(
                symbol,
                "ETF",
                exchange,
                "index",
                "type",
                "2003-06-25",
                "2003-06-30",
                "TWD",
                exchange,
                "twse:profile",
                "https://example.invalid/official",
                now,
                "official",
            )
        return TaiwanETFSnapshot(
            symbol,
            "2026-08-31",
            None,
            None,
            None,
            None,
            None,
            None,
            1000,
            "TWD",
            exchange,
            "twse:snapshot",
            "https://example.invalid/official",
            now,
            "data_insufficient",
            "units",
            "units",
        )


def test_capability_matrix_keeps_usage_and_status_concepts_separate():
    matrix = {(row.domain, row.dataset): row for row in capability_matrix()}
    for dataset in ("financial_statement", "valuation", "share_capital_record"):
        item = matrix[("company", dataset)]
        assert item.production_supported and item.current_reference
        assert not item.pit_historical and item.usage_scope == "current_reference"
        assert item.status == "data_insufficient"
    assert matrix[("company", "dividend_lifecycle_event")].pit_historical
    assert not matrix[("etf", "historical_nav")].production_supported
    assert matrix[("etf", "historical_nav")].usage_scope == "historical_reference"
    assert matrix[("etf", "profile")].exchanges == ["TWSE"]


def test_hybrid_provider_exposes_valuation_and_share_capital(monkeypatch):
    marker = object()
    monkeypatch.setattr(
        "app.taiwan.fundamentals.TaiwanOfficialFundamentals.valuation",
        lambda self, symbol, exchange, security_type="stock": marker,
    )
    monkeypatch.setattr(
        "app.taiwan.fundamentals.TaiwanOfficialFundamentals.share_capital",
        lambda self, symbol, exchange, security_type="stock": marker,
    )
    provider = TaiwanHybridProvider()
    for dataset in ("valuation", "share_capital_record"):
        assert (
            provider.get_fundamentals(
                "2330.TWSE",
                dataset,
                exchange="TWSE",
            )
            is marker
        )


def test_company_current_sections_are_reference_only_and_isolated():
    provider = FakeProvider(fail_dataset="valuation")
    result = TaiwanCurrentDataService(provider, FakeMaster("stock")).get_current_data("2330.TWSE")
    assert set(result.sections) == {
        "monthly_revenue",
        "financial_statement",
        "valuation",
        "share_capital_record",
    }
    revenue = result.sections["monthly_revenue"]
    assert revenue.data["values"]["value"] == 1
    assert revenue.available_at is None
    assert revenue.usage_scope == "current_reference"
    assert not revenue.historically_eligible
    assert result.sections["valuation"].status == "error"
    assert "RuntimeError" in result.sections["valuation"].reason
    assert result.sections["financial_statement"].data is not None


def test_tpex_company_uses_the_same_current_reference_contract():
    result = TaiwanCurrentDataService(FakeProvider(), FakeMaster("stock")).get_current_data(
        "6488.TPEX"
    )
    assert result.exchange == "TPEX"
    assert all(section.provider == "TPEX" for section in result.sections.values())
    assert all(section.usage_scope == "current_reference" for section in result.sections.values())
    assert all(not section.historically_eligible for section in result.sections.values())


def test_etf_current_data_does_not_fetch_company_or_historical_sections():
    provider = FakeProvider()
    result = TaiwanCurrentDataService(provider, FakeMaster("etf")).get_current_data("0050.TWSE")
    assert set(result.sections) == {"profile", "snapshot"}
    assert provider.calls == [("etf", "profile"), ("etf", "snapshot")]
    snapshot = result.sections["snapshot"]
    assert snapshot.data["issued_units"] == 1000
    assert snapshot.data["nav"] is None and snapshot.data["outstanding_units"] is None
    assert snapshot.available_at is None and not snapshot.historically_eligible


def test_api_capabilities_and_current_data(monkeypatch):
    service = TaiwanCurrentDataService(FakeProvider(), FakeMaster("stock"))
    monkeypatch.setattr(
        "app.api.taiwan.get_taiwan_current_data_service",
        lambda: service,
    )
    client = TestClient(app, client=("127.0.0.1", 50000))

    capabilities = client.get("/api/taiwan/capabilities")
    assert capabilities.status_code == 200
    assert any(
        row["dataset"] == "historical_nav" and not row["production_supported"]
        for row in capabilities.json()
    )

    response = client.get("/api/taiwan/data/2330.TWSE")
    assert response.status_code == 200
    payload = response.json()
    assert payload["security_type"] == "stock"
    assert payload["sections"]["valuation"]["usage_scope"] == "current_reference"
    assert payload["sections"]["valuation"]["historically_eligible"] is False


def test_api_rejects_invalid_symbol_and_unknown_security_type(monkeypatch):
    service = TaiwanCurrentDataService(FakeProvider(), FakeMaster("unsupported"))
    monkeypatch.setattr(
        "app.api.taiwan.get_taiwan_current_data_service",
        lambda: service,
    )
    client = TestClient(app, client=("127.0.0.1", 50000))
    assert client.get("/api/taiwan/data/not-a-symbol").status_code == 400
    unknown = client.get("/api/taiwan/data/0050.TWSE")
    assert unknown.status_code == 404

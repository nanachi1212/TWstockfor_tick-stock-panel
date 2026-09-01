"""Product-safe Taiwan current/reference data and capability metadata."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.taiwan.providers.hybrid_provider import TaiwanHybridProvider
from app.taiwan.symbol import parse_symbol
from app.taiwan.universe import get_security_master

UsageScope = Literal["current_reference", "historical_reference", "pit_historical", "unsupported"]


class TaiwanDatasetCapability(BaseModel):
    domain: Literal["company", "etf"]
    dataset: str
    exchanges: list[Literal["TWSE", "TPEX"]]
    production_supported: bool
    current_reference: bool
    pit_historical: bool
    usage_scope: UsageScope
    status: str
    reason: str


CAPABILITIES = (
    TaiwanDatasetCapability(
        domain="company",
        dataset="monthly_revenue",
        exchanges=["TWSE", "TPEX"],
        production_supported=True,
        current_reference=True,
        pit_historical=False,
        usage_scope="current_reference",
        status="data_insufficient",
        reason="official aggregate has no stable exact availability",
    ),
    TaiwanDatasetCapability(
        domain="company",
        dataset="financial_statement",
        exchanges=["TWSE", "TPEX"],
        production_supported=True,
        current_reference=True,
        pit_historical=False,
        usage_scope="current_reference",
        status="data_insufficient",
        reason="no stable document-to-aggregate availability join",
    ),
    TaiwanDatasetCapability(
        domain="company",
        dataset="valuation",
        exchanges=["TWSE", "TPEX"],
        production_supported=True,
        current_reference=True,
        pit_historical=False,
        usage_scope="current_reference",
        status="data_insufficient",
        reason="official trade-date snapshot has no first-public timestamp",
    ),
    TaiwanDatasetCapability(
        domain="company",
        dataset="share_capital_record",
        exchanges=["TWSE", "TPEX"],
        production_supported=True,
        current_reference=True,
        pit_historical=False,
        usage_scope="current_reference",
        status="data_insufficient",
        reason="official company profile is current and date-level only",
    ),
    TaiwanDatasetCapability(
        domain="company",
        dataset="dividend_lifecycle_event",
        exchanges=["TWSE", "TPEX"],
        production_supported=True,
        current_reference=True,
        pit_historical=True,
        usage_scope="pit_historical",
        status="official",
        reason="stable MOPS event identity and exact timestamp",
    ),
    TaiwanDatasetCapability(
        domain="etf",
        dataset="profile",
        exchanges=["TWSE"],
        production_supported=True,
        current_reference=True,
        pit_historical=False,
        usage_scope="current_reference",
        status="official",
        reason="official TWSE product metadata",
    ),
    TaiwanDatasetCapability(
        domain="etf",
        dataset="snapshot",
        exchanges=["TWSE"],
        production_supported=True,
        current_reference=True,
        pit_historical=False,
        usage_scope="current_reference",
        status="data_insufficient",
        reason="issued units are date-level current metadata",
    ),
    TaiwanDatasetCapability(
        domain="etf",
        dataset="historical_nav",
        exchanges=[],
        production_supported=False,
        current_reference=False,
        pit_historical=False,
        usage_scope="historical_reference",
        status="data_insufficient",
        reason="official reference source has no verified availability or revision identity",
    ),
    TaiwanDatasetCapability(
        domain="etf",
        dataset="distribution",
        exchanges=[],
        production_supported=False,
        current_reference=False,
        pit_historical=False,
        usage_scope="historical_reference",
        status="data_insufficient",
        reason="official reference source has no announcement timestamp or stable event identity",
    ),
)

_CAPABILITY_BY_KEY = {(item.domain, item.dataset): item for item in CAPABILITIES}


class TaiwanDataSection(BaseModel):
    dataset: str
    status: str
    usage_scope: UsageScope
    historically_eligible: bool
    available_at: str | None = None
    source: str | None = None
    provider: str | None = None
    reason: str | None = None
    data: dict[str, Any] | None = None


class TaiwanCurrentDataResponse(BaseModel):
    symbol: str
    exchange: str
    security_type: Literal["stock", "etf"]
    identity: dict[str, Any]
    sections: dict[str, TaiwanDataSection] = Field(default_factory=dict)


def capability_matrix() -> list[TaiwanDatasetCapability]:
    """Return the single authoritative Taiwan data capability matrix."""
    return list(CAPABILITIES)


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if is_dataclass(value):
        return {key: _json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


class TaiwanCurrentDataService:
    """Thin productization layer over existing Taiwan official providers."""

    COMPANY_DATASETS = (
        "monthly_revenue",
        "financial_statement",
        "valuation",
        "share_capital_record",
    )
    ETF_DATASETS = ("profile", "snapshot")

    def __init__(self, provider: TaiwanHybridProvider | None = None, security_master=None) -> None:
        self.provider = provider or TaiwanHybridProvider()
        self.security_master = security_master or get_security_master()

    def get_current_data(
        self,
        raw_symbol: str,
    ) -> TaiwanCurrentDataResponse:
        symbol = parse_symbol(raw_symbol)
        instrument = self.security_master.get_instrument(symbol.canonical)
        resolved_type = instrument.instrument_type if instrument else None
        if resolved_type not in {"stock", "etf"}:
            raise LookupError(f"supported security type not found for {symbol.canonical}")

        identity = {
            "symbol": symbol.canonical,
            "code": symbol.code,
            "exchange": symbol.exchange.value,
            "security_type": resolved_type,
            "name": instrument.name,
        }
        datasets = self.COMPANY_DATASETS if resolved_type == "stock" else self.ETF_DATASETS
        sections = {
            dataset: self._fetch_section(
                symbol.canonical,
                symbol.exchange.value,
                resolved_type,
                dataset,
            )
            for dataset in datasets
        }
        return TaiwanCurrentDataResponse(
            symbol=symbol.canonical,
            exchange=symbol.exchange.value,
            security_type=resolved_type,
            identity=identity,
            sections=sections,
        )

    def _fetch_section(
        self,
        symbol: str,
        exchange: str,
        security_type: str,
        dataset: str,
    ) -> TaiwanDataSection:
        domain = "etf" if security_type == "etf" else "company"
        capability = _CAPABILITY_BY_KEY[(domain, dataset)]
        if exchange not in capability.exchanges:
            return TaiwanDataSection(
                dataset=dataset,
                status="data_insufficient",
                usage_scope=capability.usage_scope,
                historically_eligible=False,
                reason=f"{dataset} is not production-supported for {exchange}",
            )
        try:
            if domain == "company":
                record = self.provider.get_fundamentals(
                    symbol,
                    dataset,
                    exchange=exchange,
                    security_type=security_type,
                )
            else:
                record = self.provider.get_etf_data(symbol, dataset, exchange=exchange)
            data = _json_value(record)
            available_at = data.get("available_at")
            return TaiwanDataSection(
                dataset=dataset,
                status=str(data.get("status") or "error"),
                usage_scope=capability.usage_scope,
                historically_eligible=bool(capability.pit_historical and available_at),
                available_at=available_at,
                source=data.get("source"),
                provider=data.get("provider"),
                reason=capability.reason,
                data=data,
            )
        except Exception as exc:
            return TaiwanDataSection(
                dataset=dataset,
                status="error",
                usage_scope=capability.usage_scope,
                historically_eligible=False,
                reason=f"{type(exc).__name__}: {exc}",
            )


def get_taiwan_current_data_service() -> TaiwanCurrentDataService:
    return TaiwanCurrentDataService()

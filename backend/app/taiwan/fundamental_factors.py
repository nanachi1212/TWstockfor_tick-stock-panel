"""PIT-safe Taiwan company fundamental factor seam."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal

from app.taiwan.fundamentals import TaiwanFundamentalStore, latest_as_of

FactorStatus = Literal["available", "missing", "data_insufficient", "unsupported", "malformed"]

FACTOR_SPECS = {
    "eps": ("financial_statement", "cumulative_eps", "TWD_per_share"),
    "roe": ("financial_statement", None, "ratio"),
    "pe": ("valuation", "pe", "ratio"),
    "pb": ("valuation", "pb", "ratio"),
    "dividend_yield": ("valuation", "dividend_yield", "percent"),
}


@dataclass(frozen=True)
class FundamentalFactorResult:
    symbol: str
    factor: str
    value: float | None
    status: FactorStatus
    as_of_date: str | None
    available_at: datetime | None
    source: str | None
    provider: str | None
    revision_identity: str | None
    normalized_unit: str | None


@dataclass(frozen=True)
class FundamentalFactorCoverage:
    total_securities: int
    eligible_securities: int
    available_values: int
    missing_values: int
    data_insufficient: int
    unsupported: int
    malformed: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


class TaiwanVerifiedFundamentalFactors:
    """Derive factors only from revisions proven available before ``query_at``."""

    def __init__(self, store: TaiwanFundamentalStore) -> None:
        self.store = store

    def evaluate(
        self,
        symbol: str,
        factor: str,
        query_at: datetime,
        *,
        security_type: str = "stock",
    ) -> FundamentalFactorResult:
        if security_type == "etf" or factor not in FACTOR_SPECS:
            return self._result(symbol, factor, "unsupported")
        dataset, field, unit = FACTOR_SPECS[factor]
        records = [row for row in self.store.load() if row.symbol == symbol and row.dataset == dataset]
        if not records:
            return self._result(symbol, factor, "missing", unit=unit)
        record = latest_as_of(records, symbol, query_at, dataset=dataset)
        if record is None:
            return self._result(symbol, factor, "data_insufficient", unit=unit)
        values = record.values or {}
        if factor == "roe":
            numerator, denominator = values.get("net_income"), values.get("equity")
            if numerator is None or denominator is None:
                value = None
            elif denominator == 0:
                return self._from_record(record, factor, None, "malformed", unit)
            else:
                value = numerator / denominator
        else:
            value = values.get(field)
        if value is None:
            return self._from_record(record, factor, None, "missing", unit)
        try:
            number = float(value)
        except (TypeError, ValueError):
            return self._from_record(record, factor, None, "malformed", unit)
        if not math.isfinite(number):
            return self._from_record(record, factor, None, "malformed", unit)
        return self._from_record(record, factor, number, "available", unit)

    def rank(
        self,
        symbols: list[str],
        factor: str,
        query_at: datetime,
        *,
        security_types: dict[str, str] | None = None,
        descending: bool = True,
    ) -> tuple[list[FundamentalFactorResult], FundamentalFactorCoverage]:
        types = security_types or {}
        results = [
            self.evaluate(symbol, factor, query_at, security_type=types.get(symbol, "stock"))
            for symbol in symbols
        ]
        ranked = sorted(
            (row for row in results if row.status == "available"),
            key=lambda row: ((-row.value if descending else row.value), row.symbol),
        )
        counts = {status: sum(row.status == status for row in results) for status in (
            "available", "missing", "data_insufficient", "unsupported", "malformed"
        )}
        coverage = FundamentalFactorCoverage(
            total_securities=len(results),
            eligible_securities=len(results) - counts["unsupported"],
            available_values=counts["available"],
            missing_values=counts["missing"],
            data_insufficient=counts["data_insufficient"],
            unsupported=counts["unsupported"],
            malformed=counts["malformed"],
        )
        return ranked, coverage

    @staticmethod
    def _result(symbol: str, factor: str, status: FactorStatus, *, unit: str | None = None) -> FundamentalFactorResult:
        return FundamentalFactorResult(symbol, factor, None, status, None, None, None, None, None, unit)

    @staticmethod
    def _from_record(record, factor: str, value: float | None, status: FactorStatus, unit: str) -> FundamentalFactorResult:
        return FundamentalFactorResult(
            record.symbol, factor, value, status, record.period_end, record.available_at,
            record.source, record.provider, record.revision, unit,
        )

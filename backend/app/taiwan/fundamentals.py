"""Taiwan point-in-time fundamentals with strict availability semantics.

Official MOPS open data currently exposes a report date (``出表日期``), not a
verifiable publication time.  Such live records are retained with provenance but
``available_at=None`` and therefore cannot enter an as-of backtest.
"""
# ruff: noqa: RUF001 -- official field names contain full-width punctuation.
from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.taiwan.providers.taiwan_values import TAIPEI, parse_number, parse_taiwan_date

STATEMENT_CATEGORIES = ("ci", "basi", "bd", "fh", "ins", "mim")
NORMALIZED_STATUSES = {
    "official", "third_party", "third_party_fallback", "unsupported",
    "data_insufficient", "stale", "schema_changed", "error",
}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _datetime(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(value)
    if result is not None and result.tzinfo is None:
        raise ValueError("fundamental timestamps must be timezone-aware")
    return result


def thousand_twd(raw: object) -> int | None:
    value = parse_number(raw)
    return None if value is None else round(value * 1000)


def roc_month(raw: object) -> str:
    value = str(raw).strip()
    if len(value) not in (5, 6) or not value.isdigit():
        raise ValueError(f"invalid ROC month: {raw!r}")
    year, month = int(value[:-2]) + 1911, int(value[-2:])
    if not 1 <= month <= 12:
        raise ValueError(f"invalid ROC month: {raw!r}")
    return f"{year:04d}-{month:02d}"


@dataclass(frozen=True)
class FundamentalRecord:
    symbol: str
    dataset: str
    period_start: str | None
    period_end: str
    published_at: datetime | None
    available_at: datetime | None
    retrieved_at: datetime
    revision: str
    provider: str
    source: str
    source_url: str
    status: str
    normalized_unit: str
    raw_unit: str | None = None
    values: dict[str, Any] | None = None
    accounting_category: str | None = None
    statement_type: str = "unknown"

    def __post_init__(self) -> None:
        for value in (self.published_at, self.available_at, self.retrieved_at):
            _datetime(value)
        if self.status not in NORMALIZED_STATUSES:
            raise ValueError(f"invalid fundamental status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for name in ("published_at", "available_at", "retrieved_at"):
            result[name] = _iso(getattr(self, name))
        return result

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> FundamentalRecord:
        data = {field.name: raw.get(field.name) for field in fields(cls)}
        for name in ("published_at", "available_at", "retrieved_at"):
            data[name] = _datetime(data[name])
        return cls(**data)


@dataclass(frozen=True)
class ShareCapital:
    """Distinct capital concepts; absent official float data stays absent."""

    total_shares: int | None = None
    issued_shares: int | None = None
    float_shares: int | None = None
    capital_twd: int | None = None
    status: str = "data_insufficient"


def latest_as_of(
    records: Iterable[FundamentalRecord], symbol: str, query_at: datetime,
    *, dataset: str | None = None,
) -> FundamentalRecord | None:
    """Return the newest revision that was available strictly before query_at."""
    query_at = _datetime(query_at)
    eligible = [
        record for record in records
        if record.symbol == symbol
        and (dataset is None or record.dataset == dataset)
        and record.available_at is not None
        and query_at > record.available_at
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda row: (row.period_end, row.available_at, row.revision))


class TaiwanFundamentalStore:
    """Small append-only JSONL store; revisions are never overwritten."""

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "taiwan" / "fundamentals" / "records.jsonl"

    def load(self) -> list[FundamentalRecord]:
        if not self.path.exists():
            return []
        return [FundamentalRecord.from_dict(json.loads(line)) for line in self.path.read_text(encoding="utf-8").splitlines() if line]

    def save(self, records: Iterable[FundamentalRecord]) -> int:
        merged = {self._identity(row): row for row in self.load()}
        for row in records:
            merged[self._identity(row)] = row
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(json.dumps(row.to_dict(), ensure_ascii=False, sort_keys=True) for row in sorted(merged.values(), key=self._identity))
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(payload + ("\n" if payload else ""), encoding="utf-8")
        temporary.replace(self.path)
        return len(merged)

    def get_as_of(self, symbol: str, query_at: datetime, *, dataset: str | None = None) -> FundamentalRecord | None:
        return latest_as_of(self.load(), symbol, query_at, dataset=dataset)

    @staticmethod
    def _identity(row: FundamentalRecord) -> tuple[str, str, str, str, str]:
        return row.symbol, row.dataset, row.period_end, _iso(row.available_at) or "", row.revision


class TaiwanOfficialFundamentals:
    """Official TWSE/TPEx fundamentals feeding the Taiwan contract model."""

    TWSE = "https://openapi.twse.com.tw/v1"
    TPEX = "https://www.tpex.org.tw/openapi/v1"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(timeout=30.0)

    def monthly_revenue(self, symbol: str, exchange: str, *, security_type: str = "stock") -> FundamentalRecord:
        if security_type == "etf":
            return self._unsupported(symbol, "monthly_revenue")
        path = "opendata/t187ap05_L" if exchange == "TWSE" else "mopsfin_t187ap05_O"
        url, row, retrieved = self._company_row(symbol, exchange, path)
        values = {
            "revenue": thousand_twd(row.get("營業收入-當月營收")),
            "previous_month": thousand_twd(row.get("營業收入-上月營收")),
            "previous_year_month": thousand_twd(row.get("營業收入-去年當月營收")),
            "mom": parse_number(row.get("營業收入-上月比較增減(%)")),
            "yoy": parse_number(row.get("營業收入-去年同月增減(%)")),
            "cumulative": thousand_twd(row.get("累計營業收入-當月累計營收")),
        }
        period = roc_month(row.get("資料年月"))
        return self._record(symbol, "monthly_revenue", period, row, exchange, url, retrieved, values, "TWD", "thousand_TWD")

    def financial_statement(self, symbol: str, exchange: str, *, security_type: str = "stock") -> FundamentalRecord:
        if security_type == "etf":
            return self._unsupported(symbol, "financial_statement")
        base = "opendata/t187ap06_L_" if exchange == "TWSE" else "mopsfin_t187ap06_O_"
        balance = "opendata/t187ap07_L_" if exchange == "TWSE" else "mopsfin_t187ap07_O_"
        matches: list[tuple[str, str, dict[str, Any], datetime]] = []
        for category in STATEMENT_CATEGORIES:
            try:
                url, row, retrieved = self._company_row(symbol, exchange, base + category)
                matches.append((category, url, row, retrieved))
            except LookupError:
                continue
        if len(matches) != 1:
            raise LookupError(f"accounting category data_insufficient: matches={len(matches)}")
        category, income_url, income, retrieved = matches[0]
        balance_url, sheet, _ = self._company_row(symbol, exchange, balance + category)
        year = int(str(income.get("年度") or income.get("Year") or "").strip()) + 1911
        quarter = int(str(income.get("季別") or income.get("Season") or "").strip())
        if not 1 <= quarter <= 4:
            raise ValueError("invalid fiscal quarter")
        next_month = date(year + (quarter == 4), 1 if quarter == 4 else quarter * 3 + 1, 1)
        period_end = (next_month - timedelta(days=1)).isoformat()
        def amount(rows: dict[str, Any], *names: str) -> int | None:
            return thousand_twd(next((rows[name] for name in names if str(rows.get(name, "")).strip()), None))
        values = {
            "revenue": amount(income, "營業收入", "收益合計", "淨收益"),
            "operating_income": amount(income, "營業利益（損失）"),
            "net_income": amount(income, "本期淨利（淨損）", "本期稅後淨利（淨損）"),
            "cumulative_eps": parse_number(income.get("基本每股盈餘（元）")),
            "assets": amount(sheet, "資產總計", "資產總額"),
            "liabilities": amount(sheet, "負債總計", "負債總額"),
            "equity": amount(sheet, "權益總計", "權益總額"),
        }
        record = self._record(symbol, "financial_statement", period_end, income, exchange, income_url + " | " + balance_url, retrieved, values, "TWD", "thousand_TWD")
        return FundamentalRecord(**{**record.to_dict(), "published_at": record.published_at, "available_at": record.available_at, "retrieved_at": record.retrieved_at, "period_start": f"{year:04d}-01-01", "accounting_category": category})

    def valuation(self, symbol: str, exchange: str, *, security_type: str = "stock") -> FundamentalRecord:
        if security_type == "etf":
            return self._insufficient(symbol, "valuation", "official ETF valuation capability unverified")
        path = "exchangeReport/BWIBBU_ALL" if exchange == "TWSE" else "tpex_mainboard_peratio_analysis"
        url, row, retrieved = self._company_row(symbol, exchange, path)
        raw_date = row.get("Date") or row.get("日期")
        trade_date = parse_taiwan_date(str(raw_date)).isoformat()
        values = {
            "pe": parse_number(row.get("PEratio") or row.get("PriceEarningRatio")),
            "pb": parse_number(row.get("PBratio") or row.get("PriceBookRatio")),
            "dividend_yield": parse_number(row.get("DividendYield") or row.get("YieldRatio")),
        }
        return self._record(symbol, "valuation", trade_date, row, exchange, url, retrieved, values, "ratio", None)

    def dividends(self, symbol: str, exchange: str, *, security_type: str = "stock") -> list[FundamentalRecord]:
        path = "opendata/t187ap45_L" if exchange == "TWSE" else "mopsfin_t187ap39_O"
        url, rows, retrieved = self._rows(exchange, path)
        result = []
        for row in rows:
            code = str(row.get("公司代號") or row.get("SecuritiesCompanyCode") or "").strip()
            if code != symbol.split(".")[0]:
                continue
            year_raw = str(row.get("股利年度") or "").strip()
            year = int(year_raw) + (1911 if year_raw and int(year_raw) < 1911 else 0)
            raw_status = str(row.get("決議（擬議）進度") or "").strip()
            def total(*names: str, current: dict[str, Any] = row) -> float | None:
                values = [parse_number(current.get(name)) for name in names]
                present = [value for value in values if value is not None]
                return sum(present) if present else None
            values = {
                "cash_dividend": total("股東配發-盈餘分配之現金股利(元/股)", "股東配發內容-盈餘分配之現金股利(元/股)"),
                "stock_dividend": total("股東配發-盈餘轉增資配股(元/股)", "股東配發內容-盈餘轉增資配股(元/股)"),
                "ex_date": None, "record_date": None, "payment_date": None,
                "raw_status": raw_status, "normalized_status": normalize_dividend_status(raw_status),
            }
            result.append(self._record(symbol, "dividend", f"{year:04d}-12-31", row, exchange, url, retrieved, values, "TWD_per_share", None))
        return result

    def _record(self, symbol: str, dataset: str, period_end: str, raw: dict[str, Any], exchange: str, url: str, retrieved: datetime, values: dict[str, Any], unit: str, raw_unit: str | None) -> FundamentalRecord:
        revision = str(raw.get("出表日期") or raw.get("Date") or "").strip()
        # Date-only metadata cannot prove when during that date the record became available.
        return FundamentalRecord(symbol=symbol, dataset=dataset, period_start=None, period_end=period_end, published_at=None, available_at=None, retrieved_at=retrieved, revision=revision, provider="TWSE" if exchange == "TWSE" else "TPEx", source=f"{exchange.lower()}:{dataset}", source_url=url, status="data_insufficient", normalized_unit=unit, raw_unit=raw_unit, values=values)

    def _unsupported(self, symbol: str, dataset: str) -> FundamentalRecord:
        now = datetime.now(TAIPEI)
        return FundamentalRecord(symbol, dataset, None, "", None, None, now, "", "Taiwan", "taiwan:capability", "", "unsupported", "unavailable")

    def _insufficient(self, symbol: str, dataset: str, reason: str) -> FundamentalRecord:
        now = datetime.now(TAIPEI)
        return FundamentalRecord(symbol, dataset, None, "", None, None, now, "", "Taiwan", "taiwan:capability", "", "data_insufficient", "unavailable", values={"reason": reason})

    def _company_row(self, symbol: str, exchange: str, path: str) -> tuple[str, dict[str, Any], datetime]:
        url, rows, retrieved = self._rows(exchange, path)
        for row in rows:
            code = str(row.get("公司代號") or row.get("SecuritiesCompanyCode") or row.get("Code") or "").strip()
            if code == symbol.split(".")[0]:
                return url, row, retrieved
        raise LookupError(f"official {path} row not found for {symbol}")

    def _rows(self, exchange: str, path: str) -> tuple[str, list[dict[str, Any]], datetime]:
        url = f"{self.TWSE if exchange == 'TWSE' else self.TPEX}/{path}"
        response = self.client.get(url)
        response.raise_for_status()
        retrieved = datetime.now(TAIPEI)
        rows = response.json()
        if not isinstance(rows, list):
            raise ValueError(f"official {path} schema changed")
        return url, rows, retrieved


def normalize_dividend_status(raw: str) -> str:
    if "股東會" in raw and "通過" in raw:
        return "shareholder_approved"
    if "董事會" in raw and ("決議" in raw or "通過" in raw):
        return "board_approved"
    if "擬議" in raw:
        return "board_proposed"
    if "除息" in raw or "除權" in raw:
        return "ex_date_announced"
    if "發放" in raw or "已付" in raw:
        return "paid"
    return "unknown"

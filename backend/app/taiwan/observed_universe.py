"""A2a — Historical Observed Membership Census (market truth, no classification).

What this records
-----------------
For every trading session it records **which securities the official daily
snapshot actually contained**.  That is an *observation*, nothing more:

* ``observed = true``  →  this code appeared in the official snapshot that day.
* no row              →  it did not appear.

It is deliberately **not** a listing status.  ``docs/taiwan-historical-universe-probe.md``
§2.1 has the counter-example: 2358 廷鑫 and 2443 昶虹 were absent from the
2024-06-03 snapshot months before their 2024-11-19 delisting, because trading
had already been suspended.  A security can be listed and untraded.  Therefore
this module never writes ``listing_status``, ``delisting_date``,
``transition_type`` — and never guesses ``instrument_type``.

Why a separate store
--------------------
``OfficialDailySnapshotAdapter`` filters every row through the **current**
Security Master allowlist, so historical delisted securities are dropped
(probe §2: 8/8 samples).  That behaviour is correct for the live product and is
left completely untouched — this module does not import or modify it.

The census also does not write into ``TaiwanDailyStore``.  15 modules consume
that store (screener, technical_indicators, research_context,
market_intelligence, watchlist_enrichment, …) and every one of them treats a
row as a tradable, classified Taiwan stock/ETF.  A TPEx session carries ~10,900
warrants whose type cannot be verified (probe §9.4), so writing census rows
there would silently poison all of them.  Staging storage it is.

Layout
------
``<taiwan_data_root>/observed_universe/exchange=<EX>/date=<YYYY-MM-DD>/part.parquet``

The **existence of a partition file is the completion marker** — that is the
whole resume mechanism.  An official "no rows" response writes an *empty*
partition, which is terminal for processing but does not prove a holiday.
Only an independently verified calendar may confirm a non-trading date; that
evidence is kept in the same atomic Parquet partition.  A transport/HTTP
failure writes nothing, so the date is simply retried.

Requests
--------
One request per exchange per session, from the two independently throttled
buckets (``taiwan:twse`` / ``taiwan:tpex``):

* TWSE ``MI_INDEX?type=ALLBUT0999`` — every security except warrants/CBBCs.
* TPEx ``dailyQuotes`` — both official tables (上櫃股票行情 and 管理股票).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import polars as pl
import pyarrow.parquet as pq

from app.taiwan.providers.http import DEFAULT_USER_AGENT, taiwan_client
from app.taiwan.providers.taiwan_values import TAIPEI, parse_number
from app.taiwan.realtime.calendar import TaiwanTradingCalendar

logger = logging.getLogger(__name__)

TWSE_MI_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TPEX_DAILY_QUOTES_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"

#: Everything except warrants / CBBCs.  Chosen over ``type=ALL`` (39k rows/day,
#: almost all warrants) and over the 31-industry sweep (32 requests/day) — see
#: probe §11 plan C.
TWSE_CENSUS_TYPE = "ALLBUT0999"

TWSE_SOURCE = f"twse:MI_INDEX:{TWSE_CENSUS_TYPE}"
TPEX_SOURCE = "tpex:dailyQuotes"

CENSUS_COLUMNS: list[str] = [
    "date", "raw_code", "exchange", "observed",
    "raw_name", "raw_source_category",
    "open", "high", "low", "close", "volume", "amount",
    "instrument_type", "instrument_type_status",
    "source", "retrieved_at",
]

#: A2a never classifies.  Both TWSE and TPEx rows leave here unclassified;
#: A2b fills TWSE in from the official historical industry/ETF tables.
UNCLASSIFIED_STATUS = "data_insufficient"

_CENSUS_SCHEMA: dict[str, Any] = {
    "date": pl.Date,
    "raw_code": pl.Utf8,
    "exchange": pl.Utf8,
    "observed": pl.Boolean,
    "raw_name": pl.Utf8,
    "raw_source_category": pl.Utf8,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "amount": pl.Float64,
    "instrument_type": pl.Utf8,
    "instrument_type_status": pl.Utf8,
    "source": pl.Utf8,
    "retrieved_at": pl.Utf8,
}

_MISSING = {"", "-", "--", "---", "----", "N/A", "null", "None"}
_MARKET_STATUS_KEY = b"taiwan_census_market_status"
_CONFIRMATION_SOURCE_KEY = b"taiwan_census_confirmation_source"
_CONFIRMED_NON_TRADING = b"confirmed_non_trading"


def _num(raw: object) -> float | None:
    """Parse an official price/quantity cell; official blanks become None."""
    if raw is None:
        return None
    text = str(raw).strip().rstrip("*").strip()
    if text in _MISSING:
        return None
    try:
        return parse_number(text)
    except ValueError:
        return None


# ── Storage ────────────────────────────────────────────────────

class ObservedUniverseStore:
    """Parquet staging store for observed market membership.

    Partition completion is file existence; there is no manifest.
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        if data_dir is None:
            from app.taiwan.data_root import taiwan_data_root

            data_dir = taiwan_data_root() / "observed_universe"
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def partition_path(self, exchange: str, day: date) -> Path:
        return self._data_dir / f"exchange={exchange}" / f"date={day.isoformat()}" / "part.parquet"

    def has(self, exchange: str, day: date) -> bool:
        """True once the session is terminal — including an official no-data day."""
        return self.partition_path(exchange, day).exists()

    def write(
        self,
        exchange: str,
        day: date,
        rows: list[dict[str, Any]],
        *,
        confirmed_non_trading_source: str | None = None,
    ) -> int:
        """Atomically write observations; only explicit evidence marks a closure."""
        if confirmed_non_trading_source is not None and not confirmed_non_trading_source:
            raise ValueError("non-trading confirmation requires a source")
        if rows and confirmed_non_trading_source is not None:
            raise ValueError("an observed session cannot be confirmed non-trading")
        frame = (
            pl.DataFrame(rows, schema=_CENSUS_SCHEMA)
            if rows else pl.DataFrame(schema=_CENSUS_SCHEMA)
        )
        if frame.height:
            frame = frame.unique(subset=["raw_code"], keep="last").sort("raw_code")
        path = self.partition_path(exchange, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        os.close(handle)
        try:
            if confirmed_non_trading_source is None:
                frame.write_parquet(temporary)
            else:
                # PyArrow's file metadata travels with the same atomic partition.
                # Missing metadata on old/unknown empty partitions fails closed.
                table = frame.to_arrow().replace_schema_metadata({
                    _MARKET_STATUS_KEY: _CONFIRMED_NON_TRADING,
                    _CONFIRMATION_SOURCE_KEY: confirmed_non_trading_source.encode("utf-8"),
                })
                pq.write_table(table, temporary, compression="zstd")
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return frame.height

    def read(self, exchange: str | None = None) -> pl.DataFrame:
        pattern = f"exchange={exchange}" if exchange else "exchange=*"
        files = sorted(self._data_dir.glob(f"{pattern}/date=*/part.parquet"))
        frames = [pl.read_parquet(f) for f in files]
        frames = [f for f in frames if f.height]
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame(schema=_CENSUS_SCHEMA)

    def completed_dates(self, exchange: str) -> set[date]:
        root = self._data_dir / f"exchange={exchange}"
        if not root.exists():
            return set()
        return {
            date.fromisoformat(p.name.removeprefix("date="))
            for p in root.iterdir()
            if p.name.startswith("date=") and (p / "part.parquet").exists()
        }

    def session_dates(self, exchange: str) -> set[date]:
        """Completed dates that actually contained observations."""
        return {day for day, status in self.partition_statuses(exchange).items()
                if status == "observed"}

    def confirmed_non_trading_dates(self, exchange: str) -> set[date]:
        """Only empty partitions with recorded verification count as closures."""
        return {day for day, status in self.partition_statuses(exchange).items()
                if status == "confirmed_non_trading"}

    def partition_statuses(self, exchange: str) -> dict[date, str]:
        """Read Parquet footers only; legacy empty partitions remain unresolved."""
        return {day: self.partition_status(exchange, day)
                for day in self.completed_dates(exchange)}

    def partition_status(self, exchange: str, day: date) -> str:
        """Return observed, confirmed_non_trading, or empty_unknown."""
        footer = pq.read_metadata(self.partition_path(exchange, day))
        metadata = footer.metadata or {}
        if footer.num_rows:
            return "observed"
        if (metadata.get(_MARKET_STATUS_KEY) == _CONFIRMED_NON_TRADING
                and metadata.get(_CONFIRMATION_SOURCE_KEY)):
            return "confirmed_non_trading"
        return "empty_unknown"


@dataclass(frozen=True)
class CensusCoverage:
    """Processing progress and observed trading coverage for one exchange."""

    candidate_dates: int
    processed_dates: int
    observed_trading_sessions: int
    confirmed_non_trading_dates: int
    unknown_empty_dates: int
    unresolved_dates: int
    expected_trading_sessions: int
    processed_ratio: float
    trading_coverage_ratio: float

    def describe(self) -> dict[str, int | float]:
        return {
            "candidate_dates": self.candidate_dates,
            "processed_dates": self.processed_dates,
            "observed_trading_sessions": self.observed_trading_sessions,
            "confirmed_non_trading_dates": self.confirmed_non_trading_dates,
            "unknown_empty_dates": self.unknown_empty_dates,
            "unresolved_dates": self.unresolved_dates,
            "expected_trading_sessions": self.expected_trading_sessions,
            "processed_ratio": self.processed_ratio,
            "trading_coverage_ratio": self.trading_coverage_ratio,
        }


def census_coverage(
    store: ObservedUniverseStore,
    exchange: str,
    candidates: set[date],
    *,
    partition_statuses: dict[date, str] | None = None,
) -> CensusCoverage:
    """Keep unprocessed and unverified empty weekdays in the denominator."""
    all_statuses = partition_statuses if partition_statuses is not None else store.partition_statuses(exchange)
    statuses = {day: status for day, status in all_statuses.items()
                if day in candidates}
    observed = sum(status == "observed" for status in statuses.values())
    confirmed = sum(status == "confirmed_non_trading" for status in statuses.values())
    unknown = sum(status == "empty_unknown" for status in statuses.values())
    expected = len(candidates) - confirmed
    return CensusCoverage(
        candidate_dates=len(candidates),
        processed_dates=len(statuses),
        observed_trading_sessions=observed,
        confirmed_non_trading_dates=confirmed,
        unknown_empty_dates=unknown,
        unresolved_dates=len(candidates) - observed - confirmed,
        expected_trading_sessions=expected,
        processed_ratio=len(statuses) / len(candidates) if candidates else 0.0,
        trading_coverage_ratio=observed / expected if expected else 0.0,
    )


# ── Parsing (no allowlist, no classification) ──────────────────

def _base_row(day: date, code: str, exchange: str, name: str | None,
              category: str, source: str, retrieved_at: str) -> dict[str, Any]:
    return {
        "date": day,
        "raw_code": code,
        "exchange": exchange,
        "observed": True,
        "raw_name": name,
        "raw_source_category": category,
        "open": None, "high": None, "low": None,
        "close": None, "volume": None, "amount": None,
        # A2a classifies nothing.  Not "stock because the code has 4 digits".
        "instrument_type": None,
        "instrument_type_status": UNCLASSIFIED_STATUS,
        "source": source,
        "retrieved_at": retrieved_at,
    }


def parse_twse_census(payload: dict[str, Any], day: date, retrieved_at: str) -> list[dict[str, Any]]:
    """Parse ``MI_INDEX?type=ALLBUT0999`` into observations.

    Returns ``[]`` for an official no-data response.  Its cause is not proven
    by an empty result alone.
    """
    if str(payload.get("stat", "")).strip() != "OK":
        return []
    table = next(
        (t for t in payload.get("tables") or []
         if "每日收盤行情" in str(t.get("title") or "")),
        None,
    )
    if table is None:
        return []
    fields = list(table.get("fields") or [])
    try:
        i_code = fields.index("證券代號")
        i_name = fields.index("證券名稱")
        i_vol = fields.index("成交股數")
        i_amt = fields.index("成交金額")
        i_open = fields.index("開盤價")
        i_high = fields.index("最高價")
        i_low = fields.index("最低價")
        i_close = fields.index("收盤價")
    except ValueError as exc:
        raise ValueError(f"TWSE MI_INDEX schema changed on {day}: {exc}; fields={fields}") from exc

    category = str(table.get("title") or "")
    rows: list[dict[str, Any]] = []
    for raw in table.get("data") or []:
        if len(raw) <= max(i_code, i_name, i_vol, i_amt, i_open, i_high, i_low, i_close):
            continue
        code = str(raw[i_code]).strip()
        if not code:
            continue
        row = _base_row(day, code, "TWSE", str(raw[i_name]).strip() or None,
                        category, TWSE_SOURCE, retrieved_at)
        row.update({
            "open": _num(raw[i_open]), "high": _num(raw[i_high]),
            "low": _num(raw[i_low]), "close": _num(raw[i_close]),
            "volume": _num(raw[i_vol]), "amount": _num(raw[i_amt]),
        })
        rows.append(row)
    return rows


def parse_tpex_census(payload: dict[str, Any], day: date, retrieved_at: str) -> list[dict[str, Any]]:
    """Parse TPEx ``dailyQuotes`` into observations.

    Both official tables are kept and tagged by their own title
    (上櫃股票行情 / 管理股票).  The title is recorded as provenance only — the
    probe (§4.2) showed ETFs sit inside 上櫃股票行情, so it is not a type signal.
    """
    rows: list[dict[str, Any]] = []
    for table in payload.get("tables") or []:
        fields = list(table.get("fields") or [])
        data = table.get("data") or []
        if not data:
            continue
        try:
            i_code = fields.index("代號")
            i_name = fields.index("名稱")
            i_close = fields.index("收盤")
            i_open = fields.index("開盤")
            i_high = fields.index("最高")
            i_low = fields.index("最低")
            i_vol = fields.index("成交股數")
            i_amt = fields.index("成交金額(元)")
        except ValueError as exc:
            raise ValueError(f"TPEx dailyQuotes schema changed on {day}: {exc}; fields={fields}") from exc

        category = str(table.get("title") or "")
        for raw in data:
            if len(raw) <= max(i_code, i_name, i_vol, i_amt, i_open, i_high, i_low, i_close):
                continue
            code = str(raw[i_code]).strip()
            if not code:
                continue
            row = _base_row(day, code, "TPEX", str(raw[i_name]).strip() or None,
                            category, TPEX_SOURCE, retrieved_at)
            row.update({
                "open": _num(raw[i_open]), "high": _num(raw[i_high]),
                "low": _num(raw[i_low]), "close": _num(raw[i_close]),
                "volume": _num(raw[i_vol]), "amount": _num(raw[i_amt]),
            })
            rows.append(row)
    return rows


# ── Census run ─────────────────────────────────────────────────

def candidate_sessions(start: date, end: date,
                       calendar: TaiwanTradingCalendar | None = None) -> Iterator[date]:
    """Yield dates that are not *confirmed* non-trading days.

    ``is_trading_day`` returns None for an unverified weekday; those are still
    probed, because only the official response can settle it.
    """
    cal = calendar or TaiwanTradingCalendar()
    day = start
    while day <= end:
        if cal.is_trading_day(day) is not False:
            yield day
        day += timedelta(days=1)


class ObservedUniverseCensus:
    """Runs the A2a census. Resumable, idempotent, rate-limited."""

    def __init__(self, store: ObservedUniverseStore | None = None,
                 calendar: TaiwanTradingCalendar | None = None,
                 client: Any = None, timeout: float = 60.0) -> None:
        self.store = store or ObservedUniverseStore()
        self.calendar = calendar or TaiwanTradingCalendar()
        self._client = client or taiwan_client(
            timeout=timeout, headers={"User-Agent": DEFAULT_USER_AGENT})
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _json(self, url: str, attempts: int = 3) -> dict[str, Any]:
        """GET + parse, retrying transport-level flakiness a bounded number of times.

        TPEx regularly drops large responses mid-body (observed as WinError
        10054 / incomplete chunked read).  Each attempt takes its own rate-limit
        slot, so a retry can never outrun the bucket.
        """
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = self._client.get(url)
                response.raise_for_status()
                return json.loads(response.content.decode("utf-8-sig"))
            except (httpx.TransportError, json.JSONDecodeError) as exc:
                last = exc
                logger.debug("transient fetch error (%d/%d) on %s: %s",
                             attempt, attempts, url, exc)
        raise last if last else RuntimeError(f"no response for {url}")

    def fetch_twse(self, day: date) -> list[dict[str, Any]]:
        url = (f"{TWSE_MI_INDEX_URL}?date={day:%Y%m%d}"
               f"&type={TWSE_CENSUS_TYPE}&response=json")
        return parse_twse_census(self._json(url), day, datetime.now(TAIPEI).isoformat())

    def fetch_tpex(self, day: date) -> list[dict[str, Any]]:
        roc = f"{day.year - 1911}/{day.month:02d}/{day.day:02d}"
        url = f"{TPEX_DAILY_QUOTES_URL}?date={roc}&response=json"
        return parse_tpex_census(self._json(url), day, datetime.now(TAIPEI).isoformat())


# ── Reporting ──────────────────────────────────────────────────

def first_observed_dates(store: ObservedUniverseStore, exchange: str) -> dict[str, date]:
    """Earliest observed session per code — the input to A2b classification."""
    frame = store.read(exchange)
    if frame.is_empty():
        return {}
    grouped = frame.group_by("raw_code").agg(pl.col("date").min().alias("first_observed"))
    return {row["raw_code"]: row["first_observed"] for row in grouped.iter_rows(named=True)}

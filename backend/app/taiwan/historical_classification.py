"""A2b — point-in-time TWSE instrument-type classification.

What this establishes
---------------------
For a given historical session, which TWSE codes were **common stocks** and
which were **ETFs**, using only that date's official TWSE response.

Membership is point-in-time and was verified as such
(``docs/taiwan-historical-universe-probe.md`` §4.3): on 2015-01-05 the union of
the industry tables is 855 codes and ``ALLBUT0999`` is 911, with **zero** codes
in the union that are absent from ``ALLBUT0999``.  TWSE does not inject
not-yet-listed companies into a historical response.

What this deliberately does NOT establish
-----------------------------------------
**Industry.**  The same probe showed the industry *label* is the current
classification, not the historical one: a 2015-01-05 query returns
``35 綠能環保`` / ``36 數位雲端`` / ``37 運動休閒`` / ``38 居家生活``, TWSE
categories that only exist from 2021, and 435 of the 855 codes appear in more
than one industry table (``1701 → 07 化學生技醫療`` *and* ``22 生技醫療業``,
because 07 is the legacy umbrella later split into 21/22).

So the industry tables are used **only as a membership oracle** — "did TWSE
file this code under some industry on this date?" — never as an industry value.
Every record carries ``industry=None`` and ``industry_status="data_insufficient"``.

Fail-closed
-----------
A code that appears in no industry table and no ETF table is **not** classified.
It gets no record, so it can never reach the Primary verified universe.  There
is no format heuristic anywhere in this module.

TPEx
----
Out of scope by decision: TPEx historical instrument_type is BLOCKED (probe
§9.4).  This module refuses any exchange other than TWSE.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from app.taiwan.observed_universe import ObservedUniverseStore
from app.taiwan.providers.http import DEFAULT_USER_AGENT, taiwan_client
from app.taiwan.providers.taiwan_values import TAIPEI

logger = logging.getLogger(__name__)

TWSE_MI_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"

#: The 34 industry type codes TWSE actually serves (probe §4.3).  Codes 19, 32,
#: 33, 39, 40 return no table.  Sweeping the full 01..40 range would cost 6 more
#: requests per date for nothing; this list is the measured set.
TWSE_INDUSTRY_TYPES: tuple[str, ...] = (
    "01", "02", "03", "04", "05", "06", "07", "08", "09", "10",
    "11", "12", "13", "14", "15", "16", "17", "18", "20", "21",
    "22", "23", "24", "25", "26", "27", "28", "29", "30", "31",
    "35", "36", "37", "38",
)
TWSE_ETF_TYPE = "0099P"

#: Requests consumed to classify one session.
REQUESTS_PER_DATE = len(TWSE_INDUSTRY_TYPES) + 1

CLASSIFICATION_COLUMNS: list[str] = [
    "code", "exchange", "instrument_type",
    "industry", "industry_status",
    "classification_effective_from", "classification_source", "classification_status",
    "retrieved_at",
]

_SCHEMA: dict[str, Any] = {
    "code": pl.Utf8,
    "exchange": pl.Utf8,
    "instrument_type": pl.Utf8,
    "industry": pl.Utf8,
    "industry_status": pl.Utf8,
    "classification_effective_from": pl.Date,
    "classification_source": pl.Utf8,
    "classification_status": pl.Utf8,
    "retrieved_at": pl.Utf8,
}

#: Only these may enter the Primary verified stock universe.
VERIFIED_STOCK_TYPES = frozenset({"stock"})


class HistoricalClassificationStore:
    """Parquet store of point-in-time TWSE instrument types, keyed by session."""

    def __init__(self, data_dir: Path | None = None) -> None:
        if data_dir is None:
            from app.taiwan.data_root import taiwan_data_root

            data_dir = taiwan_data_root() / "historical_classification"
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def partition_path(self, day: date) -> Path:
        return self._data_dir / f"date={day.isoformat()}" / "part.parquet"

    def has(self, day: date) -> bool:
        return self.partition_path(day).exists()

    def write(self, day: date, rows: list[dict[str, Any]]) -> int:
        frame = pl.DataFrame(rows, schema=_SCHEMA) if rows else pl.DataFrame(schema=_SCHEMA)
        if frame.height:
            frame = frame.unique(subset=["code"], keep="last").sort("code")
        path = self.partition_path(day)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        os.close(handle)
        try:
            frame.write_parquet(temporary)
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return frame.height

    def completed_dates(self) -> set[date]:
        return {
            date.fromisoformat(p.name.removeprefix("date="))
            for p in self._data_dir.iterdir()
            if p.is_dir() and p.name.startswith("date=") and (p / "part.parquet").exists()
        } if self._data_dir.exists() else set()

    def read(self) -> pl.DataFrame:
        files = sorted(self._data_dir.glob("date=*/part.parquet"))
        frames = [f for f in (pl.read_parquet(p) for p in files) if f.height]
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame(schema=_SCHEMA)


def classification_queue(
    census: ObservedUniverseStore,
    store: HistoricalClassificationStore,
) -> list[date]:
    """Sessions that still need a classification sweep.

    One sweep per *distinct first-observed date*: classifying that session
    covers every TWSE code first seen on it.  Already-swept dates are skipped,
    which is what makes repeated runs cheap and idempotent.
    """
    from app.taiwan.observed_universe import first_observed_dates

    first_seen = first_observed_dates(census, "TWSE")
    wanted = sorted(set(first_seen.values()))
    done = store.completed_dates()
    return [day for day in wanted if day not in done]


class TwseHistoricalClassifier:
    """Fetches one session's official type tables and derives instrument_type."""

    def __init__(self, store: HistoricalClassificationStore | None = None,
                 client: Any = None, timeout: float = 60.0) -> None:
        self.store = store or HistoricalClassificationStore()
        self._client = client or taiwan_client(
            timeout=timeout, headers={"User-Agent": DEFAULT_USER_AGENT})
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _codes(self, day: date, type_code: str) -> set[str]:
        url = f"{TWSE_MI_INDEX_URL}?date={day:%Y%m%d}&type={type_code}&response=json"
        response = self._client.get(url)
        response.raise_for_status()
        payload = json.loads(response.content.decode("utf-8-sig"))
        if str(payload.get("stat", "")).strip() != "OK":
            return set()
        table = next(
            (t for t in payload.get("tables") or []
             if "每日收盤行情" in str(t.get("title") or "")),
            None,
        )
        if table is None:
            return set()
        return {
            str(row[0]).strip()
            for row in table.get("data") or []
            if row and str(row[0]).strip()
        }

    def classify_date(self, day: date) -> list[dict[str, Any]]:
        """Classify one session. Costs ``REQUESTS_PER_DATE`` requests."""
        stock_codes: set[str] = set()
        for type_code in TWSE_INDUSTRY_TYPES:
            stock_codes |= self._codes(day, type_code)
        etf_codes = self._codes(day, TWSE_ETF_TYPE)
        # A code in both is an ETF: the industry tables carry the legacy
        # umbrella listings, the ETF table is the specific one.
        stock_codes -= etf_codes

        retrieved = datetime.now(TAIPEI).isoformat()
        rows: list[dict[str, Any]] = []
        for codes, kind, source_type in (
            (sorted(stock_codes), "stock", "industry_tables"),
            (sorted(etf_codes), "etf", TWSE_ETF_TYPE),
        ):
            for code in codes:
                rows.append({
                    "code": code,
                    "exchange": "TWSE",
                    "instrument_type": kind,
                    # Never a value: the official label is current, not PIT,
                    # and not unique (probe §4.3).
                    "industry": None,
                    "industry_status": "data_insufficient",
                    "classification_effective_from": day,
                    "classification_source": f"twse:MI_INDEX:{source_type}@{day.isoformat()}",
                    "classification_status": "verified",
                    "retrieved_at": retrieved,
                })
        return rows

    def run(self, queue: list[date], *, request_budget: int,
            should_stop: Callable[[], bool] | None = None) -> dict[str, Any]:
        """Work the queue until the request budget or a stop signal is hit.

        ``request_budget <= 0`` means unlimited (LongRun): drain the queue.
        The rate limiter still paces every request; unlimited only removes the
        per-run cap, never the throttle.
        """
        unlimited = request_budget <= 0
        stats: dict[str, Any] = {
            "requests_used": 0, "dates_done": 0, "rows": 0,
            "failed_dates": [], "stopped_early": False,
        }
        for day in queue:
            if should_stop is not None and should_stop():
                stats["stopped_early"] = True
                break
            if not unlimited and stats["requests_used"] + REQUESTS_PER_DATE > request_budget:
                stats["stopped_early"] = True
                break
            try:
                rows = self.classify_date(day)
            except Exception as exc:
                logger.warning("classification failed for %s: %s", day, exc)
                stats["failed_dates"].append({"date": day.isoformat(), "error": str(exc)})
                stats["requests_used"] += REQUESTS_PER_DATE
                continue
            stats["rows"] += self.store.write(day, rows)
            stats["requests_used"] += REQUESTS_PER_DATE
            stats["dates_done"] += 1
        return stats


def verified_stock_codes(store: HistoricalClassificationStore) -> pl.DataFrame:
    """Codes verified as common stocks, with the session that established it."""
    frame = store.read()
    if frame.is_empty():
        return frame
    return frame.filter(pl.col("instrument_type").is_in(list(VERIFIED_STOCK_TYPES)))

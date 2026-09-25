"""A2b — point-in-time TWSE instrument-type classification.

What this establishes
---------------------
For a historical session, which TWSE securities have a verified specific type.
ETF, TDR and beneficiary tables are specific. Industry tables prove equity
membership but NOT common-stock subtype (probe §3 contradicts §4.3). They must
stay unresolved for V1 until an authoritative common/preferred source exists.

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
A code absent from every queried historical type table is **not** classified.
It gets no record, so it can never reach the Primary verified universe. There
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
import pyarrow.parquet as pq

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

# Verified with historical 2015-01-05 responses, not symbol/name heuristics.
# Other unsupported categories remain unknown until their source is audited.
TWSE_UNSUPPORTED_TYPES = {"019919T": "beneficiary_security", "9299": "tdr"}
VERIFIED_UNSUPPORTED_TYPES = frozenset({
    *TWSE_UNSUPPORTED_TYPES.values(), "preferred_share", "etn", "bond", "closed_end_fund",
})
_CONTRACT_KEY = b"taiwan_classification_contract"
_CONTRACT_VERSION = b"2"

#: Requests consumed to classify one session.
REQUESTS_PER_DATE = len(TWSE_INDUSTRY_TYPES) + 1 + len(TWSE_UNSUPPORTED_TYPES)

CLASSIFICATION_COLUMNS: list[str] = [
    "code", "exchange", "instrument_type",
    "industry", "industry_status",
    "classification_effective_from", "classification_source", "classification_status",
    "retrieved_at",
    "instrument_type_status", "primary_oos_eligible_type",
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
    "instrument_type_status": pl.Utf8,
    "primary_oos_eligible_type": pl.Boolean,
}


def _with_type_contract(frame: pl.DataFrame) -> pl.DataFrame:
    """Additive read compatibility; never rewrite old partitions on read."""
    # Old A2b partitions claimed all industry members were ordinary shares.
    # The same audit §3 records preferred shares in that union. Do not inherit
    # this unsupported claim, nor use code/name/current-master filters to fix it.
    industry_only = pl.col("classification_source").str.starts_with(
        "twse:MI_INDEX:industry_tables@").fill_null(False)
    frame = frame.with_columns(
        pl.when(industry_only).then(None).otherwise(pl.col("instrument_type")).alias("instrument_type"),
        pl.when(industry_only).then(pl.lit("data_insufficient"))
        .otherwise(pl.col("classification_status")).alias("classification_status"),
    )
    verified = ((pl.col("classification_status") == "verified")
                & pl.col("instrument_type").is_in(["stock", "etf", *sorted(VERIFIED_UNSUPPORTED_TYPES)]))
    return frame.with_columns(
        pl.when(verified).then(pl.lit("verified")).otherwise(pl.lit("data_insufficient"))
        .alias("instrument_type_status"),
        (verified & (pl.col("instrument_type") == "stock")).fill_null(False)
        .alias("primary_oos_eligible_type"),
    )

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
            conflicts = frame.group_by("code").agg(
                pl.struct("instrument_type", "classification_status").n_unique().alias("variants")
            ).filter(pl.col("variants") > 1)["code"].to_list()
            frame = frame.with_columns(
                pl.when(pl.col("code").is_in(conflicts)).then(None)
                .otherwise(pl.col("instrument_type")).alias("instrument_type"),
                pl.when(pl.col("code").is_in(conflicts)).then(pl.lit("data_insufficient"))
                .otherwise(pl.col("classification_status")).alias("classification_status"),
            )
        frame = _with_type_contract(frame)
        if frame.height:
            frame = frame.unique(subset=["code"], keep="last").sort("code")
        path = self.partition_path(day)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        os.close(handle)
        try:
            pq.write_table(frame.to_arrow().replace_schema_metadata(
                {_CONTRACT_KEY: _CONTRACT_VERSION}), temporary, compression="zstd")
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return frame.height

    def merge(self, day: date, rows: list[dict[str, Any]]) -> int:
        """Replace the rows of the given codes in an existing partition, keep the rest."""
        path = self.partition_path(day)
        kept = (pl.read_parquet(path).filter(~pl.col("code").is_in([r["code"] for r in rows]))
                if path.exists() else pl.DataFrame(schema=_SCHEMA))
        return self.write(day, [*kept.select(list(_SCHEMA)).to_dicts(), *rows])

    def completed_dates(self) -> set[date]:
        return {
            date.fromisoformat(p.name.removeprefix("date="))
            for p in self._data_dir.iterdir()
            if p.is_dir() and p.name.startswith("date=") and (p / "part.parquet").exists()
        } if self._data_dir.exists() else set()

    def read(self) -> pl.DataFrame:
        files = sorted(self._data_dir.glob("date=*/part.parquet"))
        frames = [f for f in (pl.read_parquet(p) for p in files) if f.height]
        frame = pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame(schema=_SCHEMA)
        return _with_type_contract(frame)

    def needs_upgrade(self, day: date) -> bool:
        metadata = pq.read_metadata(self.partition_path(day)).metadata or {}
        return metadata.get(_CONTRACT_KEY) != _CONTRACT_VERSION


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
    return [day for day in wanted if day not in done or store.needs_upgrade(day)]


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
        if payload.get("date") and payload["date"] != day.strftime("%Y%m%d"):
            raise ValueError("TWSE classification response date mismatch")
        stat = str(payload.get("stat", "")).strip()
        if stat != "OK":
            if stat == "很抱歉，沒有符合條件的資料!":  # noqa: RUF001 -- Official status.
                return set()
            raise ValueError(f"TWSE classification provider error: {stat or 'missing status'}")
        table = next(
            (t for t in payload.get("tables") or []
             if "每日收盤行情" in str(t.get("title") or "")),
            None,
        )
        if table is None:
            raise ValueError("TWSE classification missing market table")
        fields = table.get("fields") or []
        if "證券代號" not in fields:
            raise ValueError("TWSE classification missing security code field")
        index = fields.index("證券代號")
        rows = table.get("data") or []
        if any(len(row) <= index or not str(row[index]).strip() for row in rows):
            raise ValueError("TWSE classification malformed security row")
        return {
            str(row[index]).strip() for row in rows
        }

    def codes_in_table(self, day: date, type_code: str) -> set[str]:
        """Codes in one official historical MI_INDEX table (one request)."""
        return self._codes(day, type_code)

    def classify_date(self, day: date) -> list[dict[str, Any]]:
        """Classify one session. Costs ``REQUESTS_PER_DATE`` requests."""
        stock_codes: set[str] = set()
        for type_code in TWSE_INDUSTRY_TYPES:
            stock_codes |= self._codes(day, type_code)
        etf_codes = self._codes(day, TWSE_ETF_TYPE)
        # A code in both is an ETF: the industry tables carry the legacy
        # umbrella listings, the ETF table is the specific one.
        stock_codes -= etf_codes

        unsupported = {kind: self._codes(day, code)
                       for code, kind in TWSE_UNSUPPORTED_TYPES.items()}
        excluded = set().union(*unsupported.values())
        # Specific official security-type tables take precedence over industry
        # umbrella membership. Contradictory specific types fail closed.
        conflicts = excluded & etf_codes
        kinds = list(unsupported.values())
        conflicts |= kinds[0] & kinds[1]
        stock_codes -= excluded
        etf_codes -= conflicts

        retrieved = datetime.now(TAIPEI).isoformat()
        rows: list[dict[str, Any]] = []
        for codes, kind, source_type in (
            (sorted(stock_codes), "stock", "industry_tables"),
            (sorted(etf_codes), "etf", TWSE_ETF_TYPE),
            *((sorted(unsupported[kind] - conflicts), kind, code)
              for code, kind in TWSE_UNSUPPORTED_TYPES.items()),
        ):
            for code in codes:
                rows.append({
                    "code": code,
                    "exchange": "TWSE",
                    "instrument_type": None if kind == "stock" else kind,
                    # Never a value: the official label is current, not PIT,
                    # and not unique (probe §4.3).
                    "industry": None,
                    "industry_status": "data_insufficient",
                    "classification_effective_from": day,
                    "classification_source": f"twse:MI_INDEX:{source_type}@{day.isoformat()}",
                    "classification_status": "data_insufficient" if kind == "stock" else "verified",
                    "retrieved_at": retrieved,
                    "instrument_type_status": "data_insufficient" if kind == "stock" else "verified",
                    "primary_oos_eligible_type": False,
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
            "failed_dates": [], "completed_dates": [], "stopped_early": False,
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
            stats["completed_dates"].append(day.isoformat())
        return stats


def verified_stock_codes(store: HistoricalClassificationStore) -> pl.DataFrame:
    """Codes verified as common stocks, with the session that established it."""
    frame = store.read()
    if frame.is_empty():
        return frame
    return frame.filter(pl.col("primary_oos_eligible_type"))


def classification_counts(
    first_observed: dict[str, date], classified: pl.DataFrame,
) -> dict[str, int | float]:
    """Distinct observed securities, resolved no later than first use in scope.

    ETF and verified unsupported types are resolved exclusions. Only verified
    stocks + unresolved potential stocks belong in the Primary denominator.
    Later classifications never erase earlier unresolved observations.
    """
    by_code: dict[str, list[dict[str, Any]]] = {}
    for row in classified.iter_rows(named=True):
        if row["exchange"] == "TWSE":
            by_code.setdefault(row["code"], []).append(row)
    counts = {"verified_stock_count": 0, "verified_etf_count": 0,
              "verified_unsupported_count": 0, "unknown_count": 0,
              "industry_only_unresolved_count": 0}
    for code, first in first_observed.items():
        eligible_rows = [r for r in by_code.get(code, [])
                         if r["classification_effective_from"] <= first]
        latest = max(eligible_rows, key=lambda r: r["classification_effective_from"], default=None)
        kind = latest["instrument_type"] if latest and latest["classification_status"] == "verified" else None
        key = ("verified_stock_count" if kind == "stock" else
               "verified_etf_count" if kind == "etf" else
               "verified_unsupported_count" if kind in VERIFIED_UNSUPPORTED_TYPES else "unknown_count")
        counts[key] += 1
        if key == "unknown_count" and latest and str(latest.get("classification_source", "")).startswith(
                "twse:MI_INDEX:industry_tables@"):
            counts["industry_only_unresolved_count"] += 1
    denominator = counts["verified_stock_count"] + counts["unknown_count"]
    return {**counts,
            "unknown_ratio": counts["unknown_count"] / len(first_observed) if first_observed else 0.0,
            "primary_classification_denominator": denominator,
            "primary_classification_ratio": counts["verified_stock_count"] / denominator if denominator else 0.0}

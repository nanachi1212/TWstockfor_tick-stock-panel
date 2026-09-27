"""Point-in-time regulatory history for replaying trend_liquidity_v1.

The live v1 lock excludes a candidate using four current official lists
(``events_service``): TWSE and TPEx disposition securities, the TPEx
"altered trading / managed / suspended" status list (``tpex_cmode``), and the
TWSE termination list.  Each of them has an official historical form:

``twse_punish``    TWSE 公布處置有價證券 ``rwd/zh/announcement/punish`` — one row per
                   announcement with 公布日期 and 處置起迄時間; a date-range query
                   returns every announcement whose period overlaps the range.
``tpex_disposal``  TPEx 上櫃處置有價證券 ``www/zh-tw/bulletin/disposal`` — same shape
                   and overlap semantics; days without a disposition carry a
                   placeholder row "本日無處置資料" with no code.
``tpex_cmode``     TPEx 變更交易、分盤交易、管理股票與停止交易資訊 by trading date
                   (``cmode/chtm_result.php?d=``) — the dated version of the list
                   the live lock reads.
``termination``    TWSE 終止上市公司 (already kept by ``instrument_evidence``).

Point-in-time rules used by the replay (source session ``t``, entry ``T``):

* a disposition counts only if published on or before ``t`` and its period
  covers ``T`` (the live check: period covers the entry session);
* suspension comes from the cmode list dated ``t``, the list a lock after the
  close of ``t`` reads;
* a termination counts when its effective date is on or before ``T`` and in the
  live two-calendar-year window.  An effective date after ``t`` cannot be shown
  to have been public at ``t`` (the list carries no publication date), so such a
  code observed at ``t`` blocks the session instead of guessing.

Fail-closed storage: a partition exists only for a complete, schema-valid
official response.  A re-fetch that disagrees with a stored partition is kept as
a conflict and that partition stops counting as coverage.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pyarrow.parquet as pq

from app.taiwan.providers.taiwan_values import TAIPEI

logger = logging.getLogger(__name__)

TWSE_PUNISH_URL = ("https://www.twse.com.tw/rwd/zh/announcement/punish"
                   "?startDate={start:%Y%m%d}&endDate={end:%Y%m%d}&response=json")
TPEX_DISPOSAL_URL = ("https://www.tpex.org.tw/www/zh-tw/bulletin/disposal"
                     "?startDate={start:%Y/%m/%d}&endDate={end:%Y/%m/%d}&response=json")
TPEX_CMODE_URL = ("https://www.tpex.org.tw/web/stock/aftertrading/cmode/chtm_result.php"
                  "?l=zh-tw&d={roc}")

TWSE_PUNISH_FIELDS = ["編號", "公布日期", "證券代號", "證券名稱", "累計", "處置條件",
                      "處置起迄時間", "處置措施", "處置內容", "備註"]
TPEX_DISPOSAL_FIELDS = ["編號", "公布日期", "證券代號", "證券名稱", "累計", "處置起訖時間",
                        "處置原因", "處置內容", "收盤價", "本益比", " "]
TPEX_CMODE_FIELDS = ["證券代號", "證券名稱", "變更交易", "分盤交易", "屬管理股票",
                     "分盤或管理股票撮合循環時間(分鐘)", "停止交易", "財務資訊重點專區",
                     "公告連結", "財務重點專區連結"]
_NO_DISPOSITION = "本日無處置資料"
ANNOUNCEMENT_SOURCES = ("twse_punish", "tpex_disposal")

#: Longest disposition period the coverage rule allows for; a stored period longer
#: than this makes the lookback insufficient and fails the build.
DISPOSITION_LOOKBACK_DAYS = 120

ANNOUNCEMENT_SCHEMA: dict[str, Any] = {
    "source": pl.Utf8, "exchange": pl.Utf8, "code": pl.Utf8, "published_date": pl.Date,
    "period_start": pl.Date, "period_end": pl.Date, "measure": pl.Utf8,
    "raw_json": pl.Utf8, "content_hash": pl.Utf8,
}
CMODE_SCHEMA: dict[str, Any] = {
    "date": pl.Date, "code": pl.Utf8, "altered_trading": pl.Boolean,
    "periodic_trading": pl.Boolean, "managed_stock": pl.Boolean, "suspended": pl.Boolean,
    "raw_json": pl.Utf8, "content_hash": pl.Utf8,
}

JsonFetcher = Callable[[str], Any]


class RegulatorySchemaError(ValueError):
    """An official response could not establish a complete regulatory record."""


def _roc(text: str) -> date:
    match = re.fullmatch(r"\s*(\d{2,3})/(\d{1,2})/(\d{1,2})\s*", str(text))
    if not match:
        raise RegulatorySchemaError(f"not a ROC date: {text!r}")
    year, month, day = (int(part) for part in match.groups())
    return date(year + 1911, month, day)


def _period(text: str) -> tuple[date, date]:
    parts = re.findall(r"\d{2,3}/\d{1,2}/\d{1,2}", str(text))
    if len(parts) != 2:
        raise RegulatorySchemaError(f"disposition period is not a date range: {text!r}")
    start, end = _roc(parts[0]), _roc(parts[1])
    if end < start:
        raise RegulatorySchemaError("disposition period ends before it starts")
    return start, end


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     default=str).encode("utf-8")).hexdigest()


def _clean_code(text: str) -> str:
    # TPEx embeds a relative link after the name, never inside the code.
    code = str(text).strip()
    if not code or not code.isascii() or any(ch.isspace() for ch in code):
        raise RegulatorySchemaError(f"invalid security code: {text!r}")
    return code


def _announcement(source: str, exchange: str, code: str, published: date,
                  period: tuple[date, date], measure: str, raw: list[Any]) -> dict[str, Any]:
    row = {"source": source, "exchange": exchange, "code": code, "published_date": published,
           "period_start": period[0], "period_end": period[1], "measure": measure}
    return {**row, "raw_json": json.dumps(raw, ensure_ascii=False),
            "content_hash": _hash({**row, "raw": raw})}


def parse_twse_punish(payload: Mapping[str, Any]) -> pl.DataFrame:
    if not isinstance(payload, Mapping) or str(payload.get("stat", "")).strip() != "OK":
        raise RegulatorySchemaError("TWSE punish response is not OK")
    if list(payload.get("fields") or []) != TWSE_PUNISH_FIELDS:
        raise RegulatorySchemaError("TWSE punish schema changed")
    data = payload.get("data") or []
    if payload.get("total") is not None and int(payload["total"]) != len(data):
        raise RegulatorySchemaError("TWSE punish response is truncated")
    rows = []
    for raw in data:
        if len(raw) != len(TWSE_PUNISH_FIELDS):
            raise RegulatorySchemaError("TWSE punish row is truncated")
        rows.append(_announcement("twse_punish", "TWSE", _clean_code(raw[2]), _roc(raw[1]),
                                  _period(raw[6]), str(raw[7]).strip(), raw))
    return pl.DataFrame(rows, schema=ANNOUNCEMENT_SCHEMA) if rows else pl.DataFrame(
        schema=ANNOUNCEMENT_SCHEMA)


def parse_tpex_disposal(payload: Mapping[str, Any], start: date, end: date) -> pl.DataFrame:
    if not isinstance(payload, Mapping) or str(payload.get("stat", "")).strip().lower() != "ok":
        raise RegulatorySchemaError("TPEx disposal response is not ok")
    if payload.get("date") != f"{start:%Y%m%d}~{end:%Y%m%d}":
        raise RegulatorySchemaError("TPEx disposal response is for another range")
    tables = payload.get("tables") or []
    if len(tables) != 1 or list(tables[0].get("fields") or []) != TPEX_DISPOSAL_FIELDS:
        raise RegulatorySchemaError("TPEx disposal schema changed")
    rows = []
    for raw in tables[0].get("data") or []:
        if len(raw) != len(TPEX_DISPOSAL_FIELDS):
            raise RegulatorySchemaError("TPEx disposal row is truncated")
        if not str(raw[2]).strip():
            if _NO_DISPOSITION not in str(raw[7]):
                raise RegulatorySchemaError("TPEx disposal row has no security code")
            continue  # official "no disposition today" placeholder
        rows.append(_announcement("tpex_disposal", "TPEX", _clean_code(raw[2]), _roc(raw[1]),
                                  _period(raw[5]), str(raw[6]).strip(), raw))
    return pl.DataFrame(rows, schema=ANNOUNCEMENT_SCHEMA) if rows else pl.DataFrame(
        schema=ANNOUNCEMENT_SCHEMA)


def parse_tpex_cmode(payload: Mapping[str, Any], day: date) -> pl.DataFrame:
    """The dated status list. An empty list is not accepted as evidence."""
    if not isinstance(payload, Mapping) or str(payload.get("stat", "")).strip().lower() != "ok":
        raise RegulatorySchemaError("TPEx cmode response is not ok")
    if payload.get("date") != f"{day:%Y%m%d}":
        raise RegulatorySchemaError("TPEx cmode response is for another date")
    tables = payload.get("tables") or []
    if len(tables) != 1 or list(tables[0].get("fields") or []) != TPEX_CMODE_FIELDS:
        raise RegulatorySchemaError("TPEx cmode schema changed")
    data = tables[0].get("data") or []
    if not data:
        # The site answers an empty list for dates it has no data for; on a trading
        # session that cannot be told apart from a lost list.
        raise RegulatorySchemaError("TPEx cmode list is empty")
    rows = []
    for raw in data:
        if len(raw) != len(TPEX_CMODE_FIELDS):
            raise RegulatorySchemaError("TPEx cmode row is truncated")
        flags = [bool(str(raw[i]).strip()) for i in (2, 3, 4, 6)]
        row = {"date": day, "code": _clean_code(raw[0]), "altered_trading": flags[0],
               "periodic_trading": flags[1], "managed_stock": flags[2], "suspended": flags[3]}
        rows.append({**row, "raw_json": json.dumps(raw, ensure_ascii=False),
                     "content_hash": _hash({**row, "raw": raw})})
    return pl.DataFrame(rows, schema=CMODE_SCHEMA)


def month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    following = date(year + (month == 12), month % 12 + 1, 1)
    return start, following - timedelta(days=1)


# ── Storage ────────────────────────────────────────────────────


class RegulatoryHistoryStore:
    """``<taiwan_data_root>/regulatory_history`` — one Parquet file per official response."""

    def __init__(self, root: Path | None = None) -> None:
        if root is None:
            from app.taiwan.data_root import taiwan_data_root

            root = taiwan_data_root() / "regulatory_history"
        self.root = Path(root)

    def month_path(self, source: str, year: int, month: int) -> Path:
        return self.root / f"source={source}" / f"month={year:04d}-{month:02d}.parquet"

    def cmode_path(self, day: date) -> Path:
        return self.root / "source=tpex_cmode" / f"date={day.isoformat()}.parquet"

    def _conflict_path(self, path: Path) -> Path:
        return path.with_suffix(".conflict.json")

    @staticmethod
    def retrieved_on(path: Path) -> date | None:
        """Taipei calendar date the stored response was retrieved, if recorded."""
        raw = (pq.read_metadata(path).metadata or {}).get(b"retrieved_at")
        try:
            return datetime.fromisoformat(raw.decode()).astimezone(TAIPEI).date() if raw else None
        except ValueError:
            return None

    def _write(self, path: Path, frame: pl.DataFrame, metadata: dict[str, str], *,
               refreshable: bool = False) -> str:
        """Write once; a closed partition never changes, a different re-fetch is a conflict.

        A ``refreshable`` partition was retrieved before its period closed; a later
        response that keeps every stored row replaces it and advances its coverage.
        """
        hashes = sorted(frame["content_hash"].to_list())
        if path.exists():
            stored = sorted(pl.read_parquet(path)["content_hash"].to_list())
            if stored == hashes and not refreshable:
                return "unchanged"
            if refreshable and set(stored) <= set(hashes):
                path.unlink()
                self._write(path, frame, metadata)
                return "refreshed"
            record = {"stored": _hash(stored), "fetched": _hash(hashes),
                      "detected_at": datetime.now(TAIPEI).isoformat(),
                      "source_url": metadata.get("source_url")}
            self._conflict_path(path).write_text(json.dumps(record), encoding="utf-8")
            logger.warning("regulatory history revision conflict: %s", path.name)
            return "conflict"
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        os.close(handle)
        try:
            table = frame.to_arrow().replace_schema_metadata(
                {key.encode(): value.encode() for key, value in metadata.items()})
            pq.write_table(table, temporary, compression="zstd")
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return "written"

    def write_month(self, source: str, year: int, month: int, frame: pl.DataFrame,
                    metadata: dict[str, str]) -> str:
        if source not in ANNOUNCEMENT_SOURCES:
            raise ValueError("unknown announcement source")
        through = self.covered_months(source).get((year, month))
        return self._write(self.month_path(source, year, month), frame, metadata,
                           refreshable=through is not None and through < month_bounds(year, month)[1])

    def write_cmode(self, day: date, frame: pl.DataFrame, metadata: dict[str, str]) -> str:
        path = self.cmode_path(day)
        retrieved = self.retrieved_on(path) if path.exists() else None
        return self._write(path, frame, metadata,
                           refreshable=retrieved is not None and retrieved <= day)

    def covered_months(self, source: str) -> dict[tuple[int, int], date]:
        """Month -> last publication date the stored response can contain.

        A response holds announcements published before the day it was retrieved,
        and never beyond its own month.
        """
        folder = self.root / f"source={source}"
        months: dict[tuple[int, int], date] = {}
        for path in folder.glob("month=*.parquet") if folder.exists() else ():
            retrieved = self.retrieved_on(path)
            if self._conflict_path(path).exists() or retrieved is None:
                continue
            year, month = (int(part) for part in path.stem.removeprefix("month=").split("-"))
            months[(year, month)] = min(month_bounds(year, month)[1], retrieved - timedelta(days=1))
        return months

    def cmode_dates(self) -> set[date]:
        """Dates whose list was retrieved after that date ended (no intraday additions left)."""
        folder = self.root / "source=tpex_cmode"
        dates = set()
        for path in folder.glob("date=*.parquet") if folder.exists() else ():
            day = date.fromisoformat(path.stem.removeprefix("date="))
            retrieved = self.retrieved_on(path)
            if not self._conflict_path(path).exists() and retrieved is not None and retrieved > day:
                dates.add(day)
        return dates

    def conflicts(self) -> list[str]:
        return sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*.conflict.json")) \
            if self.root.exists() else []

    def announcements(self) -> pl.DataFrame:
        frames = []
        for source in ANNOUNCEMENT_SOURCES:
            for year, month in sorted(self.covered_months(source)):
                frames.append(pl.read_parquet(self.month_path(source, year, month)))
        frames = [f for f in frames if f.height]
        # Overlap queries return one announcement in every month its period touches.
        return (pl.concat(frames).unique(subset=["content_hash"]).sort(
            "exchange", "code", "published_date", "period_start")
            if frames else pl.DataFrame(schema=ANNOUNCEMENT_SCHEMA))

    def suspended_on(self, days: Iterable[date]) -> dict[date, frozenset[str]]:
        wanted = set(days) & self.cmode_dates()
        return {day: frozenset(pl.read_parquet(self.cmode_path(day)).filter(
            pl.col("suspended"))["code"].to_list()) for day in wanted}

    def digest(self) -> str:
        """Identity of every stored response and conflict marker."""
        digest = hashlib.sha256()
        if self.root.exists():
            for path in sorted(self.root.rglob("*")):
                if path.is_file() and path.suffix in (".parquet", ".json"):
                    digest.update(str(path.relative_to(self.root)).replace("\\", "/").encode())
                    digest.update(hashlib.sha256(path.read_bytes()).digest())
        return digest.hexdigest()


# ── Backfill ───────────────────────────────────────────────────


def _metadata(url: str, payload: Any, **extra: str) -> dict[str, str]:
    return {"source_url": url, "retrieved_at": datetime.now(TAIPEI).isoformat(),
            "raw_sha256": _hash(payload), **extra}


def backfill_announcements(
    store: RegulatoryHistoryStore, start: date, end: date, *, fetch: JsonFetcher,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Fetch every missing month for both disposition archives."""
    report: dict[str, Any] = {"written": 0, "unchanged": 0, "refreshed": 0, "conflict": 0,
                              "errors": []}
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        first, last = month_bounds(year, month)
        for source in ANNOUNCEMENT_SOURCES:
            through = store.covered_months(source).get((year, month))
            if through is not None and through >= last:
                continue
            if should_stop is not None and should_stop():
                return report
            url = (TWSE_PUNISH_URL if source == "twse_punish" else TPEX_DISPOSAL_URL).format(
                start=first, end=last)
            try:
                payload = fetch(url)
                frame = (parse_twse_punish(payload) if source == "twse_punish"
                         else parse_tpex_disposal(payload, first, last))
            except Exception as exc:
                report["errors"].append(f"{source}:{year:04d}-{month:02d}:{type(exc).__name__}")
                continue
            outcome = store.write_month(source, year, month, frame, _metadata(
                url, payload, query_start=first.isoformat(), query_end=last.isoformat()))
            report[outcome] += 1
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return report


def backfill_cmode(
    store: RegulatoryHistoryStore, sessions: Sequence[date], *, fetch: JsonFetcher,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Fetch the dated cmode list for every verified session still missing."""
    report: dict[str, Any] = {"written": 0, "unchanged": 0, "refreshed": 0, "conflict": 0,
                              "errors": []}
    done = store.cmode_dates()
    for day in sessions:
        if day in done:
            continue
        if should_stop is not None and should_stop():
            break
        url = TPEX_CMODE_URL.format(roc=f"{day.year - 1911}/{day.month:02d}/{day.day:02d}")
        try:
            payload = fetch(url)
            frame = parse_tpex_cmode(payload, day)
        except Exception as exc:
            report["errors"].append(f"{day.isoformat()}:{type(exc).__name__}")
            continue
        report[store.write_cmode(day, frame, _metadata(url, payload))] += 1
    return report


# ── Replay evidence ────────────────────────────────────────────

REGULATORY_BLOCKERS: dict[str, str] = {
    "regulatory_twse_disposition_unavailable":
        "TWSE disposition archive does not cover the entry session's lookback window",
    "regulatory_tpex_disposition_unavailable":
        "TPEx disposition archive does not cover the entry session's lookback window",
    "regulatory_tpex_status_unavailable":
        "no verified TPEx altered/suspended status list dated on the source session",
    "regulatory_termination_unavailable":
        "the TWSE termination list was not retrieved after the entry session",
    "regulatory_publication_time_unproven":
        "an observed code's termination takes effect after the source session; its "
        "publication before the cutoff cannot be shown",
}


def _months_between(start: date, end: date) -> set[tuple[int, int]]:
    months, year, month = set(), start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.add((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def replay_regulatory_evidence(
    store: RegulatoryHistoryStore,
    pairs: Sequence[tuple[date, date]],
    *,
    terminations: Mapping[str, date] | None,
    terminations_retrieved: date | None,
    observed_codes: Mapping[date, frozenset[str]],
) -> tuple[dict[date, tuple[str, ...]], dict[date, frozenset[str]]]:
    """Per entry session: blockers, and codes the live lock would have excluded.

    ``pairs`` are (source session, entry session); ``observed_codes`` holds the
    codes with a price bar at each source session.
    """
    announcements = store.announcements()
    if announcements.height:
        longest = announcements.select(
            (pl.col("period_end") - pl.col("period_start")).dt.total_days().max()).item()
        if longest is not None and longest > DISPOSITION_LOOKBACK_DAYS:
            raise RegulatorySchemaError("a disposition period exceeds the coverage lookback")
    months = {source: store.covered_months(source) for source in ANNOUNCEMENT_SOURCES}

    def disposition_covered(name: str, source: date, target: date) -> bool:
        # Every month in the lookback must hold all announcements published by ``source``.
        lookback = _months_between(target - timedelta(days=DISPOSITION_LOOKBACK_DAYS), target)
        return all(months[name].get(month, date.min) >= min(source, month_bounds(*month)[1])
                   for month in lookback)

    suspended = store.suspended_on(source for source, _ in pairs)
    rows = announcements.select("code", "published_date", "period_start", "period_end").rows()
    blockers: dict[date, tuple[str, ...]] = {}
    excluded: dict[date, frozenset[str]] = {}
    for source, target in pairs:
        missing = []
        if not disposition_covered("twse_punish", source, target):
            missing.append("regulatory_twse_disposition_unavailable")
        if not disposition_covered("tpex_disposal", source, target):
            missing.append("regulatory_tpex_disposition_unavailable")
        if source not in suspended:
            missing.append("regulatory_tpex_status_unavailable")
        if terminations is None or terminations_retrieved is None or terminations_retrieved <= target:
            missing.append("regulatory_termination_unavailable")
        codes = {code for code, published, start, end in rows
                 if published <= source and start <= target <= end}
        codes |= suspended.get(source, frozenset())
        if terminations is not None:
            window = date(source.year - 2, 1, 1)
            for code, effective in terminations.items():
                if window <= effective <= source:
                    codes.add(code)
                elif source < effective <= target and code in observed_codes.get(source, ()):
                    missing.append("regulatory_publication_time_unproven")
        blockers[target] = tuple(sorted(set(missing)))
        excluded[target] = frozenset(codes)
    return blockers, excluded

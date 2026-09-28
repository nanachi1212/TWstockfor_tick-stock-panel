"""Official monthly-revenue publication evidence for point-in-time selection.

Source: MOPS 營業收入統計表 ``t21sc03`` (TWSE ``sii`` / TPEx ``otc`` boards,
domestic ``_0`` and foreign ``_1`` issuer pages), one static page per
market/issuer-kind/revenue month.  Each page lists issuer code, name and the
issuer's current revenue for that month in thousand TWD.

No official monthly-revenue interface exposes a per-record publication time or
revision identifier (Phase 6G, ``docs/taiwan-fundamentals-availability-phase-6g.md``).
The page ``出表日期`` is regenerated daily and is kept as metadata only.  The
only provable fact is therefore the observation itself: when this module
retrieves an official page at ``retrieved_at``, every value on it was public at
that instant.  A value is usable for a query cutoff only when it was observed
strictly before the cutoff (``cutoff > observed_at``, the repository's
existing strict rule).  Values are never back-dated to a filing deadline,
revenue month, report date or a third-party ``create_time``.

Revisions are append-only observations.  For a cutoff, each page resolves to
its latest successful fetch before the cutoff, so a later correction never
overwrites what was visible earlier.  Corrections published before our first
observation are indistinguishable from originals and are reported as
``first_observed``.
"""
# ruff: noqa: RUF001 -- official field names contain full-width punctuation.
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
import threading
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from app.taiwan.providers.taiwan_values import TAIPEI

logger = logging.getLogger(__name__)

SOURCE = "mops:t21sc03"
PAGE_URL = "https://mopsov.twse.com.tw/nas/t21/{board}/t21sc03_{roc_year}_{month}_{kind}.html"
BOARDS: dict[str, str] = {"TWSE": "sii", "TPEX": "otc"}
ISSUER_KINDS: dict[str, str] = {"0": "domestic", "1": "foreign"}
# Latest three revenue months plus their year-ago months: enough for the
# latest YoY, the previous month's YoY and MoM without inventing a series.
WINDOW_MONTH_OFFSETS = (1, 2, 3, 13, 14, 15)
REFETCH_AFTER = timedelta(hours=20)
MAX_EVIDENCE_AGE = timedelta(days=4)

EvidenceStatus = Literal[
    "available", "missing", "not_observed_before_cutoff", "stale", "incomplete",
]
FetchStatus = Literal["available", "not_published", "schema_changed", "error"]

_CODE = re.compile(r"^[0-9]{4}[0-9A-Z]{0,2}$")
_TITLE = re.compile(r"(\d{2,3})年\s*(\d{1,2})月份")
_REPORT_DATE = re.compile(r"出表日期\s*[:：]\s*(\d{2,3})/(\d{1,2})/(\d{1,2})")
_REQUIRED_HEADERS = ("當月營收", "上月營收", "去年當月營收")


class RevenuePageSchemaError(ValueError):
    """The official page no longer matches the parsed contract."""


@dataclass(frozen=True)
class RevenuePageRow:
    raw_code: str
    name: str
    revenue_thousand_twd: int | None
    previous_month_thousand_twd: int | None


@dataclass(frozen=True)
class RevenuePage:
    status: Literal["available", "not_published"]
    period: str
    report_date: str | None
    rows: tuple[RevenuePageRow, ...]


@dataclass(frozen=True)
class RevenueEvidenceSnapshot:
    """Official revenue rows visible at ``cutoff`` plus their provenance."""

    cutoff: datetime
    status: EvidenceStatus
    rows_by_symbol: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    mismatches: dict[str, list[str]] = field(default_factory=dict)
    first_observed_at: str | None = None
    latest_observed_at: str | None = None
    page_count: int = 0
    stale_page_count: int = 0
    missing_pages: list[str] = field(default_factory=list)
    digest: str | None = None


# ─────────────────────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────────────────────

def month_period(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def shift_period(period: str, months: int) -> str:
    year, month = int(period[:4]), int(period[5:7])
    index = year * 12 + (month - 1) - months
    return month_period(index // 12, index % 12 + 1)


def window_periods(today: date) -> list[str]:
    current = month_period(today.year, today.month)
    return [shift_period(current, offset) for offset in WINDOW_MONTH_OFFSETS]


def expected_page_keys(cutoff: datetime) -> list[tuple[str, str, str]]:
    """Every board/issuer-kind/month page a complete snapshot needs at ``cutoff``."""
    periods = window_periods(cutoff.astimezone(TAIPEI).date())
    return [(m, k, p) for m in BOARDS for k in ISSUER_KINDS for p in periods]


def page_url(market: str, kind: str, period: str) -> str:
    year, month = int(period[:4]), int(period[5:7])
    return PAGE_URL.format(board=BOARDS[market], roc_year=year - 1911, month=month, kind=kind)


def decode_page(content: bytes) -> str:
    head = content[:2048].decode("ascii", "ignore").lower()
    encodings = ("big5", "cp950", "utf-8") if "big5" in head else ("utf-8", "cp950", "big5")
    for encoding in encodings:
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise RevenuePageSchemaError("monthly revenue page encoding is not recognised")


def _cell_text(raw: str) -> str:
    text = re.sub(r"<[^>]+>", "", raw)
    return re.sub(r"(?:&nbsp;|\s)+", " ", text).strip()


def _thousand(raw: str) -> int | None:
    """Parse an official thousand-TWD cell; blanks and dashes stay null, not 0."""
    text = raw.replace(",", "").strip()
    if not text or text in {"-", "--", "N/A"}:
        return None
    if not re.fullmatch(r"-?\d+", text):
        raise RevenuePageSchemaError(f"unexpected revenue cell: {raw!r}")
    return int(text)


def parse_revenue_page(html: str, *, period: str) -> RevenuePage:
    """Parse one MOPS t21sc03 page; any contract drift raises instead of guessing."""
    plain = _cell_text(html)
    title = _TITLE.search(plain)
    if title is None:
        raise RevenuePageSchemaError("monthly revenue page title not found")
    if month_period(int(title.group(1)) + 1911, int(title.group(2))) != period:
        raise RevenuePageSchemaError(f"page period {title.group(0)} does not match {period}")
    report = _REPORT_DATE.search(plain)
    report_date = (
        date(int(report.group(1)) + 1911, int(report.group(2)), int(report.group(3))).isoformat()
        if report else None
    )
    if "查無資料" in plain:
        return RevenuePage("not_published", period, report_date, ())
    if not all(header in plain for header in _REQUIRED_HEADERS):
        raise RevenuePageSchemaError("monthly revenue page headers changed")

    rows: dict[str, RevenuePageRow] = {}
    conflicted: set[str] = set()
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S | re.I):
        cells = [_cell_text(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.S | re.I)]
        if len(cells) != 11 or not _CODE.match(cells[0]):
            continue
        row = RevenuePageRow(
            raw_code=cells[0], name=cells[1],
            revenue_thousand_twd=_thousand(cells[2]),
            previous_month_thousand_twd=_thousand(cells[3]),
        )
        if cells[0] in rows and rows[cells[0]] != row:
            conflicted.add(cells[0])
        rows[cells[0]] = row
    for code in conflicted:
        # Two different rows for one issuer on one page cannot be resolved.
        rows.pop(code, None)
    if not rows and not conflicted:
        raise RevenuePageSchemaError("monthly revenue page has no issuer rows")
    return RevenuePage("available", period, report_date, tuple(rows[c] for c in sorted(rows)))


# ─────────────────────────────────────────────────────────────
# Store
# ─────────────────────────────────────────────────────────────

def _aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("evidence timestamps must be timezone-aware")
    return parsed


@lru_cache(maxsize=512)
def _load_rows(path: str, sha: str) -> dict[str, tuple[str, int | None, int | None]]:
    """Content-addressed rows are immutable, so caching by digest is safe."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return {row[0]: (row[1], row[2], row[3]) for row in payload}


class MonthlyRevenueEvidenceStore:
    """Append-only fetch ledger with content-addressed page rows."""

    def __init__(self, root: Path | None = None) -> None:
        if root is None:
            from app.taiwan.data_root import taiwan_data_root

            root = taiwan_data_root() / "monthly_revenue_evidence"
        self.root = Path(root)
        self.ledger = self.root / "fetches.jsonl"
        self._lock = threading.Lock()

    # ── writes ────────────────────────────────────────────────
    def record_fetch(
        self, *, run_id: str, market: str, kind: str, period: str, retrieved_at: datetime,
        status: FetchStatus, raw: bytes | None = None, page: RevenuePage | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        if retrieved_at.tzinfo is None:
            raise ValueError("retrieved_at must be timezone-aware")
        record: dict[str, Any] = {
            "fetch_id": f"{run_id}:{market}:{kind}:{period}",
            "run_id": run_id, "market": market, "kind": kind, "period": period,
            "source": SOURCE, "source_url": page_url(market, kind, period),
            "retrieved_at": retrieved_at.isoformat(), "status": status,
            "report_date": page.report_date if page else None,
            "raw_sha256": None, "rows_sha256": None, "row_count": 0, "error": error,
        }
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            if raw is not None:
                record["raw_sha256"] = hashlib.sha256(raw).hexdigest()
                self._write_once(self.root / "raw" / f"{record['raw_sha256']}.html.gz", gzip.compress(raw))
            if page is not None and page.status == "available":
                payload = json.dumps(
                    [[r.raw_code, r.name, r.revenue_thousand_twd, r.previous_month_thousand_twd]
                     for r in page.rows],
                    ensure_ascii=False, separators=(",", ":"),
                ).encode("utf-8")
                record["rows_sha256"] = hashlib.sha256(payload).hexdigest()
                record["row_count"] = len(page.rows)
                self._write_once(self._rows_path(record["rows_sha256"]), payload)
            # Content files land before the ledger line, so readers never see
            # a fetch that references missing rows.
            with self.ledger.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return record

    @staticmethod
    def _write_once(path: Path, content: bytes) -> None:
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
        temporary.write_bytes(content)
        temporary.replace(path)

    def _rows_path(self, sha: str) -> Path:
        return self.root / "tables" / f"{sha}.json"

    # ── reads ─────────────────────────────────────────────────
    def fetches(self) -> list[dict[str, Any]]:
        if not self.ledger.exists():
            return []
        records = []
        for line in self.ledger.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
                _aware(record["retrieved_at"])
            except (ValueError, KeyError, TypeError):
                continue  # a torn trailing line is not evidence
            records.append(record)
        return sorted(records, key=lambda r: (_aware(r["retrieved_at"]), r["fetch_id"]))

    def rows(self, sha: str) -> dict[str, tuple[str, int | None, int | None]]:
        return _load_rows(str(self._rows_path(sha)), sha)

    def last_success(self, market: str, kind: str, period: str) -> datetime | None:
        times = [
            _aware(r["retrieved_at"]) for r in self.fetches()
            if (r["market"], r["kind"], r["period"]) == (market, kind, period)
            and r["status"] in ("available", "not_published")
        ]
        return max(times, default=None)

    def evidence_as_of(
        self, cutoff: datetime, *, max_age: timedelta = MAX_EVIDENCE_AGE,
    ) -> RevenueEvidenceSnapshot:
        """Resolve the latest official observation of every page before ``cutoff``."""
        if cutoff.tzinfo is None:
            raise ValueError("cutoff must be timezone-aware")
        successes = [r for r in self.fetches() if r["status"] in ("available", "not_published")]
        if not successes:
            return RevenueEvidenceSnapshot(cutoff=cutoff, status="missing")
        first_observed = min(_aware(r["retrieved_at"]) for r in successes)
        before = [r for r in successes if _aware(r["retrieved_at"]) < cutoff]
        if not before:
            return RevenueEvidenceSnapshot(
                cutoff=cutoff, status="not_observed_before_cutoff",
                first_observed_at=first_observed.isoformat(),
            )

        history: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for record in before:
            history.setdefault((record["market"], record["kind"], record["period"]), []).append(record)
        chosen: dict[tuple[str, str, str], dict[str, Any]] = {}
        stale = 0
        for key, records in history.items():
            latest = records[-1]
            if _aware(latest["retrieved_at"]) < cutoff - max_age:
                stale += 1
            else:
                chosen[key] = latest
        latest_observed = max(_aware(r["retrieved_at"]) for r in before).isoformat()
        if not chosen:
            return RevenueEvidenceSnapshot(
                cutoff=cutoff, status="stale", first_observed_at=first_observed.isoformat(),
                latest_observed_at=latest_observed, stale_page_count=stale,
            )
        # A partial page set would silently drop a board, issuer kind or
        # comparison month, so it is not an available snapshot.
        missing = [
            f"{market}/{kind}/{period}" for market, kind, period in expected_page_keys(cutoff)
            if (market, kind, period) not in chosen
        ]
        if missing:
            return RevenueEvidenceSnapshot(
                cutoff=cutoff, status="incomplete", first_observed_at=first_observed.isoformat(),
                latest_observed_at=latest_observed, page_count=len(chosen),
                stale_page_count=stale, missing_pages=missing,
            )

        rows_by_symbol: dict[str, list[dict[str, Any]]] = {}
        stated_previous: dict[tuple[str, str, str], tuple[int, str]] = {}
        stated_current: dict[tuple[str, str, str], tuple[int, str]] = {}
        for key in sorted(chosen):
            market, _kind, period = key
            record = chosen[key]
            if record["status"] != "available":
                continue
            visible = self._value_history(history[key])
            year, month = int(period[:4]), int(period[5:7])
            for code, (name, revenue, previous) in self.rows(record["rows_sha256"]).items():
                if previous is not None:
                    stated_previous[(market, code, shift_period(period, 1))] = (previous, record["run_id"])
                if revenue is None:
                    continue  # a blank official cell stays missing, never zero
                stated_current[(market, code, period)] = (revenue, record["run_id"])
                since, revision = visible[code]
                rows_by_symbol.setdefault(f"{code}.{market}", []).append({
                    "date": f"{period}-01", "revenue_year": year, "revenue_month": month,
                    "revenue": revenue * 1000, "revenue_thousand_twd": revenue, "name": name,
                    "evidence_observed_at": since, "retrieved_at": record["retrieved_at"],
                    "revision_seq": revision,
                    "revision_status": "first_observed" if revision == 1 else "changed_after_first_observation",
                    "source": SOURCE, "source_url": record["source_url"],
                    "report_date": record["report_date"], "fetch_id": record["fetch_id"],
                    "rows_sha256": record["rows_sha256"],
                })

        mismatches: dict[str, list[str]] = {}
        for (market, code, period), (value, run_id) in stated_previous.items():
            current = stated_current.get((market, code, period))
            # Pages fetched by the same refresh must agree; across refreshes a
            # difference is an ordinary later correction, not a conflict.
            if current is not None and current[1] == run_id and current[0] != value:
                mismatches.setdefault(f"{code}.{market}", []).append(period)
        for symbol, periods in mismatches.items():
            bad = {f"{period}-01" for period in periods}
            rows_by_symbol[symbol] = [
                row for row in rows_by_symbol.get(symbol, []) if row["date"] not in bad
            ]
        for rows in rows_by_symbol.values():
            rows.sort(key=lambda row: row["date"])

        identity = sorted(
            (key[0], key[1], key[2], chosen[key]["fetch_id"], chosen[key]["rows_sha256"] or "")
            for key in chosen
        )
        digest = hashlib.sha256(json.dumps(identity).encode("utf-8")).hexdigest()
        return RevenueEvidenceSnapshot(
            cutoff=cutoff, status="available", rows_by_symbol=rows_by_symbol,
            mismatches={s: sorted(p) for s, p in sorted(mismatches.items())},
            first_observed_at=first_observed.isoformat(), latest_observed_at=latest_observed,
            page_count=len(chosen), stale_page_count=stale, digest=digest,
        )

    def _value_history(self, records: Iterable[dict[str, Any]]) -> dict[str, tuple[str, int]]:
        """Per issuer: when the latest value was first seen and its revision number."""
        state: dict[str, tuple[int | None, str, int]] = {}
        previous_sha = None
        for record in records:
            if record["status"] != "available" or record["rows_sha256"] == previous_sha:
                continue
            previous_sha = record["rows_sha256"]
            for code, (_name, revenue, _previous) in self.rows(record["rows_sha256"]).items():
                current = state.get(code)
                if current is None:
                    state[code] = (revenue, record["retrieved_at"], 1)
                elif current[0] != revenue:
                    state[code] = (revenue, record["retrieved_at"], current[2] + 1)
        return {code: (since, revision) for code, (_value, since, revision) in state.items()}

    def revisions(self, symbol: str, period: str) -> list[dict[str, Any]]:
        """Official value chronology of one issuer month, as observed."""
        code, _, market = symbol.partition(".")
        result: list[dict[str, Any]] = []
        for record in self.fetches():
            if (record["market"], record["period"], record["status"]) != (market, period, "available"):
                continue
            row = self.rows(record["rows_sha256"]).get(code)
            if row is None:
                continue
            value = row[1]
            if result and result[-1]["revenue_thousand_twd"] == value:
                result[-1]["last_observed_at"] = record["retrieved_at"]
                continue
            result.append({
                "revision_seq": len(result) + 1, "revenue_thousand_twd": value,
                "first_observed_at": record["retrieved_at"],
                "last_observed_at": record["retrieved_at"],
                "fetch_id": record["fetch_id"], "source_url": record["source_url"],
            })
        return result


# ─────────────────────────────────────────────────────────────
# Refresh (user-triggered update flow only; never from the screener)
# ─────────────────────────────────────────────────────────────

def refresh_monthly_revenue_evidence(
    store: MonthlyRevenueEvidenceStore | None = None, *,
    now: datetime | None = None, client: Any = None,
) -> dict[str, Any]:
    """Fetch the official window pages in market batches, skipping fresh ones."""
    from app.taiwan.providers.http import DEFAULT_USER_AGENT, taiwan_client

    store = store or MonthlyRevenueEvidenceStore()
    now = now or datetime.now(TAIPEI)
    run_id = f"{now.astimezone(TAIPEI):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
    # An observation can serve cutoffs up to MAX_EVIDENCE_AGE later, which may
    # fall in the next month (e.g. a month-end refresh for a next-month entry).
    local = now.astimezone(TAIPEI)
    periods = list(dict.fromkeys(
        window_periods(local.date()) + window_periods((local + MAX_EVIDENCE_AGE).date())
    ))
    owned = client is None
    client = client or taiwan_client(timeout=30.0, headers={"User-Agent": DEFAULT_USER_AGENT})
    summary: dict[str, Any] = {
        "run_id": run_id, "source": SOURCE, "periods": periods,
        "fetched": 0, "skipped_fresh": 0, "not_published": 0, "failed": 0, "errors": [],
    }
    try:
        for market in BOARDS:
            for kind in ISSUER_KINDS:
                for period in periods:
                    last = store.last_success(market, kind, period)
                    if last is not None and now - last < REFETCH_AFTER:
                        summary["skipped_fresh"] += 1
                        continue
                    url = page_url(market, kind, period)
                    raw: bytes | None = None
                    try:
                        response = client.get(url)
                        response.raise_for_status()
                        raw = response.content
                        page = parse_revenue_page(decode_page(raw), period=period)
                        store.record_fetch(
                            run_id=run_id, market=market, kind=kind, period=period,
                            retrieved_at=datetime.now(TAIPEI), status=page.status,
                            raw=raw, page=page,
                        )
                        summary["fetched"] += 1
                        summary["not_published"] += page.status == "not_published"
                    except Exception as exc:
                        status: FetchStatus = "schema_changed" if isinstance(exc, RevenuePageSchemaError) else "error"
                        store.record_fetch(
                            run_id=run_id, market=market, kind=kind, period=period,
                            retrieved_at=datetime.now(TAIPEI), status=status, raw=raw,
                            error=f"{type(exc).__name__}: {exc}"[:300],
                        )
                        summary["failed"] += 1
                        if len(summary["errors"]) < 10:
                            summary["errors"].append(f"{market}/{kind}/{period}: {type(exc).__name__}")
                        logger.warning("Monthly revenue evidence fetch failed for %s: %s", url, exc)
    finally:
        if owned:
            client.close()
    attempted = summary["fetched"] + summary["failed"]
    summary["status"] = (
        "available" if summary["failed"] == 0
        else "partial" if attempted > summary["failed"] or summary["skipped_fresh"]
        else "unavailable"
    )
    return summary

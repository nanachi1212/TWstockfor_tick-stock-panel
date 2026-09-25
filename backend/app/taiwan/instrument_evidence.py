"""Official security-registry evidence for historical common-stock subtype.

The A2b industry tables prove that a code traded under some industry on a date,
not that it is an ordinary share (``historical_classification`` docs).  This
module adds the missing *subtype* evidence from official registries, without
any code-format, name or current-master heuristic:

``isin_listed``    TWSE 本國上市證券 ISIN 一覽表: code, ISIN, listing date, section
                   (股票 / 創新板 / 特別股 / ETN) and CFI.  Covers securities still
                   listed today.
``isin_unlisted``  TWSE 本國未上市未上櫃公開發行證券 ISIN 一覽表: delisted companies
                   that remain public issuers (same CFI semantics; its date column is
                   the public-offering date, so it is not used as an existence check).
``company``        TWSE OpenAPI 上市公司基本資料 (t187ap03_L): company code and company
                   listing date. A company code is its ordinary-share code, so a company
                   listed on or before the first observation proves who held the code,
                   which the security-level listing date cannot after a re-listing or an
                   ISIN change (for example a par-value change).
``announcement``   TWSE 公文公告 (2017 onward): an official notice whose subject names the
                   exact code together with 特別股 (e.g. 甲種特別股 ... 3702A 到期收回暨終止
                   上市). The code must appear verbatim; nothing is read from its format.
``termination``    TWSE 終止上市公司: one row per delisted *company*, i.e. its primary
                   listed share.  Preferred shares of such a company never appear
                   (verified against delisted preferred codes).

A security's share class is intrinsic to its ISIN, so a registry row supports the
class on any date the security existed.  Existence is checked, never assumed:
the listed-registry listing date must not be later than the first observed date
unless the company registry shows the company already listed then, or the
historical 創新板 table shows the code on that date (a board move resets it).  Anything not settled stays unresolved.
"""
from __future__ import annotations

# ruff: noqa: RUF001 -- Official Chinese provider fields must remain exact.
import hashlib
import json
import logging
import os
import re
import tempfile
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pyarrow.parquet as pq

from app.taiwan.historical_classification import (
    HistoricalClassificationStore,
    TwseHistoricalClassifier,
)
from app.taiwan.providers.http import DEFAULT_USER_AGENT, fetch_json, throttle
from app.taiwan.providers.taiwan_values import TAIPEI

logger = logging.getLogger(__name__)

ISIN_URL = "https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"
TERMINATION_URL = ("https://www.twse.com.tw/rwd/zh/company/suspendListing"
                   "?response=json&startDate=20010101&endDate={end:%Y%m%d}")
COMPANY_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
ANNOUNCEMENT_URL = ("https://www.twse.com.tw/rwd/zh/announcement/announcement"
                    "?startDate={start:%Y%m%d}&endDate={end:%Y%m%d}&keyword=&response=json")
ETN_TYPE = "029999"
TIB_TYPE = "TIB"

REGISTRY_SCHEMA: dict[str, Any] = {
    "code": pl.Utf8, "isin": pl.Utf8, "listing_date": pl.Date,
    "section": pl.Utf8, "cfi": pl.Utf8, "registry": pl.Utf8,
}
TERMINATION_SCHEMA: dict[str, Any] = {"code": pl.Utf8, "termination_date": pl.Date}
COMPANY_SCHEMA: dict[str, Any] = {"code": pl.Utf8, "listing_date": pl.Date}

_LISTED_SECTIONS = {"股票": "stock", "創新板": "stock", "特別股": "preferred_share", "ETN": "etn"}
_SEP = "　"


# ── Fetch / parse ──────────────────────────────────────────────

def _cells(row_html: str) -> list[str]:
    return [re.sub(r"<.*?>", "", cell).replace("&nbsp;", " ").strip()
            for cell in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row_html, flags=re.S)]


def _parse_date(text: str) -> date:
    year, month, day = (int(part) for part in text.strip().split("/"))
    return date(year, month, day)


def parse_isin_registry(raw: bytes, registry: str) -> pl.DataFrame:
    """Parse one ISIN page. Unknown layouts raise instead of yielding partial rows."""
    text = raw.decode("big5hkscs", errors="strict")
    listed = registry == "isin_listed"
    if listed and "本國上市證券國際證券辨識號碼一覽表" not in text:
        raise ValueError("ISIN listed registry title changed")
    if not listed and "本國未上市，未上櫃公開發行證券" not in text:
        raise ValueError("ISIN unlisted registry title changed")
    width = 7 if listed else 6
    section = "" if listed else "unlisted"
    rows: list[dict[str, Any]] = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.S):
        cells = _cells(row_html)
        if len(cells) == 1:
            section = cells[0]
            continue
        if len(cells) != width or _SEP not in cells[0]:
            continue
        code = cells[0].split(_SEP)[0].strip()
        cfi = cells[5] if width == 7 else cells[4]
        if not code or not cfi:
            raise ValueError("ISIN registry row is missing code or CFI")
        if listed and section not in _LISTED_SECTIONS:
            continue  # warrants, ETF, TDR, REIT: resolved by the historical tables
        rows.append({
            "code": code, "isin": cells[1], "listing_date": _parse_date(cells[2]),
            "section": section, "cfi": cfi, "registry": registry,
        })
    if not rows:
        raise ValueError("ISIN registry produced no rows")
    return pl.DataFrame(rows, schema=REGISTRY_SCHEMA)


def parse_company(payload: Any) -> pl.DataFrame:
    if not isinstance(payload, list) or not payload:
        raise ValueError("TWSE company registry is unavailable")
    rows = []
    for item in payload:
        code, text = str(item.get("公司代號", "")).strip(), str(item.get("上市日期", "")).strip()
        if not code or len(text) != 8 or not text.isdigit():
            raise ValueError("company registry row is malformed")
        rows.append({"code": code, "listing_date": date(int(text[:4]), int(text[4:6]), int(text[6:]))})
    return pl.DataFrame(rows, schema=COMPANY_SCHEMA)


def _fetch_bytes(url: str) -> bytes:
    throttle(url)
    request = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def parse_termination(payload: Mapping[str, Any]) -> pl.DataFrame:
    if payload.get("status") != "ok" or not payload.get("data"):
        raise ValueError("TWSE termination list is unavailable")
    rows = []
    for item in payload["data"]:
        roc, _name, code = item[0], item[1], str(item[2]).strip()
        year, month, day = (int(part) for part in roc.split("/"))
        if not code:
            raise ValueError("termination row has no code")
        rows.append({"code": code, "termination_date": date(year + 1911, month, day)})
    return pl.DataFrame(rows, schema=TERMINATION_SCHEMA)


# ── Persistence ────────────────────────────────────────────────

class InstrumentEvidenceStore:
    """Small Parquet snapshots with source provenance in the footer."""

    def __init__(self, root: Path | None = None) -> None:
        if root is None:
            from app.taiwan.data_root import taiwan_data_root

            root = taiwan_data_root() / "instrument_evidence"
        self.root = Path(root)

    def _write(self, name: str, frame: pl.DataFrame, metadata: dict[str, str]) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / name
        handle, temporary = tempfile.mkstemp(dir=str(self.root), suffix=".tmp")
        os.close(handle)
        try:
            table = frame.to_arrow().replace_schema_metadata(
                {key.encode(): value.encode() for key, value in metadata.items()})
            pq.write_table(table, temporary, compression="zstd")
            os.replace(temporary, target)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return target

    def save_registry(self, frame: pl.DataFrame, *, source_url: str, sha256: str,
                      retrieved_at: str, registry: str) -> Path:
        return self._write(f"{registry}.parquet", frame, {
            "source_url": source_url, "source_sha256": sha256,
            "retrieved_at": retrieved_at, "registry": registry,
        })

    def save_termination(self, frame: pl.DataFrame, *, source_url: str, sha256: str,
                         retrieved_at: str) -> Path:
        return self._write("termination.parquet", frame, {
            "source_url": source_url, "source_sha256": sha256,
            "retrieved_at": retrieved_at, "registry": "termination",
        })

    def save_company(self, frame: pl.DataFrame, *, source_url: str, sha256: str,
                     retrieved_at: str) -> Path:
        return self._write("company.parquet", frame, {
            "source_url": source_url, "source_sha256": sha256,
            "retrieved_at": retrieved_at, "registry": "company",
        })

    def load_company(self) -> dict[str, date]:
        path = self.root / "company.parquet"
        if not path.exists():
            raise FileNotFoundError("instrument evidence snapshot is missing: company")
        frame = pl.read_parquet(path)
        return dict(zip(frame["code"], frame["listing_date"], strict=True))

    def load(self) -> tuple[pl.DataFrame, pl.DataFrame, dict[str, str]]:
        """Return (registry rows, termination rows, retrieved_at by registry)."""
        frames, stamps = [], {}
        for name in ("isin_listed", "isin_unlisted"):
            path = self.root / f"{name}.parquet"
            if not path.exists():
                raise FileNotFoundError(f"instrument evidence snapshot is missing: {name}")
            frames.append(pl.read_parquet(path))
            stamps[name] = (pq.read_metadata(path).metadata or {}).get(
                b"retrieved_at", b"").decode()
        termination_path = self.root / "termination.parquet"
        if not termination_path.exists():
            raise FileNotFoundError("instrument evidence snapshot is missing: termination")
        stamps["termination"] = (pq.read_metadata(termination_path).metadata or {}).get(
            b"retrieved_at", b"").decode()
        return pl.concat(frames), pl.read_parquet(termination_path), stamps


def refresh_evidence(
    store: InstrumentEvidenceStore, *, today: date,
    fetch_bytes: Callable[[str], bytes] = _fetch_bytes,
    fetch_payload: Callable[[str], Any] = lambda url: fetch_json(url, timeout=60.0, max_attempts=3),
) -> dict[str, int]:
    """Download the three official registries once and persist normalized snapshots."""
    stamp = datetime.now(TAIPEI).isoformat()
    counts: dict[str, int] = {}
    for mode, registry in ((2, "isin_listed"), (1, "isin_unlisted")):
        url = ISIN_URL.format(mode=mode)
        raw = fetch_bytes(url)
        frame = parse_isin_registry(raw, registry)
        store.save_registry(frame, source_url=url, sha256=hashlib.sha256(raw).hexdigest(),
                            retrieved_at=stamp, registry=registry)
        counts[registry] = frame.height
    url = TERMINATION_URL.format(end=today)
    payload = fetch_payload(url)
    frame = parse_termination(payload)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    store.save_termination(frame, source_url=url, sha256=digest, retrieved_at=stamp)
    counts["termination"] = frame.height
    payload = fetch_payload(COMPANY_URL)
    frame = parse_company(payload)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    store.save_company(frame, source_url=COMPANY_URL, sha256=digest, retrieved_at=stamp)
    counts["company"] = frame.height
    return counts


# ── Derivation ─────────────────────────────────────────────────

@dataclass(frozen=True)
class Decision:
    code: str
    instrument_type: str
    source: str
    detail: str


def _registry_type(section: str, cfi: str, registry: str) -> str | None:
    # The unlisted registry has no sections; its CFI alone carries the class.
    kind = ("stock" if cfi.startswith("ES") else "preferred_share"
            if cfi.startswith(("EP", "ER", "EF")) else None
            ) if registry == "isin_unlisted" else _LISTED_SECTIONS.get(section)
    if kind == "stock":
        return "stock" if cfi.startswith("ES") else None
    if kind == "preferred_share":
        return "preferred_share" if cfi.startswith(("EP", "ER", "EF")) else None
    if kind == "etn":
        return "etn"
    return None


def decide(
    code: str, first_seen: date, last_seen: date, *,
    registry: Mapping[str, list[Mapping[str, Any]]],
    terminations: Mapping[str, date],
    stamps: Mapping[str, str],
    industry_member: bool = False,
    company_listed: date | None = None,
    tib_codes: frozenset[str] = frozenset(),
    etn_codes: frozenset[str] = frozenset(),
) -> Decision | None:
    """Settle one unresolved code from official evidence, or return None.

    ``company_listed`` is the company registry listing date for the code;
    ``tib_codes`` / ``etn_codes`` are the historical table members on ``first_seen``;
    ``industry_member`` says the code held an industry-table row that day, which the
    termination list needs (it names companies, not instrument classes).
    """
    if code in etn_codes:
        return Decision(code, "etn", f"twse:MI_INDEX:{ETN_TYPE}@{first_seen.isoformat()}", "historical ETN table")
    rows = registry.get(code, [])
    if len(rows) > 1:
        return None  # ambiguous ISIN identity
    if rows:
        row = rows[0]
        kind = _registry_type(row["section"], row["cfi"], row["registry"])
        if kind is None:
            return None
        if row["registry"] == "isin_listed":
            existed = row["listing_date"] <= first_seen or (
                kind == "stock" and (code in tib_codes
                                     or (company_listed is not None and company_listed <= first_seen)))
            if not existed:
                return None
            return Decision(code, kind, f"twse:isin_listed@{stamps['isin_listed'][:10]}",
                            f"{row['section']} {row['cfi']}")
        return Decision(code, kind, f"twse:isin_unlisted@{stamps['isin_unlisted'][:10]}",
                        f"{row['cfi']}")
    ended = terminations.get(code)
    if industry_member and ended is not None and ended >= last_seen:
        return Decision(code, "stock", f"twse:termination_list@{stamps['termination'][:10]}",
                        f"delisted company primary share, terminated {ended.isoformat()}")
    return None


def announcement_decision(
    code: str, subjects: list[tuple[str, str, str]],
) -> Decision | None:
    """Preferred-share evidence from official notices: (date, document no., subject)."""
    pattern = re.compile(rf"(?<![0-9A-Za-z]){re.escape(code)}(?![0-9A-Za-z])")
    for issued, number, subject in subjects:
        # A company code names the issuer, not the preferred class.
        named = re.sub(r"公司代[號号][:：]\s*[0-9A-Za-z]+", "", subject)
        if "特別股" in named and pattern.search(named):
            return Decision(code, "preferred_share", f"twse:announcement:{number}",
                            f"{issued} {subject[:80]}")
    return None


def _row(decision: Decision, first_seen: date, retrieved_at: str) -> dict[str, Any]:
    return {
        "code": decision.code, "exchange": "TWSE",
        "instrument_type": decision.instrument_type,
        "industry": None, "industry_status": "data_insufficient",
        "classification_effective_from": first_seen,
        "classification_source": decision.source,
        "classification_status": "verified",
        "retrieved_at": retrieved_at,
        "instrument_type_status": "verified",
        "primary_oos_eligible_type": False,
    }


def resolve_industry_only_codes(
    classifications: HistoricalClassificationStore,
    first_seen: Mapping[str, date],
    last_seen: Mapping[str, date],
    evidence: InstrumentEvidenceStore,
    classifier: TwseHistoricalClassifier | None = None,
    fetch_payload: Callable[[str], Any] = lambda url: fetch_json(url, timeout=60.0, max_attempts=3),
) -> dict[str, Any]:
    """Replace unresolved industry-only rows with registry-backed verified rows.

    Only codes whose *own* first-observed partition still holds an unresolved
    industry row are touched; ETF/TDR/REIT decisions from the historical tables
    are never overwritten.  Codes settled by nothing stay exactly as they were.
    """
    registry_rows, termination_rows, stamps = evidence.load()
    registry: dict[str, list[dict[str, Any]]] = {}
    for row in registry_rows.iter_rows(named=True):
        registry.setdefault(row["code"], []).append(row)
    terminations = dict(zip(termination_rows["code"], termination_rows["termination_date"], strict=True))
    companies = evidence.load_company()

    pending: dict[date, list[str]] = {}
    industry_members: set[str] = set()
    for code, day in first_seen.items():
        if not classifications.has(day):
            continue
        mine = pl.read_parquet(classifications.partition_path(day)).filter(pl.col("code") == code)
        if mine.height == 1 and mine["classification_status"][0] == "verified":
            continue
        if mine.height > 1:
            continue
        pending.setdefault(day, []).append(code)
        if mine.height == 1:
            industry_members.add(code)

    decisions: dict[str, Decision] = {}
    retry: dict[date, list[str]] = {}
    for day, codes in pending.items():
        for code in codes:
            decision = decide(code, day, last_seen[code], registry=registry,
                              terminations=terminations, stamps=stamps,
                              industry_member=code in industry_members,
                              company_listed=companies.get(code))
            if decision is not None:
                decisions[code] = decision
            else:
                retry.setdefault(day, []).append(code)

    swept = 0
    if retry:
        owns = classifier is None
        classifier = classifier or TwseHistoricalClassifier(store=classifications)
        try:
            for day, codes in sorted(retry.items()):
                tib = frozenset(classifier.codes_in_table(day, TIB_TYPE))
                etn = frozenset(classifier.codes_in_table(day, ETN_TYPE))
                swept += 2
                for code in codes:
                    decision = decide(code, day, last_seen[code], registry=registry,
                                      terminations=terminations, stamps=stamps,
                                      industry_member=code in industry_members,
                                      company_listed=companies.get(code),
                                      tib_codes=tib, etn_codes=etn)
                    if decision is not None:
                        decisions[code] = decision
        finally:
            if owns:
                classifier.close()

    announcements = 0
    for code in sorted(c for codes in pending.values() for c in codes if c not in decisions):
        start, end = last_seen[code] - timedelta(days=120), last_seen[code] + timedelta(days=120)
        if end < date(2017, 1, 1):
            continue  # the official notice database starts in 2017
        payload = fetch_payload(ANNOUNCEMENT_URL.format(start=max(start, date(2017, 1, 1)), end=end))
        announcements += 1
        rows = [(str(r[1]), str(r[2]), str(r[3])) for r in payload.get("data") or []]
        decision = announcement_decision(code, rows)
        if decision is not None:
            decisions[code] = decision

    stamp = datetime.now(TAIPEI).isoformat()
    by_day: dict[date, list[Decision]] = {}
    for code, decision in decisions.items():
        by_day.setdefault(first_seen[code], []).append(decision)
    for day, items in by_day.items():
        classifications.merge(day, [_row(item, day, stamp) for item in items])

    unresolved = sorted(code for codes in pending.values() for code in codes
                        if code not in decisions)
    kinds: dict[str, int] = {}
    for decision in decisions.values():
        kinds[decision.instrument_type] = kinds.get(decision.instrument_type, 0) + 1
    return {"considered": sum(len(v) for v in pending.values()),
            "resolved": len(decisions), "by_type": kinds,
            "sweep_requests": swept, "announcement_requests": announcements,
            "unresolved_codes": unresolved}

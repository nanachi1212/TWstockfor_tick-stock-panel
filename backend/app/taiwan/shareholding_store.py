"""TDCC weekly shareholding distribution (集保戶股權分散表), 千張大戶 summary.

Official open data (opendata.tdcc.com.tw, dataset 1-5) only serves the latest
week, so history accumulates one snapshot per weekly publication. Week-over-week
change exists only when the previous persisted week is present; it is never
assumed to be 0. Level 15 is "1,000,001 shares and above" (> 1,000 lots);
level 17 is the total row.
"""
from __future__ import annotations

import csv
import io
import logging
import os
import tempfile
import threading
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from app.taiwan.providers.http import DEFAULT_USER_AGENT, throttle
from app.taiwan.realtime.calendar import taipei_now

logger = logging.getLogger(__name__)

TDCC_URL = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"
BIG_LEVEL = "15"
TOTAL_LEVEL = "17"

_SCHEMA = pl.Schema({
    "symbol": pl.String, "date": pl.Date, "total_holders": pl.Int64, "total_shares": pl.Int64,
    "big_holders": pl.Int64, "big_shares": pl.Int64, "big_holder_pct": pl.Float64,
    "source_url": pl.String, "retrieved_at": pl.String,
})
_LOCK = threading.Lock()


def parse_tdcc_csv(text: str, code_to_symbol: dict[str, str], *, retrieved_at: str) -> pl.DataFrame:
    """Keep codes known to the security master; big_holder_pct is a percent value (84.78 = 84.78%)."""
    rows: dict[tuple[str, date], dict[str, Any]] = {}
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    next(reader, None)
    for rec in reader:
        if len(rec) < 6:
            continue
        symbol = code_to_symbol.get(rec[1].strip())
        level = rec[2].strip()
        if symbol is None or level not in (BIG_LEVEL, TOTAL_LEVEL):
            continue
        try:
            day = datetime.strptime(rec[0].strip(), "%Y%m%d").date()
            holders, shares = int(rec[3]), int(rec[4])
        except ValueError:
            continue
        row = rows.setdefault((symbol, day), {"symbol": symbol, "date": day})
        prefix = "big" if level == BIG_LEVEL else "total"
        row[f"{prefix}_holders"], row[f"{prefix}_shares"] = holders, shares
    out = []
    for row in rows.values():
        if "big_shares" not in row or not row.get("total_shares"):
            continue  # incomplete row stays absent instead of becoming 0%
        out.append({**row, "big_holder_pct": row["big_shares"] / row["total_shares"] * 100,
                    "source_url": TDCC_URL, "retrieved_at": retrieved_at})
    return pl.DataFrame(out, schema=_SCHEMA)


def _parse_week_date(val: Any) -> date | None:
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, (str, int)):
        raw = str(val).strip()
        for fmt in ("%Y-%m-%d", "%Y%m%d"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                pass
    return None


def _default_path() -> Path:
    from app.taiwan.data_root import taiwan_data_root

    return taiwan_data_root() / "shareholding.parquet"


class TaiwanShareholdingStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _default_path()

    def read(self) -> pl.DataFrame:
        if not self.path.exists():
            return pl.DataFrame(schema=_SCHEMA)
        return pl.read_parquet(self.path)

    def write(self, frame: pl.DataFrame) -> int:
        if frame.is_empty():
            return 0
        with _LOCK:
            merged = (pl.concat([self.read(), frame.select(list(_SCHEMA)).cast(_SCHEMA)])
                      .unique(subset=["symbol", "date"], keep="last").sort(["symbol", "date"]))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
            os.close(fd)
            try:
                merged.write_parquet(tmp)
                os.replace(tmp, self.path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        return frame.height

    def latest_week(self) -> date | None:
        """Return the latest stored TDCC week date, or None if store is empty."""
        data = self.read()
        if data.is_empty() or "date" not in data.columns:
            return None
        valid_dates: list[date] = []
        for d in data["date"].drop_nulls().unique().to_list():
            parsed = _parse_week_date(d)
            if parsed is not None:
                valid_dates.append(parsed)
        return max(valid_dates) if valid_dates else None

    def latest_with_change(self, symbols: list[str] | None = None, before: date | None = None) -> pl.DataFrame:
        """Latest week per symbol plus big_holder_pct_change (percentage points) vs the previous stored week.

        ``before`` keeps only weeks dated strictly earlier: a week is published after its data date.
        """
        data = self.read()
        if "date" in data.columns and not data.is_empty():
            dtype = data["date"].dtype
            if dtype != pl.Date:
                if dtype.is_temporal():
                    data = data.with_columns(pl.col("date").dt.date())
                elif dtype in (pl.String, pl.Utf8):
                    data = data.with_columns(
                        pl.coalesce([
                            pl.col("date").str.to_date("%Y-%m-%d", strict=False),
                            pl.col("date").str.to_date("%Y%m%d", strict=False),
                        ]).alias("date")
                    )
                elif dtype.is_integer():
                    data = data.with_columns(
                        pl.col("date").cast(pl.String).str.to_date("%Y%m%d", strict=False).alias("date")
                    )
        before_date = _parse_week_date(before) if before is not None else None
        if before_date is not None:
            data = data.filter(pl.col("date") < before_date)
        if symbols is not None:
            data = data.filter(pl.col("symbol").is_in(symbols))
        if data.is_empty():
            return data.with_columns(pl.lit(None, pl.Float64).alias("big_holder_pct_change"),
                                     pl.lit(None, pl.Date).alias("previous_date"))
        weeks = sorted(data["date"].drop_nulls().unique().to_list())
        if not weeks:
            return (data.unique(subset=["symbol"], keep="last")
                    .with_columns(pl.lit(None, pl.Float64).alias("big_holder_pct_change"),
                                  pl.lit(None, pl.Date).alias("previous_date")))
        latest = weeks[-1]
        prev = weeks[-2] if len(weeks) > 1 else None
        cur = data.filter(pl.col("date") == latest)
        if prev is None:
            return cur.with_columns(pl.lit(None, pl.Float64).alias("big_holder_pct_change"),
                                    pl.lit(None, pl.Date).alias("previous_date"))
        d_latest = _parse_week_date(latest)
        d_prev = _parse_week_date(prev)
        is_adjacent_week = (
            d_latest is not None
            and d_prev is not None
            and 0 < (d_latest - d_prev).days <= 7
        )
        if not is_adjacent_week:
            return cur.with_columns(pl.lit(None, pl.Float64).alias("big_holder_pct_change"),
                                    pl.lit(None, pl.Date).alias("previous_date"))
        before_df = data.filter(pl.col("date") == prev).select(
            "symbol", pl.col("big_holder_pct").alias("_prev"))
        return (cur.join(before_df, on="symbol", how="left")
                .with_columns((pl.col("big_holder_pct") - pl.col("_prev")).alias("big_holder_pct_change"),
                              pl.lit(prev, pl.Date).alias("previous_date"))
                .drop("_prev"))

    def refresh(self, fetch_text=None, code_to_symbol: dict[str, str] | None = None) -> dict[str, Any]:
        """One HTTP call for the whole market; re-fetching the same week is an idempotent upsert."""
        if code_to_symbol is None:
            from app.taiwan.universe import get_security_master

            master = get_security_master().to_dataframe(supported_only=True)
            code_to_symbol = dict(zip(master["code"].to_list(), master["symbol"].to_list(), strict=True))
        text = (fetch_text or _fetch_text)(TDCC_URL)
        frame = parse_tdcc_csv(text, code_to_symbol, retrieved_at=taipei_now().isoformat())
        written = self.write(frame)
        latest = frame["date"].max() if not frame.is_empty() else None
        return {"rows_written": written, "latest": str(latest) if latest else None}


def _fetch_text(url: str = TDCC_URL) -> str:
    throttle(url)
    req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8-sig")


def get_store_latest_week(store: Any) -> date | None:
    """Safely obtain the latest stored week date from a store or mock object."""
    if hasattr(store, "latest_week") and callable(store.latest_week):
        try:
            res = store.latest_week()
            if isinstance(res, (date, datetime)):
                return _parse_week_date(res)
            if res is None:
                return None
        except Exception:
            pass
    if hasattr(store, "read") and callable(store.read):
        try:
            data = store.read()
            if isinstance(data, pl.DataFrame):
                if data.is_empty() or "date" not in data.columns:
                    return None
                valid_dates = [_parse_week_date(d) for d in data["date"].drop_nulls().unique().to_list()]
                valid_dates = [d for d in valid_dates if d is not None]
                return max(valid_dates) if valid_dates else None
        except Exception:
            pass
    return None


def needs_shareholding_catchup(
    store: TaiwanShareholdingStore | Any | None = None,
    as_of: date | None = None,
) -> tuple[bool, str]:
    """Determine whether shareholding data needs catchup.

    Rule:
    - Empty store: fetch needed.
    - Latest stored week is >= 7 days older than today (Asia/Taipei): fetch needed.
    - Otherwise: skip.
    """
    target_store = store or TaiwanShareholdingStore()
    latest = get_store_latest_week(target_store)
    if latest is None:
        return True, "empty"
    today = as_of or taipei_now().date()
    days = (today - latest).days
    if days >= 7:
        return True, f"stale:{latest}:{days}d"
    return False, f"current:{latest}:{days}d"


def catchup_shareholding(
    store: TaiwanShareholdingStore | Any | None = None,
    fetch_text: Any = None,
    code_to_symbol: dict[str, str] | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Execute startup catch-up for TDCC weekly shareholding table if needed.

    Safety:
    - If store is fresh (< 7 days), skips without calling fetch or write.
    - If fetch raises or returns empty, logs warning and returns without write.
    - Only writes if the fetched week is newer than what is already stored.
    """
    target_store = store or TaiwanShareholdingStore()
    try:
        needed, reason = needs_shareholding_catchup(target_store, as_of=as_of)
    except Exception as exc:
        logger.warning("Checking shareholding catchup failed: %s", exc)
        return {"status": "error", "reason": "check_failed", "error": str(exc)}

    if not needed:
        logger.info("Shareholding catchup skipped: %s", reason)
        return {"status": "skipped", "reason": reason}

    fn = fetch_text or _fetch_text
    try:
        try:
            raw = fn(TDCC_URL)
        except TypeError:
            raw = fn()
    except Exception as exc:
        logger.warning("Shareholding catchup fetch failed: %s", exc)
        return {"status": "error", "reason": "fetch_failed", "error": str(exc)}

    if not raw:
        logger.warning("Shareholding catchup fetch returned empty data")
        return {"status": "skipped", "reason": "empty_fetch"}

    if isinstance(raw, pl.DataFrame):
        frame = raw
    else:
        try:
            if code_to_symbol is None:
                from app.taiwan.universe import get_security_master

                master = get_security_master().to_dataframe(supported_only=True)
                code_to_symbol = dict(zip(master["code"].to_list(), master["symbol"].to_list(), strict=True))
            frame = parse_tdcc_csv(raw, code_to_symbol, retrieved_at=taipei_now().isoformat())
        except Exception as exc:
            logger.warning("Shareholding catchup parse failed: %s", exc)
            return {"status": "error", "reason": "parse_failed", "error": str(exc)}

    if frame.is_empty() or "date" not in frame.columns:
        logger.warning("Shareholding catchup parsed frame is empty")
        return {"status": "skipped", "reason": "empty_frame"}

    fetched_dates = [_parse_week_date(d) for d in frame["date"].drop_nulls().unique().to_list()]
    fetched_dates = [d for d in fetched_dates if d is not None]
    if not fetched_dates:
        logger.warning("Shareholding catchup could not determine date from fetched data")
        return {"status": "skipped", "reason": "missing_date"}
    fetched_latest = max(fetched_dates)

    latest_stored = get_store_latest_week(target_store)
    if latest_stored is not None and fetched_latest <= latest_stored:
        logger.info(
            "Shareholding catchup: fetched week %s is not newer than latest stored %s; skipping write",
            fetched_latest, latest_stored,
        )
        return {"status": "skipped", "reason": f"already_stored:{latest_stored}", "latest": str(fetched_latest)}

    try:
        rows = target_store.write(frame)
        logger.info("Shareholding catchup written for week %s (%d rows)", fetched_latest, rows)
        return {"status": "written", "latest": str(fetched_latest), "rows_written": rows}
    except Exception as exc:
        logger.warning("Shareholding catchup write failed: %s", exc)
        return {"status": "error", "reason": "write_failed", "error": str(exc)}

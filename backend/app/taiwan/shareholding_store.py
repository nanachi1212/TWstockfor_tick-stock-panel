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

    def latest_with_change(self, symbols: list[str] | None = None, before: date | None = None) -> pl.DataFrame:
        """Latest week per symbol plus big_holder_pct_change (percentage points) vs the previous stored week.

        ``before`` keeps only weeks dated strictly earlier: a week is published after its data date.
        """
        data = self.read()
        if before is not None:
            data = data.filter(pl.col("date") < before)
        if symbols is not None:
            data = data.filter(pl.col("symbol").is_in(symbols))
        if data.is_empty():
            return data.with_columns(pl.lit(None, pl.Float64).alias("big_holder_pct_change"),
                                     pl.lit(None, pl.Date).alias("previous_date"))
        weeks = sorted(data["date"].unique().to_list())
        latest = weeks[-1]
        prev = weeks[-2] if len(weeks) > 1 else None
        cur = data.filter(pl.col("date") == latest)
        if prev is None:
            return cur.with_columns(pl.lit(None, pl.Float64).alias("big_holder_pct_change"),
                                    pl.lit(None, pl.Date).alias("previous_date"))
        before = data.filter(pl.col("date") == prev).select(
            "symbol", pl.col("big_holder_pct").alias("_prev"))
        d_latest = _parse_week_date(latest)
        d_prev = _parse_week_date(prev)
        is_adjacent_week = (
            d_latest is not None
            and d_prev is not None
            and 0 < (d_latest - d_prev).days <= 7
        )
        change_expr = (
            (pl.col("big_holder_pct") - pl.col("_prev")).alias("big_holder_pct_change")
            if is_adjacent_week
            else pl.lit(None, pl.Float64).alias("big_holder_pct_change")
        )
        return (cur.join(before, on="symbol", how="left")
                .with_columns(change_expr,
                              pl.lit(prev).alias("previous_date"))
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


def _fetch_text(url: str) -> str:
    throttle(url)
    req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8-sig")

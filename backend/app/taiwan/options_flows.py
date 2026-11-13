"""期權籌碼: 台指選擇權各履約價未平倉分布 + 外資台指期／選擇權部位。

未平倉分布來自期交所 OpenAPI「選擇權每日交易行情」(只提供最新一個交易日)。
每次抓到的一般盤 TXO 未平倉存成當日檔案，增減和「前一個有存檔的交易日」比較；
沒有前一日檔案時增減為 null，不補 0。外資部位沿用市場情緒服務的 FinMind 來源。

Current-market display only: no PIT semantics, past dates read stored snapshots.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.taiwan.data_root import taiwan_data_root
from app.taiwan.providers.http import fetch_json

logger = logging.getLogger(__name__)

TAIFEX_DAILY_OPTIONS_URL = "https://openapi.taifex.com.tw/v1/DailyMarketReportOpt"
SOURCE = "taifex_openapi:DailyMarketReportOpt"
_REFETCH_SECONDS = 30 * 60
_CONTRACT_RE = re.compile(r"^\d{6}([WF]\d+)?$")
_SIDES = {"買權": "call", "賣權": "put"}

_fetch_lock = threading.Lock()
# (monotonic time of the last attempt, its error message or None on success). The error is
# repeated for the whole cooldown so a failed refresh never reads as fresh data.
_last_refresh: tuple[float, str | None] | None = None


def parse_txo_open_interest(rows: list[dict[str, Any]]) -> tuple[date, dict[str, list[list[float]]]] | None:
    """Regular-session TXO rows -> (trade date, {contract: [[strike, call_oi, put_oi], ...]})."""
    by_contract: dict[str, dict[float, dict[str, int]]] = {}
    days: set[str] = set()
    for row in rows:
        if row.get("Contract") != "TXO" or row.get("TradingSession") != "一般":
            continue
        side = _SIDES.get(str(row.get("CallPut") or ""))
        contract = str(row.get("ContractMonth(Week)") or "").strip()
        oi = str(row.get("OpenInterest") or "").strip()
        try:
            strike = float(str(row.get("StrikePrice") or ""))
        except ValueError:
            continue
        if side is None or not _CONTRACT_RE.match(contract) or not oi.isdigit() or strike <= 0:
            continue
        days.add(str(row.get("Date") or ""))
        by_contract.setdefault(contract, {}).setdefault(strike, {})[side] = int(oi)
    if len(days) != 1 or not by_contract:
        return None  # empty or mixed-date payload: never guess which day it is
    day = datetime.strptime(days.pop(), "%Y%m%d").date()
    return day, {
        contract: [[strike, sides.get("call"), sides.get("put")] for strike, sides in sorted(strikes.items())]
        for contract, strikes in sorted(by_contract.items())
    }


class TxoOpenInterestStore:
    """One JSON file per trade date: {contract: [[strike, call_oi, put_oi], ...]}."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or taiwan_data_root() / "txo_open_interest"

    def write(self, day: date, contracts: dict[str, list[list[float]]]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.data_dir / f"{day.isoformat()}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(contracts, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)

    def read(self, day: date) -> dict[str, list[list[float]]] | None:
        try:
            return json.loads((self.data_dir / f"{day.isoformat()}.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return None

    def dates(self) -> list[date]:
        out = []
        for path in self.data_dir.glob("*.json"):
            try:
                out.append(date.fromisoformat(path.stem))
            except ValueError:
                continue
        return sorted(out)


def refresh_latest(store: TxoOpenInterestStore, fetch: Callable[..., Any] = fetch_json) -> str | None:
    """Fetch TAIFEX's latest day at most every 30 minutes; returns an error message on failure."""
    global _last_refresh
    with _fetch_lock:
        now = time.monotonic()
        if _last_refresh is not None and now - _last_refresh[0] < _REFETCH_SECONDS:
            return _last_refresh[1]
        error: str | None = None
        try:
            parsed = parse_txo_open_interest(fetch(TAIFEX_DAILY_OPTIONS_URL, timeout=60.0))
        except Exception as exc:  # network / JSON: the stored days still serve
            logger.warning("TAIFEX daily options fetch failed: %s", type(exc).__name__)
            parsed, error = None, f"期交所選擇權行情暫時無法取得（{type(exc).__name__}）"
        if parsed is None:
            error = error or "期交所選擇權行情沒有可用的台指選擇權資料"
        else:
            store.write(*parsed)
        _last_refresh = (now, error)
        return error


def _contract_view(contract: str, strikes: list[list[float]], previous: list[list[float]] | None) -> dict[str, Any]:
    before = {row[0]: row for row in previous or []}

    def change(now: float | None, strike: float, index: int) -> float | None:
        old = before.get(strike)
        if previous is None or now is None or old is None or old[index] is None:
            return None
        return now - old[index]

    rows = [{"strike": s, "call": c, "put": p, "call_change": change(c, s, 1), "put_change": change(p, s, 2)}
            for s, c, p in strikes]

    def peak(key: str) -> float | None:
        best = max((r for r in rows if (r[key] or 0) > 0), key=lambda r: (r[key], -r["strike"]), default=None)
        return best["strike"] if best else None

    return {
        "contract": contract,
        "strikes": rows,
        "call_peak": peak("call"),
        "put_peak": peak("put"),
        "total_oi": sum((r["call"] or 0) + (r["put"] or 0) for r in rows),
    }


def _foreign_positions(day: date) -> dict[str, Any]:
    from app.taiwan.market_sentiment_service import get_market_sentiment_service

    svc = get_market_sentiment_service()
    futures, futures_status, futures_msg = svc._fetch_tx_futures(day)
    options, options_status, options_msg = svc._fetch_txo_options(day)
    return {
        "futures": futures, "futures_status": futures_status, "futures_message": futures_msg,
        "options": options, "options_status": options_status, "options_message": options_msg,
        "source": "finmind",
    }


def _latest_session() -> date:
    from app.taiwan.daily_update import resolve_target_latest_trading_date

    return resolve_target_latest_trading_date()


def options_flows_snapshot(
    target: date | None = None,
    *,
    store: TxoOpenInterestStore | None = None,
    fetch: Callable[..., Any] = fetch_json,
    foreign: Callable[[date], dict[str, Any]] = _foreign_positions,
) -> dict[str, Any]:
    store = store or TxoOpenInterestStore()
    message = refresh_latest(store, fetch) if target is None else None
    stored = store.dates()
    day = target if target is not None else (stored[-1] if stored else None)
    snapshot = store.read(day) if day else None
    if day is None or snapshot is None:
        return {"date": day.isoformat() if day else None, "status": "unavailable", "source": SOURCE,
                "message": message or "尚無這一天的選擇權未平倉資料（期交所只提供最新交易日，之前沒存到的日期無法回補）",
                "previous_date": None, "contracts": [], "default_contract": None,
                # FinMind positions do not depend on TAIFEX: still show them for the latest session.
                "foreign": foreign(day or _latest_session())}
    previous_day = next((d for d in reversed(stored) if d < day), None)
    previous = store.read(previous_day) if previous_day else None
    contracts = [_contract_view(c, strikes, (previous or {}).get(c) if previous is not None else None)
                 for c, strikes in snapshot.items()]
    monthly = [c for c in contracts if len(c["contract"]) == 6 and c["total_oi"] > 0]
    default = (monthly or [c for c in contracts if c["total_oi"] > 0] or contracts)[0]["contract"]
    return {
        "date": day.isoformat(), "status": "available", "source": SOURCE, "message": message,
        "previous_date": previous_day.isoformat() if previous_day else None,
        "contracts": contracts, "default_contract": default, "foreign": foreign(day),
    }


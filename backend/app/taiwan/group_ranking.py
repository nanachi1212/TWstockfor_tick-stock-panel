"""Watchlist group ranking (族群排名), ported from EquiLens 衡析 grouprank (MIT).

Each watchlist group is ranked by its √turnover-weighted raw-close change against the previous
research session. A constituent whose turnover is under 0.5% of the group's or under 10 million TWD
is listed but left out of the score, so neither an illiquid outlier nor one heavyweight decides it.
A group with a member that has no row on the session is still ranked but marked incomplete, and the
snapshot carries the census coverage of the members. Current-membership view: past dates are ranked
with today's group membership, so this is not a Historical PIT evaluation.
"""
from __future__ import annotations

import math
from datetime import date
from statistics import fmean, median
from typing import Any

import polars as pl

from app.services import watchlist
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.daily_update import resolve_target_latest_trading_date
from app.taiwan.market_heatmap import (
    SNAPSHOT_STATUS,
    day_coverage,
    previous_coverage_status,
    session_changes,
)
from app.taiwan.observed_universe import ObservedUniverseStore
from app.taiwan.realtime.calendar import TaiwanTradingCalendar
from app.taiwan.research_metrics import research_sessions
from app.taiwan.universe import TaiwanSecurityMaster, get_security_master

RULES = {"min_share": 0.005, "min_amount": 1e7}


def _group_row(group: dict, quotes: dict[str, dict], listed: set[str], rules: dict) -> dict[str, Any]:
    quoted = [quotes[symbol] for symbol in group["symbols"] if symbol in quotes]
    missing = [symbol for symbol in group["symbols"] if symbol not in listed]
    group_amount = sum(q["amount"] for q in quoted)
    items = []
    for q in quoted:
        share = q["amount"] / group_amount
        items.append({
            "symbol": q["symbol"], "name": q["name"], "close": q["close"], "change_pct": q["change_pct"],
            "amount": q["amount"], "share": share, "weight": 0.0,
            "excluded": share < rules["min_share"] or q["amount"] < rules["min_amount"],
        })
    used = [item for item in items if not item["excluded"]]
    root_total = sum(math.sqrt(item["amount"]) for item in used)
    for item in used:
        item["weight"] = math.sqrt(item["amount"]) / root_total
    used_amount = sum(item["amount"] for item in used)
    changes = [item["change_pct"] for item in items]
    advance, decline = sum(c > 0 for c in changes), sum(c < 0 for c in changes)
    return {
        "id": group["id"], "name": group["name"], "color": group["color"], "rank": None,
        "score": sum(i["change_pct"] * math.sqrt(i["amount"]) for i in used) / root_total if used else None,
        "weighted_avg_change": sum(i["change_pct"] * i["amount"] for i in used) / used_amount if used else None,
        "average_change": fmean(changes) if changes else None,
        "median_change": median(changes) if changes else None,
        "members": len(group["symbols"]), "quoted": len(items), "used": len(used), "excluded": len(items) - len(used),
        "missing_symbols": missing, "complete": not missing,
        "advance": advance, "decline": decline, "flat": len(items) - advance - decline,
        "breadth": advance / len(items) if items else None,
        "amount": group_amount,
        "top_symbol": max(used, key=lambda i: i["amount"])["symbol"] if used else None,
        "items": sorted(items, key=lambda i: -i["change_pct"]),
    }


def rank_groups(groups: list[dict], quotes: pl.DataFrame, rules: dict = RULES) -> list[dict]:
    """Rank ``groups`` ([{id, name, color, symbols}]) on one session of ``quotes``.

    ``quotes`` has symbol/name/close/change_pct/amount; change_pct is decimal. Only members with a
    change and a positive turnover count; a member without any row is listed in missing_symbols.
    Groups without a score get rank None and go last.
    """
    usable = quotes.filter(pl.col("change_pct").is_not_null() & pl.col("amount").is_finite() & (pl.col("amount") > 0))
    by_symbol = {row["symbol"]: row for row in usable.to_dicts()}
    listed = set(quotes["symbol"].to_list())
    rows = [_group_row(group, by_symbol, listed, rules) for group in groups]
    ranked = sorted((r for r in rows if r["score"] is not None), key=lambda r: (-r["score"], -r["breadth"], r["name"]))
    for rank, row in enumerate(ranked, 1):
        row["rank"] = rank
    return ranked + [r for r in rows if r["score"] is None]


class TaiwanGroupRankingService:
    def __init__(
        self,
        daily_store: TaiwanDailyStore | None = None,
        calendar: TaiwanTradingCalendar | None = None,
        security_master: TaiwanSecurityMaster | None = None,
        evidence_store: ObservedUniverseStore | None = None,
        groups: list[dict] | None = None,
        entries: list[dict] | None = None,
    ) -> None:
        self.daily_store = daily_store or TaiwanDailyStore()
        self.calendar = calendar or TaiwanTradingCalendar()
        self.security_master = security_master or get_security_master()
        self.evidence_store = evidence_store or ObservedUniverseStore()
        self.groups = groups
        self.entries = entries

    def snapshot(self, target_date: date | None = None) -> dict[str, Any]:
        target = target_date or resolve_target_latest_trading_date(self.calendar, evidence_store=self.evidence_store)
        available = set(self.daily_store.available_dates())
        out: dict[str, Any] = {
            "date": str(target), "previous_date": None, "status": "unavailable", "reason": "no_daily_data",
            "price_semantics": "raw_close", "rules": RULES, "coverage": None, "groups": [],
        }
        if target not in available:
            return out
        sessions = research_sessions(target, 3, available, self.calendar, self.evidence_store)
        previous = sessions[-2] if len(sessions) > 1 else None
        out.update(previous_date=str(previous) if previous else None, status="available", reason=None)

        groups = watchlist.list_groups() if self.groups is None else self.groups
        if not groups:
            return out
        entries = watchlist.list_symbols() if self.entries is None else self.entries
        members: dict[str, list[str]] = {group["id"]: [] for group in groups}
        for entry in entries:
            for group_id in entry.get("group_ids") or []:
                if group_id in members:
                    members[group_id].append(entry["symbol"])
        grouped = [{**group, "symbols": members[group["id"]]} for group in groups]

        names = self.security_master.to_dataframe(supported_only=True).select("symbol", "name")
        symbols = sorted({symbol for group in grouped for symbol in group["symbols"]})
        # The previous session's change is against the one before it, so 3 sessions rank both days.
        quotes = session_changes(self.daily_store, symbols, sessions).join(names, on="symbol", how="left").with_columns(
            pl.coalesce("name", "symbol").alias("name"))
        members_frame = pl.DataFrame({"symbol": symbols}, schema={"symbol": pl.String})
        coverage = day_coverage(quotes, members_frame, self.evidence_store, target)
        coverage["previous_status"] = previous_coverage_status(
            quotes, members_frame, self.evidence_store, previous, available)
        status, reason = SNAPSHOT_STATUS[coverage["status"]]
        out.update(status=status, reason=reason, coverage=coverage)
        previous_ranks: dict[str, int | None] = {}
        # A rank move against an incomplete previous session would be invented, so it stays None.
        if coverage["previous_status"] == "complete":
            previous_ranks = {row["id"]: row["rank"] for row in rank_groups(grouped, quotes.filter(pl.col("date") == previous))}
        rows = rank_groups(grouped, quotes.filter(pl.col("date") == target))
        for row in rows:
            previous_rank = previous_ranks.get(row["id"])
            row["previous_rank"] = previous_rank
            row["rank_change"] = previous_rank - row["rank"] if previous_rank is not None and row["rank"] is not None else None
        out["groups"] = rows
        return out

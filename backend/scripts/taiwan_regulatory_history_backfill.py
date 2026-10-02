"""Maintainer entry point: backfill official regulatory history for PIT replay."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from app.taiwan.providers.http import fetch_json
from app.taiwan.regulatory_history import (
    RegulatoryHistoryStore,
    backfill_announcements,
    backfill_cmode,
)


def _fetch(url: str):
    return fetch_json(url, timeout=60.0, max_attempts=3)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="taiwan_regulatory_history_backfill")
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default=None, help="default: last verified TPEx session")
    parser.add_argument("--announcements", action="store_true", help="TWSE/TPEx disposition months")
    parser.add_argument("--cmode", action="store_true", help="dated TPEx status lists")
    args = parser.parse_args(argv)
    from app.taiwan.observed_universe import ObservedUniverseStore

    store = RegulatoryHistoryStore()
    sessions = sorted(ObservedUniverseStore().session_dates("TPEX"))
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else sessions[-1]
    report: dict[str, object] = {}
    if args.announcements:
        report["announcements"] = backfill_announcements(store, start, end, fetch=_fetch)
    if args.cmode:
        report["cmode"] = backfill_cmode(
            store, [d for d in sessions if start <= d <= end], fetch=_fetch)
    report["conflicts"] = store.conflicts()
    print(json.dumps(report, ensure_ascii=False, default=str))
    failed = any(part.get("errors") for part in report.values() if isinstance(part, dict))
    return 1 if failed or report["conflicts"] else 0


if __name__ == "__main__":
    sys.exit(main())

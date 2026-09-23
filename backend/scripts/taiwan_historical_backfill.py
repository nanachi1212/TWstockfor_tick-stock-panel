"""Taiwan historical backfill worker — Background Data Lane CLI.

One bounded run per invocation; designed to be scheduled nightly.  Reaching the
daily budget is a normal finish and exits 0.  A second concurrent instance
detects the lock and exits 0 without doing work.

Usage
-----
    cd backend
    uv run --frozen python -m scripts.taiwan_historical_backfill --status
    uv run --frozen python -m scripts.taiwan_historical_backfill
    uv run --frozen python -m scripts.taiwan_historical_backfill \
        --daily-session-budget 300 --classification-request-budget 1200

    # first-time smoke check, no scheduling involved
    uv run --frozen python -m scripts.taiwan_historical_backfill \
        --daily-session-budget 5 --classification-request-budget 0

Exit codes
----------
    0  work done, budget reached, nothing to do, or another instance is running
    1  unexpected failure
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date

from app.taiwan.backfill_worker import (
    CENSUS_START,
    DEFAULT_CLASSIFICATION_BUDGET,
    DEFAULT_SESSION_BUDGET,
    UNLIMITED_BUDGET,
    TaiwanHistoricalBackfillWorker,
    WorkerBusyError,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="taiwan_historical_backfill",
        description="Resumable Taiwan historical observed-universe census "
                    "and point-in-time TWSE classification.",
    )
    parser.add_argument("--status", action="store_true",
                        help="print a machine-readable status snapshot and exit")
    parser.add_argument("--start", type=date.fromisoformat, default=CENSUS_START,
                        help=f"census start date (default {CENSUS_START.isoformat()})")
    parser.add_argument("--end", type=date.fromisoformat, default=None,
                        help="census end date (default: today in Taipei)")
    parser.add_argument("--daily-session-budget", type=int, default=DEFAULT_SESSION_BUDGET,
                        help="max census sessions per exchange per run "
                             f"(default {DEFAULT_SESSION_BUDGET}; 2 requests per session). "
                             "0 means UNLIMITED — run until done or interrupted.")
    parser.add_argument("--classification-request-budget", type=int,
                        default=DEFAULT_CLASSIFICATION_BUDGET,
                        help="max A2b classification requests per run "
                             f"(default {DEFAULT_CLASSIFICATION_BUDGET}). "
                             "0 means UNLIMITED.")
    parser.add_argument("--long-run", action="store_true",
                        help="LongRun: unlimited budgets for both phases. Rate limiting, "
                             "retry, locking, atomic writes and checkpointing are unchanged; "
                             "Ctrl+C stops cleanly and the next run resumes.")
    parser.add_argument("--skip-census", action="store_true",
                        help="skip the A2a census phase (a budget of 0 now means unlimited)")
    parser.add_argument("--skip-classification", action="store_true",
                        help="skip the A2b classification phase")
    parser.add_argument("--force-unlock", action="store_true",
                        help="drop an existing lock before starting (use only when "
                             "certain no other worker is running)")
    parser.add_argument("--json", action="store_true",
                        help="print the run result as JSON")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    worker = TaiwanHistoricalBackfillWorker()

    if args.status:
        print(json.dumps(worker.status(start=args.start, end=args.end),
                         ensure_ascii=False, indent=2))
        return 0

    session_budget = UNLIMITED_BUDGET if args.long_run else args.daily_session_budget
    classification_budget = (
        UNLIMITED_BUDGET if args.long_run else args.classification_request_budget)
    if args.long_run:
        logging.getLogger(__name__).info(
            "LongRun: unlimited budgets; rate limiting and checkpointing unchanged. "
            "Ctrl+C once to stop cleanly.")

    try:
        result = worker.run_once(
            start=args.start,
            end=args.end,
            session_budget=session_budget,
            classification_budget=classification_budget,
            force_unlock=args.force_unlock,
            skip_census=args.skip_census,
            skip_classification=args.skip_classification,
        )
    except WorkerBusyError as exc:
        # Not an error: yesterday's run is still going.
        logging.getLogger(__name__).info("%s", exc)
        return 0

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        census = result["census"]
        for exchange, stats in census.items():
            logging.getLogger(__name__).info(
                "%s census: %d sessions, %d empty, %d rows, %d failed%s",
                exchange, stats["sessions"], stats["empty"], stats["rows"],
                len(stats["failed"]), " (stopped early)" if stats["stopped_early"] else "")
        classification = result["classification"]
        logging.getLogger(__name__).info(
            "classification: %d dates, %d rows, %d requests, queue depth %d",
            classification["dates_done"], classification["rows"],
            classification["requests_used"], classification.get("queue_depth", 0))
        logging.getLogger(__name__).info("elapsed %.1fs", result["elapsed_seconds"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

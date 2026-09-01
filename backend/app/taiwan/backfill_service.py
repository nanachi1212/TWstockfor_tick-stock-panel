"""Taiwan Full-Market Daily Backfill & Refresh Orchestration Service.

Provides a safe, bounded-concurrency, resumable workflow for:
  1. Multi-symbol historical daily backfill in configurable batches
  2. Live progress and failure reporting with ETA estimation
  3. Safe resumption from existing TaiwanDailyStore partitions
  4. Robust rate-limit backoff and fallback tracking
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Any, Callable

import polars as pl

from app.taiwan.daily_refresh import TaiwanDailyRefreshService
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.providers.hybrid_provider import TaiwanHybridProvider
from app.taiwan.realtime.calendar import TaiwanTradingCalendar
from app.taiwan.universe import get_security_master

logger = logging.getLogger(__name__)

DEFAULT_BACKFILL_START = "2024-01-01"
DEFAULT_BATCH_SIZE = 20
DEFAULT_CONCURRENCY = 5


class TaiwanBackfillService:
    """Orchestrates multi-symbol historical backfills and routine refreshes."""

    def __init__(
        self,
        store: TaiwanDailyStore | None = None,
        provider: TaiwanHybridProvider | None = None,
        calendar: TaiwanTradingCalendar | None = None,
        refresh_service: TaiwanDailyRefreshService | None = None,
        concurrency: int = DEFAULT_CONCURRENCY,
    ) -> None:
        self.store = store or TaiwanDailyStore()
        self.provider = provider or TaiwanHybridProvider()
        self.calendar = calendar or TaiwanTradingCalendar()
        self.concurrency = concurrency
        self.refresh_service = refresh_service or TaiwanDailyRefreshService(
            store=self.store,
            provider=self.provider,
            calendar=self.calendar,
            concurrency=self.concurrency,
        )

    def get_supported_universe(self) -> list[str]:
        """Fetch canonical active stock & ETF symbols from TaiwanSecurityMaster."""
        master = get_security_master()
        df = master.to_dataframe(supported_only=True)
        if df.is_empty():
            return []
        filtered = df.filter(
            (pl.col("listing_status") == "active")
            & (pl.col("instrument_type").is_in(["stock", "etf"]))
        )
        return sorted(filtered["symbol"].unique().to_list())

    def backfill(
        self,
        symbols: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Execute safe batch backfill across symbols with progress checkpoints and resume.

        Args:
            symbols: Target canonical symbols (defaults to full supported universe).
            start_date: Earliest date to backfill (defaults to 2024-01-01).
            end_date: Latest date to backfill (defaults to today).
            batch_size: Number of symbols processed per checkpoint.
            progress_callback: Optional callable receiving progress snapshots.

        Returns:
            Aggregated execution metrics and status dictionary.
        """
        all_symbols = symbols if symbols is not None else self.get_supported_universe()
        if not all_symbols:
            return {"error": "no symbols to backfill", "total_symbols": 0}

        start = start_date or date.fromisoformat(DEFAULT_BACKFILL_START)
        end = end_date or date.today()

        total_symbols = len(all_symbols)
        batches = [
            all_symbols[i : i + batch_size]
            for i in range(0, total_symbols, batch_size)
        ]

        overall_stats: dict[str, Any] = {
            "total_symbols": total_symbols,
            "processed_symbols": 0,
            "symbols_fetched": 0,
            "symbols_skipped_up_to_date": 0,
            "symbols_failed": 0,
            "total_rows_fetched": 0,
            "total_rows_written_new": 0,
            "total_rows_written": 0,
            "total_jobs_executed": 0,
            "failed_symbols": [],
            "per_symbol_status": {},
            "start_time": datetime.now().isoformat(),
            "elapsed_seconds": 0.0,
            "batches_total": len(batches),
            "batches_completed": 0,
        }

        t_global_start = time.time()

        for b_idx, batch in enumerate(batches, start=1):
            t_batch_start = time.time()
            res = self.refresh_service.refresh_symbols(batch, start=start, end=end)
            t_batch_end = time.time()

            # Merge results into overall stats
            batch_fetched = res.get("symbols_fetched", 0)
            batch_jobs = res.get("jobs_executed", 0)
            batch_rows = res.get("rows_written", 0)
            batch_failed = res.get("failed", [])
            per_sym = res.get("per_symbol", {})

            overall_stats["processed_symbols"] += len(batch)
            overall_stats["symbols_fetched"] += batch_fetched
            overall_stats["total_jobs_executed"] += batch_jobs
            overall_stats["total_rows_fetched"] += batch_rows
            overall_stats["total_rows_written_new"] += batch_rows
            overall_stats["total_rows_written"] += batch_rows
            overall_stats["batches_completed"] += 1
            overall_stats["elapsed_seconds"] = round(time.time() - t_global_start, 2)

            for sym in batch:
                if sym in batch_failed:
                    overall_stats["symbols_failed"] += 1
                    overall_stats["failed_symbols"].append(sym)
                    overall_stats["per_symbol_status"][sym] = {
                        "status": "failed",
                        "error": per_sym.get(sym, {}).get("error", "unknown"),
                    }
                elif sym in per_sym:
                    s_info = per_sym[sym]
                    rows = s_info.get("rows", 0)
                    overall_stats["per_symbol_status"][sym] = {
                        "status": "success" if rows > 0 else "empty",
                        "rows": rows,
                        "ranges": s_info.get("ranges", []),
                    }
                else:
                    # Symbol had no missing ranges in storage
                    overall_stats["symbols_skipped_up_to_date"] += 1
                    overall_stats["per_symbol_status"][sym] = {
                        "status": "skipped_up_to_date",
                        "rows": 0,
                    }

            # Progress checkpoint reporting
            elapsed = overall_stats["elapsed_seconds"]
            processed = overall_stats["processed_symbols"]
            speed = processed / elapsed if elapsed > 0 else 0.0
            remaining = total_symbols - processed
            eta_seconds = round(remaining / speed, 1) if speed > 0 else 0.0

            checkpoint = {
                "batch": b_idx,
                "batches_total": len(batches),
                "processed": processed,
                "total": total_symbols,
                "pct_complete": round((processed / total_symbols) * 100.0, 1),
                "batch_rows": batch_rows,
                "total_rows": overall_stats["total_rows_written"],
                "speed_sym_per_sec": round(speed, 2),
                "elapsed_sec": elapsed,
                "eta_sec": eta_seconds,
            }

            logger.info(
                "Backfill batch %d/%d done: %d/%d symbols (%.1f%%), +%d rows, ETA: %.1fs",
                b_idx,
                len(batches),
                checkpoint["processed"],
                total_symbols,
                checkpoint["pct_complete"],
                batch_rows,
                eta_seconds,
            )

            if progress_callback:
                progress_callback(checkpoint)

        return overall_stats

    def refresh_daily_routine(self, as_of_date: date | None = None) -> dict[str, Any]:
        """Perform routine post-market daily close refresh for the entire supported universe."""
        target_date = as_of_date or date.today()
        # If weekend, skip immediately
        if self.calendar.is_trading_day(target_date) is False:
            return {
                "status": "skipped",
                "reason": f"{target_date.isoformat()} is confirmed non-trading day (weekend or holiday)",
                "rows_written": 0,
            }
        return self.refresh_service.refresh_symbols(start=target_date, end=target_date)

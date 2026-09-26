"""Taiwan Selection Review Service (A12).

Manages selection snapshots and point-in-time horizon tracking (1D, 5D, 20D).
Strict Guarantees:
- Snapshots are immutable and never updated with future lookback data.
- Horizons evaluated across actual trading days via TaiwanDailyStore.
- Transparent status: 'pending' when horizon not yet reached, 'unavailable' if stock price missing (no fake zeros).
- Benchmark comparison against 0050.TWSE / TAIEX, calculating excess return (without claiming alpha).
- Descriptive condition analytics without causal claims or automated weight changes.
- Stored exclusively in user_data/taiwan_selection_snapshots.json (private, non-bundled).
"""
from __future__ import annotations

import contextlib
import json
import logging
import threading
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

from app.config import settings
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.selection_review_models import (
    ConditionReviewStats,
    HorizonReviewItem,
    HorizonStatus,
    SaveSelectionSnapshotRequest,
    SelectionSnapshot,
    SnapshotListItem,
    SnapshotReviewDetail,
    StrategyReviewStats,
)

logger = logging.getLogger(__name__)

DEFAULT_BENCHMARK_SYMBOL = "0050.TWSE"
DEFAULT_BENCHMARK_NAME = "台灣50"
SAMPLE_SUFFICIENT_THRESHOLD = 5


def _storage_path() -> Path:
    p = settings.data_dir / "user_data" / "taiwan_selection_snapshots.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


class TaiwanSelectionReviewService:
    """Service for managing screener snapshots and calculating review horizons."""

    def __init__(
        self,
        path: Path | None = None,
        daily_store: TaiwanDailyStore | None = None,
    ) -> None:
        self.path = path or _storage_path()
        self.daily_store = daily_store or TaiwanDailyStore()
        self._lock = threading.Lock()

    def _read_snapshots_raw(self) -> list[SelectionSnapshot]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                return [SelectionSnapshot(**item) for item in raw]
        except Exception as e:
            logger.warning("Failed to load selection snapshots from %s: %s", self.path, e)
        return []

    def _save_snapshots_raw(self, snapshots: list[SelectionSnapshot]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        payload = [s.model_dump() for s in snapshots]
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self.path)

    # ── Snapshot CRUD ───────────────────────────────────────────────

    def save_snapshot(self, req: SaveSelectionSnapshotRequest) -> SelectionSnapshot:
        """Save a new screener result snapshot. Guaranteed immutable once written."""
        now_dt = datetime.now(UTC)
        now_iso = now_dt.isoformat()
        now_tag = now_dt.strftime("%Y%m%d_%H%M%S")
        snapshot_id = f"snap_{now_tag}_{uuid.uuid4().hex[:6]}"

        symbols = [item.symbol for item in req.items]

        snapshot = SelectionSnapshot(
            snapshot_id=snapshot_id,
            created_at=now_iso,
            strategy_id=req.strategy_id.strip(),
            strategy_name=req.strategy_name.strip() or "未命名策略",
            as_of_date=req.as_of_date.strip(),
            market_context_summary=req.market_context_summary.strip(),
            selected_symbols=symbols,
            items=req.items,
        )

        with self._lock:
            existing = self._read_snapshots_raw()
            # Guard immutability: snapshot_id cannot collide
            for s in existing:
                if s.snapshot_id == snapshot_id:
                    raise ValueError(f"Snapshot ID already exists: {snapshot_id}")
            existing.append(snapshot)
            self._save_snapshots_raw(existing)

        logger.info("Saved selection snapshot %s for strategy %s (%d items)", snapshot_id, snapshot.strategy_id, len(symbols))
        return snapshot

    def get_snapshot(self, snapshot_id: str) -> SelectionSnapshot | None:
        """Find raw snapshot by ID without horizon recalculation."""
        with self._lock:
            for s in self._read_snapshots_raw():
                if s.snapshot_id == snapshot_id:
                    return s
        return None

    def delete_snapshot(self, snapshot_id: str) -> bool:
        """Delete a snapshot by ID."""
        with self._lock:
            existing = self._read_snapshots_raw()
            initial_len = len(existing)
            filtered = [s for s in existing if s.snapshot_id != snapshot_id]
            if len(filtered) < initial_len:
                self._save_snapshots_raw(filtered)
                logger.info("Deleted snapshot %s", snapshot_id)
                return True
        return False

    # ── Horizon Evaluation & Review ────────────────────────────────

    def _get_forward_trading_days(self, as_of: date) -> list[date]:
        """Fetch trading days strictly strictly following as_of from TaiwanDailyStore."""
        avail = sorted(self.daily_store.available_dates())
        return [d for d in avail if d > as_of]

    def _get_close_prices(self, symbols: list[str], target_date: date) -> dict[str, float]:
        """Query close prices for a list of symbols on a given trade date."""
        df = self.daily_store.read_range(symbols, target_date, target_date)
        if df.is_empty():
            return {}
        result: dict[str, float] = {}
        for row in df.select(["symbol", "close"]).iter_rows(named=True):
            sym = row.get("symbol")
            close_val = row.get("close")
            if sym and close_val is not None:
                with contextlib.suppress(ValueError, TypeError):
                    result[sym] = float(close_val)
        return result

    def get_snapshot_review(self, snapshot_id: str) -> SnapshotReviewDetail | None:
        """Evaluate a snapshot across 1D, 5D, 20D horizons."""
        snapshot = self.get_snapshot(snapshot_id)
        if not snapshot:
            return None

        try:
            as_of_dt = date.fromisoformat(snapshot.as_of_date)
        except Exception:
            as_of_dt = datetime.fromisoformat(snapshot.created_at).date()

        forward_days = self._get_forward_trading_days(as_of_dt)

        # Horizons: 1D = index 0, 5D = index 4, 20D = index 19
        d_1d = forward_days[0] if len(forward_days) >= 1 else None
        d_5d = forward_days[4] if len(forward_days) >= 5 else None
        d_20d = forward_days[19] if len(forward_days) >= 20 else None

        symbols = [item.symbol for item in snapshot.items]
        all_symbols = list(set([*symbols, DEFAULT_BENCHMARK_SYMBOL]))

        # Read prices
        prices_entry = self._get_close_prices(all_symbols, as_of_dt)
        prices_1d = self._get_close_prices(all_symbols, d_1d) if d_1d else {}
        prices_5d = self._get_close_prices(all_symbols, d_5d) if d_5d else {}
        prices_20d = self._get_close_prices(all_symbols, d_20d) if d_20d else {}

        # Benchmark returns
        bm_entry = prices_entry.get(DEFAULT_BENCHMARK_SYMBOL)
        bm_ret_1d: float | None = None
        bm_ret_5d: float | None = None
        bm_ret_20d: float | None = None

        if bm_entry and bm_entry > 0:
            if d_1d and DEFAULT_BENCHMARK_SYMBOL in prices_1d:
                bm_ret_1d = round((prices_1d[DEFAULT_BENCHMARK_SYMBOL] - bm_entry) / bm_entry * 100.0, 2)
            if d_5d and DEFAULT_BENCHMARK_SYMBOL in prices_5d:
                bm_ret_5d = round((prices_5d[DEFAULT_BENCHMARK_SYMBOL] - bm_entry) / bm_entry * 100.0, 2)
            if d_20d and DEFAULT_BENCHMARK_SYMBOL in prices_20d:
                bm_ret_20d = round((prices_20d[DEFAULT_BENCHMARK_SYMBOL] - bm_entry) / bm_entry * 100.0, 2)

        evaluated_items: list[HorizonReviewItem] = []
        ret_5d_list: list[float] = []
        ret_20d_list: list[float] = []
        excess_5d_list: list[float] = []
        excess_20d_list: list[float] = []

        for item in snapshot.items:
            entry_p = item.price
            if entry_p <= 0 and item.symbol in prices_entry:
                entry_p = prices_entry[item.symbol]

            # 1D
            h1_price: float | None = None
            h1_ret: float | None = None
            h1_status: HorizonStatus = "pending" if d_1d is None else "unavailable"
            h1_excess: float | None = None
            if d_1d is not None and item.symbol in prices_1d and entry_p > 0:
                h1_price = prices_1d[item.symbol]
                h1_ret = round((h1_price - entry_p) / entry_p * 100.0, 2)
                h1_status = "completed"
                if bm_ret_1d is not None:
                    h1_excess = round(h1_ret - bm_ret_1d, 2)

            # 5D
            h5_price: float | None = None
            h5_ret: float | None = None
            h5_status: HorizonStatus = "pending" if d_5d is None else "unavailable"
            h5_excess: float | None = None
            if d_5d is not None and item.symbol in prices_5d and entry_p > 0:
                h5_price = prices_5d[item.symbol]
                h5_ret = round((h5_price - entry_p) / entry_p * 100.0, 2)
                h5_status = "completed"
                ret_5d_list.append(h5_ret)
                if bm_ret_5d is not None:
                    h5_excess = round(h5_ret - bm_ret_5d, 2)
                    excess_5d_list.append(h5_excess)

            # 20D
            h20_price: float | None = None
            h20_ret: float | None = None
            h20_status: HorizonStatus = "pending" if d_20d is None else "unavailable"
            h20_excess: float | None = None
            if d_20d is not None and item.symbol in prices_20d and entry_p > 0:
                h20_price = prices_20d[item.symbol]
                h20_ret = round((h20_price - entry_p) / entry_p * 100.0, 2)
                h20_status = "completed"
                ret_20d_list.append(h20_ret)
                if bm_ret_20d is not None:
                    h20_excess = round(h20_ret - bm_ret_20d, 2)
                    excess_20d_list.append(h20_excess)

            evaluated_items.append(
                HorizonReviewItem(
                    symbol=item.symbol,
                    name=item.name,
                    rank=item.rank,
                    entry_price=entry_p,
                    quant_score=item.quant_score,
                    match_reasons=item.match_reasons,
                    fundamental_summary=item.fundamental_summary,
                    chips_summary=item.chips_summary,
                    event_risk_summary=item.event_risk_summary,
                    h1d_price=h1_price,
                    h1d_return_pct=h1_ret,
                    h1d_status=h1_status,
                    h1d_bm_return_pct=bm_ret_1d,
                    h1d_excess_pct=h1_excess,
                    h5d_price=h5_price,
                    h5d_return_pct=h5_ret,
                    h5d_status=h5_status,
                    h5d_bm_return_pct=bm_ret_5d,
                    h5d_excess_pct=h5_excess,
                    h20d_price=h20_price,
                    h20d_return_pct=h20_ret,
                    h20d_status=h20_status,
                    h20d_bm_return_pct=bm_ret_20d,
                    h20d_excess_pct=h20_excess,
                    benchmark_symbol=DEFAULT_BENCHMARK_SYMBOL,
                    benchmark_name=DEFAULT_BENCHMARK_NAME,
                )
            )

        avg_5d = round(sum(ret_5d_list) / len(ret_5d_list), 2) if ret_5d_list else None
        avg_20d = round(sum(ret_20d_list) / len(ret_20d_list), 2) if ret_20d_list else None
        avg_excess_5d = round(sum(excess_5d_list) / len(excess_5d_list), 2) if excess_5d_list else None
        avg_excess_20d = round(sum(excess_20d_list) / len(excess_20d_list), 2) if excess_20d_list else None

        return SnapshotReviewDetail(
            snapshot=snapshot,
            evaluated_items=evaluated_items,
            h5d_evaluated_count=len(ret_5d_list),
            h20d_evaluated_count=len(ret_20d_list),
            h5d_avg_return_pct=avg_5d,
            h20d_avg_return_pct=avg_20d,
            h5d_bm_avg_return_pct=bm_ret_5d,
            h20d_bm_avg_return_pct=bm_ret_20d,
            h5d_avg_excess_pct=avg_excess_5d,
            h20d_avg_excess_pct=avg_excess_20d,
        )

    def list_snapshots(self) -> list[SnapshotListItem]:
        """List all snapshots with summarized evaluation metrics."""
        with self._lock:
            raw_list = self._read_snapshots_raw()

        # Sort newest first
        raw_list.sort(key=lambda s: s.created_at, reverse=True)

        result: list[SnapshotListItem] = []
        for s in raw_list:
            review = self.get_snapshot_review(s.snapshot_id)
            if review:
                result.append(
                    SnapshotListItem(
                        snapshot_id=s.snapshot_id,
                        created_at=s.created_at,
                        strategy_id=s.strategy_id,
                        strategy_name=s.strategy_name,
                        as_of_date=s.as_of_date,
                        selected_count=len(s.items),
                        h5d_evaluated_count=review.h5d_evaluated_count,
                        h20d_evaluated_count=review.h20d_evaluated_count,
                        h5d_avg_return_pct=review.h5d_avg_return_pct,
                        h20d_avg_return_pct=review.h20d_avg_return_pct,
                        h5d_bm_return_pct=review.h5d_bm_avg_return_pct,
                        h20d_bm_return_pct=review.h20d_bm_avg_return_pct,
                        h5d_excess_pct=review.h5d_avg_excess_pct,
                        h20d_excess_pct=review.h20d_avg_excess_pct,
                    )
                )
            else:
                result.append(
                    SnapshotListItem(
                        snapshot_id=s.snapshot_id,
                        created_at=s.created_at,
                        strategy_id=s.strategy_id,
                        strategy_name=s.strategy_name,
                        as_of_date=s.as_of_date,
                        selected_count=len(s.items),
                    )
                )
        return result

    # ── Strategy & Condition Analytics ──────────────────────────────

    def get_strategy_reviews(self) -> list[StrategyReviewStats]:
        """Aggregate performance for each saved strategy across all evaluated snapshots."""
        with self._lock:
            raw_list = self._read_snapshots_raw()

        by_strat: dict[str, list[SelectionSnapshot]] = {}
        strat_names: dict[str, str] = {}
        for s in raw_list:
            by_strat.setdefault(s.strategy_id, []).append(s)
            strat_names[s.strategy_id] = s.strategy_name

        results: list[StrategyReviewStats] = []
        for strat_id, snapshots in by_strat.items():
            all_ret_5d: list[float] = []
            all_ret_20d: list[float] = []
            all_excess_5d: list[float] = []
            all_excess_20d: list[float] = []

            for s in snapshots:
                rev = self.get_snapshot_review(s.snapshot_id)
                if not rev:
                    continue
                for item in rev.evaluated_items:
                    if item.h5d_status == "completed" and item.h5d_return_pct is not None:
                        all_ret_5d.append(item.h5d_return_pct)
                        if item.h5d_excess_pct is not None:
                            all_excess_5d.append(item.h5d_excess_pct)
                    if item.h20d_status == "completed" and item.h20d_return_pct is not None:
                        all_ret_20d.append(item.h20d_return_pct)
                        if item.h20d_excess_pct is not None:
                            all_excess_20d.append(item.h20d_excess_pct)

            ev_count_5d = len(all_ret_5d)
            ev_count_20d = len(all_ret_20d)

            avg_5d = round(sum(all_ret_5d) / ev_count_5d, 2) if ev_count_5d > 0 else None
            avg_20d = round(sum(all_ret_20d) / ev_count_20d, 2) if ev_count_20d > 0 else None

            # Hit rate definition: Return > 0
            hit_5d = round(len([r for r in all_ret_5d if r > 0]) / ev_count_5d * 100.0, 1) if ev_count_5d > 0 else None
            hit_20d = round(len([r for r in all_ret_20d if r > 0]) / ev_count_20d * 100.0, 1) if ev_count_20d > 0 else None

            bm_exc_5d = round(sum(all_excess_5d) / len(all_excess_5d), 2) if all_excess_5d else None
            bm_exc_20d = round(sum(all_excess_20d) / len(all_excess_20d), 2) if all_excess_20d else None

            results.append(
                StrategyReviewStats(
                    strategy_id=strat_id,
                    strategy_name=strat_names.get(strat_id, strat_id),
                    snapshots_count=len(snapshots),
                    evaluated_picks_5d=ev_count_5d,
                    evaluated_picks_20d=ev_count_20d,
                    avg_return_5d=avg_5d,
                    avg_return_20d=avg_20d,
                    hit_rate_5d=hit_5d,
                    hit_rate_20d=hit_20d,
                    bm_excess_5d=bm_exc_5d,
                    bm_excess_20d=bm_exc_20d,
                )
            )

        # Sort by snapshot count DESC, then evaluated_picks_5d DESC
        results.sort(key=lambda r: (r.snapshots_count, r.evaluated_picks_5d), reverse=True)
        return results

    def get_condition_reviews(self) -> list[ConditionReviewStats]:
        """Descriptive analytics grouping evaluated returns by match_reasons."""
        with self._lock:
            raw_list = self._read_snapshots_raw()

        cond_ret_5d: dict[str, list[float]] = {}
        cond_ret_20d: dict[str, list[float]] = {}

        for s in raw_list:
            rev = self.get_snapshot_review(s.snapshot_id)
            if not rev:
                continue
            for item in rev.evaluated_items:
                reasons = item.match_reasons or ["通用篩選"]
                for reason in reasons:
                    cleaned_reason = reason.strip()
                    if not cleaned_reason:
                        continue
                    if item.h5d_status == "completed" and item.h5d_return_pct is not None:
                        cond_ret_5d.setdefault(cleaned_reason, []).append(item.h5d_return_pct)
                    if item.h20d_status == "completed" and item.h20d_return_pct is not None:
                        cond_ret_20d.setdefault(cleaned_reason, []).append(item.h20d_return_pct)

        all_conditions = set(cond_ret_5d.keys()) | set(cond_ret_20d.keys())
        stats: list[ConditionReviewStats] = []

        for cond in sorted(all_conditions):
            rets_5d = cond_ret_5d.get(cond, [])
            rets_20d = cond_ret_20d.get(cond, [])

            c5 = len(rets_5d)
            c20 = len(rets_20d)

            is_sufficient = c5 >= SAMPLE_SUFFICIENT_THRESHOLD

            avg_5d = round(sum(rets_5d) / c5, 2) if c5 > 0 else None
            avg_20d = round(sum(rets_20d) / c20, 2) if c20 > 0 else None

            hit_5d = round(len([r for r in rets_5d if r > 0]) / c5 * 100.0, 1) if c5 > 0 else None
            hit_20d = round(len([r for r in rets_20d if r > 0]) / c20 * 100.0, 1) if c20 > 0 else None

            stats.append(
                ConditionReviewStats(
                    condition_label=cond,
                    sample_count_5d=c5,
                    sample_count_20d=c20,
                    is_sample_sufficient=is_sufficient,
                    avg_return_5d=avg_5d,
                    avg_return_20d=avg_20d,
                    hit_rate_5d=hit_5d,
                    hit_rate_20d=hit_20d,
                )
            )

        # Sort by sample count 5D DESC
        stats.sort(key=lambda x: (x.sample_count_5d, x.sample_count_20d), reverse=True)
        return stats


_service_instance: TaiwanSelectionReviewService | None = None
_service_lock = threading.Lock()


def get_selection_review_service() -> TaiwanSelectionReviewService:
    global _service_instance
    with _service_lock:
        if _service_instance is None:
            _service_instance = TaiwanSelectionReviewService()
        return _service_instance

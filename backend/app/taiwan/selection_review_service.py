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
import os
import threading
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

from app.config import settings
from app.taiwan.adjust import adjust_prices_as_of
from app.taiwan.corporate_actions import CorporateActionStore, event_market_open
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.observed_universe import ObservedUniverseStore
from app.taiwan.providers.corporate_actions import SOURCE_URLS
from app.taiwan.providers.taiwan_values import market_close
from app.taiwan.realtime.calendar import TaiwanTradingCalendar, taipei_now
from app.taiwan.selection_review_models import (
    ConditionReviewStats,
    ForwardBatchStats,
    HorizonReviewItem,
    HorizonStatus,
    SaveSelectionSnapshotRequest,
    SelectionSnapshot,
    SelectionSnapshotItem,
    SnapshotListItem,
    SnapshotReviewDetail,
    StrategyReviewStats,
)

logger = logging.getLogger(__name__)

DEFAULT_BENCHMARK_SYMBOL = "0050.TWSE"
DEFAULT_BENCHMARK_NAME = "台灣50"
FORWARD_RULE_VERSION = "trend_liquidity_v1"
FORWARD_PRICE_ADJUSTMENT = "pit_price_normalized_cash_and_share_actions_not_total_return"
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
        calendar: TaiwanTradingCalendar | None = None,
        action_store: CorporateActionStore | None = None,
        census_store: ObservedUniverseStore | None = None,
    ) -> None:
        self.path = path or _storage_path()
        self.daily_store = daily_store or TaiwanDailyStore()
        self.calendar = calendar or TaiwanTradingCalendar()
        self.action_store = action_store or CorporateActionStore()
        self.census_store = census_store if census_store is not None else (
            ObservedUniverseStore() if daily_store is None else None
        )
        self._lock = threading.Lock()
        self._forward_cache_fingerprint: tuple | None = None
        self._completed_forward_reviews: dict[str, SnapshotReviewDetail] = {}

    def _read_snapshots_raw(self) -> list[SelectionSnapshot]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                return [SelectionSnapshot(**item) for item in raw]
            raise ValueError("選股快照檔案格式錯誤")
        except Exception as e:
            logger.warning("Failed to load selection snapshots from %s: %s", self.path, e)
            raise ValueError("選股快照檔案無法讀取。已停止寫入以保留原檔") from e

    def _save_snapshots_raw(self, snapshots: list[SelectionSnapshot]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        payload = [s.model_dump() for s in snapshots]
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self.path)

    @contextlib.contextmanager
    def _write_guard(self):
        """Serialize read-modify-write across API worker processes."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(".lock")
        # Keep the sidecar inode: unlinking it could split concurrent locks.
        # The OS releases the byte/flock automatically if a worker is killed.
        with lock_path.open("a+b") as stream:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            os.set_inheritable(stream.fileno(), False)
            for attempt in range(40):
                try:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if attempt == 39:
                        raise RuntimeError("選股快照檔案寫入忙碌") from None
                    time.sleep(0.05)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

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

        with self._lock, self._write_guard():
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

    def lock_forward_batch(self, screener=None) -> SelectionSnapshot:
        """Run the canonical screener server-side and lock one batch per source session."""
        from app.taiwan.screener import TaiwanScreenerRequest, TaiwanScreenerService

        screen = (screener or TaiwanScreenerService()).run(
            TaiwanScreenerRequest(preset=FORWARD_RULE_VERSION)
        )
        if not screen.data_dates.daily_as_of:
            raise ValueError("沒有可鎖定的行情資料日期")
        source_day = date.fromisoformat(screen.data_dates.daily_as_of)
        now = taipei_now()
        if now < market_close(source_day):
            raise ValueError("來源交易日尚未收盤。不能鎖定正式批次")
        completed_dates = [d for d in self.daily_store.available_dates() if market_close(d) <= now]
        if source_day != max(completed_dates, default=None):
            raise ValueError("選股來源日期與評估行情庫不一致")
        batch_id = f"forward_{FORWARD_RULE_VERSION}_{source_day:%Y%m%d}"
        with self._lock, self._write_guard():
            existing = self._read_snapshots_raw()
            for snapshot in existing:
                if snapshot.snapshot_id == batch_id:
                    return snapshot
            # A queued writer can cross 09:00 while waiting for the process lock.
            now = taipei_now()
            items = [SelectionSnapshotItem(
                symbol=row.symbol, name=row.name, rank=index, price=row.close,
                quant_score=row.quant_score, match_reasons=row.match_reasons,
                strategy_conditions={"preset": FORWARD_RULE_VERSION},
                risk_status=row.risk_status, quote_status="available",
            ) for index, row in enumerate(screen.items[:20], start=1) if row.close is not None and row.close > 0]
            target = self._next_potential_session(source_day)
            if now >= event_market_open(target):
                raise ValueError("預定進場日已開盤。不能事後建立正式前瞻批次")
            snapshot = SelectionSnapshot(
                snapshot_id=batch_id, created_at=now.astimezone(UTC).isoformat(),
                locked_at=now.isoformat(),
                strategy_id=FORWARD_RULE_VERSION, strategy_name="趨勢流動性 v1",
                as_of_date=source_day.isoformat(), source_data_date=source_day.isoformat(),
                target_trade_date=target.isoformat(), rule_version=FORWARD_RULE_VERSION,
                target_trade_date_status=(
                    "confirmed" if self.calendar.day_evidence(target, "TWSE").status == "trading"
                    else "scheduled_unverified"
                ),
                evaluation_basis="next_open", record_type="forward_batch",
                price_adjustment=FORWARD_PRICE_ADJUSTMENT,
                market_context_summary="正式前瞻批次。以鎖定後下一交易日開盤作紙上進場基準",
                selected_symbols=[item.symbol for item in items], items=items,
                eligible_total=screen.total, primary_observation_count=min(len(items), 10),
                missing_quote_count=screen.missing_quote_count,
                risk_unknown_count=screen.risk_unknown_count,
                risk_source_status=screen.risk_source_status,
                risk_source_as_of=screen.risk_source_as_of,
            )
            existing.append(snapshot)
            self._save_snapshots_raw(existing)
            return snapshot

    def _next_potential_session(self, after: date) -> date:
        cursor = after + timedelta(days=1)
        for _ in range(30):
            if self.calendar.day_evidence(cursor, "TWSE").status != "non_trading":
                return cursor
            cursor += timedelta(days=1)
        raise ValueError("無法確認下個預定交易日")

    def delete_snapshot(self, snapshot_id: str) -> bool:
        """Delete a snapshot by ID."""
        with self._lock, self._write_guard():
            existing = self._read_snapshots_raw()
            if any(s.snapshot_id == snapshot_id and s.record_type == "forward_batch" for s in existing):
                raise PermissionError("正式前瞻批次已鎖定。不能刪除")
            initial_len = len(existing)
            filtered = [s for s in existing if s.snapshot_id != snapshot_id]
            if len(filtered) < initial_len:
                self._save_snapshots_raw(filtered)
                logger.info("Deleted snapshot %s", snapshot_id)
                return True
        return False

    # ── Horizon Evaluation & Review ────────────────────────────────

    def _get_forward_trading_days(self, as_of: date) -> list[date]:
        """Resolve sessions from calendar evidence, never slide over missing prices."""
        partitions = set(self.daily_store.available_dates())
        observed: dict[date, bool] = {}

        def has_observation(day: date) -> bool:
            if day not in observed:
                if day not in partitions:
                    observed[day] = False
                else:
                    frame = self.daily_store.read_range(None, day, day)
                    observed[day] = bool(not frame.is_empty() and frame.filter(
                        pl.col("symbol").str.ends_with(".TWSE")
                        & (pl.col("close") > 0) & (pl.col("volume") > 0)
                    ).height)
            return observed[day]

        days: list[date] = []
        cursor = as_of + timedelta(days=1)
        for _ in range(100):
            evidence = self.calendar.day_evidence(cursor, "TWSE")
            if evidence.status == "unresolved" and self.census_store is not None:
                evidence = self.census_store.day_evidence("TWSE", cursor, calendar=self.calendar)
            if evidence.status == "non_trading":
                if has_observation(cursor):
                    break
                cursor += timedelta(days=1)
                continue
            if evidence.status == "trading" or has_observation(cursor):
                days.append(cursor)
                if len(days) == 20:
                    break
            else:
                # Unknown weekday could be a closure. Subsequent observed bars
                # must not silently reassign horizon N to date N+1.
                break
            cursor += timedelta(days=1)
        return days

    def _potential_horizon_due(self, as_of: date, horizon: int) -> date:
        """Earliest scheduled due date when some weekday evidence is unresolved."""
        cursor = as_of
        count = 0
        for _ in range(120):
            cursor += timedelta(days=1)
            if self.calendar.day_evidence(cursor, "TWSE").status != "non_trading":
                count += 1
                if count == horizon:
                    return cursor
        raise ValueError("評估交易日超出支援範圍")

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

    def _verified_action_events(self, start: date, end: date):
        """Read the existing audited action snapshot without request-time downloads."""
        marker = self.action_store.path.with_name("coverage.json")
        if not marker.is_file() or not self.action_store.path.is_file():
            return None
        try:
            record = json.loads(marker.read_text(encoding="utf-8"))
            if (record.get("events_sha256") != self.action_store.snapshot_digest()
                    or set(record.get("sources", ())) != set(SOURCE_URLS)
                    or date.fromisoformat(record["start"]) > start
                    or date.fromisoformat(record["end"]) < end):
                return None
            events = tuple(e for e in self.action_store.read()
                           if start <= e.effective_date <= end)
            if any(e.status == "provider_error" for e in events):
                return None
            return events
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _get_forward_batch_review(self, snapshot: SelectionSnapshot) -> SnapshotReviewDetail:
        source = date.fromisoformat(snapshot.source_data_date or snapshot.as_of_date)
        target = date.fromisoformat(snapshot.target_trade_date or snapshot.as_of_date)
        sessions = self._get_forward_trading_days(source)
        symbols = [*(item.symbol for item in snapshot.items), DEFAULT_BENCHMARK_SYMBOL]
        entry_valid = bool(sessions and sessions[0] == target)
        target_closed = self.calendar.day_evidence(target, "TWSE").status == "non_trading"
        today = taipei_now()
        needed = [target, *(sessions[index - 1] for index in (5, 20) if len(sessions) >= index)]
        prices: dict[date, dict[str, dict]] = {}
        for session in set(needed):
            frame = self.daily_store.read_range(symbols, session, session)
            prices[session] = {row["symbol"]: row for row in frame.iter_rows(named=True)}
        events_by_end = {
            end: self._verified_action_events(target, end)
            for end in set(needed) if entry_valid and today >= market_close(end)
        }
        bm_returns: dict[date, float | None] = {}
        for end, events in events_by_end.items():
            bm_entry = prices.get(target, {}).get(DEFAULT_BENCHMARK_SYMBOL)
            bm_end = prices.get(end, {}).get(DEFAULT_BENCHMARK_SYMBOL)
            bm_returns[end] = (
                self._paper_return(DEFAULT_BENCHMARK_SYMBOL, target, end,
                                   bm_entry, bm_end, events)
                if bm_entry is not None and bm_end is not None and events is not None
                else None
            )
        evaluated: list[HorizonReviewItem] = []
        for pick in snapshot.items:
            entry_row = prices.get(target, {}).get(pick.symbol) if entry_valid else None
            entry_open = entry_row.get("open") if entry_row else None
            entry_open = float(entry_open) if entry_open is not None else None
            if entry_open is not None and entry_open <= 0:
                entry_open = None
            entry_status: HorizonStatus = (
                "unavailable" if target_closed else
                "unavailable" if not entry_valid and today >= market_close(target) else
                "pending" if not entry_valid or today < market_close(target)
                else "completed" if entry_open is not None else "unavailable"
            )
            result = HorizonReviewItem(
                symbol=pick.symbol, name=pick.name, rank=pick.rank,
                entry_price=pick.price, paper_entry_price=entry_open,
                entry_date=target.isoformat(), entry_status=entry_status,
                price_adjustment=FORWARD_PRICE_ADJUSTMENT,
                quant_score=pick.quant_score, match_reasons=pick.match_reasons,
                fundamental_summary=pick.fundamental_summary,
                chips_summary=pick.chips_summary,
                event_risk_summary=pick.event_risk_summary,
            )
            if entry_status == "unavailable":
                result.status_reasons["entry"] = (
                    "scheduled_entry_day_closed" if target_closed else
                    "trading_day_unverified" if not entry_valid else "missing_entry_open"
                )
            for horizon in (1, 5, 20):
                end = sessions[horizon - 1] if len(sessions) >= horizon and entry_valid else None
                prefix = f"h{horizon}d"
                due = end or self._potential_horizon_due(source, horizon)
                if target_closed or end is None or today < market_close(end):
                    unresolved_expired = end is None and today >= market_close(due)
                    setattr(result, f"{prefix}_status",
                            "unavailable" if target_closed or unresolved_expired else "pending")
                    setattr(result, f"{prefix}_bm_status",
                            "unavailable" if target_closed or unresolved_expired else "pending")
                    if target_closed or unresolved_expired:
                        result.status_reasons[prefix] = (
                            "scheduled_entry_day_closed" if target_closed
                            else "trading_day_unverified"
                        )
                    continue
                end_row = prices.get(end, {}).get(pick.symbol)
                events = events_by_end.get(end)
                bm_raw = bm_returns.get(end)
                setattr(result, f"{prefix}_bm_status", "unavailable")
                if bm_raw is not None:
                    setattr(result, f"{prefix}_bm_status", "completed")
                    setattr(result, f"{prefix}_bm_return_pct", round(bm_raw, 2))
                else:
                    result.status_reasons[f"{prefix}_benchmark"] = (
                        "corporate_action_coverage_unavailable" if events is None
                        else "missing_or_invalid_benchmark_price"
                    )
                if entry_open is None or end_row is None or events is None:
                    setattr(result, f"{prefix}_status", "unavailable")
                    result.status_reasons[prefix] = (
                        "missing_entry_open" if entry_open is None else
                        "missing_horizon_close" if end_row is None else
                        "corporate_action_coverage_unavailable"
                    )
                    continue
                raw = self._paper_return(pick.symbol, target, end, entry_row, end_row, events)
                if raw is None:
                    setattr(result, f"{prefix}_status", "unavailable")
                    result.status_reasons[prefix] = "price_adjustment_unavailable"
                    continue
                setattr(result, f"{prefix}_status", "completed")
                setattr(result, f"{prefix}_price", float(end_row["close"]))
                setattr(result, f"{prefix}_raw_return_pct", raw)
                setattr(result, f"{prefix}_return_pct", round(raw, 2))
                if bm_raw is not None:
                    setattr(result, f"{prefix}_excess_pct", round(raw - bm_raw, 2))
            evaluated.append(result)

        detail = SnapshotReviewDetail(snapshot=snapshot, evaluated_items=evaluated)
        for horizon in (1, 5, 20):
            prefix = f"h{horizon}d"
            returns = [getattr(item, f"{prefix}_raw_return_pct") for item in evaluated
                       if getattr(item, f"{prefix}_status") == "completed"]
            benchmark = [getattr(item, f"{prefix}_bm_return_pct") for item in evaluated
                         if getattr(item, f"{prefix}_bm_return_pct") is not None]
            excess = [getattr(item, f"{prefix}_excess_pct") for item in evaluated
                      if getattr(item, f"{prefix}_excess_pct") is not None]
            if horizon == 1:
                detail.h1d_evaluated_count = len(returns)
            if horizon in (5, 20):
                setattr(detail, f"{prefix}_evaluated_count", len(returns))
                setattr(detail, f"{prefix}_avg_return_pct",
                        round(sum(returns) / len(returns), 2) if returns else None)
                setattr(detail, f"{prefix}_bm_avg_return_pct",
                        round(sum(benchmark) / len(benchmark), 2) if benchmark else None)
                setattr(detail, f"{prefix}_avg_excess_pct",
                        round(sum(excess) / len(excess), 2) if excess else None)
            setattr(detail, f"{prefix}_pending_count",
                    sum(getattr(i, f"{prefix}_status") == "pending" for i in evaluated))
            setattr(detail, f"{prefix}_unavailable_count",
                    sum(getattr(i, f"{prefix}_status") == "unavailable" for i in evaluated))
        return detail

    @staticmethod
    def _paper_return(symbol, start, end, entry, exit_row, events) -> float | None:
        try:
            frame = pl.DataFrame({
                "symbol": [symbol] if start == end else [symbol, symbol],
                "date": [start] if start == end else [start, end],
                "open": [entry["open"]] if start == end else [entry["open"], exit_row["open"]],
                "close": [exit_row["close"]] if start == end else [entry["close"], exit_row["close"]],
            }).with_columns(pl.col("date").cast(pl.Date))
            adjusted = adjust_prices_as_of(frame, as_of=end, events=events,
                                           price_columns=("open", "close"))
            if adjusted.status != "verified":
                return None
            rows = adjusted.to_frame().sort("date")
            return (float(rows["close"][-1]) / float(rows["open"][0]) - 1) * 100
        except (ValueError, TypeError, ZeroDivisionError):
            return None

    def get_forward_batch_stats(self) -> ForwardBatchStats:
        with self._lock:
            batches = [s for s in self._read_snapshots_raw() if s.record_type == "forward_batch"]
        stats = ForwardBatchStats(batches_count=len(batches),
                                  picks_count=sum(len(s.items) for s in batches))
        if not batches:
            return stats
        fingerprint = self._forward_review_inputs()
        with self._lock:
            if fingerprint != self._forward_cache_fingerprint:
                self._completed_forward_reviews.clear()
                self._forward_cache_fingerprint = fingerprint
        reviews: list[SnapshotReviewDetail] = []
        for snapshot in batches:
            with self._lock:
                review = self._completed_forward_reviews.get(snapshot.snapshot_id)
            if review is None:
                review = self._get_forward_batch_review(snapshot)
                if all(
                    getattr(item, f"h{horizon}d_status") == "completed"
                    and getattr(item, f"h{horizon}d_bm_status") == "completed"
                    for item in review.evaluated_items for horizon in (1, 5, 20)
                ) and review.evaluated_items:
                    with self._lock:
                        if self._forward_cache_fingerprint == fingerprint:
                            self._completed_forward_reviews[snapshot.snapshot_id] = review
            reviews.append(review)
        for horizon in (1, 5, 20):
            prefix = f"h{horizon}d"
            items = [item for review in reviews if review for item in review.evaluated_items]
            returns = [getattr(item, f"{prefix}_raw_return_pct") for item in items
                       if getattr(item, f"{prefix}_status") == "completed"]
            setattr(stats, f"{prefix}_evaluated_count", len(returns))
            setattr(stats, f"{prefix}_pending_count",
                    sum(getattr(item, f"{prefix}_status") == "pending" for item in items))
            setattr(stats, f"{prefix}_unavailable_count",
                    sum(getattr(item, f"{prefix}_status") == "unavailable" for item in items))
            setattr(stats, f"{prefix}_hit_rate_pct",
                    round(sum(value > 0 for value in returns) / len(returns) * 100, 1)
                    if returns else None)
        return stats

    def _forward_review_inputs(self) -> tuple:
        """Local data versions used to invalidate completed forward reviews."""
        def file_versions(paths) -> tuple:
            versions = []
            for path in paths:
                try:
                    stat = path.stat()
                    versions.append((str(path), stat.st_mtime_ns, stat.st_size))
                except FileNotFoundError:
                    continue
            return tuple(sorted(versions))

        daily = file_versions(self.daily_store._data_dir.glob("date=*/part.parquet"))
        actions = file_versions((self.action_store.path,
                                self.action_store.path.with_name("coverage.json")))
        census = file_versions(self.census_store._data_dir.glob("exchange=*/date=*/part.parquet")) if self.census_store else ()
        calendar = (tuple(sorted(self.calendar.known_holidays)),
                    tuple(sorted(self.calendar.known_trading_days)))
        return daily, actions, census, calendar

    def get_snapshot_review(self, snapshot_id: str) -> SnapshotReviewDetail | None:
        """Evaluate a snapshot across 1D, 5D, 20D horizons."""
        snapshot = self.get_snapshot(snapshot_id)
        if not snapshot:
            return None
        if snapshot.record_type == "forward_batch":
            return self._get_forward_batch_review(snapshot)

        try:
            as_of_dt = date.fromisoformat(snapshot.as_of_date)
        except Exception:
            as_of_dt = datetime.fromisoformat(snapshot.created_at).date()

        forward_days = self._get_forward_trading_days(as_of_dt)

        # Horizons: 1D = index 0, 5D = index 4, 20D = index 19
        d_1d = forward_days[0] if len(forward_days) >= 1 else None
        d_5d = forward_days[4] if len(forward_days) >= 5 else None
        d_20d = forward_days[19] if len(forward_days) >= 20 else None
        now_tpe = taipei_now()
        d_1d = d_1d if d_1d and now_tpe >= market_close(d_1d) else None
        d_5d = d_5d if d_5d and now_tpe >= market_close(d_5d) else None
        d_20d = d_20d if d_20d and now_tpe >= market_close(d_20d) else None

        def missing_state(horizon: int, target: date | None) -> HorizonStatus:
            if target is not None:
                return "unavailable"
            due = self._potential_horizon_due(as_of_dt, horizon)
            return "unavailable" if now_tpe >= market_close(due) else "pending"

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
            h1_raw: float | None = None
            h1_status: HorizonStatus = missing_state(1, d_1d)
            h1_excess: float | None = None
            if d_1d is not None and item.symbol in prices_1d and entry_p > 0:
                h1_price = prices_1d[item.symbol]
                h1_raw = (h1_price - entry_p) / entry_p * 100.0
                h1_ret = round(h1_raw, 2)
                h1_status = "completed"
                if bm_ret_1d is not None:
                    h1_excess = round(h1_ret - bm_ret_1d, 2)

            # 5D
            h5_price: float | None = None
            h5_ret: float | None = None
            h5_raw: float | None = None
            h5_status: HorizonStatus = missing_state(5, d_5d)
            h5_excess: float | None = None
            if d_5d is not None and item.symbol in prices_5d and entry_p > 0:
                h5_price = prices_5d[item.symbol]
                h5_raw = (h5_price - entry_p) / entry_p * 100.0
                h5_ret = round(h5_raw, 2)
                h5_status = "completed"
                ret_5d_list.append(h5_raw)
                if bm_ret_5d is not None:
                    h5_excess = round(h5_ret - bm_ret_5d, 2)
                    excess_5d_list.append(h5_excess)

            # 20D
            h20_price: float | None = None
            h20_ret: float | None = None
            h20_raw: float | None = None
            h20_status: HorizonStatus = missing_state(20, d_20d)
            h20_excess: float | None = None
            if d_20d is not None and item.symbol in prices_20d and entry_p > 0:
                h20_price = prices_20d[item.symbol]
                h20_raw = (h20_price - entry_p) / entry_p * 100.0
                h20_ret = round(h20_raw, 2)
                h20_status = "completed"
                ret_20d_list.append(h20_raw)
                if bm_ret_20d is not None:
                    h20_excess = round(h20_ret - bm_ret_20d, 2)
                    excess_20d_list.append(h20_excess)

            evaluated_items.append(
                HorizonReviewItem(
                    symbol=item.symbol,
                    name=item.name,
                    rank=item.rank,
                    entry_price=entry_p,
                    entry_date=as_of_dt.isoformat(),
                    entry_status="completed" if entry_p > 0 else "unavailable",
                    quant_score=item.quant_score,
                    match_reasons=item.match_reasons,
                    fundamental_summary=item.fundamental_summary,
                    chips_summary=item.chips_summary,
                    event_risk_summary=item.event_risk_summary,
                    h1d_price=h1_price,
                    h1d_return_pct=h1_ret,
                    h1d_raw_return_pct=h1_raw,
                    h1d_status=h1_status,
                    h1d_bm_return_pct=bm_ret_1d,
                    h1d_bm_status=("completed" if bm_ret_1d is not None else
                                   missing_state(1, d_1d)),
                    h1d_excess_pct=h1_excess,
                    h5d_price=h5_price,
                    h5d_return_pct=h5_ret,
                    h5d_raw_return_pct=h5_raw,
                    h5d_status=h5_status,
                    h5d_bm_return_pct=bm_ret_5d,
                    h5d_bm_status=("completed" if bm_ret_5d is not None else
                                   missing_state(5, d_5d)),
                    h5d_excess_pct=h5_excess,
                    h20d_price=h20_price,
                    h20d_return_pct=h20_ret,
                    h20d_raw_return_pct=h20_raw,
                    h20d_status=h20_status,
                    h20d_bm_return_pct=bm_ret_20d,
                    h20d_bm_status=("completed" if bm_ret_20d is not None else
                                    missing_state(20, d_20d)),
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
            h1d_evaluated_count=sum(i.h1d_status == "completed" for i in evaluated_items),
            h5d_evaluated_count=len(ret_5d_list),
            h20d_evaluated_count=len(ret_20d_list),
            h5d_avg_return_pct=avg_5d,
            h20d_avg_return_pct=avg_20d,
            h5d_bm_avg_return_pct=bm_ret_5d,
            h20d_bm_avg_return_pct=bm_ret_20d,
            h5d_avg_excess_pct=avg_excess_5d,
            h20d_avg_excess_pct=avg_excess_20d,
            h1d_pending_count=sum(i.h1d_status == "pending" for i in evaluated_items),
            h1d_unavailable_count=sum(i.h1d_status == "unavailable" for i in evaluated_items),
            h5d_pending_count=sum(i.h5d_status == "pending" for i in evaluated_items),
            h5d_unavailable_count=sum(i.h5d_status == "unavailable" for i in evaluated_items),
            h20d_pending_count=sum(i.h20d_status == "pending" for i in evaluated_items),
            h20d_unavailable_count=sum(i.h20d_status == "unavailable" for i in evaluated_items),
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
                        record_type=s.record_type,
                        locked_at=s.locked_at,
                        source_data_date=s.source_data_date,
                        target_trade_date=s.target_trade_date,
                        target_trade_date_status=s.target_trade_date_status,
                        rule_version=s.rule_version,
                        evaluation_basis=s.evaluation_basis,
                        risk_source_status=s.risk_source_status,
                        risk_source_as_of=s.risk_source_as_of,
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
                        record_type=s.record_type,
                        locked_at=s.locked_at,
                        source_data_date=s.source_data_date,
                        target_trade_date=s.target_trade_date,
                        target_trade_date_status=s.target_trade_date_status,
                        rule_version=s.rule_version,
                        evaluation_basis=s.evaluation_basis,
                        risk_source_status=s.risk_source_status,
                        risk_source_as_of=s.risk_source_as_of,
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
            if s.record_type != "research":
                continue
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
                    if item.h5d_status == "completed" and item.h5d_raw_return_pct is not None:
                        all_ret_5d.append(item.h5d_raw_return_pct)
                        if item.h5d_excess_pct is not None:
                            all_excess_5d.append(item.h5d_excess_pct)
                    if item.h20d_status == "completed" and item.h20d_raw_return_pct is not None:
                        all_ret_20d.append(item.h20d_raw_return_pct)
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
            if s.record_type != "research":
                continue
            rev = self.get_snapshot_review(s.snapshot_id)
            if not rev:
                continue
            for item in rev.evaluated_items:
                reasons = item.match_reasons or ["通用篩選"]
                for reason in reasons:
                    cleaned_reason = reason.strip()
                    if not cleaned_reason:
                        continue
                    if item.h5d_status == "completed" and item.h5d_raw_return_pct is not None:
                        cond_ret_5d.setdefault(cleaned_reason, []).append(item.h5d_raw_return_pct)
                    if item.h20d_status == "completed" and item.h20d_raw_return_pct is not None:
                        cond_ret_20d.setdefault(cleaned_reason, []).append(item.h20d_raw_return_pct)

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

"""Read-only descriptive experiments over saved forward observations.

Selection Review owns price evaluation; LiveLedger owns appended daily labels.
This module admits their evidence, keeps identities separate and aggregates it.
It never reconstructs historical signals, downloads data or writes snapshots.
"""
# ruff: noqa: RUF001
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.taiwan.corporate_actions import event_market_open
from app.taiwan.providers.taiwan_values import market_close
from app.taiwan.quant.live_contract import HORIZONS, LIVE_CONTRACT, canonical_hash
from app.taiwan.quant.live_store import LiveLedger
from app.taiwan.realtime.calendar import taipei_now
from app.taiwan.selection_review_models import SelectionSnapshot
from app.taiwan.selection_review_service import TaiwanSelectionReviewService

Source = Literal["Selection", "A13 Buy Point", "Daily recommendation"]
Status = Literal["matured", "pending", "unavailable"]
SLICES = ("exchange", "industry", "market_regime", "liquidity_bucket", "risk_status")


class LabFilters(BaseModel):
    minimum_sample: int = Field(5, ge=5, le=1000)
    strategy_key: str | None = None
    source: Source | None = None
    exchange: Literal["TWSE", "TPEX"] | None = None
    industry: str | None = None
    market_regime: str | None = None
    liquidity_bucket: str | None = None
    risk_status: str | None = None


class StrategyIdentity(BaseModel):
    key: str
    strategy_id: str
    strategy_name: str
    version: str | None
    source: Source
    definition_digest: str | None
    entry_basis: str
    price_semantics: str
    cost_assumption: str


class Observation(BaseModel):
    observation_id: str
    snapshot_id: str
    symbol: str
    name: str
    signal_date: str
    as_of: str
    outcome_date: str | None = None
    entry_date: str | None = None
    entry_price: float | None = None
    horizon: int
    status: Status
    return_pct: float | None = None
    benchmark_symbol: str | None = None
    benchmark_return_pct: float | None = None
    excess_pct: float | None = None
    reason: str | None = None
    benchmark_reason: str | None = None
    identity: StrategyIdentity
    evidence_label: str = "Forward / OOS observed"
    exchange: str | None = None
    industry: str | None = None
    market_regime: str | None = None
    liquidity_bucket: str | None = None
    risk_status: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class HorizonStats(BaseModel):
    matured: int = 0
    pending: int = 0
    unavailable: int = 0
    hit_count: int = 0
    hit_rate_denominator: int = 0
    hit_rate_pct: float | None = None
    average_return_pct: float | None = None
    benchmark_n: int = 0
    excess_n: int = 0
    average_benchmark_return_pct: float | None = None
    average_excess_pct: float | None = None
    sample_sufficient: bool = False
    excess_sample_sufficient: bool = False


class StrategyStats(BaseModel):
    identity: StrategyIdentity
    sample_count: int
    snapshot_count: int
    horizons: dict[str, HorizonStats]


class LabOverview(BaseModel):
    evidence_label: str = "Forward / OOS observed"
    historical_pit_status: str = "未完全可用"
    hit_definition: str = "未四捨五入 return > 0；pending / unavailable 不納入分母"
    unit: str = "報酬與超額報酬為百分數；超額 = 個股報酬 - 同期 benchmark 報酬"
    minimum_sample: int
    sample_count: int
    strategies: list[StrategyStats]
    strategy_options: list[StrategyIdentity]
    filter_options: dict[str, list[str]]
    slice_availability: dict[str, str]
    duplicate_snapshots: int = 0
    duplicate_samples: int = 0
    integrity_conflicts: int = 0
    excluded_research_snapshots: int = 0


class ObservationPage(BaseModel):
    total: int
    offset: int
    limit: int
    observations: list[Observation]


def finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def identity(**values: Any) -> StrategyIdentity:
    # Names may change; frozen version/configuration and entry semantics are identity.
    key = canonical_hash({k: v for k, v in values.items() if k != "strategy_name"})
    return StrategyIdentity(key=key, **values)


def summarize(rows: list[Observation], minimum_sample: int) -> HorizonStats:
    matured = [row for row in rows if row.status == "matured" and row.return_pct is not None]
    returns = [row.return_pct for row in matured if row.return_pct is not None]
    benchmark = [row.benchmark_return_pct for row in matured if row.benchmark_return_pct is not None]
    excess = [row.excess_pct for row in matured if row.excess_pct is not None]
    n = len(returns)
    hits = sum(value > 0 for value in returns)
    enough = n >= minimum_sample
    enough_excess = len(excess) >= minimum_sample
    return HorizonStats(
        matured=n, pending=sum(row.status == "pending" for row in rows),
        unavailable=len(rows) - n - sum(row.status == "pending" for row in rows),
        hit_count=hits, hit_rate_denominator=n,
        hit_rate_pct=100 * hits / n if enough else None,
        average_return_pct=sum(returns) / n if enough else None,
        benchmark_n=len(benchmark), excess_n=len(excess),
        average_benchmark_return_pct=(sum(benchmark) / len(benchmark)
                                      if len(benchmark) >= minimum_sample else None),
        average_excess_pct=sum(excess) / len(excess) if enough_excess else None,
        sample_sufficient=enough, excess_sample_sufficient=enough_excess,
    )


def _matches(row: Observation, filters: LabFilters) -> bool:
    if filters.strategy_key and row.identity.key != filters.strategy_key:
        return False
    if filters.source and row.identity.source != filters.source:
        return False
    return all(getattr(filters, field) is None or getattr(row, field) == getattr(filters, field)
               for field in SLICES)


class StrategyLabService:
    def __init__(self, selection: TaiwanSelectionReviewService, ledger: LiveLedger) -> None:
        self.selection = selection
        self.ledger = ledger

    def _selection_admission(self, snapshot: SelectionSnapshot) -> str | None:
        try:
            source = date.fromisoformat(snapshot.as_of_date)
            created = datetime.fromisoformat(snapshot.created_at)
            if created.utcoffset() is None or created > taipei_now():
                return "snapshot_timestamp_unverified"
            target = self.selection._next_potential_session(source)
            if not market_close(source) <= created < event_market_open(target):
                return "snapshot_not_frozen_before_forward_window"
            if snapshot.record_type == "forward_batch":
                if (snapshot.source_data_date != snapshot.as_of_date
                        or snapshot.target_trade_date != target.isoformat()
                        or not snapshot.locked_at or not snapshot.strategy_version
                        or snapshot.rule_version != snapshot.strategy_id
                        or snapshot.quote_coverage_status != "verified"
                        or snapshot.risk_source_status != "available"
                        or not snapshot.selection_action_events_sha256):
                    return "formal_snapshot_provenance_incomplete"
                locked = datetime.fromisoformat(snapshot.locked_at)
                if locked.utcoffset() is None or not market_close(source) <= locked < event_market_open(target):
                    return "snapshot_lock_timestamp_unverified"
            elif (snapshot.observation_origin != "a13_server_observed"
                  or not snapshot.strategy_definition_digest):
                return "a13_forward_provenance_not_persisted"
        except (ValueError, TypeError):
            return "snapshot_dates_unverified"
        return None

    @staticmethod
    def _selection_identity(snapshot: SelectionSnapshot) -> StrategyIdentity:
        return identity(
            strategy_id=snapshot.strategy_id, strategy_name=snapshot.strategy_name,
            version=snapshot.strategy_version,
            source="A13 Buy Point" if snapshot.source == "Buy Point" else "Selection",
            definition_digest=snapshot.strategy_definition_digest,
            entry_basis=snapshot.evaluation_basis, price_semantics=snapshot.price_adjustment,
            cost_assumption=snapshot.cost_assumption,
        )

    def _selection_rows(self, snapshot: SelectionSnapshot) -> list[Observation]:
        ident = self._selection_identity(snapshot)
        failure = self._selection_admission(snapshot)
        if snapshot.evaluation_basis == "reference_close" and any(
            finite(pick.price) is None or pick.price <= 0 for pick in snapshot.items
        ):
            failure = "invalid_saved_reference_price"
        unique = {item.symbol: item for item in snapshot.items}
        if len(unique) != len(snapshot.items):
            failure = "duplicate_symbol_in_snapshot"
        detail = self.selection.review_snapshot(snapshot) if failure is None else None
        evaluations = {item.symbol: item for item in detail.evaluated_items} if detail else {}
        snapshot_digest = canonical_hash(snapshot.model_dump())
        inputs_digest = canonical_hash(list(map(str, self.selection._forward_batch_fingerprint(
            snapshot, self.selection._forward_review_inputs())))) if detail else None
        result = []
        for symbol, pick in unique.items():
            item = evaluations.get(symbol)
            for horizon in HORIZONS:
                prefix = f"h{horizon}d"
                status = getattr(item, f"{prefix}_status", "unavailable")
                raw = finite(getattr(item, f"{prefix}_raw_return_pct", None))
                end = getattr(item, f"{prefix}_outcome_date", None)
                valid_end = self._closed_end(snapshot.as_of_date, end, allow_same=False)
                state: Status = ("pending" if status == "pending" and failure is None else
                                 "matured" if status == "completed" and raw is not None
                                 and valid_end and failure is None else "unavailable")
                bm = (finite(getattr(item, f"{prefix}_raw_bm_return_pct", None))
                      if getattr(item, f"{prefix}_bm_status", None) == "completed" else None)
                bm = bm if state == "matured" else None
                result.append(Observation(
                    observation_id=f"{snapshot.snapshot_id}:{symbol}:{horizon}",
                    snapshot_id=snapshot.snapshot_id, symbol=symbol, name=pick.name,
                    signal_date=snapshot.as_of_date, as_of=snapshot.created_at,
                    entry_date=item.entry_date if item else None,
                    entry_price=(item.paper_entry_price if snapshot.evaluation_basis == "next_open"
                                 else pick.price) if item else finite(pick.price),
                    outcome_date=end, horizon=horizon, status=state,
                    return_pct=raw if state == "matured" else None,
                    benchmark_symbol="0050.TWSE", benchmark_return_pct=bm,
                    excess_pct=raw - bm if raw is not None and bm is not None else None,
                    reason=failure or (item.status_reasons.get(prefix) if item else None)
                    or ("invalid_matured_outcome" if state == "unavailable" else None),
                    benchmark_reason=None if bm is not None else "benchmark_unavailable",
                    identity=ident, exchange=symbol.split(".")[-1], risk_status=pick.risk_status,
                    evidence_label="Forward / OOS observed" if failure is None else "來源證據不足",
                    provenance={
                        "snapshot_digest": snapshot_digest,
                        "source": snapshot.source, "created_at": snapshot.created_at,
                        "locked_at": snapshot.locked_at, "source_data_date": snapshot.source_data_date,
                        "observation_origin": snapshot.observation_origin,
                        "strategy_definition_digest": snapshot.strategy_definition_digest,
                        "selection_action_events_sha256": snapshot.selection_action_events_sha256,
                        "revenue_evidence_digest": snapshot.revenue_evidence_digest,
                        "outcome_source": "Selection Review / TaiwanDailyStore",
                        "evaluation_inputs_digest": inputs_digest,
                        "evaluation_as_of": taipei_now().isoformat(),
                        "status_reasons": item.status_reasons if item else {},
                    },
                ))
        return result

    @staticmethod
    def _closed_end(start: str, end: str | None, *, allow_same: bool = False) -> bool:
        try:
            source, target = date.fromisoformat(start), date.fromisoformat(str(end))
            return (target >= source if allow_same else target > source) and taipei_now() >= market_close(target)
        except ValueError:
            return False

    @staticmethod
    def _daily_identity(run: dict[str, Any]) -> StrategyIdentity:
        snapshot = run["snapshot"]
        model = snapshot.get("model", {})
        return identity(
            strategy_id=model.get("model_id", run["model_key"]),
            strategy_name=f"Daily recommendation · {model.get('model_id', run['model_key'])}",
            version=model.get("version"), source="Daily recommendation",
            definition_digest=canonical_hash(model), entry_basis="reference_close",
            price_semantics="pit_price_normalized_close_return_not_total_return",
            cost_assumption="未扣成本與滑價；收盤基準觀察報酬",
        )

    def _daily_rows(self, run: dict[str, Any]) -> list[Observation]:
        snapshot = run["snapshot"]
        ident = self._daily_identity(run)
        valid = (run.get("audit_status") == "ok" and snapshot.get("contract") == LIVE_CONTRACT
                 and snapshot.get("signal_session") == run["session"]
                 and canonical_hash(snapshot) == run.get("snapshot_hash"))
        signals = {item["symbol"]: item for item in snapshot.get("signals", [])}
        regime = snapshot.get("regime", {})
        regime_value = regime.get("regime") if (regime.get("status") == "verified"
                        and regime.get("as_of") == run["session"]) else None
        result = []
        for item in self.ledger.signal_outcomes(run["model_key"], run["session"], list(signals.values()), read_only=True):
            raw = finite(item.get("value"))
            end = str(item["end_session"]) if item.get("end_session") else None
            status: Status = ("pending" if valid and item["status"] == "pending" else
                              "matured" if valid and item["status"] == "verified"
                              and item.get("audit_status") == "ok" and raw is not None
                              and self._closed_end(run["session"], end) else "unavailable")
            symbol = item["symbol"]
            result.append(Observation(
                observation_id=f"{run['model_key']}:{run['session']}:{symbol}:{item['horizon']}",
                snapshot_id=f"live:{run['model_key']}:{run['session']}",
                symbol=symbol, name=signals[symbol].get("name", symbol),
                signal_date=run["session"], as_of=snapshot.get("data_cutoff", run["frozen_at"]),
                entry_date=run["session"], entry_price=finite(signals[symbol].get("reference_close")),
                outcome_date=end, horizon=item["horizon"], status=status,
                return_pct=raw * 100 if status == "matured" and raw is not None else None,
                reason=("live_snapshot_integrity_unverified" if not valid else
                        item.get("audit_status") if item.get("audit_status") != "ok" else
                        item.get("reason") or ("invalid_matured_outcome" if status == "unavailable" else None)),
                benchmark_reason="benchmark_not_persisted_in_live_ledger", identity=ident,
                evidence_label="Forward / OOS observed" if valid else "來源證據不足",
                exchange=symbol.split(".")[-1], risk_status=signals[symbol].get("risk_status"),
                market_regime=regime_value,
                provenance={"source": "LiveLedger", "model_key": run["model_key"],
                            "snapshot_hash": run["snapshot_hash"], "frozen_at": run["frozen_at"],
                            "feature_snapshot_hash": snapshot.get("feature_snapshot_hash"),
                            "raw_history_hash": snapshot.get("raw_history_hash"),
                            "outcome_source": "LiveLedger appended observations",
                            "outcome_digest": canonical_hash(item.get("observations", [])),
                            "outcome_audit_status": item.get("audit_status"),
                            "price_semantics": item.get("price_semantics")},
            ))
        return result

    def observations(self) -> tuple[list[Observation], dict[str, int], dict[str, tuple[StrategyIdentity, set[str]]]]:
        snapshots = self.selection.read_snapshots()
        groups: dict[str, list[SelectionSnapshot]] = defaultdict(list)
        audit = {"duplicate_snapshots": 0, "duplicate_samples": 0, "integrity_conflicts": 0, "excluded_research_snapshots": 0}
        for snapshot in snapshots:
            groups[snapshot.snapshot_id].append(snapshot)
        rows = []
        catalog: dict[str, tuple[StrategyIdentity, set[str]]] = {}

        def register(ident: StrategyIdentity, snapshot_id: str) -> None:
            catalog.setdefault(ident.key, (ident, set()))[1].add(snapshot_id)

        for copies in groups.values():
            digests = {canonical_hash(snapshot.model_dump()) for snapshot in copies}
            if len(digests) != 1:
                audit["integrity_conflicts"] += 1
                continue
            audit["duplicate_snapshots"] += len(copies) - 1
            snapshot = copies[0]
            if snapshot.record_type != "forward_batch" and snapshot.source != "Buy Point":
                audit["excluded_research_snapshots"] += 1
                continue
            rows.extend(self._selection_rows(snapshot))
            register(self._selection_identity(snapshot), snapshot.snapshot_id)
        if (self.ledger.root / "signals.sqlite3").exists():
            for header in self.ledger.outcome_runs(read_only=True):
                run = self.ledger.read_run(header["model_key"], header["session"], read_only=True)
                if run:
                    rows.extend(self._daily_rows(run))
                    register(self._daily_identity(run), f"live:{run['model_key']}:{run['session']}")
        observations: dict[tuple[str, str, str, int], list[Observation]] = defaultdict(list)
        for row in rows:
            observations[(row.identity.key, row.signal_date, row.symbol, row.horizon)].append(row)
        deduplicated = []
        for observation_copies in observations.values():
            observation_copies.sort(key=lambda row: (row.as_of, row.snapshot_id))
            first = observation_copies[0]
            if len(observation_copies) > 1:
                if first.horizon == 1:
                    audit["duplicate_samples"] += len(observation_copies) - 1
                values = {(r.entry_price, r.entry_date, r.outcome_date, r.status,
                           r.return_pct, r.benchmark_return_pct) for r in observation_copies}
                if len(values) > 1:
                    if first.horizon == 1:
                        audit["integrity_conflicts"] += 1
                    first = first.model_copy(update={"status": "unavailable", "return_pct": None,
                        "benchmark_return_pct": None, "excess_pct": None,
                        "reason": "duplicate_forward_observation_conflict"})
                first = first.model_copy(update={"provenance": {
                    **first.provenance, "duplicate_snapshot_ids": [r.snapshot_id for r in observation_copies],
                }})
            deduplicated.append(first)
        return deduplicated, audit, catalog

    def overview(self, filters: LabFilters) -> LabOverview:
        rows, audit, catalog = self.observations()
        filtered = [row for row in rows if _matches(row, filters)]
        groups: dict[str, list[Observation]] = defaultdict(list)
        for row in filtered:
            groups[row.identity.key].append(row)
        if not any(getattr(filters, field) is not None for field in SLICES):
            for key, (ident, _) in catalog.items():
                if (not filters.strategy_key or key == filters.strategy_key) and (not filters.source or ident.source == filters.source):
                    groups.setdefault(key, [])
        strategies = [StrategyStats(
            identity=catalog[key][0],
            sample_count=len({(row.snapshot_id, row.symbol) for row in group}),
            snapshot_count=len({row.snapshot_id for row in group}) if group else len(catalog[key][1]),
            horizons={f"{h}D": summarize([r for r in group if r.horizon == h], filters.minimum_sample)
                      for h in HORIZONS},
        ) for key, group in sorted(groups.items())]
        # Stable identity order only; no sorting by observed returns or winner selection.
        options = {field: sorted({str(getattr(row, field)) for row in rows
                                 if getattr(row, field) is not None}) for field in SLICES}
        options["source"] = sorted({row.identity.source for row in rows})
        return LabOverview(
            minimum_sample=filters.minimum_sample,
            sample_count=len({(row.snapshot_id, row.symbol) for row in filtered}),
            strategies=strategies, filter_options=options,
            strategy_options=[ident for ident, _ in catalog.values()],
            slice_availability={field: "persisted" if options[field] else "未保存可驗證背景，無法切片"
                                for field in SLICES},
            duplicate_snapshots=audit["duplicate_snapshots"],
            duplicate_samples=audit["duplicate_samples"],
            integrity_conflicts=audit["integrity_conflicts"],
            excluded_research_snapshots=audit["excluded_research_snapshots"],
        )

    def drilldown(self, filters: LabFilters, offset: int = 0, limit: int = 50) -> ObservationPage:
        rows, _, _ = self.observations()
        filtered = [row for row in rows if _matches(row, filters)]
        filtered.sort(key=lambda row: (row.signal_date, row.snapshot_id, row.symbol, row.horizon), reverse=True)
        return ObservationPage(total=len(filtered), offset=offset, limit=limit,
                               observations=filtered[offset:offset + limit])

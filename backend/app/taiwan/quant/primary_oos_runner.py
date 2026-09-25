"""Single readiness-gated runner for formal TWSE Primary OOS results."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from app.taiwan.backfill_worker import (
    CENSUS_START,
    TaiwanHistoricalBackfillWorker,
    WorkerBusyError,
    WorkerLock,
)
from app.taiwan.corporate_actions import CorporateActionEvent, CorporateActionStore
from app.taiwan.historical_classification import HistoricalClassificationStore
from app.taiwan.observed_universe import ObservedUniverseStore, first_observed_dates
from app.taiwan.providers.corporate_actions import SOURCE_URLS, CorporateActionProvider
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.quant.data_health import (
    DataHealth,
    QuantEvaluationReadiness,
    ReadinessThresholds,
    health_from_stores,
    price_dataset_capability,
    quant_evaluation_readiness,
)
from app.taiwan.quant.evaluation import ActionCoverage, evaluate_quant
from app.taiwan.quant.evaluation_spec import PRIMARY_OOS_SPEC, PrimaryOosEvaluationSpec
from app.taiwan.quant.evaluation_store import PrimaryOosRunStore
from app.taiwan.quant.feature_manifest import FeatureManifest
from app.taiwan.quant.live_contract import canonical_hash, canonical_json
from app.taiwan.quant.storage import FactorPanelStore
from app.taiwan.quant.training import TrainingMatrixResult, panel_training_matrix
from app.taiwan.quant_eligibility import PRIMARY_VERIFIED

CLAIM_SCOPE_DESCRIPTION = (
    "TWSE Primary verified universe under the predefined 99% data-health policy"
)
UNRESOLVED_EXCLUSION_REASON = (
    "historical instrument subtype lacks authoritative official evidence"
)


def data_health_policy_record(
    health: DataHealth, unresolved_codes: Sequence[str],
) -> dict[str, Any]:
    """Exact coverage and every excluded unresolved code, for the saved artifact."""
    thresholds = ReadinessThresholds()
    classification = health.classification
    return {
        "scope": CLAIM_SCOPE_DESCRIPTION,
        "thresholds": {
            "primary_oos_classification_ratio": thresholds.primary_oos_classification_ratio,
            "primary_oos_census_ratio": thresholds.primary_oos_census_ratio,
        },
        "classification_coverage": classification.get("primary_classification_ratio"),
        "trading_day_coverage": health.census_ratio,
        "verified_stock_count": classification.get("verified_stock_count"),
        "unresolved_count": classification.get("unknown_count"),
        "excluded_unresolved_symbols": [
            {"symbol": code, "reason": UNRESOLVED_EXCLUSION_REASON}
            for code in sorted(unresolved_codes)
        ],
    }


class PrimaryOosNotReadyError(RuntimeError):
    def __init__(self, readiness: QuantEvaluationReadiness) -> None:
        self.readiness = readiness
        super().__init__("Primary OOS evaluation is blocked by data-health readiness")


class PrimaryOosInputError(RuntimeError):
    """Required, provenance-backed evaluation inputs are missing or inconsistent."""


class PrimaryOosRunFailedError(RuntimeError):
    def __init__(self, run_id: str, code: str) -> None:
        self.run_id = run_id
        self.code = code
        super().__init__(f"Primary OOS run {run_id} failed ({code})")


@dataclass(frozen=True)
class PrimaryOosPreflight:
    data_health: DataHealth
    a2b_progress: dict[str, int]
    a2b_worker_status: str
    end_date: date

    @property
    def readiness(self) -> QuantEvaluationReadiness:
        return quant_evaluation_readiness(
            self.data_health, self.a2b_progress,
            worker_status=self.a2b_worker_status,
        )

    def context(self, spec_hash: str) -> dict[str, Any]:
        return {
            "health": self.data_health.describe(),
            "a2b": PrimaryOosRunStore.progress_snapshot(self.a2b_progress),
            "spec_hash": spec_hash,
        }


@dataclass(frozen=True)
class PrimaryOosEvaluationInputs:
    preflight: PrimaryOosPreflight
    admission: TrainingMatrixResult
    daily: pl.DataFrame
    exchange_by_symbol: Mapping[str, str]
    sessions_by_exchange: Mapping[str, Sequence[date]]
    walk_forward_sessions: Sequence[date]
    events: tuple[CorporateActionEvent, ...]
    action_coverage: Mapping[str, ActionCoverage]
    classification_identity: str
    latest_market_date: date
    unresolved_type_codes: tuple[str, ...] = ()
    rows_without_price_bar: int = 0


def read_primary_oos_preflight() -> PrimaryOosPreflight:
    """Read A2a/A2b checkpoints and shared DataHealth without starting a worker."""
    worker = TaiwanHistoricalBackfillWorker()
    snapshot = worker.status(start=CENSUS_START)
    end_date = date.fromisoformat(snapshot["end_date"])
    health = health_from_stores(
        worker.census_store, worker.classification_store,
        start=CENSUS_START, end=end_date,
    )
    progress = snapshot["classification"]
    a2b = {
        key: progress[key]
        for key in ("completed_jobs", "pending_jobs", "failed_jobs", "unique_first_seen_dates")
    }
    return PrimaryOosPreflight(
        data_health=health,
        a2b_progress=a2b,
        a2b_worker_status=worker.lock.owner_status(),
        end_date=end_date,
    )


def _frame_digest(frame: pl.DataFrame, sort_by: tuple[str, ...]) -> str:
    ordered = frame.sort(list(sort_by))
    digest = hashlib.sha256()
    digest.update(canonical_json({
        "columns": [(name, str(dtype)) for name, dtype in ordered.schema.items()],
        "height": ordered.height,
    }).encode("utf-8"))
    for row in ordered.iter_rows(named=True):
        digest.update(b"\n")
        digest.update(canonical_json(row).encode("utf-8"))
    return digest.hexdigest()


def usable_price_bar() -> pl.Expr:
    """An official row with a complete positive OHLC bar.

    The exchange prints ``--`` for a day without a price (no trade, suspension).
    Such a row stays a market observation (``observed_on_market``), but it has no
    bar to adjust, so it cannot be a feature row (``price_bar_available`` is
    false) and a bar-less window is unavailable rather than back-filled.
    """
    return pl.all_horizontal(
        pl.col(name).is_not_null() & (pl.col(name) > 0) & pl.col(name).is_finite()
        for name in ("open", "high", "low", "close")
    )


def _primary_universe(
    census: ObservedUniverseStore,
    classifications: HistoricalClassificationStore,
) -> tuple[pl.DataFrame, str]:
    observations = census.read("TWSE")
    if observations.is_empty():
        raise PrimaryOosInputError("A2a TWSE observed-universe rows are unavailable")
    first_seen = first_observed_dates(census, "TWSE")
    classified = classifications.read()
    if classified.is_empty():
        raise PrimaryOosInputError("A2b historical classification rows are unavailable")
    first_seen_frame = pl.DataFrame(
        {"code": list(first_seen), "first_seen_date": list(first_seen.values())},
        schema={"code": pl.String, "first_seen_date": pl.Date},
    )
    types_at_first_seen = classified.join(first_seen_frame, on="code", how="inner").filter(
        pl.col("classification_effective_from") == pl.col("first_seen_date")
    ).select("code", "instrument_type", "instrument_type_status")
    universe = (
        observations.select("date", "raw_code", usable_price_bar().alias("price_bar_available")).join(
            types_at_first_seen, left_on="raw_code", right_on="code", how="left",
        )
        .with_columns(
            pl.concat_str([pl.col("raw_code"), pl.lit(".TWSE")]).alias("market_symbol"),
            pl.lit(True).alias("observed_on_market"),
            pl.lit("TWSE").alias("exchange"),
            pl.col("instrument_type_status").fill_null("data_insufficient"),
        )
        .select("date", "market_symbol", "observed_on_market", "price_bar_available", "exchange",
                "instrument_type_status", "instrument_type")
        .unique(subset=["date", "market_symbol"], keep="first")
        .sort(["date", "market_symbol"])
    )
    identity = _frame_digest(classified, ("classification_effective_from", "code"))
    return universe, identity


def _fetch_actions(start: date, end: date) -> tuple[CorporateActionEvent, ...]:
    provider = CorporateActionProvider()
    events: list[CorporateActionEvent] = []
    try:
        for source in sorted(SOURCE_URLS):
            events.extend(provider.fetch(source, start, end))
    finally:
        provider.close()
    return tuple(events)


def _write_marker(
    store: CorporateActionStore, marker: Path, sources: list[str], span: tuple[date, date],
) -> None:
    marker.parent.mkdir(parents=True, exist_ok=True)
    temporary = marker.with_suffix(".tmp")
    temporary.write_text(json.dumps({
        "sources": sources, "start": span[0].isoformat(), "end": span[1].isoformat(),
        "events_sha256": store.snapshot_digest(),
        "recorded_at": datetime.now(TAIPEI).isoformat(),
    }), encoding="utf-8")
    os.replace(temporary, marker)


def _fetch_actions_on(source: str, day: date) -> tuple[CorporateActionEvent, ...]:
    provider = CorporateActionProvider()
    try:
        return provider.fetch(source, day, day)
    finally:
        provider.close()


def _provider_errors(
    events: Sequence[CorporateActionEvent], required: frozenset[str] | None,
) -> list[CorporateActionEvent]:
    return [e for e in events if e.status == "provider_error"
            and (required is None or e.symbol in required)]


def _action_snapshot(
    start: date, end: date, *, store: CorporateActionStore | None = None,
    required_symbols: frozenset[str] | None = None,
) -> tuple[tuple[CorporateActionEvent, ...], ActionCoverage]:
    """All five official corporate-action sources over [start, end].

    A full-history pull costs hours of throttled detail requests, so completed
    pulls are kept in the canonical ``CorporateActionStore`` next to a coverage
    marker; later calls only fetch the uncovered head/tail. A failed pull raises
    before anything is recorded, so a marker always means every source completed.

    A ``provider_error`` event (failed detail request) is not a claim about the
    event, and for a symbol in ``required_symbols`` it must never be recorded as
    covered: the pull fails before anything is stored, and stale errors already in
    the store are re-fetched for their own source and date.
    """
    store = store or CorporateActionStore()
    marker = store.path.with_name("coverage.json")
    sources = sorted(SOURCE_URLS)
    covered: tuple[date, date] | None = None
    if marker.is_file():
        record = json.loads(marker.read_text(encoding="utf-8"))
        if record.get("events_sha256") != (store.snapshot_digest() if store.path.is_file() else None):
            raise PrimaryOosInputError(
                "corporate-action coverage marker does not match the stored event snapshot; "
                "remove coverage.json to re-pull")
        if record.get("sources") == sources:
            covered = (date.fromisoformat(record["start"]), date.fromisoformat(record["end"]))
    if covered is None:
        gaps = [(start, end)]
        new_range = (start, end)
    else:
        gaps = (
            ([(start, covered[0] - timedelta(days=1))] if start < covered[0] else [])
            + ([(covered[1] + timedelta(days=1), end)] if end > covered[1] else [])
        )
        new_range = (min(start, covered[0]), max(end, covered[1]))
    if gaps:
        # Stage every gap first: the store and its marker only ever advance together.
        staged = [event for gap_start, gap_end in gaps
                  for event in _fetch_actions(gap_start, gap_end)]
        failed = _provider_errors(staged, required_symbols)
        if failed:
            raise PrimaryOosInputError(
                "corporate-action detail requests failed for "
                f"{sorted({e.symbol for e in failed})[:10]}; nothing was recorded as covered")
        store.save(staged)  # one atomic replace for every staged gap
        _write_marker(store, marker, sources, new_range)
    events = tuple(event for event in store.read() if start <= event.effective_date <= end)
    stale = _provider_errors(events, required_symbols)
    if stale:
        for source, day in sorted({(e.source, e.effective_date) for e in stale}):
            store.save(_fetch_actions_on(source, day))
        if marker.is_file():
            record = json.loads(marker.read_text(encoding="utf-8"))
            _write_marker(store, marker, sources,
                          (date.fromisoformat(record["start"]), date.fromisoformat(record["end"])))
        events = tuple(event for event in store.read() if start <= event.effective_date <= end)
        if _provider_errors(events, required_symbols):
            raise PrimaryOosInputError(
                "corporate-action events for Primary symbols remain provider_error after a retry")
    return events, ActionCoverage(start, end, "verified", tuple(sources))


def load_primary_oos_inputs(expected: PrimaryOosPreflight) -> PrimaryOosEvaluationInputs:
    """Read an already materialized PIT panel and historical observed prices.

    The loader fails closed if the shared gate changed while data were loading,
    a feature partition is absent for any verified Primary market observation,
    or the five official corporate-action sources do not complete successfully.
    """
    if expected.readiness.status.value != "ready":
        raise PrimaryOosNotReadyError(expected.readiness)
    fresh = read_primary_oos_preflight()
    if fresh.context(PRIMARY_OOS_SPEC.fingerprint) != expected.context(PRIMARY_OOS_SPEC.fingerprint):
        raise PrimaryOosNotReadyError(fresh.readiness)

    worker = TaiwanHistoricalBackfillWorker()
    panel = FactorPanelStore().read_all(
        factor_version=PRIMARY_OOS_SPEC.factor_version,
        policy_version=PRIMARY_OOS_SPEC.policy_version,
        universe_tier=PRIMARY_VERIFIED,
    )
    if panel.values.filter(
        (pl.col("usage_scope") != "pit_feature")
        | (pl.col("adjustment_status") != "verified")
    ).height:
        raise PrimaryOosInputError("PIT factor panel contains unverified feature rows")
    if any(name.startswith(("forward_return_", "label_", "future_return"))
           for name in panel.values.columns):
        raise PrimaryOosInputError("evaluation labels cannot enter the PIT feature panel")

    universe, classification_identity = _primary_universe(
        worker.census_store, worker.classification_store,
    )
    primary_members = universe.filter(
        (pl.col("instrument_type_status") == "verified")
        & (pl.col("instrument_type") == "stock")
        & pl.col("price_bar_available")
    )
    panel_keys = panel.values.select("date", pl.col("symbol").alias("market_symbol"))
    missing = primary_members.join(panel_keys, on=["date", "market_symbol"], how="anti")
    if missing.height:
        raise PrimaryOosInputError(
            f"PIT factor panel omits {missing.height} verified historical Primary universe rows; "
            "refusing survivorship-biased evaluation"
        )

    manifest = FeatureManifest(
        PRIMARY_OOS_SPEC.feature_schema_version,
        PRIMARY_OOS_SPEC.feature_specs,
    )
    admission = panel_training_matrix(
        panel, universe.filter(pl.col("price_bar_available")), PRIMARY_OOS_SPEC.universe_policy,
        manifest, {"corporate_action": price_dataset_capability(expected.data_health)},
    )
    if admission.matrix.is_empty():
        raise PrimaryOosInputError("PIT admission produced no verified Primary feature rows")

    raw = worker.census_store.read("TWSE")
    daily = raw.select(
        pl.concat_str([pl.col("raw_code"), pl.lit(".TWSE")]).alias("symbol"),
        pl.col("date"), pl.col("close"),
    ).sort(["symbol", "date"])
    if daily.is_empty() or daily.select(
        pl.struct("symbol", "date").is_duplicated().any()
    ).item():
        raise PrimaryOosInputError("A2a raw Primary daily prices are missing or duplicated")
    sessions = sorted(worker.census_store.session_dates("TWSE"))
    if not sessions:
        raise PrimaryOosInputError("verified TWSE market-session evidence is unavailable")
    latest_market_date = max(daily["date"].to_list())
    first_feature_date = min(admission.matrix["date"].to_list())
    sessions = [day for day in sessions if first_feature_date <= day <= latest_market_date]
    events, coverage = _action_snapshot(
        first_feature_date, latest_market_date,
        required_symbols=frozenset(primary_members["market_symbol"].unique().to_list()))
    fresh_after_load = read_primary_oos_preflight()
    if fresh_after_load.context(PRIMARY_OOS_SPEC.fingerprint) != expected.context(PRIMARY_OOS_SPEC.fingerprint):
        raise PrimaryOosNotReadyError(fresh_after_load.readiness)
    return PrimaryOosEvaluationInputs(
        preflight=expected,
        admission=admission,
        daily=daily,
        exchange_by_symbol={symbol: "TWSE" for symbol in daily["symbol"].unique().to_list()},
        sessions_by_exchange={"TWSE": sessions},
        walk_forward_sessions=sessions,
        events=events,
        action_coverage={"TWSE": coverage},
        classification_identity=classification_identity,
        latest_market_date=latest_market_date,
        rows_without_price_bar=int(universe.filter(
            (pl.col("instrument_type_status") == "verified")
            & (pl.col("instrument_type") == "stock") & ~pl.col("price_bar_available")).height),
        unresolved_type_codes=tuple(sorted(
            universe.filter(pl.col("instrument_type_status") != "verified")["market_symbol"]
            .str.removesuffix(".TWSE").unique().to_list())),
    )


def _input_identity(inputs: PrimaryOosEvaluationInputs) -> str:
    matrix = inputs.admission.matrix
    if matrix.is_empty():
        raise PrimaryOosInputError("cannot identify an empty Primary factor dataset")
    return canonical_hash({
        "factor_rows_sha256": _frame_digest(matrix, ("date", "symbol")),
        "raw_primary_prices_sha256": _frame_digest(
            inputs.daily.select("symbol", "date", "close"), ("symbol", "date"),
        ),
        "twse_sessions": list(inputs.sessions_by_exchange.get("TWSE", ())),
        "classification_identity": inputs.classification_identity,
        "events": sorted(event.content_hash for event in inputs.events),
        "action_coverage": {
            exchange: {
                "start": coverage.start,
                "end": coverage.end,
                "status": coverage.status,
                "sources": coverage.sources,
            }
            for exchange, coverage in inputs.action_coverage.items()
        },
    })


def _code_sha() -> str:
    repo = Path(__file__).resolve().parents[4]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=repo, check=True, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PrimaryOosInputError("git code version is unavailable") from exc
    value = result.stdout.strip()
    if len(value) != 40 or any(char not in "0123456789abcdef" for char in value.lower()):
        raise PrimaryOosInputError("git code version is invalid")
    return value


def _validate_report(report: Mapping[str, Any], spec: PrimaryOosEvaluationSpec) -> None:
    if (
        report.get("status") != "success"
        or report.get("primary_oos_ready") is not True
        or report.get("claim_scope") != "primary_verified_oos"
        or report.get("universe_tier") != PRIMARY_VERIFIED
        or report.get("horizons") != list(spec.horizons)
    ):
        raise PrimaryOosInputError("evaluation did not produce a complete Primary OOS result")
    folds = report.get("walk_forward", {}).get("folds", [])
    if not folds:
        raise PrimaryOosInputError("evaluation produced no complete walk-forward folds")
    for horizon in spec.horizons:
        summary = report.get("composite_score_ic", {}).get(str(horizon), {})
        bucket = report.get("composite_score_buckets", {}).get(str(horizon), {})
        oos_ic = report.get("walk_forward", {}).get("oos", {}).get(
            "composite_score_ic", {},
        ).get(str(horizon), {})
        if not isinstance(summary.get("n_dates"), int) or summary["n_dates"] <= 0:
            raise PrimaryOosInputError(f"{horizon}D composite IC has no observations")
        if any(bucket.get(name) is None for name in (
            "top_bucket_future_return", "bottom_bucket_future_return", "long_short_spread",
        )):
            raise PrimaryOosInputError(f"{horizon}D bucket returns are unavailable")
        if not isinstance(oos_ic.get("n_dates"), int) or oos_ic["n_dates"] <= 0:
            raise PrimaryOosInputError(f"{horizon}D walk-forward OOS has no observations")


def run_primary_oos_evaluation(
    preflight: PrimaryOosPreflight,
    *,
    input_loader: Callable[[PrimaryOosPreflight], PrimaryOosEvaluationInputs] = load_primary_oos_inputs,
    store: PrimaryOosRunStore | None = None,
    spec: PrimaryOosEvaluationSpec = PRIMARY_OOS_SPEC,
    code_sha: str | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(TAIPEI),
) -> dict[str, Any]:
    """Evaluate once after the shared gate passes; no health override exists."""
    readiness = preflight.readiness
    if readiness.status.value != "ready":
        raise PrimaryOosNotReadyError(readiness)
    store = store or PrimaryOosRunStore()
    lock = WorkerLock(store.path.with_name(".primary_oos.lock"))
    try:
        with lock:
            inputs = input_loader(preflight)
            if inputs.preflight.context(spec.fingerprint) != preflight.context(spec.fingerprint):
                raise PrimaryOosNotReadyError(inputs.preflight.readiness)
            fresh_readiness = inputs.preflight.readiness
            if fresh_readiness.status.value != "ready":
                raise PrimaryOosNotReadyError(fresh_readiness)
            matrix = inputs.admission.matrix
            if not matrix.height or matrix["universe_tier"].unique().to_list() != [PRIMARY_VERIFIED]:
                raise PrimaryOosInputError("runner only accepts Primary Verified PIT feature rows")
            forbidden = [name for name in matrix.columns
                         if name.startswith(("forward_return_", "label_", "future_return"))]
            if forbidden:
                raise PrimaryOosInputError("future labels are isolated from the factor feature matrix")
            for exchange, coverage in inputs.action_coverage.items():
                if not coverage.covers(
                    min(matrix["date"].to_list()), inputs.latest_market_date,
                ):
                    raise PrimaryOosInputError(
                        f"{exchange} corporate-action source coverage is incomplete"
                    )
            if set(inputs.action_coverage) != {"TWSE"}:
                raise PrimaryOosInputError("Primary OOS source must contain TWSE coverage only")

            actual_code_sha = code_sha or _code_sha()
            dataset_identity = _input_identity(inputs)
            spec_data = spec.describe()
            spec_hash = spec.fingerprint
            identity_key = canonical_hash({
                "spec_hash": spec_hash,
                "dataset_identity": dataset_identity,
                "code_sha": actual_code_sha,
            })
            created_at = now().isoformat()
            run_id = str(uuid.uuid4())
            context = inputs.preflight.context(spec_hash)
            context.update({"dataset_identity": dataset_identity, "code_sha": actual_code_sha})
            previous = store.begin(
                run_id=run_id,
                identity_key=identity_key,
                recorded_at=created_at,
                context=context,
            )
            if previous is not None:
                return {
                    "status": "evaluation_available", "run_id": previous["run_id"],
                    "reused": True, **previous["artifact"],
                }
            try:
                report = evaluate_quant(
                    inputs.admission,
                    inputs.daily,
                    exchange_by_symbol=inputs.exchange_by_symbol,
                    sessions_by_exchange=inputs.sessions_by_exchange,
                    walk_forward_sessions=inputs.walk_forward_sessions,
                    events=inputs.events,
                    action_coverage=inputs.action_coverage,
                    data_health=inputs.preflight.data_health,
                    a2b_progress=inputs.preflight.a2b_progress,
                    a2b_worker_status=inputs.preflight.a2b_worker_status,
                    horizons=spec.horizons,
                    fold_config=spec.fold_config,
                    bucket_fraction=spec.bucket_fraction,
                )
                _validate_report(report, spec)
                artifact = {
                    "provenance": {
                        "run_id": run_id,
                        "created_at": created_at,
                        "code_sha": actual_code_sha,
                        "evaluation_spec_version": spec.version,
                        "evaluation_spec_hash": spec_hash,
                        "evaluation_spec": spec_data,
                        "dataset_identity": dataset_identity,
                        "latest_market_date": inputs.latest_market_date.isoformat(),
                        "a2b_classification_identity": inputs.classification_identity,
                        "unresolved_type_codes": list(inputs.unresolved_type_codes),
                        "rows_without_price_bar": inputs.rows_without_price_bar,
                        "claim_scope_description": CLAIM_SCOPE_DESCRIPTION,
                        "data_health_policy": data_health_policy_record(
                            inputs.preflight.data_health, inputs.unresolved_type_codes),
                        "a2b": store.progress_snapshot(inputs.preflight.a2b_progress),
                        "data_health": inputs.preflight.data_health.describe(),
                        "horizons": list(spec.horizons),
                        "universe_definition": spec_data["universe_policy"],
                        "factor_definitions": spec_data["factor_panel"]["factor_definitions"],
                        "walk_forward_configuration": spec.fold_config.describe(),
                        "random_seed": None,
                    },
                    "evaluation": report,
                }
                store.succeed(
                    run_id=run_id, identity_key=identity_key,
                    recorded_at=now().isoformat(), context=context, artifact=artifact,
                )
                return {"status": "evaluation_available", "reused": False, **artifact}
            except Exception as exc:
                store.fail(
                    run_id=run_id, identity_key=identity_key,
                    recorded_at=now().isoformat(), context=context,
                    error_code=type(exc).__name__,
                )
                if isinstance(exc, PrimaryOosInputError):
                    raise PrimaryOosRunFailedError(run_id, type(exc).__name__) from exc
                raise PrimaryOosRunFailedError(run_id, type(exc).__name__) from exc
    except WorkerBusyError:
        return {"status": "evaluation_running", "run_id": None, "reused": False}

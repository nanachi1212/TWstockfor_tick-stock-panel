from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import data_health
from app.taiwan.data_health_center import (
    DATASETS,
    DataHealthService,
    combined_status,
    finmind_policy,
    normalize_status,
    safe_reason,
)
from app.taiwan.data_health_jobs import HealthJobManager, execute_existing
from app.taiwan.finmind_cache import FinMindCache


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)


def report(status="unavailable"):
    return DataHealthService({key: lambda: {"status": status} for key in DATASETS}).snapshot()


def wait_job(manager, job_id):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        job = manager.get(job_id)
        if job.status not in {"queued", "running"}:
            return job
        time.sleep(0.01)
    pytest.fail("job did not finish")


@pytest.mark.parametrize(
    ("raw", "stale", "expected"),
    [
        ("available", False, "current"),
        ("available", True, "stale"),
        ("partial", False, "partial"),
        ("degraded", False, "partial"),
        ("fallback", False, "partial"),
        ("missing", False, "unavailable"),
        ("not_queried", False, "unavailable"),
        ("data_insufficient", False, "unavailable"),
        ("failed", False, "error"),
        ("queued", False, "updating"),
        ("unknown", False, "unavailable"),
    ],
)
def test_status_contract(raw, stale, expected):
    assert normalize_status(raw, stale=stale) == expected


def test_aggregation_isolates_provider_failure_and_keeps_missing_null():
    def broken():
        raise RuntimeError("api_key=sk-private https://secret.example/token")

    readers = {key: lambda: {"status": "unavailable"} for key in DATASETS}
    readers["margin"] = lambda: {"status": "stale", "data_date": "2026-09-29", "source": "official"}
    readers["financial"] = broken
    snapshot = DataHealthService(readers).snapshot()
    assert len(snapshot.datasets) == len(DATASETS)
    rows = {row.id: row for row in snapshot.datasets}
    assert rows["margin"].data_date == "2026-09-29"
    assert rows["financial"].status == "error"
    assert rows["dcard"].data_date is None
    assert rows["dcard"].last_success is None
    assert rows["dcard"].actions == []
    # Benchmarks now have an official updater (the same daily run).
    assert rows["taiex"].actions == ["validate", "retry"]
    assert "sk-private" not in snapshot.model_dump_json()
    assert "secret.example" not in snapshot.model_dump_json()


def test_summary_counts_only_current():
    readers = {key: lambda: {"status": "partial"} for key in DATASETS}
    readers["daily"] = lambda: {"status": "current"}
    snapshot = DataHealthService(readers).snapshot()
    assert snapshot.current_count == 1
    assert snapshot.total_count == len(DATASETS)
    assert combined_status(["current", "unavailable"]) == "partial"
    assert combined_status([]) == "unavailable"


def test_group_metadata_reloads_each_snapshot(monkeypatch):
    calls = []

    def daily():
        calls.append(1)
        return {
            key: {"status": "current" if len(calls) == 1 else "stale"}
            for key in ("daily", "institutional", "margin")
        }

    monkeypatch.setattr(DataHealthService, "_daily", lambda self: daily())
    for name, keys in (
        (
            "_finmind",
            ["financial", "monthly_revenue", "foreign_shareholding", "securities_lending"],
        ),
        ("_social", ["ptt", "dcard", "social_ai"]),
        ("_selection", ["selection_snapshot", "selection_outcome"]),
    ):
        monkeypatch.setattr(DataHealthService, name, lambda self, keys=keys: {k: {} for k in keys})
    for name in ("_realtime", "_security_master", "_calendar", "_quant", "_ai"):
        monkeypatch.setattr(DataHealthService, name, lambda self: {})
    svc = DataHealthService()
    assert svc.snapshot().datasets[0].status == "current"
    assert svc.snapshot().datasets[0].status == "stale"
    assert len(calls) == 2


def test_finmind_metadata_expired_empty_corrupt_and_no_fetch(tmp_path):
    cache = FinMindCache(tmp_path)
    dataset = "TaiwanStockMonthRevenue"
    cache.set(dataset, "2330.TWSE", [{"date": "2026-08-01"}], data_date="2026-08-01")
    path = tmp_path / dataset / "2330.TWSE.json"
    raw = json.loads(path.read_text())
    raw["fetched_at"] = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    path.write_text(json.dumps(raw))
    cache.set(dataset, "2317.TWSE", [], status="available")
    (tmp_path / dataset / "bad.json").write_text("invalid")
    assert {r["status"] for r in cache.health_metadata(dataset)} == {
        "stale",
        "unavailable",
        "error",
    }
    assert cache.get(dataset, "2330.TWSE") is None
    assert path.exists()  # health inspection retains expired evidence


def test_daily_metadata_reuses_freshness_and_checks_exchange_coverage(monkeypatch, tmp_path):
    from app.taiwan import daily_update

    def store(symbols):
        return SimpleNamespace(
            read_latest_date_rows=lambda: pl.DataFrame({"symbol": symbols}),
            _data_dir=tmp_path / "daily",
        )

    freshness = SimpleNamespace(
        model_dump=lambda: {
            "daily_status": "current",
            "daily_as_of": "2026-09-29",
            "daily_days_behind": 0,
            "institutional_status": "current",
            "institutional_as_of": "2026-09-29",
            "institutional_days_behind": 0,
            "margin_status": "stale",
            "margin_as_of": "2026-09-28",
            "margin_days_behind": 1,
            "target_latest_trading_date": "2026-09-29",
        }
    )
    svc = SimpleNamespace(
        get_freshness=lambda: freshness,
        daily_store=store(["2330.TWSE", "8069.TPEX"]),
        inst_store=store(["2330.TWSE"]),
        margin_store=store(["2330.TWSE", "8069.TPEX"]),
    )
    monkeypatch.setattr(daily_update, "TaiwanDailyUpdateService", lambda: svc)
    rows = DataHealthService()._daily()
    assert rows["daily"]["status"] == "current"
    assert rows["institutional"]["status"] == "partial"
    assert rows["margin"]["status"] == "stale"
    assert "TPEX" in rows["institutional"]["reason"]
    assert rows["daily"]["last_success"] is None  # no partition, no invented timestamp
    assert rows["daily"]["last_attempt"] is None  # no recorded updater run

    def fail_partition():
        raise ValueError("private partition path")

    svc.inst_store.read_latest_date_rows = fail_partition
    rows = DataHealthService()._daily()
    assert rows["institutional"]["status"] == "error"
    assert rows["daily"]["status"] == "current"
    assert rows["margin"]["status"] == "stale"
    assert "private" not in rows["institutional"]["reason"]


def test_social_403_is_reason_only_and_ai_degraded(monkeypatch):
    from app.taiwan import social_sentiment

    monkeypatch.setattr(
        social_sentiment,
        "load_social_sentiment",
        lambda: {
            "as_of": date.today().isoformat(),
            "started_at": "2026-09-30T09:00:00+08:00",
            "sources": {
                "ptt": {"status": "available"},
                "dcard": {
                    "status": "unavailable",
                    "errors": ["HTTP 403 at https://secret.example?token=sk-abc"],
                },
            },
            "ai": {"status": "degraded"},
        },
    )
    rows = DataHealthService()._social()
    assert rows["dcard"]["status"] == "provider_error"
    assert "HTTP 403" in rows["dcard"]["reason"]
    assert rows["dcard"]["last_success"] is None
    assert rows["dcard"]["data_date"] is None
    assert rows["social_ai"]["status"] == "partial"
    assert "sk-abc" not in json.dumps(rows)


@pytest.mark.parametrize(
    "raw",
    [
        "HTTP 403 token=sk-secret",
        "HTTP status=503 Bearer abc",
        "C:\\private\\keys api_key=sk-secret",
        "Authorization: abc",
    ],
)
def test_no_secrets_from_reason(raw):
    value = safe_reason(raw)
    assert "sk-secret" not in value and "Bearer" not in value and "private" not in value


def test_duplicate_update_is_single_flight_across_related_datasets():
    release, started = threading.Event(), threading.Event()
    calls = []

    def runner(dataset, action):
        calls.append((dataset, action))
        started.set()
        assert release.wait(3)
        return "partial", "部分官方資料尚未發布"

    manager = HealthJobManager(runner=runner, reader=report)
    try:
        first = manager.start("daily", "retry")
        assert first.status == "queued"
        assert started.wait(1)
        with ThreadPoolExecutor(max_workers=8) as pool:
            duplicates = list(pool.map(lambda _: manager.start("margin", "retry"), range(16)))
        assert {j.job_id for j in duplicates} == {first.job_id}
        snapshot = manager.snapshot()
        assert {
            r.status for r in snapshot.datasets if r.id in {"daily", "margin", "institutional"}
        } == {"updating"}
        assert len(calls) == 1
    finally:
        release.set()
    done = wait_job(manager, first.job_id)
    assert done.status == "partial"
    assert done.finished_at and done.started_at
    retry = manager.start("margin", "retry")
    assert retry.job_id != first.job_id
    assert wait_job(manager, retry.job_id).status == "partial"


def test_provider_failure_safe_retry_and_bounded_registry():
    calls = []

    def runner(dataset, action):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("Authorization: Bearer secret https://private")
        return "completed", "檢查完成"

    manager = HealthJobManager(runner=runner, reader=report)
    first = manager.start("daily", "retry")
    assert wait_job(manager, first.job_id).status == "failed"
    assert "Bearer" not in manager.snapshot().model_dump_json()
    retry = manager.start("daily", "retry")
    assert wait_job(manager, retry.job_id).status == "completed"
    assert manager.snapshot().datasets[0].status == "unavailable"  # no fake success
    for _ in range(62):
        assert wait_job(manager, manager.start("daily", "validate").job_id).status == "completed"
    assert len(manager.list_jobs()) == 60


def test_action_whitelist_and_api(monkeypatch):
    manager = HealthJobManager(
        runner=lambda dataset, action: ("completed", "檢查完成"), reader=report
    )
    monkeypatch.setattr(data_health, "get_health_job_manager", lambda: manager)
    app = FastAPI()
    app.include_router(data_health.router)
    client = TestClient(app)
    assert client.get("/api/taiwan/data-health").json()["total_count"] == len(DATASETS)
    for dataset in ("dcard", "financial", "quant_live"):
        assert (
            client.post(
                f"/api/taiwan/data-health/{dataset}/actions", json={"action": "retry"}
            ).status_code
            == 409
        )
    assert (
        client.post("/api/taiwan/data-health/daily/actions", json={"action": "crack"}).status_code
        == 422
    )
    assert (
        client.post("/api/taiwan/data-health/unknown/actions", json={"action": "retry"}).status_code
        == 404
    )
    response = client.post("/api/taiwan/data-health/margin/actions", json={"action": "retry"})
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    wait_job(manager, job_id)
    assert client.get(f"/api/taiwan/data-health/jobs/{job_id}").json()["status"] == "completed"
    assert client.get("/api/taiwan/data-health/jobs").json()
    assert client.get("/api/taiwan/data-health/jobs/unknown").status_code == 404


def test_existing_daily_updater_is_called_once_and_partial_is_preserved(monkeypatch):
    from app.taiwan import daily_update

    calls = []
    monkeypatch.setattr(
        daily_update,
        "TaiwanDailyUpdateService",
        lambda: SimpleNamespace(
            run_update=lambda: calls.append(1) or SimpleNamespace(overall_status="partial")
        ),
    )
    monkeypatch.setattr("app.taiwan.data_health_jobs.get_health_snapshot", report)
    assert execute_existing("margin", "retry")[0] == "partial"
    assert calls == [1]


def test_existing_social_manager_is_reused(monkeypatch):
    from app.taiwan import social_sentiment_jobs

    calls = []
    manager = SimpleNamespace(
        start_manual=lambda: calls.append(1) or {"job_id": "existing"},
        get_job=lambda job_id: {"status": "partial"},
    )
    monkeypatch.setattr(social_sentiment_jobs, "get_social_sentiment_job_manager", lambda: manager)
    assert execute_existing("ptt", "retry")[0] == "partial"
    assert calls == [1]


def test_ai_validation_invalidated_by_configuration_change(monkeypatch):
    from app.taiwan import data_health_jobs

    revision = ["first"]
    monkeypatch.setattr(data_health_jobs, "_ai_revision", lambda: revision[0])
    manager = HealthJobManager(
        runner=lambda dataset, action: ("completed", "驗證成功"), reader=report
    )
    job = manager.start("ai_provider", "validate")
    wait_job(manager, job.job_id)
    rows = {row.id: row for row in manager.snapshot().datasets}
    assert rows["ai_provider"].status == "current"
    revision[0] = "second"
    rows = {row.id: row for row in manager.snapshot().datasets}
    assert rows["ai_provider"].status == "unavailable"


def test_finmind_reader_aggregates_per_symbol_ttl_and_null_dates(monkeypatch):
    from app.taiwan import data_health_center

    monkeypatch.setattr(data_health_center, "etf_symbols", lambda: set())
    monkeypatch.setattr(
        FinMindCache,
        "health_metadata",
        lambda self, dataset: [
            {
                "symbol": "2330.TWSE",
                "status": "available",
                "fetched_at": "2026-09-30T09:00:00+08:00",
                "data_date": "2026-08-01",
            },
            {"symbol": "2454.TWSE", "status": "unavailable",
             "fetched_at": "2026-09-30T02:00:00+00:00", "data_date": None},
        ],
    )
    row = DataHealthService()._finmind()["monthly_revenue"]
    assert row["status"] == "partial"
    assert row["last_attempt"] == "2026-09-30T02:00:00+00:00"
    assert row["last_success"] == "2026-09-30T01:00:00+00:00"


@pytest.mark.parametrize(
    ("records", "expected_status"),
    [
        ([{"status": "error", "error_msg": "HTTP 503"}], "error"),
        ([{"status": "unavailable"}], "unavailable"),
        (
            [
                {"status": "unavailable"},
                {"status": "available", "data_date": "2026-08-01"},
            ],
            "partial",
        ),
    ],
)
def test_finmind_reporting_cache_fails_closed_when_all_records_are_invalid(
    records, expected_status,
):
    status, _ = finmind_policy(
        "monthly_revenue",
        records,
        date(2026, 8, 1),
        6 * 3600,
    )
    assert status == expected_status


def test_persisted_benchmark_available_awaiting_and_missing(monkeypatch):
    from app.taiwan import daily_update, data_health_center
    from app.taiwan.benchmark_store import TaiwanBenchmarkStore

    now = datetime(2026, 9, 30, 16, 40)
    monkeypatch.setattr(data_health_center, "taipei_now", lambda: now)
    monkeypatch.setattr(
        daily_update, "resolve_target_latest_trading_date", lambda *a, **k: date(2026, 9, 30)
    )
    missing = DataHealthService()._index("taiex")
    assert missing["status"] == "unavailable" and missing.get("data_date") is None

    TaiwanBenchmarkStore().write(pl.DataFrame({
        "symbol": ["TAIEX", "TAIEX", "TPEX_INDEX"],
        "date": [date(2026, 9, 24), date(2026, 9, 29), date(2026, 9, 30)],
        "open": [None, None, 414.21], "high": [None, None, 419.03], "low": [None, None, 414.21],
        "close": [48024.6, 47631.96, 417.07],
        "source": ["twse:MI_5MINS_HIST", "twse:MI_5MINS_HIST", "tpex:tpex_index"],
        "source_url": ["u", "u", "u"], "retrieved_at": ["2026-09-30T16:30:00+08:00"] * 3,
    }))
    taiex = DataHealthService()._index("taiex")
    assert taiex["status"] == "awaiting_publication"  # official OpenAPI lags one session
    assert taiex["data_date"] == "2026-09-29"
    assert DataHealthService()._index("tpex_index")["status"] == "current"


def test_persisted_benchmark_failed_refresh_is_not_publication_lag(monkeypatch):
    from app.taiwan import daily_update, data_health_center
    from app.taiwan.benchmark_store import TaiwanBenchmarkStore

    now = datetime(2026, 9, 30, 16, 40)
    monkeypatch.setattr(data_health_center, "taipei_now", lambda: now)
    monkeypatch.setattr(
        daily_update,
        "resolve_target_latest_trading_date",
        lambda *a, **k: date(2026, 9, 30),
    )
    monkeypatch.setattr(
        daily_update,
        "read_last_run",
        lambda *a, **k: {
            "run_started_at": "2026-09-30T16:30:00+08:00",
            "target_latest_trading_date": "2026-09-30",
            "benchmark": {
                "failed": [
                    {
                        "symbol": "TAIEX",
                        "error": "HTTP 503 https://private.example?token=secret",
                    }
                ]
            },
        },
    )
    TaiwanBenchmarkStore().write(pl.DataFrame({
        "symbol": ["TAIEX"],
        "date": [date(2026, 9, 29)],
        "open": [None],
        "high": [None],
        "low": [None],
        "close": [47631.96],
        "source": ["twse:MI_5MINS_HIST"],
        "source_url": ["u"],
        "retrieved_at": ["2026-09-29T16:30:00+08:00"],
    }))

    row = DataHealthService()._index("taiex")
    assert row["status"] == "provider_error"
    assert "HTTP 503" in row["reason"]
    assert "private.example" not in row["reason"]
    assert row["last_attempt"] == "2026-09-30T08:30:00+00:00"


def test_security_master_offline_metadata_does_not_fetch(tmp_path):
    from app.taiwan.universe.service import TaiwanSecurityMaster

    master = TaiwanSecurityMaster(cache_path=tmp_path / "master.parquet")
    assert master.health_metadata()["status"] == "unavailable"
    pl.DataFrame(
        {"exchange": ["TWSE"], "source": ["official"], "updated_at": ["2026-09-29T10:00:00+08:00"]}
    ).write_parquet(master.cache_path)
    assert master.health_metadata()["status"] == "partial"
    assert not master._loaded


def test_selection_metadata_preserves_pending_and_unavailable(tmp_path, monkeypatch):
    from app.taiwan import daily_update
    from app.taiwan.selection_review_models import (
        HorizonReviewItem,
        SelectionSnapshot,
        SnapshotReviewDetail,
    )
    from app.taiwan.selection_review_service import TaiwanSelectionReviewService

    svc = TaiwanSelectionReviewService(path=tmp_path / "snapshots.json")
    assert svc.health_metadata()["selection_snapshot"]["status"] == "unavailable"
    snapshot = SelectionSnapshot(
        snapshot_id="snap",
        created_at="2026-09-29T18:00:00+08:00",
        strategy_id="trend",
        strategy_name="trend",
        as_of_date="2026-09-29",
        market_context_summary="",
    )
    monkeypatch.setattr(svc, "_read_snapshots_raw", lambda: [snapshot])
    monkeypatch.setattr(
        daily_update,
        "resolve_target_latest_trading_date",
        lambda *args, **kwargs: date(2026, 9, 30),
    )
    item = HorizonReviewItem(symbol="2330.TWSE", name="台積電", rank=1, entry_price=100)
    monkeypatch.setattr(
        svc,
        "get_snapshot_review",
        lambda snapshot_id: SnapshotReviewDetail(snapshot=snapshot, evaluated_items=[item]),
    )
    rows = svc.health_metadata()
    # Snapshots are locked manually: an older one is "not run today", not stale.
    assert rows["selection_snapshot"]["status"] == "not_run"
    # Horizons not yet due are expected, not missing data.
    assert rows["selection_outcome"]["status"] == "current"
    done = item.model_copy(update={"h1d_status": "completed", "h1d_bm_status": "unavailable"})
    monkeypatch.setattr(
        svc,
        "get_snapshot_review",
        lambda snapshot_id: SnapshotReviewDetail(snapshot=snapshot, evaluated_items=[done]),
    )
    assert svc.health_metadata()["selection_outcome"]["status"] == "partial"
    monkeypatch.setattr(
        svc, "get_snapshot_review", lambda snapshot_id: SnapshotReviewDetail(snapshot=snapshot)
    )
    assert svc.health_metadata()["selection_outcome"]["status"] == "unavailable"

    def fail_review(snapshot_id):
        raise ValueError("private outcome path")

    monkeypatch.setattr(svc, "get_snapshot_review", fail_review)
    rows = svc.health_metadata()
    assert rows["selection_snapshot"]["status"] == "not_run"
    assert rows["selection_outcome"]["status"] == "error"
    assert "private" not in rows["selection_outcome"]["reason"]


@pytest.mark.parametrize(
    ("audit", "reason", "expected"),
    [
        ("ok", "current", "current"),
        ("conflict", "current", "error"),
        ("ok", "operation_not_current_success", "unavailable"),
    ],
)
def test_quant_respects_existing_operation_and_audit_gate(monkeypatch, audit, reason, expected):
    from app.taiwan.quant import live_store

    ledger = SimpleNamespace(
        current_session=lambda: date(2026, 9, 29),
        latest_operation=lambda: {},
        read_run=lambda *args: {"audit_status": audit},
        operation_projection=lambda *args: {"reason": reason},
    )
    monkeypatch.setattr(live_store, "LiveLedger", lambda **kwargs: ledger)
    assert DataHealthService()._quant()["status"] == expected


def test_full_metadata_aggregation_never_fetches_market_or_ai(taiwan_data_env, monkeypatch):
    import httpx

    from app.services import ai_provider
    from app.taiwan import selection_review_service

    def no_http(*args, **kwargs):
        pytest.fail("health GET must not fetch remote data")

    monkeypatch.setattr(httpx.Client, "get", no_http)
    monkeypatch.setattr(httpx.Client, "post", no_http)
    monkeypatch.setattr(ai_provider, "generate_ai_text", no_http)
    monkeypatch.setattr(selection_review_service, "_service_instance", None)
    snapshot = DataHealthService().snapshot()
    assert snapshot.total_count == len(DATASETS)
    assert all(row.status != "error" for row in snapshot.datasets)
    daily = next(row for row in snapshot.datasets if row.id == "daily")
    assert daily.data_date == taiwan_data_env["target"].isoformat()
    assert daily.last_attempt is None


def test_ai_failure_does_not_survive_configuration_change(monkeypatch):
    from app.taiwan import data_health_jobs

    revision = ["first"]
    monkeypatch.setattr(data_health_jobs, "_ai_revision", lambda: revision[0])
    manager = HealthJobManager(runner=lambda dataset, action: ("failed", "連線失敗"), reader=report)
    wait_job(manager, manager.start("ai_provider", "validate").job_id)
    rows = {row.id: row for row in manager.snapshot().datasets}
    assert rows["ai_provider"].status == "error"
    revision[0] = "second"
    rows = {row.id: row for row in manager.snapshot().datasets}
    assert rows["ai_provider"].status == "unavailable"

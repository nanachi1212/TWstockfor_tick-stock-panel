import hashlib
import json
from datetime import date, datetime
from types import SimpleNamespace

import polars as pl
import pytest

from app.taiwan.observed_universe import ObservedUniverseStore
from app.taiwan.quant.live_contract import canonical_hash, canonical_json
from app.taiwan.realtime.calendar import TAIPEI_TZ, TaiwanTradingCalendar
from app.taiwan.selection_review_models import SelectionSnapshot
from app.taiwan.weekly_review_report import _legacy_records, generate_report, main, render_markdown
from app.taiwan.weekly_review_stats import build_weekly_review


def formal(day="2026-11-13", *, locked=None, next_day="2026-11-16", kind="forward_batch"):
    return {
        "as_of_date": day, "source_data_date": day, "record_type": kind,
        "created_at": locked or f"{day}T17:00:00+08:00", "locked_at": locked or f"{day}T17:00:00+08:00",
        "target_trade_date": next_day, "snapshot_id": "batch-" + day, "strategy_name": "突破轉強",
        "strategy_id": "breakout_v1", "rule_version": "breakout_v1", "strategy_version": "v1",
        "risk_source_as_of": "2026-11-14T12:00:00+08:00", "market_context_summary": "fixture",
        "selected_symbols": ["2330.TWSE"], "items": [{"symbol": "2330.TWSE", "name": "台積電", "rank": 1, "price": 100}],
    }


def test_historical_batches_require_final_session_and_contemporaneous_lock():
    source = date(2026, 11, 13)
    batches = [formal(), formal(kind="research"), formal("2026-11-12", next_day="2026-11-13"),
               formal(locked="2026-11-16T09:00:00+08:00")]
    evaluator = SimpleNamespace(week_close=lambda d, market: source)
    cutoff = datetime(2026, 11, 17, tzinfo=TAIPEI_TZ)
    records, admitted, skipped = _legacy_records(
        [SelectionSnapshot(**b) for b in batches], cutoff, evaluator, TaiwanTradingCalendar(), None, set(),
    )
    assert [r["symbol"] for r in records] == ["2330.TWSE"]
    assert [r["risk_date_after_close_day"] for r in admitted] == [True]
    assert skipped == {"research": 1, "not_verified_week_close": 1, "outside_lock_window": 1}


def _data_dir(tmp_path):
    user = tmp_path / "user_data"
    user.mkdir()
    (user / "taiwan_selection_snapshots.json").write_text(json.dumps([formal()]), encoding="utf8")
    day = date(2026, 11, 13)
    target = tmp_path / "taiwan" / "daily" / f"date={day}"
    target.mkdir(parents=True)
    pl.DataFrame([{"symbol": symbol, "date": day, "open": 100., "high": 102., "low": 99., "close": 100.,
                   "volume": 1000., "amount": 100000., "quote_ts": 0, "source": "twse_tpex_official_snapshot",
                   "status": "official"} for symbol in ("2330.TWSE", "6488.TPEX")]).write_parquet(target / "part.parquet")
    return user


def _ledger_record(stamp="2026-11-13T09:00:00+00:00", symbol="2330.TWSE", day="2026-11-13"):
    record = {"week": date.fromisoformat(day).strftime("%G-W%V"), "last_trading_day": day, "written_at": stamp,
              "signal_basis": "daily_strategy_weekly_snapshot", "formal_batch_id": f"batch-{day}",
              "source_strategy": {"id": "breakout_v1", "name": "突破轉強", "version": "v1", "rule_version": "breakout_v1"},
              "symbol": symbol, "decision": "signal", "pattern_tag": "breakout", "reference_close": 100,
              "snapshot": {"rank": 1}}
    record["snapshot_sha256"] = canonical_hash(record["snapshot"])
    record["record_sha256"] = canonical_hash(record)
    return record


def _save_bound_fixture(user, rows, batch, *, legacy=()):
    week = date.fromisoformat(batch["as_of_date"]).strftime("%G-W%V")
    path = user / "weekly_selection" / week / f"{batch['strategy_id']}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes("".join(canonical_json(r) + "\n" for r in rows).encode("utf8"))
    batch["weekly_records_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (user / "taiwan_selection_snapshots.json").write_text(json.dumps([*legacy, batch]), encoding="utf8")


@pytest.mark.parametrize("has_legacy,name,corrupt", [
    (True, "breakout_v1", False), (False, "breakout_v1", False),
    (True, "unrelated_name", False), (True, "breakout_v1", True), (False, "unrelated_name", True),
])
def test_unbound_weekly_file_cannot_supply_signals_or_displace_legacy(tmp_path, has_legacy, name, corrupt):
    user = _data_dir(tmp_path)
    if not has_legacy:
        (user / "taiwan_selection_snapshots.json").write_text("[]", encoding="utf8")
    path = user / "weekly_selection" / "2026-W46" / f"{name}.jsonl"
    path.parent.mkdir(parents=True)
    # Even matching strategy metadata, formal_batch_id, timestamps and both
    # per-row hashes cannot prove this file came from the locked run.
    row = _ledger_record(symbol="6488.TPEX")
    path.write_bytes(b"broken JSONL" if corrupt else (canonical_json(row) + "\n").encode("utf8"))
    report = generate_report(tmp_path, date(2026, 11, 16))
    assert report["weekly_record_count"] == 0 and report["weekly"]["groups"] == []
    expected = ["2330.TWSE"] if has_legacy else []
    assert [g["symbol"] for g in report["historical"]["groups"]] == expected
    rejected, = report["rejected_weekly_batches"]
    assert rejected["file"] == f"2026-W46/{name}.jsonl" and rejected["reason"] == "weekly_file_unbound"
    assert rejected["reason"] in render_markdown(report)


@pytest.mark.parametrize("has_binding", [True, False])
def test_as_of_omits_future_bound_and_unbound_files(tmp_path, has_binding):
    user = _data_dir(tmp_path)
    _save_bound_fixture(user, [_ledger_record()], formal())
    if not has_binding:
        (user / "taiwan_selection_snapshots.json").write_text(json.dumps([formal()]), encoding="utf8")
    future_path = user / "weekly_selection" / "2026-W47" / "unbound.jsonl"
    future_path.parent.mkdir()
    future_path.write_bytes(b"corrupt future file")
    report = generate_report(tmp_path, date(2026, 11, 11))
    assert report["weekly_record_count"] == 0 and report["rejected_weekly_batches"] == []


def test_true_ledger_priority_and_integrity_in_local_report(tmp_path):
    user = _data_dir(tmp_path)
    as_of = date(2026, 11, 16)
    legacy = generate_report(tmp_path, as_of)
    assert (legacy["weekly_record_count"], legacy["historical_record_count"]) == (0, 1)
    row = legacy["historical"]["strategies"][0]
    assert row["pending_count"] == 1 and row["mean_return_pct"] is None and row["missed_count"] is None
    assert "樣本不足，僅供參考" in render_markdown(legacy)
    record = _ledger_record()
    batch = formal()
    _save_bound_fixture(user, [record], batch)
    actual = generate_report(tmp_path, as_of)
    assert (actual["weekly_record_count"], actual["historical_record_count"]) == (1, 0)
    assert actual["skipped_batches"]["covered_by_weekly_ledger"] == 1
    assert actual["weekly"]["strategies"][0]["pending_count"] == 1
    earlier_day = date(2026, 11, 6)
    prices = tmp_path / "taiwan" / "daily"
    earlier = prices / f"date={earlier_day}"
    earlier.mkdir()
    pl.read_parquet(prices / "date=2026-11-13" / "part.parquet", hive_partitioning=False).with_columns(
        pl.lit(earlier_day).alias("date")).write_parquet(earlier / "part.parquet")
    earlier_batch = formal(day="2026-11-06", next_day="2026-11-09")
    _save_bound_fixture(user, [record], batch, legacy=[earlier_batch])
    mixed = generate_report(tmp_path, as_of)
    group, = mixed["combined"]["groups"]
    assert group["weeks"] == ["2026-W45", "2026-W46"] and group["start_day"] == "2026-11-06"
    assert (group["execution_session"], group["execution_open_status"]) == ("2026-11-09", "not_recorded")
    assert "2 筆入選合併為 **1 組**" in render_markdown(mixed)
    record["reference_close"] = 101
    # The batch digest covers this exact file, but the row's digest no longer matches.
    _save_bound_fixture(user, [record], batch, legacy=[earlier_batch])
    corrupt = generate_report(tmp_path, as_of)
    assert corrupt["weekly_record_count"] == 0 and corrupt["historical_record_count"] == 1
    assert corrupt["rejected_weekly_batches"][0]["reason"] == "weekly_record_digest_mismatch"


@pytest.mark.parametrize("stamp", ["2026-11-13T13:29:59+08:00", "2026-11-16T09:00:00+08:00"])
def test_out_of_window_weekly_file_rejected_without_displacing_valid_history(tmp_path, stamp):
    user = _data_dir(tmp_path)
    valid_legacy = {**formal(), "strategy_id": "trend_v1", "strategy_name": "趨勢", "snapshot_id": "other-legacy"}
    rows = [_ledger_record(), _ledger_record(stamp, "6488.TPEX")]
    _save_bound_fixture(user, rows, formal(), legacy=[valid_legacy])
    report = generate_report(tmp_path, date(2026, 11, 16))
    assert (report["weekly_record_count"], report["historical_record_count"]) == (0, 1)
    assert report["historical"]["groups"][0]["symbol"] == "2330.TWSE"
    rejected, = report["rejected_weekly_batches"]
    assert rejected["file"] == "2026-W46/breakout_v1.jsonl" and rejected["reason"] == "outside_weekly_capture_window"
    assert "拒收週快照" in render_markdown(report) and "outside_weekly_capture_window" in render_markdown(report)


@pytest.mark.parametrize("fault,reason", [
    ("valid", None), ("truncated", "weekly_batch_digest_mismatch"),
    ("missing", "bound_weekly_file_missing"), ("wrong_batch", "weekly_formal_batch_mismatch"),
    ("wrong_strategy", "weekly_formal_batch_mismatch"), ("wrong_week", "weekly_formal_batch_mismatch"),
    ("unselected_signal", "weekly_signal_not_in_formal_selection"), ("renamed", "weekly_file_unbound"),
])
def test_new_formal_batch_pins_complete_weekly_decisions(tmp_path, fault, reason):
    user = _data_dir(tmp_path)
    batch = formal()
    path = user / "weekly_selection" / "2026-W46" / "breakout_v1.jsonl"
    path.parent.mkdir(parents=True)
    records = [_ledger_record(), _ledger_record(symbol="6488.TPEX")]
    for row in records:
        row.update(formal_batch_id=batch["snapshot_id"], decision="no_signal", reason="data_incomplete")
        row["source_strategy"].update(version="v1", rule_version="breakout_v1")
        if fault == "wrong_batch":
            row["formal_batch_id"] = "other-batch"
        if fault == "wrong_strategy":
            row["source_strategy"]["version"] = "other-version"
        if fault == "wrong_week":
            row["week"] = "2026-W45"
        if fault == "unselected_signal" and row["symbol"] == "6488.TPEX":
            row.update(decision="signal", reason="selected_at_week_close")
        row.pop("record_sha256")
        row["record_sha256"] = canonical_hash(row)
    content = [canonical_json(row) + "\n" for row in records]
    path.write_bytes("".join(content).encode("utf8"))
    batch["weekly_records_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (user / "taiwan_selection_snapshots.json").write_text(json.dumps([batch]), encoding="utf8")
    if fault == "truncated":
        path.write_bytes(content[0].encode("utf8"))  # The remaining row still passes its own SHA.
    if fault == "missing":
        path.unlink()
    if fault == "renamed":
        path.rename(path.with_name("other_strategy.jsonl"))
    report = generate_report(tmp_path, date(2026, 11, 16))
    assert report["historical_record_count"] == 0 and report["historical"]["groups"] == []
    if reason is None:
        assert report["weekly_record_count"] == 2 and report["rejected_weekly_batches"] == []
        assert report["weekly"]["strategies"][0]["no_signal_count"] == 2 and report["weekly"]["groups"] == []
    else:
        assert report["weekly_record_count"] == 0 and report["weekly"]["groups"] == []
        assert report["skipped_batches"]["bound_weekly_records_unavailable"] == 1
        assert reason in [r["reason"] for r in report["rejected_weekly_batches"]]
        assert reason in render_markdown(report)
        if fault == "renamed":
            assert {r["reason"] for r in report["rejected_weekly_batches"]} == {"weekly_file_unbound", "bound_weekly_file_missing"}


@pytest.mark.parametrize("capture_error", ["weekly_unbound_ledger_exists", "future_snapshot_date"])
def test_failed_weekly_capture_cannot_be_scored_from_daily_or_conflicting_file(tmp_path, capture_error):
    user = _data_dir(tmp_path)
    batch = {**formal(), "weekly_capture_error": capture_error}
    (user / "taiwan_selection_snapshots.json").write_text(json.dumps([batch]), encoding="utf8")
    path = user / "weekly_selection" / "2026-W46" / "breakout_v1.jsonl"
    if capture_error == "weekly_unbound_ledger_exists":
        row = _ledger_record("2026-11-13T16:00:00+08:00")
        row.update(decision="no_signal", formal_batch_id=None, reason="data_incomplete")
        row.pop("record_sha256")
        row["record_sha256"] = canonical_hash(row)
        path.parent.mkdir(parents=True)
        original = (canonical_json(row) + "\n").encode("utf8")
        path.write_bytes(original)
    report = generate_report(tmp_path, date(2026, 11, 16))
    assert report["weekly_record_count"] == report["historical_record_count"] == 0
    assert report["weekly"]["missed"] == report["historical"]["missed"] == []
    assert report["skipped_batches"]["bound_weekly_records_unavailable"] == 1
    assert report["rejected_weekly_batches"][0]["reason"] == capture_error
    assert capture_error in render_markdown(report)
    if capture_error == "weekly_unbound_ledger_exists":
        assert path.read_bytes() == original


def test_imported_short_week_uses_verified_holidays_for_capture_deadline(tmp_path):
    user = _data_dir(tmp_path)
    census = ObservedUniverseStore(tmp_path / "taiwan" / "observed_universe")
    thursday = date(2026, 11, 12)
    # Replace this fixture's normal Friday session with an explicitly closed day.
    friday_path = tmp_path / "taiwan" / "daily" / "date=2026-11-13" / "part.parquet"
    thursday_path = friday_path.parent.parent / "date=2026-11-12" / "part.parquet"
    thursday_path.parent.mkdir()
    pl.read_parquet(friday_path, hive_partitioning=False).with_columns(pl.lit(thursday).alias("date")).write_parquet(thursday_path)
    friday_path.unlink()
    for market in ("TWSE", "TPEX"):
        for closure in (date(2026, 11, 13), date(2026, 11, 16)):
            census.write(market, closure, [], confirmed_non_trading_source="fixture:exchange_calendar")
    batch = formal(thursday.isoformat(), next_day="2026-11-17")
    for second, kept in (("08:59:59", 1), ("09:00:00", 0)):
        row = _ledger_record(f"2026-11-17T{second}+08:00", day=thursday.isoformat())
        _save_bound_fixture(user, [row], batch)
        report = generate_report(tmp_path, date(2026, 11, 17))
        assert report["weekly_record_count"] == kept
        assert len(report["rejected_weekly_batches"]) == 1 - kept


def test_zero_pick_formal_strategy_is_visible_with_small_sample_warning(tmp_path):
    user = _data_dir(tmp_path)
    empty = formal()
    empty.update(selected_symbols=[], items=[])
    (user / "taiwan_selection_snapshots.json").write_text(json.dumps([empty]), encoding="utf8")
    report = generate_report(tmp_path, date(2026, 11, 16))
    stats, = report["historical"]["strategies"]
    assert report["historical_record_count"] == stats["group_count"] == stats["completed_count"] == 0
    assert stats["mean_return_pct"] is None and stats["missed_count"] is None
    assert "樣本不足，僅供參考" in render_markdown(report)
    empty["strategy_id"], empty["strategy_name"] = "trend_v1", "趨勢"
    empty["snapshot_id"] = "empty-legacy"
    _save_bound_fixture(user, [_ledger_record()], formal(), legacy=[empty])
    combined = generate_report(tmp_path, date(2026, 11, 16))["combined"]["strategies"]
    assert [(row["strategy_id"], row["raw_signal_count"]) for row in combined] == [("breakout_v1", 1), ("trend_v1", 0)]


def test_backdated_midweek_output_cannot_create_dropped_stock_or_missed(tmp_path):
    user = _data_dir(tmp_path)
    invalid = formal("2026-11-11", locked="2026-11-13T16:50:00+08:00", next_day="2026-11-16")
    invalid["selected_symbols"] = ["6488.TPEX"]
    invalid["items"][0]["symbol"] = "6488.TPEX"
    (user / "taiwan_selection_snapshots.json").write_text(json.dumps([invalid, formal()]), encoding="utf8")
    report = generate_report(tmp_path, date(2026, 11, 16))
    assert report["historical_drops"] == [] and report["historical"]["missed"] == []
    assert report["historical_batches"][0]["prior_dates"] == []


def test_legacy_drops_use_latest_saved_selection_day_and_week_close():
    earlier = [formal(day="2026-11-11", next_day="2026-11-12"), formal(day="2026-11-12", next_day="2026-11-13")]
    for batch in earlier:
        batch["selected_symbols"] = ["6488.TPEX"]
        batch["items"][0]["symbol"] = "6488.TPEX"
    cutoff = datetime(2026, 11, 16, tzinfo=TAIPEI_TZ)
    evaluator = SimpleNamespace(week_close=lambda *_: date(2026, 11, 13), official_close=lambda *_: 102.)
    records, admitted, _ = _legacy_records([SelectionSnapshot(**b) for b in [*earlier, formal()]],
                                            cutoff, evaluator, TaiwanTradingCalendar(), None, set())
    drop, = [r for r in records if r["decision"] == "no_signal"]
    assert (drop["symbol"], drop["reference_close"], drop["reason"]) == ("6488.TPEX", 102., "dropped_before_week_close")
    assert drop["last_selected_date"] == "2026-11-12"
    assert admitted[0]["prior_dates"] == ["2026-11-11", "2026-11-12"]


def test_markdown_includes_missed_details():
    row = {"source_strategy": {"id": "trend_v1", "name": "趨勢"}, "decision": "no_signal",
           "pattern_tag": "trend", "week": "2026-W46", "last_trading_day": "2026-11-13",
           "reference_close": 100, "symbol": "2330.TWSE", "reason": "dropped_before_week_close"}
    def evaluate(*_):
        return {4: {"status": "completed", "return_pct": 8.5}}
    stats = build_weekly_review([row], evaluate)
    md = render_markdown({"as_of": "2026-12-11", "latest_daily_partition": "2026-12-11",
                          "parameters": {"max_gap_weeks": 2, "neutral_pct": 1., "missed_pct": 8.},
                          "weekly": stats, "weekly_record_count": 1, "historical": build_weekly_review([], evaluate),
                          "historical_record_count": 0, "historical_batches": []})
    assert "| 2330.TWSE | trend_v1 | 2026-11-13 | 8.50 |" in md


def test_latest_weekend_morning_report_includes_overnight_lock_only_after_observation(tmp_path, monkeypatch):
    user = _data_dir(tmp_path)
    stamp = "2026-11-14T07:15:00+08:00"
    _save_bound_fixture(user, [_ledger_record(stamp)], formal(locked=stamp))
    monkeypatch.setattr("app.taiwan.weekly_review_report.taipei_now", lambda: datetime(2026, 11, 14, 8, tzinfo=TAIPEI_TZ))
    output = tmp_path / "latest.json"
    monkeypatch.setattr("sys.argv", ["weekly_review_report", "--data-dir", str(tmp_path), "--format", "json", "--output", str(output)])
    main()
    latest = json.loads(output.read_text(encoding="utf8"))
    assert latest["weekly_record_count"] == 1 and latest["latest_daily_partition"] == "2026-11-13"
    assert generate_report(tmp_path, date(2026, 11, 14))["weekly_record_count"] == 1
    assert generate_report(tmp_path, date(2026, 11, 13))["weekly_record_count"] == 0


def test_group_shows_first_sealed_execution_open_and_keeps_friday_return_base(tmp_path):
    user = _data_dir(tmp_path)
    row = _ledger_record()
    _save_bound_fixture(user, [row], formal())
    observation = {"record_sha256": row["record_sha256"], "session": "2026-11-16", "symbol": row["symbol"],
                   "executable_reference_open": 101.5, "status": "available", "observed_at": "2026-11-16T17:00:00+08:00"}
    observation["observation_sha256"] = canonical_hash(observation)
    companion = user / "weekly_selection" / "2026-W46" / "breakout_v1.opens.jsonl"
    companion.write_text(canonical_json(observation) + "\n", encoding="utf8")
    friday = generate_report(tmp_path, date(2026, 11, 13))
    group, = friday["weekly"]["groups"]
    assert group["execution_open_status"] == "pending" and group["executable_reference_open"] is None
    monday = generate_report(tmp_path, date(2026, 11, 16))
    group, = monday["weekly"]["groups"]
    assert (group["reference_close"], group["executable_reference_open"], group["execution_open_status"]) == (100, 101.5, "available")
    assert "101.50" in render_markdown(monday)
    assert render_markdown(json.loads(json.dumps(monday))) == render_markdown(monday)
    companion.write_text('{"truncated":', encoding="utf8")
    damaged = generate_report(tmp_path, date(2026, 11, 16))
    group, = damaged["weekly"]["groups"]
    assert group["execution_open_status"] == "invalid" and group["executable_reference_open"] is None
    assert damaged["weekly_record_count"] == 1 and damaged["rejected_weekly_batches"] == []


def test_week_with_sessions_but_no_ledger_is_listed_as_missing(tmp_path):
    user = _data_dir(tmp_path)
    _save_bound_fixture(user, [_ledger_record()], formal())
    # The ledger is written on the week's last session, so W47 is not due while it can still trade.
    assert generate_report(tmp_path, date(2026, 11, 20))["missing_weeks"] == []
    # W47 has weekdays and the strategy is known, but nothing was captured that week.
    report = generate_report(tmp_path, date(2026, 11, 21))
    assert report["weekly_record_count"] == 1
    missing, = report["missing_weeks"]
    assert missing["week"] == "2026-W47" and missing["strategies"] == ["breakout_v1"]
    assert missing["reasons"]["breakout_v1"] == ["weekly_ledger_missing"]
    markdown = render_markdown(report)
    assert "## 缺少週快照的週別" in markdown and "2026-W47" in markdown


def test_rejected_ledger_week_is_missing_with_its_reason(tmp_path):
    user = _data_dir(tmp_path)
    batch = formal()
    _save_bound_fixture(user, [_ledger_record()], batch)
    (user / "weekly_selection" / "2026-W46" / "breakout_v1.jsonl").write_bytes(b"tampered")
    report = generate_report(tmp_path, date(2026, 11, 16))
    week46 = next(m for m in report["missing_weeks"] if m["week"] == "2026-W46")
    assert week46["reasons"]["breakout_v1"] == ["weekly_batch_digest_mismatch"]


def test_weekly_report_endpoint_returns_report_and_markdown(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.taiwan import router

    _data_dir(tmp_path)
    app = FastAPI()
    app.include_router(router)
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    with TestClient(app) as client:
        body = client.get("/api/taiwan/selection-review/weekly-report").json()
        assert set(body) == {"report", "markdown"} and "missing_weeks" in body["report"]
        assert body["markdown"].startswith("# 台股每週選股復盤")
    empty = FastAPI()
    empty.include_router(router)
    empty.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path / "nope"))
    with TestClient(empty) as client:
        assert client.get("/api/taiwan/selection-review/weekly-report").status_code == 409

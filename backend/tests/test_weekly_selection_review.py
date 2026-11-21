"""Small official-format Parquet partitions; all clocks and market sessions are local fixtures."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from app.taiwan import weekly_selection_review as weekly
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.realtime.calendar import TAIPEI_TZ, TaiwanTradingCalendar
from app.taiwan.selection_review_models import SelectionSnapshot, SelectionSnapshotItem

DAY = date(2026, 11, 19)  # Thursday, with an explicitly closed Friday in this fixture
NEXT = date(2026, 11, 23)
STRATEGY = "oversold_rebound_v1"
SYMBOLS = ("2330.TWSE", "6488.TPEX", "2317.TWSE", "2454.TWSE", "2881.TWSE", "2603.TWSE")


def batch(day, picked, **kwargs):
    return SelectionSnapshot(
        snapshot_id=f"forward_{STRATEGY}_{day:%Y%m%d}", created_at=f"{day}T08:00:00Z",
        record_type="forward_batch", strategy_id=STRATEGY, strategy_version="v1", rule_version=STRATEGY,
        strategy_name="超跌反彈 v1", market_context_summary="自由文字不得寫進週快照",
        as_of_date=day.isoformat(), source_data_date=day.isoformat(), locked_at=f"{day}T16:00:00+08:00",
        target_trade_date=(NEXT if day == DAY else day + timedelta(days=1)).isoformat(),
        selected_symbols=picked, items=[
            SelectionSnapshotItem(symbol=s, name="公司", rank=i, price=11, match_reasons=["非結構化說明"])
            for i, s in enumerate(picked, 1)
        ], **kwargs,
    )


def prices(day, symbols=SYMBOLS, close=11, source="twse_tpex_official_snapshot"):
    return pl.DataFrame([{
        "symbol": symbol, "date": day, "open": close - 1.0, "high": close + 1.0,
        "low": close - 2.0, "close": float(close), "volume": 100.0, "amount": 1000.0,
        "quote_ts": 1, "source": source, "status": "official",
    } for symbol in symbols])


@pytest.fixture
def setup(tmp_path, monkeypatch):
    daily = TaiwanDailyStore(tmp_path / "daily")
    inst = TaiwanInstitutionalStore(tmp_path / "institutional")
    daily.write_batch(prices(DAY - timedelta(days=2), close=8))
    daily.write_batch(prices(DAY))
    daily.write_batch(prices(NEXT, close=101))
    inst.write_batch(pl.DataFrame([
        {"symbol": s, "date": DAY, "trade_date": DAY, "source": "tpex:daily_trade" if s.endswith("TPEX") else "twse:t86",
         "status": "official", "foreign_net": 0 if i != 2 else None, "investment_trust_net": -7, "dealer_net": 4}
        for i, s in enumerate(SYMBOLS)
    ]))
    calendar = TaiwanTradingCalendar(known_holidays={DAY + timedelta(days=1)}, known_trading_days={DAY, NEXT})
    evidence = weekly.WeeklyCandidates(symbols=set(SYMBOLS[:-1]), eligible={SYMBOLS[0], SYMBOLS[1], SYMBOLS[2]})
    evidence.facts = {s: {"date": DAY, "institutional_date": DAY, "amount": 1000,
                          "ma5": 10, "rsi_14": 30, "change_pct": .01, "revenue_latest_period": "2026-10",
                          "name": "自由文字不得寫進週快照"} for s in SYMBOLS[:-1]}
    history = [batch(DAY - timedelta(days=2), [SYMBOLS[-1]]),
               batch(DAY - timedelta(days=1), [SYMBOLS[-1], SYMBOLS[3]]),
               batch(DAY, [SYMBOLS[0], SYMBOLS[2]])]
    clock = [datetime(2026, 11, 19, 17, tzinfo=TAIPEI_TZ)]
    monkeypatch.setattr(weekly, "taipei_now", lambda: clock[0])
    args = (tmp_path / "weeks", DAY, STRATEGY, evidence, history, daily, inst, calendar)
    return SimpleNamespace(args=args, clock=clock, evidence=evidence, history=history, daily=daily, calendar=calendar)


def test_published_markets_with_one_invalid_row_allow_weekly_lock(setup, tmp_path, monkeypatch):
    from app.jobs.daily_pipeline import _lock_forward_selections
    from app.taiwan.daily_update import TaiwanDailyUpdateService
    from app.taiwan.institutional_margin_refresh import TaiwanInstitutionalRefreshService
    from app.taiwan.observed_universe import ObservedUniverseStore

    store = TaiwanInstitutionalStore(tmp_path / "fresh-institutional")
    census = ObservedUniverseStore(tmp_path / "sessions")
    refresh = TaiwanInstitutionalRefreshService(store=store, evidence_store=census)
    twse = {"date": str(DAY), "data": [[code, "fixture", *(["0"] * 17)] for code in ("2330", "2317")]}
    twse["data"][1][4] = "--"
    tpex = {"date": str(DAY), "aaData": [["6488", "fixture", *(["0"] * 22)]]}
    monkeypatch.setattr(refresh, "_fetch_json", lambda url: tpex if "tpex" in url else twse)
    # Daily and margin have already published this session. Only the institutional feed is fetched.
    published = SimpleNamespace(available_dates=lambda: [DAY], partial_dates=set, has_verified_publication=lambda d: d == DAY)
    service = TaiwanDailyUpdateService(daily_store=published, margin_store=published, inst_store=store,
                                       inst_service=refresh, calendar=setup.calendar, evidence_store=census)
    result = service.run_update(target_date=DAY)
    assert result.daily.status == result.institutional.status == result.overall_status == "success"
    assert [r["symbol"] for r in result.institutional.rejected_rows] == ["2317.TWSE"]
    captured = []

    def freeze_fixture_batch():
        captured.append(weekly.capture_week(*setup.args[:6], store, setup.calendar))

    monkeypatch.setattr("app.taiwan.selection_review_service.lock_daily_forward_batches", freeze_fixture_batch)
    _lock_forward_selections(result)
    assert len(captured) == 1
    records = {r["symbol"]: r for r in weekly.read_records(captured[0])}
    assert records["2330.TWSE"]["decision"] == "signal"
    bad = records["2317.TWSE"]
    # This strategy does not read institutional flow: the rejected row stays null and the pick stays a signal.
    assert (bad["decision"], bad["reason"]) == ("signal", "selected_at_week_close")
    assert bad["snapshot"]["institutional_net_shares"] == dict.fromkeys(weekly.FLOW)


def test_freeze_all_candidates_short_week_and_dropped_history(setup):
    path = weekly.capture_week(*setup.args)
    rows = {r["symbol"]: r for r in weekly.read_records(path)}
    assert set(rows) == set(SYMBOLS)
    signal, watch, missing, dropped, filtered, gone = [rows[s] for s in SYMBOLS]
    assert signal["decision"] == "signal"
    assert signal["source_strategy"]["name"] == "超跌反彈 v1"
    assert signal["signal_basis"] == "daily_strategy_weekly_snapshot"
    assert signal["week"] == "2026-W47"
    assert signal["snapshot"]["week_bar"] == {"first_session": "2026-11-17", "last_session": "2026-11-19",
                                               "open": 7, "high": 12, "low": 6, "close": 11, "volume": 200}
    assert signal["reference_close"] == 11
    assert signal["executable_reference_open"] is None
    assert signal["snapshot"]["institutional_net_shares"]["foreign_net"] == 0
    assert (watch["decision"], watch["reason"]) == ("watch", "outside_formal_selection_limit")
    # A formal pick of a strategy that never reads institutional flow keeps its signal;
    # the missing same-day net stays null and is not listed as a missing core field.
    assert (missing["decision"], missing["reason"]) == ("signal", "selected_at_week_close")
    assert missing["snapshot"]["institutional_net_shares"]["foreign_net"] is None
    assert missing["snapshot"]["missing_fields"] == []
    assert (filtered["decision"], filtered["reason"]) == ("no_signal", "daily_conditions_not_met")
    for row in (dropped, gone):
        assert row["decision"] == "no_signal"
        assert row["reason"] == "dropped_before_week_close"
        assert row["last_selected_date"] == "2026-11-18"
    assert "自由文字" not in weekly._read_ledger(path).decode("utf8") and "非結構化" not in weekly._read_ledger(path).decode("utf8")
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        weekly.capture_week(*setup.args)
    assert path.read_bytes() == original


def test_same_day_institutional_gap_only_counts_for_strategies_that_read_flow(setup):
    root, day, _, evidence, _, daily, inst, calendar = setup.args
    for strategy, listed in (("oversold_rebound_v1", False), ("foreign_trend_v1", True)):
        path = weekly.capture_week(root, day, strategy, evidence, [], daily, inst, calendar)
        gap = next(r for r in weekly.read_records(path) if r["symbol"] == SYMBOLS[2])["snapshot"]
        assert gap["institutional_net_shares"]["foreign_net"] is None
        assert ("foreign_net" in gap["missing_fields"]) is listed


@pytest.mark.parametrize("stamp, error", [
    ("2026-11-19T13:29:59+08:00", "outside_weekly_capture_window"),
    ("2026-11-23T09:00:00+08:00", "outside_weekly_capture_window"),
])
def test_write_window(setup, stamp, error):
    setup.clock[0] = datetime.fromisoformat(stamp)
    with pytest.raises(ValueError, match=error):
        weekly.capture_week(*setup.args)
    assert not setup.args[0].exists()


def test_unknown_friday_is_not_a_holiday(setup):
    with pytest.raises(ValueError, match="week_not_closed"):
        weekly.capture_week(*setup.args[:-1], TaiwanTradingCalendar())


def test_future_facts_and_corruption_are_rejected(setup):
    setup.evidence.facts[SYMBOLS[0]]["institutional_date"] = NEXT
    with pytest.raises(ValueError, match="future_snapshot_date"):
        weekly.capture_week(*setup.args)
    assert not setup.args[0].exists()
    setup.evidence.facts[SYMBOLS[0]]["institutional_date"] = DAY
    setup.evidence.revenue_observed_at = "2026-11-19T16:30:00Z"  # next date in Taipei
    with pytest.raises(ValueError, match="future_snapshot_date"):
        weekly.capture_week(*setup.args)
    setup.evidence.revenue_observed_at = None
    path = weekly.capture_week(*setup.args)
    path.write_text(weekly._read_ledger(path).decode("utf8").replace('"reference_close":11', '"reference_close":12'), "utf8")
    with pytest.raises(ValueError, match="digest_mismatch"):
        weekly.read_records(path)


def test_actual_next_open_is_observed_separately_and_never_rewrites_decision(setup):
    import json

    path = weekly.capture_week(*setup.args)
    original = path.read_bytes()
    args = (setup.args[0], setup.daily, setup.calendar, setup.history)
    companion = path.with_suffix(".opens.jsonl")
    setup.clock[0] = datetime(2026, 11, 23, 16, tzinfo=TAIPEI_TZ)
    weekly.record_execution_opens(*args)
    assert not companion.exists()  # No formal batch has bound this file yet.
    setup.history[-1].weekly_records_sha256 = weekly.file_sha256(path)
    setup.clock[0] = datetime(2026, 11, 19, 17, tzinfo=TAIPEI_TZ)
    weekly.record_execution_opens(*args)
    assert not companion.exists()
    setup.clock[0] = datetime(2026, 11, 23, 16, tzinfo=TAIPEI_TZ)
    weekly.record_execution_opens(*args)
    values = [json.loads(line) for line in weekly._read_ledger(companion).decode("utf8").splitlines()]
    assert [v["record_sha256"] for v in values] == [r["record_sha256"] for r in weekly.read_records(path)]
    assert all(v["executable_reference_open"] == 100 and v["session"] == "2026-11-23" for v in values)
    saved = companion.read_bytes()
    weekly.record_execution_opens(*args)
    assert companion.read_bytes() == saved and path.read_bytes() == original


def test_next_open_waits_for_missing_market_and_requires_a_trade(setup, tmp_path):
    path = weekly.capture_week(*setup.args)
    setup.history[-1].weekly_records_sha256 = weekly.file_sha256(path)
    delayed = TaiwanDailyStore(tmp_path / "next-morning")
    setup.clock[0] = datetime(2026, 11, 23, 17, tzinfo=TAIPEI_TZ)
    args = (setup.args[0], delayed, setup.calendar, setup.history)
    delayed.write_batch(prices(NEXT, symbols=[s for s in SYMBOLS if s.endswith(".TWSE")], close=101))
    weekly.record_execution_opens(*args)
    assert not path.with_suffix(".opens.jsonl").exists()
    delayed.write_batch(prices(NEXT, symbols=["6488.TPEX"], close=88))
    delayed.write_batch(prices(NEXT, symbols=["2330.TWSE"], close=101).with_columns(pl.lit(0.).alias("volume")))
    weekly.record_execution_opens(*args)
    records = weekly.read_records(path)
    assert not weekly.read_execution_opens(path, records, NEXT, setup.clock[0] - timedelta(minutes=1))
    events = weekly.read_execution_opens(path, records, NEXT, setup.clock[0])
    by_symbol = {e["symbol"]: e for e in events.values()}
    assert by_symbol["6488.TPEX"]["executable_reference_open"] == 87
    assert by_symbol["2330.TWSE"]["executable_reference_open"] is None
    assert by_symbol["2330.TWSE"]["status"] == "official_open_unavailable"


@pytest.mark.parametrize("otc_code,opening", [("6488", 91), ("8069", None)])
def test_next_open_uses_published_census_for_missing_market(setup, tmp_path, otc_code, opening):
    from app.taiwan.observed_universe import TPEX_SOURCE, ObservedUniverseStore

    path = weekly.capture_week(*setup.args)
    setup.history[-1].weekly_records_sha256 = weekly.file_sha256(path)
    daily = TaiwanDailyStore(tmp_path / "one-market")
    daily.write_batch(prices(NEXT, symbols=[s for s in SYMBOLS if s.endswith(".TWSE")], close=101))
    census = ObservedUniverseStore(tmp_path / "census")
    census.write("TPEX", NEXT, [{"raw_code": otc_code, "exchange": "TPEX", "date": NEXT, "source": TPEX_SOURCE,
                                 "observed": True, "open": 91., "volume": 200.}])
    setup.clock[0] = datetime(2026, 11, 23, 17, tzinfo=TAIPEI_TZ)
    weekly.record_execution_opens(setup.args[0], daily, setup.calendar, setup.history, census)
    records = weekly.read_records(path)
    events = weekly.read_execution_opens(path, records, NEXT, setup.clock[0])
    event = next(e for e in events.values() if e["symbol"] == "6488.TPEX")
    assert event["executable_reference_open"] == opening
    assert event["status"] == ("available" if opening else "official_open_unavailable")


def test_legacy_open_reader_checks_availability_time_and_complete_membership(setup):
    import json
    import os

    path = weekly.capture_week(*setup.args)
    setup.history[-1].weekly_records_sha256 = weekly.file_sha256(path)
    setup.clock[0] = datetime(2026, 11, 23, 17, tzinfo=TAIPEI_TZ)
    weekly.record_execution_opens(setup.args[0], setup.daily, setup.calendar, setup.history)
    records = weekly.read_records(path)
    companion = path.with_suffix(".opens.jsonl")
    lines = []
    for line in weekly._read_ledger(companion).decode("utf8").splitlines():
        event = json.loads(line)
        del event["observation_sha256"], event["observed_at"]
        event["observation_sha256"] = weekly.canonical_hash(event)
        lines.append(weekly.canonical_json(event))
    companion.write_text("\n".join(lines) + "\n", encoding="utf8")
    stamp = setup.clock[0].timestamp()
    os.utime(companion, (stamp, stamp))
    assert not weekly.read_execution_opens(path, records, NEXT, setup.clock[0] - timedelta(minutes=1))
    assert len(weekly.read_execution_opens(path, records, NEXT, setup.clock[0])) == len(records)
    companion.write_text("\n".join(lines[:-1]) + "\n", encoding="utf8")
    with pytest.raises(ValueError, match="execution_open_batch_incomplete"):
        weekly.read_execution_opens(path, records, NEXT, setup.clock[0])


def test_no_saved_daily_history_and_unofficial_price(setup):
    setup.history[:] = [setup.history[-1]]
    setup.daily.write_batch(prices(DAY, symbols=[SYMBOLS[0]], source="other_provider"))
    rows = {r["symbol"]: r for r in weekly.read_records(weekly.capture_week(*setup.args))}
    assert SYMBOLS[-1] not in rows
    assert rows[SYMBOLS[0]]["decision"] == "no_signal"
    assert rows[SYMBOLS[0]]["reference_close"] is None
    assert rows[SYMBOLS[3]]["snapshot"]["prior_formal_output_dates"] == []
    assert rows[SYMBOLS[3]]["reason"] == "daily_conditions_not_met"


def test_no_formal_batch_records_missing_for_entire_candidate_universe(setup):
    setup.history.clear()
    rows = weekly.read_records(weekly.capture_week(*setup.args))
    assert len(rows) == len(setup.evidence.symbols)
    assert all(row["reason"] == "data_incomplete" and row["decision"] == "no_signal" for row in rows)


def test_deadline_crossed_during_write_does_not_publish(setup, monkeypatch):
    times = iter([setup.clock[0], datetime(2026, 11, 23, 9, tzinfo=TAIPEI_TZ)])
    monkeypatch.setattr(weekly, "taipei_now", lambda: next(times))
    with pytest.raises(ValueError, match="outside_weekly_capture_window"):
        weekly.capture_week(*setup.args)
    assert not list(setup.args[0].rglob("*.jsonl"))


def test_week_bar_does_not_gate_formal_daily_result(setup):
    setup.daily.write_batch(prices(DAY).with_columns(pl.lit(None, dtype=pl.Float64).alias("high")))
    row = next(r for r in weekly.read_records(weekly.capture_week(*setup.args)) if r["symbol"] == SYMBOLS[0])
    assert row["snapshot"]["week_bar"]["high"] is None
    assert row["decision"] == "signal"


@pytest.mark.parametrize("closed", [False, True])
def test_five_day_flow_never_replaces_an_unobserved_session_with_an_older_day(setup, closed):
    symbol, strategy = SYMBOLS[0], "foreign_trend_v1"
    wednesday = DAY - timedelta(days=1)
    if closed:
        setup.calendar.add_holiday(wednesday)
    else:
        setup.calendar.add_trading_day(wednesday)
    for offset in (7, 6, 3, 2, 0):
        session = DAY - timedelta(days=offset)
        setup.daily.write_batch(prices(session, [symbol]))
        setup.args[6].write_batch(pl.DataFrame([{
            "symbol": symbol, "source": "twse:t86", "status": "official", "date": session, "trade_date": session,
            "foreign_net": 1000, "investment_trust_net": 100, "dealer_net": 0,
        }]))
    formal = batch(DAY, [symbol]).model_copy(update={"strategy_id": strategy})
    observations = weekly.WeeklyCandidates(symbols={symbol}, eligible={symbol})
    observations.facts[symbol] = {
        "date": DAY, "institutional_date": DAY, "amount": 100000, "ma20": 9, "ma60": 8,
        "momentum_20d": 0.1, "foreign_net_5d": 5000,
    }
    path = weekly.capture_week(setup.args[0], DAY, strategy, observations, [formal], *setup.args[5:])
    record, = weekly.read_records(path)
    assert record["snapshot"]["institutional_net_shares"]["foreign_net"] == 1000
    assert record["snapshot"]["daily_inputs"]["foreign_net_5d"] == (5000 if closed else None)
    assert record["decision"] == ("signal" if closed else "no_signal")
    assert record["reason"] == ("selected_at_week_close" if closed else "data_incomplete")


@pytest.mark.parametrize("bad", ["missing_earlier", "discrepancy_today", "mismatched_totals", "complete"])
def test_week_capture_validates_each_source_of_institutional_factors(setup, bad):
    strategy = "institutional_momentum_v1"
    symbol = SYMBOLS[0]
    formal = batch(DAY, [symbol]).model_copy(update={"strategy_id": strategy})
    evidence = weekly.WeeklyCandidates(symbols={symbol}, eligible={symbol})
    evidence.facts[symbol] = {"date": DAY, "institutional_date": DAY, "amount": 5000, "ma20": 10,
                              "foreign_net_5d": 400000 if bad == "missing_earlier" else 500000,
                              "investment_trust_net_5d": 0, "institutional_flow_ratio_5d": 2}
    inst = setup.args[6]
    for day_offset in (6, 3, 2, 1, 0):
        d = DAY - timedelta(days=day_offset)
        setup.daily.write_batch(prices(d, [symbol]))
        inst.write_batch(pl.DataFrame([{"symbol": symbol, "source": "twse:t86", "status": "official",
            "date": d, "trade_date": d, "foreign_net": None if bad == "missing_earlier" and day_offset == 2 else 100000,
            "investment_trust_net": 0, "dealer_net": 0, "has_discrepancy": bad == "discrepancy_today" and d == DAY,
            "official_net": 100000, "computed_net": 123 if bad == "mismatched_totals" and d == DAY else 100000}]))
    row, = weekly.read_records(weekly.capture_week(setup.args[0], DAY, strategy, evidence, [formal],
                                                   setup.daily, inst, setup.calendar))
    if bad == "complete":
        assert row["decision"] == "signal"
        assert row["snapshot"]["daily_inputs"]["foreign_net_5d"] == 500000
    else:
        assert (row["decision"], row["reason"]) == ("no_signal", "data_incomplete")
        assert row["snapshot"]["daily_inputs"]["foreign_net_5d"] is None
        assert row["snapshot"]["daily_inputs"]["institutional_flow_ratio_5d"] is None
        if bad != "missing_earlier":
            assert "institutional_discrepancy" in row["snapshot"]["missing_fields"]
            assert row["snapshot"]["institutional_net_shares"]["foreign_net"] is None


def test_formal_entry_freezes_original_daily_output(taiwan_data_env, monkeypatch):
    from app.taiwan import events_service, selection_review_service
    from app.taiwan.screener import TaiwanScreenerService

    day = taiwan_data_env["target"]
    now = datetime.combine(day, datetime.min.time(), TAIPEI_TZ) + timedelta(hours=17)
    monkeypatch.setattr(selection_review_service, "taipei_now", lambda: now)
    monkeypatch.setattr(weekly, "taipei_now", lambda: now)
    daily, inst = TaiwanDailyStore(), TaiwanInstitutionalStore()
    for i, session in enumerate(taiwan_data_env["trading_days"]):
        close = 81 if session == day else 100 - i
        daily.write_batch(prices(session, ["2330.TWSE"], close).with_columns(
            pl.lit(100_000_000.).alias("amount"), pl.lit(1_000_000.).alias("volume"),
        ))
    inst.write_batch(pl.DataFrame([{"symbol": "2330.TWSE", "date": day, "trade_date": day,
                                    "source": "twse:t86", "status": "official", "foreign_net": 1,
                                    "investment_trust_net": 0, "dealer_net": -1}]))
    calendar = TaiwanTradingCalendar(known_trading_days=set(taiwan_data_env["trading_days"]))
    svc = selection_review_service.TaiwanSelectionReviewService(daily_store=daily, calendar=calendar)
    screener = TaiwanScreenerService(daily_store=daily, institutional_store=inst, calendar=calendar)
    monkeypatch.setattr(screener.margin_store, "has_verified_publication", lambda d: d == day)
    monkeypatch.setattr(svc, "_action_coverage_evidence", lambda: {
        "start": day.isoformat(), "end": day.isoformat(), "events_sha256": "fixture", "saved_at": now.isoformat(),
    })
    monkeypatch.setattr(screener, "_quote_coverage_status", lambda *args: "verified")
    risk = SimpleNamespace(get_cached_regulatory_snapshot=lambda: ([], "available", day.isoformat()),
                           check_symbol_risk_status=lambda *args, **kw: {"is_disposition": False, "is_suspended": False})
    monkeypatch.setattr(events_service, "get_event_service", lambda: risk)
    batch = svc.lock_forward_batch(screener, STRATEGY)
    assert "2330.TWSE" in batch.selected_symbols
    path = svc.path.parent / "weekly_selection" / day.strftime("%G-W%V") / f"{STRATEGY}.jsonl"
    rows = weekly.read_records(path)
    chosen = next(r for r in rows if r["symbol"] == "2330.TWSE")
    assert (chosen["decision"], chosen["reference_close"], chosen["formal_batch_id"]) == ("signal", 81, batch.snapshot_id)
    assert chosen["snapshot"]["daily_inputs"]["rsi_14"] <= 35
    assert any(r["decision"] == "no_signal" for r in rows)


def test_ledger_is_gzip_on_disk_and_legacy_plain_text_reads_identically(tmp_path):
    import gzip
    import hashlib
    import json

    from app.taiwan.quant.live_contract import canonical_hash, canonical_json

    rows = []
    for i in range(200):
        record = {"symbol": f"{2300 + i}.TWSE", "snapshot": {"close": i, "note": "收盤"}}
        record["snapshot_sha256"] = canonical_hash(record["snapshot"])
        record["record_sha256"] = canonical_hash(record)
        rows.append(canonical_json(record))
    plain = ("\n".join(rows) + "\n").encode("utf8")

    packed = tmp_path / "2026-W48" / "s.jsonl"
    weekly._publish(packed, rows)
    raw = packed.read_bytes()
    assert raw[:2] == b"\x1f\x8b" and gzip.decompress(raw) == plain
    assert raw == gzip.compress(plain, mtime=0)
    assert len(raw) < len(plain)

    legacy = tmp_path / "2026-W47" / "s.jsonl"
    legacy.parent.mkdir()
    legacy.write_bytes(plain)

    expected = hashlib.sha256(plain).hexdigest()
    assert weekly.file_sha256(packed) == weekly.file_sha256(legacy) == expected
    assert weekly.read_records(packed) == weekly.read_records(legacy) == [json.loads(r) for r in rows]

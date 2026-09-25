# ruff: noqa: RUF001 -- fixtures mirror official Chinese payload text exactly.
"""OOS blocker convergence: trading-day evidence and historical subtype evidence.

Offline only. Payloads mirror the official responses recorded in the probes
(FMTQIK / inx month tables, ISIN registry pages, termination list).
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from app.taiwan.historical_classification import (
    HistoricalClassificationStore,
    classification_counts,
)
from app.taiwan.instrument_evidence import (
    Decision,
    InstrumentEvidenceStore,
    announcement_decision,
    decide,
    parse_isin_registry,
    parse_termination,
    resolve_industry_only_codes,
)
from app.taiwan.observed_universe import ObservedUniverseStore
from app.taiwan.trading_day_evidence import (
    MonthTableError,
    fetch_month_sessions,
    verify_empty_days,
)

ROW = {
    "date": date(2015, 2, 2), "raw_code": "2330", "exchange": "TWSE", "observed": True,
    "raw_name": "x", "raw_source_category": "x", "open": 1.0, "high": 1.0, "low": 1.0,
    "close": 1.0, "volume": 1.0, "amount": 1.0, "instrument_type": None,
    "instrument_type_status": "data_insufficient", "source": "t", "retrieved_at": "t",
}


def _twse_month(year: int, month: int, days: list[str]) -> dict:
    return {"stat": "OK", "date": f"{year:04d}{month:02d}01",
            "fields": ["日期", "成交股數"], "data": [[d, "1"] for d in days]}


def _tpex_month(year: int, month: int, days: list[str]) -> dict:
    return {"stat": "ok", "date": f"{year:04d}{month:02d}01",
            "tables": [{"fields": ["日期", "開市"], "data": [[d, "1"] for d in days]}]}


def _fetcher(payloads: dict[str, dict]):
    def fetch(url: str) -> dict:
        for key, payload in payloads.items():
            if key in url:
                return payload
        raise AssertionError(f"unexpected url {url}")
    return fetch


def _seed(store: ObservedUniverseStore, exchange: str, observed: list[date], empty: list[date]) -> None:
    for day in observed:
        store.write(exchange, day, [{**ROW, "date": day, "exchange": exchange}])
    for day in empty:
        store.write(exchange, day, [])


def _weekdays(first: date, last: date) -> list[date]:
    return [first + timedelta(days=n) for n in range((last - first).days + 1)
            if (first + timedelta(days=n)).weekday() < 5]


def _seed_span(store: ObservedUniverseStore, exchange: str, first: date, last: date,
               observed: list[date], skip: tuple[date, ...] = ()) -> None:
    """A partition for every weekday of the span: rows on ``observed``, empty otherwise."""
    for day in _weekdays(first, last):
        if day in skip:
            continue
        store.write(exchange, day,
                    [{**ROW, "date": day, "exchange": exchange}] if day in observed else [])


# ── Trading-day evidence ───────────────────────────────────────

def test_absent_weekdays_are_confirmed_only_before_the_last_published_session(tmp_path: Path) -> None:
    store = ObservedUniverseStore(tmp_path)
    _seed(store, "TWSE", [date(2015, 2, 2), date(2015, 2, 26)],
          [date(2015, 2, 16), date(2015, 2, 27)])
    fetch = _fetcher({"FMTQIK": _twse_month(2015, 2, ["104/02/02", "104/02/26"])})
    report = verify_empty_days(store, "TWSE", start=date(2015, 2, 1), end=date(2015, 2, 27), fetch=fetch)
    assert report["confirmed_non_trading"] == ["2015-02-16"]
    assert report["after_last_published_session"] == ["2015-02-27"]
    statuses = store.partition_statuses("TWSE")
    assert statuses[date(2015, 2, 16)] == "confirmed_non_trading"
    assert statuses[date(2015, 2, 27)] == "empty_unknown"
    assert store.day_evidence("TWSE", date(2015, 2, 16)).evidence_source.startswith("twse:FMTQIK")


def test_finished_month_is_complete_so_trailing_absence_is_confirmed(tmp_path: Path) -> None:
    store = ObservedUniverseStore(tmp_path)
    _seed(store, "TWSE", [date(2015, 2, 2), date(2015, 2, 26)], [date(2015, 2, 27)])
    fetch = _fetcher({"FMTQIK": _twse_month(2015, 2, ["104/02/02", "104/02/26"])})
    report = verify_empty_days(store, "TWSE", start=date(2015, 2, 1), end=date(2015, 3, 2), fetch=fetch)
    assert report["confirmed_non_trading"] == ["2015-02-27"]


def test_saturday_make_up_session_is_fetched_not_assumed_closed(tmp_path: Path) -> None:
    store = ObservedUniverseStore(tmp_path)
    _seed(store, "TWSE", [date(2016, 1, 29)], [])
    fetch = _fetcher({"FMTQIK": _twse_month(2016, 1, ["105/01/29", "105/01/30"])})
    saturday = date(2016, 1, 30)
    rows = {saturday: [{**ROW, "date": saturday}]}
    report = verify_empty_days(store, "TWSE", start=date(2016, 1, 1), end=date(2016, 1, 31),
                               fetch=fetch, census_rows=lambda day: rows.get(day, []))
    assert report["weekend_sessions_missing"] == ["2016-01-30"]
    assert report["weekend_sessions_added"] == ["2016-01-30"]
    assert store.partition_status("TWSE", saturday) == "observed"


def test_official_session_with_empty_census_is_never_a_holiday(tmp_path: Path) -> None:
    store = ObservedUniverseStore(tmp_path)
    _seed(store, "TPEX", [date(2015, 2, 2)], [date(2015, 2, 3)])
    fetch = _fetcher({"inx": _tpex_month(2015, 2, ["2015/02/02", "2015/02/03", "2015/02/04"])})
    report = verify_empty_days(store, "TPEX", start=date(2015, 2, 1), end=date(2015, 3, 1), fetch=fetch)
    assert report["empty_but_official_session"] == ["2015-02-03"]
    assert store.partition_status("TPEX", date(2015, 2, 3)) == "empty_unknown"
    recovered = verify_empty_days(
        store, "TPEX", start=date(2015, 2, 1), end=date(2015, 3, 1), fetch=fetch,
        census_rows=lambda day: [{**ROW, "date": day, "exchange": "TPEX"}])
    assert recovered["empty_session_recovered"] == ["2015-02-03"]
    assert store.partition_status("TPEX", date(2015, 2, 3)) == "observed"


def test_month_with_observed_disagreement_is_left_untouched(tmp_path: Path) -> None:
    store = ObservedUniverseStore(tmp_path)
    _seed(store, "TWSE", [date(2015, 2, 2), date(2015, 2, 5)], [date(2015, 2, 16)])
    fetch = _fetcher({"FMTQIK": _twse_month(2015, 2, ["104/02/02", "104/02/26"])})
    report = verify_empty_days(store, "TWSE", start=date(2015, 2, 1), end=date(2015, 3, 1), fetch=fetch)
    assert report["observed_conflicts"] == ["2015-02-05"]
    assert report["confirmed_non_trading"] == []
    assert store.partition_status("TWSE", date(2015, 2, 16)) == "empty_unknown"


def test_dry_run_writes_nothing_and_bad_month_is_reported(tmp_path: Path) -> None:
    store = ObservedUniverseStore(tmp_path)
    _seed(store, "TWSE", [date(2015, 2, 2), date(2015, 2, 26)],
          [date(2015, 2, 16), date(2015, 3, 30)])
    fetch = _fetcher({"date=20150201": _twse_month(2015, 2, ["104/02/02", "104/02/26"]),
                      "date=20150301": {"stat": "很抱歉，沒有符合條件的資料!"}})
    report = verify_empty_days(store, "TWSE", start=date(2015, 2, 1), end=date(2015, 3, 31),
                               fetch=fetch, apply=False)
    assert report["confirmed_non_trading"] == ["2015-02-16"]
    assert report["month_errors"] == ["2015-03:MonthTableError"]
    assert store.partition_status("TWSE", date(2015, 2, 16)) == "empty_unknown"


@pytest.mark.parametrize("payload", [
    {"stat": "OK", "date": "20150201", "fields": ["日期"], "data": []},
    {"stat": "OK", "date": "20150301", "fields": ["日期"], "data": [["104/03/02"]]},
    {"stat": "OK", "date": "20150201", "fields": ["日期"], "data": [["104/02/03"], ["104/02/02"]]},
])
def test_invalid_month_tables_fail_closed(payload: dict) -> None:
    with pytest.raises(MonthTableError):
        fetch_month_sessions("TWSE", 2015, 2, fetch=lambda _url: payload)


# ── Subtype evidence ───────────────────────────────────────────

def _isin_page(rows: list[tuple[str, ...]], title: str, sections: list[str] | None = None) -> bytes:
    cells = "".join(f"<tr><td>{'</td><td>'.join(row)}</td></tr>" for row in rows)
    return f"<h1>{title}</h1><table>{cells}</table>".encode("big5hkscs")


def test_isin_listed_parser_keeps_only_supported_sections() -> None:
    sep = "　"
    rows = [
        (" 股票 ",),
        (f"2330{sep}台積電", "TW0002330008", "1994/09/05", "上市", "半導體業", "ESVUFR", ""),
        (" 特別股 ",),
        (f"2881A{sep}富邦特", "TW0002881A00", "2016/05/31", "上市", "金融保險業", "EPNRAR", ""),
        (" 上市認購(售)權證 ",),
        (f"030079{sep}某權證", "TW25Z0300799", "2025/10/01", "上市", "", "RWSCCA", ""),
    ]
    frame = parse_isin_registry(_isin_page(rows, "本國上市證券國際證券辨識號碼一覽表"), "isin_listed")
    assert frame["code"].to_list() == ["2330", "2881A"]
    assert frame["listing_date"].to_list() == [date(1994, 9, 5), date(2016, 5, 31)]
    with pytest.raises(ValueError):
        parse_isin_registry(_isin_page(rows, "another page"), "isin_listed")


def test_termination_list_parser() -> None:
    frame = parse_termination({"status": "ok", "data": [["113/09/02", "中化", "1701"]]})
    assert frame.to_dicts() == [{"code": "1701", "termination_date": date(2024, 9, 2)}]
    with pytest.raises(ValueError):
        parse_termination({"status": "ok", "data": []})


STAMPS = {"isin_listed": "2026-09-25T00:00", "isin_unlisted": "2026-09-25T00:00",
          "termination": "2026-09-25T00:00"}


def _reg(code: str, section: str, cfi: str, listed: date, registry: str = "isin_listed") -> dict:
    return {code: [{"code": code, "isin": "x", "listing_date": listed,
                    "section": section, "cfi": cfi, "registry": registry}]}


def test_decide_common_preferred_and_listing_date_guard() -> None:
    first = date(2015, 1, 5)
    common = decide("2330", first, date(2026, 1, 1),
                    registry=_reg("2330", "股票", "ESVUFR", date(1994, 9, 5)),
                    terminations={}, stamps=STAMPS)
    assert common == Decision("2330", "stock", "twse:isin_listed@2026-09-25", "股票 ESVUFR")
    preferred = decide("2881A", date(2016, 6, 1), date(2026, 1, 1),
                       registry=_reg("2881A", "特別股", "EPNRAR", date(2016, 5, 31)),
                       terminations={}, stamps=STAMPS)
    assert preferred is not None and preferred.instrument_type == "preferred_share"
    # A security listed after the first observation is a different/renamed identity.
    late = decide("6757", date(2023, 8, 15), date(2026, 1, 1),
                  registry=_reg("6757", "股票", "ESVUFR", date(2024, 11, 29)),
                  terminations={}, stamps=STAMPS)
    assert late is None
    # ...unless the historical 創新板 table shows the code that day.
    board_move = decide("6757", date(2023, 8, 15), date(2026, 1, 1),
                        registry=_reg("6757", "股票", "ESVUFR", date(2024, 11, 29)),
                        terminations={}, stamps=STAMPS, tib_codes=frozenset({"6757"}))
    assert board_move is not None and board_move.instrument_type == "stock"
    # A re-listing date after the first observation (par-value or ISIN change) is
    # explained only when the company registry shows the company already listed then.
    relisted = decide("7780", date(2025, 9, 9), date(2026, 9, 24),
                      registry=_reg("7780", "股票", "ESVUFR", date(2026, 1, 19)),
                      terminations={}, stamps=STAMPS, company_listed=date(2025, 9, 9))
    assert relisted is not None and relisted.instrument_type == "stock"
    assert decide("7780", date(2025, 9, 9), date(2026, 9, 24),
                  registry=_reg("7780", "股票", "ESVUFR", date(2026, 1, 19)),
                  terminations={}, stamps=STAMPS, company_listed=date(2026, 1, 19)) is None
    # Preferred shares are never settled by company listing dates.
    assert decide("2881A", date(2015, 1, 5), date(2026, 1, 1),
                  registry=_reg("2881A", "特別股", "EPNRAR", date(2016, 5, 31)),
                  terminations={}, stamps=STAMPS, company_listed=date(2000, 1, 1)) is None


def test_decide_never_uses_code_format_or_name() -> None:
    # A letter-suffixed code with no registry or termination evidence stays unresolved.
    assert decide("2833A", date(2015, 1, 5), date(2015, 10, 1), registry={},
                  terminations={}, stamps=STAMPS, industry_member=True) is None
    assert decide("9999", date(2015, 1, 5), date(2015, 10, 1), registry={},
                  terminations={}, stamps=STAMPS, industry_member=True) is None


def test_termination_list_needs_industry_row_and_covering_dates() -> None:
    ended = {"1701": date(2024, 9, 2)}
    kwargs = {"registry": {}, "terminations": ended, "stamps": STAMPS}
    assert decide("1701", date(2015, 1, 5), date(2024, 8, 20), industry_member=True, **kwargs) is not None
    assert decide("1701", date(2015, 1, 5), date(2024, 8, 20), industry_member=False, **kwargs) is None
    assert decide("1701", date(2015, 1, 5), date(2025, 1, 1), industry_member=True, **kwargs) is None


def test_etn_table_beats_everything_else() -> None:
    decision = decide("020002", date(2019, 4, 30), date(2024, 4, 25), registry={},
                      terminations={}, stamps=STAMPS, etn_codes=frozenset({"020002"}))
    assert decision is not None and decision.instrument_type == "etn"


def _industry_row(code: str, day: date) -> dict:
    return {
        "code": code, "exchange": "TWSE", "instrument_type": None, "industry": None,
        "industry_status": "data_insufficient", "classification_effective_from": day,
        "classification_source": f"twse:MI_INDEX:industry_tables@{day.isoformat()}",
        "classification_status": "data_insufficient", "retrieved_at": "t",
        "instrument_type_status": "data_insufficient", "primary_oos_eligible_type": False,
    }


class _NoSweep:
    def codes_in_table(self, day: date, type_code: str) -> set[str]:
        return set()

    def close(self) -> None:
        return None


def test_resolution_merges_verified_rows_and_leaves_unsettled_codes_untouched(tmp_path: Path) -> None:
    day = date(2015, 1, 5)
    classifications = HistoricalClassificationStore(tmp_path / "cls")
    classifications.write(day, [_industry_row(c, day) for c in ("2330", "2881A", "7777")]
                          + [{**_industry_row("0050", day), "instrument_type": "etf",
                              "classification_status": "verified",
                              "classification_source": "twse:MI_INDEX:0099P@2015-01-05"}])
    evidence = InstrumentEvidenceStore(tmp_path / "ev")
    registry = pl.DataFrame([
        {"code": "2330", "isin": "a", "listing_date": date(1994, 9, 5), "section": "股票",
         "cfi": "ESVUFR", "registry": "isin_listed"},
        {"code": "2881A", "isin": "b", "listing_date": date(2014, 1, 1), "section": "特別股",
         "cfi": "EPNRAR", "registry": "isin_listed"},
    ])
    evidence.save_registry(registry.filter(pl.col("registry") == "isin_listed"),
                           source_url="u", sha256="s", retrieved_at="2026-09-25T00:00", registry="isin_listed")
    evidence.save_registry(registry.head(0), source_url="u", sha256="s",
                           retrieved_at="2026-09-25T00:00", registry="isin_unlisted")
    evidence.save_termination(pl.DataFrame(schema={"code": pl.Utf8, "termination_date": pl.Date}),
                              source_url="u", sha256="s", retrieved_at="2026-09-25T00:00")
    evidence.save_company(pl.DataFrame(schema={"code": pl.Utf8, "listing_date": pl.Date}),
                          source_url="u", sha256="s", retrieved_at="2026-09-25T00:00")
    first = {c: day for c in ("2330", "2881A", "7777", "0050")}
    last = {c: date(2026, 1, 1) for c in first}

    report = resolve_industry_only_codes(classifications, first, last, evidence, _NoSweep())

    assert report["resolved"] == 2 and report["unresolved_codes"] == ["7777"]
    frame = classifications.read()
    kinds = dict(zip(frame["code"], frame["instrument_type"], strict=True))
    assert kinds == {"0050": "etf", "2330": "stock", "2881A": "preferred_share", "7777": None}
    counts = classification_counts(first, frame)
    assert counts["verified_stock_count"] == 1
    assert counts["verified_unsupported_count"] == 1
    assert counts["unknown_count"] == 1 and counts["industry_only_unresolved_count"] == 1
    eligible = dict(zip(frame["code"], frame["primary_oos_eligible_type"], strict=True))
    assert eligible["2330"] is True and eligible["2881A"] is False


def test_primary_universe_joins_types_by_first_observed_session(tmp_path: Path) -> None:
    from app.taiwan.quant.primary_oos_runner import _primary_universe

    census = ObservedUniverseStore(tmp_path / "census")
    classifications = HistoricalClassificationStore(tmp_path / "cls")
    first, later = date(2015, 1, 5), date(2015, 1, 6)
    for day in (first, later):
        census.write("TWSE", day, [{**ROW, "date": day, "raw_code": "2330"},
                                   {**ROW, "date": day, "raw_code": "2881A"}])
    classifications.write(first, [{**_industry_row(c, first), "instrument_type": kind,
                                   "classification_status": "verified",
                                   "classification_source": "twse:isin_listed@2026-09-25"}
                                  for c, kind in (("2330", "stock"), ("2881A", "preferred_share"))])
    universe, identity = _primary_universe(census, classifications)
    kinds = {(r["date"], r["market_symbol"]): r["instrument_type"] for r in universe.iter_rows(named=True)}
    assert kinds[(later, "2330.TWSE")] == "stock"
    assert kinds[(later, "2881A.TWSE")] == "preferred_share"
    assert len(identity) == 64


def _preflight():
    from app.taiwan.quant.data_health import evaluate_data_health
    from app.taiwan.quant.primary_oos_runner import PrimaryOosPreflight

    health = evaluate_data_health(census_sessions=2850, census_total_sessions=2850,
                                  twse_codes_observed=100, twse_codes_classified=100)
    progress = {"completed_jobs": 478, "pending_jobs": 0, "failed_jobs": 0,
                "unique_first_seen_dates": 478}
    return PrimaryOosPreflight(health, progress, "idle", date(2026, 9, 23))


def _stock_history(codes: tuple[str, ...], sessions: list[date]) -> list[dict]:
    rows = []
    for offset, code in enumerate(codes):
        for index, day in enumerate(sessions):
            price = 50.0 + offset * 10 + index * 0.3 + (index % 7) * 0.2
            rows.append({**ROW, "date": day, "raw_code": code, "open": price, "high": price + 1,
                         "low": price - 1, "close": price, "volume": 1000.0 + index,
                         "amount": price * (1000.0 + index)})
    return rows


def test_factor_panel_entry_publishes_existing_calculation_for_verified_stocks_only(
    tmp_path: Path,
) -> None:
    from app.taiwan.backfill_worker import TaiwanHistoricalBackfillWorker
    from app.taiwan.quant.evaluation_spec import PRIMARY_OOS_SPEC
    from app.taiwan.quant.panel import build_factor_panel
    from app.taiwan.quant.primary_panel import build_primary_factor_panel
    from app.taiwan.quant.storage import FactorPanelStore
    from app.taiwan.quant_eligibility import PRIMARY_VERIFIED

    sessions = [date(2020, 1, 1) + timedelta(days=n) for n in range(120)]
    sessions = [day for day in sessions if day.weekday() < 5][:70]
    census = ObservedUniverseStore(tmp_path / "observed")
    for day in sessions:
        census.write("TWSE", day, [r for r in _stock_history(("2330", "2881A"), sessions)
                                   if r["date"] == day])
    classifications = HistoricalClassificationStore(tmp_path / "cls")
    classifications.write(sessions[0], [
        {**_industry_row(code, sessions[0]), "instrument_type": kind,
         "classification_status": "verified", "classification_source": "twse:isin_listed@x"}
        for code, kind in (("2330", "stock"), ("2881A", "preferred_share"))])
    worker = TaiwanHistoricalBackfillWorker(
        data_dir=tmp_path, census_store=census, classification_store=classifications)
    store = FactorPanelStore(tmp_path / "factors")

    built = build_primary_factor_panel(_preflight(), events=(), store=store, worker=worker, workers=1)

    assert built == {"symbols": 1, "sessions": 70, "rows": 70}
    stored = store.read_all(factor_version=PRIMARY_OOS_SPEC.factor_version,
                            policy_version=PRIMARY_OOS_SPEC.policy_version,
                            universe_tier=PRIMARY_VERIFIED)
    assert set(stored.values["symbol"].to_list()) == {"2330.TWSE"}
    history = census.read("TWSE").filter(pl.col("raw_code") == "2330").select(
        pl.concat_str([pl.col("raw_code"), pl.lit(".TWSE")]).alias("symbol"),
        "date", "open", "high", "low", "close", "volume", "amount").sort("date")
    direct = build_factor_panel(history, events=(), policy_version=PRIMARY_OOS_SPEC.policy_version,
                                universe_tier=PRIMARY_VERIFIED)
    assert stored.values.equals(direct.values, null_equal=True)


def test_action_snapshot_reuses_completed_pulls_and_fetches_only_new_tail(
    tmp_path: Path, monkeypatch,
) -> None:
    from app.taiwan.corporate_actions import CorporateActionStore
    from app.taiwan.quant import primary_oos_runner as runner

    calls: list[tuple[date, date]] = []

    def fake_fetch(start: date, end: date):
        calls.append((start, end))
        return ()

    monkeypatch.setattr(runner, "_fetch_actions", fake_fetch)
    store = CorporateActionStore(tmp_path / "adj_factor")
    _events, coverage = runner._action_snapshot(date(2015, 1, 5), date(2026, 9, 1), store=store)
    assert coverage.status == "verified" and calls == [(date(2015, 1, 5), date(2026, 9, 1))]
    runner._action_snapshot(date(2016, 1, 4), date(2026, 8, 1), store=store)
    assert len(calls) == 1  # fully covered: no network
    runner._action_snapshot(date(2015, 1, 5), date(2026, 9, 25), store=store)
    assert calls[-1] == (date(2026, 9, 2), date(2026, 9, 25))

    def failing(_start: date, _end: date):
        raise RuntimeError("provider down")

    monkeypatch.setattr(runner, "_fetch_actions", failing)
    with pytest.raises(RuntimeError):
        runner._action_snapshot(date(2015, 1, 5), date(2026, 10, 1), store=store)
    monkeypatch.setattr(runner, "_fetch_actions", fake_fetch)
    runner._action_snapshot(date(2015, 1, 5), date(2026, 9, 25), store=store)  # marker unchanged


def test_official_notice_settles_preferred_only_when_the_code_is_named_verbatim() -> None:
    subjects = [
        ("中華民國113年11月25日", "臺證上一字第1130022626號",
         "大聯大控股股份有限公司（公司代號：3702）甲種特別股（大聯大甲特3702A）到期收回暨終止上市買賣等相關事宜"),
        ("中華民國113年09月26日", "臺證上一字第1130018786號",
         "王道商業銀行股份有限公司乙種特別股（股票代號：2897B）股款繳納憑證上市開始買賣日期"),
    ]
    decision = announcement_decision("3702A", subjects)
    assert decision is not None and decision.instrument_type == "preferred_share"
    assert decision.source == "twse:announcement:臺證上一字第1130022626號"
    assert announcement_decision("2897A", subjects) is None       # only the sibling code appears
    assert announcement_decision("3702", subjects) is None        # company code is not the class code
    assert announcement_decision("2897B", [subjects[0]]) is None  # no 特別股 notice for it


def test_priceless_official_row_is_not_evaluable_and_does_not_poison_later_windows(
    tmp_path: Path,
) -> None:
    from app.taiwan.backfill_worker import TaiwanHistoricalBackfillWorker
    from app.taiwan.quant.evaluation_spec import PRIMARY_OOS_SPEC
    from app.taiwan.quant.primary_oos_runner import _primary_universe
    from app.taiwan.quant.primary_panel import build_primary_factor_panel
    from app.taiwan.quant.storage import FactorPanelStore
    from app.taiwan.quant_eligibility import PRIMARY_VERIFIED

    sessions = [day for day in (date(2020, 1, 1) + timedelta(days=n) for n in range(120))
                if day.weekday() < 5][:70]
    halted = sessions[30]
    census = ObservedUniverseStore(tmp_path / "observed")
    for day in sessions:
        row = _stock_history(("2330",), sessions)[sessions.index(day)]
        if day == halted:  # the exchange prints "--": a row without any price
            row.update(open=None, high=None, low=None, close=None, volume=0.0, amount=0.0)
        census.write("TWSE", day, [row])
    classifications = HistoricalClassificationStore(tmp_path / "cls")
    classifications.write(sessions[0], [{
        **_industry_row("2330", sessions[0]), "instrument_type": "stock",
        "classification_status": "verified", "classification_source": "twse:isin_listed@x"}])

    universe, _ = _primary_universe(census, classifications)
    observed = dict(zip(universe["date"], universe["observed_on_market"], strict=True))
    priced = dict(zip(universe["date"], universe["price_bar_available"], strict=True))
    assert observed[halted] is True  # still an official market observation
    assert priced[halted] is False and priced[sessions[31]] is True

    worker = TaiwanHistoricalBackfillWorker(
        data_dir=tmp_path, census_store=census, classification_store=classifications)
    store = FactorPanelStore(tmp_path / "factors")
    built = build_primary_factor_panel(_preflight(), events=(), store=store, worker=worker, workers=1)
    assert built["rows"] == 69
    panel = store.read_all(factor_version=PRIMARY_OOS_SPEC.factor_version,
                           policy_version=PRIMARY_OOS_SPEC.policy_version,
                           universe_tier=PRIMARY_VERIFIED)
    assert halted not in panel.values["date"].to_list()
    assert set(panel.values["adjustment_status"].to_list()) == {"verified"}
    # A window that skips the halted session is unavailable, not silently 5 bars long.
    row = {r["date"]: r for r in panel.values.iter_rows(named=True)}
    assert row[sessions[31]]["momentum_5d"] is None      # bars 26..31 skip session 30
    assert row[sessions[35]]["momentum_5d"] is None      # window 30..35 still skips it
    assert row[sessions[36]]["momentum_5d"] is not None  # 31..36: consecutive sessions again
    assert row[sessions[29]]["momentum_5d"] is not None
    unavailable = panel.coverage.filter(
        (pl.col("factor") == "momentum_5d") & (pl.col("date") > halted)
        & (pl.col("date") <= sessions[35]))
    assert unavailable["date"].sort().to_list() == sessions[31:36]
    assert set(unavailable["status"].to_list()) == {"insufficient_history"}


def _health(verified: int, unknown: int):
    from app.taiwan.quant.data_health import evaluate_data_health

    counts = {"verified_stock_count": verified, "unknown_count": unknown,
              "industry_only_unresolved_count": unknown,
              "primary_classification_denominator": verified + unknown,
              "primary_classification_ratio": verified / (verified + unknown)}
    return evaluate_data_health(
        census_sessions=2850, census_total_sessions=2852, twse_codes_observed=verified + unknown,
        twse_codes_classified=verified, classification=counts)


def test_classification_gate_is_the_ratio_not_a_single_unresolved_code() -> None:
    assert _health(1153, 2).is_ready(__import__("app.taiwan.quant.data_health",
                                                fromlist=["ReadinessLevel"]).ReadinessLevel.PRIMARY_OOS)
    blocked = _health(1153, 20)  # 98.3% < 99%
    assert not blocked.levels["ready_for_primary_oos"]
    assert any("classification coverage" in reason
               for reason in blocked.blocked_reasons["ready_for_primary_oos"])
    assert not _health(0, 1188).levels["ready_for_training"]  # industry-only alone never passes


def _event(symbol: str, status: str, day: date = date(2020, 1, 6), **kw):
    import datetime as dt

    from app.taiwan.corporate_actions import CorporateActionEvent, event_market_open
    from app.taiwan.providers.taiwan_values import TAIPEI

    values = {"symbol": symbol, "exchange": "TWSE", "effective_date": day,
              "effective_at": event_market_open(day), "event_type": "stock_dividend",
              "previous_close": None, "reference_price": None, "factor": None,
              "cash_dividend": None, "free_share_ratio": None, "reduction_ratio": None,
              "source": "TWT49U", "source_url": "u",
              "retrieved_at": dt.datetime(2026, 9, 26, tzinfo=TAIPEI),
              "status": status,
              "reason": "detail_provider_error" if status == "provider_error" else None}
    values.update(kw)
    return CorporateActionEvent(**values)


def test_failed_fetch_never_conflicts_with_a_real_observation() -> None:
    from app.taiwan.corporate_actions import resolve_event_conflicts

    failed = _event("2330.TWSE", "provider_error")
    real = _event("2330.TWSE", "data_insufficient", reason="cash_subscription_requires_detail")
    assert resolve_event_conflicts([failed, real]) == (real,)
    assert resolve_event_conflicts([failed]) == (failed,)


def test_provider_errors_for_primary_symbols_are_never_recorded_as_covered(
    tmp_path: Path, monkeypatch,
) -> None:
    from app.taiwan.corporate_actions import CorporateActionStore
    from app.taiwan.quant import primary_oos_runner as runner

    store = CorporateActionStore(tmp_path / "adj_factor")
    marker = store.path.with_name("coverage.json")
    monkeypatch.setattr(runner, "_fetch_actions",
                        lambda a, b: (_event("2330.TWSE", "provider_error"),))
    with pytest.raises(runner.PrimaryOosInputError):
        runner._action_snapshot(date(2020, 1, 1), date(2020, 2, 1), store=store,
                                required_symbols=frozenset({"2330.TWSE"}))
    assert not marker.exists() and not store.path.exists()
    # A preferred share outside the Primary universe may carry a permanent detail error.
    runner._action_snapshot(date(2020, 1, 1), date(2020, 2, 1), store=store,
                            required_symbols=frozenset({"1101.TWSE"}))
    assert marker.exists()
    # A symbol that later joins the Primary universe is healed by a single-date refetch.
    monkeypatch.setattr(runner, "_fetch_actions_on", lambda source, day: (_event(
        "2330.TWSE", "verified", previous_close=100.0, reference_price=90.0, factor=0.9,
        precision_method="test", event_type="cash_dividend"),))
    events, _ = runner._action_snapshot(date(2020, 1, 1), date(2020, 2, 1), store=store,
                                        required_symbols=frozenset({"2330.TWSE"}))
    assert [e.status for e in events] == ["verified"]


def test_panel_is_not_published_when_any_adjustment_is_unverified(tmp_path: Path) -> None:
    from app.taiwan.backfill_worker import TaiwanHistoricalBackfillWorker
    from app.taiwan.quant.primary_oos_runner import PrimaryOosInputError
    from app.taiwan.quant.primary_panel import build_primary_factor_panel
    from app.taiwan.quant.storage import FactorPanelStore

    sessions = [day for day in (date(2020, 1, 1) + timedelta(days=n) for n in range(120))
                if day.weekday() < 5][:40]
    census = ObservedUniverseStore(tmp_path / "observed")
    for day in sessions:
        census.write("TWSE", day,
                     [r for r in _stock_history(("2330",), sessions) if r["date"] == day])
    classifications = HistoricalClassificationStore(tmp_path / "cls")
    classifications.write(sessions[0], [{
        **_industry_row("2330", sessions[0]), "instrument_type": "stock",
        "classification_status": "verified", "classification_source": "twse:isin_listed@x"}])
    worker = TaiwanHistoricalBackfillWorker(
        data_dir=tmp_path, census_store=census, classification_store=classifications)
    store = FactorPanelStore(tmp_path / "factors")
    broken = _event("2330.TWSE", "data_insufficient", day=sessions[20],
                    reason="detail reference mismatch")
    with pytest.raises(PrimaryOosInputError):
        build_primary_factor_panel(_preflight(), events=(broken,), store=store,
                                   worker=worker, workers=1)
    assert not any((tmp_path / "factors").rglob("values.parquet"))


def test_evidence_refresh_publishes_nothing_when_a_later_source_fails(tmp_path: Path) -> None:
    from app.taiwan.instrument_evidence import refresh_evidence

    sep = "\u3000"
    listed = _isin_page(
        [(" 股票 ",), (f"2330{sep}台積電", "TW0002330008", "1994/09/05", "上市", "半導體業",
                     "ESVUFR", "")], "本國上市證券國際證券辨識號碼一覽表")
    unlisted = _isin_page(
        [(f"1111{sep}欣欣水泥", "TW0001111003", "1982/11/24", "水泥工業", "ESVUFR", "")],
        "本國未上市，未上櫃公開發行證券，國際證券辨識號碼一覽表")
    pages = {"strMode=2": listed, "strMode=1": unlisted}
    store = InstrumentEvidenceStore(tmp_path / "ev")

    def payload(url: str):
        if "suspendListing" in url:
            return {"status": "ok", "data": [["113/09/02", "中化", "1701"]]}
        return []  # the company registry is unavailable

    with pytest.raises(ValueError):
        refresh_evidence(store, today=date(2026, 9, 26),
                         fetch_bytes=lambda url: next(v for k, v in pages.items() if k in url),
                         fetch_payload=payload)
    assert not (tmp_path / "ev").exists() or not any((tmp_path / "ev").iterdir())


def test_unresolved_symbol_never_enters_the_primary_universe_or_panel(tmp_path: Path) -> None:
    from app.taiwan.backfill_worker import TaiwanHistoricalBackfillWorker
    from app.taiwan.quant.evaluation_spec import PRIMARY_OOS_SPEC
    from app.taiwan.quant.primary_oos_runner import _primary_universe
    from app.taiwan.quant.primary_panel import build_primary_factor_panel
    from app.taiwan.quant.storage import FactorPanelStore
    from app.taiwan.quant_eligibility import PRIMARY_VERIFIED, eligible

    sessions = [day for day in (date(2020, 1, 1) + timedelta(days=n) for n in range(120))
                if day.weekday() < 5][:70]
    census = ObservedUniverseStore(tmp_path / "observed")
    for day in sessions:
        census.write("TWSE", day, [r for r in _stock_history(("2330", "2833A"), sessions)
                                   if r["date"] == day])
    classifications = HistoricalClassificationStore(tmp_path / "cls")
    classifications.write(sessions[0], [
        {**_industry_row("2330", sessions[0]), "instrument_type": "stock",
         "classification_status": "verified", "classification_source": "twse:isin_listed@x"},
        _industry_row("2833A", sessions[0]),   # industry table only: subtype unproven
    ])

    universe, _ = _primary_universe(census, classifications)
    unresolved = universe.filter(pl.col("market_symbol") == "2833A.TWSE")
    assert set(unresolved["instrument_type_status"].to_list()) == {"data_insufficient"}
    admitted = eligible(universe, PRIMARY_OOS_SPEC.universe_policy)
    assert set(admitted["market_symbol"].to_list()) == {"2330.TWSE"}

    worker = TaiwanHistoricalBackfillWorker(
        data_dir=tmp_path, census_store=census, classification_store=classifications)
    store = FactorPanelStore(tmp_path / "factors")
    build_primary_factor_panel(_preflight(), events=(), store=store, worker=worker, workers=1)
    panel = store.read_all(factor_version=PRIMARY_OOS_SPEC.factor_version,
                           policy_version=PRIMARY_OOS_SPEC.policy_version,
                           universe_tier=PRIMARY_VERIFIED)
    assert set(panel.values["symbol"].to_list()) == {"2330.TWSE"}


def test_unrecovered_saturday_session_stays_in_the_readiness_denominator(tmp_path: Path) -> None:
    from app.taiwan.observed_universe import census_coverage, session_candidates

    store = ObservedUniverseStore(tmp_path)
    friday, saturday = date(2016, 1, 29), date(2016, 1, 30)
    _seed(store, "TWSE", [friday], [])
    fetch = _fetcher({"FMTQIK": _twse_month(2016, 1, ["105/01/29", "105/01/30"])})
    report = verify_empty_days(store, "TWSE", start=date(2016, 1, 1), end=date(2016, 1, 31),
                               fetch=fetch, census_rows=lambda day: [])
    assert report["weekend_sessions_missing"] == ["2016-01-30"]
    assert store.partition_status("TWSE", saturday) == "empty_unknown"
    candidates = session_candidates(store, "TWSE", date(2016, 1, 29), date(2016, 1, 31))
    assert saturday in candidates
    coverage = census_coverage(store, "TWSE", candidates)
    assert coverage.unresolved_dates == 1 and coverage.trading_coverage_ratio == 0.5
    # A later run that recovers rows completes it.
    verify_empty_days(store, "TWSE", start=date(2016, 1, 1), end=date(2016, 1, 31), fetch=fetch,
                      census_rows=lambda day: [{**ROW, "date": day}])
    assert store.partition_status("TWSE", saturday) == "observed"
    assert census_coverage(store, "TWSE", candidates).trading_coverage_ratio == 1.0


def test_mixed_evidence_generations_and_unbound_action_markers_fail_closed(
    tmp_path: Path, monkeypatch,
) -> None:
    from app.taiwan.corporate_actions import CorporateActionStore
    from app.taiwan.quant import primary_oos_runner as runner

    evidence = InstrumentEvidenceStore(tmp_path / "ev")
    empty_registry = pl.DataFrame(schema={"code": pl.Utf8, "isin": pl.Utf8, "listing_date": pl.Date,
                                          "section": pl.Utf8, "cfi": pl.Utf8, "registry": pl.Utf8})
    for name, stamp in (("isin_listed", "t1"), ("isin_unlisted", "t1")):
        evidence.save_registry(empty_registry, source_url="u", sha256="s", retrieved_at=stamp,
                               registry=name)
    evidence.save_termination(pl.DataFrame(schema={"code": pl.Utf8, "termination_date": pl.Date}),
                              source_url="u", sha256="s", retrieved_at="t1")
    evidence.save_company(pl.DataFrame(schema={"code": pl.Utf8, "listing_date": pl.Date}),
                          source_url="u", sha256="s", retrieved_at="t2")  # another refresh
    with pytest.raises(ValueError, match="different refreshes"):
        evidence.load()
    with pytest.raises(ValueError, match="different refreshes"):
        evidence.load_company()

    store = CorporateActionStore(tmp_path / "adj_factor")
    monkeypatch.setattr(runner, "_fetch_actions", lambda a, b: (_event("2330.TWSE", "verified",
                        previous_close=100.0, reference_price=90.0, factor=0.9,
                        precision_method="t", event_type="cash_dividend"),))
    runner._action_snapshot(date(2020, 1, 1), date(2020, 2, 1), store=store)
    store.path.unlink()  # the marker survives without its event file (partial copy)
    with pytest.raises(runner.PrimaryOosInputError, match="does not match"):
        runner._action_snapshot(date(2020, 1, 1), date(2020, 2, 1), store=store)


def test_provider_error_is_superseded_only_by_the_same_source() -> None:
    from app.taiwan.corporate_actions import resolve_event_conflicts

    failed = _event("2330.TWSE", "provider_error")
    other_source = _event("2330.TWSE", "data_insufficient", source="TWTAUU",
                          event_type="capital_reduction", reason="x")
    kept = resolve_event_conflicts([failed, other_source])
    # Another source's answer does not settle the failed request: both stay unusable.
    assert len(kept) == 2 and all(e.status != "verified" for e in kept)
    same_source = _event("2330.TWSE", "data_insufficient", reason="x")
    assert resolve_event_conflicts([failed, same_source]) == (same_source,)



def test_failed_request_survives_a_cross_source_conflict_for_the_retry() -> None:
    from app.taiwan.corporate_actions import resolve_event_conflicts

    failed = _event("2330.TWSE", "provider_error")
    other = _event("2330.TWSE", "verified", source="TWTAUU", event_type="capital_reduction",
                   previous_close=10.0, reference_price=20.0, factor=2.0,
                   precision_method="official_reference_ratio")
    kept = {e.source: e for e in resolve_event_conflicts([failed, other])}
    assert kept["TWT49U"].status == "provider_error"        # still visible to the stale retry
    assert kept["TWTAUU"].status == "data_insufficient"     # same-day cross-source: unusable


def test_action_gaps_are_staged_so_store_and_marker_advance_together(
    tmp_path: Path, monkeypatch,
) -> None:
    from app.taiwan.corporate_actions import CorporateActionStore
    from app.taiwan.quant import primary_oos_runner as runner

    store = CorporateActionStore(tmp_path / "adj_factor")
    monkeypatch.setattr(runner, "_fetch_actions", lambda a, b: ())
    runner._action_snapshot(date(2020, 1, 10), date(2020, 2, 1), store=store)
    before = store.snapshot_digest() if store.path.exists() else None

    def fetch(start: date, end: date):
        if start > date(2020, 2, 1):
            raise RuntimeError("tail outage")
        return (_event("2330.TWSE", "verified", previous_close=100.0, reference_price=90.0,
                       factor=0.9, precision_method="t", event_type="cash_dividend"),)

    monkeypatch.setattr(runner, "_fetch_actions", fetch)
    with pytest.raises(RuntimeError):  # head gap succeeds, tail gap fails
        runner._action_snapshot(date(2019, 12, 1), date(2020, 3, 1), store=store)
    assert (store.snapshot_digest() if store.path.exists() else None) == before
    monkeypatch.setattr(runner, "_fetch_actions", lambda a, b: ())
    runner._action_snapshot(date(2019, 12, 1), date(2020, 3, 1), store=store)  # retry works


def test_panel_refuses_an_unresolved_empty_session_inside_its_span(tmp_path: Path) -> None:
    from app.taiwan.backfill_worker import TaiwanHistoricalBackfillWorker
    from app.taiwan.quant.primary_oos_runner import PrimaryOosInputError
    from app.taiwan.quant.primary_panel import build_primary_factor_panel
    from app.taiwan.quant.storage import FactorPanelStore

    sessions = [day for day in (date(2020, 1, 1) + timedelta(days=n) for n in range(120))
                if day.weekday() < 5][:40]
    census = ObservedUniverseStore(tmp_path / "observed")
    for day in sessions:
        if day == sessions[20]:
            census.write("TWSE", day, [])  # official session whose rows never arrived
        else:
            census.write("TWSE", day,
                         [r for r in _stock_history(("2330",), sessions) if r["date"] == day])
    classifications = HistoricalClassificationStore(tmp_path / "cls")
    classifications.write(sessions[0], [{
        **_industry_row("2330", sessions[0]), "instrument_type": "stock",
        "classification_status": "verified", "classification_source": "twse:isin_listed@x"}])
    worker = TaiwanHistoricalBackfillWorker(
        data_dir=tmp_path, census_store=census, classification_store=classifications)
    store = FactorPanelStore(tmp_path / "factors")
    with pytest.raises(PrimaryOosInputError, match="unresolved empty sessions"):
        build_primary_factor_panel(_preflight(), events=(), store=store, worker=worker, workers=1)
    assert not any((tmp_path / "factors").rglob("values.parquet"))


def test_resume_identity_changes_with_the_factor_implementation(monkeypatch) -> None:
    from app.taiwan.quant import primary_panel

    before = primary_panel._code_fingerprint()
    assert before == primary_panel._code_fingerprint()
    original = primary_panel.Path.read_bytes

    def changed(self):
        data = original(self)
        return data + b"#" if self.name == "panel.py" else data

    monkeypatch.setattr(primary_panel.Path, "read_bytes", changed)
    assert primary_panel._code_fingerprint() != before



def test_month_verification_marker_only_after_a_clean_complete_pass(tmp_path: Path) -> None:
    store = ObservedUniverseStore(tmp_path)
    span = (date(2015, 2, 1), date(2015, 2, 26))
    traded = [date(2015, 2, 2), date(2015, 2, 26)]
    _seed_span(store, "TWSE", *span, observed=traded)
    good = _fetcher({"FMTQIK": _twse_month(2015, 2, ["104/02/02", "104/02/26"])})
    bad = _fetcher({"FMTQIK": {"stat": "很抱歉，沒有符合條件的資料!"}})
    verify_empty_days(store, "TWSE", start=span[0], end=span[1], fetch=bad,
                      census_rows=lambda day: [])
    assert not store.month_verification_covers("TWSE", *span)
    verify_empty_days(store, "TWSE", start=span[0], end=span[1], fetch=good, apply=False,
                      census_rows=lambda day: [])
    assert not store.month_verification_covers("TWSE", *span)   # a dry run proves nothing
    verify_empty_days(store, "TWSE", start=span[0], end=span[1], fetch=good,
                      census_rows=lambda day: [])
    assert store.month_verification_covers("TWSE", *span)
    assert not store.month_verification_covers("TWSE", date(2015, 1, 1), span[1])
    assert not store.month_verification_covers("TWSE", span[0], date(2015, 4, 1))
    store.partition_path("TWSE", date(2015, 2, 26)).unlink()   # a verified partition is lost
    assert not store.month_verification_covers("TWSE", *span)


def test_panel_build_fails_if_the_stores_change_while_it_computes(
    tmp_path: Path, monkeypatch,
) -> None:
    from app.taiwan.backfill_worker import TaiwanHistoricalBackfillWorker
    from app.taiwan.quant import primary_panel
    from app.taiwan.quant.primary_oos_runner import PrimaryOosInputError
    from app.taiwan.quant.storage import FactorPanelStore

    sessions = [day for day in (date(2020, 1, 1) + timedelta(days=n) for n in range(120))
                if day.weekday() < 5][:41]
    census = ObservedUniverseStore(tmp_path / "observed")
    for day in sessions[:40]:
        census.write("TWSE", day,
                     [r for r in _stock_history(("2330",), sessions) if r["date"] == day])
    classifications = HistoricalClassificationStore(tmp_path / "cls")
    classifications.write(sessions[0], [{
        **_industry_row("2330", sessions[0]), "instrument_type": "stock",
        "classification_status": "verified", "classification_source": "twse:isin_listed@x"}])
    worker = TaiwanHistoricalBackfillWorker(
        data_dir=tmp_path, census_store=census, classification_store=classifications)

    def concurrent_backfill(snapshot, store, workers, batch_size):
        census.write("TWSE", sessions[40],
                     [r for r in _stock_history(("2330",), sessions) if r["date"] == sessions[40]])

    monkeypatch.setattr(primary_panel, "_compute_batches", concurrent_backfill)
    store = FactorPanelStore(tmp_path / "factors")
    with pytest.raises(PrimaryOosInputError, match="changed while the panel was computed"):
        primary_panel.build_primary_factor_panel(_preflight(), events=(), store=store,
                                                 worker=worker, workers=1)
    assert not worker.lock.path.exists() if hasattr(worker.lock, "path") else True


def test_history_is_cut_at_every_missing_exchange_session() -> None:
    from app.taiwan.quant.primary_panel import split_at_session_gaps

    sessions = [date(2020, 1, 1) + timedelta(days=n) for n in range(10)]
    present = [0, 1, 2, 3, 4, 6, 7, 8]  # session 5 has no price bar
    frame = pl.DataFrame({"symbol": ["A.TWSE"] * 8 + ["B.TWSE"] * 3,
                          "date": [sessions[i] for i in present] + sessions[:3],
                          "close": [1.0] * 11})
    runs = split_at_session_gaps(frame, sessions)
    assert [(r["symbol"][0], r["date"].to_list()) for r in runs] == [
        ("A.TWSE", sessions[0:5]), ("A.TWSE", sessions[6:9]), ("B.TWSE", sessions[0:3])]
    assert all("_session" not in r.columns and "_run" not in r.columns for r in runs)


def test_recursive_indicators_restart_and_warm_up_after_a_gap(tmp_path: Path) -> None:
    from app.taiwan.quant.evaluation_spec import PRIMARY_OOS_SPEC
    from app.taiwan.quant.primary_panel import _build_batch

    sessions = [day for day in (date(2020, 1, 1) + timedelta(days=n) for n in range(150))
                if day.weekday() < 5][:100]
    rows = [r for i, r in enumerate(_stock_history(("2330",), sessions)) if i != 60]
    history = pl.DataFrame(rows).select(
        pl.concat_str([pl.col("raw_code"), pl.lit(".TWSE")]).alias("symbol"),
        "date", "open", "high", "low", "close", "volume", "amount")
    _build_batch((history, (), PRIMARY_OOS_SPEC.factor_version, tmp_path, sessions))
    values = pl.read_parquet(tmp_path / "values_2330_TWSE.parquet")
    row = {r["date"]: r for r in values.iter_rows(named=True)}
    assert row[sessions[59]]["rsi_14"] is not None and row[sessions[59]]["macd_dif"] is not None
    assert row[sessions[61]]["rsi_14"] is None          # restarted: warming up again
    assert row[sessions[70]]["rsi_14"] is None and row[sessions[70]]["macd_dif"] is None
    assert row[sessions[80]]["rsi_14"] is not None      # 15 bars after the gap
    assert row[sessions[80]]["macd_dif"] is None        # MACD needs 35
    assert row[sessions[97]]["macd_dif"] is not None


def test_trailing_day_needs_the_official_schedule_and_the_marker_needs_the_exact_end(
    tmp_path: Path,
) -> None:
    from app.taiwan.trading_day_evidence import fetch_twse_closures

    store = ObservedUniverseStore(tmp_path)
    days = _weekdays(date(2026, 9, 1), date(2026, 9, 24))
    _seed_span(store, "TWSE", date(2026, 9, 1), date(2026, 9, 25), observed=days)
    month = _twse_month(2026, 9, ["115/09/23", "115/09/24"])
    earlier = [[f"115/09/{d.day:02d}", "1"] for d in _weekdays(date(2026, 9, 1), date(2026, 9, 22))]
    month["data"] = earlier + month["data"]   # published earlier in the month
    fetch = _fetcher({"FMTQIK": month})
    span = (date(2026, 9, 1), date(2026, 9, 25))
    first = verify_empty_days(store, "TWSE", start=span[0], end=span[1], fetch=fetch,
                              census_rows=lambda day: [])
    assert first["after_last_published_session"] == ["2026-09-25"]
    assert not store.month_verification_covers("TWSE", *span)   # a lagging month is not clean

    schedule = {"stat": "ok", "data": [
        ["2026-09-25", "中秋節", "依規定放假1日。"],
        ["2026-09-28", "孔子誕辰紀念日/ 教師節", "依規定放假1日。"],
        ["2026-01-02", "國曆新年開始交易日", "國曆新年開始交易。"],
        ["2022-01-27", "農曆春節前最後交易日", "1月27日市場無交易，僅辦理結算交割作業。"]]}
    closures = fetch_twse_closures(2026, fetch=lambda url: schedule)
    assert closures == {date(2026, 9, 25), date(2026, 9, 28), date(2022, 1, 27)}
    verify_empty_days(store, "TWSE", start=span[0], end=span[1], fetch=fetch,
                      census_rows=lambda day: [], closures=closures)
    assert store.partition_status("TWSE", date(2026, 9, 25)) == "confirmed_non_trading"
    assert store.day_evidence("TWSE", date(2026, 9, 25)).evidence_source == "twse:holidaySchedule"
    assert store.month_verification_covers("TWSE", *span)
    assert not store.month_verification_covers("TWSE", span[0], date(2026, 9, 26))  # exact date


def test_official_weekday_without_a_partition_is_fetched_or_keeps_verification_open(
    tmp_path: Path,
) -> None:
    store = ObservedUniverseStore(tmp_path)
    tuesday = date(2015, 2, 3)
    span = (date(2015, 2, 1), date(2015, 2, 3))
    # Tuesday's census request failed: it is the only weekday without a partition.
    _seed_span(store, "TWSE", *span, observed=[date(2015, 2, 2)], skip=(tuesday,))
    fetch = _fetcher({"FMTQIK": _twse_month(2015, 2, ["104/02/02", "104/02/03"])})

    def failing(day):
        raise RuntimeError("provider down")

    report = verify_empty_days(store, "TWSE", start=span[0], end=span[1], fetch=fetch,
                               census_rows=failing)
    assert report["weekend_sessions_missing"] == ["2015-02-03"]
    assert not store.has("TWSE", tuesday)
    assert not store.month_verification_covers("TWSE", *span)
    verify_empty_days(store, "TWSE", start=span[0], end=span[1], fetch=fetch,
                      census_rows=lambda day: [{**ROW, "date": day}])
    assert store.partition_status("TWSE", tuesday) == "observed"
    assert store.month_verification_covers("TWSE", *span)


def test_month_marker_notices_a_replaced_partition_and_legacy_rows_are_normalized(
    tmp_path: Path,
) -> None:
    store = ObservedUniverseStore(tmp_path / "census")
    span = (date(2015, 2, 1), date(2015, 2, 3))
    _seed_span(store, "TWSE", *span, observed=[date(2015, 2, 2), date(2015, 2, 3)])
    verify_empty_days(store, "TWSE", start=span[0], end=span[1],
                      fetch=_fetcher({"FMTQIK": _twse_month(2015, 2, ["104/02/02", "104/02/03"])}),
                      census_rows=lambda day: [])
    assert store.month_verification_covers("TWSE", *span)
    store.write("TWSE", date(2015, 2, 3), [])          # same date, but no longer the verified rows
    assert not store.month_verification_covers("TWSE", *span)

    # A legacy partition says "verified" for an industry-only row; resolution must still see it.
    classifications = HistoricalClassificationStore(tmp_path / "cls")
    day = date(2015, 1, 5)
    legacy = {**_industry_row("2330", day), "classification_status": "verified",
              "instrument_type_status": "verified", "instrument_type": "stock"}
    path = classifications.partition_path(day)
    path.parent.mkdir(parents=True)
    pl.DataFrame([legacy]).write_parquet(path)
    evidence = InstrumentEvidenceStore(tmp_path / "ev")
    listed = pl.DataFrame([{"code": "2330", "isin": "a", "listing_date": date(1994, 9, 5),
                            "section": "股票", "cfi": "ESVUFR", "registry": "isin_listed"}])
    for name, frame in (("isin_listed", listed), ("isin_unlisted", listed.head(0))):
        evidence.save_registry(frame, source_url="u", sha256="s", retrieved_at="t", registry=name)
    evidence.save_termination(pl.DataFrame(schema={"code": pl.Utf8, "termination_date": pl.Date}),
                              source_url="u", sha256="s", retrieved_at="t")
    evidence.save_company(pl.DataFrame(schema={"code": pl.Utf8, "listing_date": pl.Date}),
                          source_url="u", sha256="s", retrieved_at="t")
    report = resolve_industry_only_codes(classifications, {"2330": day}, {"2330": day},
                                         evidence, _NoSweep())
    assert report["considered"] == 1 and report["resolved"] == 1

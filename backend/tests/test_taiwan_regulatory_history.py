"""Official regulatory history used by the trend_liquidity_v1 PIT replay."""
from __future__ import annotations

# ruff: noqa: RUF001 -- Official Chinese fixtures keep the exchange's fullwidth separators.
from datetime import date, timedelta

import polars as pl
import pytest

from app.taiwan.regulatory_history import (
    TPEX_CMODE_FIELDS,
    TPEX_DISPOSAL_FIELDS,
    TWSE_PUNISH_FIELDS,
    RegulatoryHistoryStore,
    RegulatorySchemaError,
    backfill_announcements,
    backfill_cmode,
    parse_tpex_cmode,
    parse_tpex_disposal,
    parse_twse_punish,
    replay_regulatory_evidence,
)

META = {"source_url": "u", "retrieved_at": "2026-09-26T12:00:00+08:00"}


def _meta(day: str) -> dict[str, str]:
    return {"source_url": "u", "retrieved_at": f"{day}T20:00:00+08:00"}


def _punish(*rows):
    data = [[i + 1, pub, code, "名稱", 1, "連續三次", period, "第一次處置", "內容", "備註"]
            for i, (pub, code, period) in enumerate(rows)]
    return {"stat": "OK", "fields": TWSE_PUNISH_FIELDS, "data": data, "total": len(data)}


def _disposal(start, end, *rows):
    data = []
    for i, (pub, code, period) in enumerate(rows):
        content = "本日無處置資料" if not code else "處置內容"
        data.append([i + 1, pub, code, f"{code}名稱(../../x.html)" if code else "", 1,
                     period, "原因" if code else "", content, "10.0", "20.0", ""])
    return {"stat": "ok", "date": f"{start:%Y%m%d}~{end:%Y%m%d}",
            "tables": [{"fields": TPEX_DISPOSAL_FIELDS, "data": data}]}


def _cmode(day, *rows):
    data = [[code, "名稱", "Ｙ" if altered else "", "", "", "", "Ｙ" if suspended else "", "", "", ""]
            for code, altered, suspended in rows]
    return {"stat": "ok", "date": f"{day:%Y%m%d}", "tables": [{"fields": TPEX_CMODE_FIELDS, "data": data}]}


# ── parsers ─────────────────────────────────────────────────────


def test_twse_punish_parses_publication_and_fullwidth_period():
    frame = parse_twse_punish(_punish(("104/01/27", "3051", "104/01/28～104/02/10")))
    row = frame.row(0, named=True)
    assert (row["code"], row["exchange"], row["published_date"], row["period_start"],
            row["period_end"]) == ("3051", "TWSE", date(2015, 1, 27), date(2015, 1, 28),
                                   date(2015, 2, 10))
    assert len(row["content_hash"]) == 64


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(stat="很抱歉"),
    lambda p: p.update(fields=TWSE_PUNISH_FIELDS[:-1]),
    lambda p: p.update(total=99),
    lambda p: p["data"][0].__setitem__(6, "104/01/28"),
    lambda p: p["data"][0].__setitem__(6, "104/02/10～104/01/28"),
    lambda p: p["data"][0].__setitem__(2, ""),
])
def test_twse_punish_rejects_incomplete_or_changed_responses(mutate):
    payload = _punish(("104/01/27", "3051", "104/01/28～104/02/10"))
    mutate(payload)
    with pytest.raises(RegulatorySchemaError):
        parse_twse_punish(payload)


def test_tpex_disposal_skips_only_the_official_placeholder():
    start, end = date(2021, 1, 1), date(2021, 1, 31)
    frame = parse_tpex_disposal(_disposal(
        start, end, ("110/01/29", "", ""), ("110/01/06", "1593", "110/01/07~110/01/20")), start, end)
    assert frame["code"].to_list() == ["1593"]
    assert frame["period_end"][0] == date(2021, 1, 20)
    broken = _disposal(start, end, ("110/01/06", "", ""))
    broken["tables"][0]["data"][0][7] = "其他內容"
    with pytest.raises(RegulatorySchemaError):
        parse_tpex_disposal(broken, start, end)
    with pytest.raises(RegulatorySchemaError):
        parse_tpex_disposal(_disposal(start, end), date(2021, 2, 1), date(2021, 2, 28))


def test_tpex_cmode_flags_and_empty_list_is_not_evidence():
    day = date(2024, 6, 3)
    frame = parse_tpex_cmode(_cmode(day, ("4712", False, True), ("2724", True, False)), day)
    assert frame.filter(pl.col("suspended"))["code"].to_list() == ["4712"]
    assert frame.filter(pl.col("altered_trading"))["code"].to_list() == ["2724"]
    with pytest.raises(RegulatorySchemaError):
        parse_tpex_cmode(_cmode(day), day)
    with pytest.raises(RegulatorySchemaError):
        parse_tpex_cmode(_cmode(date(2024, 6, 4), ("4712", False, True)), day)


# ── storage ─────────────────────────────────────────────────────


def test_revision_is_kept_as_conflict_and_stops_counting(tmp_path):
    store = RegulatoryHistoryStore(tmp_path)
    first = parse_twse_punish(_punish(("104/01/27", "3051", "104/01/28～104/02/10")))
    assert store.write_month("twse_punish", 2015, 1, first, META) == "written"
    assert store.write_month("twse_punish", 2015, 1, first, META) == "unchanged"
    revised = parse_twse_punish(_punish(("104/01/27", "3051", "104/01/28～104/02/11")))
    before = store.digest()
    assert store.write_month("twse_punish", 2015, 1, revised, META) == "conflict"
    assert (2015, 1) not in store.covered_months("twse_punish")
    assert store.conflicts() and store.digest() != before
    # The first observation is never overwritten.
    assert pl.read_parquet(store.month_path("twse_punish", 2015, 1))["period_end"][0] == date(2015, 2, 10)


def test_backfill_records_only_valid_responses(tmp_path):
    store = RegulatoryHistoryStore(tmp_path)
    responses = {
        "twse": _punish(("104/01/27", "3051", "104/01/28～104/02/10")),
        "tpex": {"stat": "error"},
    }
    report = backfill_announcements(
        store, date(2015, 1, 1), date(2015, 1, 31),
        fetch=lambda url: responses["twse" if "twse" in url else "tpex"])
    assert report["written"] == 1 and len(report["errors"]) == 1
    assert store.covered_months("twse_punish") == {(2015, 1): date(2015, 1, 31)}
    assert store.covered_months("tpex_disposal") == {}
    day = date(2015, 1, 5)
    cmode = backfill_cmode(store, [day, date(2015, 1, 6)], fetch=lambda url: (
        _cmode(day, ("4415", False, True)) if "104/01/05" in url else _cmode(date(2015, 1, 6))))
    assert cmode["written"] == 1 and store.cmode_dates() == {day}


def test_month_fetched_before_it_closes_covers_only_what_was_published(tmp_path):
    store = _covered_store(tmp_path, cmode=[(date(2024, 6, 5), [("2724", True, False)]),
                                            (date(2024, 6, 12), [("2724", True, False)])])
    early = parse_twse_punish(_punish(("113/06/03", "2330", "113/06/04～113/06/17")))
    store.month_path("twse_punish", 2024, 6).unlink()
    assert store.write_month("twse_punish", 2024, 6, early, _meta("2024-06-10")) == "written"
    assert store.covered_months("twse_punish")[(2024, 6)] == date(2024, 6, 9)

    def blockers(source, target):
        return replay_regulatory_evidence(
            store, [(source, target)], terminations={}, terminations_retrieved=date(2026, 9, 26),
            observed_codes={})[0][target]

    assert blockers(date(2024, 6, 5), date(2024, 6, 6)) == ()
    assert blockers(date(2024, 6, 12), date(2024, 6, 13)) == ("regulatory_twse_disposition_unavailable",)
    # A later response that keeps every stored row extends the coverage.
    later = parse_twse_punish(_punish(("113/06/03", "2330", "113/06/04～113/06/17"),
                                      ("113/06/11", "2317", "113/06/12～113/06/25")))
    assert store.write_month("twse_punish", 2024, 6, later, _meta("2024-07-02")) == "refreshed"
    assert store.covered_months("twse_punish")[(2024, 6)] == date(2024, 6, 30)
    assert blockers(date(2024, 6, 12), date(2024, 6, 13)) == ()
    # A closed month is never rewritten; a response that drops a row is a conflict.
    assert store.write_month("twse_punish", 2024, 6, early, _meta("2024-08-01")) == "conflict"
    assert (2024, 6) not in store.covered_months("twse_punish")


def test_cmode_list_retrieved_on_its_own_date_is_not_final(tmp_path):
    store = RegulatoryHistoryStore(tmp_path)
    day = date(2024, 6, 3)
    frame = parse_tpex_cmode(_cmode(day, ("4712", False, True)), day)
    assert store.write_cmode(day, frame, _meta("2024-06-03")) == "written"
    assert store.cmode_dates() == set()
    grown = parse_tpex_cmode(_cmode(day, ("4712", False, True), ("2724", True, False)), day)
    assert store.write_cmode(day, grown, _meta("2024-06-04")) == "refreshed"
    assert store.cmode_dates() == {day}
    assert store.write_cmode(day, grown, _meta("2024-06-05")) == "unchanged"


# ── point-in-time replay ────────────────────────────────────────


def _covered_store(tmp_path, *announcements, cmode=()):
    store = RegulatoryHistoryStore(tmp_path)
    twse = [a for a in announcements if a[0] == "TWSE"]
    tpex = [a for a in announcements if a[0] == "TPEX"]
    for year, month in [(2024, m) for m in range(1, 13)]:
        start = date(year, month, 1)
        end = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
        store.write_month("twse_punish", year, month, parse_twse_punish(
            _punish(*[a[1:] for a in twse])), META)
        store.write_month("tpex_disposal", year, month, parse_tpex_disposal(
            _disposal(start, end, *[a[1:] for a in tpex]), start, end), META)
    for day, rows in cmode:
        store.write_cmode(day, parse_tpex_cmode(_cmode(day, *rows), day), META)
    return store


def test_disposition_counts_only_when_published_by_the_source_session(tmp_path):
    source, target = date(2024, 6, 3), date(2024, 6, 4)
    store = _covered_store(
        tmp_path,
        ("TWSE", "113/06/03", "2330", "113/06/04～113/06/17"),   # published at source: known
        ("TWSE", "113/06/04", "2317", "113/06/04～113/06/17"),   # published at entry: not known
        ("TPEX", "113/05/20", "8069", "113/05/21~113/06/03"),    # ended before entry
        ("TPEX", "113/05/31", "3131", "113/06/03~113/06/14"),
        cmode=[(source, [("4712", False, True), ("2724", True, False)])])
    blockers, excluded = replay_regulatory_evidence(
        store, [(source, target)], terminations={}, terminations_retrieved=date(2026, 9, 26),
        observed_codes={})
    assert blockers[target] == ()
    assert excluded[target] == {"2330", "3131", "4712"}


def test_missing_sources_are_named_blockers(tmp_path):
    source, target = date(2024, 6, 3), date(2024, 6, 4)
    store = _covered_store(tmp_path)
    blockers, _ = replay_regulatory_evidence(
        store, [(source, target)], terminations=None, terminations_retrieved=None,
        observed_codes={})
    assert blockers[target] == ("regulatory_termination_unavailable",
                                "regulatory_tpex_status_unavailable")
    early = date(2024, 1, 3)
    blockers, _ = replay_regulatory_evidence(
        store, [(date(2024, 1, 2), early)], terminations={},
        terminations_retrieved=date(2026, 9, 26), observed_codes={})
    # The 120-day lookback reaches into 2023, which was never fetched.
    assert "regulatory_twse_disposition_unavailable" in blockers[early]
    assert "regulatory_tpex_disposition_unavailable" in blockers[early]


def test_termination_follows_live_window_and_blocks_unprovable_publication(tmp_path):
    source, target = date(2024, 6, 3), date(2024, 6, 4)
    store = _covered_store(tmp_path, cmode=[(source, [("2724", True, False)])])
    terminations = {"6423": date(2024, 5, 30), "1701": date(2021, 12, 31),
                    "2888": date(2024, 6, 4), "5371": date(2024, 6, 4)}
    blockers, excluded = replay_regulatory_evidence(
        store, [(source, target)], terminations=terminations,
        terminations_retrieved=date(2026, 9, 26), observed_codes={source: frozenset({"2888"})})
    assert excluded[target] == {"6423"}  # 1701 is outside the two-calendar-year window
    assert blockers[target] == ("regulatory_publication_time_unproven",)
    stale = replay_regulatory_evidence(
        store, [(source, target)], terminations=terminations,
        terminations_retrieved=target, observed_codes={})[0]
    assert stale[target] == ("regulatory_termination_unavailable",)


def test_overlong_period_breaks_the_coverage_rule(tmp_path):
    store = _covered_store(tmp_path, ("TWSE", "113/01/02", "2330", "113/01/03～113/06/30"))
    with pytest.raises(RegulatorySchemaError):
        replay_regulatory_evidence(store, [(date(2024, 6, 3), date(2024, 6, 4))], terminations={},
                                   terminations_retrieved=date(2026, 9, 26), observed_codes={})


def test_identity_is_deterministic_and_tracks_every_response(tmp_path):
    one = _covered_store(tmp_path / "a", cmode=[(date(2024, 6, 3), [("4712", False, True)])])
    two = _covered_store(tmp_path / "b", cmode=[(date(2024, 6, 3), [("4712", False, True)])])
    assert one.digest() == two.digest()
    two.write_cmode(date(2024, 6, 4), parse_tpex_cmode(
        _cmode(date(2024, 6, 4), ("4712", False, True)), date(2024, 6, 4)), META)
    assert one.digest() != two.digest()

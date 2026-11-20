from datetime import date, timedelta
from unittest.mock import Mock

import polars as pl
import pytest

from app.taiwan.weekly_review_stats import SAMPLE_WARNING, build_weekly_review


def observation(day, *, symbol="2330.TWSE", strategy="trend_v1", pattern="trend",
                decision="signal", close=100, reason=None):
    return {
        "source_strategy": {"id": strategy, "name": strategy, "version": "v1", "rule_version": strategy},
        "signal_basis": "daily_strategy_weekly_snapshot", "week": day.strftime("%G-W%V"),
        "last_trading_day": day.isoformat(), "symbol": symbol, "pattern_tag": pattern,
        "decision": decision, "reference_close": close, "reason": reason,
    }


def outcomes(value=None, status="completed"):
    return {n: {"status": status, "return_pct": value} for n in (1, 4, 8, 13)}


def test_group_by_last_selected_iso_week_including_year_boundary(tmp_path):
    first = date(2025, 12, 26)
    dates = [first + timedelta(weeks=n) for n in (0, 1, 2, 4, 7)]
    rows = [observation(d, close=100 + i) for i, d in enumerate(dates)]
    rows.extend([rows[0].copy(), observation(first, pattern="breakout"), observation(first, strategy="other_v1")])
    path = tmp_path / "weekly.parquet"
    pl.DataFrame(list(reversed(rows))).write_parquet(path)
    evaluate = Mock(return_value=outcomes(3.0))
    report = build_weekly_review(pl.read_parquet(path).to_dicts(), evaluate)
    trend_groups = [g for g in report["groups"] if g["strategy_id"] == "trend_v1" and g["pattern_tag"] == "trend"]
    assert [g["weeks"] for g in trend_groups] == [
        ["2025-W52", "2026-W01", "2026-W02", "2026-W04"], ["2026-W07"],
    ]
    assert trend_groups[0]["start_day"] == "2025-12-26"
    assert trend_groups[0]["reference_close"] == 100
    assert set(trend_groups[0]["outcomes"]) == {1, 4, 8, 13}
    assert report["duplicate_count"] == 1
    summaries = {s["strategy_id"]: s for s in report["strategies"]}
    assert (summaries["trend_v1"]["raw_signal_count"], summaries["trend_v1"]["group_count"]) == (6, 3)
    assert summaries["other_v1"]["group_count"] == 1
    assert evaluate.call_count == 2
    evaluate.assert_any_call("2330.TWSE", first, 100)
    evaluate.assert_any_call("2330.TWSE", dates[-1], 104)


def test_short_week_uses_iso_week_gap_and_configurable_merge_limit():
    short_week = date(2026, 10, 5)  # last session on Monday in this fixture
    friday = date(2026, 10, 23)
    rows = [observation(short_week), observation(friday)]
    assert len(build_weekly_review(rows, lambda *_: outcomes(0))["groups"]) == 1
    split = build_weekly_review(rows, lambda *_: outcomes(0), max_gap_weeks=1)
    assert [g["start_day"] for g in split["groups"]] == ["2026-10-05", "2026-10-23"]


def test_four_week_denominators_and_boundaries_preserve_neutral_returns():
    day = date(2026, 10, 2)
    values = {
        "1001.TWSE": outcomes(1), "1002.TWSE": outcomes(-1), "1003.TWSE": outcomes(0.75),
        "1004.TWSE": outcomes(None, "pending"), "1005.TWSE": outcomes(None, "unavailable"),
        "1006.TWSE": outcomes(8), "1007.TWSE": outcomes(7.999), "1008.TWSE": outcomes(None, "pending"),
        "1009.TWSE": outcomes(None, "unavailable"),
    }
    rows = [observation(day, symbol=symbol, decision="signal" if i < 5 else "no_signal",
                        reason=None if i < 5 else "daily_conditions_not_met")
            for i, symbol in enumerate(values)]
    rows.append(observation(day, symbol="1010.TWSE", decision="watch"))
    result = build_weekly_review(rows, lambda symbol, *_: values[symbol])
    stats = result["strategies"][0]
    assert stats["group_count"] == 5
    assert stats["completed_count"] == 3
    assert (stats["pending_count"], stats["unavailable_count"]) == (1, 1)
    assert (stats["win_count"], stats["loss_count"], stats["neutral_count"]) == (1, 1, 1)
    assert stats["win_rate_pct"] == pytest.approx(100 / 3)
    assert stats["mean_return_pct"] == stats["expectancy_pct"] == 0.25
    assert stats["max_loss_pct"] == -1
    assert stats["sample_warning"] == SAMPLE_WARNING
    assert stats["missed_count"] == 1
    assert (stats["no_signal_count"], stats["no_signal_completed_count"], stats["no_signal_pending_count"],
            stats["no_signal_unavailable_count"]) == (4, 2, 1, 1)
    assert result["missed"][0]["symbol"] == "1006.TWSE"
    assert result["missed"][0]["reason"] == "daily_conditions_not_met"
    adjusted = build_weekly_review(rows, lambda symbol, *_: values[symbol], neutral_pct=0.5, missed_pct=7.5)
    assert adjusted["strategies"][0]["win_count"] == 2
    assert adjusted["strategies"][0]["missed_count"] == 2


@pytest.mark.parametrize("size,warning", [(29, SAMPLE_WARNING), (30, None)])
def test_small_sample_warning_uses_completed_groups(size, warning):
    rows = [observation(date(2026, 10, 2), symbol=f"{1000+i}.TPEX") for i in range(size)]
    stats = build_weekly_review(rows, lambda *_: outcomes(2.0))["strategies"][0]
    assert stats["completed_count"] == size
    assert stats["sample_warning"] == warning
    assert stats["max_loss_pct"] == 0


def test_unmatured_groups_have_null_statistics_and_never_zero_returns():
    row = observation(date(2026, 10, 2))
    stats = build_weekly_review([row], lambda *_: outcomes(None, "pending"))["strategies"][0]
    assert stats["pending_count"] == 1 and stats["completed_count"] == 0
    for key in ("mean_return_pct", "expectancy_pct", "win_rate_pct", "max_loss_pct"):
        assert stats[key] is None
    with pytest.raises(ValueError, match="must be null"):
        build_weekly_review([row], lambda *_: outcomes(0, "pending"))


def test_conflicting_records_do_not_depend_on_load_order():
    row = observation(date(2026, 10, 2))
    with pytest.raises(ValueError, match="Conflicting"):
        build_weekly_review([row, {**row, "decision": "no_signal"}], lambda *_: outcomes(0))
    with pytest.raises(ValueError, match="ISO week"):
        build_weekly_review([{**row, "week": "2025-W05"}], lambda *_: outcomes(0))


def test_decimal_exchange_prices_on_thresholds_and_unmatured_missed():
    day = date(2026, 10, 2)
    pick = observation(day)
    skipped = observation(day, symbol="6488.TPEX", decision="no_signal")
    def evaluate(symbol, *_):
        value = (10.1 - 10) * 100 / 10 if symbol == pick["symbol"] else (37.8 - 35) * 100 / 35
        return outcomes(value)
    result = build_weekly_review([pick, skipped], evaluate)
    assert result["groups"][0]["classification"] == "WIN"
    assert result["strategies"][0]["missed_count"] == 1
    pending = build_weekly_review([skipped], lambda *_: outcomes(None, "pending"))
    assert pending["strategies"][0]["missed_count"] is None

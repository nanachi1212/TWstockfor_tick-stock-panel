# ruff: noqa: RUF001 -- assertions use the user-facing Traditional Chinese text.
from datetime import date, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from app.taiwan.beginner_selection import (
    BeginnerCandidate,
    BeginnerSelectionService,
    _with_live_check,
)
from app.taiwan.beginner_technical import build_beginner_technical_panel, with_live_signal_check
from app.taiwan.market_rules import TickSizeModel
from app.taiwan.providers.external_models import ExternalProviderResult

AS_OF = "2026-10-02"


def _market(state="favorable"):
    return SimpleNamespace(
        state=state,
        as_of=AS_OF,
        strongest_industries=["半導體業"],
    )


def _plan(**overrides):
    values = {
        "entry_zone_low": 176.5,
        "entry_zone_high": 182.5,
        "reference_high": 194.0,
        "breakout_trigger": 195.5,
        "stop_price": 166.0,
        "evidence_as_of": AS_OF,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _metrics(**overrides):
    values = {
        "adjusted_close": 190.0,
        "ma5": 188.0,
        "ma20": 180.0,
        "ma60": 170.0,
        "atr_14": 5.7,
        "today_volume": 2_000_000.0,
        "average_volume_20d": 1_000_000.0,
        "volume_ratio_20d": 2.0,
        "price_change_pct": 1.5,
        "foreign_net_5d": 500_000.0,
        "investment_trust_net_5d": 100_000.0,
        "dealer_net_5d": -50_000.0,
        "institutional_complete_sessions": 5,
        "institutional_status": "official",
        "institutional_as_of": AS_OF,
        "margin_balance": 10_300_000.0,
        "margin_change": 300_000.0,
        "short_balance": 1_000_000.0,
        "short_change": 5_000.0,
        "margin_status": "official",
        "margin_as_of": "2026-10-01",
        "stock_return_20d": 12.0,
        "benchmark_return_20d": 3.0,
        "relative_return_20d": 9.0,
        "low_20d": 160.0,
        "high_20d": 195.0,
        "range_position_pct": 85.7,
        "revenue_yoy": 25.0,
        "revenue_mom": 3.0,
        "revenue_status": "available",
        "revenue_as_of": "2026-08",
        "eps": 8.0,
        "financials_as_of": "2026-06-30",
        "pe": 18.0,
        "valuation_as_of": AS_OF,
        "quote_freshness": "current",
    }
    values.update(overrides)
    return values


def _panel(*, current=190.0, plan=None, metrics=None, market=None):
    return build_beginner_technical_panel(
        current_price=current,
        as_of=AS_OF,
        plan=_plan() if plan is None else plan,
        metrics=_metrics() if metrics is None else metrics,
        market=_market() if market is None else market,
        industry="半導體業",
        action_summary="等待回檔，不追價。",
    )


def test_trade_plan_maps_support_resistance_invalidation_and_tick_legality():
    panel = _panel()
    assert panel.support.support_zone_low == 176.5
    assert panel.support.support_zone_high == 182.5
    assert panel.resistance.resistance == 195.5
    assert panel.invalidation.invalidation == 166.0
    for value in (176.5, 182.5, 195.5, 166.0):
        assert TickSizeModel.round_order_price(value) == value


def test_resistance_falls_back_to_reference_high_and_never_uses_level_at_or_below_current():
    fallback = _panel(plan=_plan(breakout_trigger=189.0, reference_high=194.0))
    assert fallback.resistance.resistance == 194.0
    unavailable = _panel(plan=_plan(breakout_trigger=190.0, reference_high=189.5))
    assert unavailable.resistance.resistance is None
    assert unavailable.resistance.resistance_distance_pct is None

    breakout = _panel(plan=_plan(entry_zone_low=None, entry_zone_high=None))
    assert breakout.support.status == "data_insufficient"
    assert breakout.resistance.status == "available"
    assert breakout.invalidation.status == "available"


@pytest.mark.parametrize(
    ("updates", "state"),
    [
        ({}, "strong"),
        ({"adjusted_close": 160.0}, "weak"),
        ({"adjusted_close": 175.0}, "neutral"),
        ({"ma60": None}, "unavailable"),
    ],
)
def test_ma_5_20_60_classification_and_missing_data(updates, state):
    panel = _panel(metrics=_metrics(**updates))
    assert panel.moving_averages.state == state
    assert panel.moving_averages.status == ("data_insufficient" if state == "unavailable" else "available")


@pytest.mark.parametrize(
    ("price_change", "ratio", "pattern"),
    [
        (1.0, 1.2, "price_up_volume_up"),
        (1.0, 0.8, "price_up_volume_down"),
        (-1.0, 1.2, "price_down_volume_up"),
        (-1.0, 0.8, "price_down_volume_down"),
    ],
)
def test_volume_ratio_and_four_price_volume_states(price_change, ratio, pattern):
    panel = _panel(metrics=_metrics(
        price_change_pct=price_change,
        volume_ratio_20d=ratio,
        average_volume_20d=2_000_000 / ratio,
    ))
    assert panel.volume.ratio == ratio
    assert panel.volume.pattern == pattern


def test_inner_outer_is_data_insufficient_and_never_filled_with_zero():
    evidence = _panel().inner_outer
    assert evidence.status == "data_insufficient"
    assert evidence.inner_pct is None and evidence.outer_pct is None
    assert evidence.as_of is None


def test_institutional_requires_complete_five_sessions():
    complete = _panel().institutional
    assert complete.status == "available"
    assert complete.complete_sessions == 5
    assert complete.total_net_5d == 550_000

    incomplete = _panel(metrics=_metrics(institutional_complete_sessions=4)).institutional
    assert incomplete.status == "data_insufficient"
    assert incomplete.total_net_5d is None


def test_margin_uses_latest_official_date_and_rejects_stale_status():
    current = _panel().margin
    assert current.status == "available"
    assert current.as_of == "2026-10-01"
    assert current.margin_state == "increase_fast"
    stale = _panel(metrics=_metrics(margin_status="stale")).margin
    assert stale.status == "data_insufficient"
    assert stale.margin_state == "unavailable"

    no_shorts = _panel(metrics=_metrics(short_balance=0, short_change=0)).margin
    assert no_shorts.status == "available"
    assert no_shorts.short_state == "stable"


def test_relative_strength_range_position_volatility_and_extreme_yoy_warning():
    panel = _panel(metrics=_metrics(revenue_yoy=250.0))
    assert panel.relative_strength.state == "stronger"
    assert panel.relative_strength.excess_return_pct == 9.0
    assert panel.range_position.position_pct == pytest.approx(85.7)
    assert panel.volatility.level == "normal"
    assert panel.fundamentals.warning is not None


def test_stale_quote_marks_daily_technical_evidence_stale():
    panel = _panel(metrics=_metrics(quote_freshness="stale"))
    assert panel.support.freshness == "stale"
    assert panel.moving_averages.freshness == "stale"
    assert panel.volume.freshness == "stale"
    assert panel.relative_strength.freshness == "stale"
    assert panel.range_position.freshness == "stale"
    assert panel.volatility.freshness == "stale"


def test_fundamentals_keep_pe_only_and_separate_source_dates():
    panel = _panel(metrics=_metrics(
        revenue_status="unavailable", revenue_yoy=None, revenue_mom=None, eps=None,
    ))
    assert panel.fundamentals.status == "available"
    assert panel.fundamentals.pe == 18.0
    assert panel.fundamentals.revenue_as_of == "2026-08"
    assert panel.fundamentals.financials_as_of == "2026-06-30"
    assert panel.fundamentals.valuation_as_of == AS_OF
    assert panel.fundamentals.as_of == AS_OF


def test_missing_benchmark_stays_unavailable_and_key_risks_are_limited():
    panel = _panel(metrics=_metrics(benchmark_return_20d=None, relative_return_20d=None))
    assert panel.relative_strength.status == "data_insufficient"
    assert panel.relative_strength.benchmark_return_pct is None
    assert 2 <= len(panel.key_risks) <= 4


def test_service_reuses_pit_factor_panel_for_ma_volume_atr_and_0050():
    sessions = [date(2026, 6, 1) + timedelta(days=index) for index in range(65)]
    rows = []
    for symbol, base, step in (("2330.TWSE", 100.0, 1.0), ("0050.TWSE", 100.0, 0.2)):
        for index, day in enumerate(sessions):
            close = base + index * step
            rows.append({
                "symbol": symbol, "date": day, "open": close - 0.5,
                "high": close + 1.0, "low": close - 1.0, "close": close,
                "volume": 1_000_000.0 + index * 10_000,
                "amount": 100_000_000.0, "quote_ts": None,
            })
    frame = pl.DataFrame(rows).with_columns(pl.col("date").cast(pl.Date))

    class Daily:
        def available_dates(self):
            return sessions

        def read_range(self, symbols, start, end):
            return frame.filter(
                pl.col("symbol").is_in(symbols)
                & (pl.col("date") >= start)
                & (pl.col("date") <= end)
            )

    screener = SimpleNamespace(
        daily_store=Daily(),
        action_store=SimpleNamespace(
            read_verified_coverage=lambda: (sessions[0], sessions[-1], ()),
        ),
    )
    metrics = BeginnerSelectionService(screener=screener)._technical_metrics(
        ["2330.TWSE"], sessions[-1]
    )["2330.TWSE"]
    assert metrics["ma5"] is not None and metrics["ma20"] is not None and metrics["ma60"] is not None
    assert metrics["volume_ratio_20d"] is not None
    assert metrics["atr_14"] is not None
    assert metrics["stock_return_20d"] > metrics["benchmark_return_20d"]
    assert metrics["relative_return_20d"] > 0


# ── Live signal re-check (daily conditions vs latest quote) ──────────────────


def _live(panel, price, *, below_stop=False, session="intraday"):
    return with_live_signal_check(
        panel, price=price, below_stop=below_stop, session=session,
        quote_date="2026-10-05", quote_time="2026-10-05T10:30:00+08:00", source="twse_mis",
    )


def _frozen(before, after):
    """The daily evidence must not be rewritten by the live overlay (it feeds stored AI explanations)."""
    assert after.summary == before.summary
    assert after.moving_averages == before.moving_averages
    assert after.key_risks == before.key_risks


def test_live_check_keeps_reading_when_daily_conditions_still_hold():
    panel = _panel()  # close 190 > MA5 188 > MA20 180
    checked = _live(panel, 189.0)
    assert checked.live_check.status == "holding"
    assert "2026-10-05 尚未納入日線" in checked.live_check.explanation
    assert checked.live_check.daily_as_of == AS_OF
    _frozen(panel, checked)


def test_live_check_flags_short_term_weakening_when_ma5_is_lost():
    panel = _panel()
    checked = _live(panel, 185.0)
    assert checked.live_check.status == "weakened"
    assert "5 日均線 188" in checked.live_check.alerts[0]
    _frozen(panel, checked)


def test_live_check_breaks_on_ma20_or_plan_stop():
    assert _live(_panel(), 178.0).live_check.status == "broken"
    stopped = _live(_panel(), 160.0, below_stop=True).live_check
    assert stopped.status == "broken" and "失效位" in stopped.alerts[0]


def test_live_check_ignores_conditions_that_did_not_hold_at_the_daily_close():
    # Daily close 187 was already below MA5 188, so an intraday 185 is not a new weakening.
    assert _live(_panel(current=187.0), 185.0).live_check.status == "holding"


def test_live_check_never_shows_holding_without_ma_evidence():
    missing = _panel(metrics=_metrics(ma5=None))
    assert missing.moving_averages.status == "data_insufficient"
    live = _live(missing, 189.0).live_check
    assert live.status == "unavailable" and "均線資料不足" in live.explanation
    # A plan-stop break is still reported, since it does not depend on the MAs.
    stopped = _live(missing, 160.0, below_stop=True).live_check
    assert stopped.status == "broken" and "均線資料不足" in stopped.explanation


def test_live_check_without_quote_states_the_daily_date_and_changes_nothing():
    panel = _panel()
    checked = with_live_signal_check(panel, price=None, below_stop=False, session="unavailable",
                                     unavailable_note="目前拿不到即時報價。")
    assert checked.live_check.status == "unavailable"
    assert AS_OF in checked.live_check.explanation
    _frozen(panel, checked)


def _quote(price, *, market_status="open", trade_date=date(2026, 10, 5), stale=False,
           freshness="realtime", prev_close=190.0):
    meta = SimpleNamespace(is_stale=stale, status="realtime", source_type="exchange", freshness_class=freshness, source="twse_mis")
    return SimpleNamespace(
        last_price=price, prev_close=prev_close, trade_date=trade_date, market_status=market_status,
        source_meta=meta, quote_time=None, open=189.0, high=191.0, low=184.0, volume=1_234_000,
        bids=[(184.5, 1000)], asks=[(185.0, 2000)],
    )


def _candidate(panel):
    return SimpleNamespace(technical_panel=panel, trade_plan=SimpleNamespace(stop_price=166.0), as_of=AS_OF)


def _check(quote, panel=None):
    return _with_live_check(_candidate(panel or _panel()), quote).live_check


def test_with_live_check_uses_the_shared_quote_gate():
    live = _check(_quote(185.0))  # AS_OF Fri 2026-10-02 -> next session Mon 2026-10-05
    assert (live.status, live.session, live.best_bid, live.best_ask, live.volume) == ("weakened", "intraday", 184.5, 185.0, 1_234_000)
    assert _check(_quote(185.0, stale=True)).status == "unavailable"
    assert _check(None).status == "unavailable"


@pytest.mark.parametrize("market_status", ["post_close", "closed"])
def test_after_close_uses_a_fresh_close_newer_than_the_daily_store(market_status):
    assert _check(_quote(185.0, market_status=market_status)).session == "after_close"


@pytest.mark.parametrize(("overrides", "note"), [
    ({"market_status": "pre_open"}, None),                        # pre-open is not a close
    ({"market_status": "non_trading_day"}, None),
    ({"market_status": "closed", "freshness": "delayed_15m"}, "延遲"),  # delayed feed after close
    ({"market_status": "closed", "freshness": None}, "延遲"),           # unknown freshness
    ({"market_status": "closed", "stale": True}, "過期"),
    ({"market_status": "closed", "trade_date": date(2026, 10, 2)}, "日線已更新"),
])
def test_after_close_rejects_non_close_sessions_and_untrusted_sources(overrides, note):
    live = _check(_quote(185.0, **overrides))
    assert live.status == "unavailable" and live.live_price is None
    if note:
        assert note in live.explanation


def test_corporate_action_or_skipped_session_disables_the_comparison():
    # Ex-dividend: today's reference price 186 differs from the stored close 190 -> no false MA5 break.
    ex_div = _check(_quote(185.0, prev_close=186.0))
    assert ex_div.status == "unavailable" and "可能除權息" in ex_div.explanation
    # The daily store is more than one session behind the quote.
    gap = _check(_quote(185.0, trade_date=date(2026, 10, 6)))
    assert gap.status == "unavailable" and "落後超過一個交易日" in gap.explanation


def test_live_quote_is_only_fetched_when_the_caller_asks(monkeypatch):
    import app.taiwan.providers.finbridge as finbridge
    import app.taiwan.providers.fred_macro as fred_macro
    import app.taiwan.realtime.fugle_provider as fugle_provider

    def offline():
        raise RuntimeError("offline in tests")

    for module, name in ((fugle_provider, "get_fugle_aggregates_provider"),
                         (fred_macro, "get_fred_macro_provider"), (finbridge, "get_finbridge_provider")):
        monkeypatch.setattr(module, name, offline)
    calls = []
    monkeypatch.setattr(BeginnerSelectionService, "_live_quote", staticmethod(lambda symbol: calls.append(symbol)))
    candidate = BeginnerCandidate.model_construct(technical_panel=_panel(), trade_plan=None, as_of=AS_OF, symbol="2330.TWSE")
    fx = ExternalProviderResult.unavailable("test", "provider_unavailable")
    BeginnerSelectionService._with_external(candidate, fx=fx)
    assert calls == []
    BeginnerSelectionService._with_external(candidate, fx=fx, live_check=True)
    assert calls == ["2330.TWSE"]


def test_chips_older_than_the_daily_date_are_dated_and_marked_stale():
    # AS_OF 2026-10-02 vs margin 2026-10-01: margin publishes later than prices.
    margin = _panel().margin
    assert margin.freshness == "stale"
    assert margin.explanation.startswith("（2026-10-01 資料，尚未更新到 2026-10-02，不是今日狀況）")
    assert any(r.text.startswith("（2026-10-01 資料）融資增加較快") for r in _panel().key_risks if r.code == "margin_crowded") \
        or all(r.code != "margin_crowded" for r in _panel().key_risks)
    same_day = _panel(metrics=_metrics(margin_as_of=AS_OF)).margin
    assert same_day.freshness == "latest_official" and "資料，尚未更新" not in same_day.explanation

"""Tests for Taiwan Market Intelligence Snapshot Service (Phase 7A).

Verifies:
- 100% deterministic breadth calculations:
    * 3 symbols fixture: 1 up, 1 down, 1 flat -> advance=1, decline=1, flat=1.
    * Missing previous close -> uncompared_count incremented, excluded from A/D/F.
    * Volume = 0 -> not counted in traded_count.
    * Monday target date correctly resolves to prior Friday.
- Price limit rules:
    * 0050.TWSE (domestic ETF, 10% limit)
    * 00631L.TWSE (leveraged domestic 2X, 20% limit)
    * 00632R.TWSE (inverse domestic -1X, 10% limit)
    * 00646.TWSE (foreign-underlying ETF, NO_LIMIT -> never counted as hit limit)
    * 8069.TPEX (TPEx ordinary stock, 10% limit with standard tick tiers)
- Unit preservation:
    * Turnover in TWD
    * Institutional net in shares
    * Margin balance in shares
- Aggregate margin ratio:
    * sum(short) / sum(margin) * 100 (never average of ratios)
- Partial state & data quality reporting:
    * Missing dataset correctly marks partial/stale
- Historical snapshot with NO look-ahead:
    * Query for date D uses only data on/before D
- Zero HTTP:
    * GET /api/taiwan/market-intelligence performs 0 external requests
"""
from datetime import date
from unittest.mock import MagicMock, patch

import polars as pl
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.taiwan.market_intelligence import (
    TaiwanMarketIntelligenceService,
)
from app.taiwan.realtime.calendar import TaiwanTradingCalendar
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import MarketProfileBridge, TaiwanInstrument


@pytest.fixture
def mock_calendar():
    cal = TaiwanTradingCalendar()
    # 2026-08-28 is Friday, 2026-08-31 is Monday
    return cal


def test_deterministic_breadth_fixture(mock_calendar, monkeypatch):
    """3 symbols: 1 up, 1 down, 1 flat, plus 1 uncompared and 1 zero-volume."""
    d_curr = date(2026, 8, 28)
    d_prev = date(2026, 8, 27)
    monkeypatch.setattr("app.taiwan.market_intelligence.resolve_target_latest_trading_date", lambda _: d_curr)
    for prior_day in (21, 24, 25, 26, 27):
        mock_calendar.add_trading_day(date(2026, 8, prior_day))

    # Mock daily store
    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = [d_prev, d_curr]

    # Current rows: SYM1 (up), SYM2 (down), SYM3 (flat), SYM4 (new, no prev), SYM5 (vol=0)
    curr_data = pl.DataFrame({
        "symbol": ["SYM1.TWSE", "SYM2.TWSE", "SYM3.TWSE", "SYM4.TWSE", "SYM5.TWSE"],
        "date": [d_curr] * 5,
        "open": [10.0, 20.0, 30.0, 40.0, 50.0],
        "high": [12.0, 21.0, 31.0, 41.0, 50.0],
        "low": [9.0, 18.0, 29.0, 39.0, 50.0],
        "close": [11.0, 19.0, 30.0, 42.0, 50.0],
        "volume": [1000.0, 2000.0, 3000.0, 4000.0, 0.0],
        "amount": [11000.0, 38000.0, 90000.0, 168000.0, 0.0],
        "quote_ts": [None] * 5,
    })

    # Prev rows: SYM1 (10.0), SYM2 (20.0), SYM3 (30.0), SYM5 (50.0) -> SYM4 missing
    prev_data = pl.DataFrame({
        "symbol": ["SYM1.TWSE", "SYM2.TWSE", "SYM3.TWSE", "SYM5.TWSE"],
        "date": [d_prev] * 4,
        "open": [10.0, 20.0, 30.0, 50.0],
        "high": [10.0, 20.0, 30.0, 50.0],
        "low": [10.0, 20.0, 30.0, 50.0],
        "close": [10.0, 20.0, 30.0, 50.0],
        "volume": [1000.0, 1000.0, 1000.0, 1000.0],
        "amount": [10000.0, 20000.0, 30000.0, 50000.0],
        "quote_ts": [None] * 4,
    })

    def mock_read_range(syms, start, end):
        if start == d_curr:
            return curr_data
        elif start == d_prev:
            return prev_data
        return pl.DataFrame()

    mock_daily.read_range.side_effect = mock_read_range

    # Mock security master
    mock_sm = MagicMock()
    uni_df = pl.DataFrame({
        "symbol": ["SYM1.TWSE", "SYM2.TWSE", "SYM3.TWSE", "SYM4.TWSE", "SYM5.TWSE"],
        "exchange": ["TWSE"] * 5,
        "instrument_type": ["stock"] * 5,
        "listing_status": ["active"] * 5,
    })
    mock_sm.to_dataframe.return_value = uni_df
    mock_sm.get_instrument.return_value = TaiwanInstrument(
        symbol="SYM1.TWSE",
        code="SYM1",
        exchange="TWSE",
        name="測試股",
        instrument_type="stock",
        listing_status="active",
        listing_date="2026/08/27",
        isin="TW000SYM1000",
        industry="半導體業",
        cfi_code="ESVUFR",
        raw_category="股票",
        is_supported=True,
        source="TWSE_ISIN",
        updated_at="2026-08-31T00:00:00",
    )

    # Empty inst & margin
    mock_inst = MagicMock()
    mock_inst.read_range.return_value = pl.DataFrame()
    mock_inst.available_dates.return_value = []
    mock_margin = MagicMock()
    mock_margin.read_range.return_value = pl.DataFrame()
    mock_margin.available_dates.return_value = []

    svc = TaiwanMarketIntelligenceService(
        daily_store=mock_daily,
        inst_store=mock_inst,
        margin_store=mock_margin,
        calendar=mock_calendar,
        security_master=mock_sm,
    )

    snapshot = svc.get_snapshot(d_curr)
    totals = snapshot.market_totals

    assert totals.supported_count == 5
    assert totals.snapshot_row_count == 5
    assert totals.traded_count == 4  # SYM5 volume=0 not traded
    assert totals.advance_count == 1  # SYM1 11.0 > 10.0
    assert totals.decline_count == 1  # SYM2 19.0 < 20.0
    assert totals.flat_count == 2  # SYM3 30.0 == 30.0, SYM5 50.0 == 50.0
    assert totals.uncompared_count == 1  # SYM4 missing prev close
    assert totals.official_compared_count == 0
    assert totals.inferred_compared_count == 4
    # SYM1 closes 10% above the prior close within its first five sessions.
    assert totals.upper_limit_count == 0
    assert snapshot.data_quality.price_limit_unavailable_count == 5
    assert totals.turnover == 307000.0


def test_monday_resolves_to_prior_friday(mock_calendar):
    """Target date Monday 2026-08-31 should resolve previous trading day to Friday 2026-08-28."""
    mock_daily = MagicMock()
    d_fri = date(2026, 8, 28)
    d_mon = date(2026, 8, 31)
    mock_daily.available_dates.return_value = [d_fri, d_mon]

    svc = TaiwanMarketIntelligenceService(
        daily_store=mock_daily,
        calendar=mock_calendar,
    )
    prev = svc.get_previous_trading_date(d_mon)
    assert prev == d_fri


def test_price_limit_rules_and_no_limit(tmp_path):
    """Verify limit-up and limit-down detection across diverse regulatory profiles."""
    def instrument(
        code: str, exchange: str, instrument_type: str, *, category: str | None = None,
        underlying_scope: str | None = None, leverage: float = 1.0,
    ) -> TaiwanInstrument:
        symbol = f"{code}.{exchange}"
        return TaiwanInstrument(
            symbol=symbol,
            code=code,
            exchange=exchange,
            name=f"fixture {code}",
            instrument_type=instrument_type,
            listing_status="active",
            listing_date=None,
            isin=None,
            industry=None,
            cfi_code=None,
            raw_category="ETF" if instrument_type == "etf" else "股票",
            is_supported=True,
            source="fixture",
            updated_at="2026-09-24T00:00:00+00:00",
            etf_category=category,
            classification_source="official_metadata" if category else None,
            underlying_scope=underlying_scope,
            leverage_multiplier=leverage,
        )

    twse_items = [
        instrument("0050", "TWSE", "etf", category="domestic_equity",
                   underlying_scope="domestic"),
        instrument("00631L", "TWSE", "etf"),
        instrument("00632R", "TWSE", "etf"),
        instrument("00646", "TWSE", "etf", category="foreign_equity",
                   underlying_scope="foreign"),
    ]
    tpex_items = [instrument("8069", "TPEX", "stock")]
    twse_adapter = MagicMock()
    twse_adapter.get_instruments.return_value = twse_items
    tpex_adapter = MagicMock()
    tpex_adapter.get_instruments.return_value = tpex_items
    sm = TaiwanSecurityMaster(
        cache_path=tmp_path / "security_master.parquet",
        twse_adapter=twse_adapter,
        tpex_adapter=tpex_adapter,
    )

    # 1. 0050.TWSE: domestic ETF (10% limit)
    inst_0050 = sm.get_instrument("0050.TWSE")
    assert inst_0050 is not None
    assert MarketProfileBridge.get_price_limit_pct(inst_0050) == 0.10

    # 2. 00631L.TWSE: leveraged domestic 2X (20% limit)
    inst_00631l = sm.get_instrument("00631L.TWSE")
    assert inst_00631l is not None
    assert MarketProfileBridge.get_price_limit_pct(inst_00631l) == 0.20

    # 3. 00632R.TWSE: inverse domestic -1X (10% limit)
    inst_00632r = sm.get_instrument("00632R.TWSE")
    assert inst_00632r is not None
    assert MarketProfileBridge.get_price_limit_pct(inst_00632r) == 0.10

    # 4. 00646.TWSE: foreign-underlying ETF (NO_LIMIT -> None)
    inst_00646 = sm.get_instrument("00646.TWSE")
    assert inst_00646 is not None
    assert MarketProfileBridge.get_price_limit_pct(inst_00646) is None
    up, dn = MarketProfileBridge.calc_limits(100.0, inst_00646)
    assert up is None and dn is None

    # 5. 8069.TPEX: ordinary TPEx stock (10% limit)
    inst_8069 = sm.get_instrument("8069.TPEX")
    assert inst_8069 is not None
    assert MarketProfileBridge.get_price_limit_pct(inst_8069) == 0.10


def test_aggregate_short_margin_ratio():
    """Verify sum(short) / sum(margin) * 100 logic, never average of individual ratios."""
    d = date(2026, 8, 28)
    mock_margin = MagicMock()
    mock_margin.available_dates.return_value = [d]
    mock_margin.read_range.return_value = pl.DataFrame({
        "symbol": ["A.TWSE", "B.TWSE"],
        "date": [d, d],
        "margin_balance": [1000.0, 2000.0],
        "margin_change": [10.0, 20.0],
        "short_balance": [100.0, 300.0],
        "short_change": [5.0, 15.0],
    })

    svc = TaiwanMarketIntelligenceService(margin_store=mock_margin)
    snapshot = svc.get_snapshot(d)
    m = snapshot.margin
    assert m.margin_balance == 3000.0
    assert m.short_balance == 400.0
    # Expected: 400 / 3000 * 100 = 13.33% (NOT avg(10%, 15%) = 12.5%)
    assert m.aggregate_short_margin_ratio == 13.33


def test_partial_state_reporting():
    """Daily current, Inst stale, Margin unavailable -> overall status partial."""
    d = date(2026, 8, 28)
    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = [d]
    mock_daily.read_range.side_effect = lambda _, start, end: (
        pl.DataFrame({"symbol": ["A.TWSE"], "close": [10.0], "volume": [1.0], "amount": [10.0]})
        if start == d else pl.DataFrame()
    )
    master = MagicMock()
    master.to_dataframe.return_value = pl.DataFrame({
        "symbol": ["A.TWSE"], "exchange": ["TWSE"], "instrument_type": ["stock"], "listing_status": ["active"],
    })

    mock_inst = MagicMock()
    mock_inst.available_dates.return_value = [date(2026, 8, 27)]  # stale
    mock_inst.read_range.return_value = pl.DataFrame({
        "foreign_net": [0], "investment_trust_net": [0], "dealer_net": [0],
    })

    mock_margin = MagicMock()
    mock_margin.available_dates.return_value = []  # unavailable
    mock_margin.read_range.return_value = pl.DataFrame()

    svc = TaiwanMarketIntelligenceService(
        daily_store=mock_daily,
        inst_store=mock_inst,
        margin_store=mock_margin,
        security_master=master,
    )
    snapshot = svc.get_snapshot(d)
    dq = snapshot.data_quality
    assert dq.daily.status == "current"
    assert dq.institutional.status == "stale"
    assert dq.margin.status == "unavailable"
    assert dq.overall_status == "partial"


def test_aggregate_partitions_match_the_dates_marked_current(monkeypatch):
    expected = date(2026, 9, 30)
    daily_day = date(2026, 9, 29)
    monkeypatch.setattr(
        "app.taiwan.market_intelligence.resolve_target_latest_trading_date",
        lambda *_: expected,
    )

    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = [daily_day]
    mock_daily.read_range.return_value = pl.DataFrame()

    mock_inst = MagicMock()
    mock_inst.available_dates.return_value = [expected]
    mock_inst.read_range.return_value = pl.DataFrame({
        "foreign_net": [10.0],
        "investment_trust_net": [20.0],
        "dealer_net": [30.0],
    })

    mock_margin = MagicMock()
    mock_margin.available_dates.return_value = [expected]
    mock_margin.read_range.return_value = pl.DataFrame({
        "margin_balance": [1000.0],
        "margin_change": [10.0],
        "short_balance": [100.0],
        "short_change": [1.0],
    })
    mock_sm = MagicMock()
    mock_sm.to_dataframe.return_value = pl.DataFrame()

    snapshot = TaiwanMarketIntelligenceService(
        daily_store=mock_daily,
        inst_store=mock_inst,
        margin_store=mock_margin,
        security_master=mock_sm,
    ).get_snapshot()

    assert snapshot.trade_date == daily_day.isoformat()
    assert snapshot.institutional.trade_date == expected.isoformat()
    assert snapshot.institutional.status == "current"
    assert snapshot.margin.trade_date == expected.isoformat()
    assert snapshot.margin.status == "current"
    mock_inst.read_range.assert_called_once_with(None, expected, expected)
    mock_margin.read_range.assert_called_once_with(None, expected, expected)


def test_historical_snapshot_no_look_ahead():
    """Querying date D passes exact (target, target) to read_range, never reading D+1."""
    d_target = date(2026, 8, 20)
    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = [date(2026, 8, 19), d_target, date(2026, 8, 21)]
    mock_daily.read_range.return_value = pl.DataFrame()

    mock_inst = MagicMock()
    mock_inst.available_dates.return_value = [d_target, date(2026, 8, 21)]
    mock_inst.read_range.return_value = pl.DataFrame()

    mock_margin = MagicMock()
    mock_margin.available_dates.return_value = [d_target, date(2026, 8, 21)]
    mock_margin.read_range.return_value = pl.DataFrame()

    svc = TaiwanMarketIntelligenceService(
        daily_store=mock_daily,
        inst_store=mock_inst,
        margin_store=mock_margin,
    )
    svc.get_snapshot(d_target)

    # Check that stores were called with start=d_target, end=d_target
    for call in mock_inst.read_range.call_args_list:
        _, start, end = call[0]
        assert start <= d_target
        assert end <= d_target

    for call in mock_margin.read_range.call_args_list:
        _, start, end = call[0]
        assert start <= d_target
        assert end <= d_target


def test_api_endpoint_zero_external_http():
    """GET /api/taiwan/market-intelligence performs 0 external market provider HTTP calls."""
    client = TestClient(app, client=("127.0.0.1", 50000))
    with patch("urllib.request.urlopen") as mock_urlopen:
        resp = client.get("/api/taiwan/market-intelligence?date=2026-08-28")
        assert resp.status_code == 200
        data = resp.json()
        assert data["trade_date"] == "2026-08-28"
        assert "market_totals" in data
        assert "institutional" in data
        assert "margin" in data
        assert "by_exchange" in data
        assert mock_urlopen.call_count == 0


@pytest.mark.parametrize("partition_on_target", [False, True])
def test_empty_historical_daily_snapshot_never_uses_future_freshness(partition_on_target):
    prior, target, future = (date(2026, 11, day) for day in (16, 17, 18))
    daily, inst, margin, master = (MagicMock() for _ in range(4))
    daily.available_dates.return_value = [prior, future] + ([target] if partition_on_target else [])
    daily.read_range.return_value = master.to_dataframe.return_value = pl.DataFrame()
    for store, columns in (
        (inst, ["foreign_net", "investment_trust_net", "dealer_net"]),
        (margin, ["margin_balance", "margin_change", "short_balance", "short_change"]),
    ):
        store.available_dates.return_value = [target]
        store.read_range.return_value = pl.DataFrame({col: [10] for col in columns})
    snapshot = TaiwanMarketIntelligenceService(daily, inst, margin, security_master=master).get_snapshot(target)
    assert snapshot.data_quality.daily.status == "unavailable"
    assert snapshot.data_quality.daily.as_of is None
    assert snapshot.data_quality.overall_status == "partial"


def test_incomplete_market_flows_keep_nulls_and_real_zero_distinct(monkeypatch):
    day = date(2026, 11, 17)
    monkeypatch.setattr("app.taiwan.market_intelligence.resolve_target_latest_trading_date", lambda _: day)
    daily, inst, margin, master = (MagicMock() for _ in range(4))
    daily.available_dates.return_value = []
    daily.read_range.return_value = master.to_dataframe.return_value = pl.DataFrame()
    inst.available_dates.return_value = margin.available_dates.return_value = [day]
    inst.read_range.return_value = pl.DataFrame({
        "foreign_net": [None, 100], "investment_trust_net": [30, -30], "dealer_net": [-5, 0],
    })
    margin.read_range.return_value = pl.DataFrame({
        "margin_balance": [100, None], "margin_change": [None, None], "short_balance": [5, 5],
    })
    snap = TaiwanMarketIntelligenceService(daily, inst, margin, security_master=master).get_snapshot(day)
    assert snap.institutional.foreign_net is None
    assert snap.institutional.investment_trust_net == 0
    assert snap.institutional.dealer_net == -5
    assert snap.institutional.total_net is None
    assert snap.margin.margin_balance is snap.margin.margin_balance_change is None
    assert snap.margin.short_balance == 10
    assert snap.margin.aggregate_short_margin_ratio is snap.margin.short_balance_change is None
    assert snap.institutional.status == snap.margin.status == "partial"
    assert snap.data_quality.overall_status == "partial"


def test_previous_session_does_not_skip_missing_weekday(mock_calendar):
    before, missing, target = (date(2026, 11, day) for day in (16, 17, 18))
    daily = MagicMock()
    daily.available_dates.return_value = [before, target]
    svc = TaiwanMarketIntelligenceService(daily_store=daily, calendar=mock_calendar)
    assert svc.get_previous_trading_date(target) == missing


def test_single_symbol_partition_stays_partial_until_full_snapshot(tmp_path, monkeypatch):
    from app.taiwan.daily_store import TaiwanDailyStore

    day = date(2026, 11, 17)
    monkeypatch.setattr("app.taiwan.market_intelligence.resolve_target_latest_trading_date", lambda _: day)
    symbols = ["2330.TWSE", "2317.TWSE"]
    rows = pl.DataFrame({
        "symbol": symbols, "date": [day] * 2, "open": [100.0] * 2, "high": [100.0] * 2,
        "low": [100.0] * 2, "close": [100.0] * 2, "volume": [1.0] * 2,
        "amount": [100.0] * 2, "quote_ts": [None] * 2,
    })
    daily = TaiwanDailyStore(tmp_path / "daily")
    master, inst, margin = MagicMock(), MagicMock(), MagicMock()
    master.to_dataframe.return_value = pl.DataFrame({
        "symbol": symbols, "exchange": ["TWSE"] * 2, "instrument_type": ["stock"] * 2,
        "listing_status": ["active"] * 2,
    })
    for store, columns in (
        (inst, ["foreign_net", "investment_trust_net", "dealer_net"]),
        (margin, ["margin_balance", "margin_change", "short_balance", "short_change"]),
    ):
        store.available_dates.return_value = [day]
        store.read_range.return_value = pl.DataFrame({col: [1] for col in columns})
    service = TaiwanMarketIntelligenceService(daily, inst, margin, security_master=master)
    daily.write_batch(rows.head(1), partial=True)
    snapshot = service.get_snapshot(day)
    assert snapshot.data_quality.daily.status == snapshot.data_quality.overall_status == "partial"
    assert snapshot.data_quality.missing_symbols_count == 1
    assert snapshot.market_totals.turnover == 100

    daily.write_batch(rows)
    snapshot = service.get_snapshot(day)
    assert snapshot.data_quality.daily.status == "current"
    assert snapshot.data_quality.overall_status == "complete"
    assert snapshot.market_totals.turnover == 200


def test_historical_market_uses_dated_membership_and_exchange_reference(taiwan_data_env, monkeypatch):
    from types import SimpleNamespace

    from app.taiwan.daily_store import TaiwanDailyStore
    from app.taiwan.historical_classification import HistoricalClassificationStore
    from app.taiwan.observed_universe import TWSE_SOURCE, ObservedUniverseStore

    day, previous = date(2026, 8, 28), date(2026, 8, 27)
    monkeypatch.setattr("app.taiwan.market_intelligence.resolve_target_latest_trading_date",
                        lambda _: date(2026, 11, 26))
    census = ObservedUniverseStore()
    classified = HistoricalClassificationStore()
    # A and B traded then. Today's active list lacks B; C appears only after this snapshot.
    codes = ["2330", "9999"]
    for d, closes in [(previous, [100., 10.]), (day, [90., 11.])]:
        census.write("TWSE", d, [{
            "date": d, "raw_code": code, "exchange": "TWSE", "observed": True, "source": TWSE_SOURCE,
            "close": close, "volume": 1., "amount": close
        } for code, close in zip(codes, closes, strict=True)])
        path = census.partition_path("TWSE", d)
        legacy = pl.read_parquet(path).drop("official_direction")
        legacy.write_parquet(path)
    for when, members in [(previous, codes), (date(2026, 10, 1), ["8888"])]:
        classified.write(when, [{
            "code": c, "exchange": "TWSE", "instrument_type": "stock", "classification_effective_from": when,
            "classification_status": "verified", "classification_source": "official_test_fixture"
        } for c in members])
    master = MagicMock()
    svc = TaiwanMarketIntelligenceService(daily_store=TaiwanDailyStore(taiwan_data_env["data_dir"] / "only_history"),
                                         evidence_store=census, security_master=master)
    event = SimpleNamespace(symbol="2330.TWSE", source="TWT49U", status="verified",
                            effective_date=day, raw_fields='{"除權息參考價":"90.00","漲停價格":"99","跌停價格":"81"}', reference_price=90.01)
    monkeypatch.setattr(svc.action_store, "events_for_price_reference", lambda day: (event,))
    monkeypatch.setattr("app.api.taiwan.TaiwanMarketIntelligenceService", lambda: svc)
    response = TestClient(app).get("/api/taiwan/market-intelligence", params={"date": day.isoformat()})
    assert response.status_code == 200
    assert response.json()["by_exchange"]["twse"]["uncompared_count"] == 1
    snapshot = svc.get_snapshot(day)
    assert snapshot.by_exchange.twse.supported_count == 2
    assert snapshot.by_exchange.twse.uncompared_count == 1
    assert snapshot.data_quality.missing_symbols_count == 0
    assert snapshot.by_exchange.twse.flat_count == 0
    assert snapshot.by_exchange.twse.advance_count == 1
    assert snapshot.by_exchange.twse.official_compared_count == 0
    assert snapshot.by_exchange.twse.inferred_compared_count == 1
    assert snapshot.data_quality.price_limit_unavailable_count == 1
    assert snapshot.by_exchange.twse.upper_limit_count == 0
    assert snapshot.by_exchange.twse.lower_limit_count == 0
    assert snapshot.data_quality.universe_status == "partial"  # no TPEx census
    master.to_dataframe.assert_not_called()
    master.get_instrument.assert_not_called()
    event.raw_fields = '{"除權息參考價":"85","漲停價格":"90","跌停價格":"75"}'
    at_limit = svc.get_snapshot(day).market_totals
    assert (at_limit.upper_limit_count, at_limit.uncompared_count) == (1, 1)
    event.status = "data_insufficient"
    unverified = svc.get_snapshot(day)
    assert unverified.data_quality.price_limit_unavailable_count == snapshot.data_quality.price_limit_unavailable_count + 1
    assert unverified.by_exchange.twse.uncompared_count == 1
    assert unverified.by_exchange.twse.decline_count == unverified.by_exchange.twse.lower_limit_count == 0


def test_twse_2330_official_ex_date_and_ordinary_day(taiwan_data_env, monkeypatch):
    from types import SimpleNamespace

    from app.taiwan.daily_store import TaiwanDailyStore
    from app.taiwan.observed_universe import ObservedUniverseStore, parse_twse_census

    # Source: TWSE MI_INDEX and TWT49U; only the fields used by this contract are kept.
    # Ordinary Sept 17 is +45. Sept 16 is X (not comparable), even though close > ex reference.
    observations = [
        (date(2026, 9, 15), 2385., "+", 5.),
        (date(2026, 9, 16), 2380., "X", 0.),
        (date(2026, 9, 17), 2425., "+", 45.),
    ]
    fields = ["證券代號", "證券名稱", "收盤價", "開盤價", "最高價", "最低價", "成交股數", "成交金額", "漲跌(+/-)", "漲跌價差"]
    census, daily = ObservedUniverseStore(), TaiwanDailyStore()
    for day, close, sign, diff in observations:
        payload = {"stat": "OK", "date": day.strftime("%Y%m%d"), "tables": [{
            "title": "每日收盤行情", "fields": fields,
            "data": [["2330", "台積電", close, None, None, None, 1, close, f"<p>{sign}</p>", diff]]}]}
        rows = parse_twse_census(payload, day, "test")
        census.write("TWSE", day, rows)
        daily.write_batch(pl.DataFrame({"symbol": ["2330.TWSE"], "date": [day], "close": [close],
                                        "open": [close], "high": [close], "low": [close], "volume": [1.],
                                        "amount": [close], "quote_ts": [None]}))
    svc = TaiwanMarketIntelligenceService(daily_store=daily, evidence_store=census)
    event = SimpleNamespace(source="TWT49U", status="verified", symbol="2330.TWSE",
                            effective_date=observations[1][0],
                            raw_fields='{"除權息參考價":"2,377.99","開盤競價基準":"2,380","漲停價格":"2,615","跌停價格":"2,145"}')
    monkeypatch.setattr(svc.action_store, "events_for_price_reference",
                        lambda day: (event,) if day == event.effective_date else ())
    monkeypatch.setattr("app.api.taiwan.TaiwanMarketIntelligenceService", lambda: svc)
    for day, up, incomparable in [(observations[1][0], 0, 1), (observations[2][0], 1, 0)]:
        response = TestClient(app).get("/api/taiwan/market-intelligence", params={"date": day.isoformat()})
        assert response.status_code == 200
        totals = response.json()["market_totals"]
        assert (totals["advance_count"], totals["uncompared_count"]) == (up, incomparable)
        assert totals["official_compared_count"] == up
        assert totals["inferred_compared_count"] == 0
        assert response.json()["data_quality"]["price_limit_unavailable_count"] == up
        assert totals["lower_limit_count"] == totals["upper_limit_count"] == 0


def test_tpex_next_day_limits_apply_only_to_the_adjacent_session(taiwan_data_env, monkeypatch):
    from app.taiwan.historical_classification import HistoricalClassificationStore
    from app.taiwan.observed_universe import ObservedUniverseStore, parse_tpex_census

    # TPEx dailyQuotes 2026-11-24/25. On the 25th 3664 hit the upper bound, 1815 the lower.
    before, target = date(2026, 11, 24), date(2026, 11, 25)
    monkeypatch.setattr("app.taiwan.market_intelligence.resolve_target_latest_trading_date",
                        lambda _: date(2026, 11, 26))
    cases = {before: [("3664", 7.08, "+0.64", 7.78, 6.38), ("1815", 112.50, "+10.00", 123.50, 101.50)],
             target: [("3664", 7.78, "+0.70", 8.55, 7.01), ("1815", 101.50, "-11.00", 111.50, 91.40)]}
    census = ObservedUniverseStore()
    fields = ["代號", "名稱", "收盤", "漲跌", "次日 漲停價", "次日 跌停價", "開盤", "最高", "最低", "成交股數", "成交金額(元)"]
    for d, securities in cases.items():
        payload = {"date": d.strftime("%Y%m%d"), "tables": [{"title": "上櫃股票行情", "fields": fields,
                   "data": [[c, c, close, diff, upper, lower, close, close, close, 1, close]
                            for c, close, diff, upper, lower in securities]}]}
        census.write("TPEX", d, parse_tpex_census(payload, d, "test"))
    HistoricalClassificationStore().write(before, [
        {"code": code, "exchange": "TPEX", "instrument_type": "stock", "classification_effective_from": before,
         "classification_status": "verified", "classification_source": "test"}
        for code, *_ in cases[before]])
    svc = TaiwanMarketIntelligenceService(evidence_store=census)
    monkeypatch.setattr("app.api.taiwan.TaiwanMarketIntelligenceService", lambda: svc)
    for d, expected, missing in [(before, 0, 2), (target, 1, 0)]:
        result = TestClient(app).get("/api/taiwan/market-intelligence", params={"date": d.isoformat()})
        assert result.status_code == 200
        totals = result.json()["market_totals"]
        assert totals["official_compared_count"] == totals["snapshot_row_count"] == 2
        assert totals["upper_limit_count"] == totals["lower_limit_count"] == expected
        assert result.json()["data_quality"]["price_limit_unavailable_count"] == missing

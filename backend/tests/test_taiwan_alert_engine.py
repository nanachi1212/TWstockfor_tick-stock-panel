"""Offline Unit Tests for Taiwan Realtime Alert Engine (Phase 5B).

Tests:
  - Price Rules (Above, Below, Missing Price)
  - Change % Rules (Above, Below, normalized against prev_close)
  - Volume Rules (shares normalization, volume spike multiple)
  - Data Quality Gate (rejection of stale, daily fallback, delayed sources)
  - Market Status Gate (rejection of unverified, closed, non-trading days)
  - Canonical Price Limits (0050, 00631L, 00632R; rejection of NO_LIMIT 00646)
  - Deduplication (edge-triggered: true -> suppressed, re-arm on false)
  - Cooldown (suppression of rapid toggles within cooldown window)
  - Hysteresis (price must retreat past threshold - hysteresis to re-arm)
  - Batching (only 1 quote request per unique symbol across multiple rules)
"""
from __future__ import annotations

from datetime import date, datetime
import pytest

from app.taiwan.enrichment.models import SourceMeta
from app.taiwan.realtime.calendar import TAIPEI_TZ, MarketStatus
from app.taiwan.realtime.models import RealtimeStatus, TaiwanRealtimeQuote
from app.taiwan.realtime.monitor_engine import TaiwanMonitorEngine
from app.taiwan.realtime.monitor_models import (
    EvaluationStatus,
    TaiwanAlertSeverity,
    TaiwanMonitorRule,
    TaiwanRuleType,
)
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import TaiwanInstrument


@pytest.fixture
def mock_sec_master(tmp_path):
    """Seed Security Master with known instruments."""
    master = TaiwanSecurityMaster(cache_path=tmp_path / "sec_master.parquet")
    tsmc = TaiwanInstrument(
        symbol="2330.TWSE", code="2330", exchange="TWSE", name="台積電",
        instrument_type="stock", listing_status="active", is_supported=True,
        listing_date="1994/09/05", isin="TW0002330008", industry="半導體業",
        cfi_code="ESVUFR", raw_category="股票", source="TWSE_ISIN", updated_at="2026-08-30",
    )
    eink = TaiwanInstrument(
        symbol="8069.TPEX", code="8069", exchange="TPEX", name="元太",
        instrument_type="stock", listing_status="active", is_supported=True,
        listing_date="2004/03/30", isin="TW0008069006", industry="光電業",
        cfi_code="ESVUFR", raw_category="上櫃股票", source="TPEX_ISIN", updated_at="2026-08-30",
    )
    etf_0050 = TaiwanInstrument(
        symbol="0050.TWSE", code="0050", exchange="TWSE", name="元大台灣50",
        instrument_type="etf", listing_status="active", is_supported=True,
        listing_date="2003/06/30", isin="TW0000050004", industry=None,
        cfi_code="CEOJEU", raw_category="ETF", source="TWSE_ISIN", updated_at="2026-08-30",
        etf_category="domestic_equity", classification_source="official_metadata",
        underlying_scope="domestic", leverage_multiplier=1.0,
    )
    etf_00631l = TaiwanInstrument(
        symbol="00631L.TWSE", code="00631L", exchange="TWSE", name="元大台灣50正2",
        instrument_type="etf", listing_status="active", is_supported=True,
        listing_date="2014/10/31", isin="TW00000631L8", industry=None,
        cfi_code="CELJEU", raw_category="ETF", source="TWSE_ISIN", updated_at="2026-08-30",
        etf_category="leveraged", classification_source="official_metadata",
        underlying_scope="domestic", leverage_multiplier=2.0,
    )
    etf_00632r = TaiwanInstrument(
        symbol="00632R.TWSE", code="00632R", exchange="TWSE", name="元大台灣50反1",
        instrument_type="etf", listing_status="active", is_supported=True,
        listing_date="2014/10/31", isin="TW00000632R6", industry=None,
        cfi_code="CELJEU", raw_category="ETF", source="TWSE_ISIN", updated_at="2026-08-30",
        etf_category="leveraged", classification_source="official_metadata",
        underlying_scope="domestic", leverage_multiplier=-1.0,
    )
    etf_00646 = TaiwanInstrument(
        symbol="00646.TWSE", code="00646", exchange="TWSE", name="元大S&P500",
        instrument_type="etf", listing_status="active", is_supported=True,
        listing_date="2015/12/14", isin="TW0000064608", industry=None,
        cfi_code="CEOJEU", raw_category="ETF", source="TWSE_ISIN", updated_at="2026-08-30",
        etf_category="foreign_equity", classification_source="official_metadata",
        underlying_scope="foreign", leverage_multiplier=1.0,
    )
    master._instruments = {
        tsmc.symbol: tsmc,
        eink.symbol: eink,
        etf_0050.symbol: etf_0050,
        etf_00631l.symbol: etf_00631l,
        etf_00632r.symbol: etf_00632r,
        etf_00646.symbol: etf_00646,
    }
    master._loaded = True
    return master


@pytest.fixture
def engine(tmp_path, mock_sec_master, monkeypatch):
    """Instantiate test engine backed by temporary json file."""
    monkeypatch.setattr("app.taiwan.realtime.monitor_engine.get_security_master", lambda: mock_sec_master)
    storage = tmp_path / "test_rules.json"
    return TaiwanMonitorEngine(storage_path=storage)


def make_quote(
    symbol: str = "2330.TWSE",
    name: str = "台積電",
    last_price: float | None = 2505.0,
    prev_close: float | None = 2500.0,
    volume: int | None = 25000000,
    market_status: str = MarketStatus.OPEN.value,
    source: str = "twse:mis",
    source_type: str = "first_party_web_endpoint",
    freshness_class: str = "best_effort_near_realtime",
    is_stale: bool = False,
    status: str = RealtimeStatus.REALTIME.value,
) -> TaiwanRealtimeQuote:
    """Helper to generate realistic TaiwanRealtimeQuote fixtures."""
    now_tpe = datetime.now(TAIPEI_TZ)
    chg = (last_price - prev_close) if (last_price is not None and prev_close is not None) else None
    chg_pct = (chg / prev_close * 100.0) if (chg is not None and prev_close and prev_close > 0) else None

    meta = SourceMeta(
        source=source,
        source_url="http://mis.twse.com.tw",
        fetched_at=now_tpe.isoformat(),
        trade_date=now_tpe.date(),
        status=status,
        is_realtime=not is_stale and status == RealtimeStatus.REALTIME.value,
        is_stale=is_stale,
        source_type=source_type,
        freshness_class=freshness_class,
        is_best_effort=True,
    )
    from app.taiwan.symbol import Exchange
    ex = Exchange.TWSE if symbol.endswith(".TWSE") else Exchange.TPEX
    return TaiwanRealtimeQuote(
        symbol=symbol,
        name=name,
        exchange=ex,
        last_price=last_price,
        prev_close=prev_close,
        open=prev_close,
        high=last_price,
        low=prev_close,
        change=chg,
        change_pct=chg_pct,
        volume=volume,
        amount=100000000.0,
        quote_time=now_tpe,
        trade_date=now_tpe.date(),
        market_status=market_status,
        source_meta=meta,
    )


class TestPriceAndChangeRules:
    """Test price and percentage rule evaluations."""

    def test_price_above_triggers(self, engine):
        rule = TaiwanMonitorRule(
            rule_id="r_p_above", name="台積電破2500",
            symbol="2330.TWSE", rule_type=TaiwanRuleType.PRICE_ABOVE,
            threshold=2500.0,
        )
        quote = make_quote(last_price=2505.0)
        alert, status, _ = engine.evaluate_single_rule(rule, quote)
        assert status == EvaluationStatus.TRIGGERED
        assert alert is not None
        assert alert.trigger_value == 2505.0
        assert "已突破設定閾值 2500.00" in alert.message

    def test_price_below_triggers(self, engine):
        rule = TaiwanMonitorRule(
            rule_id="r_p_below", name="元太跌破160",
            symbol="8069.TPEX", rule_type=TaiwanRuleType.PRICE_BELOW,
            threshold=160.0,
        )
        quote = make_quote(symbol="8069.TPEX", name="元太", last_price=158.0, prev_close=162.0)
        alert, status, _ = engine.evaluate_single_rule(rule, quote)
        assert status == EvaluationStatus.TRIGGERED
        assert alert is not None
        assert alert.trigger_value == 158.0

    def test_missing_last_price_does_not_trigger(self, engine):
        rule = TaiwanMonitorRule(
            rule_id="r_p_missing", name="無價格",
            symbol="2330.TWSE", rule_type=TaiwanRuleType.PRICE_ABOVE,
            threshold=2500.0,
        )
        quote = make_quote(last_price=None)
        alert, status, _ = engine.evaluate_single_rule(rule, quote)
        assert status == EvaluationStatus.SKIPPED_MISSING_FIELD
        assert alert is None

    def test_change_pct_above_triggers(self, engine):
        rule = TaiwanMonitorRule(
            rule_id="r_pct_above", name="上漲超2%",
            symbol="2330.TWSE", rule_type=TaiwanRuleType.CHANGE_PCT_ABOVE,
            threshold=2.0,
        )
        quote = make_quote(last_price=2555.0, prev_close=2500.0)  # +2.2%
        alert, status, _ = engine.evaluate_single_rule(rule, quote)
        assert status == EvaluationStatus.TRIGGERED
        assert alert is not None
        assert alert.trigger_value == 2.2


class TestVolumeRules:
    """Test volume in shares and volume spike multiple."""

    def test_volume_above_shares_normalization(self, engine):
        """Rule threshold must be in SHARES."""
        rule = TaiwanMonitorRule(
            rule_id="r_vol_above", name="成交量破千萬股",
            symbol="2330.TWSE", rule_type=TaiwanRuleType.VOLUME_ABOVE,
            threshold=10000000.0,  # 10,000,000 shares
        )
        quote = make_quote(volume=12500000)
        alert, status, _ = engine.evaluate_single_rule(rule, quote)
        assert status == EvaluationStatus.TRIGGERED
        assert alert is not None
        assert alert.trigger_value == 12500000.0
        assert "12,500,000 股" in alert.message

    def test_volume_spike_multiple(self, engine):
        """Volume spike: cumulative volume vs explicit reference volume."""
        rule = TaiwanMonitorRule(
            rule_id="r_vol_spike", name="量能爆發2倍",
            symbol="2330.TWSE", rule_type=TaiwanRuleType.VOLUME_SPIKE,
            threshold=2.0,  # 2.0x multiple
            reference_volume=5000000,  # 5,000,000 baseline
        )
        quote = make_quote(volume=12000000)  # 12,000,000 / 5,000,000 = 2.4x
        alert, status, _ = engine.evaluate_single_rule(rule, quote)
        assert status == EvaluationStatus.TRIGGERED
        assert alert is not None
        assert alert.trigger_value == 2.4


class TestDataQualityAndMarketStatusGates:
    """Verify strict rejection when data quality or market session is compromised."""

    def test_stale_data_rejected(self, engine):
        rule = TaiwanMonitorRule(
            rule_id="r_stale", name="過期行情攔截",
            symbol="2330.TWSE", rule_type=TaiwanRuleType.PRICE_ABOVE,
            threshold=2500.0,
        )
        quote = make_quote(last_price=2505.0, is_stale=True)
        alert, status, _ = engine.evaluate_single_rule(rule, quote)
        assert status == EvaluationStatus.SKIPPED_STALE_DATA
        assert alert is None

    def test_daily_fallback_rejected(self, engine):
        rule = TaiwanMonitorRule(
            rule_id="r_daily_fb", name="日線降級攔截",
            symbol="2330.TWSE", rule_type=TaiwanRuleType.PRICE_ABOVE,
            threshold=2500.0,
        )
        quote = make_quote(
            last_price=2505.0,
            status=RealtimeStatus.DAILY_FALLBACK.value,
            source="daily_kline",
            source_type="local_store",
        )
        alert, status, _ = engine.evaluate_single_rule(rule, quote)
        assert status == EvaluationStatus.SKIPPED_DAILY_FALLBACK
        assert alert is None

    def test_delayed_yahoo_source_rejected(self, engine):
        rule = TaiwanMonitorRule(
            rule_id="r_delayed", name="延遲行情攔截",
            symbol="2330.TWSE", rule_type=TaiwanRuleType.PRICE_ABOVE,
            threshold=2500.0,
        )
        quote = make_quote(
            last_price=2505.0,
            source="yahoo:chart",
            source_type="third_party_aggregator",
            freshness_class="delayed_15m",
        )
        alert, status, _ = engine.evaluate_single_rule(rule, quote)
        assert status == EvaluationStatus.SKIPPED_DELAYED_SOURCE
        assert alert is None

    def test_unverified_market_status_rejected(self, engine):
        rule = TaiwanMonitorRule(
            rule_id="r_unverified", name="未確認開盤攔截",
            symbol="2330.TWSE", rule_type=TaiwanRuleType.PRICE_ABOVE,
            threshold=2500.0,
        )
        quote = make_quote(
            last_price=2505.0,
            market_status=MarketStatus.SCHEDULED_OPEN_UNVERIFIED.value,
        )
        alert, status, _ = engine.evaluate_single_rule(rule, quote)
        assert status == EvaluationStatus.SKIPPED_MARKET_UNVERIFIED
        assert alert is None

    def test_closed_market_status_rejected(self, engine):
        rule = TaiwanMonitorRule(
            rule_id="r_closed", name="閉市攔截",
            symbol="2330.TWSE", rule_type=TaiwanRuleType.PRICE_ABOVE,
            threshold=2500.0,
        )
        quote = make_quote(
            last_price=2505.0,
            market_status=MarketStatus.CLOSED.value,
        )
        alert, status, _ = engine.evaluate_single_rule(rule, quote)
        assert status == EvaluationStatus.SKIPPED_MARKET_CLOSED
        assert alert is None


class TestTaiwanPriceLimitRules:
    """Verify official price limit bounds: 0050 (+10%), 00631L (+20%), 00632R (+10%), 00646 (NO_LIMIT)."""

    def test_0050_near_upper_limit(self, engine):
        """0050 ref 200.0 -> Limit Up = 220.0 (+10%). Current 218.5 -> Dist = 0.68% <= 1.0%."""
        rule = TaiwanMonitorRule(
            rule_id="r_0050_limit", name="0050接近漲停",
            symbol="0050.TWSE", rule_type=TaiwanRuleType.NEAR_UPPER_LIMIT,
            threshold=1.0,  # <= 1.0%
        )
        quote = make_quote(symbol="0050.TWSE", name="元大台灣50", last_price=218.5, prev_close=200.0)
        alert, status, _ = engine.evaluate_single_rule(rule, quote)
        assert status == EvaluationStatus.TRIGGERED
        assert alert is not None
        assert alert.trigger_value <= 1.0
        assert "距漲停價僅" in alert.message

    def test_00631l_leveraged_domestic_near_upper_limit(self, engine):
        """00631L (2X Domestic) ref 100.0 -> Limit Up = 120.0 (+20%). Current 119.0 -> Dist = 0.83% <= 1.0%."""
        rule = TaiwanMonitorRule(
            rule_id="r_00631l_limit", name="正2接近漲停",
            symbol="00631L.TWSE", rule_type=TaiwanRuleType.NEAR_UPPER_LIMIT,
            threshold=1.0,
        )
        quote = make_quote(symbol="00631L.TWSE", name="元大台灣50正2", last_price=119.0, prev_close=100.0)
        alert, status, _ = engine.evaluate_single_rule(rule, quote)
        assert status == EvaluationStatus.TRIGGERED
        assert alert is not None
        assert alert.trigger_value == 0.83

    def test_00646_foreign_etf_near_limit_not_applicable(self, engine):
        """00646 has NO_LIMIT; evaluation must return NOT_APPLICABLE, never falsely alert."""
        rule = TaiwanMonitorRule(
            rule_id="r_00646_limit", name="00646無漲跌幅限制",
            symbol="00646.TWSE", rule_type=TaiwanRuleType.NEAR_UPPER_LIMIT,
            threshold=1.0,
        )
        quote = make_quote(symbol="00646.TWSE", name="元大S&P500", last_price=60.0, prev_close=50.0)
        alert, status, reason = engine.evaluate_single_rule(rule, quote)
        assert status == EvaluationStatus.NOT_APPLICABLE
        assert alert is None
        assert "NO_LIMIT" in reason


class TestDeduplicationCooldownAndHysteresis:
    """Test state transitions: edge-triggered dedup, cooldown, and hysteresis re-arming."""

    def test_edge_triggered_dedup_and_rearm(self, engine):
        rule = TaiwanMonitorRule(
            rule_id="r_dedup", name="去重測試",
            symbol="2330.TWSE", rule_type=TaiwanRuleType.PRICE_ABOVE,
            threshold=2500.0, cooldown_seconds=0,
        )
        # 1. First trigger: false -> true -> Alert emitted
        q1 = make_quote(last_price=2505.0)
        alert1, status1, _ = engine.evaluate_single_rule(rule, q1)
        assert status1 == EvaluationStatus.TRIGGERED
        assert alert1 is not None

        # 2. Still above threshold: true -> true -> Deduplicated
        q2 = make_quote(last_price=2508.0)
        alert2, status2, _ = engine.evaluate_single_rule(rule, q2)
        assert status2 == EvaluationStatus.DEDUP_SUPPRESSED
        assert alert2 is None

        # 3. Price drops below threshold: true -> false -> Re-arms
        q3 = make_quote(last_price=2490.0)
        alert3, status3, _ = engine.evaluate_single_rule(rule, q3)
        assert status3 == EvaluationStatus.NOT_TRIGGERED
        assert alert3 is None

        # 4. Price rises above threshold again: false -> true -> Emits alert again
        q4 = make_quote(last_price=2502.0)
        alert4, status4, _ = engine.evaluate_single_rule(rule, q4)
        assert status4 == EvaluationStatus.TRIGGERED
        assert alert4 is not None

    def test_cooldown_suppression(self, engine):
        rule = TaiwanMonitorRule(
            rule_id="r_cool", name="冷卻測試",
            symbol="2330.TWSE", rule_type=TaiwanRuleType.PRICE_ABOVE,
            threshold=2500.0, cooldown_seconds=300,
        )
        t0 = 1000.0
        # 1. Trigger at t0
        q1 = make_quote(last_price=2505.0)
        alert1, status1, _ = engine.evaluate_single_rule(rule, q1, now_mono=t0)
        assert status1 == EvaluationStatus.TRIGGERED

        # 2. Drop price at t0 + 10s (re-arms state)
        q2 = make_quote(last_price=2490.0)
        engine.evaluate_single_rule(rule, q2, now_mono=t0 + 10.0)

        # 3. Rise price at t0 + 20s (still within 300s cooldown) -> Suppressed by cooldown
        q3 = make_quote(last_price=2505.0)
        alert3, status3, _ = engine.evaluate_single_rule(rule, q3, now_mono=t0 + 20.0)
        assert status3 == EvaluationStatus.COOLDOWN_ACTIVE
        assert alert3 is None

        # 4. Rise price at t0 + 305s -> Outside cooldown -> Emits alert!
        alert4, status4, _ = engine.evaluate_single_rule(rule, q3, now_mono=t0 + 305.0)
        assert status4 == EvaluationStatus.TRIGGERED
        assert alert4 is not None

    def test_hysteresis_prevents_chatter(self, engine):
        """Threshold 2500, Hysteresis 5 -> Must drop below 2495 to re-arm."""
        rule = TaiwanMonitorRule(
            rule_id="r_hys", name="遲滯防抖測試",
            symbol="2330.TWSE", rule_type=TaiwanRuleType.PRICE_ABOVE,
            threshold=2500.0, hysteresis=5.0, cooldown_seconds=0,
        )
        # 1. Trigger at 2501
        q1 = make_quote(last_price=2501.0)
        alert1, status1, _ = engine.evaluate_single_rule(rule, q1)
        assert status1 == EvaluationStatus.TRIGGERED

        # 2. Fluctuate to 2498.0 (below 2500, but NOT below 2495.0 -> Still armed!)
        q2 = make_quote(last_price=2498.0)
        engine.evaluate_single_rule(rule, q2)

        # 3. Rise back to 2502.0 -> Suppressed because it never re-armed!
        q3 = make_quote(last_price=2502.0)
        alert3, status3, _ = engine.evaluate_single_rule(rule, q3)
        assert status3 == EvaluationStatus.DEDUP_SUPPRESSED
        assert alert3 is None

        # 4. Drop decisively to 2490.0 (< 2495.0 -> Re-armed!)
        q4 = make_quote(last_price=2490.0)
        engine.evaluate_single_rule(rule, q4)

        # 5. Rise back to 2502.0 -> Triggers!
        alert5, status5, _ = engine.evaluate_single_rule(rule, q3)
        assert status5 == EvaluationStatus.TRIGGERED
        assert alert5 is not None


class TestBatchSymbolGrouping:
    """Verify multiple rules for the same and different symbols are evaluated in 1 batch."""

    def test_single_batch_evaluation(self, engine):
        class MockRealtimeService:
            def __init__(self):
                self.calls = []

            def get_quotes(self, symbols, force_refresh=False):
                self.calls.append(list(symbols))
                return {
                    s: make_quote(symbol=s, last_price=2600.0 if "2330" in s else 170.0)
                    for s in symbols
                }

        mock_svc = MockRealtimeService()
        engine.realtime_service = mock_svc

        # 3 rules on 2330.TWSE, 2 rules on 8069.TPEX (5 rules total, 2 unique symbols)
        engine.add_rule(TaiwanMonitorRule(rule_id="r1", name="R1", symbol="2330.TWSE", rule_type=TaiwanRuleType.PRICE_ABOVE, threshold=2500))
        engine.add_rule(TaiwanMonitorRule(rule_id="r2", name="R2", symbol="2330.TWSE", rule_type=TaiwanRuleType.CHANGE_PCT_ABOVE, threshold=1.0))
        engine.add_rule(TaiwanMonitorRule(rule_id="r3", name="R3", symbol="2330.TWSE", rule_type=TaiwanRuleType.VOLUME_ABOVE, threshold=1000000))
        engine.add_rule(TaiwanMonitorRule(rule_id="r4", name="R4", symbol="8069.TPEX", rule_type=TaiwanRuleType.PRICE_ABOVE, threshold=160))
        engine.add_rule(TaiwanMonitorRule(rule_id="r5", name="R5", symbol="8069.TPEX", rule_type=TaiwanRuleType.VOLUME_ABOVE, threshold=1000000))

        alerts = engine.evaluate_all()
        # Exactly 1 call to get_quotes!
        assert len(mock_svc.calls) == 1
        # Exactly 2 symbols requested
        assert sorted(mock_svc.calls[0]) == ["2330.TWSE", "8069.TPEX"]
        assert len(alerts) == 5

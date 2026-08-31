"""Unit tests for Taiwan Market Rules and Models (Hardened).

Covers:
  - Commission model: statutory default 0.0 min commission, configurable discount/fee
  - Securities tax model: ordinary stock (0.3%), domestic ETF (0.1%), day-trading (0.15%), bond ETF (0%)
  - Regulatory effective dates & RegulatoryRuleUnavailableError upon statutory expiry
  - Lot model: board lot (1000) vs odd lot (1), share alignment
  - Tick size model: stock 6 tiers, ETF 2 tiers, price limit boundary vs order rounding
  - Price limit single source of truth: delegation in price_limits.py, NO_LIMIT handling
  - Settlement cycle vs same-day trading decoupling (STANDARD_CASH vs DAY_TRADING)
"""
from __future__ import annotations

from datetime import date

import pytest

from app.price_limits import board_limit_pct, price_limit_pct, taiwan_price_limit_pct
from app.taiwan.market_rules import (
    EXAMPLE_BROKER_MIN_COMMISSION,
    BacktestMode,
    LotModel,
    PriceLimitClass,
    PriceLimitModel,
    RegulatoryRuleUnavailableError,
    SecuritiesTaxModel,
    SettlementModel,
    TaiwanMarketProfile,
    TaxClass,
    TickSizeClass,
    TickSizeModel,
    TradingCostModel,
)
from app.taiwan.universe.models import OFFICIAL_ETF_RULE_PROFILES

# ── 1. Commission Model Tests ──────────────────────────────────


class TestCommissionModel:
    """Test configurable brokerage commission model."""

    def test_statutory_default_has_zero_minimum_commission(self):
        """Statutory default has no minimum commission floor (broker-specific)."""
        cost = TradingCostModel()
        assert cost.minimum_commission == 0.0
        # Trade value 1,000 TWD -> 1,000 * 0.001425 = 1.425 TWD
        assert cost.calc_commission(1_000.0) == pytest.approx(1.425)

    def test_explicit_broker_configuration_with_20_twd_floor(self):
        """Explicit broker profile with 20 TWD minimum charge."""
        cost = TradingCostModel(
            commission_rate=0.001425,
            discount=0.6,
            minimum_commission=EXAMPLE_BROKER_MIN_COMMISSION,
        )
        assert cost.calc_commission(100_000.0) == pytest.approx(85.5)
        # Small trade floored at 20 TWD
        assert cost.calc_commission(1_000.0) == 20.0

    def test_broker_discount_28pct(self):
        cost = TradingCostModel(
            commission_rate=0.001425,
            discount=0.28,
            minimum_commission=20.0,
        )
        assert cost.calc_commission(100_000.0) == pytest.approx(39.9)

    def test_odd_lot_configurable_min_commission(self):
        cost = TradingCostModel(
            commission_rate=0.001425,
            discount=0.6,
            minimum_commission=1.0,
        )
        assert cost.calc_commission(1_000.0) == pytest.approx(1.0)

    def test_zero_or_negative_trade_value(self):
        cost = TradingCostModel(minimum_commission=20.0)
        assert cost.calc_commission(0.0) == 0.0
        assert cost.calc_commission(-500.0) == 0.0


# ── 2. Securities Tax Model Tests & Expiry Handling ────────────


class TestSecuritiesTaxModel:
    """Test securities transaction tax rules and regulatory expiry."""

    def test_ordinary_stock_tax_sell_side(self):
        tax_model = SecuritiesTaxModel()
        assert tax_model.calc_tax(100_000.0, TaxClass.ORDINARY_STOCK) == 300.0

    def test_domestic_etf_tax(self):
        tax_model = SecuritiesTaxModel()
        assert tax_model.calc_tax(100_000.0, TaxClass.DOMESTIC_ETF) == 100.0

    def test_day_trade_reduced_tax_within_enacted_window(self):
        tax_model = SecuritiesTaxModel()
        trade_date = date(2026, 8, 28)
        assert tax_model.calc_tax(100_000.0, TaxClass.ORDINARY_STOCK, is_day_trade=True, trade_date=trade_date) == 150.0

    def test_day_trade_tax_raises_when_expired(self):
        """Do not speculate on future laws beyond enacted expiry date."""
        tax_model = SecuritiesTaxModel()
        post_expiry = date(2028, 1, 5)
        with pytest.raises(RegulatoryRuleUnavailableError, match="Day-trade tax incentive"):
            tax_model.get_tax_rate(TaxClass.ORDINARY_STOCK, is_day_trade=True, trade_date=post_expiry)

    def test_bond_etf_tax_free_within_enacted_window(self):
        tax_model = SecuritiesTaxModel()
        trade_date = date(2026, 8, 28)
        assert tax_model.calc_tax(100_000.0, TaxClass.BOND_ETF, trade_date=trade_date) == 0.0

    def test_bond_etf_tax_raises_when_expired(self):
        """Do not speculate on future laws beyond enacted expiry date."""
        tax_model = SecuritiesTaxModel()
        post_expiry = date(2027, 1, 5)
        with pytest.raises(RegulatoryRuleUnavailableError, match="Bond ETF 0% tax exemption expired"):
            tax_model.get_tax_rate(TaxClass.BOND_ETF, trade_date=post_expiry)


# ── 3. Lot Model Tests ─────────────────────────────────────────


class TestLotModel:
    """Test board lot (1000 shares) and odd lot sizing."""

    def test_board_lot_rounds_to_multiples_of_1000(self):
        lot = LotModel(mode="board_lot", board_lot_size=1000)
        assert lot.align_shares(allocation=250_000.0, price=100.0) == 2000

    def test_board_lot_insufficient_for_one_lot(self):
        lot = LotModel(mode="board_lot", board_lot_size=1000)
        assert lot.align_shares(allocation=50_000.0, price=100.0) == 0

    def test_odd_lot_mode_allows_single_shares(self):
        lot = LotModel(mode="odd_lot", odd_lot_size=1)
        assert lot.align_shares(allocation=50_000.0, price=100.0) == 500


# ── 4. Tick Size Model Tests (Boundary vs Order Rounding) ──────


class TestTickSizeModel:
    """Test stock 6 tiers and ETF 2 tiers price tick boundaries."""

    @pytest.mark.parametrize("price,expected_tick", [
        (5.50, 0.01),
        (9.99, 0.01),
        (10.00, 0.05),
        (25.50, 0.05),
        (49.95, 0.05),
        (50.00, 0.10),
        (99.90, 0.10),
        (100.00, 0.50),
        (245.50, 0.50),
        (499.50, 0.50),
        (500.00, 1.00),
        (850.00, 1.00),
        (999.00, 1.00),
        (1000.00, 5.00),
        (2420.00, 5.00),
    ])
    def test_stock_tick_size_boundaries(self, price: float, expected_tick: float):
        assert TickSizeModel.get_tick_size(price, TickSizeClass.ORDINARY_STOCK) == expected_tick

    @pytest.mark.parametrize("price,expected_tick", [
        (20.00, 0.01),
        (49.99, 0.01),
        (50.00, 0.05),
        (106.95, 0.05),
    ])
    def test_etf_tick_size_boundaries(self, price: float, expected_tick: float):
        assert TickSizeModel.get_tick_size(price, TickSizeClass.ETF) == expected_tick

    def test_round_price_limit_boundary_strict(self):
        # 1005.3 TWD with 5.00 tick
        # Limit-up must floor so ceiling is not breached
        assert TickSizeModel.round_price_limit_boundary(1005.3, direction="up") == 1005.0
        # Limit-down must ceil so floor is not breached
        assert TickSizeModel.round_price_limit_boundary(1005.3, direction="down") == 1010.0

    def test_round_order_price_nearest(self):
        # Order price does not force floor or ceil; rounds to nearest tick
        assert TickSizeModel.round_order_price(1006.0) == 1005.0
        assert TickSizeModel.round_order_price(1008.0) == 1010.0


# ── 5. Price Limit Single Source of Truth & Delegation ─────────


class TestPriceLimitModelAndDelegation:
    """Verify PriceLimitModel is the single authoritative source of truth."""

    def test_model_get_limit_pct_ordinary_stock(self):
        assert PriceLimitModel.get_limit_pct(PriceLimitClass.ORDINARY_TEN_PERCENT) == 0.10

    def test_model_get_limit_pct_no_limit(self):
        assert PriceLimitModel.get_limit_pct(PriceLimitClass.NO_LIMIT) is None

    def test_price_limits_py_delegation_ordinary_stock(self):
        # Ordinary stock 2330.TWSE -> 10%
        assert taiwan_price_limit_pct("2330.TWSE") == 0.10
        assert board_limit_pct("2330.TWSE") == 0.10
        assert price_limit_pct("2330.TWSE", date(2026, 8, 28)) == 0.10

    def test_price_limits_py_delegation_no_limit_etf(self):
        # Foreign ETF / Bond ETF with NO_LIMIT class must return None, NOT 10%
        assert taiwan_price_limit_pct("00646.TWSE", limit_class=PriceLimitClass.NO_LIMIT) is None
        assert board_limit_pct("00646.TWSE", limit_class=PriceLimitClass.NO_LIMIT) is None
        assert price_limit_pct("00646.TWSE", date(2026, 8, 28), limit_class=PriceLimitClass.NO_LIMIT) is None


# ── 6. Settlement and Same-Day Exit Decoupling ─────────────────


class TestSettlementAndSameDayExit:
    """Verify settlement cycle is decoupled from position exit rules."""

    def test_settlement_model_is_t_plus_2(self):
        settlement = SettlementModel()
        assert settlement.settlement_days == 2

    def test_standard_cash_disallows_same_day_exit(self):
        profile = TaiwanMarketProfile(mode=BacktestMode.STANDARD_CASH)
        assert profile.can_same_day_exit(is_day_trade_eligible=True) is False
        assert profile.can_same_day_exit(is_day_trade_eligible=False) is False

    def test_day_trading_allows_same_day_exit_for_eligible_securities(self):
        profile = TaiwanMarketProfile(mode=BacktestMode.DAY_TRADING)
        assert profile.can_same_day_exit(is_day_trade_eligible=True) is True
        assert profile.can_same_day_exit(is_day_trade_eligible=False) is False


def test_audited_leveraged_etf_rule_profiles_are_explicit_official_evidence():
    leveraged = OFFICIAL_ETF_RULE_PROFILES["00631L.TWSE"]
    inverse = OFFICIAL_ETF_RULE_PROFILES["00632R.TWSE"]
    assert (leveraged.category, leveraged.underlying_scope, leveraged.price_limit_multiplier) == ("leveraged", "domestic", 2.0)
    assert (inverse.category, inverse.underlying_scope, inverse.price_limit_multiplier) == ("inverse", "domestic", -1.0)
    assert leveraged.evidence_url.startswith("https://www.twse.com.tw/")

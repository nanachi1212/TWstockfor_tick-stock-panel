"""Unit tests for Taiwan Market Rules and Models.

Covers:
  - Commission model: configurable rate, discount, minimum commission
  - Securities tax model: ordinary stock (0.3%), domestic ETF (0.1%), day-trading (0.15%), bond ETF (0%)
  - Regulatory effective dates & expiry checks
  - Lot model: board lot (1000) vs odd lot (1), share alignment
  - Tick size model: stock 6 tiers, ETF 2 tiers, buy/sell rounding semantics
  - Price limit model: 10% standard and no-limit class
  - Settlement cycle vs same-day trading decoupling (STANDARD_CASH vs DAY_TRADING)
"""
from __future__ import annotations

from datetime import date
import pytest

from app.taiwan.market_rules import (
    BacktestMode,
    LotModel,
    PriceLimitClass,
    PriceLimitModel,
    SecuritiesTaxModel,
    SettlementModel,
    TaiwanMarketProfile,
    TaxClass,
    TickSizeClass,
    TickSizeModel,
    TradingCostModel,
)


# ── 1. Commission Model Tests ──────────────────────────────────


class TestCommissionModel:
    """Test configurable brokerage commission model."""

    def test_default_full_commission(self):
        cost = TradingCostModel(commission_rate=0.001425, discount=1.0, minimum_commission=20.0)
        # Trade value 100,000 TWD -> 100,000 * 0.001425 = 142.5 TWD
        assert cost.calc_commission(100_000.0) == 142.5

    def test_broker_discount_60pct(self):
        cost = TradingCostModel(commission_rate=0.001425, discount=0.6, minimum_commission=20.0)
        # 100,000 * 0.001425 * 0.6 = 85.5 TWD
        assert cost.calc_commission(100_000.0) == pytest.approx(85.5)

    def test_broker_discount_28pct(self):
        cost = TradingCostModel(commission_rate=0.001425, discount=0.28, minimum_commission=20.0)
        # 100,000 * 0.001425 * 0.28 = 39.9 TWD
        assert cost.calc_commission(100_000.0) == pytest.approx(39.9)

    def test_minimum_commission_floor(self):
        cost = TradingCostModel(commission_rate=0.001425, discount=0.6, minimum_commission=20.0)
        # Trade value 1,000 TWD -> raw fee = 0.855 TWD < 20 TWD floor -> 20.0 TWD
        assert cost.calc_commission(1_000.0) == 20.0

    def test_odd_lot_configurable_min_commission(self):
        cost = TradingCostModel(commission_rate=0.001425, discount=0.6, minimum_commission=1.0)
        # Trade value 1,000 TWD -> raw fee 0.855 TWD < 1 TWD floor -> 1.0 TWD
        assert cost.calc_commission(1_000.0) == 1.0

    def test_zero_or_negative_trade_value(self):
        cost = TradingCostModel()
        assert cost.calc_commission(0.0) == 0.0
        assert cost.calc_commission(-500.0) == 0.0


# ── 2. Securities Tax Model Tests ──────────────────────────────


class TestSecuritiesTaxModel:
    """Test securities transaction tax rules and regulatory expiry."""

    def test_ordinary_stock_tax_sell_side(self):
        tax_model = SecuritiesTaxModel()
        # 100,000 TWD trade value -> 0.3% = 300 TWD
        assert tax_model.calc_tax(100_000.0, TaxClass.ORDINARY_STOCK) == 300.0

    def test_domestic_etf_tax(self):
        tax_model = SecuritiesTaxModel()
        # 100,000 TWD trade value -> 0.1% = 100 TWD
        assert tax_model.calc_tax(100_000.0, TaxClass.DOMESTIC_ETF) == 100.0

    def test_day_trade_reduced_tax_before_expiry(self):
        tax_model = SecuritiesTaxModel()
        # On 2026-08-28 (before 2027-12-31 expiry) -> 0.15% = 150 TWD
        trade_date = date(2026, 8, 28)
        assert tax_model.calc_tax(100_000.0, TaxClass.ORDINARY_STOCK, is_day_trade=True, trade_date=trade_date) == 150.0

    def test_day_trade_tax_after_expiry_reverts_to_full(self):
        tax_model = SecuritiesTaxModel()
        # On 2028-01-05 (after 2027-12-31 expiry) -> reverts to 0.3% = 300 TWD
        post_expiry = date(2028, 1, 5)
        assert tax_model.calc_tax(100_000.0, TaxClass.ORDINARY_STOCK, is_day_trade=True, trade_date=post_expiry) == 300.0

    def test_bond_etf_tax_free_before_expiry(self):
        tax_model = SecuritiesTaxModel()
        # On 2026-08-28 (before 2026-12-31 expiry) -> 0% = 0 TWD
        trade_date = date(2026, 8, 28)
        assert tax_model.calc_tax(100_000.0, TaxClass.BOND_ETF, trade_date=trade_date) == 0.0

    def test_bond_etf_tax_after_expiry(self):
        tax_model = SecuritiesTaxModel()
        # On 2027-01-05 (after 2026-12-31 expiry) -> falls back to ETF rate 0.1%
        trade_date = date(2027, 1, 5)
        assert tax_model.calc_tax(100_000.0, TaxClass.BOND_ETF, trade_date=trade_date) == 100.0


# ── 3. Lot Model Tests ─────────────────────────────────────────


class TestLotModel:
    """Test board lot (1000 shares) and odd lot sizing."""

    def test_board_lot_rounds_to_multiples_of_1000(self):
        lot = LotModel(mode="board_lot", board_lot_size=1000)
        # Allocation 250,000 at price 100 TWD -> 2,500 shares max -> 2,000 shares (2 lots)
        shares = lot.align_shares(allocation=250_000.0, price=100.0)
        assert shares == 2000

    def test_board_lot_insufficient_for_one_lot(self):
        lot = LotModel(mode="board_lot", board_lot_size=1000)
        # Allocation 50,000 at price 100 TWD -> 500 shares -> 0 lots
        shares = lot.align_shares(allocation=50_000.0, price=100.0)
        assert shares == 0

    def test_odd_lot_mode_allows_single_shares(self):
        lot = LotModel(mode="odd_lot", odd_lot_size=1)
        # Allocation 50,000 at price 100 TWD -> 500 shares
        shares = lot.align_shares(allocation=50_000.0, price=100.0)
        assert shares == 500


# ── 4. Tick Size Model Tests ───────────────────────────────────


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

    def test_round_to_valid_tick_stock_buy_and_sell(self):
        # Price 1005.3 TWD: tick size is 5.00
        # side='buy' floors to 1005.00 (don't pay above tick)
        assert TickSizeModel.round_to_valid_tick(1005.3, TickSizeClass.ORDINARY_STOCK, side="buy") == 1005.0
        # side='sell' ceils to 1010.00 (don't sell below tick)
        assert TickSizeModel.round_to_valid_tick(1005.3, TickSizeClass.ORDINARY_STOCK, side="sell") == 1010.0
        # side='round' nearest is 1005.00
        assert TickSizeModel.round_to_valid_tick(1005.3, TickSizeClass.ORDINARY_STOCK, side="round") == 1005.0

    def test_round_to_valid_tick_etf(self):
        # ETF price 50.03 TWD: tick size is 0.05
        assert TickSizeModel.round_to_valid_tick(50.03, TickSizeClass.ETF, side="buy") == 50.00
        assert TickSizeModel.round_to_valid_tick(50.03, TickSizeClass.ETF, side="sell") == 50.05


# ── 5. Price Limit Model Tests ─────────────────────────────────


class TestPriceLimitModel:
    """Test 10% daily price limit and no-limit rules."""

    def test_ordinary_stock_price_limit(self):
        # Base price 100.0 TWD -> +10% = 110.0, -10% = 90.0 (both valid ticks)
        up, down = PriceLimitModel.calc_limits(100.0, PriceLimitClass.ORDINARY_TEN_PERCENT, TickSizeClass.ORDINARY_STOCK)
        assert up == 110.0
        assert down == 90.0

    def test_tsmc_price_limit_rounding(self):
        # 2330 base price 2420.0 -> up: 2420 * 1.10 = 2662.0 -> tick is 5.0 -> floor to 2660.0
        up, down = PriceLimitModel.calc_limits(2420.0, PriceLimitClass.ORDINARY_TEN_PERCENT, TickSizeClass.ORDINARY_STOCK)
        assert up == 2660.0
        # down: 2420 * 0.90 = 2178.0 -> tick is 5.0 -> ceil to 2180.0
        assert down == 2180.0

    def test_no_limit_etf_class(self):
        up, down = PriceLimitModel.calc_limits(100.0, PriceLimitClass.NO_LIMIT, TickSizeClass.ETF)
        assert up is None
        assert down is None


# ── 6. Settlement and Same-Day Exit Decoupling ─────────────────


class TestSettlementAndSameDayExit:
    """Verify settlement cycle is decoupled from position exit rules."""

    def test_settlement_model_is_t_plus_2(self):
        settlement = SettlementModel()
        assert settlement.settlement_days == 2

    def test_standard_cash_disallows_same_day_exit(self):
        profile = TaiwanMarketProfile(mode=BacktestMode.STANDARD_CASH)
        # Even if eligible for day-trade, STANDARD_CASH account policy prohibits same-day exit
        assert profile.can_same_day_exit(is_day_trade_eligible=True) is False
        assert profile.can_same_day_exit(is_day_trade_eligible=False) is False

    def test_day_trading_allows_same_day_exit_for_eligible_securities(self):
        profile = TaiwanMarketProfile(mode=BacktestMode.DAY_TRADING)
        assert profile.can_same_day_exit(is_day_trade_eligible=True) is True
        # Ineligible security (e.g. disposed stock) prohibited
        assert profile.can_same_day_exit(is_day_trade_eligible=False) is False

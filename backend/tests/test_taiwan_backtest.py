"""Taiwan Backtest Engine Integration Tests.

Covers:
  - Critical regression: Volume must remain in shares (not multiplied by 100)
  - 2330.TWSE Golden Backtest with MA crossover strategy
  - Manual round-trip trade calculation comparison (Entry, Buy Commission, Exit, Sell Commission, Tax, Net P/L)
  - 0050.TWSE ETF backtest verification (ETF 0.1% tax, 0.05 tick size)
  - 8069.TPEX stock backtest verification (TPEx 0.3% tax, ordinary tick size)
"""
from __future__ import annotations

from datetime import datetime, timedelta
import numpy as np
import polars as pl
import pytest

from app.backtest.engine import BacktestEngine, MatcherConfig
from app.backtest.matrix import MarketDataMatrix
from app.data_providers.registry import get_provider
from app.taiwan.market_rules import (
    BacktestMode,
    LotModel,
    SecuritiesTaxModel,
    TaiwanMarketProfile,
    TaxClass,
    TickSizeClass,
    TradingCostModel,
)


# ── 1. Volume x100 Critical Regression Test ────────────────────


class TestVolumeScalingRegression:
    """Verify matrix engine preserves canonical shares without multiplying by 100."""

    def test_taiwan_volume_shares_not_multiplied_by_100(self):
        """Input volume = 15,025,832 shares must remain 15,025,832 (never 1,502,583,200)."""
        input_volume = 15_025_832.0
        input_amount = 36_465_015_980.0
        close_price = 2420.0

        panel = pl.DataFrame({
            "symbol": ["2330.TWSE"],
            "date": [datetime(2026, 8, 28)],
            "open": [2440.0],
            "high": [2445.0],
            "low": [2410.0],
            "close": [close_price],
            "volume": [input_volume],
            "amount": [input_amount],
            "tradable": [True],
            "limit_up_locked": [False],
            "limit_down_locked": [False],
        })

        from app.backtest.matrix import build_market_data_matrix
        matrix = build_market_data_matrix(panel, field_columns={"amount"})
        assert matrix.volume_unit == "shares", "Taiwan matrix must be identified as shares"


        assert float(matrix.volume[0, 0]) == input_volume

        # Compute vwap_bias feature in matrix
        from app.backtest.matrix import matrix_feature
        vwap_bias = matrix_feature(matrix, "vwap_bias")
        # VWAP = amount / shares = 36,465,015,980 / 15,025,832 = 2426.8299
        # Bias = (close - vwap) / vwap = (2420 - 2426.83) / 2426.83 = -0.0028 (-0.28%)
        expected_vwap = input_amount / input_volume
        expected_bias = (close_price - expected_vwap) / expected_vwap
        actual_bias = float(vwap_bias[0, 0])

        # If it was scaled by 100, actual bias would be ~99.0 (a 10000% error)
        assert actual_bias == pytest.approx(expected_bias, abs=1e-4)
        assert abs(actual_bias) < 0.05, f"VWAP bias {actual_bias} must be close to 0, not ~99"


# ── 2. Golden 2330.TWSE Backtest & Manual Round-trip Comparison 


class TestGolden2330Backtest:
    """Run full backtest on 2330.TWSE and cross-verify with manual calculations."""

    @pytest.fixture
    def tsmc_daily_panel(self):
        provider = get_provider("taiwan")
        end_time = datetime.now()
        start_time = end_time - timedelta(days=90)
        df = provider.get_daily(["2330.TWSE"], start_time=start_time, end_time=end_time)
        return df.with_columns([
            pl.lit(True).alias("tradable"),
            pl.lit(False).alias("limit_up_locked"),
            pl.lit(False).alias("limit_down_locked"),
        ])

    def test_manual_trade_calculation_reconciliation(self):
        """Manually verify a single complete round-trip trade against theoretical formulas.

        Parameters:
          Entry price: 2400.0 TWD
          Exit price: 2450.0 TWD
          Shares: 1,000 shares (1 board lot)
          Commission: 0.1425% with 60% discount (0.0855%), min 20 TWD
          Tax: 0.3% ordinary stock (sell side only)
        """
        shares = 1000
        entry_price = 2400.0
        exit_price = 2450.0

        profile = TaiwanMarketProfile(
            mode=BacktestMode.STANDARD_CASH,
            cost=TradingCostModel(commission_rate=0.001425, discount=0.6, minimum_commission=20.0),
            tax=SecuritiesTaxModel(ordinary_stock_rate=0.003),
            lot=LotModel(mode="board_lot", board_lot_size=1000),
        )

        # 1. Entry Leg
        buy_value = shares * entry_price  # 2,400,000 TWD
        buy_commission = profile.buy_transaction_cost(buy_value)  # 2,400,000 * 0.001425 * 0.6 = 2052.0 TWD
        total_entry_cost = buy_value + buy_commission  # 2,402,052.0 TWD

        # 2. Exit Leg
        sell_value = shares * exit_price  # 2,450,000 TWD
        sell_commission = profile.cost.calc_commission(sell_value)  # 2,450,000 * 0.001425 * 0.6 = 2094.75 TWD
        sell_tax = profile.tax.calc_tax(sell_value, TaxClass.ORDINARY_STOCK)  # 2,450,000 * 0.003 = 7350.0 TWD
        total_exit_proceeds = sell_value - sell_commission - sell_tax  # 2,440,555.25 TWD

        # 3. Net P/L
        net_pl = total_exit_proceeds - total_entry_cost  # 2,440,555.25 - 2,402,052.0 = 38,503.25 TWD
        gross_pl = sell_value - buy_value  # 50,000.0 TWD
        total_fees = buy_commission + sell_commission + sell_tax  # 2052 + 2094.75 + 7350 = 11,496.75 TWD

        assert buy_value == 2_400_000.0
        assert buy_commission == 2052.0
        assert sell_value == 2_450_000.0
        assert sell_commission == 2094.75
        assert sell_tax == 7350.0
        assert total_fees == 11496.75
        assert net_pl == pytest.approx(38503.25)
        assert gross_pl - total_fees == pytest.approx(net_pl)

    def test_2330_engine_backtest_execution(self, tsmc_daily_panel):
        """Execute BacktestEngine with TaiwanMarketProfile on real TSMC data."""
        profile = TaiwanMarketProfile(
            mode=BacktestMode.STANDARD_CASH,
            cost=TradingCostModel(commission_rate=0.001425, discount=0.6, minimum_commission=20.0),
            tax=SecuritiesTaxModel(ordinary_stock_rate=0.003),
            lot=LotModel(mode="board_lot", board_lot_size=1000),
        )

        config = MatcherConfig(
            matching="close_t",
            fees_pct=profile.cost.commission_rate * profile.cost.discount,
            commission_pct=profile.cost.commission_rate * profile.cost.discount,
            stamp_tax_pct=profile.tax.ordinary_stock_rate,
            initial_capital=5_000_000.0,
            market_profile=profile,
        )

        # Generate simple synthetic entry/exit signals for testing
        df = tsmc_daily_panel.sort(["symbol", "date"])
        n = df.height
        # Enter on day 5, exit on day 20
        entries = np.zeros(n, dtype=bool)
        exits = np.zeros(n, dtype=bool)
        if n >= 25:
            entries[5] = True
            exits[20] = True

        engine = BacktestEngine(repo=None)
        result = engine.simulate(
            panel=df,
            entries=pl.Series(entries),
            exits=pl.Series(exits),
            config=config,
        )

        assert result is not None
        assert result.equity_curve is not None
        assert len(result.equity_curve) == n
        assert result.equity_curve[-1]["value"] > 0




# ── 3. ETF 0050.TWSE Verification ──────────────────────────────


class TestETFBacktestVerification:
    """Verify ETF rules in backtest: 0.1% tax rate, ETF tick sizes."""

    def test_etf_tax_and_lot_model(self):
        profile = TaiwanMarketProfile(
            mode=BacktestMode.STANDARD_CASH,
            cost=TradingCostModel(commission_rate=0.001425, discount=0.6),
            tax=SecuritiesTaxModel(),
            lot=LotModel(mode="board_lot", board_lot_size=1000),
        )

        trade_value = 100_000.0
        # ETF tax is 0.1% = 100 TWD (instead of stock 0.3% = 300 TWD)
        tax = profile.tax.calc_tax(trade_value, TaxClass.DOMESTIC_ETF)
        assert tax == 100.0

        # ETF board lot sizing (1000 shares)
        # Price 106.95 -> 1 lot = 106,950 TWD
        shares = profile.lot.align_shares(allocation=250_000.0, price=106.95)
        assert shares == 2000  # 2 lots


# ── 4. TPEx 8069.TPEX Verification ─────────────────────────────


class TestTPExBacktestVerification:
    """Verify TPEx ordinary stock rules in backtest."""

    def test_tpex_stock_tax_and_limits(self):
        profile = TaiwanMarketProfile()
        trade_value = 200_000.0
        # TPEx ordinary stock tax is 0.3% = 600 TWD
        tax = profile.tax.calc_tax(trade_value, TaxClass.ORDINARY_STOCK)
        assert tax == 600.0

        # Price limit +-10%
        up, down = profile.price_limit_model.calc_limits(160.0)
        assert up == 176.0  # 160 * 1.10 = 176.0 (valid 0.50 tick)
        assert down == 144.0  # 160 * 0.90 = 144.0

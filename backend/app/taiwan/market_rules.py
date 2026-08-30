"""Taiwan market trading rules, models, and backtest market profiles.

Responsibilities:
  - Brokerage commission model (configurable rate, discount, and min fee)
  - Securities transaction tax model (stock, day-trade, ETF, regulatory expiry)
  - Trading unit lot model (board lot 1000 shares vs odd lot 1 share)
  - Price limit model (ordinary 10% vs no-limit ETF/IPO)
  - Tick size model (ordinary stock 6 tiers vs ETF 2 tiers)
  - Settlement model (T+2) decoupled from same-day trading eligibility
  - TaiwanMarketProfile uniting all models for backtest engine integration
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Literal

import numpy as np


# ── Enums ──────────────────────────────────────────────────────


class TaxClass(str, Enum):
    """Securities transaction tax classification."""
    ORDINARY_STOCK = "ordinary_stock"  # 0.3%
    DOMESTIC_ETF = "domestic_etf"      # 0.1%
    BOND_ETF = "bond_etf"              # 0.0% (effective until 2026-12-31)
    FOREIGN_ETF = "foreign_etf"        # 0.1%


class PriceLimitClass(str, Enum):
    """Price limit rule classification."""
    ORDINARY_TEN_PERCENT = "ordinary_10pct"
    NO_LIMIT = "no_limit"              # Foreign-component ETF, bond ETF, IPO 5 days
    LEVERAGED_DOMESTIC = "leveraged_domestic"  # Domestic leveraged/inverse ETF (10% * abs(multiplier))



class TickSizeClass(str, Enum):
    """Price tick size classification."""
    ORDINARY_STOCK = "ordinary_stock"  # 6 tiers
    ETF = "etf"                        # 2 tiers (<50: 0.01, >=50: 0.05)


class BacktestMode(str, Enum):
    """Backtest simulation modes."""
    STANDARD_CASH = "standard_cash"    # Conservative cash mode: same-day exit prohibited
    DAY_TRADING = "day_trading"        # Day-trading enabled: same-day exit allowed with 0.15% tax
    ODD_LOT = "odd_lot"                # Odd-lot mode: 1-share sizing with odd-lot min commission


# ── Exceptions ──────────────────────────────────────────────────


class RegulatoryRuleUnavailableError(ValueError):
    """Raised when a regulatory rule is unverified or expired beyond enacted statutory windows."""


# ── 1. Trading Cost (Commission) Model ─────────────────────────

EXAMPLE_BROKER_MIN_COMMISSION: float = 20.0  # Common broker floor for reference only


@dataclass(frozen=True)
class TradingCostModel:
    """Brokerage commission model.

    The legacy backtest default is 0.1425% (0.001425); it is not a statutory rate.
    Current TWSE guidance requires brokers to set their own commission rate.
    Minimum commission is strictly broker-specific. The statutory default is 0.0 (no exchange floor).
    Specific broker settings (e.g. 20 TWD standard, 1 TWD odd-lot) must be explicitly configured.
    """
    commission_rate: float = 0.001425  # Legacy backtest default; broker configuration in production.
    discount: float = 1.0              # 1.0 = full price; 0.6 = 60%; 0.28 = 28%
    minimum_commission: float = 0.0    # Statutory default 0.0 (broker-specific when > 0)

    def calc_commission(self, trade_value: float) -> float:
        """Calculate commission in TWD for a given trade value."""
        if trade_value <= 0:
            return 0.0
        fee = trade_value * self.commission_rate * self.discount
        if self.minimum_commission > 0:
            return max(self.minimum_commission, fee)
        return fee


# ── 2. Securities Transaction Tax Model ────────────────────────


@dataclass(frozen=True)
class SecuritiesTaxModel:
    """Securities transaction tax model.

    Taxes are levied EXCLUSIVELY on the SELL side.
    Day-trade and bond ETF exemptions have statutory effective windows.
    Querying beyond the enacted expiry date raises RegulatoryRuleUnavailableError rather than guessing.
    """
    ordinary_stock_rate: float = 0.003
    day_trade_stock_rate: float = 0.0015
    domestic_etf_rate: float = 0.001
    foreign_etf_rate: float = 0.001
    bond_etf_rate: float = 0.0

    # Statutory expiry dates from enacted legislation
    day_trade_expiry: date = date(2027, 12, 31)
    bond_etf_expiry: date = date(2026, 12, 31)

    def get_tax_rate(
        self,
        tax_class: TaxClass = TaxClass.ORDINARY_STOCK,
        is_day_trade: bool = False,
        trade_date: date | None = None,
    ) -> float:
        """Get applicable tax rate based on asset class, day-trade status, and date."""
        if is_day_trade:
            if trade_date is not None and trade_date > self.day_trade_expiry:
                raise RegulatoryRuleUnavailableError(
                    f"Day-trade tax incentive (0.15%) expired on {self.day_trade_expiry}. "
                    f"Statutory policy for trade_date {trade_date} is unverified by law."
                )
            return self.day_trade_stock_rate

        if tax_class == TaxClass.BOND_ETF:
            if trade_date is not None and trade_date > self.bond_etf_expiry:
                raise RegulatoryRuleUnavailableError(
                    f"Bond ETF 0% tax exemption expired on {self.bond_etf_expiry}. "
                    f"Statutory policy for trade_date {trade_date} is unverified by law."
                )
            return self.bond_etf_rate

        if tax_class in (TaxClass.DOMESTIC_ETF, TaxClass.FOREIGN_ETF):
            return self.domestic_etf_rate

        return self.ordinary_stock_rate


    def calc_tax(
        self,
        trade_value: float,
        tax_class: TaxClass = TaxClass.ORDINARY_STOCK,
        is_day_trade: bool = False,
        trade_date: date | None = None,
    ) -> float:
        """Calculate transaction tax in TWD (sell-side only)."""
        if trade_value <= 0:
            return 0.0
        rate = self.get_tax_rate(tax_class=tax_class, is_day_trade=is_day_trade, trade_date=trade_date)
        return trade_value * rate


# ── 3. Trading Unit (Lot) Model ────────────────────────────────


@dataclass(frozen=True)
class LotModel:
    """Trading unit model.

    Taiwan standard board lot (張) is 1,000 shares.
    Odd-lot trading allows 1 to 999 shares.
    """
    mode: Literal["board_lot", "odd_lot"] = "board_lot"
    board_lot_size: int = 1000
    odd_lot_size: int = 1

    @property
    def lot_size(self) -> int:
        return self.board_lot_size if self.mode == "board_lot" else self.odd_lot_size

    def align_shares(self, allocation: float, price: float, buy_cost_pct: float = 0.0) -> int:
        """Calculate max allowable integer shares within allocation adhering to lot size."""
        if price <= 0 or allocation <= 0:
            return 0
        raw_shares = allocation / (price * (1.0 + buy_cost_pct))
        unit = self.lot_size
        return int(math.floor(raw_shares / unit) * unit)


# ── 4. Price Tick Size Model ───────────────────────────────────


class TickSizeModel:
    """Price tick size model adhering to TWSE Rule 62 & ETF regulations."""

    @staticmethod
    def get_tick_size(
        price: float,
        tick_class: TickSizeClass = TickSizeClass.ORDINARY_STOCK,
        trade_date: date | None = None,  # noqa: ARG004
    ) -> float:
        """Get minimum price movement for a given price and asset category."""
        if price <= 0:
            return 0.01

        if tick_class == TickSizeClass.ETF:
            # ETF: 2 tiers (<50: 0.01, >=50: 0.05)
            if price < 50.0:
                return 0.01
            return 0.05

        # Ordinary Stock: 6 tiers
        if price < 10.0:
            return 0.01
        if price < 50.0:
            return 0.05
        if price < 100.0:
            return 0.10
        if price < 500.0:
            return 0.50
        if price < 1000.0:
            return 1.00
        return 5.00

    @classmethod
    def round_price_limit_boundary(
        cls,
        raw_price: float,
        direction: Literal["up", "down"],
        tick_class: TickSizeClass = TickSizeClass.ORDINARY_STOCK,
        trade_date: date | None = None,
    ) -> float:
        """Round statutory price limit boundaries strictly without violating exchange bands.

        direction='up': floor to tick so limit-up never exceeds statutory ceiling (+10%).
        direction='down': ceil to tick so limit-down never breaches statutory floor (-10%).
        """
        if raw_price <= 0:
            return 0.0
        tick = cls.get_tick_size(raw_price, tick_class=tick_class, trade_date=trade_date)
        if direction == "up":
            steps = math.floor(raw_price / tick + 1e-9)
        else:
            steps = math.ceil(raw_price / tick - 1e-9)
        return round(steps * tick, 4)

    @classmethod
    def round_order_price(
        cls,
        price: float,
        tick_class: TickSizeClass = TickSizeClass.ORDINARY_STOCK,
        mode: Literal["nearest", "floor", "ceil"] = "nearest",
        trade_date: date | None = None,
    ) -> float:
        """Round order or execution fill price to valid tick without skewing strategy intent.

        mode='nearest': standard closest tick (default for orders/fills).
        mode='floor': round downward to tick.
        mode='ceil': round upward to tick.
        """
        if price <= 0:
            return 0.0
        tick = cls.get_tick_size(price, tick_class=tick_class, trade_date=trade_date)
        if mode == "floor":
            steps = math.floor(price / tick + 1e-9)
        elif mode == "ceil":
            steps = math.ceil(price / tick - 1e-9)
        else:
            steps = round(price / tick)
        return round(steps * tick, 4)

    @classmethod
    def round_to_valid_tick(
        cls,
        price: float,
        tick_class: TickSizeClass = TickSizeClass.ORDINARY_STOCK,
        side: Literal["buy", "sell", "round"] = "round",
        trade_date: date | None = None,
    ) -> float:
        """General rounding entry point.

        side='round': standard nearest tick (recommended for general orders/fills).
        side='buy': floor to tick (conservative boundary limit).
        side='sell': ceil to tick (conservative boundary limit).
        """
        if side == "buy":
            return cls.round_price_limit_boundary(price, direction="up", tick_class=tick_class, trade_date=trade_date)
        if side == "sell":
            return cls.round_price_limit_boundary(price, direction="down", tick_class=tick_class, trade_date=trade_date)
        return cls.round_order_price(price, tick_class=tick_class, mode="nearest", trade_date=trade_date)


# ── 5. Price Limit Model ───────────────────────────────────────


class PriceLimitModel:
    """Daily price limit model (+-10% standard, multiplier-adjusted, or no-limit). Single source of truth."""

    @staticmethod
    def get_limit_pct(
        limit_class: PriceLimitClass = PriceLimitClass.ORDINARY_TEN_PERCENT,
        multiplier: float = 1.0,
    ) -> float | None:
        """Authoritative single source of truth for Taiwan price limit percentages.

        Returns:
            0.10 for ordinary stocks and domestic equity ETFs.
            0.10 * abs(multiplier) for domestic leveraged/inverse ETFs (e.g. 0.20 for 2X, 0.10 for -1X).
            None for NO_LIMIT class (foreign ETFs, bond ETFs, 5-day IPOs).
        """
        if limit_class == PriceLimitClass.NO_LIMIT:
            return None
        if limit_class == PriceLimitClass.LEVERAGED_DOMESTIC:
            return round(0.10 * abs(multiplier), 4)
        return 0.10

    @classmethod
    def calc_limits_for_pct(
        cls,
        ref_price: float,
        limit_pct: float | None,
        tick_class: TickSizeClass = TickSizeClass.ORDINARY_STOCK,
    ) -> tuple[float | None, float | None]:
        """Calculate (limit_up, limit_down) for an explicit percentage, aligned to valid ticks."""
        if ref_price <= 0 or limit_pct is None:
            return None, None
        up_raw = ref_price * (1.0 + limit_pct)
        down_raw = max(0.01, ref_price * (1.0 - limit_pct))
        limit_up = TickSizeModel.round_price_limit_boundary(up_raw, direction="up", tick_class=tick_class)
        limit_down = TickSizeModel.round_price_limit_boundary(down_raw, direction="down", tick_class=tick_class)
        return limit_up, limit_down

    @classmethod
    def calc_limits(
        cls,
        ref_price: float,
        limit_class: PriceLimitClass = PriceLimitClass.ORDINARY_TEN_PERCENT,
        tick_class: TickSizeClass = TickSizeClass.ORDINARY_STOCK,
        multiplier: float = 1.0,
    ) -> tuple[float | None, float | None]:
        """Calculate (limit_up, limit_down) rounded to valid ticks."""
        pct = cls.get_limit_pct(limit_class=limit_class, multiplier=multiplier)
        return cls.calc_limits_for_pct(ref_price=ref_price, limit_pct=pct, tick_class=tick_class)




# ── 6. Settlement Model ────────────────────────────────────────


@dataclass(frozen=True)
class SettlementModel:
    """Settlement cycle model (T+2 cash/stock delivery).

    Settlement cycle is purely for delivery timing and cash accounting.
    It does NOT govern position same-day exit restrictions.
    """
    settlement_days: int = 2


# ── 7. Unified Taiwan Market Profile ───────────────────────────


@dataclass
class TaiwanMarketProfile:
    """Comprehensive Taiwan market profile for backtest execution."""
    mode: BacktestMode = BacktestMode.STANDARD_CASH
    cost: TradingCostModel = field(default_factory=TradingCostModel)
    tax: SecuritiesTaxModel = field(default_factory=SecuritiesTaxModel)
    lot: LotModel = field(default_factory=LotModel)
    settlement: SettlementModel = field(default_factory=SettlementModel)
    tick_size_model: TickSizeModel = field(default_factory=TickSizeModel)
    price_limit_model: PriceLimitModel = field(default_factory=PriceLimitModel)

    def can_same_day_exit(
        self,
        is_day_trade_eligible: bool = True,
    ) -> bool:
        """Determine if a position entered today may be exited today.

        STANDARD_CASH mode: Prohibited (must hold overnight to T+1).
        DAY_TRADING mode: Allowed if instrument is eligible.
        """
        if self.mode == BacktestMode.STANDARD_CASH:
            return False
        if self.mode == BacktestMode.DAY_TRADING:
            return bool(is_day_trade_eligible)
        if self.mode == BacktestMode.ODD_LOT:
            return False
        return False

    def buy_transaction_cost(self, trade_value: float) -> float:
        """Buy cost: commission only."""
        return self.cost.calc_commission(trade_value)

    def sell_transaction_cost(
        self,
        trade_value: float,
        tax_class: TaxClass = TaxClass.ORDINARY_STOCK,
        is_day_trade: bool = False,
        trade_date: date | None = None,
    ) -> float:
        """Sell cost: commission + securities transaction tax."""
        comm = self.cost.calc_commission(trade_value)
        tax = self.tax.calc_tax(
            trade_value,
            tax_class=tax_class,
            is_day_trade=is_day_trade,
            trade_date=trade_date,
        )
        return comm + tax

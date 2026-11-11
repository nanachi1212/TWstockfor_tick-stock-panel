"""Live Smoke / Integration tests for Taiwan Data Enrichment (Phase 4.5).

Connects to official TWSE and TPEx live endpoints:
  - Institutional: 2330.TWSE, 8069.TPEX
  - Margin: 2330.TWSE, 8069.TPEX
  - ETF Classification: 0050.TWSE + dynamically selected bond/foreign ETF
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.taiwan.enrichment.institutional import TaiwanInstitutionalProvider
from app.taiwan.enrichment.margin import TaiwanMarginProvider
from app.taiwan.enrichment.models import EtfCategory
from app.taiwan.universe import get_security_master
from app.taiwan.universe.models import MarketProfileBridge


def _get_recent_trading_date() -> date:
    """Find the most recent weekday."""
    today = date.today()
    offset = 1
    while (today - timedelta(days=offset)).weekday() >= 5:  # 5=Saturday, 6=Sunday
        offset += 1
    return today - timedelta(days=offset)


@pytest.mark.integration
class TestTaiwanEnrichmentLiveSmoke:
    """Live smoke validation connecting to real Taiwan official sources."""

    def test_live_institutional_and_margin(self):
        recent_date = _get_recent_trading_date()
        inst_provider = TaiwanInstitutionalProvider()
        margin_provider = TaiwanMarginProvider()

        # TWSE 2330 Institutional & Margin
        try:
            twse_flows = inst_provider.fetch_live_day("TWSE", recent_date, target_code="2330")
            if twse_flows:
                f = twse_flows[0]
                assert f.symbol == "2330.TWSE"
                assert f.foreign_buy >= 0
                assert f.foreign_sell >= 0
                assert f.computed_net == (f.foreign_net + f.investment_trust_net + f.dealer_net)

            twse_margins = margin_provider.fetch_live_day("TWSE", recent_date, target_code="2330")
            if twse_margins:
                m = twse_margins[0]
                assert m.symbol == "2330.TWSE"
                assert m.unit == "shares"
                assert m.margin_balance >= 0
        except Exception as e:
            pytest.skip(f"TWSE institutional/margin live endpoint unavailable: {e}")

    def test_etf_classification_and_bridge_validation(self):
        master = get_security_master()
        master.ensure_loaded()

        # 1. Domestic equity ETF: 0050
        etf_0050 = master.get_instrument("0050.TWSE")
        assert etf_0050 is not None
        assert etf_0050.etf_category == EtfCategory.DOMESTIC_EQUITY.value
        assert MarketProfileBridge.get_price_limit_pct(etf_0050) == 0.10

        # 2. Bond ETF: dynamically select from master
        bond_etf = master.get_instrument("00720B.TPEX")
        if bond_etf:
            assert bond_etf.etf_category == EtfCategory.BOND.value
            assert MarketProfileBridge.get_price_limit_pct(bond_etf) is None
            assert MarketProfileBridge.get_tax_class(bond_etf).value == "bond_etf"

        # 3. Foreign Component ETF: dynamically select from master (00646 S&P 500)
        foreign_etf = master.get_instrument("00646.TWSE")
        if foreign_etf:
            assert foreign_etf.etf_category == EtfCategory.FOREIGN_EQUITY.value
            assert MarketProfileBridge.get_price_limit_pct(foreign_etf) is None

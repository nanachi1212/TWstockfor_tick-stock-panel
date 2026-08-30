"""Comprehensive Unit Tests for Taiwan Data Enrichment (Phase 4.5).

100% Offline tests using injected payloads and HTML fixtures (zero internet dependencies).
Covers:
  - TWSE & TPEx Three Major Institutional flows parsing
  - Institutional net integrity: computed_net vs official_net and discrepancy handling
  - Institutional volume unit: strictly shares
  - TWSE & TPEx Margin trading & Short selling parsing
  - Margin unit normalization: lots (張) * 1000 -> shares (股)
  - Unified SourceMeta tracking, available_fields, and status
  - StalePolicy evaluation per dataset
  - Official Quote snapshots (TWSE & TPEx) and transparent fallback with reasons
  - Benchmark Index parsing (TAIEX & TPEx Index) with dedicated schema
  - Hardened ETF classification and strict UNKNOWN ETF safety in MarketProfileBridge
  - Polars factor computation (5-day rolling net, margin balance change, short-margin ratio)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import polars as pl
import pytest

from app.taiwan.enrichment.factors import compute_chip_factors, compute_margin_factors
from app.taiwan.enrichment.index import TaiwanIndexProvider
from app.taiwan.enrichment.institutional import (
    TaiwanInstitutionalProvider,
    TpexInstitutionalAdapter,
    TwseInstitutionalAdapter,
)
from app.taiwan.enrichment.margin import (
    TaiwanMarginProvider,
    TpexMarginAdapter,
    TwseMarginAdapter,
)
from app.taiwan.enrichment.models import (
    DatasetType,
    EtfCategory,
    StalePolicy,
)
from app.taiwan.enrichment.quote import TaiwanOfficialQuoteProvider
from app.taiwan.market_rules import PriceLimitClass, TaxClass, TickSizeClass
from app.taiwan.universe.models import MarketProfileBridge, TaiwanInstrument


# ── 1. Institutional Flows Tests ────────────────────────────────


class TestInstitutionalEnrichment:
    """Verify TWSE & TPEx institutional investors flows and net integrity."""

    def test_twse_institutional_parsing_and_integrity(self):
        adapter = TwseInstitutionalAdapter()
        # Mock TWSE T86 payload for 2330
        payload = {
            "stat": "OK",
            "data": [
                [
                    "2330", "台積電",
                    "15,000,000", "10,000,000", "5,000,000",  # Foreign buy, sell, net (idx 2, 3, 4)
                    "", "", "",
                    "2,000,000", "500,000", "1,500,000",      # Trust buy, sell, net (idx 8, 9, 10)
                    "800,000",                                 # Dealer net (idx 11)
                    "600,000", "200,000", "400,000",           # Dealer prop buy, sell, net (12, 13, 14)
                    "500,000", "100,000", "400,000",           # Dealer hedge buy, sell, net (15, 16, 17)
                    "7,300,000",                               # Official total net (idx 18) = 5M + 1.5M + 800k = 7.3M
                ]
            ]
        }
        trade_dt = date(2026, 8, 28)
        flows = adapter.parse_payload(payload, trade_dt, "https://mock.twse/t86", target_code="2330")
        assert len(flows) == 1
        flow = flows[0]

        assert flow.symbol == "2330.TWSE"
        assert flow.foreign_net == 5_000_000
        assert flow.investment_trust_net == 1_500_000
        assert flow.dealer_net == 800_000
        assert flow.unit == "shares"
        assert flow.dealer_buy == 1_100_000  # 600k + 500k
        assert flow.dealer_sell == 300_000   # 200k + 100k
        assert flow.official_net == 7_300_000
        assert flow.computed_net == 7_300_000
        assert flow.has_discrepancy is False
        assert flow.meta.status == ("stale" if flow.meta.is_stale else "official")
        assert flow.meta.provider == "twse"
        assert flow.meta.source == "twse:t86"
        assert flow.meta.source_url == "https://mock.twse/t86"
        assert flow.meta.retrieved_at == flow.meta.fetched_at
        assert flow.meta.trade_date == trade_dt

    def test_twse_institutional_discrepancy_detection(self):
        adapter = TwseInstitutionalAdapter()
        # Intentional discrepancy: official total net says 8,000,000 but computed sum is 7,300,000
        payload = {
            "stat": "OK",
            "data": [
                [
                    "2330", "台積電",
                    "15,000,000", "10,000,000", "5,000,000",
                    "", "", "",
                    "2,000,000", "500,000", "1,500,000",
                    "800,000",
                    "600,000", "200,000", "400,000",
                    "500,000", "100,000", "400,000",
                    "8,000,000",  # Discrepant official net!
                ]
            ]
        }
        trade_dt = date(2026, 8, 28)
        flows = adapter.parse_payload(payload, trade_dt, "https://mock.twse/t86")
        assert len(flows) == 1
        flow = flows[0]
        assert flow.official_net == 8_000_000
        assert flow.computed_net == 7_300_000
        assert flow.has_discrepancy is True
        assert flow.meta.status == ("stale" if flow.meta.is_stale else "official")

    def test_tpex_institutional_parsing_and_integrity(self):
        adapter = TpexInstitutionalAdapter()
        payload = {
            "aaData": [
                [
                    "8069", "元太",
                    "3,000,000", "2,000,000", "1,000,000",  # Foreign buy, sell, net (idx 2, 3, 4)
                    "", "", "", "", "", "",
                    "1,200,000", "200,000", "1,000,000",    # Trust buy, sell, net (idx 11, 12, 13)
                    "300,000", "100,000", "200,000",        # Prop buy, sell, net (idx 14, 15, 16)
                    "200,000", "100,000", "100,000",        # Hedge buy, sell, net (idx 17, 18, 19)
                    "500,000", "200,000", "300,000",      # Dealer buy, sell, net (idx 20-22)
                    "2,300,000",                            # Official total net (idx 23)
                ]
            ]
        }
        trade_dt = date(2026, 8, 28)
        flows = adapter.parse_payload(payload, trade_dt, "https://mock.tpex/insti", target_code="8069")
        assert len(flows) == 1
        flow = flows[0]

        assert flow.symbol == "8069.TPEX"
        assert flow.foreign_net == 1_000_000
        assert flow.investment_trust_net == 1_000_000
        assert flow.dealer_net == 300_000
        assert flow.official_net == 2_300_000
        assert flow.computed_net == 2_300_000
        assert flow.has_discrepancy is False
        assert flow.meta.provider == "tpex"
        assert flow.meta.status == ("stale" if flow.meta.is_stale else "official")

    @pytest.mark.parametrize("raw", ["", "-", "--", "N/A", "abc", "12x3"])
    def test_institutional_missing_or_malformed_is_not_zero(self, raw):
        row = ["2330", "台積電", raw, "0", "0", "0", "0", "0", "0", "0", "0",
               "0", "0", "0", "0", "0", "0", "0", "0"]
        with pytest.raises(ValueError):
            TwseInstitutionalAdapter().parse_payload(
                {"data": [row]}, date(2026, 8, 28), "https://mock.twse/t86"
            )

    def test_institutional_explicit_zero_is_preserved(self):
        row = ["2330", "台積電", "0.0", "0", "0", "0", "0", "0", "0", "0", "0",
               "0", "0", "0", "0", "0", "0", "0", "0"]
        flow = TwseInstitutionalAdapter().parse_payload(
            {"data": [row]}, date(2026, 8, 28), "https://mock.twse/t86"
        )[0]
        assert flow.foreign_buy == 0


# ── 2. Margin Trading & Short Selling Tests ─────────────────────


class TestMarginEnrichment:
    """Verify Margin trading parsing and lots -> shares deterministic normalization."""

    def test_twse_margin_lots_to_shares_normalization(self):
        adapter = TwseMarginAdapter()
        # In TWSE MI_MARGN, quantities are expressed in lots (張). Multiplier is 1000.
        payload = {
            "data": [
                [
                    "2330", "台積電",
                    "1,500", "1,000", "50", "20,000", "20,450",  # Margin: buy, sell, cash_red, prev, bal (idx 2-6)
                    "",
                    "200", "300", "10", "2,000", "2,090",       # Short: cover, sell, stock_red, prev, bal (idx 8-12)
                    "", "", "合格標的",
                ]
            ]
        }
        trade_dt = date(2026, 8, 28)
        margins = adapter.parse_payload(payload, trade_dt, "https://mock.twse/margin", target_code="2330", lot_multiplier=1000)
        assert len(margins) == 1
        m = margins[0]

        assert m.symbol == "2330.TWSE"
        assert m.unit == "shares"
        assert m.source_unit == "lots"
        assert m.lot_multiplier == 1000

        # Verify shares normalization (*1000)
        assert m.margin_buy == 1_500_000
        assert m.margin_sell == 1_000_000
        assert m.margin_previous_balance == 20_000_000
        assert m.margin_balance == 20_450_000
        assert m.margin_change == 450_000

        assert m.short_sell == 300_000
        assert m.short_cover == 200_000
        assert m.short_previous_balance == 2_000_000
        assert m.short_balance == 2_090_000
        assert m.short_change == 90_000

        assert m.short_margin_ratio is None
        assert m.note == "合格標的"
        assert m.meta.provider == "twse"
        assert m.meta.source == "twse:mi_margn"
        assert m.meta.source_url == "https://mock.twse/margin"
        assert m.meta.retrieved_at == m.meta.fetched_at
        assert m.meta.trade_date == trade_dt
        assert m.meta.status == ("stale" if m.meta.is_stale else "official")

    def test_tpex_margin_parsing(self):
        adapter = TpexMarginAdapter()
        payload = {
            "aaData": [
                [
                    "8069", "元太",
                    "5,000", "500", "200", "10", "5,290",  # Margin: prev, buy, sell, cash_red, bal (idx 2-6)
                    "", "", "",
                    "1,000", "100", "50", "0", "1,050",     # Short: prev, sell, cover, red, bal (idx 10-14)
                    "", "", "", "", "上櫃融資融券",
                ]
            ]
        }
        trade_dt = date(2026, 8, 28)
        margins = adapter.parse_payload(payload, trade_dt, "https://mock.tpex/margin", target_code="8069")
        assert len(margins) == 1
        m = margins[0]

        assert m.symbol == "8069.TPEX"
        assert m.margin_balance == 5_290_000
        assert m.margin_change == 290_000
        assert m.short_balance == 1_050_000
        assert m.short_change == 50_000
        assert m.short_margin_ratio is None
        assert m.meta.provider == "tpex"

    @pytest.mark.parametrize("raw", ["", "-", "--", "N/A", "abc", "12x3"])
    def test_margin_missing_or_malformed_is_not_zero(self, raw):
        row = ["2330", "台積電", raw, "0", "0", "0", "0", "", "0", "0", "0", "0", "0"]
        with pytest.raises(ValueError):
            TwseMarginAdapter().parse_payload(
                {"data": [row]}, date(2026, 8, 28), "https://mock.twse/margin"
            )

    def test_margin_explicit_zero_is_preserved(self):
        row = ["2330", "台積電", "0.0", "0", "0", "0", "0", "", "0", "0", "0", "0", "0"]
        margin = TwseMarginAdapter().parse_payload(
            {"data": [row]}, date(2026, 8, 28), "https://mock.twse/margin"
        )[0]
        assert margin.margin_buy == 0


# ── 3. SourceMeta & StalePolicy Tests ───────────────────────────


class TestSourceMetaAndStalePolicy:
    """Verify dataset-specific freshness rules."""

    def test_stale_policy_differentiation(self):
        now = datetime(2026, 8, 30, 18, 0)
        friday_dt = date(2026, 8, 28)

        # Quotes should be stale after 4h past session
        assert StalePolicy.is_stale(DatasetType.QUOTE, friday_dt, fetched_at=datetime(2026, 8, 28, 14, 0), now=now) is True

        # Daily / Chip / Margin allow weekend buffer (4 days threshold)
        # Friday trade date compared to Sunday evening is within 4 days -> not stale
        assert StalePolicy.is_stale(DatasetType.INSTITUTIONAL, friday_dt, fetched_at=datetime(2026, 8, 28, 18, 0), now=now) is False
        assert StalePolicy.is_stale(DatasetType.MARGIN, friday_dt, fetched_at=datetime(2026, 8, 28, 18, 0), now=now) is False

        # Older date (e.g. 10 days ago) is stale
        old_dt = date(2026, 8, 15)
        assert StalePolicy.is_stale(DatasetType.INSTITUTIONAL, old_dt, fetched_at=datetime(2026, 8, 15, 18, 0), now=now) is True


class TestOfficialFailureSemantics:
    """Institutional/margin have no reliable third-party fallback seam."""

    @pytest.mark.parametrize("provider", [TaiwanInstitutionalProvider(), TaiwanMarginProvider()])
    def test_official_network_failure_is_not_empty_success(self, provider, monkeypatch):
        def fail(*_args, **_kwargs):
            raise TimeoutError("official endpoint timeout")

        monkeypatch.setattr("urllib.request.urlopen", fail)
        with pytest.raises(TimeoutError, match="official endpoint timeout"):
            provider.fetch_live_day("TWSE", date(2026, 8, 28), target_code="2330")


# ── 4. Quote Snapshots & Fallback Tests ─────────────────────────


class TestQuoteProviderWithFallback:
    """Verify official quote snapshots and transparent fallback with non-empty reason."""

    def test_twse_official_quote_parsing(self):
        provider = TaiwanOfficialQuoteProvider()
        rows = [
            {
                "Code": "2330",
                "Name": "台積電",
                "TradeVolume": "35,000,000",
                "TradeValue": "34,000,000,000",
                "OpeningPrice": "970.0",
                "HighestPrice": "980.0",
                "LowestPrice": "965.0",
                "ClosingPrice": "975.0",
                "Change": "+15.0",
            }
        ]
        quote = provider.get_quote_with_fallback("2330.TWSE", "台積電", live_rows=rows)
        assert quote.symbol == "2330.TWSE"
        assert quote.price == 975.0
        assert quote.open == 970.0
        assert quote.high == 980.0
        assert quote.low == 965.0
        assert quote.previous_close == 960.0
        assert quote.change == 15.0
        assert quote.change_pct == 1.56
        assert quote.volume == 35_000_000
        assert quote.meta.status == "official_close"
        assert quote.meta.fallback_reason is None

    def test_quote_fallback_to_daily_kline_with_reason(self):
        provider = TaiwanOfficialQuoteProvider()
        # Official live rows do NOT contain 2330
        empty_rows: list[dict] = []
        daily_fallback = {
            "date": date(2026, 8, 28),
            "open": 960.0,
            "high": 970.0,
            "low": 955.0,
            "close": 965.0,
            "pre_close": 950.0,
            "change": 15.0,
            "change_pct": 1.58,
            "volume": 28_000_000,
            "amount": 27_000_000_000,
        }

        quote = provider.get_quote_with_fallback(
            "2330.TWSE",
            "台積電",
            live_rows=empty_rows,
            fallback_daily_row=daily_fallback,
        )

        assert quote.symbol == "2330.TWSE"
        assert quote.price == 965.0
        assert quote.meta.status == "official_monthly_fallback"
        assert quote.meta.fallback_reason is not None
        assert "Official snapshot unavailable" in quote.meta.fallback_reason


# ── 5. Market Index Provider Tests ──────────────────────────────


class TestMarketIndexEnrichment:
    """Verify TAIEX and TPEx Index parsing with dedicated schema."""

    def test_taiex_parsing(self):
        provider = TaiwanIndexProvider()
        rows = [
            {
                "Date": "1150828",
                "OpeningIndex": "22,100.50",
                "HighestIndex": "22,250.00",
                "LowestIndex": "22,050.20",
                "ClosingIndex": "22,200.00",
            }
        ]
        series = provider.parse_taiex_rows(rows)
        assert len(series) == 1
        idx = series[0]

        assert idx.symbol == "TAIEX"
        assert idx.name == "發行量加權股價指數"
        assert idx.date == date(2026, 8, 28)
        assert idx.open == 22100.50
        assert idx.close == 22200.00
        assert idx.meta.source == "twse:MI_5MINS_HIST"

    def test_tpex_index_parsing(self):
        provider = TaiwanIndexProvider()
        rows = [
            {
                "Date": "115/08/28",
                "Open": "265.50",
                "High": "268.00",
                "Low": "264.80",
                "Close": "267.50",
                "Change": "+2.00",
            }
        ]
        series = provider.parse_tpex_rows(rows)
        assert len(series) == 1
        idx = series[0]

        assert idx.symbol == "TPEX_INDEX"
        assert idx.name == "櫃買指數"
        assert idx.date == date(2026, 8, 28)
        assert idx.close == 267.50
        assert idx.previous_close == 265.50
        assert idx.change == 2.00
        assert idx.change_pct == 0.75


# ── 6. ETF Hardened Classification & Safety Tests ───────────────


class TestEtfClassificationAndSafety:
    """Verify ETF categorization and strict safety for UNKNOWN ETFs in MarketProfileBridge."""

    def test_etf_subclass_rule_bridge(self):
        # 1. Domestic Equity ETF: 0050 (Confirmed official_metadata)
        etf_0050 = TaiwanInstrument(
            symbol="0050.TWSE",
            code="0050",
            exchange="TWSE",
            name="元大台灣50",
            instrument_type="etf",
            listing_status="active",
            listing_date="2003/06/30",
            isin="TW0000050004",
            industry=None,
            cfi_code="CEOGEU",
            raw_category="ETF",
            is_supported=True,
            source="TWSE_ISIN",
            updated_at="2026-08-30",
            etf_category=EtfCategory.DOMESTIC_EQUITY.value,
            classification_source="official_metadata",
            underlying_scope="domestic",
            leverage_multiplier=1.0,
        )
        assert MarketProfileBridge.get_tax_class(etf_0050) == TaxClass.DOMESTIC_ETF
        assert MarketProfileBridge.get_tick_size_class(etf_0050) == TickSizeClass.ETF
        assert MarketProfileBridge.get_price_limit_pct(etf_0050) == 0.10
        assert MarketProfileBridge.get_price_limit_class(etf_0050) == PriceLimitClass.ORDINARY_TEN_PERCENT
        up, down = MarketProfileBridge.calc_limits(100.0, etf_0050)
        assert up == 110.0 and down == 90.0

        # 2. Bond ETF: 00720B (Confirmed cfi_code)
        bond_etf = TaiwanInstrument(
            symbol="00720B.TPEX",
            code="00720B",
            exchange="TPEX",
            name="元大投資級公司債",
            instrument_type="etf",
            listing_status="active",
            listing_date="2018/01/09",
            isin="TW00000720B5",
            industry=None,
            cfi_code="CEOJBU",
            raw_category="ETF",
            is_supported=True,
            source="TPEX_ISIN",
            updated_at="2026-08-30",
            etf_category=EtfCategory.BOND.value,
            classification_source="cfi_code",
            underlying_scope="foreign",
            leverage_multiplier=1.0,
        )
        assert MarketProfileBridge.get_tax_class(bond_etf) == TaxClass.BOND_ETF
        assert MarketProfileBridge.get_price_limit_pct(bond_etf) is None
        assert MarketProfileBridge.get_price_limit_class(bond_etf) == PriceLimitClass.NO_LIMIT
        assert MarketProfileBridge.calc_limits(100.0, bond_etf) == (None, None)

        # 3. Foreign Component ETF: 00646 S&P 500 (Confirmed official_metadata)
        foreign_etf = TaiwanInstrument(
            symbol="00646.TWSE",
            code="00646",
            exchange="TWSE",
            name="元大S&P500",
            instrument_type="etf",
            listing_status="active",
            listing_date="2015/12/14",
            isin="TW0000064666",
            industry=None,
            cfi_code="CEOJEU",
            raw_category="ETF",
            is_supported=True,
            source="TWSE_ISIN",
            updated_at="2026-08-30",
            etf_category=EtfCategory.FOREIGN_EQUITY.value,
            classification_source="official_metadata",
            underlying_scope="foreign",
            leverage_multiplier=1.0,
        )
        assert MarketProfileBridge.get_price_limit_pct(foreign_etf) is None
        assert MarketProfileBridge.get_price_limit_class(foreign_etf) == PriceLimitClass.NO_LIMIT
        assert MarketProfileBridge.calc_limits(100.0, foreign_etf) == (None, None)

    def test_leveraged_and_inverse_etf_statutory_rules(self):
        """Verify domestic leveraged (+-20%), domestic inverse (+-10%), foreign leveraged (NO_LIMIT), and bond tax audit."""
        # 1. Domestic Leveraged 2X: 00631L (元大台灣50正2)
        etf_00631l = TaiwanInstrument(
            symbol="00631L.TWSE",
            code="00631L",
            exchange="TWSE",
            name="元大台灣50正2",
            instrument_type="etf",
            listing_status="active",
            listing_date="2014/10/31",
            isin="TW00000631L2",
            industry=None,
            cfi_code="CEOGDU",
            raw_category="ETF",
            is_supported=True,
            source="TWSE_ISIN",
            updated_at="2026-08-30",
            etf_category=EtfCategory.LEVERAGED.value,
            classification_source="official_metadata",
            underlying_scope="domestic",
            leverage_multiplier=2.0,
        )
        assert MarketProfileBridge.get_price_limit_pct(etf_00631l) == 0.20
        assert MarketProfileBridge.get_price_limit_class(etf_00631l) == PriceLimitClass.LEVERAGED_DOMESTIC
        assert MarketProfileBridge.get_tax_class(etf_00631l) == TaxClass.DOMESTIC_ETF
        # Tick-aligned calculation for ref_price = 100.0 (tick is 0.05 for ETF >= 50)
        up, down = MarketProfileBridge.calc_limits(100.0, etf_00631l)
        assert up == 120.0 and down == 80.0
        # Tick-aligned calculation for ref_price = 45.0 (tick is 0.01 for ETF < 50, raw up 54.0 -> tick 0.05)
        up_45, down_45 = MarketProfileBridge.calc_limits(45.0, etf_00631l)
        assert up_45 == 54.0 and down_45 == 36.0

        # 2. Domestic Inverse -1X: 00632R (元大台灣50反1)
        etf_00632r = TaiwanInstrument(
            symbol="00632R.TWSE",
            code="00632R",
            exchange="TWSE",
            name="元大台灣50反1",
            instrument_type="etf",
            listing_status="active",
            listing_date="2014/10/31",
            isin="TW00000632R7",
            industry=None,
            cfi_code="CEOGDU",
            raw_category="ETF",
            is_supported=True,
            source="TWSE_ISIN",
            updated_at="2026-08-30",
            etf_category=EtfCategory.INVERSE.value,
            classification_source="official_metadata",
            underlying_scope="domestic",
            leverage_multiplier=-1.0,
        )
        assert MarketProfileBridge.get_price_limit_pct(etf_00632r) == 0.10
        assert MarketProfileBridge.get_price_limit_class(etf_00632r) == PriceLimitClass.LEVERAGED_DOMESTIC
        up_r, down_r = MarketProfileBridge.calc_limits(10.0, etf_00632r)
        assert up_r == 11.0 and down_r == 9.0

        # 3. Foreign Leveraged 2X: 00633L (富邦上証正2)
        etf_00633l = TaiwanInstrument(
            symbol="00633L.TWSE",
            code="00633L",
            exchange="TWSE",
            name="富邦上証正2",
            instrument_type="etf",
            listing_status="active",
            listing_date="2014/11/25",
            isin="TW00000633L8",
            industry=None,
            cfi_code="CEOGDU",
            raw_category="ETF",
            is_supported=True,
            source="TWSE_ISIN",
            updated_at="2026-08-30",
            etf_category=EtfCategory.LEVERAGED.value,
            classification_source="official_metadata",
            underlying_scope="foreign",
            leverage_multiplier=2.0,
        )
        assert MarketProfileBridge.get_price_limit_pct(etf_00633l) is None
        assert MarketProfileBridge.get_price_limit_class(etf_00633l) == PriceLimitClass.NO_LIMIT
        assert MarketProfileBridge.calc_limits(50.0, etf_00633l) == (None, None)

        # 4. Leveraged Bond ETF: 00688L (國泰20年美債正2)
        # Audit: Must be subject to 0.1% tax (NOT 0% tax exemption) under MoF ruling!
        bond_2x = TaiwanInstrument(
            symbol="00688L.TWSE",
            code="00688L",
            exchange="TWSE",
            name="國泰20年美債正2",
            instrument_type="etf",
            listing_status="active",
            listing_date="2017/04/13",
            isin="TW00000688L6",
            industry=None,
            cfi_code="CEOGBU",
            raw_category="ETF",
            is_supported=True,
            source="TWSE_ISIN",
            updated_at="2026-08-30",
            etf_category=EtfCategory.BOND.value,
            classification_source="official_metadata",
            underlying_scope="foreign",
            leverage_multiplier=2.0,
        )
        assert MarketProfileBridge.get_price_limit_pct(bond_2x) is None
        assert MarketProfileBridge.get_price_limit_class(bond_2x) == PriceLimitClass.NO_LIMIT
        assert MarketProfileBridge.get_tax_class(bond_2x) == TaxClass.DOMESTIC_ETF  # 0.1% tax!

    def test_single_character_false_positive_prevention(self):
        """Disallow single-character keywords like '美' from classifying an ETF as foreign."""
        from app.taiwan.universe.adapters import classify_etf_provenance

        # Name contains '美' (e.g. 美麗台灣) but no official metadata or multi-char foreign keyword
        cat, source, scope, mult = classify_etf_provenance(
            code="00991",
            name="富邦美麗台灣",
            cfi_code=None,
            official_product_types=None,
        )
        assert cat == "unknown"
        assert source == "unknown"
        assert scope == "unknown"

    def test_name_heuristic_rejected_by_market_profile_safety(self):
        """MarketProfileBridge must reject regulatory application if classification is merely heuristic."""
        heuristic_foreign_etf = TaiwanInstrument(
            symbol="00992.TWSE",
            code="00992",
            exchange="TWSE",
            name="富邦全球旗艦",
            instrument_type="etf",
            listing_status="active",
            listing_date=None,
            isin=None,
            industry=None,
            cfi_code=None,
            raw_category="ETF",
            is_supported=True,
            source="TWSE_ISIN",
            updated_at="2026-08-30",
            etf_category=EtfCategory.FOREIGN_EQUITY.value,
            classification_source="name_heuristic",  # Unconfirmed!
            underlying_scope="foreign",
            leverage_multiplier=1.0,
        )
        with pytest.raises(ValueError, match="Refusing to apply regulatory market rules to unconfirmed ETF"):
            MarketProfileBridge.get_price_limit_class(heuristic_foreign_etf)

    def test_unknown_underlying_scope_fails_loudly(self):
        """MarketProfileBridge must fail loudly if an ETF has unknown underlying scope."""
        unknown_scope_etf = TaiwanInstrument(
            symbol="00993.TWSE",
            code="00993",
            exchange="TWSE",
            name="未知標的ETF",
            instrument_type="etf",
            listing_status="active",
            listing_date=None,
            isin=None,
            industry=None,
            cfi_code="CEOGDU",
            raw_category="ETF",
            is_supported=True,
            source="TWSE_ISIN",
            updated_at="2026-08-30",
            etf_category=EtfCategory.LEVERAGED.value,
            classification_source="cfi_code",
            underlying_scope="unknown",  # UNKNOWN SCOPE!
            leverage_multiplier=2.0,
        )
        with pytest.raises(ValueError, match="with UNKNOWN underlying scope"):
            MarketProfileBridge.get_price_limit_pct(unknown_scope_etf)

    def test_unknown_etf_safety_fails_loudly(self):
        """Bridge must refuse to silently assume domestic 10% limit for an unclassified ETF."""
        unknown_etf = TaiwanInstrument(
            symbol="00999.TWSE",
            code="00999",
            exchange="TWSE",
            name="神秘測試ETF",
            instrument_type="etf",
            listing_status="active",
            listing_date=None,
            isin=None,
            industry=None,
            cfi_code=None,
            raw_category="ETF",
            is_supported=True,
            source="TWSE_ISIN",
            updated_at="2026-08-30",
            etf_category=EtfCategory.UNKNOWN.value,
            classification_source="unknown",
            underlying_scope="unknown",
            leverage_multiplier=1.0,
        )
        with pytest.raises(ValueError, match="Refusing to apply regulatory market rules to unconfirmed ETF"):
            MarketProfileBridge.get_price_limit_class(unknown_etf)




# ── 7. Polars Factor Compatibility Tests ────────────────────────


class TestFactorCompatibility:
    """Verify rolling institutional flows and margin momentum can be computed in Polars."""

    def test_chip_factor_pipeline(self):
        dates = [date(2026, 8, 20) + timedelta(days=i) for i in range(7)]
        rows = [
            {
                "symbol": "2330.TWSE",
                "trade_date": d,
                "foreign_net": 1000,
                "investment_trust_net": 500,
                "dealer_net": 200,
            }
            for d in dates
        ]
        df = pl.DataFrame(rows)
        factored = compute_chip_factors(df)

        assert "foreign_net_5d" in factored.columns
        assert "investment_trust_net_5d" in factored.columns
        assert "dealer_net_5d" in factored.columns

        # 5-day rolling sum for the 5th item (idx 4) should be 5 * 1000 = 5000
        assert factored["foreign_net_5d"][4] == 5000
        assert factored["investment_trust_net_5d"][4] == 2500
        assert factored["dealer_net_5d"][4] == 1000

    def test_margin_factor_pipeline(self):
        dates = [date(2026, 8, 24), date(2026, 8, 25)]
        rows = [
            {
                "symbol": "2330.TWSE",
                "trade_date": dates[0],
                "margin_previous_balance": 10000,
                "margin_balance": 10500,
                "short_balance": 1050,
            },
            {
                "symbol": "2330.TWSE",
                "trade_date": dates[1],
                "margin_previous_balance": 10500,
                "margin_balance": 11000,
                "short_balance": 1200,
            },
        ]
        df = pl.DataFrame(rows)
        factored = compute_margin_factors(df)

        assert "margin_balance_change" in factored.columns
        assert "short_margin_ratio" in factored.columns

        assert factored["margin_balance_change"][0] == 500
        assert factored["short_margin_ratio"][0] == 10.0  # 1050 / 10500 * 100
        assert factored["margin_balance_change"][1] == 500
        assert factored["short_margin_ratio"][1] == round(1200 / 11000 * 100, 2)

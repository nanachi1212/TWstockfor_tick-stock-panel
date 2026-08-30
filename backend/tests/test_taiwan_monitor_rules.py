"""Offline Unit Tests for Taiwan Monitor Rules (Phase 5B).

Tests:
  - TaiwanMonitorRule construction, serialization, deserialization
  - Validation rules (symbol in security master, supported instruments, threshold bounds)
  - Unsupported assets (e.g. warrants/ETNs) rejected
  - JSON persistence, reload, restart safety
  - CRUD operations (add, get, list, enable/disable, delete)
"""
from __future__ import annotations

from pathlib import Path
import pytest

from app.taiwan.realtime.monitor_engine import TaiwanMonitorEngine
from app.taiwan.realtime.monitor_models import (
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
    etf_00646 = TaiwanInstrument(
        symbol="00646.TWSE", code="00646", exchange="TWSE", name="元大S&P500",
        instrument_type="etf", listing_status="active", is_supported=True,
        listing_date="2015/12/14", isin="TW0000064608", industry=None,
        cfi_code="CEOJEU", raw_category="ETF", source="TWSE_ISIN", updated_at="2026-08-30",
        etf_category="foreign_equity", classification_source="official_metadata",
        underlying_scope="foreign", leverage_multiplier=1.0,
    )
    warrant = TaiwanInstrument(
        symbol="03001.TWSE", code="03001", exchange="TWSE", name="台積電認購01",
        instrument_type="warrant", listing_status="active", is_supported=False,
        listing_date="2020/01/01", isin="TW0000300100", industry=None,
        cfi_code="RWST", raw_category="認購權證", source="TWSE_ISIN", updated_at="2026-08-30",
    )
    master._instruments = {
        tsmc.symbol: tsmc,
        eink.symbol: eink,
        etf_0050.symbol: etf_0050,
        etf_00631l.symbol: etf_00631l,
        etf_00646.symbol: etf_00646,
        warrant.symbol: warrant,
    }
    master._loaded = True
    return master


@pytest.fixture
def engine(tmp_path, mock_sec_master, monkeypatch):
    """Instantiate test engine backed by temporary json file."""
    monkeypatch.setattr("app.taiwan.realtime.monitor_engine.get_security_master", lambda: mock_sec_master)
    storage = tmp_path / "test_rules.json"
    return TaiwanMonitorEngine(storage_path=storage)


class TestMonitorRuleValidation:
    """Validate rule creation bounds and Security Master integrity."""

    def test_valid_price_above_rule(self, engine):
        rule = TaiwanMonitorRule(
            rule_id="r1",
            name="台積電突破2500",
            symbol="2330.TWSE",
            rule_type=TaiwanRuleType.PRICE_ABOVE,
            threshold=2500.0,
        )
        saved = engine.add_rule(rule)
        assert saved.rule_id == "r1"
        assert saved.threshold == 2500.0
        assert engine.get_rule("r1") is not None

    def test_invalid_price_threshold_fails_loudly(self, engine):
        rule = TaiwanMonitorRule(
            rule_id="r_bad_price",
            name="無效價格",
            symbol="2330.TWSE",
            rule_type=TaiwanRuleType.PRICE_ABOVE,
            threshold=-10.0,  # <= 0 must fail
        )
        with pytest.raises(ValueError, match="threshold must be strictly positive"):
            engine.add_rule(rule)

    def test_unsupported_warrant_rejected(self, engine):
        rule = TaiwanMonitorRule(
            rule_id="r_warrant",
            name="權證監控",
            symbol="03001.TWSE",
            rule_type=TaiwanRuleType.PRICE_ABOVE,
            threshold=10.0,
        )
        with pytest.raises(ValueError, match="not a supported trading asset"):
            engine.add_rule(rule)

    def test_nonexistent_symbol_rejected(self, engine):
        rule = TaiwanMonitorRule(
            rule_id="r_ghost",
            name="不存在標的",
            symbol="99999.TWSE",
            rule_type=TaiwanRuleType.PRICE_ABOVE,
            threshold=100.0,
        )
        with pytest.raises(ValueError, match="does not exist in Security Master"):
            engine.add_rule(rule)

    def test_no_limit_etf_near_upper_limit_rejected_at_validation(self, engine):
        """00646 has NO_LIMIT trading rules; near_upper_limit rule must be rejected."""
        rule = TaiwanMonitorRule(
            rule_id="r_00646",
            name="00646接近漲停",
            symbol="00646.TWSE",
            rule_type=TaiwanRuleType.NEAR_UPPER_LIMIT,
            threshold=1.0,
        )
        with pytest.raises(ValueError, match="NO_LIMIT trading rules; near_upper_limit is not applicable"):
            engine.add_rule(rule)

    def test_domestic_etf_near_upper_limit_allowed(self, engine):
        """0050 and 00631L have statutory limits; near_upper_limit rule is accepted."""
        rule1 = TaiwanMonitorRule(
            rule_id="r_0050",
            name="0050接近漲停",
            symbol="0050.TWSE",
            rule_type=TaiwanRuleType.NEAR_UPPER_LIMIT,
            threshold=1.0,
        )
        rule2 = TaiwanMonitorRule(
            rule_id="r_00631l",
            name="正2接近漲停",
            symbol="00631L.TWSE",
            rule_type=TaiwanRuleType.NEAR_UPPER_LIMIT,
            threshold=1.5,
        )
        assert engine.add_rule(rule1).rule_id == "r_0050"
        assert engine.add_rule(rule2).rule_id == "r_00631l"


class TestMonitorRuleCRUDAndPersistence:
    """Test CRUD, restart safety, enable/disable toggling."""

    def test_persistence_across_engine_restart(self, tmp_path, mock_sec_master, monkeypatch):
        monkeypatch.setattr("app.taiwan.realtime.monitor_engine.get_security_master", lambda: mock_sec_master)
        storage = tmp_path / "persistence_test.json"
        engine1 = TaiwanMonitorEngine(storage_path=storage)

        rule1 = TaiwanMonitorRule(
            rule_id="rule_tsmc",
            name="台積電監控",
            symbol="2330.TWSE",
            rule_type=TaiwanRuleType.PRICE_ABOVE,
            threshold=2500.0,
            cooldown_seconds=600,
            hysteresis=5.0,
            severity=TaiwanAlertSeverity.CRITICAL,
        )
        rule2 = TaiwanMonitorRule(
            rule_id="rule_eink",
            name="元太量能監控",
            symbol="8069.TPEX",
            rule_type=TaiwanRuleType.VOLUME_ABOVE,
            threshold=10000000.0,  # 1,000萬股
        )
        engine1.add_rule(rule1)
        engine1.add_rule(rule2)

        # Restart engine from disk
        engine2 = TaiwanMonitorEngine(storage_path=storage)
        rules = engine2.list_rules()
        assert len(rules) == 2

        r_tsmc = engine2.get_rule("rule_tsmc")
        assert r_tsmc is not None
        assert r_tsmc.threshold == 2500.0
        assert r_tsmc.cooldown_seconds == 600
        assert r_tsmc.hysteresis == 5.0
        assert r_tsmc.severity == TaiwanAlertSeverity.CRITICAL

    def test_enable_disable_and_delete(self, engine):
        rule = TaiwanMonitorRule(
            rule_id="r_toggle",
            name="測試切換",
            symbol="2330.TWSE",
            rule_type=TaiwanRuleType.PRICE_BELOW,
            threshold=2000.0,
            enabled=True,
        )
        engine.add_rule(rule)
        assert engine.get_rule("r_toggle").enabled is True

        # Disable
        engine.set_rule_enabled("r_toggle", False)
        assert engine.get_rule("r_toggle").enabled is False

        # Delete
        assert engine.delete_rule("r_toggle") is True
        assert engine.get_rule("r_toggle") is None
        assert engine.delete_rule("r_toggle") is False

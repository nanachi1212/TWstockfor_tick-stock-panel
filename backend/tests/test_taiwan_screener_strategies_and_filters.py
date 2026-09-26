from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import polars as pl
from fastapi.testclient import TestClient

from app.main import app
from app.taiwan.finmind_cache import FinMindCache
from app.taiwan.screener import (
    TaiwanScreenerRequest,
    TaiwanScreenerService,
)
from app.taiwan.screener_strategy_store import (
    TaiwanScreenerStrategyStore,
)


def test_strategy_store_presets_and_crud(tmp_path: Path):
    store_file = tmp_path / "strategies.json"
    store = TaiwanScreenerStrategyStore(path=store_file)

    # 1. Preset list
    strats = store.list_strategies()
    assert len(strats) >= 2
    preset_ids = {s.id for s in strats if s.is_preset}
    assert "preset_revenue_growth_quant" in preset_ids
    assert "preset_chip_improvement" in preset_ids

    # 2. Create custom strategy
    new_strat = store.save_strategy(
        name="價值高股息",
        conditions={"pe_max": 15.0, "dividend_yield_min": 5.0},
        description="本益比小於15且殖利率高於5%",
    )
    assert new_strat.id.startswith("strat_")
    assert new_strat.name == "價值高股息"
    assert new_strat.is_preset is False
    assert new_strat.conditions["pe_max"] == 15.0

    # Read back
    all_strats = store.list_strategies()
    assert any(s.id == new_strat.id for s in all_strats)

    # 3. Update custom strategy
    updated = store.save_strategy(
        name="價值高股息改",
        conditions={"pe_max": 12.0, "dividend_yield_min": 6.0},
        description="本益比小於12且殖利率高於6%",
        strategy_id=new_strat.id,
    )
    assert updated.id == new_strat.id
    assert updated.name == "價值高股息改"
    assert updated.conditions["pe_max"] == 12.0

    # 4. Delete custom strategy
    deleted = store.delete_strategy(new_strat.id)
    assert deleted is True
    assert store.get_strategy(new_strat.id) is None

    # 5. Cannot delete preset
    deleted_preset = store.delete_strategy("preset_revenue_growth_quant")
    assert deleted_preset is False


def test_screener_cached_fundamentals_and_chips(tmp_path: Path):
    cache = FinMindCache(cache_dir=tmp_path / "finmind_cache")

    # Set up cache for 2330
    cache.set(
        "TaiwanStockMonthRevenue",
        "2330",
        [
            {"date": "2026-07-01", "revenue": 200_000_000_000},
            {"date": "2026-08-01", "revenue": 220_000_000_000},
        ],
        data_date="2026-08-01",
    )
    cache.set(
        "TaiwanStockFinancialStatements",
        "2330",
        [
            {"date": "2026-06-30", "type": "EPS", "value": 12.5},
            {"date": "2026-06-30", "type": "NetIncome", "value": 300_000_000_000},
        ],
        data_date="2026-06-30",
    )
    cache.set(
        "TaiwanValuation",
        "2330",
        {"pe": 18.5, "pb": 4.5, "dividend_yield": 2.8},
        data_date="2026-08-20",
    )
    cache.set(
        "TaiwanStockShareholding",
        "2330",
        [
            {"date": "2026-08-01", "ForeignInvestmentSharesRatio": 72.0},
            {"date": "2026-08-20", "ForeignInvestmentSharesRatio": 74.5},
        ],
        data_date="2026-08-20",
    )
    cache.set(
        "TaiwanStockSecuritiesLending",
        "2330",
        [
            {"date": "2026-08-20", "volume": 1000, "fee_rate": 2.0},
        ],
        data_date="2026-08-20",
    )

    # Mock Universe & Daily store
    mock_universe_df = pl.DataFrame({
        "symbol": ["2330", "2317"],
        "name": ["台積電", "鴻海"],
        "exchange": ["TWSE", "TWSE"],
        "instrument_type": ["stock", "stock"],
        "listing_status": ["active", "active"],
        "industry": ["半導體", "電子組裝"],
    })
    mock_security_master = MagicMock()

    mock_security_master.to_dataframe.return_value = mock_universe_df
    mock_security_master.get_instrument.return_value = None

    mock_daily_df = pl.DataFrame({
        "symbol": ["2330", "2317"],
        "date": ["2026-08-20", "2026-08-20"],
        "open": [950.0, 180.0],
        "high": [960.0, 182.0],
        "low": [945.0, 178.0],
        "close": [955.0, 181.0],
        "volume": [30000000.0, 20000000.0],
        "amount": [28000000000.0, 3600000000.0],
    })
    mock_daily_store = MagicMock()
    mock_daily_store.read_latest_per_symbol.return_value = mock_daily_df
    mock_daily_store.available_dates.return_value = ["2026-08-20"]
    mock_daily_store.read_range.return_value = pl.DataFrame()

    mock_inst_store = MagicMock()
    mock_inst_store.read_latest_per_symbol.return_value = pl.DataFrame({
        "symbol": ["2330"],
        "date": ["2026-08-20"],
        "foreign_net": [5000000.0],
        "investment_trust_net": [1000000.0],
        "dealer_net": [200000.0],
        "status": ["available"],
    })

    mock_margin_store = MagicMock()
    mock_margin_store.read_latest_per_symbol.return_value = pl.DataFrame()

    svc = TaiwanScreenerService(
        security_master=mock_security_master,
        daily_store=mock_daily_store,
        institutional_store=mock_inst_store,
        margin_store=mock_margin_store,
        finmind_cache=cache,
    )

    # Test 1: Screen with revenue_yoy_min (2330 should pass if yoy calculated, 2317 missing so fail-closed)
    req1 = TaiwanScreenerRequest(revenue_mom_min=5.0)
    resp1 = svc.run(req1)
    # 2330 mom is (220 - 200) / 200 = 10% >= 5%
    # 2317 has no cache -> filtered out
    assert resp1.total == 1
    assert resp1.items[0].symbol == "2330"
    assert resp1.items[0].revenue_mom is not None
    assert any("營收月增" in r for r in resp1.items[0].match_reasons)
    assert resp1.coverage_info is not None
    assert resp1.coverage_info.total_universe == 2
    assert resp1.coverage_info.fundamental_cached_count == 1

    # Test 2: Foreign shareholding ratio min
    req2 = TaiwanScreenerRequest(foreign_shareholding_ratio_min=70.0)
    resp2 = svc.run(req2)
    assert resp2.total == 1
    assert resp2.items[0].symbol == "2330"
    assert any("外資持股" in r for r in resp2.items[0].match_reasons)

    # Test 3: Valuation filter (pe_max: 20.0)
    req3 = TaiwanScreenerRequest(pe_max=20.0)
    resp3 = svc.run(req3)
    assert resp3.total == 1
    assert resp3.items[0].symbol == "2330"
    assert resp3.items[0].pe == 18.5
    assert any("本益比" in r for r in resp3.items[0].match_reasons)

    # Test 4: Missing data never coerces to 0 or passes filter
    req4 = TaiwanScreenerRequest(pe_max=10.0)  # 2330 is 18.5, 2317 is null
    resp4 = svc.run(req4)
    assert resp4.total == 0


def test_screener_strategy_api_endpoints():
    client = TestClient(app, client=("127.0.0.1", 50000))

    # 1. GET /api/taiwan/screener/strategies
    res = client.get("/api/taiwan/screener/strategies")

    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert any(s["id"] == "preset_revenue_growth_quant" for s in data)

    # 2. POST /api/taiwan/screener/strategies
    res = client.post(
        "/api/taiwan/screener/strategies",
        json={
            "name": "測試自訂選股",
            "conditions": {"pe_max": 20.0, "volume_min": 1000000.0},
            "description": "測試用策略",
        },
    )
    assert res.status_code == 200
    strat = res.json()
    strat_id = strat["id"]
    assert strat["name"] == "測試自訂選股"
    assert strat["is_preset"] is False

    # 3. PUT /api/taiwan/screener/strategies/{strategy_id}
    res = client.put(
        f"/api/taiwan/screener/strategies/{strat_id}",
        json={
            "name": "測試自訂選股(更新)",
            "conditions": {"pe_max": 18.0},
            "description": "更新後描述",
        },
    )
    assert res.status_code == 200
    updated = res.json()
    assert updated["name"] == "測試自訂選股(更新)"
    assert updated["conditions"]["pe_max"] == 18.0

    # 4. DELETE preset should be rejected
    res = client.delete("/api/taiwan/screener/strategies/preset_revenue_growth_quant")
    assert res.status_code == 400

    # 5. DELETE custom strategy
    res = client.delete(f"/api/taiwan/screener/strategies/{strat_id}")
    assert res.status_code == 200
    assert res.json()["ok"] is True

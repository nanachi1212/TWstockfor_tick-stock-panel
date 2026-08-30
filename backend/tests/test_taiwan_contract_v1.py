import json
from datetime import date
from pathlib import Path

from app.taiwan.market_rules import (
    PriceLimitModel,
    SecuritiesTaxModel,
    TaxClass,
    TickSizeClass,
    TickSizeModel,
)

FIXTURE = json.loads((Path(__file__).parents[2] / "docs" / "taiwan_market_contract_v1.json").read_text(encoding="utf-8"))


def test_taiwan_market_contract_v1_ticks_and_limits():
    classes = {"ordinary_stock": TickSizeClass.ORDINARY_STOCK, "etf": TickSizeClass.ETF}
    for name, cases in FIXTURE["tick_sizes"].items():
        for price, expected in cases:
            assert TickSizeModel.get_tick_size(price, classes[name]) == expected
    for case in FIXTURE["price_limits"]:
        assert PriceLimitModel.calc_limits_for_pct(
            case["reference_price"], case["limit_pct"], classes[case["tick_class"]]
        ) == (case["upper"], case["lower"])


def test_taiwan_market_contract_v1_tax_windows():
    model = SecuritiesTaxModel()
    classes = {
        "ordinary_stock": (TaxClass.ORDINARY_STOCK, False),
        "day_trade_stock": (TaxClass.ORDINARY_STOCK, True),
        "domestic_etf": (TaxClass.DOMESTIC_ETF, False),
        "foreign_component_etf": (TaxClass.FOREIGN_ETF, False),
        "passive_bond_etf": (TaxClass.BOND_ETF, False),
    }
    for case in FIXTURE["taxes"]:
        tax_class, day_trade = classes[case["class"]]
        assert model.get_tax_rate(tax_class, day_trade, date.fromisoformat(case["trade_date"])) == case["rate"]

"""共用 pytest fixture。

`taiwan_data_env` 提供一個完全 hermetic 的台股資料環境:

  - settings.data_dir 指到 tmp_path, 所有 Taiwan store 的預設路徑 (taiwan_data_root())
    隨之落在測試專屬目錄, 不碰開發機既有 data/taiwan/。
  - 預先寫入 security_master.parquet, 讓 TaiwanSecurityMaster.ensure_loaded()
    走 load_cache() 而不是 load_from_adapters() —— 後者會真的連 TWSE/TPEx 抓 ISIN,
    這正是 test_api_endpoint_zero_market_http_and_no_ai 要防的 0-HTTP 契約。
  - 寫入固定的日線 / 三大法人 / 融資券 partition, 交易日窗口以 2026-08-28 為最後一天。

資料是刻意造的最小集合 (足以算出 MA20 / RSI14 / 5D、20D 報酬), 不依賴任何外部來源。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import polars as pl
import pytest

# 台股資料 fixture 的錨定交易日 (與既有台股測試共用的 2026-08-28)。
TAIWAN_FIXTURE_TARGET = date(2026, 8, 28)
# 25 個交易日足夠算 MA20 / RSI14 / 20D 報酬 (>= 21 根)。
TAIWAN_FIXTURE_TRADING_DAYS = 25

_FIXTURE_SECURITIES = [
    {
        "symbol": "2330.TWSE", "code": "2330", "exchange": "TWSE", "name": "台積電",
        "instrument_type": "stock", "industry": "半導體業", "isin": "TW0002330008",
        "cfi_code": "ESVUFR", "raw_category": "股票", "base_price": 1000.0,
    },
    {
        "symbol": "2881.TWSE", "code": "2881", "exchange": "TWSE", "name": "富邦金",
        "instrument_type": "stock", "industry": "金融保險業", "isin": "TW0002881000",
        "cfi_code": "ESVUFR", "raw_category": "股票", "base_price": 90.0,
    },
    {
        "symbol": "2454.TWSE", "code": "2454", "exchange": "TWSE", "name": "聯發科",
        "instrument_type": "stock", "industry": "半導體業", "isin": "TW0002454006",
        "cfi_code": "ESVUFR", "raw_category": "股票", "base_price": 1400.0,
    },
    {
        "symbol": "2603.TWSE", "code": "2603", "exchange": "TWSE", "name": "長榮",
        "instrument_type": "stock", "industry": "航運業", "isin": "TW0002603008",
        "cfi_code": "ESVUFR", "raw_category": "股票", "base_price": 200.0,
    },
    {
        "symbol": "8069.TPEX", "code": "8069", "exchange": "TPEX", "name": "元太",
        "instrument_type": "stock", "industry": "光電業", "isin": "TW0008069007",
        "cfi_code": "ESVUFR", "raw_category": "股票", "base_price": 250.0,
    },
    {
        "symbol": "0050.TWSE", "code": "0050", "exchange": "TWSE", "name": "元大台灣50",
        "instrument_type": "etf", "industry": None, "isin": "TW0000050004",
        "cfi_code": "CEOJEU", "raw_category": "ETF", "base_price": 200.0,
        "etf_category": "domestic_equity", "underlying_scope": "domestic",
        "leverage_multiplier": 1.0,
    },
    {
        "symbol": "00646.TWSE", "code": "00646", "exchange": "TWSE", "name": "元大S&P500",
        "instrument_type": "etf", "industry": None, "isin": "TW0000646009",
        "cfi_code": "CEOIEU", "raw_category": "ETF", "base_price": 55.0,
        "etf_category": "foreign_equity", "underlying_scope": "foreign",
        "leverage_multiplier": 1.0,
    },
    {
        # 官方審定產品規則: 元大台灣50正2 為 2.0 倍槓桿 (見 OFFICIAL_ETF_RULE_PROFILES)。
        "symbol": "00631L.TWSE", "code": "00631L", "exchange": "TWSE", "name": "元大台灣50正2",
        "instrument_type": "etf", "industry": None, "isin": "TW0000631001",
        "cfi_code": "CEOJEU", "raw_category": "ETF", "base_price": 300.0,
        "etf_category": "domestic_equity", "underlying_scope": "domestic",
        "leverage_multiplier": 2.0,
    },
]


def _trading_days(end: date, count: int) -> list[date]:
    """回推 *count* 個工作日 (週末排除), 以 *end* 為最後一天。"""
    days: list[date] = []
    cur = end
    while len(days) < count:
        if cur.weekday() < 5:
            days.append(cur)
        cur -= timedelta(days=1)
    return sorted(days)


def _daily_rows(trading_days: list[date]) -> list[dict]:
    """造出溫和上行、每日皆有量的日線, 讓技術指標都能算出非 None。"""
    rows: list[dict] = []
    for spec in _FIXTURE_SECURITIES:
        base = spec["base_price"]
        for i, d in enumerate(trading_days):
            # 交錯的漲跌讓 RSI 兩邊都有樣本, 不會退化成 100。
            close = round(base * (1.0 + 0.002 * i + (0.001 if i % 2 == 0 else -0.0005)), 2)
            rows.append({
                "symbol": spec["symbol"],
                "date": d,
                "open": round(close * 0.995, 2),
                "high": round(close * 1.01, 2),
                "low": round(close * 0.99, 2),
                "close": close,
                "volume": 10_000_000.0 + i * 100_000.0,
                "amount": close * (10_000_000.0 + i * 100_000.0),
                "quote_ts": None,
            })
    return rows


def _institutional_rows(trading_days: list[date]) -> list[dict]:
    rows: list[dict] = []
    for spec in _FIXTURE_SECURITIES:
        for i, d in enumerate(trading_days):
            foreign_net = 1_000_000 - i * 10_000
            trust_net = 200_000 + i * 5_000
            dealer_net = 50_000
            rows.append({
                "symbol": spec["symbol"], "date": d, "trade_date": d,
                "foreign_buy": 5_000_000, "foreign_sell": 5_000_000 - foreign_net,
                "foreign_net": foreign_net,
                "investment_trust_buy": 1_000_000,
                "investment_trust_sell": 1_000_000 - trust_net,
                "investment_trust_net": trust_net,
                "dealer_buy": 300_000, "dealer_sell": 250_000, "dealer_net": dealer_net,
                "dealer_proprietary_buy": 150_000, "dealer_proprietary_sell": 130_000,
                "dealer_proprietary_net": 20_000,
                "dealer_hedge_buy": 150_000, "dealer_hedge_sell": 120_000,
                "dealer_hedge_net": 30_000,
                "official_net": foreign_net + trust_net + dealer_net,
                "computed_net": foreign_net + trust_net + dealer_net,
                "has_discrepancy": False,
                "status": "ok", "source": "twse:fund/T86",
            })
    return rows


def _margin_rows(trading_days: list[date]) -> list[dict]:
    rows: list[dict] = []
    for spec in _FIXTURE_SECURITIES:
        for i, d in enumerate(trading_days):
            margin_balance = 100_000 + i * 500
            short_balance = 10_000 + i * 50
            rows.append({
                "symbol": spec["symbol"], "date": d, "trade_date": d,
                "margin_previous_balance": margin_balance - 500,
                "margin_buy": 3_000, "margin_sell": 2_500, "margin_cash_redemption": 0,
                "margin_balance": margin_balance, "margin_change": 500,
                "short_previous_balance": short_balance - 50,
                "short_sell": 300, "short_cover": 250, "short_stock_redemption": 0,
                "short_balance": short_balance, "short_change": 50,
                "short_margin_ratio": round(short_balance / margin_balance * 100, 2),
                "unit": "shares", "status": "ok", "source": "twse:exchangeReport/MI_MARGN",
            })
    return rows


def _security_master_frame() -> pl.DataFrame:
    now_iso = datetime(2026, 8, 28, 18, 0, 0).isoformat()
    rows = []
    for spec in _FIXTURE_SECURITIES:
        rows.append({
            "symbol": spec["symbol"], "code": spec["code"], "exchange": spec["exchange"],
            "name": spec["name"], "instrument_type": spec["instrument_type"],
            "listing_status": "active", "listing_date": "2000/01/01",
            "isin": spec["isin"], "industry": spec["industry"], "cfi_code": spec["cfi_code"],
            "raw_category": spec["raw_category"], "is_supported": True,
            "source": "TWSE_ISIN" if spec["exchange"] == "TWSE" else "TPEX_ISIN",
            "updated_at": now_iso,
            "etf_category": spec.get("etf_category"),
            "classification_source": "official_rule" if spec.get("etf_category") else None,
            "underlying_scope": spec.get("underlying_scope"),
            "leverage_multiplier": float(spec.get("leverage_multiplier", 1.0)),
            "currency": "TWD", "lot_size": 1000,
        })
    return pl.DataFrame(rows)


@pytest.fixture
def taiwan_data_env(tmp_path, monkeypatch):
    """Hermetic 台股資料環境: tmp data_dir + 預先落盤的 master / 日線 / 法人 / 融資券。

    回傳 dict, 內含 target (最後交易日)、trading_days、data_dir。
    """
    from app.config import settings
    from app.taiwan import universe as taiwan_universe
    from app.taiwan.daily_store import TaiwanDailyStore
    from app.taiwan.institutional_store import TaiwanInstitutionalStore
    from app.taiwan.margin_store import TaiwanMarginStore
    from app.taiwan.universe.service import TaiwanSecurityMaster

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    taiwan_root = tmp_path / "taiwan"
    taiwan_root.mkdir(parents=True, exist_ok=True)

    trading_days = _trading_days(TAIWAN_FIXTURE_TARGET, TAIWAN_FIXTURE_TRADING_DAYS)

    TaiwanDailyStore().write_batch(pl.DataFrame(_daily_rows(trading_days)))
    TaiwanInstitutionalStore().write_batch(pl.DataFrame(_institutional_rows(trading_days)))
    TaiwanMarginStore().write_batch(pl.DataFrame(_margin_rows(trading_days)))

    # security master: 先落 parquet, 再把 module-level singleton 換成指向該檔的實例,
    # ensure_loaded() 於是走 load_cache(), 永遠不會呼叫 adapters (= 不連外網)。
    master_path = taiwan_root / "security_master.parquet"
    _security_master_frame().write_parquet(master_path)
    master = TaiwanSecurityMaster(cache_path=master_path)
    assert master.load_cache() is True
    monkeypatch.setattr(taiwan_universe, "_default_master", master)

    return {
        "data_dir": tmp_path,
        "taiwan_root": taiwan_root,
        "target": TAIWAN_FIXTURE_TARGET,
        "trading_days": trading_days,
        "security_master": master,
    }

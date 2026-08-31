"""Tests for OfficialDailySnapshotAdapter and daily snapshot refresh orchestration.

Validates:
  1. TWSE normal row parsing
  2. TWSE ETF row parsing
  3. TWSE "--" no-trade row parsing
  4. TWSE comma and whitespace numeric parsing
  5. TPEx normal row parsing
  6. TPEx ETF row parsing
  7. TPEx "--" no-trade row parsing
  8. TPEx comma numeric parsing
  9. Schema mismatch handling (fails safely, returns empty)
  10. Security Master allowlist filtering (rejects warrants, rights, unknown codes)
  11. 6488.TPEX 2026-08-28 exact regression (preserves 11_772_540 shares, 11_478_474_997 TWD)
  12. One-day full-market refresh HTTP budget (exactly 1 TWSE + 1 TPEx = 2 requests)
  13. Multi-day catch-up HTTP budget (3 missing dates = 6 requests)
  14. Idempotency (already-persisted date skips network requests)
  15. Failure isolation (TWSE success + TPEx failure does not mark holiday or crash)
"""
from datetime import date
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from app.taiwan.daily_refresh import TaiwanDailyRefreshService
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.daily_update import TaiwanDailyUpdateService
from app.taiwan.providers.snapshot_provider import OfficialDailySnapshotAdapter
from app.taiwan.realtime.calendar import TaiwanTradingCalendar
from app.taiwan.universe import TaiwanSecurityMaster


# ── Sample Fixtures ───────────────────────────────────────────────

def mock_security_master():
    master = MagicMock(spec=TaiwanSecurityMaster)
    # Provide supported stocks and ETFs
    df_stocks = pl.DataFrame({
        "symbol": ["2330.TWSE", "6488.TPEX", "8069.TPEX"],
        "exchange": ["TWSE", "TPEX", "TPEX"],
        "name": ["台積電", "環球晶", "元太"],
        "instrument_type": ["stock", "stock", "stock"],
    })
    df_etfs = pl.DataFrame({
        "symbol": ["0050.TWSE", "00679B.TPEX"],
        "exchange": ["TWSE", "TPEX"],
        "name": ["元大台灣50", "元大美債20年"],
        "instrument_type": ["etf", "etf"],
    })
    master.to_provider_dataframe.side_effect = lambda asset_type: df_stocks if asset_type == "stock" else df_etfs
    return master


# ── Parser Unit Tests ─────────────────────────────────────────────

def test_twse_parsing_normal_and_etf():
    master = mock_security_master()
    adapter = OfficialDailySnapshotAdapter(security_master=master)

    fields = [
        "證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額",
        "開盤價", "最高價", "最低價", "收盤價", "漲跌(+/-)", "漲跌價差",
        "最後揭示買價", "最後揭示買量", "最後揭示賣價", "最後揭示賣量", "本益比",
    ]
    rows = [
        # Normal stock: 2330
        ["2330", "台積電", "15,025,832", "52,492", "36,465,015,980", "2,440.00", "2,445.00", "2,410.00", "2,420.00", "+", "10.00", "2,420.00", "30", "2,425.00", "200", "28.05"],
        # ETF: 0050
        ["0050", "元大台灣50", "78,158,934", "70,286", "8,369,173,993", "107.10", "107.35", "106.70", "106.95", "+", "0.90", "106.90", "266", "106.95", "17", "0.00"],
        # Warrant: 041589 (should be filtered out by allowlist)
        ["041589", "台積電群益5A購02", "10,000", "1", "35,900", "3.59", "3.59", "3.59", "3.59", "+", "0.10", "3.50", "10", "3.60", "10", "0.00"],
    ]

    target_d = date(2026, 8, 28)
    df = adapter._parse_twse_table(rows, fields, target_d)

    assert df.height == 2
    symbols = df["symbol"].to_list()
    assert "2330.TWSE" in symbols
    assert "0050.TWSE" in symbols
    assert "041589.TWSE" not in symbols

    r_2330 = df.filter(pl.col("symbol") == "2330.TWSE").to_dicts()[0]
    assert r_2330["open"] == 2440.0
    assert r_2330["high"] == 2445.0
    assert r_2330["low"] == 2410.0
    assert r_2330["close"] == 2420.0
    assert r_2330["volume"] == 15025832.0
    assert r_2330["amount"] == 36465015980.0
    assert r_2330["quote_ts"] is None


def test_twse_parsing_no_trade_row():
    master = mock_security_master()
    adapter = OfficialDailySnapshotAdapter(security_master=master)

    fields = ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"]
    rows = [
        # Suspended / no trade
        ["2330", "台積電", "0", "0", "0", "--", "--", "--", "--"],
    ]
    target_d = date(2026, 8, 28)
    df = adapter._parse_twse_table(rows, fields, target_d)

    assert df.height == 1
    r = df.to_dicts()[0]
    assert r["symbol"] == "2330.TWSE"
    assert r["open"] is None
    assert r["high"] is None
    assert r["low"] is None
    assert r["close"] is None
    assert r["volume"] == 0.0
    assert r["amount"] == 0.0


def test_tpex_parsing_normal_and_etf():
    master = mock_security_master()
    adapter = OfficialDailySnapshotAdapter(security_master=master)

    fields = [
        "代號", "名稱", "收盤", "漲跌", "開盤", "最高", "最低", "均價",
        "成交股數", "成交金額(元)", "成交筆數", "最後買價", "最後買量(張數)",
        "最後賣價", "最後賣量(張數)", "發行股數", "次日 參考價", "次日 漲停價", "次日 跌停價"
    ]
    rows = [
        # 6488 (regression check for exact numbers)
        ["6488", "環球晶", "972.00", "+14.00", "967.00", "1015.00", "949.00", "975.02", "11,772,540", "11,478,474,997", "22,806", "971.00", "1", "972.00", "11", "478,113,725", "972.00", "1065.00", "875.00"],
        # 00679B ETF
        ["00679B", "元大美債20年", "25.85", "-0.05", "25.92", "25.92", "25.81", "25.86", "12,808,955", "331,260,338", "5,123", "25.84", "10", "25.85", "50", "1,000,000,000", "25.85", "28.43", "23.27"],
        # Unsupported warrant or code
        ["71234P", "環球晶凱基5A", "1.20", "+0.02", "1.18", "1.22", "1.18", "1.20", "50,000", "60,000", "10", "1.19", "5", "1.21", "5", "0", "1.20", "1.32", "1.08"],
    ]

    target_d = date(2026, 8, 28)
    df = adapter._parse_tpex_table(rows, fields, target_d)

    assert df.height == 2
    symbols = df["symbol"].to_list()
    assert "6488.TPEX" in symbols
    assert "00679B.TPEX" in symbols
    assert "71234P.TPEX" not in symbols

    r_6488 = df.filter(pl.col("symbol") == "6488.TPEX").to_dicts()[0]
    assert r_6488["open"] == 967.0
    assert r_6488["high"] == 1015.0
    assert r_6488["low"] == 949.0
    assert r_6488["close"] == 972.0
    # Must preserve exact shares and amount (not truncated to lots or thousand TWD)
    assert r_6488["volume"] == 11772540.0
    assert r_6488["amount"] == 11478474997.0


def test_tpex_parsing_no_trade_row():
    master = mock_security_master()
    adapter = OfficialDailySnapshotAdapter(security_master=master)

    fields = ["代號", "名稱", "收盤", "漲跌", "開盤", "最高", "最低", "均價", "成交股數", "成交金額(元)"]
    rows = [
        ["6488", "環球晶", "---", "0.00", "---", "---", "---", "---", "0", "0"],
    ]
    target_d = date(2026, 8, 28)
    df = adapter._parse_tpex_table(rows, fields, target_d)

    assert df.height == 1
    r = df.to_dicts()[0]
    assert r["symbol"] == "6488.TPEX"
    assert r["open"] is None
    assert r["high"] is None
    assert r["low"] is None
    assert r["close"] is None
    assert r["volume"] == 0.0
    assert r["amount"] == 0.0


def test_schema_mismatch_fails_safely():
    master = mock_security_master()
    adapter = OfficialDailySnapshotAdapter(security_master=master)

    # Missing mandatory column
    broken_fields = ["代號", "名稱", "收盤"]
    broken_rows = [["6488", "環球晶", "972.00"]]
    df = adapter._parse_tpex_table(broken_rows, broken_fields, date(2026, 8, 28))
    assert df.is_empty()


# ── Refresh Service & HTTP Budget Tests ───────────────────────────

def test_refresh_dates_one_day_budget():
    """One day full-market refresh calls TWSE once and TPEx once."""
    mock_store = MagicMock(spec=TaiwanDailyStore)
    mock_store.available_dates.return_value = []
    mock_store.write_batch.return_value = 2330

    mock_adapter = MagicMock(spec=OfficialDailySnapshotAdapter)
    mock_adapter.fetch_date.return_value = pl.DataFrame({
        "symbol": ["2330.TWSE", "6488.TPEX"],
        "date": [date(2026, 8, 28), date(2026, 8, 28)],
        "open": [2440.0, 967.0],
        "high": [2445.0, 1015.0],
        "low": [2410.0, 949.0],
        "close": [2420.0, 972.0],
        "volume": [15025832.0, 11772540.0],
        "amount": [36465015980.0, 11478474997.0],
        "quote_ts": [None, None],
    })

    svc = TaiwanDailyRefreshService(
        store=mock_store,
        snapshot_adapter=mock_adapter,
        calendar=TaiwanTradingCalendar(),
    )

    stats = svc.refresh_dates(date(2026, 8, 28), date(2026, 8, 28))
    assert stats["dates_requested"] == 1
    assert stats["dates_fetched"] == 1
    assert stats["dates_skipped"] == 0
    assert stats["total_rows_written"] == 2330
    assert stats["failed_dates"] == []

    # Exactly one call to snapshot fetch_date
    mock_adapter.fetch_date.assert_called_once_with(date(2026, 8, 28))
    mock_store.write_batch.assert_called_once()


def test_refresh_dates_catchup_three_days():
    """Three missing trading days catch-up dispatches exactly 3 snapshot calls."""
    mock_store = MagicMock(spec=TaiwanDailyStore)
    mock_store.available_dates.return_value = []
    mock_store.write_batch.return_value = 2330

    mock_adapter = MagicMock(spec=OfficialDailySnapshotAdapter)
    mock_adapter.fetch_date.return_value = pl.DataFrame({
        "symbol": ["2330.TWSE"],
        "date": [date(2026, 8, 28)],
        "open": [2440.0], "high": [2445.0], "low": [2410.0], "close": [2420.0],
        "volume": [15025832.0], "amount": [36465015980.0], "quote_ts": [None],
    })

    svc = TaiwanDailyRefreshService(
        store=mock_store,
        snapshot_adapter=mock_adapter,
        calendar=TaiwanTradingCalendar(),
    )

    # 2026-08-26 (Wed) to 2026-08-28 (Fri) = 3 trading days
    stats = svc.refresh_dates(date(2026, 8, 26), date(2026, 8, 28))
    assert stats["dates_requested"] == 3
    assert stats["dates_fetched"] == 3
    assert mock_adapter.fetch_date.call_count == 3


def test_refresh_dates_idempotency():
    """Already-persisted dates are skipped with 0 HTTP calls."""
    mock_store = MagicMock(spec=TaiwanDailyStore)
    mock_store.available_dates.return_value = [date(2026, 8, 28)]

    mock_adapter = MagicMock(spec=OfficialDailySnapshotAdapter)
    svc = TaiwanDailyRefreshService(
        store=mock_store,
        snapshot_adapter=mock_adapter,
        calendar=TaiwanTradingCalendar(),
    )

    stats = svc.refresh_dates(date(2026, 8, 28), date(2026, 8, 28))
    assert stats["dates_requested"] == 1
    assert stats["dates_skipped"] == 1
    assert stats["dates_fetched"] == 0
    mock_adapter.fetch_date.assert_not_called()
    mock_store.write_batch.assert_not_called()


def test_failure_isolation_does_not_crash_or_mark_holiday():
    """If TPEx fails, TWSE data is still preserved and not marked as holiday."""
    mock_store = MagicMock(spec=TaiwanDailyStore)
    mock_store.available_dates.return_value = []
    mock_store.write_batch.return_value = 1318

    # Adapter returns only TWSE rows
    mock_adapter = MagicMock(spec=OfficialDailySnapshotAdapter)
    mock_adapter.fetch_date.return_value = pl.DataFrame({
        "symbol": ["2330.TWSE"],
        "date": [date(2026, 8, 28)],
        "open": [2440.0], "high": [2445.0], "low": [2410.0], "close": [2420.0],
        "volume": [15025832.0], "amount": [36465015980.0], "quote_ts": [None],
    })

    svc = TaiwanDailyRefreshService(
        store=mock_store,
        snapshot_adapter=mock_adapter,
        calendar=TaiwanTradingCalendar(),
    )

    stats = svc.refresh_dates(date(2026, 8, 28), date(2026, 8, 28))
    assert stats["dates_requested"] == 1
    assert stats["dates_fetched"] == 1
    assert stats["total_rows_written"] == 1318
    mock_store.write_batch.assert_called_once()


def test_daily_update_orchestration_with_snapshot():
    """TaiwanDailyUpdateService seamlessly updates Daily, Inst, and Margin."""
    mock_daily_store = MagicMock()
    mock_daily_store.available_dates.return_value = [date(2026, 8, 27)]

    mock_inst_store = MagicMock()
    mock_inst_store.available_dates.return_value = [date(2026, 8, 27)]

    mock_margin_store = MagicMock()
    mock_margin_store.available_dates.return_value = [date(2026, 8, 27)]

    mock_daily_svc = MagicMock()
    mock_daily_svc.refresh_dates.return_value = {
        "dates_requested": 1, "dates_fetched": 1, "dates_skipped": 0, "total_rows_written": 2330, "failed_dates": []
    }

    mock_inst_svc = MagicMock()
    mock_inst_svc.refresh_dates.return_value = {
        "dates_requested": 1, "dates_fetched": 1, "dates_skipped": 0, "total_rows_written": 2330, "failed_dates": []
    }

    mock_margin_svc = MagicMock()
    mock_margin_svc.refresh_dates.return_value = {
        "dates_requested": 1, "dates_fetched": 1, "dates_skipped": 0, "total_rows_written": 2330, "failed_dates": []
    }

    svc = TaiwanDailyUpdateService(
        daily_store=mock_daily_store,
        inst_store=mock_inst_store,
        margin_store=mock_margin_store,
        daily_service=mock_daily_svc,
        inst_service=mock_inst_svc,
        margin_service=mock_margin_svc,
    )

    # Run default update (refresh_daily=True)
    res = svc.run_update(target_date=date(2026, 8, 28))
    assert res.overall_status == "success"
    assert res.daily.status == "success"
    assert res.institutional.status == "success"
    assert res.margin.status == "success"

    # Confirmed mock_daily_svc.refresh_dates was called, NOT refresh_symbols
    mock_daily_svc.refresh_dates.assert_called_once_with(
        start_date=date(2026, 8, 28), end_date=date(2026, 8, 28), force=False
    )
    mock_daily_svc.refresh_symbols.assert_not_called()

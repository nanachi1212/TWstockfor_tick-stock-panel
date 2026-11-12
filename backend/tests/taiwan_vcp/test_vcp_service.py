"""VCP 掃描服務：範圍、fail-closed 與排除計數（合成資料，不讀開發機資料、不連網）。"""
from __future__ import annotations

from unittest.mock import MagicMock

import polars as pl
from test_vcp_detect import _series

from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.observed_universe import ObservedUniverseStore
from app.taiwan.vcp.detect import MIN_BARS
from app.taiwan.vcp.service import TaiwanVcpScanService

SYMBOLS = ["2330.TWSE", "1101.TWSE", "2317.TWSE", "6488.TPEX", "0050.TWSE"]


def _master():
    master = MagicMock()
    master.to_dataframe.return_value = pl.DataFrame({
        "symbol": SYMBOLS, "code": [s[:4] for s in SYMBOLS], "exchange": [s[5:] for s in SYMBOLS],
        "name": ["甲", "乙", "丙", "丁", "戊"], "instrument_type": ["stock"] * 4 + ["etf"],
        "listing_status": ["active"] * 5, "industry": ["半導體業"] * 5,
    })
    return master


def _rows(symbol, bars):
    return [{"symbol": symbol, **b, "amount": (b["close"] or 0.0) * b["volume"], "quote_ts": None} for b in bars]


def _service(tmp_path, rows, coverage=True):
    daily = TaiwanDailyStore(tmp_path / "daily")
    daily.write_batch(pl.DataFrame(rows, schema_overrides={"amount": pl.Float64, "quote_ts": pl.Int64}))
    actions = MagicMock()
    bars = _series([20, 9, 4])
    actions.read_verified_coverage.return_value = (bars[0]["date"], bars[-1]["date"], ()) if coverage else None
    return TaiwanVcpScanService(daily, MagicMock(), _master(), ObservedUniverseStore(tmp_path / "census"), actions), bars


def _no_trade(bar):
    return {**bar, "open": None, "high": None, "low": None, "close": None, "volume": 0.0}


def test_scan_lists_valid_vcp_and_counts_each_exclusion(tmp_path):
    vcp = _series([20, 9, 4])
    target = vcp[-1]["date"]
    flat = [{**b, "close": 50.0, "open": 50.0, "high": 50.5, "low": 49.5} for b in vcp]
    with_gap = list(vcp)
    with_gap[-40] = _no_trade(with_gap[-40])  # 視窗內一個無成交日：略過，不排除
    broken = [dict(b) for b in flat]
    broken[-5]["high"] = broken[-5]["close"] - 1  # 高價低於收盤：價格異常
    rows = (_rows("2330.TWSE", with_gap) + _rows("1101.TWSE", flat[-(MIN_BARS - 10):])
            + _rows("2317.TWSE", broken) + _rows("6488.TPEX", [*flat[:-1], _no_trade(flat[-1])])
            + _rows("0050.TWSE", vcp))
    service, _ = _service(tmp_path, rows)

    snap = service.snapshot(target)

    assert snap["status"] == "available"
    assert snap["universe"] == 4  # ETF 不在掃描範圍
    assert snap["excluded"] == {"insufficient_history": 1, "invalid_ohlc": 1, "no_trade_on_target": 1}
    assert snap["eligible"] == 1
    assert [i["symbol"] for i in snap["items"]] == ["2330.TWSE"]
    item = snap["items"][0]
    assert item["no_trade_skipped"] == 1
    assert item["stop"] < item["price"] < item["pivot"]
    assert len(item["contractions"]) == 3


def test_missing_corporate_action_coverage_fails_closed(tmp_path):
    vcp = _series([20, 9, 4])
    service, _ = _service(tmp_path, _rows("2330.TWSE", vcp), coverage=False)
    snap = service.snapshot(vcp[-1]["date"])
    assert snap["status"] == "unavailable"
    assert snap["reason"] == "corporate_action_coverage_unavailable"
    assert snap["items"] == []


def test_target_without_daily_partition_is_unavailable(tmp_path):
    vcp = _series([20, 9, 4])
    service, _ = _service(tmp_path, _rows("2330.TWSE", vcp[:-1]))
    snap = service.snapshot(vcp[-1]["date"])
    assert snap["status"] == "unavailable" and snap["reason"] == "no_daily_data"

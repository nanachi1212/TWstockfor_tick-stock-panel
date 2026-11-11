"""迴歸測試: 即時指數 merge 不截斷盤後管道寫入的全量分區 (PR #46 問題 3)。"""
from datetime import date

import polars as pl

from app.tickflow.repository import DataStore, KlineRepository


def _enriched_row(symbol: str, close: float, dt: date) -> dict:
    return {
        "symbol": symbol, "date": dt,
        "open": close, "high": close, "low": close, "close": close,
        "volume": 1000, "amount": 10000.0,
        "quote_ts": 1753700400000,
    }


def test_merge_live_enriched_preserves_full_index_partition(tmp_path):
    """盤後管道 flush 寫入全量指數後, 即時 merge 部分指數不丟已有資料。"""
    repo = KlineRepository(DataStore(tmp_path))
    dt = date(2026, 7, 28)

    # 模擬盤後管道: flush 寫入全量 3 只指數
    full_df = pl.DataFrame([
        _enriched_row("000001.SH", 3000.0, dt),
        _enriched_row("399001.SZ", 10000.0, dt),
        _enriched_row("399006.SZ", 2000.0, dt),
    ])
    repo.flush_live_enriched_asset("index", full_df)

    # 模擬即時刷新: 只 merge 核心指數 1 只 (價格更新)
    partial_df = pl.DataFrame([
        _enriched_row("000001.SH", 3001.0, dt),
    ])
    repo.merge_live_enriched_asset("index", partial_df)

    # 驗證: 分區文件仍有 3 只指數, 000001.SH 價格已更新, 其他指數未丟失
    out = tmp_path / "kline_index_enriched" / f"date={dt.isoformat()}" / "part.parquet"
    result = pl.read_parquet(out)
    assert len(result) == 3, f"merge 後分區應有 3 只指數, 實際 {len(result)}"

    sh = result.filter(pl.col("symbol") == "000001.SH")
    assert sh["close"][0] == 3001.0, "merge 應更新 000001.SH 價格"

    sz = result.filter(pl.col("symbol") == "399001.SZ")
    assert sz["close"][0] == 10000.0, "399001.SZ 不應被 merge 覆蓋"

    cyb = result.filter(pl.col("symbol") == "399006.SZ")
    assert cyb["close"][0] == 2000.0, "399006.SZ 不應被 merge 覆蓋"


def test_merge_live_daily_preserves_full_index_partition(tmp_path):
    """日K merge 同樣不截斷全量分區。"""
    repo = KlineRepository(DataStore(tmp_path))
    dt = date(2026, 7, 28)

    # 盤後管道 flush 寫入全量 3 只指數日K
    full_df = pl.DataFrame([
        {"symbol": "000001.SH", "date": dt, "open": 3000.0, "high": 3010.0,
         "low": 2990.0, "close": 3000.0, "volume": 1000, "amount": 10000.0},
        {"symbol": "399001.SZ", "date": dt, "open": 10000.0, "high": 10010.0,
         "low": 9990.0, "close": 10000.0, "volume": 2000, "amount": 20000.0},
        {"symbol": "399006.SZ", "date": dt, "open": 2000.0, "high": 2010.0,
         "low": 1990.0, "close": 2000.0, "volume": 3000, "amount": 30000.0},
    ])
    repo.flush_live_daily_asset("index", full_df)

    # 即時 merge 部分指數
    partial_df = pl.DataFrame([
        {"symbol": "000001.SH", "date": dt, "open": 3000.0, "high": 3010.0,
         "low": 2990.0, "close": 3001.0, "volume": 1000, "amount": 10000.0},
    ])
    repo.merge_live_daily_asset("index", partial_df)

    out = tmp_path / "kline_index_daily" / f"date={dt.isoformat()}" / "part.parquet"
    result = pl.read_parquet(out)
    assert len(result) == 3, f"merge 後分區應有 3 只指數, 實際 {len(result)}"
    assert result.filter(pl.col("symbol") == "000001.SH")["close"][0] == 3001.0

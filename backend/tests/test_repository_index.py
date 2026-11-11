"""指數資產路由 — repository 層測試。"""
import datetime as _dt
import os

import polars as pl
import pytest

from app.repository import DataStore, KlineRepository


@pytest.fixture()
def repo(tmp_path):
    return KlineRepository(DataStore(tmp_path))


def _write_index_instruments(repo, rows):
    pl.DataFrame(rows).write_parquet(
        repo.store.data_dir / "instruments_index" / "part.parquet"
    )
    repo._refresh_index_instruments()


def test_name_map_includes_index(repo):
    _write_index_instruments(repo, {
        "symbol": ["IX0001.TWSE"], "name": ["測試指數"],
        "code": ["000001"], "asset_type": ["index"],
    })
    names = repo.get_name_map(["IX0001.TWSE", "2330.TWSE"])
    assert names.get("IX0001.TWSE") == "測試指數"
    assert "2330.TWSE" not in names  # 未收錄不造名


def test_name_map_stock_beats_index(repo):
    """同名 symbol 同時出現在股票/指數維表時, 股票名稱優先。"""
    _write_index_instruments(repo, {
        "symbol": ["2330.TWSE"], "name": ["某指數"],
        "code": ["2330"], "asset_type": ["index"],
    })
    pl.DataFrame({
        "symbol": ["2330.TWSE"], "name": ["台積電"], "code": ["2330"],
        "exchange": ["SH"], "region": ["CN"], "type": ["stock"],
        "listing_date": [None], "total_shares": [None], "float_shares": [None],
        "tick_size": [None], "limit_up": [None], "limit_down": [None],
        "as_of": ["2026-07-25"],
    }).write_parquet(repo.store.data_dir / "instruments" / "instruments.parquet")
    repo._refresh_instruments()
    assert repo.get_name_map(["2330.TWSE"]).get("2330.TWSE") == "台積電"


def _write_stock_instruments(repo, symbols, names):
    pl.DataFrame({
        "symbol": symbols, "name": names, "code": [s[:6] for s in symbols],
        "exchange": ["SH"] * len(symbols), "region": ["CN"] * len(symbols),
        "type": ["stock"] * len(symbols),
        "listing_date": [None] * len(symbols), "total_shares": [None] * len(symbols),
        "float_shares": [None] * len(symbols), "tick_size": [None] * len(symbols),
        "limit_up": [None] * len(symbols), "limit_down": [None] * len(symbols),
        "as_of": ["2026-08-14"] * len(symbols),
    }).write_parquet(repo.store.data_dir / "instruments" / "instruments.parquet")
    repo._refresh_instruments()


def test_name_map_partial_query_does_not_poison_cache(repo):
    """帶 symbols 的部分查詢不能把殘缺映射寫入快取 (自選新加股票無名稱的迴歸).

    舊 bug: 首次 get_name_map(["2330.TWSE"]) 把只含 600000 的映射快取住,
    之後自選加入 2317.TWSE 再查名稱命中殘缺快取 → name=None。
    """
    _write_stock_instruments(repo, ["2330.TWSE", "2317.TWSE"], ["台積電", "鴻海"])
    first = repo.get_name_map(["2330.TWSE"])
    assert first == {"2330.TWSE": "台積電"}
    # 快取必須是全量: 後續其他 symbols 查詢仍能命中
    second = repo.get_name_map(["2317.TWSE"])
    assert second == {"2317.TWSE": "鴻海"}
    full = repo.get_name_map()
    assert full == {"2330.TWSE": "台積電", "2317.TWSE": "鴻海"}


def test_name_map_cache_invalidated_on_instruments_refresh(repo):
    """維表重新整理後快取必須失效: 新收錄的股票能立刻查到名稱。"""
    _write_stock_instruments(repo, ["2330.TWSE"], ["台積電"])
    assert repo.get_name_map(["2330.TWSE"]) == {"2330.TWSE": "台積電"}
    _write_stock_instruments(repo, ["2330.TWSE", "6488.TPEX"], ["台積電", "環球晶"])
    assert repo.get_name_map(["6488.TPEX"]) == {"6488.TPEX": "環球晶"}


def test_execute_one_releases_parquet_file(repo):
    minute_dir = repo.store.data_dir / "kline_minute" / "date=2026-07-23"
    minute_dir.mkdir(parents=True, exist_ok=True)
    part = minute_dir / "part.parquet"
    replacement = minute_dir / "part.parquet.tmp"
    minute = pl.DataFrame({
        "symbol": ["2330.TWSE"],
        "datetime": [_dt.datetime(2026, 7, 23, 9, 30)],
        "close": [10.0],
    })
    minute.write_parquet(part)
    repo.store._register_views()

    assert repo.execute_one("SELECT max(datetime) FROM kline_minute")[0] == _dt.datetime(2026, 7, 23, 9, 30)

    minute.write_parquet(replacement)
    os.replace(replacement, part)


def _write_index_enriched(repo, dates_rows):
    for ds, rows in dates_rows.items():
        d = repo.store.data_dir / "kline_index_enriched" / f"date={ds}"
        d.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(rows).write_parquet(d / "part.parquet")


def _index_rows(ds, close=3000.0):
    return [{
        "symbol": "IX0001.TWSE", "date": _dt.date.fromisoformat(ds),
        "open": close - 10, "high": close + 20, "low": close - 20, "close": close,
        "volume": 1_000_000, "amount": 1e9,
    }]


def test_get_enriched_latest_asset_index(repo):
    _write_index_enriched(repo, {
        "2026-07-23": _index_rows("2026-07-23", 2990.0),
        "2026-07-24": _index_rows("2026-07-24", 3000.0),
    })
    df, dt = repo.get_enriched_latest_asset("index")
    assert str(dt) == "2026-07-24"
    assert df["symbol"].to_list() == ["IX0001.TWSE"]
    assert "ma5" in df.columns or "rsi_14" in df.columns  # 重算產出指標欄


def test_get_enriched_latest_asset_index_cold_no_refresh(repo):
    df, dt = repo.get_enriched_latest_asset("index", refresh=False)
    assert df.is_empty() and dt is None


def test_flush_live_enriched_asset_index_updates_cache(repo):
    df = pl.DataFrame([{
        "symbol": "IX0001.TWSE", "date": _dt.date(2026, 7, 25),
        "open": 3000.0, "high": 3010.0, "low": 2990.0, "close": 3005.0,
        "volume": 1_000_000, "amount": 1e9, "ma5": 3001.0, "rsi_14": 55.0,
    }])
    repo.flush_live_enriched_asset("index", df)
    cached, dt = repo.get_enriched_latest_asset("index", refresh=False)
    assert str(dt) == "2026-07-25"
    assert cached["close"].to_list() == [3005.0]
    assert (repo.store.data_dir / "kline_index_enriched" / "date=2026-07-25" / "part.parquet").exists()


def _merge_row(symbol, close):
    return {
        "symbol": symbol, "date": _dt.date(2026, 7, 25),
        "open": close - 5, "high": close + 5, "low": close - 6, "close": close,
        "volume": 1_000, "amount": 1e6,
    }


def test_merge_live_enriched_asset_index_merges_cache(repo):
    """merge 路徑: 兩次合併快取取並集 (不 NameError, 不丟已有快取)。"""
    repo.merge_live_enriched_asset("index", pl.DataFrame([_merge_row("IX0001.TWSE", 3000.0)]))
    repo.merge_live_enriched_asset("index", pl.DataFrame([_merge_row("IX0002.TWSE", 4000.0)]))
    cached, dt = repo.get_enriched_latest_asset("index", refresh=False)
    assert str(dt) == "2026-07-25"
    assert set(cached["symbol"].to_list()) == {"IX0001.TWSE", "IX0002.TWSE"}


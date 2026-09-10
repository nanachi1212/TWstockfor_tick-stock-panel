"""Security names must be plain text at JSON and legacy-cache boundaries."""
from dataclasses import replace

import polars as pl
import pytest

from app.taiwan.universe import TaiwanSecurityMaster, adapters


@pytest.mark.parametrize("name", ["元大S&amp;P500", "元大S&#38;P500", "元大S&#x26;P500", "元大S&P500"])
def test_official_etf_name_is_plain_text(monkeypatch, name):
    monkeypatch.setattr(adapters, "_get_json", lambda url: [{
        "基金代號": "00646", "基金簡稱": name,
        "基金類型": "國外成分證券指數股票型基金", "是否包含國外成分股": "是",
    }])
    instrument = adapters._official_twse_etf_directory()[0]
    assert instrument.name == "元大S&P500"
    assert instrument.source == "TWSE_OPENAPI"
    assert instrument.underlying_scope == "foreign"


@pytest.mark.parametrize("name", ["元大S&amp;P500", "元大S&#38;P500", "元大S&P500", "台積電"])
def test_legacy_cache_normalizes_without_rewriting(tmp_path, monkeypatch, name):
    monkeypatch.setattr(adapters, "_get_json", lambda url: [{
        "基金代號": "00646", "基金簡稱": "元大S&P500",
    }])
    item = replace(adapters._official_twse_etf_directory()[0], name=name)
    cache = tmp_path / "master.parquet"
    pl.DataFrame([item.to_dict()]).write_parquet(cache)
    original = cache.read_bytes()
    master = TaiwanSecurityMaster(cache_path=cache)
    assert master.load_cache()
    expected = "台積電" if name == "台積電" else "元大S&P500"
    assert master.get_instrument(item.symbol).name == expected
    assert master.search(expected)[0]["symbol"] == item.symbol
    assert master.to_dataframe()["name"].to_list() == [expected]
    assert master.to_provider_dataframe("etf")["name"].to_list() == [expected]
    assert cache.read_bytes() == original
    saved = tmp_path / "roundtrip.parquet"
    master.save_cache(saved)
    reloaded = TaiwanSecurityMaster(cache_path=saved)
    assert reloaded.load_cache()
    assert reloaded.get_instrument(item.symbol).name == expected

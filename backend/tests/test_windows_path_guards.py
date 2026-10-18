"""Windows 路径防护: 缓存键、插件名、持股同步路径在碰文件系统前就拒绝穿越与 UNC 路径。"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.data_providers.custom import loader
from app.services import portfolio_import
from app.taiwan.finmind_cache import FinMindCache
from app.taiwan.news_service import TaiwanNewsService

_BAD_KEYS = [
    "..\\..\\victim",
    "../../victim",
    "\\\\10.0.0.66\\share\\x",
    "//10.0.0.66/share/x",
    "C:\\Windows\\x",
    "..",
    "a/b",
    "",
    "CON",
    "nul",
    "COM1",
    "LPT9.TWSE",
]


@pytest.mark.parametrize("key", ["2330", "00878", "00632R", "2330.TWSE", "TX_2026-10-18", "TXO_2026-10-18"])
def test_finmind_cache_accepts_normal_keys(tmp_path: Path, key: str):
    cache = FinMindCache(tmp_path)
    cache.set("TaiwanStockNews", key, [{"a": 1}])
    assert cache.get("TaiwanStockNews", key)["data"] == [{"a": 1}]
    assert (tmp_path / "TaiwanStockNews" / f"{key}.json").is_file()


@pytest.mark.parametrize("key", _BAD_KEYS)
def test_finmind_cache_rejects_path_keys(tmp_path: Path, key: str):
    cache_dir = tmp_path / "cache"
    cache = FinMindCache(cache_dir)
    cache.set("TaiwanStockNews", key, [])
    assert cache.get("TaiwanStockNews", key) is None
    cache.clear("TaiwanStockNews", key)
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert written == []


def test_news_traversal_symbol_does_not_overwrite_files(tmp_path: Path):
    victim = tmp_path / "victim.json"
    victim.write_text('{"keep": true}', encoding="utf-8")
    adapter = MagicMock()
    adapter.fetch_dataset.return_value = []
    service = TaiwanNewsService(finmind_adapter=adapter, finmind_cache=FinMindCache(tmp_path / "cache"))

    service.get_recent_news("..\\..\\victim.TWSE")
    service.get_recent_news("../../victim.TWSE")
    assert json.loads(victim.read_text(encoding="utf-8")) == {"keep": True}

    # 正常 ETF 代码照常查询并写入缓存
    assert service.get_recent_news("00878").status == "available"
    assert (tmp_path / "cache" / "TaiwanStockNews" / "00878.json").is_file()


@pytest.mark.parametrize("name", ["\\\\10.0.0.66\\x", "..\\..", "../x", "C:\\x", "FUYAO", ""])
def test_plugin_name_rejected_before_filesystem(monkeypatch: pytest.MonkeyPatch, name: str):
    monkeypatch.setattr(Path, "exists", lambda self: pytest.fail(f"touched filesystem: {self}"))
    assert loader.plugin_manifest(name) is None
    assert loader.probe_plugin_key(name, "k")[0] is False
    with pytest.raises(ValueError):
        loader.plugin_dir_of(name)


def test_valid_plugin_manifest_still_loads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    (tmp_path / "fuyao").mkdir()
    (tmp_path / "fuyao" / "plugin.yaml").write_text("name: fuyao\n", encoding="utf-8")
    monkeypatch.setattr(loader, "plugins_dir", lambda: tmp_path)
    assert loader.plugin_manifest("fuyao") == {"name": "fuyao"}
    assert loader.plugin_dir_of("fuyao") == tmp_path / "fuyao"


@pytest.fixture()
def cloud_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "OneDrive"
    root.mkdir()
    monkeypatch.setattr(portfolio_import, "cloud_roots", lambda: {"onedrive": [root], "gdrive": []})
    return root


@pytest.mark.parametrize(
    "path",
    ["\\\\10.0.0.66\\s\\持股.csv", "//10.0.0.66/s/持股.csv", "\\\\?\\C:\\x\\持股.csv", "持股.csv", ""],
)
def test_portfolio_unc_and_relative_paths_rejected_before_filesystem(
    monkeypatch: pytest.MonkeyPatch, cloud_root: Path, path: str
):
    for attr in ("resolve", "is_file", "stat", "exists"):
        monkeypatch.setattr(Path, attr, lambda self, *a, _attr=attr, **k: pytest.fail(f"{_attr}({self})"))
    assert portfolio_import.allowed_path(path) is None
    with pytest.raises(PermissionError):
        portfolio_import.read_file(path)


def test_portfolio_file_inside_root_still_reads(cloud_root: Path, tmp_path: Path):
    good = cloud_root / "持股.csv"
    good.write_text("代號,股數\n2330,1000\n", encoding="utf-8")
    assert "2330" in portfolio_import.read_file(str(good))

    outside = tmp_path / "持股.csv"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(PermissionError):
        portfolio_import.read_file(str(outside))
    with pytest.raises(PermissionError):
        portfolio_import.read_file(str(cloud_root / ".." / "持股.csv"))


def test_auto_sync_does_not_stat_restored_unc_path(monkeypatch: pytest.MonkeyPatch, cloud_root: Path):
    from app.api import taiwan as taiwan_api
    from app.services import preferences

    unc = "\\\\attacker.example\\share\\持股.csv"
    monkeypatch.setattr(preferences, "load", lambda: {"portfolio_auto_sync_path": unc})
    monkeypatch.setattr(Path, "is_file", lambda self: pytest.fail(f"is_file({self})"))
    result = taiwan_api.get_portfolio_auto_sync()
    assert result["modified"] is None

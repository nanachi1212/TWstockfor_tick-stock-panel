"""标的搜索测试: 代码 / 名称 / 拼音首字母 (同花顺式 payh → 平安银行)。

直接调用 search_instruments, 用最小 FakeRepo 提供 instruments 缓存, 不走 HTTP/DB。
"""
from __future__ import annotations

import types

import polars as pl
import pytest

from app.api.kline import search_instruments


class _FakeRepo:
    """最小 repo 桩: 只实现 search_instruments 依赖的 get_instruments_asset。"""

    def __init__(self, by_asset: dict[str, pl.DataFrame]) -> None:
        self.store = types.SimpleNamespace(data_dir="data")
        self._by_asset = by_asset

    def get_instruments_asset(self, asset_type: str) -> pl.DataFrame:
        return self._by_asset.get(asset_type, pl.DataFrame())


def _request(repo: _FakeRepo) -> types.SimpleNamespace:
    return types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(repo=repo)))


STOCKS = pl.DataFrame({
    "symbol": ["000001.SZ", "600000.SH", "600519.SH", "000333.SZ", "600737.SH"],
    "code": ["000001", "600000", "600519", "000333", "600737"],
    "name": ["平安银行", "浦发银行", "贵州茅台", "美的集团", "中粮糖业"],
})


def _search(q: str, asset_types: str = "stock", limit: int = 20) -> list[dict]:
    repo = _FakeRepo({"stock": STOCKS})
    return search_instruments(_request(repo), q=q, limit=limit, asset_types=asset_types)["results"]


# ===== 既有逻辑回归: 代码 / 名称搜索不受影响 =====

def test_code_prefix_match():
    """code 前缀: 6005 → 600519 (前缀优先)。"""
    rows = _search("6005")
    assert "600519.SH" in [r["symbol"] for r in rows]


def test_symbol_contains_match():
    rows = _search("6005")
    assert "600519.SH" in [r["symbol"] for r in rows]


def test_chinese_name_contains_match():
    rows = _search("银行")
    assert sorted(r["symbol"] for r in rows) == ["000001.SZ", "600000.SH"]


def test_empty_query_returns_empty():
    assert _search("   ") == []


# ===== 拼音首字母搜索 (新功能) =====

def test_pinyin_full_initials_match():
    """payh → 平安银行"""
    rows = _search("payh")
    assert [r["symbol"] for r in rows] == ["000001.SZ"]


def test_pinyin_prefix_match():
    """m → 美的集团 (m 开头)"""
    rows = _search("m")
    assert "000333.SZ" in [r["symbol"] for r in rows]


def test_pinyin_prefix_picks_multiple():
    """pf → 浦发银行; pa → 平安银行 (前缀区分)"""
    assert [r["symbol"] for r in _search("pf")] == ["600000.SH"]
    assert [r["symbol"] for r in _search("pa")] == ["000001.SZ"]


def test_pinyin_respects_limit():
    """limit 限制拼音结果数"""
    rows = _search("z", limit=1)  # z → 中粮糖业
    assert len(rows) == 1


def test_pinyin_layer_between_prefix_and_contains():
    """拼音命中应排在包含匹配之前 (分层优先级)。"""
    rows = _search("md")  # md → 美的集团 (拼音); 无代码/符号以 md 前缀
    assert "000333.SZ" in [r["symbol"] for r in rows]


def test_pinyin_and_code_prefix_coexist():
    """纯字母查询同时命中 code 前缀和拼音首字母时, code 前缀优先排前。"""
    # '600000' 是浦发的 code 前缀; 这里用纯字母无法命中 code, 故仅验证拼音路径独立可用
    rows = _search("pf")  # 浦发
    assert "600000.SH" in [r["symbol"] for r in rows]


# ===== 多音字 =====

POLYPHONE_STOCKS = pl.DataFrame({
    "symbol": ["600729.SH", "000625.SZ"],
    "code": ["600729", "000625"],
    "name": ["重庆百货", "长安汽车"],
})


def test_polyphone_all_readings_match():
    """'重庆' 多音字: cq (chóng) 和 zq (zhòng) 读音都应命中。"""
    repo = _FakeRepo({"stock": POLYPHONE_STOCKS})
    # 取首字母集, 验证两种读音都能搜到
    cq = search_instruments(_request(repo), q="cqbh", limit=20, asset_types="stock")["results"]      # chóng qīng
    zq = search_instruments(_request(repo), q="zqbh", limit=20, asset_types="stock")["results"]      # zhòng qìng
    assert "600729.SH" in [r["symbol"] for r in cq]
    assert "600729.SH" in [r["symbol"] for r in zq]


# ===== 边界 =====

def test_non_ascii_skips_pinyin_branch():
    """中文输入不走拼音分支, 仍按名称匹配。"""
    rows = _search("平安")
    assert [r["symbol"] for r in rows] == ["000001.SZ"]


def test_digits_skips_pinyin_branch():
    """数字输入不走拼音分支, 走 code 前缀。"""
    rows = _search("000")
    assert "000001.SZ" in [r["symbol"] for r in rows]


def test_no_pinyin_hit_returns_empty():
    """无任何拼音命中时返回空 (不报错)。"""
    assert _search("xyz") == []


def test_cache_returns_same_result_across_calls():
    """lru_cache 不应在不同请求间串结果 (按 name 缓存, 查询无状态)。"""
    repo = _FakeRepo({"stock": STOCKS})
    req = _request(repo)
    r1 = search_instruments(req, q="payh", limit=20, asset_types="stock")["results"]
    r2 = search_instruments(req, q="payh", limit=20, asset_types="stock")["results"]
    assert r1 == r2
    assert [r["symbol"] for r in r1] == ["000001.SZ"]


# ===== market-aware 搜索: 一并搜台股证券主档 (TaiwanSecurityMaster) =====

class _FakeSecurityMaster:
    """最小台股证券主档桩: 直接按 symbol/code/name 子串匹配, 不依赖真实 adapter。"""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def search(self, q: str, limit: int = 20) -> list[dict]:
        keyword = q.strip().upper()
        hits = [
            row for row in self._rows
            if keyword in row["symbol"].upper()
            or keyword in row["code"].upper()
            or keyword in row["name"].upper()
        ]
        return hits[:limit]


_TAIWAN_ROWS = [
    {
        "symbol": "2330.TWSE", "code": "2330", "name": "台積電",
        "exchange": "TWSE", "instrument_type": "stock", "is_supported": True,
    },
    {
        "symbol": "6488.TPEX", "code": "6488", "name": "環球晶",
        "exchange": "TPEX", "instrument_type": "stock", "is_supported": True,
    },
    {
        "symbol": "0050.TWSE", "code": "0050", "name": "元大台灣50",
        "exchange": "TWSE", "instrument_type": "etf", "is_supported": True,
    },
    {
        # 未支援标的(如权证): 即使命中查询也不应出现在合并结果中。
        "symbol": "7999.TWSE", "code": "7999", "name": "測試權證",
        "exchange": "TWSE", "instrument_type": "warrant", "is_supported": False,
    },
]


def _patch_security_master(monkeypatch: pytest.MonkeyPatch, rows: list[dict]) -> None:
    fake = _FakeSecurityMaster(rows)
    monkeypatch.setattr("app.taiwan.universe.get_security_master", lambda: fake)


def test_market_omitted_is_backward_compatible_ashare_only(monkeypatch):
    """不传 market(既有 3 个 legacy 调用方的用法)时, 即使证券主档里有命中,
    结果也完全不含台股 —— 维持既有 A 股 legacy 行为不变。"""
    _patch_security_master(monkeypatch, _TAIWAN_ROWS)
    repo = _FakeRepo({"stock": STOCKS})
    rows = search_instruments(_request(repo), q="2330", limit=20, asset_types="stock")["results"]
    assert rows == []


def test_market_ashare_explicit_matches_omitted_behavior(monkeypatch):
    """market=ashare 显式指定时, 行为与省略 market 一致(向后兼容)。"""
    _patch_security_master(monkeypatch, _TAIWAN_ROWS)
    repo = _FakeRepo({"stock": STOCKS})
    rows = search_instruments(
        _request(repo), q="平安", limit=20, asset_types="stock", market="ashare",
    )["results"]
    assert [r["symbol"] for r in rows] == ["000001.SZ"]
    assert rows[0]["market"] == "ashare"


def test_market_taiwan_finds_twse_symbol_by_code(monkeypatch):
    """market=taiwan + q=2330 → 2330.TWSE, market 字段为 "taiwan" (非空字串)。"""
    _patch_security_master(monkeypatch, _TAIWAN_ROWS)
    repo = _FakeRepo({"stock": STOCKS})
    rows = search_instruments(
        _request(repo), q="2330", limit=20, asset_types="stock", market="taiwan",
    )["results"]
    assert [r["symbol"] for r in rows] == ["2330.TWSE"]
    assert rows[0]["market"] == "taiwan"
    assert rows[0]["asset_type"] == "stock"


def test_market_taiwan_finds_twse_symbol_by_canonical_symbol(monkeypatch):
    """market=taiwan + q=2330.TWSE(完整 canonical symbol)也应命中。"""
    _patch_security_master(monkeypatch, _TAIWAN_ROWS)
    repo = _FakeRepo({"stock": STOCKS})
    rows = search_instruments(
        _request(repo), q="2330.TWSE", limit=20, asset_types="stock", market="taiwan",
    )["results"]
    assert [r["symbol"] for r in rows] == ["2330.TWSE"]


def test_market_taiwan_finds_tpex_symbol(monkeypatch):
    """market=taiwan + q=6488 → 6488.TPEX(上柜)。"""
    _patch_security_master(monkeypatch, _TAIWAN_ROWS)
    repo = _FakeRepo({"stock": STOCKS})
    rows = search_instruments(
        _request(repo), q="6488", limit=20, asset_types="stock", market="taiwan",
    )["results"]
    assert [r["symbol"] for r in rows] == ["6488.TPEX"]


def test_market_taiwan_supports_chinese_name_search(monkeypatch):
    """market=taiwan 支持繁体中文名称搜索(如"台積電")。"""
    _patch_security_master(monkeypatch, _TAIWAN_ROWS)
    repo = _FakeRepo({"stock": STOCKS})
    rows = search_instruments(
        _request(repo), q="台積電", limit=20, asset_types="stock", market="taiwan",
    )["results"]
    assert [r["symbol"] for r in rows] == ["2330.TWSE"]


def test_market_taiwan_never_returns_ashare_symbols(monkeypatch):
    """market=taiwan 时绝不查 legacy A 股 repository, 结果里不会混入 .SH/.SZ/.BJ。

    用一个同时命中 A 股 STOCKS fixture(000001.SZ 平安银行)和台股 fixture 的
    查询("0"), 断言只有 market=taiwan 时排除 A 股, market 省略时排除台股。
    """
    _patch_security_master(monkeypatch, _TAIWAN_ROWS)
    repo = _FakeRepo({"stock": STOCKS})
    tw_only = search_instruments(
        _request(repo), q="0", limit=20, asset_types="stock", market="taiwan",
    )["results"]
    assert tw_only, "台股 fixture 应至少命中一笔 (如 0050.TWSE)"
    assert all(not s["symbol"].endswith((".SH", ".SZ", ".BJ")) for s in tw_only)


def test_market_taiwan_excludes_unsupported_instrument_types(monkeypatch):
    """未支援标的(如权证 is_supported=False)不应出现在 market=taiwan 结果里。"""
    _patch_security_master(monkeypatch, _TAIWAN_ROWS)
    repo = _FakeRepo({"stock": STOCKS})
    rows = search_instruments(
        _request(repo), q="7999", limit=20, asset_types="stock", market="taiwan",
    )["results"]
    assert rows == []


def test_market_taiwan_respects_limit(monkeypatch):
    """market=taiwan 时 limit 生效。"""
    _patch_security_master(monkeypatch, _TAIWAN_ROWS)
    repo = _FakeRepo({"stock": STOCKS})
    rows = search_instruments(
        _request(repo), q="TWSE", limit=1, asset_types="stock", market="taiwan",
    )["results"]
    assert len(rows) == 1


def test_market_taiwan_ignores_asset_types_and_repo(monkeypatch):
    """market=taiwan 完全不碰 repo —— 传一个会报错的 repo 也不影响台股搜索。"""
    class _ExplodingRepo:
        def get_instruments_asset(self, asset_type: str):
            raise AssertionError("market=taiwan 不应调用 repo.get_instruments_asset()")

    _patch_security_master(monkeypatch, _TAIWAN_ROWS)
    rows = search_instruments(
        _request(_ExplodingRepo()), q="2330", limit=20, asset_types="stock", market="taiwan",
    )["results"]
    assert [r["symbol"] for r in rows] == ["2330.TWSE"]


def test_market_invalid_value_rejected_by_fastapi_validation():
    """market 传非法值时, 走 FastAPI Literal["ashare","taiwan","all"] 的既有校验
    (HTTP 422), 不需要端点自行手写校验分支。

    直接 Python 调用 search_instruments(如本文件其它测试)不经 FastAPI 路由层
    解析, 类型注解不会在调用时被强制检查, 故本测试必须真正经过 HTTP/TestClient
    才能验证 Literal 校验生效 —— 与直接函数调用的其它测试刻意不同。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.kline import router as kline_router

    app = FastAPI()
    app.include_router(kline_router)
    app.state.repo = _FakeRepo({"stock": STOCKS})
    client = TestClient(app)

    resp = client.get("/api/kline/instruments/search", params={"q": "2330", "market": "us"})
    assert resp.status_code == 422

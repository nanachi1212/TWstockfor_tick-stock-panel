"""Phase 8B-5.0.4 — /api/watchlist/enriched 的 market-aware dispatch 回归测试。

验证 canonical Taiwan symbol (2330.TWSE / 6488.TPEX) 与 legacy A 股 symbol
混合出现在同一份自选清单时, 各自走独立的 enrichment 路径, 互不影响, 且
never 把 A 股 symbol 送进 Taiwan 分支或反之。全程 mock, 无即时网络。
"""
from __future__ import annotations

from types import SimpleNamespace

import polars as pl

from app.api import watchlist as wl_api


class _FakeRepo:
    """镜像 test_watchlist_enriched_join.py 的最小 repo mock。"""

    def __init__(self, enriched_df=None, enriched_date=None, name_map=None,
                 etf_set=None, index_set=None):
        self._enriched = enriched_df if enriched_df is not None else pl.DataFrame(schema={"symbol": pl.Utf8})
        self._enriched_date = enriched_date
        self._instruments = pl.DataFrame()
        self._name_map = name_map or {}
        self._etf_set = etf_set or set()
        self._index_set = index_set or set()

    def get_enriched_latest(self):
        return self._enriched, self._enriched_date

    def get_enriched_latest_asset(self, asset):
        return pl.DataFrame(schema={"symbol": pl.Utf8}), None

    def get_etf_symbol_set(self):
        return self._etf_set

    def get_index_symbol_set(self):
        return self._index_set

    def get_instruments(self):
        return self._instruments

    def get_name_map(self, symbols):
        return {s: n for s, n in self._name_map.items() if s in (symbols or [])}


def _make_request(repo):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=repo)))


def _enriched_df(symbols_data):
    return pl.DataFrame(
        [{"symbol": s, "close": c, "change_pct": p, "amount": a}
         for s, c, p, a in symbols_data],
        schema_overrides={"close": pl.Float64, "change_pct": pl.Float64, "amount": pl.Float64},
    )


def test_mixed_taiwan_and_ashare_watchlist_both_return(monkeypatch):
    """同时有台股和 legacy A 股: 两者都返回, 各走各的 enrichment path。"""
    monkeypatch.setattr(
        wl_api.watchlist, "list_symbols",
        lambda: [{"symbol": "2330.TWSE"}, {"symbol": "600519"}],
    )
    repo = _FakeRepo(
        enriched_df=_enriched_df([("600519", 1800.0, 1.2, 1e9)]),
        enriched_date="2026-07-08",
        name_map={"600519": "贵州茅台"},
    )

    def _fake_taiwan_enrich(symbols, **kwargs):
        assert symbols == ["2330.TWSE"], "不得把 A 股 symbol 混入 Taiwan enrichment 调用"
        return [{
            "symbol": "2330.TWSE", "name": "台積電", "asset_type": "stock",
            "close": 1100.0, "prev_close": 1080.0, "change_amount": 20.0, "change_pct": 1.85,
        }]

    monkeypatch.setattr(
        "app.taiwan.watchlist_enrichment.enrich_taiwan_watchlist_rows", _fake_taiwan_enrich,
    )

    res = wl_api.watchlist_enriched(_make_request(repo), ext_columns=None)
    rows = {r["symbol"]: r for r in res["rows"]}

    assert set(rows) == {"2330.TWSE", "600519"}
    assert rows["2330.TWSE"]["close"] == 1100.0
    assert rows["2330.TWSE"]["name"] == "台積電"
    assert rows["600519"]["close"] == 1800.0
    assert rows["600519"]["name"] == "贵州茅台"
    # Taiwan 行也应有(补 None 后的)技术指标 key, 与 legacy 行 key 集合一致
    assert "rsi_14" in rows["2330.TWSE"]
    assert rows["2330.TWSE"]["rsi_14"] is None


def test_mixed_watchlist_preserves_add_order(monkeypatch):
    """混合自选顺序应保持使用者的加入顺序, 不因分市场处理而打乱。"""
    monkeypatch.setattr(
        wl_api.watchlist, "list_symbols",
        lambda: [{"symbol": "600519"}, {"symbol": "2330.TWSE"}, {"symbol": "000001"}],
    )
    repo = _FakeRepo(
        enriched_df=_enriched_df([
            ("600519", 1800.0, 1.2, 1e9),
            ("000001", 15.0, 0.3, 2e9),
        ]),
        enriched_date="2026-07-08",
    )
    monkeypatch.setattr(
        "app.taiwan.watchlist_enrichment.enrich_taiwan_watchlist_rows",
        lambda symbols, **kwargs: [{"symbol": s, "asset_type": "stock", "close": 1100.0} for s in symbols],
    )

    res = wl_api.watchlist_enriched(_make_request(repo), ext_columns=None)
    assert [r["symbol"] for r in res["rows"]] == ["600519", "2330.TWSE", "000001"]


def test_taiwan_only_watchlist_does_not_touch_legacy_repo(monkeypatch):
    """自选清单全是台股时: 不得调用 legacy A 股 repo 的 enriched 查询路径,
    且不会因 legacy df 为空而把台股行漏掉(早期 return 的回归)。"""
    monkeypatch.setattr(
        wl_api.watchlist, "list_symbols",
        lambda: [{"symbol": "2330.TWSE"}, {"symbol": "6488.TPEX"}],
    )

    class _ExplodingRepo(_FakeRepo):
        def get_instruments(self):
            raise AssertionError("全台股自选不应查 legacy A 股 instruments 缓存")

    repo = _ExplodingRepo()
    monkeypatch.setattr(
        "app.taiwan.watchlist_enrichment.enrich_taiwan_watchlist_rows",
        lambda symbols, **kwargs: [
            {"symbol": s, "name": s, "asset_type": "stock", "close": 100.0} for s in symbols
        ],
    )

    res = wl_api.watchlist_enriched(_make_request(repo), ext_columns=None)
    assert {r["symbol"] for r in res["rows"]} == {"2330.TWSE", "6488.TPEX"}
    assert all(r["close"] == 100.0 for r in res["rows"])


def test_taiwan_enrichment_exception_does_not_break_legacy_rows(monkeypatch):
    """Taiwan enrichment 抛异常时: legacy A 股行仍正常返回, 台股行退化为占位行, 不 500。"""
    monkeypatch.setattr(
        wl_api.watchlist, "list_symbols",
        lambda: [{"symbol": "2330.TWSE"}, {"symbol": "600519"}],
    )
    repo = _FakeRepo(
        enriched_df=_enriched_df([("600519", 1800.0, 1.2, 1e9)]),
        enriched_date="2026-07-08",
        name_map={"600519": "贵州茅台"},
    )

    def _raise(symbols, **kwargs):
        raise RuntimeError("taiwan provider outage")

    monkeypatch.setattr(
        "app.taiwan.watchlist_enrichment.enrich_taiwan_watchlist_rows", _raise,
    )

    res = wl_api.watchlist_enriched(_make_request(repo), ext_columns=None)
    rows = {r["symbol"]: r for r in res["rows"]}
    assert rows["600519"]["close"] == 1800.0, "legacy 行不应被台股异常连累"
    assert rows["2330.TWSE"]["close"] is None
    assert rows["2330.TWSE"]["symbol"] == "2330.TWSE"

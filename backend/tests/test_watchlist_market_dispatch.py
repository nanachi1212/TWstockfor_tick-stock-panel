"""Legacy watchlist metadata survives without reading any retired market feed."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import watchlist as api


class _ExplodingRepo:
    def __getattr__(self, name):
        raise AssertionError(f"watchlist must not read legacy repository: {name}")


def _request():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=_ExplodingRepo())))


def test_mixed_watchlist_preserves_metadata_and_order_without_legacy_reads(monkeypatch):
    entries = [
        {"symbol": "600519.SH", "name": "舊自選名稱", "note": "keep", "group_ids": ["a"], "close": 9999.0},
        {"symbol": "2330.TWSE", "note": "Taiwan"},
        {"symbol": "000001", "added_at": "2020-01-01"},
    ]
    monkeypatch.setattr(api.watchlist, "list_symbols", lambda: entries)
    source = {"source": "twse:mis", "trade_date": "2026-11-03", "status": "stale"}

    def enrich(symbols):
        assert symbols == ["2330.TWSE"]
        return [{"symbol": "2330.TWSE", "close": 100.0, "source_meta": source}]

    monkeypatch.setattr("app.taiwan.watchlist_enrichment.enrich_taiwan_watchlist_rows", enrich)
    result = api.watchlist_enriched(_request(), None)
    old, taiwan, bare = result["rows"]
    assert [row["symbol"] for row in result["rows"]] == [row["symbol"] for row in entries]
    assert old["name"] == "舊自選名稱"
    assert old["note"] == "keep" and old["group_ids"] == ["a"]
    assert bare["added_at"] == "2020-01-01"
    for row in (old, bare):
        assert row["close"] is None
        assert row["status"] == "unavailable"
        assert row["unavailable_reason"] == "unsupported_market"
        assert row["market"] == "unsupported"
        assert row["source_meta"] is None
    assert taiwan["close"] == 100.0 and taiwan["source_meta"] == source
    assert taiwan["note"] == "Taiwan"
    assert result["as_of"] is None


def test_only_legacy_watchlist_does_not_invoke_enrichment(monkeypatch):
    monkeypatch.setattr(api.watchlist, "list_symbols", lambda: [{"symbol": "600519.SH"}])

    def fail(symbols):
        raise AssertionError("legacy symbols must never reach Taiwan provider")

    monkeypatch.setattr("app.taiwan.watchlist_enrichment.enrich_taiwan_watchlist_rows", fail)
    rows = api.watchlist_enriched(_request(), None)["rows"]
    assert rows[0]["unavailable_reason"] == "unsupported_market"


def test_list_preserves_legacy_names_without_master_or_repo_lookup(monkeypatch):
    entries = [{"symbol": "600519.SH", "name": "保留名稱", "note": "keep"}]
    monkeypatch.setattr(api.watchlist, "list_symbols", lambda: entries)

    def fail():
        raise AssertionError("legacy names must not trigger security master")

    monkeypatch.setattr("app.taiwan.universe.get_security_master", fail)
    assert api.list_all(_request()) == {"symbols": entries}


@pytest.mark.parametrize("path,body", [
    ("/api/watchlist", {"symbol": "600519.SH"}),
    ("/api/watchlist/batch", {"symbols": ["2330.TWSE", "000001.SZ"]}),
])
def test_new_legacy_entries_rejected_before_persistence(monkeypatch, path, body):
    def fail(*args, **kwargs):
        raise AssertionError("must reject before writing watchlist")

    monkeypatch.setattr(api.watchlist, "add", fail)
    monkeypatch.setattr(api.watchlist, "add_batch", fail)
    app = FastAPI()
    app.include_router(api.router)
    assert TestClient(app).post(path, json=body).status_code == 422

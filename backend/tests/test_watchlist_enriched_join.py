"""Every Taiwan watchlist entry remains visible even if enrichment is missing."""
from types import SimpleNamespace

import pytest

from app.api import watchlist as api


def _request():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))


@pytest.mark.parametrize("coverage", [[], ["2330.TWSE"], ["0050.TWSE"], ["2330.TWSE", "0050.TWSE"]])
def test_missing_stock_and_etf_rows_are_retained(monkeypatch, coverage):
    entries = [{"symbol": "2330.TWSE"}, {"symbol": "0050.TWSE"}]
    monkeypatch.setattr(api.watchlist, "list_symbols", lambda: entries)
    monkeypatch.setattr("app.taiwan.watchlist_enrichment.enrich_taiwan_watchlist_rows",
                        lambda symbols: [{"symbol": s, "close": 10.0} for s in coverage])
    rows = api.watchlist_enriched(_request(), None)["rows"]
    assert [r["symbol"] for r in rows] == [e["symbol"] for e in entries]
    for row in rows:
        if row["symbol"] in coverage:
            assert row["close"] == 10.0
        else:
            assert row["close"] is None
            assert row["rsi_14"] is None
            assert row["status"] == "unavailable"


def test_provider_exception_keeps_metadata_and_null_prices(monkeypatch):
    monkeypatch.setattr(api.watchlist, "list_symbols",
                        lambda: [{"symbol": "2330.TWSE", "note": "keep", "group_ids": ["a"]}])

    def fail(symbols):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr("app.taiwan.watchlist_enrichment.enrich_taiwan_watchlist_rows", fail)
    row = api.watchlist_enriched(_request(), None)["rows"][0]
    assert row["note"] == "keep" and row["group_ids"] == ["a"]
    assert row["close"] is None and row["source_meta"] is None
    assert row["unavailable_reason"] == "taiwan_data_unavailable"


def test_empty_watchlist_does_not_query_provider(monkeypatch):
    monkeypatch.setattr(api.watchlist, "list_symbols", lambda: [])

    def fail(symbols):
        raise AssertionError("empty watchlist should not query market data")

    monkeypatch.setattr("app.taiwan.watchlist_enrichment.enrich_taiwan_watchlist_rows", fail)
    result = api.watchlist_enriched(_request(), None)
    assert result["rows"] == [] and result["as_of"] is None

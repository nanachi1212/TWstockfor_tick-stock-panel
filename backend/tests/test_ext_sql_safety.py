"""ext_columns 已退役: 傳入惡意參數也不會觸發任何 SQL。"""
from __future__ import annotations


class _NoLegacySQL:
    def __getattr__(self, name):
        raise AssertionError(f"Retired extension path accessed repository: {name}")


def test_watchlist_retired_ext_parameter_never_runs_sql(monkeypatch):
    from types import SimpleNamespace

    from app.api import watchlist

    monkeypatch.setattr(watchlist.watchlist, "list_symbols", lambda: [])
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=_NoLegacySQL())))
    result = watchlist.watchlist_enriched(request, ext_columns="evil'; DROP--.field")
    assert result["rows"] == []


def test_kline_retired_ext_parameter_never_runs_sql():
    from app.api.kline import _attach_ext

    response = {"symbol": "2330.TWSE", "rows": [{"close": 100.0}]}
    result = _attach_ext(response, _NoLegacySQL(), "2330.TWSE", "evil'; DROP--.field")
    assert result is response
    assert result["rows"] == [{"close": 100.0}]

"""Retired quote requests must not fetch data or rewrite saved watchlist rows."""
from __future__ import annotations

import polars as pl
import pytest

from app.services import watchlist


@pytest.mark.parametrize("symbols", [["600000.SH"], ["2330.TWSE"], ["2330.TWSE", "600000.SH"]])
def test_retired_quote_fetch_rejects_without_changing_saved_rows(monkeypatch, tmp_path, symbols):
    monkeypatch.setattr(watchlist.settings, "data_dir", tmp_path)
    saved = tmp_path / "user_data" / "watchlist.parquet"
    saved.parent.mkdir()
    pl.DataFrame({
        "symbol": ["600000.SH", "2330.TWSE"],
        "added_at": ["2026-11-03", "2026-11-04"],
        "note": ["legacy note", "Taiwan note"],
        "group_ids": [["old-group"], []],
    }).write_parquet(saved)
    before = saved.read_bytes()

    with pytest.raises(ValueError, match="舊版行情介面已移除"):
        watchlist.fetch_quotes(symbols, None)

    assert saved.read_bytes() == before
    assert [row["symbol"] for row in watchlist.list_symbols()] == ["600000.SH", "2330.TWSE"]


def test_empty_retired_quote_request_remains_empty():
    assert watchlist.fetch_quotes([], None) == []

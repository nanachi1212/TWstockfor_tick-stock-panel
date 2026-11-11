"""get_enriched_range 的預熱守衛測試 — 預熱期間不得觸發同步全量重算。

啟動後台預熱執行緒正在 _refresh_enriched (300 天 scan + compute, 低配機 50s+)
時, 請求執行緒進入 get_enriched_range 應返回 None (快取不覆蓋語義),
不能在請求執行緒裡併發跑第二次全量重算。與 get_enriched_latest 守衛對齊。
"""
from __future__ import annotations

from datetime import date

import polars as pl

from app.tickflow.repository import KlineRepository


def _bare_repo() -> KlineRepository:
    """跳過 __init__ (避免 DataStore/目錄依賴), 只裝配守衛涉及的屬性。"""
    repo = KlineRepository.__new__(KlineRepository)
    repo._enriched_history_cache = None
    repo._enriched_warming = True
    return repo


def test_get_enriched_range_returns_none_while_warming():
    repo = _bare_repo()
    refresh_calls: list[int] = []

    def _spy_refresh():
        refresh_calls.append(1)

    repo._refresh_enriched = _spy_refresh  # type: ignore[method-assign]

    result = repo.get_enriched_range(date(2026, 1, 1), date(2026, 8, 14))

    assert result is None
    assert refresh_calls == [], "預熱期間不得觸發 _refresh_enriched"


def test_get_enriched_range_rebuilds_when_cold_and_not_warming():
    repo = _bare_repo()
    repo._enriched_warming = False
    built = pl.DataFrame({
        "symbol": ["600000.SH", "600000.SH"],
        "date": [date(2026, 1, 1), date(2026, 8, 14)],
    })

    def _fake_refresh():
        repo._enriched_history_cache = built

    repo._refresh_enriched = _fake_refresh  # type: ignore[method-assign]

    result = repo.get_enriched_range(date(2026, 1, 1), date(2026, 8, 14))

    assert result is not None
    assert result.height == 2
    assert result["symbol"].unique().to_list() == ["600000.SH"]

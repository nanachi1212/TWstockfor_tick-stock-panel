# ruff: noqa: RUF002, RUF003 -- user-facing Traditional Chinese text is intentional.
from datetime import date
from unittest.mock import MagicMock

import polars as pl

from app.taiwan import startup_catchup
from app.taiwan.shareholding_store import (
    TaiwanShareholdingStore,
    catchup_shareholding,
    needs_shareholding_catchup,
)

CSV = """﻿資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%
{d},2330  ,14,229,204873701,0.79
{d},2330  ,15,1482,{big},84.78
{d},2330  ,17,3007088,25932370067,100.00
"""
MAP = {"2330": "2330.TWSE"}


def test_catchup_skips_when_week_is_already_current():
    """Case a: 已有最新週 (距今 < 7 天)，斷言抓取函式沒有被呼叫，write 也沒有被呼叫。"""
    mock_store = MagicMock()
    mock_store.latest_week.return_value = date(2026, 10, 9)
    mock_fetch = MagicMock()

    # as_of 為 3 天後 (2026-10-12)，未滿 7 天
    result = catchup_shareholding(
        store=mock_store,
        fetch_text=mock_fetch,
        code_to_symbol=MAP,
        as_of=date(2026, 10, 12),
    )

    mock_fetch.assert_not_called()
    mock_store.write.assert_not_called()
    assert result["status"] == "skipped"


def test_catchup_writes_when_latest_week_is_missing_empty_store():
    """Case b (空庫): 庫是空的，斷言抓取函式被呼叫，write 被呼叫一次並帶入抓到的資料。"""
    mock_store = MagicMock()
    mock_store.latest_week.return_value = None
    mock_store.read.return_value = pl.DataFrame()
    mock_store.write.return_value = 1
    mock_fetch = MagicMock(return_value=CSV.format(d="20261008", big=21987181886))

    result = catchup_shareholding(
        store=mock_store,
        fetch_text=mock_fetch,
        code_to_symbol=MAP,
        as_of=date(2026, 10, 15),
    )

    mock_fetch.assert_called_once()
    mock_store.write.assert_called_once()
    written_frame = mock_store.write.call_args[0][0]
    assert isinstance(written_frame, pl.DataFrame)
    assert not written_frame.is_empty()
    assert written_frame["date"][0] == date(2026, 10, 8)
    assert written_frame["symbol"][0] == "2330.TWSE"
    assert result["status"] == "written"
    assert result["latest"] == "2026-10-08"


def test_catchup_writes_when_latest_week_is_missing_stale_store():
    """Case b (舊庫): 已入庫週別已逾 7 天，抓到新週別後斷言 write 被呼叫一次並帶入抓到的資料。"""
    mock_store = MagicMock()
    mock_store.latest_week.return_value = date(2026, 10, 2)
    mock_store.write.return_value = 1
    mock_fetch = MagicMock(return_value=CSV.format(d="20261009", big=21987181886))

    # as_of 為 13 天後 (2026-10-15)，>= 7 天，應執行抓取
    result = catchup_shareholding(
        store=mock_store,
        fetch_text=mock_fetch,
        code_to_symbol=MAP,
        as_of=date(2026, 10, 15),
    )

    mock_fetch.assert_called_once()
    mock_store.write.assert_called_once()
    written_frame = mock_store.write.call_args[0][0]
    assert written_frame["date"][0] == date(2026, 10, 9)
    assert result["status"] == "written"
    assert result["latest"] == "2026-10-09"


def test_catchup_handles_fetch_exception_gracefully():
    """Case c: 抓取拋出例外，斷言補抓函式沒有拋錯，write 沒有被呼叫。"""
    mock_store = MagicMock()
    mock_store.latest_week.return_value = None
    mock_store.read.return_value = pl.DataFrame()
    mock_fetch = MagicMock(side_effect=RuntimeError("TDCC open data network timeout"))

    result = catchup_shareholding(
        store=mock_store,
        fetch_text=mock_fetch,
        code_to_symbol=MAP,
        as_of=date(2026, 10, 15),
    )

    mock_fetch.assert_called_once()
    mock_store.write.assert_not_called()
    assert result["status"] == "error"
    assert result["reason"] == "fetch_failed"


def test_catchup_skips_write_when_fetched_week_is_not_newer():
    """抓取成功但抓到的週別與庫內相同 (未更新)，斷言不呼叫 write。"""
    mock_store = MagicMock()
    mock_store.latest_week.return_value = date(2026, 10, 2)
    mock_fetch = MagicMock(return_value=CSV.format(d="20261002", big=21987181886))

    result = catchup_shareholding(
        store=mock_store,
        fetch_text=mock_fetch,
        code_to_symbol=MAP,
        as_of=date(2026, 10, 10),
    )

    mock_fetch.assert_called_once()
    mock_store.write.assert_not_called()
    assert result["status"] == "skipped"
    assert "already_stored" in result["reason"]


def test_catchup_handles_empty_or_malformed_fetch():
    """抓取回傳空字串或無效內容，不拋錯且不呼叫 write。"""
    mock_store = MagicMock()
    mock_store.latest_week.return_value = None
    mock_store.read.return_value = pl.DataFrame()
    mock_fetch = MagicMock(return_value="")

    result = catchup_shareholding(
        store=mock_store,
        fetch_text=mock_fetch,
        code_to_symbol=MAP,
        as_of=date(2026, 10, 15),
    )

    mock_fetch.assert_called_once()
    mock_store.write.assert_not_called()
    assert result["status"] == "skipped"
    assert result["reason"] == "empty_fetch"


def test_needs_shareholding_catchup_contract():
    """驗證補抓判斷規則契約。"""
    mock_store = MagicMock()

    # 庫為空 -> True
    mock_store.latest_week.return_value = None
    mock_store.read.return_value = pl.DataFrame()
    assert needs_shareholding_catchup(mock_store, as_of=date(2026, 10, 15))[0] is True

    # 距今 6 天 -> False
    mock_store.latest_week.return_value = date(2026, 10, 9)
    assert needs_shareholding_catchup(mock_store, as_of=date(2026, 10, 15))[0] is False

    # 距今 7 天 -> True
    mock_store.latest_week.return_value = date(2026, 10, 8)
    assert needs_shareholding_catchup(mock_store, as_of=date(2026, 10, 15))[0] is True


def test_startup_catchup_run_integrates_shareholding(monkeypatch):
    """驗證 startup_catchup._run 整合集保補抓流程 (即使日常日線補抓 skipped 亦執行)。"""
    monkeypatch.setattr(startup_catchup, "needs_catchup", lambda: (False, "current"))
    shareholding_called = []

    def fake_catchup():
        shareholding_called.append(True)
        return {"status": "written", "latest": "2026-10-09"}

    monkeypatch.setattr(startup_catchup, "catchup_shareholding", fake_catchup)

    startup_catchup._LAST.clear()
    startup_catchup._run(MagicMock())

    assert len(shareholding_called) == 1
    assert startup_catchup.last_result()["status"] == "skipped"
    assert startup_catchup.last_result()["shareholding"] == {"status": "written", "latest": "2026-10-09"}


def test_real_store_end_to_end(tmp_path):
    """使用真實 TaiwanShareholdingStore 驗證完整的寫入與冪等跳過行為。"""
    store = TaiwanShareholdingStore(tmp_path / "shareholding.parquet")
    assert store.latest_week() is None

    # 第一週寫入
    res1 = catchup_shareholding(
        store=store,
        fetch_text=lambda _: CSV.format(d="20261002", big=20_000_000_000),
        code_to_symbol=MAP,
        as_of=date(2026, 10, 5),
    )
    assert res1["status"] == "written"
    assert store.latest_week() == date(2026, 10, 2)

    # 3 天後 (未滿 7 天)，不抓取
    mock_fetch = MagicMock()
    res2 = catchup_shareholding(
        store=store,
        fetch_text=mock_fetch,
        code_to_symbol=MAP,
        as_of=date(2026, 10, 6),
    )
    mock_fetch.assert_not_called()
    assert res2["status"] == "skipped"

    # 8 天後 (滿 7 天)，抓取新週別寫入
    res3 = catchup_shareholding(
        store=store,
        fetch_text=lambda _: CSV.format(d="20261009", big=21_000_000_000),
        code_to_symbol=MAP,
        as_of=date(2026, 10, 10),
    )
    assert res3["status"] == "written"
    assert store.latest_week() == date(2026, 10, 9)

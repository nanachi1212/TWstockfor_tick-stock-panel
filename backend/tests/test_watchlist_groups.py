"""自選分組持久化與 API 契約。"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import polars as pl
import pytest
from fastapi import HTTPException

from app.api import watchlist as watchlist_api
from app.config import settings
from app.services import watchlist


def _request():
    repo = MagicMock()
    repo.get_name_map.return_value = {}
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=repo)))


def test_historical_watchlist_is_read_as_ungrouped(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    path = tmp_path / "user_data" / "watchlist.parquet"
    path.parent.mkdir(parents=True)
    pl.DataFrame({
        "symbol": ["2330.TWSE"],
        "added_at": ["2026-08-08T10:00:00"],
        "note": [""],
    }).write_parquet(path)

    assert watchlist.list_symbols()[0]["group_ids"] == []


def test_group_lifecycle_preserves_watchlist_entries(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    groups, created = watchlist.create_group("  短線  ", "orange")
    assert groups == [{"id": created["id"], "name": "短線", "color": "orange"}]

    watchlist.add("2330.TWSE", group_id=created["id"])
    watchlist.add("2317.TWSE")
    assert watchlist.list_symbols()[1]["group_ids"] == [created["id"]]

    renamed = watchlist.rename_group(created["id"], "觀察", "fuchsia")
    assert renamed[0]["name"] == "觀察"
    assert renamed[0]["color"] == "fuchsia"

    remaining, rows = watchlist.delete_group(created["id"])
    assert remaining == []
    assert {row["symbol"] for row in rows} == {"2330.TWSE", "2317.TWSE"}
    assert all(row["group_ids"] == [] for row in rows)


def test_group_validation_and_assignment_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    _, created = watchlist.create_group("核心")
    watchlist.add("2330.TWSE")

    with pytest.raises(ValueError, match="已存在"):
        watchlist.create_group("核心")
    with pytest.raises(ValueError, match="顏色"):
        watchlist.create_group("無效顏色", "black")
    with pytest.raises(ValueError, match="顏色"):
        watchlist.rename_group(created["id"], "核心", "black")


def test_reorder_groups(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    _, first = watchlist.create_group("一")
    _, second = watchlist.create_group("二")
    _, third = watchlist.create_group("三")

    reordered = watchlist.reorder_groups([third["id"], first["id"], second["id"]])
    assert [group["name"] for group in reordered] == ["三", "一", "二"]
    assert [group["name"] for group in watchlist.list_groups()] == ["三", "一", "二"]

    # ids 與現有分組不一致 (缺失 / 多餘 / 重複) 均拒絕
    with pytest.raises(ValueError, match="不一致"):
        watchlist.reorder_groups([first["id"], second["id"]])
    with pytest.raises(ValueError, match="不一致"):
        watchlist.reorder_groups([first["id"], second["id"], third["id"], "missing"])
    with pytest.raises(ValueError, match="不一致"):
        watchlist.reorder_groups([first["id"], first["id"], second["id"], third["id"]])
    # 失敗請求不改變現有順序
    assert [group["name"] for group in watchlist.list_groups()] == ["三", "一", "二"]


def test_reorder_groups_api(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    _, first = watchlist.create_group("一")
    _, second = watchlist.create_group("二")

    result = watchlist_api.reorder_groups(
        watchlist_api.GroupReorderRequest(ordered_ids=[second["id"], first["id"]])
    )
    assert [group["name"] for group in result["groups"]] == ["二", "一"]

    with pytest.raises(HTTPException) as exc_info:
        watchlist_api.reorder_groups(
            watchlist_api.GroupReorderRequest(ordered_ids=["missing"])
        )
    assert exc_info.value.status_code == 400


def test_group_api_contract(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    request = _request()
    created = watchlist_api.create_group(
        watchlist_api.GroupNameRequest(name="中線", color="teal")
    )
    group_id = created["group"]["id"]
    assert created["group"]["color"] == "teal"

    added = watchlist_api.add_one(
        watchlist_api.AddRequest(symbol="2330.TWSE", group_id=group_id),
        request,
    )
    assert added["symbols"][0]["group_ids"] == [group_id]

    with pytest.raises(HTTPException) as exc_info:
        watchlist_api.rename_group("missing", watchlist_api.GroupNameRequest(name="無效"))
    assert exc_info.value.status_code == 404


def test_historical_groups_default_to_sky(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    path = tmp_path / "user_data" / "watchlist_groups.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        '[{"id":"legacy","name":"舊分組"},'
        '{"id":"invalid","name":"未知顏色","color":"black"}]',
        encoding="utf-8",
    )

    assert watchlist.list_groups() == [
        {"id": "legacy", "name": "舊分組", "color": "sky"},
        {"id": "invalid", "name": "未知顏色", "color": "sky"},
    ]


def test_clear_group_moves_members_to_ungrouped(monkeypatch, tmp_path):
    """清空分組:成員變未分組,分組定義保留。"""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    _, group = watchlist.create_group("芯片")
    watchlist.add("2330.TWSE", group_id=group["id"])
    watchlist.add("2317.TWSE", group_id=group["id"])
    watchlist.add("2454.TWSE")  # 不在任何分組

    rows = watchlist.clear_group(group["id"])
    # 3 只都還在,group_id 全部為 None
    assert len(rows) == 3
    assert all(r["group_ids"] == [] for r in rows)
    # 分組定義仍在
    assert any(g["id"] == group["id"] for g in watchlist.list_groups())

    # 清空不存在的分組 → KeyError
    with pytest.raises(KeyError):
        watchlist.clear_group("missing")


def test_clear_group_api(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    request = _request()
    created = watchlist_api.create_group(
        watchlist_api.GroupNameRequest(name="中線", color="teal")
    )
    group_id = created["group"]["id"]
    watchlist.add("2330.TWSE", group_id=group_id)
    watchlist.add("2317.TWSE", group_id=group_id)

    result = watchlist_api.clear_group(group_id, request)
    assert all(s["group_ids"] == [] for s in result["symbols"])

    # 不存在的分組 → 404
    with pytest.raises(HTTPException) as exc:
        watchlist_api.clear_group("missing", request)
    assert exc.value.status_code == 404


# ── 多組成員關係 (M:N) ──────────────────────────────────────


def test_multi_group_membership(monkeypatch, tmp_path):
    """一股可同時屬於多個分組; 移出一個不影響其他。"""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    _, ga = watchlist.create_group("半導體")
    _, gb = watchlist.create_group("反包備選")
    watchlist.add("3008.TWSE", group_id=ga["id"])

    rows = watchlist.add_to_group("3008.TWSE", gb["id"])
    got = next(r for r in rows if r["symbol"] == "3008.TWSE")
    assert got["group_ids"] == [ga["id"], gb["id"]]      # 兩組並存

    # 重複加入冪等
    rows = watchlist.add_to_group("3008.TWSE", gb["id"])
    got = next(r for r in rows if r["symbol"] == "3008.TWSE")
    assert got["group_ids"] == [ga["id"], gb["id"]]

    # 移出一個, 另一個保留
    rows = watchlist.remove_from_group("3008.TWSE", ga["id"])
    got = next(r for r in rows if r["symbol"] == "3008.TWSE")
    assert got["group_ids"] == [gb["id"]]

    # 移出最後一個 → 未分組(仍在自選)
    rows = watchlist.remove_from_group("3008.TWSE", gb["id"])
    got = next(r for r in rows if r["symbol"] == "3008.TWSE")
    assert got["group_ids"] == []
    assert any(r["symbol"] == "3008.TWSE" for r in rows)

    # 不存在的分組/標的
    with pytest.raises(ValueError, match="不存在"):
        watchlist.add_to_group("3008.TWSE", "missing")
    with pytest.raises(KeyError):
        watchlist.remove_from_group("2317.TWSE", ga["id"])


def test_clear_group_strips_only_that_group(monkeypatch, tmp_path):
    """清空分組只摘該組標籤, 其他組成員關係保留。"""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    _, ga = watchlist.create_group("一")
    _, gb = watchlist.create_group("二")
    watchlist.add("2330.TWSE", group_id=ga["id"])
    watchlist.add_to_group("2330.TWSE", gb["id"])
    watchlist.add("2317.TWSE", group_id=ga["id"])

    rows = watchlist.clear_group(ga["id"])
    a = next(r for r in rows if r["symbol"] == "2330.TWSE")
    b = next(r for r in rows if r["symbol"] == "2317.TWSE")
    assert a["group_ids"] == [gb["id"]]   # 二組保留
    assert b["group_ids"] == []


def test_legacy_single_group_id_migration(monkeypatch, tmp_path):
    """舊 schema(單值 group_id 列)讀取遷移 + 首次寫回前自動備份 .bak。"""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    path = tmp_path / "user_data" / "watchlist.parquet"
    path.parent.mkdir(parents=True)
    pl.DataFrame({
        "symbol": ["2330.TWSE", "2317.TWSE"],
        "added_at": ["2026-08-08T10:00:00"] * 2,
        "note": ["", ""],
        "group_id": ["g1", None],
    }).write_parquet(path)

    rows = watchlist.list_symbols()
    assert rows[0]["group_ids"] == ["g1"]
    assert rows[1]["group_ids"] == []
    assert not (tmp_path / "user_data" / "watchlist.parquet.bak").exists()  # 只讀不備份

    # 觸發寫入 → 備份生成, 檔案落新 schema
    watchlist.add("2454.TWSE")
    assert (tmp_path / "user_data" / "watchlist.parquet.bak").exists()
    df = pl.read_parquet(path)
    assert "group_ids" in df.columns and "group_id" not in df.columns


def test_added_at_is_a_timezone_aware_utc_instant(monkeypatch, tmp_path):
    from datetime import datetime, timedelta

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    added_at = watchlist.add("2330.TWSE")[0]["added_at"]
    # A naive string would be read as local time by any consumer; the offset must be explicit.
    assert datetime.fromisoformat(added_at).utcoffset() == timedelta(0)

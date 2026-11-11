"""自選股與分組服務。

自選存儲於 ``data/user_data/watchlist.parquet``，分組定義存儲於同目錄的
``watchlist_groups.json``。

成員關係為多值 (M:N): 每條自選帶 ``group_ids: list[str]``, 同一標的可同時
屬於多個分組; 移出分組只摘標籤(標的仍在自選), 移出自選才刪除實體。
舊 schema (單值 ``group_id`` 列) 讀取時自動遷移為 ``[group_id]``, 首次寫回
新 schema 前留一份 ``watchlist.parquet.bak`` 備份。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path

import polars as pl

from app.config import settings
from app.tickflow.capabilities import CapabilitySet

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()
# 資料版本號: 每次寫盤 +1 (在 _LOCK 內遞增, 讀取免鎖)。供監控引擎等進程內
# 消費方做快取失效判斷 —— 版本沒變就不必重讀文件, 版本一變立即拿到新成員。
_REVISION = 0


def revision() -> int:
    """自選/分組資料版本號, 每次寫操作遞增。"""
    return _REVISION


def bump_revision() -> None:
    """自選檔被整個換掉 (還原備份) 時呼叫, 讓依版本號快取的消費方重讀。"""
    global _REVISION
    with _LOCK:
        _REVISION += 1


_MAX_GROUP_NAME_LENGTH = 24
DEFAULT_GROUP_COLOR = "sky"
GROUP_COLORS = frozenset({
    "sky",
    "blue",
    "indigo",
    "violet",
    "fuchsia",
    "rose",
    "orange",
    "amber",
    "lime",
    "emerald",
    "teal",
    "cyan",
})
_ENTRY_SCHEMA = {
    "symbol": pl.Utf8,
    "added_at": pl.Utf8,
    "note": pl.Utf8,
    "group_ids": pl.List(pl.Utf8),
}


def _path() -> Path:
    p = settings.data_dir / "user_data" / "watchlist.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _groups_path() -> Path:
    p = settings.data_dir / "user_data" / "watchlist_groups.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _empty_entries() -> pl.DataFrame:
    return pl.DataFrame(schema=_ENTRY_SCHEMA)


def _read_entries() -> pl.DataFrame:
    p = _path()
    if not p.exists():
        return _empty_entries()
    df = pl.read_parquet(p)
    # 舊 schema 相容: 單值 group_id → group_ids=[gid]; 兩列都缺 → 空列表
    if "group_ids" not in df.columns:
        old = df["group_id"].to_list() if "group_id" in df.columns else [None] * df.height
        df = df.with_columns(
            pl.Series("group_ids", [[g] if g else [] for g in old], dtype=pl.List(pl.Utf8))
        ).drop("group_id", strict=False)
    if "symbol" not in df.columns:
        df = df.with_columns(pl.lit("", dtype=pl.Utf8).alias("symbol"))
    if "added_at" not in df.columns:
        df = df.with_columns(pl.lit("", dtype=pl.Utf8).alias("added_at"))
    if "note" not in df.columns:
        df = df.with_columns(pl.lit(None, dtype=pl.Utf8).alias("note"))
    return df.select(list(_ENTRY_SCHEMA))


def _write_entries(df: pl.DataFrame) -> None:
    global _REVISION
    p = _path()
    # 首次從舊 schema 遷移到 group_ids 前, 備份原文件(一次性)
    if p.exists():
        try:
            if "group_ids" not in pl.read_parquet_schema(p).names():
                shutil.copy(p, p.with_suffix(p.suffix + ".bak"))
        except OSError as e:
            logger.warning("watchlist backup before migration failed: %s", e)
    tmp = p.with_suffix(p.suffix + ".tmp")
    df.select(list(_ENTRY_SCHEMA)).write_parquet(tmp)
    os.replace(tmp, p)
    _REVISION += 1


def _read_groups() -> list[dict]:
    p = _groups_path()
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("自選分組配置損壞，請檢查 watchlist_groups.json") from exc
    if not isinstance(raw, list):
        raise ValueError("自選分組配置格式不正確")
    groups = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("id") or not item.get("name"):
            continue
        color = str(item.get("color", DEFAULT_GROUP_COLOR))
        groups.append({
            "id": str(item["id"]),
            "name": str(item["name"]),
            "color": color if color in GROUP_COLORS else DEFAULT_GROUP_COLOR,
        })
    return groups


def _write_groups(groups: list[dict]) -> None:
    global _REVISION
    p = _groups_path()
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(groups, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    _REVISION += 1


def _normalize_group_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("分組名稱不能為空")
    if len(normalized) > _MAX_GROUP_NAME_LENGTH:
        raise ValueError(f"分組名稱不能超過 {_MAX_GROUP_NAME_LENGTH} 個字元")
    return normalized


def _normalize_group_color(color: str | None) -> str:
    normalized = (color or DEFAULT_GROUP_COLOR).strip().lower()
    if normalized not in GROUP_COLORS:
        raise ValueError("不支持的分組顏色")
    return normalized


def _validate_group_id(group_id: str | None, groups: list[dict]) -> None:
    if group_id is not None and not any(group["id"] == group_id for group in groups):
        raise ValueError("自選分組不存在")


def list_symbols() -> list[dict]:
    with _LOCK:
        df = _read_entries()
        return [] if df.is_empty() else df.to_dicts()


def add(symbol: str, note: str = "", group_id: str | None = None) -> list[dict]:
    rows, _ = add_batch([symbol], note=note, group_id=group_id)
    return rows


def add_batch(
    symbols: list[str],
    note: str = "",
    group_id: str | None = None,
) -> tuple[list[dict], int]:
    """批量添加並保持既有語義：每個新處理的標的移動到列表最前面。

    group_id 為可選的初始分組(如從某分組頁添加時); 重複添加的標的保留
    既有全部分組, 僅在顯式傳入 group_id 且尚未屬於該組時併入。
    """
    with _LOCK:
        groups = _read_groups()
        _validate_group_id(group_id, groups)
        rows = _read_entries().to_dicts()
        added = 0
        for symbol in symbols:
            existing = next((row for row in rows if row["symbol"] == symbol), None)
            if existing is None:
                added += 1
            rows = [row for row in rows if row["symbol"] != symbol]
            gids = list((existing or {}).get("group_ids") or [])
            if group_id is not None and group_id not in gids:
                gids.append(group_id)
            rows.insert(0, {
                "symbol": symbol,
                "added_at": datetime.utcnow().isoformat(timespec="seconds"),
                "note": note,
                "group_ids": gids,
            })
        out = pl.DataFrame(rows, schema=_ENTRY_SCHEMA) if rows else _empty_entries()
        _write_entries(out)
        return out.to_dicts(), added


def remove(symbol: str) -> list[dict]:
    with _LOCK:
        df = _read_entries().filter(pl.col("symbol") != symbol)
        _write_entries(df)
        return df.to_dicts()


def move_to_top(symbol: str) -> list[dict]:
    with _LOCK:
        df = _read_entries()
        if df.is_empty() or symbol not in df["symbol"].to_list():
            return df.to_dicts()
        target = df.filter(pl.col("symbol") == symbol)
        rest = df.filter(pl.col("symbol") != symbol)
        out = pl.concat([target, rest], how="diagonal_relaxed")
        _write_entries(out)
        return out.to_dicts()


def clear() -> int:
    """清空自選列表。返回移除的數量。"""
    with _LOCK:
        df = _read_entries()
        count = df.height
        if count > 0:
            _write_entries(_empty_entries())
        return count


def list_groups() -> list[dict]:
    with _LOCK:
        return _read_groups()


def create_group(name: str, color: str | None = None) -> tuple[list[dict], dict]:
    with _LOCK:
        normalized = _normalize_group_name(name)
        normalized_color = _normalize_group_color(color)
        groups = _read_groups()
        if any(group["name"].casefold() == normalized.casefold() for group in groups):
            raise ValueError("分組名稱已存在")
        group = {
            "id": uuid.uuid4().hex,
            "name": normalized,
            "color": normalized_color,
        }
        groups.append(group)
        _write_groups(groups)
        return groups, group


def rename_group(group_id: str, name: str, color: str | None = None) -> list[dict]:
    with _LOCK:
        normalized = _normalize_group_name(name)
        groups = _read_groups()
        target = next((group for group in groups if group["id"] == group_id), None)
        if target is None:
            raise KeyError(group_id)
        if any(
            group["id"] != group_id and group["name"].casefold() == normalized.casefold()
            for group in groups
        ):
            raise ValueError("分組名稱已存在")
        target["name"] = normalized
        if color is not None:
            target["color"] = _normalize_group_color(color)
        _write_groups(groups)
        return groups


def reorder_groups(ordered_ids: list[str]) -> list[dict]:
    """按給定 id 順序重排分組 (json 陣列順序即定義順序)。"""
    with _LOCK:
        groups = _read_groups()
        by_id = {group["id"]: group for group in groups}
        if len(ordered_ids) != len(groups) or set(ordered_ids) != set(by_id):
            raise ValueError("分組順序與現有分組不一致")
        reordered = [by_id[group_id] for group_id in ordered_ids]
        _write_groups(reordered)
        return reordered


def delete_group(group_id: str) -> tuple[list[dict], list[dict]]:
    """刪除分組定義，原分組內的自選保留並轉為未分組(僅摘掉該組標籤)。"""
    with _LOCK:
        groups = _read_groups()
        if not any(group["id"] == group_id for group in groups):
            raise KeyError(group_id)
        df = _strip_group(_read_entries(), group_id)
        remaining = [group for group in groups if group["id"] != group_id]
        _write_entries(df)
        _write_groups(remaining)
        return remaining, df.to_dicts()


def set_group(symbol: str, group_id: str | None) -> list[dict]:
    """互斥設定: 該標的只保留這一個分組(group_id=None 即全部移出, 變未分組)。

    多組模型的日常操作走 add_to_group / remove_from_group; 本函式服務於
    「僅保留此組」的顯式場景。
    """
    with _LOCK:
        groups = _read_groups()
        _validate_group_id(group_id, groups)
        rows = _read_entries().to_dicts()
        if not any(row["symbol"] == symbol for row in rows):
            raise KeyError(symbol)
        for row in rows:
            if row["symbol"] == symbol:
                row["group_ids"] = [group_id] if group_id is not None else []
        out = pl.DataFrame(rows, schema=_ENTRY_SCHEMA)
        _write_entries(out)
        return out.to_dicts()


def add_to_group(symbol: str, group_id: str) -> list[dict]:
    """把標的加入一個分組(多組成員關係: 不影響已屬於的其他分組)。"""
    with _LOCK:
        groups = _read_groups()
        _validate_group_id(group_id, groups)
        rows = _read_entries().to_dicts()
        if not any(row["symbol"] == symbol for row in rows):
            raise KeyError(symbol)
        for row in rows:
            if row["symbol"] == symbol:
                gids = row["group_ids"] or []
                if group_id not in gids:
                    gids.append(group_id)
                    row["group_ids"] = gids
        out = pl.DataFrame(rows, schema=_ENTRY_SCHEMA)
        _write_entries(out)
        return out.to_dicts()


def remove_from_group(symbol: str, group_id: str) -> list[dict]:
    """把標的移出一個分組(僅摘本組標籤; 標的仍在自選, 可能落入未分組)。"""
    with _LOCK:
        groups = _read_groups()
        _validate_group_id(group_id, groups)
        rows = _read_entries().to_dicts()
        if not any(row["symbol"] == symbol for row in rows):
            raise KeyError(symbol)
        for row in rows:
            if row["symbol"] == symbol:
                row["group_ids"] = [g for g in (row["group_ids"] or []) if g != group_id]
        out = pl.DataFrame(rows, schema=_ENTRY_SCHEMA)
        _write_entries(out)
        return out.to_dicts()


def _strip_group(df: pl.DataFrame, group_id: str) -> pl.DataFrame:
    """從所有條目的 group_ids 中摘掉指定分組(刪除分組/清空分組共用)。"""
    rows = df.to_dicts()
    for row in rows:
        gids = row.get("group_ids") or []
        if group_id in gids:
            row["group_ids"] = [g for g in gids if g != group_id]
    return pl.DataFrame(rows, schema=_ENTRY_SCHEMA) if rows else _empty_entries()


def clear_group(group_id: str) -> list[dict]:
    """清空分組成員:把該分組標籤從所有條目摘掉(變未分組),保留分組定義。"""
    with _LOCK:
        groups = _read_groups()
        if not any(group["id"] == group_id for group in groups):
            raise KeyError(group_id)
        df = _strip_group(_read_entries(), group_id)
        _write_entries(df)
        return df.to_dicts()


def fetch_quotes(symbols: list[str], capset: CapabilitySet, timeout_s: float = 8.0) -> list[dict]:
    """Reject the retired quote endpoint; Taiwan callers use watchlist_enrichment."""
    if not symbols:
        return []
    raise ValueError("舊版行情介面已移除, 請使用台股行情服務")

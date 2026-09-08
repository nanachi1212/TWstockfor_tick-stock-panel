"""扩展数据 API — 唯读 schema 发现 + 维度成员查询(供 Watchlist/Monitor/Screener
的自定义列与钻取弹窗使用)。

Phase 8B-5.16: 移除已确认 zero 前端/后端/scheduler/脚本消费者的 CRUD + 文件
上传 + JSON 写入 + 定时拉取管理 HTTP 端点(create/update/delete/upload/ingest/
pull-config/pull-test/pull-run/fix-symbol/detect-fields/detect-url/list/
schema/{id}/presets-fetch) —— 这些原本服务已删除的 Data.tsx / Analysis.tsx /
ExtPages.tsx 管理 UI(Phase 8B-5.8、8B-5.12)。保留 schema-all 与
dimension-members —— 仍是 ListColumnCustomizer / DimensionMembersDialog /
Monitor / Watchlist / Dashboard / Screener 的真实消费者。_read_ext_dataframe
仍被 watchlist.py / screener.py 直接 import 使用, 完整保留。ExtConfigStore /
PullScheduler / ext_presets.py 均未变更, 见 backend/app/services/ext_data.py、
ext_pull.py、ext_presets.py。

FOLLOW_UP: _refresh_views() 因上述 handlers 一并移除而暂无调用方 —— 保留未删,
因为 watchlist.py/screener.py 的 ext-column JOIN 在 config 未注册的情况下仍有
一条走 DuckDB view (ext_{config_id}) 的 fallback 分支, 而该 view 目前只有此函数
会建立; 是否可安全一并清理留待下一轮更细的 view-lifecycle 稽核确认。
"""
from __future__ import annotations

import json
import logging
import math
import re
from datetime import date, datetime
from pathlib import Path

import polars as pl
from fastapi import APIRouter, HTTPException, Query, Request

from app.services.ext_data import ExtConfig, ExtConfigStore

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ext-data", tags=["ext-data"])


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _store(request: Request) -> ExtConfigStore:
    return ExtConfigStore(request.app.state.repo.store.data_dir)


def _data_dir(request: Request) -> Path:
    return request.app.state.repo.store.data_dir


_DIMENSION_SEPARATOR_CLASS = r"、,，;；|/\s-"


def _filter_dimension_member_rows(df: pl.DataFrame, field: str, value: str) -> pl.DataFrame:
    """按分隔后的完整标签匹配成员，避免“人工智能”误命中“人工智能体”。"""
    if field not in df.columns:
        raise HTTPException(400, f"字段 '{field}' 不存在")
    normalized = value.strip()
    if not normalized:
        raise HTTPException(400, "标签值不能为空")
    pattern = rf"(^|[{_DIMENSION_SEPARATOR_CLASS}]){re.escape(normalized)}($|[{_DIMENSION_SEPARATOR_CLASS}])"
    return df.filter(
        pl.col(field)
        .cast(pl.String, strict=False)
        .fill_null("")
        .str.contains(pattern)
    )


def _ext_data_dir(config: ExtConfig, data_dir: Path) -> Path:
    """返回扩展数据的数据目录。

    - snapshot: data/ext_data/{id}/（part.parquet 与 config.json 同级）
    - timeseries: data/ext_data/{id}/timeseries/
    """
    cfg_dir = data_dir / "ext_data" / config.id
    if config.mode == "timeseries":
        return cfg_dir / "timeseries"
    return cfg_dir


def _parquet_glob(config: ExtConfig, data_dir: Path) -> str:
    """返回该扩展配置下所有 parquet 文件的 glob 模式。

    snapshot: 'data/ext_data/{id}/*.parquet'（只有 part.parquet）
    timeseries: 'data/ext_data/{id}/timeseries/**/*.parquet'
    """
    cfg_dir = data_dir / "ext_data" / config.id
    if config.mode == "snapshot":
        return str(cfg_dir / "*.parquet")
    return str(cfg_dir / "timeseries" / "**" / "*.parquet")


def _safe_json_value(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _read_ext_dataframe(
    config: ExtConfig,
    data_dir: Path,
    snapshot_date: str | None = None,
) -> tuple[pl.DataFrame, str | None]:
    cfg_dir = data_dir / "ext_data" / config.id

    if config.mode == "snapshot":
        path = cfg_dir / "part.parquet"
        if not path.exists():
            return pl.DataFrame(), None
        return pl.read_parquet(path), _latest_sync_date(config, data_dir)

    base = cfg_dir / "timeseries"
    if not base.exists():
        return pl.DataFrame(), None

    if snapshot_date:
        path = base / f"date={snapshot_date}" / "part.parquet"
        if not path.exists():
            return pl.DataFrame(), snapshot_date
        return pl.read_parquet(path), snapshot_date

    partitions = sorted(
        d for d in base.iterdir()
        if d.is_dir() and d.name.startswith("date=") and (d / "part.parquet").exists()
    )
    if not partitions:
        return pl.DataFrame(), None

    latest = partitions[-1]
    latest_date = latest.name[5:]
    return pl.read_parquet(latest / "part.parquet"), latest_date


def _with_instrument_name(df: pl.DataFrame, data_dir: Path) -> pl.DataFrame:
    if df.is_empty() or "symbol" not in df.columns or "name" in df.columns:
        return df
    path = data_dir / "instruments" / "instruments.parquet"
    if not path.exists():
        return df
    try:
        inst = pl.read_parquet(path)
        if "symbol" in inst.columns and "name" in inst.columns:
            inst = inst.select(["symbol", "name"]).unique(subset=["symbol"], keep="last")
            return df.join(inst, on="symbol", how="left")
    except Exception:
        return df
    return df


def _latest_sync_date(config: ExtConfig, data_dir: Path) -> str | None:
    """扫描数据文件，返回该扩展配置的最新同步时间（含时分秒）。

    - snapshot: 直接取 ext_data/{id}/part.parquet 的 mtime
    - timeseries: 扫描 ext_data/{id}/timeseries/date=xxx 分区目录
    """
    from datetime import datetime

    if config.mode == "snapshot":
        # 快照: part.parquet 与 config.json 同级
        p = data_dir / "ext_data" / config.id / "part.parquet"
        if p.exists():
            ts = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            return ts
        # 兼容旧路径
        old = data_dir / "instruments_ext"
        if old.exists():
            return _latest_sync_from_partitions(old)
        return None

    # 时序: 扫描 timeseries/date=xxx
    base = _ext_data_dir(config, data_dir)
    if not base.exists():
        # 兼容旧路径
        base = data_dir / "kline_ext"
    if not base.exists():
        return None
    return _latest_sync_from_partitions(base)


def _latest_sync_from_partitions(base: Path) -> str | None:
    """从 date=xxx 分区目录中找到最新分区的修改时间。"""
    from datetime import datetime
    latest_ts: float = 0
    latest_date: str | None = None
    for d in base.iterdir():
        if d.is_dir() and d.name.startswith("date="):
            for f in d.glob("*.parquet"):
                mtime = f.stat().st_mtime
                if mtime > latest_ts:
                    latest_ts = mtime
                    latest_date = d.name[5:]
    if latest_date and latest_ts > 0:
        ts = datetime.fromtimestamp(latest_ts).strftime("%H:%M:%S")
        return f"{latest_date} {ts}"
    return latest_date


@router.get("/{config_id}/dimension-members")
def dimension_members(
    request: Request,
    config_id: str,
    field: str = Query(..., min_length=1),
    value: str = Query(..., min_length=1),
    snapshot_date: str | None = Query(None, alias="date"),
    limit: int = Query(1000, ge=1, le=10000),
):
    """按扩展字段的完整标签值返回成分股，不绑定具体概念/行业数据源。"""
    config = _store(request).get(config_id)
    if not config:
        raise HTTPException(404, f"配置 '{config_id}' 不存在")

    data_dir = _data_dir(request)
    df, active_date = _read_ext_dataframe(config, data_dir, snapshot_date)
    df = _with_instrument_name(df, data_dir)
    matched = _filter_dimension_member_rows(df, field, value)
    total = len(matched)

    columns = ["symbol", "code", "name", "股票代码", "股票简称", field]
    for mapping in (config.symbol_map, config.code_map):
        if isinstance(mapping, dict) and mapping.get("type") == "mapped" and mapping.get("col"):
            columns.append(str(mapping["col"]))
    selected = [column for column in dict.fromkeys(columns) if column in matched.columns]
    if selected:
        matched = matched.select(selected)
    if total > limit:
        matched = matched.head(limit)

    symbol_columns = ["symbol", "code", "股票代码", "代码"]
    name_columns = ["name", "股票简称", "名称"]
    for mapping in (config.symbol_map, config.code_map):
        if isinstance(mapping, dict) and mapping.get("type") == "mapped" and mapping.get("col"):
            symbol_columns.append(str(mapping["col"]))

    rows = []
    for raw in matched.to_dicts():
        row = {key: _safe_json_value(item) for key, item in raw.items()}
        if not row.get("symbol"):
            row["symbol"] = next((str(row[column]) for column in symbol_columns if row.get(column)), "")
        if not row.get("name"):
            row["name"] = next((str(row[column]) for column in name_columns if row.get(column)), "")
        rows.append(row)
    return {
        "id": config.id,
        "label": config.label,
        "date": active_date,
        "field": field,
        "value": value.strip(),
        "total": total,
        "limit": limit,
        "rows": rows,
    }


@router.get("/schema-all")
def discover_all_schemas(request: Request):
    """发现所有扩展表的 schema（用于前端动态列选择）。"""
    configs = _store(request).load_all()
    result = []
    for config in configs:
        data_dir = _data_dir(request)
        glob = _parquet_glob(config, data_dir)

        try:
            import duckdb
            cols = duckdb.query(
                f"SELECT column_name, data_type FROM (DESCRIBE SELECT * FROM read_parquet('{glob}', union_by_name=true))"
            ).fetchall()
            field_labels = {f.name: f.label for f in config.fields}
            columns = [{"name": r[0], "type": r[1], "label": field_labels.get(r[0], r[0])} for r in cols]
        except Exception:
            columns = [f.to_dict() for f in config.fields]

        result.append({
            "id": config.id,
            "label": config.label,
            "mode": config.mode,
            "columns": columns,
        })
    return {"items": result}


# ---------------------------------------------------------------------------
# 视图刷新
# ---------------------------------------------------------------------------

def _refresh_views(request: Request) -> None:
    """重新注册 DuckDB 视图以包含新的扩展数据。"""
    repo = request.app.state.repo
    db = repo.store.db
    d = repo.store.data_dir.as_posix()

    # 注册旧路径视图（兼容）
    for name, subdir in [("instruments_ext", "instruments_ext"), ("kline_ext", "kline_ext")]:
        old_glob = f"{d}/{subdir}/**/*.parquet"
        old_dir = Path(d) / subdir
        if old_dir.exists():
            sql = (
                f"CREATE OR REPLACE VIEW {name} AS "
                f"SELECT * FROM read_parquet('{old_glob}', union_by_name=true)"
            )
            try:
                db.execute(sql)
            except Exception:
                pass

    # 注册新路径视图：每个扩展表一个视图 ext_{config_id}
    ext_base = Path(d) / "ext_data"
    if ext_base.exists():
        for cfg_dir in ext_base.iterdir():
            if not cfg_dir.is_dir():
                continue
            cp = cfg_dir / "config.json"
            if not cp.exists():
                continue
            try:
                raw = json.loads(cp.read_text(encoding="utf-8"))
                cfg_id = raw["id"]
                # 检查是否有数据文件（snapshot: part.parquet, timeseries: timeseries/ 目录）
                has_data = (cfg_dir / "part.parquet").exists() or (cfg_dir / "timeseries").exists()
                if has_data:
                    view_name = f"ext_{cfg_id}"
                    # snapshot: part.parquet 在 cfg_dir/ 根下; timeseries: 在 timeseries/ 子目录
                    mode = raw.get("mode", "snapshot")
                    if mode == "snapshot":
                        glob_pattern = f"{cfg_dir.as_posix()}/*.parquet"
                    else:
                        glob_pattern = f"{cfg_dir.as_posix()}/timeseries/**/*.parquet"
                    sql = (
                        f"CREATE OR REPLACE VIEW {view_name} AS "
                        f"SELECT * FROM read_parquet('{glob_pattern}', union_by_name=true)"
                    )
                    db.execute(sql)
            except Exception:
                pass

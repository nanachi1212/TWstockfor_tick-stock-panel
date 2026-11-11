"""偏離列 (deviate_Nd) 計算測試 — 全量附著 + 盤中今日路徑。

注: 原 A 股「異動邊緣」監控棧 (services/abnormal_moves.py 及 type=abnormal
監控規則) 已在 Phase 8B-5.2 移除; 本文件保留的是仍被 Screener 偏離列
(deviate_3d/10d/30d) 使用的通用指標管線測試。
"""
from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from app.indicators.pipeline import (
    attach_deviation_columns,
    attach_deviation_columns_today,
    benchmark_momentum_today,
    load_benchmark_momentum,
)
from app.market_time import cn_today


def _write_index_daily(tmp_path, rows: list[tuple[str, date, float]]) -> None:
    df = pl.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "date": [r[1] for r in rows],
            "close": [r[2] for r in rows],
        }
    )
    for dt in sorted({r[1] for r in rows}):
        target = tmp_path / "kline_index_daily" / f"date={dt.isoformat()}"
        target.mkdir(parents=True, exist_ok=True)
        df.filter(pl.col("date") == dt).write_parquet(target / "part.parquet")


def test_attach_deviation_columns_math(tmp_path) -> None:
    # 上證指數 4 天等差 +1: 3日動量 = 13/10-1 = 0.30
    # 個股 close 與指數同序列 → momentum_3d 缺失時按 close 就地補算, 偏離 = 0
    days = [date(2026, 8, 13), date(2026, 8, 14), date(2026, 8, 15), date(2026, 8, 18)]
    index_rows = [("000001.SH", d, 10.0 + i) for i, d in enumerate(days)]
    _write_index_daily(tmp_path, index_rows)

    stock = pl.DataFrame(
        {
            "symbol": ["600000.SH"] * len(days),
            "date": days,
            "close": [10.0 + i for i in range(len(days))],
            # 10/30 日窗口已有動量列 → 直接使用
            "momentum_10d": [None] * 4,
            "momentum_30d": [None] * 4,
        }
    )
    out = attach_deviation_columns(stock, tmp_path)
    assert "deviate_3d" in out.columns
    assert "momentum_3d" in out.columns  # 就地補算
    last = out.sort("date").row(-1, named=True)
    assert abs(last["deviate_3d"] - 0.0) < 1e-9


def test_attach_deviation_columns_missing_benchmark(tmp_path) -> None:
    # 無指數資料: 偏離列為 null, 不拋異常
    stock = pl.DataFrame(
        {
            "symbol": ["600000.SH"],
            "date": [date(2026, 8, 18)],
            "momentum_3d": [0.2],
            "momentum_10d": [0.5],
            "momentum_30d": [1.0],
        }
    )
    out = attach_deviation_columns(stock, tmp_path)
    assert out["deviate_3d"][0] is None


# ── 盤中路徑: 今日基準動量外推 + 單日幀偏離附著 ──────────────────

_BENCH_DAYS = [date(2026, 8, 11), date(2026, 8, 12), date(2026, 8, 13),
               date(2026, 8, 14), date(2026, 8, 15), date(2026, 8, 18)]


def _write_sh_bench(tmp_path) -> None:
    # 上證指數 6 日收盤 10..15, 末值 15 為昨收
    _write_index_daily(tmp_path, [("000001.SH", d, 10.0 + i) for i, d in enumerate(_BENCH_DAYS)])


def test_benchmark_momentum_today_math(tmp_path) -> None:
    _write_sh_bench(tmp_path)
    quotes = pl.DataFrame({"symbol": ["000001.SH"], "change_pct": [0.10]})

    out = benchmark_momentum_today(tmp_path, quotes)
    row = out.row(0, named=True)
    # 今收 = 15 x 1.10 = 16.5; 3 個交易日前的收盤 = 13 (與全量路徑 shift(3) 同口徑)
    # mom3d = 16.5/13 - 1
    assert abs(row["bench_mom3d"] - (16.5 / 13 - 1)) < 1e-9
    # 10/30 日窗口收盤數不足 → null
    assert row["bench_mom10d"] is None
    assert row["bench_mom30d"] is None

    # 無即時行情 → rt 按 0 處理: mom3d = 15/13 - 1
    out0 = benchmark_momentum_today(tmp_path, None)
    assert abs(out0.row(0, named=True)["bench_mom3d"] - (15.0 / 13 - 1)) < 1e-9


def test_benchmark_momentum_today_excludes_today_rows(tmp_path) -> None:
    # 指數監控盤寫入的今日行不能當昨收 (否則即時漲跌被重複疊加)
    today = cn_today()
    rows = [("000001.SH", d, 10.0 + i) for i, d in enumerate(_BENCH_DAYS)]
    rows.append(("000001.SH", today, 99.0))  # 今日髒行
    _write_index_daily(tmp_path, rows)

    out = benchmark_momentum_today(tmp_path, None)
    assert abs(out.row(0, named=True)["bench_mom3d"] - (15.0 / 13 - 1)) < 1e-9


def test_attach_deviation_columns_today(tmp_path) -> None:
    _write_sh_bench(tmp_path)
    quotes = pl.DataFrame({"symbol": ["000001.SH"], "change_pct": [0.10]})
    # 單日幀: 增量路徑產出的 momentum 列 (無 date 歷史, 無法 shift 補算)
    today_df = pl.DataFrame(
        {
            "symbol": ["600000.SH", "000001.SZ"],
            "momentum_3d": [0.5, 0.2],
            "momentum_10d": [0.2, None],
            "momentum_30d": [1.0, None],
        }
    )
    out = attach_deviation_columns_today(today_df, tmp_path, quotes)
    # SH: 0.5 - (16.5/13 - 1)
    assert abs(out["deviate_3d"][0] - (0.5 - (16.5 / 13 - 1))) < 1e-9
    # SZ 無深證基準 → 按選基設計回退上證基準 (rt=0): 0.2 - (15/13 - 1)
    assert abs(out["deviate_3d"][1] - (0.2 - (15.0 / 13 - 1))) < 1e-9
    assert "bench_close" not in out.columns


def test_attach_deviation_columns_today_missing_momentum(tmp_path) -> None:
    # 全量回退路徑可能缺 momentum_3d: 該窗口置 null, 其餘窗口正常
    days = [date(2026, 7, 1) + timedelta(days=i) for i in range(35)]
    _write_index_daily(tmp_path, [("000001.SH", d, 10.0 + i) for i, d in enumerate(days)])
    df = pl.DataFrame(
        {
            "symbol": ["600000.SH"],
            "momentum_10d": [0.2],
            "momentum_30d": [1.0],
        }
    )
    out = attach_deviation_columns_today(df, tmp_path, None)
    assert out["deviate_3d"][0] is None
    assert out["deviate_10d"][0] is not None
    assert out["deviate_30d"][0] is not None


def test_attach_deviation_columns_no_bench_close_leak(tmp_path) -> None:
    # load_benchmark_momentum 新增 bench_close 列後, 冷路徑輸出不應洩漏該列
    _write_sh_bench(tmp_path)
    stock = pl.DataFrame(
        {
            "symbol": ["600000.SH"],
            "date": [date(2026, 8, 18)],
            "close": [15.0],
        }
    )
    out = attach_deviation_columns(stock, tmp_path)
    assert "bench_close" not in out.columns
    frame = load_benchmark_momentum(tmp_path)
    assert "bench_close" in frame.columns

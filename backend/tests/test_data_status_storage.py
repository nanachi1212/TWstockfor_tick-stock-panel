"""storage 統計求和不變數測試 — total_size_mb 必須等於各部分之和, 不得重複累加。

歷史 bug: other_dirs 循環被複制了兩遍, 且 financials 既在 other_dirs 裡又有專屬
統計塊 —— financials 被計入 3 次、pools/backtest_results/screener_results/ai_cache
各計入 2 次, total_size_mb 虛高。此測試用精確整 MB 檔案鎖定總和不變數。
"""
from __future__ import annotations

from pathlib import Path

from app.api.data import _compute_storage

MB = 1024 * 1024


def _write_mb(path: Path, mb: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * int(mb * MB))


def _build_tree(data_dir: Path) -> None:
    _write_mb(data_dir / "kline_daily" / "part.parquet", 1.0)
    _write_mb(data_dir / "financials" / "metrics" / "part.parquet", 2.0)
    _write_mb(data_dir / "pools" / "p.json", 1.0)
    _write_mb(data_dir / "backtest_results" / "r.json", 1.0)
    _write_mb(data_dir / "screener_results" / "s.json", 1.0)
    _write_mb(data_dir / "ai_cache" / "c.json", 1.0)
    _write_mb(data_dir / "capabilities.json", 0.5)  # 根目錄散檔案


def test_total_equals_sum_of_parts(tmp_path):
    _build_tree(tmp_path)
    stats = _compute_storage(tmp_path)

    parts_mb = (
        stats["daily_size_mb"]            # 1.0  (subdirs 表)
        + stats["financials_size_mb"]     # 2.0  (專屬統計塊)
        + 1.0 + 1.0 + 1.0 + 1.0           # pools/backtest/screener/ai_cache (單次循環)
        + 0.5                             # 根目錄散檔案
    )
    assert stats["total_size_mb"] == parts_mb == 7.5


def test_financials_counted_once_not_three_times(tmp_path):
    _build_tree(tmp_path)
    stats = _compute_storage(tmp_path)

    assert stats["financials_files"] == 1
    assert stats["financials_size_mb"] == 2.0
    # 舊 bug: financials 計 3 次 (6MB) + 其餘 4 目錄各計 2 次 (8MB) → 15.5
    assert stats["total_size_mb"] != 15.5


def test_missing_dirs_contribute_zero(tmp_path):
    _write_mb(tmp_path / "kline_daily" / "part.parquet", 1.0)
    stats = _compute_storage(tmp_path)

    # financials 目錄不存在時不含其明細鍵 (既有行為), 且不貢獻任何體積
    assert stats.get("financials_files", 0) == 0
    assert stats.get("financials_size_mb", 0.0) == 0.0
    assert stats["total_size_mb"] == 1.0

"""Phase 8B-5.0.6/7 — canonical Taiwan data root: CWD-independence + override tests.

Root cause under test: TaiwanDailyStore / TaiwanSecurityMaster / TaiwanMonitorEngine
(Phase 8B-5.0.6), and TaiwanInstitutionalStore / TaiwanMarginStore (Phase 8B-5.0.7)
used to default to a *bare* relative Path("data/taiwan/...") — resolved against
whatever the process's current working directory happened to be at first use,
not the project root. Phase 8B-5.0.5 observed this directly: launching the same
class from within backend/ wrote data under backend/data/taiwan instead of the
project's real data/taiwan. These tests assert the fix: the default now always
resolves to settings.data_dir/taiwan/... regardless of process CWD, and explicit
overrides (used by every existing deterministic test in this repo) still work.
"""
from __future__ import annotations

import os

from app.taiwan.data_root import taiwan_data_root
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.realtime.monitor_engine import TaiwanMonitorEngine
from app.taiwan.universe.service import TaiwanSecurityMaster


def test_taiwan_data_root_is_anchored_to_settings_data_dir():
    from app.config import settings

    assert taiwan_data_root() == settings.data_dir / "taiwan"


def test_taiwan_data_root_is_absolute():
    assert taiwan_data_root().is_absolute()


def test_taiwan_daily_store_default_path_is_cwd_independent(tmp_path, monkeypatch):
    """建兩個不同 CWD 下的 TaiwanDailyStore(), 預設路徑必須解析到同一個 canonical root。"""
    cwd_a = tmp_path / "launch_from_project_root_like"
    cwd_b = tmp_path / "launch_from_backend_dir_like"
    cwd_a.mkdir()
    cwd_b.mkdir()

    monkeypatch.chdir(cwd_a)
    store_a = TaiwanDailyStore()
    path_a = store_a._data_dir  # noqa: SLF001 (白盒驗證 resolve 結果, 非改測試脆弱內部行為)

    monkeypatch.chdir(cwd_b)
    store_b = TaiwanDailyStore()
    path_b = store_b._data_dir  # noqa: SLF001

    assert path_a == path_b, (
        f"不同 CWD 下的預設 TaiwanDailyStore 路徑應相同, 實際: {path_a} vs {path_b}"
    )
    assert path_a.is_absolute()
    assert str(cwd_a) not in str(path_a)
    assert str(cwd_b) not in str(path_b)


def test_taiwan_security_master_default_path_is_cwd_independent(tmp_path, monkeypatch):
    cwd_a = tmp_path / "cwd_a"
    cwd_b = tmp_path / "cwd_b"
    cwd_a.mkdir()
    cwd_b.mkdir()

    monkeypatch.chdir(cwd_a)
    master_a = TaiwanSecurityMaster()
    path_a = master_a.cache_path

    monkeypatch.chdir(cwd_b)
    master_b = TaiwanSecurityMaster()
    path_b = master_b.cache_path

    assert path_a == path_b
    assert path_a.is_absolute()


def test_taiwan_monitor_engine_default_path_is_cwd_independent(tmp_path, monkeypatch):
    cwd_a = tmp_path / "cwd_a"
    cwd_b = tmp_path / "cwd_b"
    cwd_a.mkdir()
    cwd_b.mkdir()

    monkeypatch.chdir(cwd_a)
    engine_a = TaiwanMonitorEngine()
    path_a = engine_a.storage_path

    monkeypatch.chdir(cwd_b)
    engine_b = TaiwanMonitorEngine()
    path_b = engine_b.storage_path

    assert path_a == path_b
    assert path_a.is_absolute()


def test_taiwan_daily_store_explicit_override_still_respected(tmp_path):
    """顯式傳入 data_dir 時, 必須精確使用該路徑 (不落回 canonical root)。"""
    custom = tmp_path / "my_custom_daily_dir"
    store = TaiwanDailyStore(data_dir=custom)
    assert store._data_dir == custom  # noqa: SLF001


def test_taiwan_security_master_explicit_override_still_respected(tmp_path):
    custom = tmp_path / "my_custom_master.parquet"
    master = TaiwanSecurityMaster(cache_path=custom)
    assert master.cache_path == custom


def test_taiwan_monitor_engine_explicit_override_still_respected(tmp_path):
    custom = tmp_path / "my_custom_rules.json"
    engine = TaiwanMonitorEngine(storage_path=custom)
    assert engine.storage_path == custom


def test_data_root_matches_ashare_sibling_layout():
    """taiwan/ 應該是 settings.data_dir 下與 instruments/、kline_daily/ 同級的
    子目錄, 而不是另立門戶的第二套資料根 —— 迴歸保護, 防止未來又長出
    TAIWAN_DATA_ROOT / APP_DATA_ROOT 之類互相競爭的路徑系統。"""
    from app.config import settings

    root = taiwan_data_root()
    assert root.parent == settings.data_dir
    assert root.name == "taiwan"


# ===== Phase 8B-5.0.7: TaiwanInstitutionalStore / TaiwanMarginStore =====

def test_taiwan_institutional_store_default_path_is_cwd_independent(tmp_path, monkeypatch):
    """建兩個不同 CWD 下的 TaiwanInstitutionalStore(), 預設路徑必須解析到同一個
    canonical root(與 TaiwanDailyStore 相同的 Phase 8B-5.0.6 bug 類別)。"""
    cwd_a = tmp_path / "cwd_a"
    cwd_b = tmp_path / "cwd_b"
    cwd_a.mkdir()
    cwd_b.mkdir()

    monkeypatch.chdir(cwd_a)
    store_a = TaiwanInstitutionalStore()
    path_a = store_a._data_dir  # noqa: SLF001 (白盒驗證 resolve 結果)

    monkeypatch.chdir(cwd_b)
    store_b = TaiwanInstitutionalStore()
    path_b = store_b._data_dir  # noqa: SLF001

    assert path_a == path_b, (
        f"不同 CWD 下的預設 TaiwanInstitutionalStore 路徑應相同, 實際: {path_a} vs {path_b}"
    )
    assert path_a.is_absolute()
    assert str(cwd_a) not in str(path_a)
    assert str(cwd_b) not in str(path_b)


def test_taiwan_margin_store_default_path_is_cwd_independent(tmp_path, monkeypatch):
    cwd_a = tmp_path / "cwd_a"
    cwd_b = tmp_path / "cwd_b"
    cwd_a.mkdir()
    cwd_b.mkdir()

    monkeypatch.chdir(cwd_a)
    store_a = TaiwanMarginStore()
    path_a = store_a._data_dir  # noqa: SLF001

    monkeypatch.chdir(cwd_b)
    store_b = TaiwanMarginStore()
    path_b = store_b._data_dir  # noqa: SLF001

    assert path_a == path_b, (
        f"不同 CWD 下的預設 TaiwanMarginStore 路徑應相同, 實際: {path_a} vs {path_b}"
    )
    assert path_a.is_absolute()
    assert str(cwd_a) not in str(path_a)
    assert str(cwd_b) not in str(path_b)


def test_taiwan_institutional_store_explicit_override_still_respected(tmp_path):
    """顯式傳入 data_dir 時, 必須精確使用該路徑 (不落回 canonical root)。"""
    custom = tmp_path / "my_custom_institutional_dir"
    store = TaiwanInstitutionalStore(data_dir=custom)
    assert store._data_dir == custom  # noqa: SLF001


def test_taiwan_margin_store_explicit_override_still_respected(tmp_path):
    custom = tmp_path / "my_custom_margin_dir"
    store = TaiwanMarginStore(data_dir=custom)
    assert store._data_dir == custom  # noqa: SLF001


def test_taiwan_institutional_and_margin_share_canonical_root_with_daily():
    """三個 Taiwan 日頻 store(daily/institutional/margin)的預設路徑都應是
    taiwan_data_root() 下的同級子目錄, 不是各自獨立的路徑系統。"""
    daily_root = TaiwanDailyStore()._data_dir  # noqa: SLF001
    inst_root = TaiwanInstitutionalStore()._data_dir  # noqa: SLF001
    margin_root = TaiwanMarginStore()._data_dir  # noqa: SLF001

    assert daily_root.parent == taiwan_data_root()
    assert inst_root.parent == taiwan_data_root()
    assert margin_root.parent == taiwan_data_root()
    assert daily_root.name == "daily"
    assert inst_root.name == "institutional"
    assert margin_root.name == "margin"

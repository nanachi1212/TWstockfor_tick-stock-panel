"""回归测试: Phase 8B-5.14 — ext_gn_ths/ext_hy_ths 自动背景拉取排除机制。

均为纯逻辑, 使用 tmp_path 隔离的 ExtConfigStore, 打桩 fetch_and_ingest,
不触网, 不依赖真实数据源 (shy313.com)。
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from app import main as app_main
from app.services import ext_pull
from app.services.ext_data import ExtConfig, ExtConfigStore, ExtField, PullConfig
from app.services.ext_pull import PullScheduler


def _make_config(config_id: str, url: str = "http://example.invalid/x") -> ExtConfig:
    return ExtConfig(
        id=config_id,
        label=config_id,
        mode="snapshot",
        fields=[ExtField("symbol", "string", "symbol")],
        pull=PullConfig(url=url, method="GET", schedule_minutes=1440, enabled=True),
    )


@pytest.fixture
async def scheduler(monkeypatch):
    """独立的 PullScheduler 实例 (不用全局单例, 避免污染其他测试)。

    fetch_and_ingest 打桩为立即返回, 确保即使某个 config 被误排程,
    也不会真正发起 HTTP 请求。teardown 时显式等待任务真正被取消,
    避免遗留 86400s (schedule_minutes=1440) 的 sleep 任务拖慢测试进程退出。
    """
    calls: list[str] = []

    async def _fake_fetch_and_ingest(config, data_dir):
        calls.append(config.id)
        return 0, "2026-01-01"

    monkeypatch.setattr(ext_pull, "fetch_and_ingest", _fake_fetch_and_ingest)

    sched = PullScheduler()
    yield sched, calls
    tasks = list(sched._tasks.values())
    sched.stop()
    if tasks:
        await asyncio.wait(tasks, timeout=1)


async def test_legacy_ashare_presets_not_scheduled_even_when_enabled(tmp_path, scheduler):
    """Case B: 旧 config 已存在且 pull.enabled=true, 但 id 在排除清单里 → 不排程、不拉取。"""
    sched, calls = scheduler
    store = ExtConfigStore(tmp_path)
    store.upsert(_make_config("ext_gn_ths"))
    store.upsert(_make_config("ext_hy_ths"))

    sched.start(tmp_path)
    sched.set_excluded_ids(("ext_gn_ths", "ext_hy_ths"))
    sched.refresh(tmp_path)
    await asyncio.sleep(0)  # 让 call_soon_threadsafe 排入的 _apply() 执行

    assert "ext_gn_ths" not in sched._tasks
    assert "ext_hy_ths" not in sched._tasks

    await asyncio.sleep(0)  # 给可能误排程的 task 一次运行机会
    assert calls == []  # fetch_and_ingest (对应实际网络拉取) 一次都没被调用


async def test_generic_config_still_scheduled(tmp_path, scheduler):
    """Case C: 非 A 股的通用 config, pull.enabled=true → 仍正常排程 (调度器未被误杀)。"""
    sched, calls = scheduler
    store = ExtConfigStore(tmp_path)
    store.upsert(_make_config("generic_test_source"))

    sched.start(tmp_path)
    sched.set_excluded_ids(("ext_gn_ths", "ext_hy_ths"))
    sched.refresh(tmp_path)
    await asyncio.sleep(0)

    assert "generic_test_source" in sched._tasks


async def test_exclusion_is_generic_not_hardcoded_in_scheduler(tmp_path, scheduler):
    """排除清单是调用方 (main.py) 注入的通用机制 —— 调度器本身不认识
    ext_gn_ths/ext_hy_ths 是什么, 换一批任意 id 一样能排除。"""
    sched, calls = scheduler
    store = ExtConfigStore(tmp_path)
    store.upsert(_make_config("some_other_source"))

    sched.start(tmp_path)
    sched.set_excluded_ids(("some_other_source",))
    sched.refresh(tmp_path)
    await asyncio.sleep(0)

    assert "some_other_source" not in sched._tasks


def test_main_no_longer_auto_seeds_ashare_ext_presets():
    """Case A (fresh install): main.py 不再自动调用 ensure_builtin_presets(),
    全新安装不会自动创建 ext_gn_ths/ext_hy_ths 的 config.json、不排程、
    不对 shy313.com 发起启动期请求。

    排除清单本身 (ext_gn_ths/ext_hy_ths 字面量) 应仍存在于 main.py 里,
    用于 Case B (旧安装) 的 set_excluded_ids() 调用 —— 只是不应再有
    ensure_builtin_presets 的调用点/导入 (说明性注释中提及该函数名不算)。
    """
    src = inspect.getsource(app_main)
    assert "from app.services.ext_presets import ensure_builtin_presets" not in src
    assert "await ensure_builtin_presets(" not in src
    assert "set_excluded_ids" in src
    assert "ext_gn_ths" in src
    assert "ext_hy_ths" in src

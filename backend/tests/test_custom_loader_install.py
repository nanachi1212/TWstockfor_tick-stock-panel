"""loader.install_plugin 單元測試 — uv 安裝必須顯式指定目標環境。

迴歸場景: dev.ps1 直接跑 backend/.venv/Scripts/python.exe 而不 activate venv,
後端行程的 VIRTUAL_ENV 為空; 此時 `uv pip install -r ...` 不傳 --python 會落到
PATH 上的基礎解釋器 (如 conda base, 常為只讀) → 裝錯環境 + exit 2 (訪問拒絕)。
修復: uv 命令顯式傳 `--python sys.executable` 鎖定後端自身 venv, 與 pip 回退
路徑 (sys.executable -m pip) 的目標一致。
"""
from __future__ import annotations

import subprocess
import sys

from app.data_providers.custom import loader


def _patch_uv_install(monkeypatch, returncodes):
    """替換 loader.shutil.which 與 subprocess.run, 捕獲 uv 命令行。"""
    calls: list[list[str]] = []

    def fake_which(cmd, *_a, **_k):
        return "/fake/uv" if cmd == "uv" else None

    def fake_run(cmd, *_a, **_k):
        calls.append(list(cmd))
        code = returncodes.pop(0) if isinstance(returncodes, list) else returncodes
        return subprocess.CompletedProcess(cmd, code)

    monkeypatch.setattr(loader.shutil, "which", fake_which)
    monkeypatch.setattr(loader.subprocess, "run", fake_run)
    return calls


def _fake_python_plugin(monkeypatch, tmp_path):
    """在臨時目錄鋪設最小 python 外掛, 並把 plugins_dir 指過去 (自包含)。"""
    pdir = tmp_path / "baostock"
    pdir.mkdir()
    (pdir / "plugin.yaml").write_text(
        "name: baostock\nruntime: python\nentry: p:provider\n", encoding="utf-8",
    )
    (pdir / "requirements.txt").write_text("baostock\n", encoding="utf-8")
    monkeypatch.setattr(loader, "plugins_dir", lambda: tmp_path)
    return pdir


def test_install_plugin_uv_targets_running_interpreter(monkeypatch, tmp_path):
    """uv 分支必須傳 --python sys.executable, 不能留給 uv 自動探測。"""
    _fake_python_plugin(monkeypatch, tmp_path)
    calls = _patch_uv_install(monkeypatch, [0])

    ok, msg = loader.install_plugin("baostock")
    assert ok, msg
    assert calls, "應呼叫 uv"
    uv_cmd = calls[0]
    assert uv_cmd[0] == "/fake/uv"
    assert "--python" in uv_cmd
    idx = uv_cmd.index("--python")
    assert uv_cmd[idx + 1] == sys.executable


def test_install_plugin_uv_retry_keeps_python_target(monkeypatch, tmp_path):
    """exit 2 → --no-config 重試時也必須保持 --python 目標。"""
    _fake_python_plugin(monkeypatch, tmp_path)
    calls = _patch_uv_install(monkeypatch, [2, 0])

    ok, msg = loader.install_plugin("baostock")
    assert ok, msg
    assert len(calls) == 2, f"應觸發一次重試, 實際 {len(calls)} 次"
    for cmd in calls:
        assert "--python" in cmd
        idx = cmd.index("--python")
        assert cmd[idx + 1] == sys.executable


def test_uninstall_plugin_uv_targets_running_interpreter(monkeypatch, tmp_path):
    """uv 解除安裝同樣必須傳 --python sys.executable, 否則會動到基礎解釋器。"""
    calls: list[list[str]] = []

    def fake_which(cmd, *_a, **_k):
        return "/fake/uv" if cmd == "uv" else None

    def fake_run(cmd, *_a, **_k):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    _fake_python_plugin(monkeypatch, tmp_path)
    monkeypatch.setattr(loader.shutil, "which", fake_which)
    monkeypatch.setattr(loader.subprocess, "run", fake_run)

    ok, msg = loader.uninstall_plugin("baostock")
    assert ok, msg
    assert calls, "應呼叫 uv"
    uv_cmd = calls[0]
    assert uv_cmd[0] == "/fake/uv"
    assert "uninstall" in uv_cmd
    assert "--python" in uv_cmd
    idx = uv_cmd.index("--python")
    assert uv_cmd[idx + 1] == sys.executable

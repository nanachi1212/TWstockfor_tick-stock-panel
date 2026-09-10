"""Nanachi 的台股監控看板 backend."""

import sys
from pathlib import Path

_version_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
__version__ = (_version_root / "VERSION").read_text(encoding="utf-8").strip().removeprefix("v")

# Windows 默认 stdout/stderr 编码为 GBK(cp936),TickFlow SDK 内部输出含 emoji 的
# 指数/标的名称(如 \U0001f193)时会抛 UnicodeEncodeError,导致请求失败。
# 进程加载最早阶段强制 UTF-8,根治此类编码崩溃。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

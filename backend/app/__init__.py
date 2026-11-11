"""Nanachi 的台股監控看板 backend."""

import contextlib
import sys
from pathlib import Path

_version_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
__version__ = (_version_root / "VERSION").read_text(encoding="utf-8").strip().removeprefix("v")

# Windows 預設 stdout/stderr 編碼為 GBK(cp936),第三方資料來源輸出含 emoji 的
# 指數/標的名稱(如 \U0001f193)時會拋 UnicodeEncodeError,導致請求失敗。
# 行程載入最早階段強制 UTF-8,根治此類編碼崩潰。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        with contextlib.suppress(Exception):
            _stream.reconfigure(encoding="utf-8", errors="replace")

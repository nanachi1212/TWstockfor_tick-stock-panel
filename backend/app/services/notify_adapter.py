"""系統通知適配器 — 三平台原生通知中心。

職責: 把後端產生的告警事件推送到操作系統通知中心。
     窗口最小化 / 被遮擋 / 後台執行時都能彈通知 (不依賴前端 WebView)。

平台實現:
  - Windows: winotify (進現代操作中心, 支持圖標)
  - macOS:   osascript (系統已內置, 無需額外依賴)
  - Linux:   notify-send (系統已內置) / plyer 兜底

設計: 失敗靜默降級, 絕不因通知失敗阻斷告警主流程 (落盤 / SSE 推送)。
     通知去重不在本層做, 複用 MonitorRuleEngine 的 cooldown 邏輯。
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# 單次通知最長字元 (避免某些平台截斷報錯)
_MAX_LEN = 200

# Windows 通知中心顯示的來源名稱
_WINDOWS_APP_ID = "Nanachi 的台股監控看板"

# 避免重複探測平台能力, 快取一次
_backend_cache: str | None = None


def _detect_backend() -> str | None:
    """探測當前平台可用的通知後端。"""
    global _backend_cache
    if _backend_cache is not None:
        return _backend_cache if _backend_cache != "none" else None

    backend = None
    if sys.platform == "win32":
        try:
            import winotify  # type: ignore[import-not-found]  # noqa: F401

            backend = "winotify"
        except ImportError:
            logger.debug("winotify 不可用, Windows 通知降級")
            backend = None
    elif sys.platform == "darwin":
        backend = "osascript"
    elif sys.platform.startswith("linux"):
        backend = "notify-send"

    _backend_cache = backend or "none"
    return backend


def _truncate(text: str) -> str:
    """截斷超長文本, 避免平台通知上限報錯。"""
    text = (text or "").strip()
    return text[:_MAX_LEN] + ("…" if len(text) > _MAX_LEN else "")


def notify(title: str, message: str, icon: Path | None = None) -> bool:
    """推送一條系統通知。

    Args:
        title:   通知標題
        message: 通知正文
        icon:    可選圖標路徑 (部分平台支持)

    Returns:
        True=成功送達, False=失敗或無可用後端。
        失敗靜默, 不拋異常 (通知是輔助通道, 不能阻斷告警主流程)。
    """
    title = _truncate(title)
    message = _truncate(message)
    if not title:
        return False

    backend = _detect_backend()
    if backend is None:
        return False

    try:
        if backend == "winotify":
            return _notify_winotify(title, message)
        if backend == "osascript":
            return _notify_osascript(title, message)
        if backend == "notify-send":
            return _notify_notify_send(title, message, icon)
    except Exception as e:  # noqa: BLE001
        logger.warning("系統通知失敗 (%s): %s", backend, e)
        return False

    return False


def _notify_winotify(title: str, message: str) -> bool:
    """Windows 通知 (winotify) — 進現代操作中心。"""
    from winotify import Notification  # type: ignore[import-not-found]

    # Notifier 需要 Registry (用於可點擊回呼); 這裡只發單向通知, 直接用 Notification。
    Notification(
        app_id=_WINDOWS_APP_ID,
        title=title,
        msg=message,
        # winotify 要求 duration 為 "short" 或 "long"
        duration="short",
    ).show()
    return True


def _notify_osascript(title: str, message: str) -> bool:
    """macOS 通知 (osascript) — 呼叫系統 AppleScript。"""
    # 轉義雙引號, 避免 AppleScript 注入
    safe_title = title.replace('"', '\\"')
    safe_msg = message.replace('"', '\\"')
    script = (
        f'display notification "{safe_msg}" with title "{safe_title}"'
    )
    result = subprocess.run(  # noqa: S603, S607
        ["osascript", "-e", script],
        capture_output=True,
        timeout=5,
    )
    return result.returncode == 0


def _notify_notify_send(title: str, message: str, icon: Path | None) -> bool:
    """Linux 通知 (notify-send) — freedesktop.org 標準。"""
    args = ["notify-send", title]
    if icon and Path(icon).exists():
        args.extend(["--icon", str(icon)])
    args.append(message)
    result = subprocess.run(  # noqa: S603
        args,
        capture_output=True,
        timeout=5,
    )
    return result.returncode == 0

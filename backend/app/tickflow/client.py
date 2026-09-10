"""TickFlow SDK 相容轉發層（已解除外部 SDK 依賴，轉為台灣官方/免費資料源）。"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

FREE_ENDPOINT = "https://twse.com.tw"
PAID_ENDPOINT = "https://tpex.org.tw"


def _should_use_free_server() -> bool:
    return True


def _base_url() -> str | None:
    return None


def get_client():
    raise RuntimeError("TickFlow 外部 SDK 已停用；系統已全面改用台灣官方 (TWSE/TPEx) 與免費資料來源。")


def get_async_client():
    raise RuntimeError("TickFlow 外部 SDK 已停用；系統已全面改用台灣官方 (TWSE/TPEx) 與免費資料來源。")


def get_paid_realtime_client():
    return None


def reset_clients() -> None:
    pass


def current_mode() -> str:
    return "official"


def current_endpoint() -> str:
    return "twse_tpex_official"

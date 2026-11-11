"""Capability 定義(§5.1)。

業務代碼只依賴 CapabilitySet 與已配置的資料來源能力。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Cap(StrEnum):
    """所有 capability 的命名常量。新增能力時只在這裡加一行。"""

    QUOTE_BY_SYMBOL        = "quote.by_symbol"
    QUOTE_BATCH            = "quote.batch"
    QUOTE_POOL             = "quote.pool"
    KLINE_DAILY_BY_SYMBOL  = "kline.daily.by_symbol"
    KLINE_DAILY_BATCH      = "kline.daily.batch"
    KLINE_MINUTE_BY_SYMBOL = "kline.minute.by_symbol"
    KLINE_MINUTE_BATCH     = "kline.minute.batch"
    INTRADAY               = "intraday"
    INTRADAY_BATCH         = "intraday.batch"
    DEPTH5                 = "depth5"
    DEPTH5_BATCH           = "depth5.batch"
    WEBSOCKET              = "websocket"
    FINANCIAL              = "financial"
    ADJ_FACTOR             = "adj_factor"


@dataclass(slots=True, frozen=True)
class CapabilityLimits:
    """單個 capability 的執行時限制。"""
    rpm: int | None = None        # 次/分鐘,None 表示未知或不限
    batch: int | None = None      # 標的/次
    subscribe: int | None = None  # WS 訂閱上限


class CapabilitySet:
    """探測得到的"使用者當前可用能力"。業務代碼的唯一真理源。"""

    def __init__(self, caps: dict[Cap, CapabilityLimits] | None = None) -> None:
        self._caps: dict[Cap, CapabilityLimits] = dict(caps or {})

    def has(self, cap: Cap) -> bool:
        return cap in self._caps

    def limits(self, cap: Cap) -> CapabilityLimits | None:
        return self._caps.get(cap)

    def require(self, cap: Cap) -> CapabilityLimits:
        """斷言可用,否則拋 CapabilityDenied。"""
        if cap not in self._caps:
            raise CapabilityDenied(cap)
        return self._caps[cap]

    def all(self) -> dict[Cap, CapabilityLimits]:
        return dict(self._caps)

    def grant(self, cap: Cap, limits: CapabilityLimits | None = None) -> None:
        """Grant configured capabilities without replacing existing limits."""
        if cap not in self._caps:
            self._caps[cap] = limits or CapabilityLimits()

    def to_dict(self) -> dict[str, dict]:
        return {
            str(cap): {
                "rpm": lim.rpm,
                "batch": lim.batch,
                "subscribe": lim.subscribe,
            }
            for cap, lim in self._caps.items()
        }


class CapabilityDenied(Exception):
    """請求的 capability 當前不可用。"""

    def __init__(self, cap: Cap, suggestion: str | None = None) -> None:
        self.cap = cap
        self.suggestion = suggestion or f"目前資料源未提供『{cap}』, 請配置支援此資料集的來源"
        super().__init__(f"capability not available: {cap}; {self.suggestion}")

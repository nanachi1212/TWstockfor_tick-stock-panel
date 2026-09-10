"""能力定義與管理（台股官方免 Key 模式）。"""
from __future__ import annotations

import logging
from app.capabilities import Cap, CapabilityLimits, CapabilitySet, CapabilityDenied

logger = logging.getLogger(__name__)


def detect_capabilities(force: bool = False) -> CapabilitySet:
    """台股官方模式：完全免 API Key、免訂閱，開放所有台股可用能力。"""
    caps = {
        Cap.QUOTE_BY_SYMBOL: CapabilityLimits(rpm=None, batch=None),
        Cap.QUOTE_BATCH: CapabilityLimits(rpm=None, batch=100),
        Cap.QUOTE_POOL: CapabilityLimits(rpm=None, batch=None),
        Cap.KLINE_DAILY_BY_SYMBOL: CapabilityLimits(rpm=None, batch=None),
        Cap.KLINE_DAILY_BATCH: CapabilityLimits(rpm=None, batch=100),
        Cap.KLINE_MINUTE_BY_SYMBOL: CapabilityLimits(rpm=None, batch=None),
        Cap.KLINE_MINUTE_BATCH: CapabilityLimits(rpm=None, batch=100),
        Cap.INTRADAY: CapabilityLimits(rpm=None, batch=None),
        Cap.INTRADAY_BATCH: CapabilityLimits(rpm=None, batch=100),
        Cap.DEPTH5: CapabilityLimits(rpm=None, batch=None),
        Cap.DEPTH5_BATCH: CapabilityLimits(rpm=None, batch=100),
        Cap.WEBSOCKET: CapabilityLimits(rpm=None, batch=None),
        Cap.FINANCIAL: CapabilityLimits(rpm=None, batch=None),
        Cap.ADJ_FACTOR: CapabilityLimits(rpm=None, batch=None),
    }
    return CapabilitySet(caps)


def tier_label() -> str:
    return ""


def base_tier_name() -> str:
    return "official"


def probe_log() -> list[str]:
    return []


def missing_caps() -> list[str]:
    return []


def extras_caps() -> list[str]:
    return []

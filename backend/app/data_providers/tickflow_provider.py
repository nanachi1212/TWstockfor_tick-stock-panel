"""TickFlow provider 相容轉發層（轉向 TaiwanHybridProvider）。"""
from __future__ import annotations

import logging
from app.taiwan.providers.hybrid_provider import TaiwanHybridProvider

logger = logging.getLogger(__name__)


class TickFlowProvider(TaiwanHybridProvider):
    """保持 class 名稱相容，但所有資料請求直接走台灣官方/免費提供者。"""
    name = "tickflow"

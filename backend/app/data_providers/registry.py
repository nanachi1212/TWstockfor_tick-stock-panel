"""Provider registry."""
from __future__ import annotations

from app.data_providers.tickflow_provider import TickFlowProvider

def _get_providers():
    from app.data_providers.tickflow_provider import TickFlowProvider
    from app.taiwan.providers.hybrid_provider import TaiwanHybridProvider
    return {
        "tickflow": TickFlowProvider,
        "taiwan": TaiwanHybridProvider,
    }


def get_provider(name: str = "tickflow"):
    providers = _get_providers()
    provider_cls = providers.get((name or "tickflow").lower())
    if provider_cls is None:
        raise ValueError(f"Unsupported data provider: {name}")
    return provider_cls()


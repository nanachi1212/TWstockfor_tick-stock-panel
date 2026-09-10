"""Provider registry."""
from __future__ import annotations

def _get_providers():
    from app.data_providers.tickflow_provider import TickFlowProvider
    from app.taiwan.providers.hybrid_provider import TaiwanHybridProvider
    return {
        "taiwan": TaiwanHybridProvider,
        "tickflow": TickFlowProvider,
    }


def get_provider(name: str = "taiwan"):
    providers = _get_providers()
    provider_cls = providers.get((name or "taiwan").lower(), providers["taiwan"])
    return provider_cls()

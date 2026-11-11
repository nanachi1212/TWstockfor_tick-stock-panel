"""Registry for Taiwan official data and explicitly configured custom providers."""
from __future__ import annotations


def get_provider(name: str = "taiwan"):
    provider_name = (name or "taiwan").strip().lower()
    if provider_name == "taiwan":
        from app.taiwan.providers.hybrid_provider import TaiwanHybridProvider

        return TaiwanHybridProvider()
    from app.data_providers import custom

    if custom.is_custom_provider(provider_name):
        return custom.get_provider(provider_name)
    raise ValueError(f"Unsupported market data provider: {provider_name}")

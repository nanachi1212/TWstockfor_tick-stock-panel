"""Capabilities of the selected Taiwan or custom data providers (no network probes)."""
from __future__ import annotations

from app.capabilities import Cap, CapabilityLimits, CapabilitySet


def detect_capabilities(force: bool = False) -> CapabilitySet:
    """Describe implemented datasets without credentials, subscriptions or HTTP."""
    from app.data_providers import custom
    from app.services import preferences

    del force  # Kept for callers refreshing their process-local capability snapshot.
    capset = CapabilitySet()
    daily = preferences.get_daily_data_provider()
    sources = (
        (daily, "daily", (Cap.KLINE_DAILY_BY_SYMBOL, Cap.KLINE_DAILY_BATCH)),
        (preferences.get_realtime_data_provider(), "realtime",
         (Cap.QUOTE_BY_SYMBOL, Cap.QUOTE_BATCH, Cap.QUOTE_POOL)),
        (preferences.get_financial_provider(), "financial", (Cap.FINANCIAL,)),
        (preferences.get_minute_data_provider(), "minute",
         (Cap.KLINE_MINUTE_BY_SYMBOL, Cap.KLINE_MINUTE_BATCH, Cap.INTRADAY, Cap.INTRADAY_BATCH)),
        (daily if preferences.get_adj_factor_provider() == "same_as_daily"
         else preferences.get_adj_factor_provider(), "adj_factor", (Cap.ADJ_FACTOR,)),
    )
    batch_caps = {Cap.QUOTE_BATCH, Cap.KLINE_DAILY_BATCH, Cap.KLINE_MINUTE_BATCH, Cap.INTRADAY_BATCH}
    for provider, dataset, capabilities in sources:
        if provider == "taiwan":
            if dataset not in {"daily", "realtime", "financial"}:
                continue
            for capability in capabilities:
                capset.grant(capability, CapabilityLimits(batch=100 if capability in batch_caps else None))
            if dataset == "realtime":
                capset.grant(Cap.DEPTH5)
                capset.grant(Cap.DEPTH5_BATCH, CapabilityLimits(batch=100))
        elif custom.provider_has_dataset(provider, dataset):
            config = custom.get_provider(provider).config.datasets[dataset]
            for capability in capabilities:
                capset.grant(capability, CapabilityLimits(rpm=config.rpm, batch=config.batch))
    return capset


def tier_label() -> str:
    """Compatibility label for capability clients; there are no paid tiers."""
    return ""

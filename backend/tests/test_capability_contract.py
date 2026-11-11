"""Taiwan/custom capability contracts never invent removed SDK features."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.capabilities import Cap, CapabilityLimits, CapabilitySet, detect_capabilities


def test_official_mode_advertises_only_implemented_datasets(monkeypatch):
    from app.services import preferences
    monkeypatch.setattr(preferences, "load", lambda: {})
    caps = detect_capabilities()
    assert set(caps.all()) == {
        Cap.QUOTE_BY_SYMBOL, Cap.QUOTE_BATCH, Cap.QUOTE_POOL,
        Cap.KLINE_DAILY_BY_SYMBOL, Cap.KLINE_DAILY_BATCH,
        Cap.DEPTH5, Cap.DEPTH5_BATCH, Cap.FINANCIAL,
    }
    assert not caps.has(Cap.KLINE_MINUTE_BATCH)
    assert not caps.has(Cap.ADJ_FACTOR)
    assert not caps.has(Cap.WEBSOCKET)


def test_selected_custom_datasets_grant_their_declared_capabilities(monkeypatch):
    from app.data_providers import custom
    from app.services import preferences
    monkeypatch.setattr(preferences, "get_minute_data_provider", lambda: "custom_tw")
    monkeypatch.setattr(preferences, "get_adj_factor_provider", lambda: "custom_tw")
    monkeypatch.setattr(custom, "provider_has_dataset", lambda name, dataset: name == "custom_tw" and dataset in {"minute", "adj_factor"})
    limit = SimpleNamespace(rpm=30, batch=25)
    provider = SimpleNamespace(config=SimpleNamespace(datasets={"minute": limit, "adj_factor": limit}))
    monkeypatch.setattr(custom, "get_provider", lambda _: provider)
    caps = detect_capabilities()
    assert caps.has(Cap.KLINE_MINUTE_BATCH)
    assert caps.has(Cap.ADJ_FACTOR)
    assert caps.limits(Cap.KLINE_MINUTE_BATCH) == CapabilityLimits(rpm=30, batch=25)
    assert not caps.has(Cap.WEBSOCKET)


def test_missing_custom_dataset_is_not_advertised(monkeypatch):
    from app.data_providers import custom
    from app.services import preferences
    monkeypatch.setattr(preferences, "get_daily_data_provider", lambda: "custom_tw")
    monkeypatch.setattr(custom, "provider_has_dataset", lambda *_: False)
    assert not detect_capabilities().has(Cap.KLINE_DAILY_BATCH)


def test_grant_never_overrides_existing_limits():
    caps = CapabilitySet()
    caps.grant(Cap.KLINE_MINUTE_BATCH, CapabilityLimits(rpm=30, batch=100))
    caps.grant(Cap.KLINE_MINUTE_BATCH, CapabilityLimits(rpm=999, batch=999))
    assert caps.limits(Cap.KLINE_MINUTE_BATCH) == CapabilityLimits(rpm=30, batch=100)


def test_provider_settings_refresh_capabilities_and_reject_retired_provider(monkeypatch):
    from fastapi import HTTPException

    from app.api import settings as settings_api
    from app.services import preferences
    saved = []
    monkeypatch.setattr(preferences, "save", saved.append)
    monkeypatch.setattr(preferences, "_allowed_data_providers", lambda: {"taiwan", "mock_src"})
    sentinel = CapabilitySet()
    monkeypatch.setattr(settings_api, "detect_capabilities", lambda: sentinel)
    request = MagicMock()
    settings_api.update_data_providers(settings_api.DataProvidersIn(daily_data_provider="mock_src"), request)
    assert request.app.state.capabilities is sentinel
    assert saved == [{"daily_data_provider": "mock_src"}]
    with pytest.raises(HTTPException) as exc:
        settings_api.update_data_providers(settings_api.DataProvidersIn(daily_data_provider="tickflow"), request)
    assert exc.value.status_code == 400
    assert len(saved) == 1

"""Retired market data providers cannot be selected or recover stored credentials."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def test_registry_rejects_retired_or_unknown_provider_without_io(monkeypatch):
    from app.data_providers import custom
    from app.data_providers.registry import get_provider
    monkeypatch.setattr(custom, "is_custom_provider", lambda name: False)
    constructor = MagicMock()
    monkeypatch.setattr("app.taiwan.providers.hybrid_provider.TaiwanHybridProvider", constructor)
    for name in ("tickflow", "unknown", "akshare"):
        with pytest.raises(ValueError, match="Unsupported market data provider"):
            get_provider(name)
    constructor.assert_not_called()
    assert get_provider("taiwan") is constructor.return_value


def test_registry_preserves_explicit_custom_provider(monkeypatch):
    from app.data_providers import custom
    from app.data_providers.registry import get_provider
    provider = object()
    monkeypatch.setattr(custom, "is_custom_provider", lambda name: name == "custom_tw")
    monkeypatch.setattr(custom, "get_provider", lambda _: provider)
    assert get_provider("custom_tw") is provider


def test_legacy_preferences_are_read_without_mutating_user_data(monkeypatch):
    from app.services import preferences
    state = {key: "tickflow" for key in ("daily_data_provider", "minute_data_provider", "realtime_data_provider", "financial_data_provider", "adj_factor_provider")}
    monkeypatch.setattr(preferences, "load", lambda: state)
    write = MagicMock()
    monkeypatch.setattr(preferences, "save", write)
    for getter in (preferences.get_daily_data_provider, preferences.get_minute_data_provider,
                   preferences.get_realtime_data_provider, preferences.get_financial_provider):
        assert getter() == "taiwan"
    assert preferences.get_adj_factor_provider() == "same_as_daily"
    assert set(state.values()) == {"tickflow"}
    write.assert_not_called()


def test_retired_settings_routes_and_key_access_are_removed():
    from app import secrets_store
    from app.api.settings import router
    from app.config import Settings
    assert "tickflow_api_key" not in Settings.model_fields
    assert not hasattr(Settings, "use_free_mode")
    assert not hasattr(secrets_store, "get_tickflow_key")
    assert "/api/settings/tickflow-key" not in {route.path for route in router.routes}


def test_removing_custom_source_resets_all_selected_datasets(monkeypatch):
    from app.api import settings as settings_api
    from app.data_providers import custom
    from app.services import preferences
    state = {key: "custom_tw" for key in ("daily_data_provider", "minute_data_provider", "realtime_data_provider", "financial_data_provider", "adj_factor_provider")}
    monkeypatch.setattr(preferences, "load", lambda: state)
    writes = []
    monkeypatch.setattr(preferences, "save", writes.append)
    monkeypatch.setattr(custom, "delete_config", lambda _: None)
    monkeypatch.setattr(custom, "load_all", lambda: None)
    monkeypatch.setattr(settings_api, "list_data_sources", lambda: {})
    monkeypatch.setattr(settings_api, "detect_capabilities", lambda: object())
    settings_api.delete_data_source("custom_tw", MagicMock())
    assert writes == [{**{key: "taiwan" for key in state if key != "adj_factor_provider"}, "adj_factor_provider": "same_as_daily"}]


def test_settings_summary_has_no_retired_subscription_fields():
    from app.api.settings import get_settings
    result = get_settings()
    assert result["mode"] == "official"
    assert not ({"tickflow_api_key_masked", "has_tickflow_key", "tier_label", "current_endpoint",
                 "probe_log", "missing_caps", "extras_caps"} & result.keys())


def test_legacy_key_environment_is_ignored(tmp_path):
    from app.config import Settings
    env = tmp_path / "legacy.env"
    env.write_text("TICKFLOW_API_KEY=retired-test-value\n", encoding="utf-8")
    result = Settings(_env_file=env)
    assert "tickflow_api_key" not in result.model_dump()

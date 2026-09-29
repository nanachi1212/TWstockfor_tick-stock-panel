"""Tests for AI key profile config retrieval with base_url and model fields."""
from __future__ import annotations

import json
from unittest import mock

import pytest


def test_create_profile_stores_base_url_and_model(tmp_path):
    import app.services.ai_key_profiles as mod

    meta_path = tmp_path / "profiles.json"
    secrets_path = tmp_path / "secrets.json"

    with (
        mock.patch.object(mod, "_profiles_path", lambda: meta_path),
        mock.patch.object(mod, "_secrets_path", lambda: secrets_path),
    ):
        result = mod.create_profile(
            name="test-agnes",
            provider="openai_compat",
            api_key="free-key-abc",
            base_url="https://api.agnai.chat/v1",
            model="agnes-2.5-flash",
        )

    assert result["base_url"] == "https://api.agnai.chat/v1"
    assert result["model"] == "agnes-2.5-flash"
    assert result["has_key"] is True

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta[0]["base_url"] == "https://api.agnai.chat/v1"
    assert meta[0]["model"] == "agnes-2.5-flash"


def test_get_active_profile_config_returns_complete_config(tmp_path):
    import app.services.ai_key_profiles as mod

    meta_path = tmp_path / "profiles.json"
    secrets_path = tmp_path / "secrets.json"

    with (
        mock.patch.object(mod, "_profiles_path", lambda: meta_path),
        mock.patch.object(mod, "_secrets_path", lambda: secrets_path),
    ):
        mod.create_profile(
            name="agnes",
            provider="openai_compat",
            api_key="free-key-xyz",
            base_url="https://api.agnai.chat/v1",
            model="agnes-2.5-flash",
        )
        cfg = mod.get_active_profile_config()

    assert cfg is not None
    assert cfg["key"] == "free-key-xyz"
    assert cfg["base_url"] == "https://api.agnai.chat/v1"
    assert cfg["model"] == "agnes-2.5-flash"
    assert cfg["provider"] == "openai_compat"


def test_get_active_profile_config_returns_none_when_no_profiles(tmp_path):
    import app.services.ai_key_profiles as mod

    meta_path = tmp_path / "profiles_empty.json"
    secrets_path = tmp_path / "secrets_empty.json"

    with (
        mock.patch.object(mod, "_profiles_path", lambda: meta_path),
        mock.patch.object(mod, "_secrets_path", lambda: secrets_path),
    ):
        cfg = mod.get_active_profile_config()

    assert cfg is None


def test_list_profiles_includes_base_url_and_model(tmp_path):
    import app.services.ai_key_profiles as mod

    meta_path = tmp_path / "lp_profiles.json"
    secrets_path = tmp_path / "lp_secrets.json"

    with (
        mock.patch.object(mod, "_profiles_path", lambda: meta_path),
        mock.patch.object(mod, "_secrets_path", lambda: secrets_path),
    ):
        mod.create_profile(
            name="glm",
            provider="openai_compat",
            api_key="glm-key-123",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            model="glm-4-flash",
        )
        profiles = mod.list_profiles()

    assert len(profiles) == 1
    p = profiles[0]
    assert p["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
    assert p["model"] == "glm-4-flash"
    assert "key" not in p
    assert p["key_masked"]


def test_legacy_profile_missing_base_url_defaults_to_empty(tmp_path):
    """Profiles created before base_url/model fields were added still work."""
    import app.services.ai_key_profiles as mod

    meta_path = tmp_path / "legacy_profiles.json"
    secrets_path = tmp_path / "legacy_secrets.json"

    meta_path.write_text(json.dumps([
        {"id": "aip_legacy001", "name": "legacy", "provider": "openai_compat", "active": True, "created_at": "2024-01-01T00:00:00+00:00"}
    ]), encoding="utf-8")
    secrets_path.write_text(json.dumps({"aip_legacy001": "legacy-key"}), encoding="utf-8")

    with (
        mock.patch.object(mod, "_profiles_path", lambda: meta_path),
        mock.patch.object(mod, "_secrets_path", lambda: secrets_path),
    ):
        cfg = mod.get_active_profile_config()

    assert cfg is not None
    assert cfg["key"] == "legacy-key"
    assert cfg["base_url"] == ""
    assert cfg["model"] == ""


def test_two_profiles_switching_updates_active_config(tmp_path):
    """Switching active profile from A to B completely switches base_url, model, key."""
    import app.services.ai_key_profiles as mod
    from app.services.ai_provider import current_openai_model, snapshot_ai_provider_config

    meta_path = tmp_path / "dual_profiles.json"
    secrets_path = tmp_path / "dual_secrets.json"

    with (
        mock.patch.object(mod, "_profiles_path", lambda: meta_path),
        mock.patch.object(mod, "_secrets_path", lambda: secrets_path),
    ):
        # Create Profile A (Agnes)
        mod.create_profile(
            name="Agnes Profile",
            provider="openai_compat",
            api_key="agnes-secret-key-111",
            base_url="https://api.agnai.chat/v1",
            model="agnes-2.5-flash",
        )
        # Create Profile B (GLM)
        prof_b = mod.create_profile(
            name="GLM Profile",
            provider="openai_compat",
            api_key="glm-secret-key-222",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            model="glm-4-flash",
        )

        # Profile A should be active by default (first profile)
        cfg_a = snapshot_ai_provider_config()
        assert cfg_a.base_url == "https://api.agnai.chat/v1"
        assert cfg_a.model == "agnes-2.5-flash"
        assert cfg_a.api_key == "agnes-secret-key-111"
        assert current_openai_model() == "agnes-2.5-flash"

        # Switch to Profile B
        assert mod.set_active_profile(prof_b["id"]) is True

        cfg_b = snapshot_ai_provider_config()
        assert cfg_b.base_url == "https://open.bigmodel.cn/api/paas/v4"
        assert cfg_b.model == "glm-4-flash"
        assert cfg_b.api_key == "glm-secret-key-222"
        assert current_openai_model() == "glm-4-flash"


@pytest.mark.asyncio
async def test_test_ai_key_profile_uses_profile_specific_endpoint(tmp_path):
    """Settings test endpoint uses profile's own base_url, model, and key."""
    import app.services.ai_key_profiles as mod
    from app.api.settings import test_ai_key_profile

    meta_path = tmp_path / "test_profiles.json"
    secrets_path = tmp_path / "test_secrets.json"

    with (
        mock.patch.object(mod, "_profiles_path", lambda: meta_path),
        mock.patch.object(mod, "_secrets_path", lambda: secrets_path),
    ):
        mod.create_profile(
            name="Active Agnes",
            provider="openai_compat",
            api_key="active-secret-key",
            base_url="https://active.example/v1",
            model="active-model",
        )
        prof = mod.create_profile(
            name="Inactive GLM",
            provider="openai_compat",
            api_key="glm-secret-key-999",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            model="glm-4.7",
        )

        with mock.patch("app.services.ai_provider.probe_openai_profile_connection") as mock_test:
            mock_test.return_value = {
                "ok": True, "responded": True, "http_status": 200,
                "finish_reason": "length", "output_tokens": 32,
            }
            res = await test_ai_key_profile(prof["id"])
            assert res["ok"] is True
            assert res["responded"] is True

            cfg = mock_test.call_args.args[0]
            assert cfg.base_url == "https://open.bigmodel.cn/api/paas/v4"
            assert cfg.model == "glm-4.7"
            assert cfg.api_key == "glm-secret-key-999"


def test_active_profile_does_not_fall_back_to_legacy_endpoint_or_model(tmp_path):
    from app import secrets_store
    import app.services.ai_key_profiles as mod
    from app.services.ai_provider import snapshot_ai_provider_config

    meta_path = tmp_path / "authoritative_profiles.json"
    secrets_path = tmp_path / "authoritative_secrets.json"
    legacy_path = tmp_path / "legacy_secrets.json"
    with (
        mock.patch.object(mod, "_profiles_path", lambda: meta_path),
        mock.patch.object(mod, "_secrets_path", lambda: secrets_path),
        mock.patch.object(secrets_store, "_path", lambda: legacy_path),
    ):
        secrets_store.save({
            "ai_api_key": "legacy-key",
            "ai_base_url": "https://legacy.example/v1",
            "ai_model": "legacy-model",
        })
        mod.create_profile(
            name="Profile",
            provider="openai_compat",
            api_key="profile-key",
            base_url="",
            model="",
        )
        cfg = snapshot_ai_provider_config()

    assert cfg.api_key == "profile-key"
    assert cfg.base_url == ""
    assert cfg.model == ""


@pytest.mark.asyncio
async def test_screener_nl_uses_active_profile_config(tmp_path):
    """Natural Language Screener uses active profile config without endpoint mismatch."""
    import app.services.ai_key_profiles as mod
    from app.taiwan.screener_nl import TaiwanScreenerTranslator

    meta_path = tmp_path / "nl_profiles.json"
    secrets_path = tmp_path / "nl_secrets.json"

    with (
        mock.patch.object(mod, "_profiles_path", lambda: meta_path),
        mock.patch.object(mod, "_secrets_path", lambda: secrets_path),
    ):
        mod.create_profile(
            name="Agnes NL",
            provider="openai_compat",
            api_key="agnes-nl-key",
            base_url="https://api.agnai.chat/v1",
            model="agnes-flash",
        )

        translator = TaiwanScreenerTranslator()
        fake_ai_resp = json.dumps({
            "request_fields": {"price_max": 100.0},
            "recognized_conditions": ["股價低於100元"],
            "unsupported_conditions": [],
            "clarification_needed": False,
        })

        with mock.patch("app.taiwan.screener_nl.generate_ai_text") as mock_gen:
            mock_gen.return_value = fake_ai_resp
            resp = await translator.translate("股價低於100元")
            assert resp.request is not None
            assert resp.request.price_max == 100.0
            assert resp.clarification_needed is False

            cfg = mock_gen.call_args.kwargs["config_snapshot"]
            assert cfg.base_url == "https://api.agnai.chat/v1"
            assert cfg.model == "agnes-flash"
            assert cfg.api_key == "agnes-nl-key"

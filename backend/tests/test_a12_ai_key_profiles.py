from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.settings import (
    AiKeyProfileCreate,
    activate_ai_key_profile,
    create_ai_key_profile,
    delete_ai_key_profile,
    get_settings,
    list_ai_key_profiles,
)
from app.services import ai_key_profiles


@pytest.fixture(autouse=True)
def clean_profiles(tmp_path, monkeypatch):
    """Isolate profiles store to a temporary directory."""
    meta_path = tmp_path / "ai_key_profiles.json"
    secrets_path = tmp_path / "ai_key_profiles_secrets.json"

    monkeypatch.setattr(ai_key_profiles, "_profiles_path", lambda: meta_path)
    monkeypatch.setattr(ai_key_profiles, "_secrets_path", lambda: secrets_path)

    # Clean secrets_store
    from app import secrets_store
    sec_path = tmp_path / "secrets.json"
    monkeypatch.setattr(secrets_store, "_path", lambda: sec_path)



def test_ai_key_profiles_crud_and_masking():
    # 1. Initially empty
    assert ai_key_profiles.list_profiles() == []
    assert ai_key_profiles.get_active_ai_key() == ""
    assert ai_key_profiles.get_active_profile_id() is None

    # 2. Create first profile (should be auto-active)
    p1 = ai_key_profiles.create_profile(
        name="Free Key 1",
        provider="openai_compat",
        api_key="sk-test-secret-key-12345678",
    )
    assert p1["name"] == "Free Key 1"
    assert p1["active"] is True
    assert p1["key_masked"].startswith("sk-t")
    assert p1["key_masked"].endswith("5678")
    assert "secret-key" not in p1["key_masked"]  # Masked!


    # Verify active resolution
    assert ai_key_profiles.get_active_ai_key() == "sk-test-secret-key-12345678"
    assert ai_key_profiles.get_active_profile_id() == p1["id"]

    # 3. Create second profile (should NOT be auto-active)
    p2 = ai_key_profiles.create_profile(
        name="Free Key 2",
        provider="openai",
        api_key="sk-another-secret-87654321",
    )
    assert p2["active"] is False

    # List shows 2 profiles, only p1 active
    profiles = ai_key_profiles.list_profiles()
    assert len(profiles) == 2
    assert profiles[0]["id"] == p1["id"] and profiles[0]["active"] is True
    assert profiles[1]["id"] == p2["id"] and profiles[1]["active"] is False

    # 4. Switch active to p2
    ok = ai_key_profiles.set_active_profile(p2["id"])
    assert ok is True
    assert ai_key_profiles.get_active_ai_key() == "sk-another-secret-87654321"
    assert ai_key_profiles.get_active_profile_id() == p2["id"]

    # 5. Delete active profile p2 -> fallback to p1 as active
    del_ok = ai_key_profiles.delete_profile(p2["id"])
    assert del_ok is True
    remaining = ai_key_profiles.list_profiles()
    assert len(remaining) == 1
    assert remaining[0]["id"] == p1["id"]
    assert remaining[0]["active"] is True
    assert ai_key_profiles.get_active_ai_key() == "sk-test-secret-key-12345678"


def test_secrets_store_fallback_behavior(tmp_path, monkeypatch):
    """secrets_store.get_ai_key() prefers active profile, falls back to legacy field."""
    from app import secrets_store

    # Case A: No profile, no legacy field -> empty
    assert secrets_store.get_ai_key() == ""

    # Case B: No profile, legacy field exists -> uses legacy
    secrets_store.save({"ai_api_key": "sk-legacy-key-9999"})
    assert secrets_store.get_ai_key() == "sk-legacy-key-9999"

    # Case C: Profile exists -> overrides legacy
    ai_key_profiles.create_profile(
        name="Profile Key",
        provider="openai_compat",
        api_key="sk-profile-override-1111",
    )
    assert secrets_store.get_ai_key() == "sk-profile-override-1111"


def test_api_endpoints_ai_key_profiles(tmp_path, monkeypatch):
    """Verify FastAPI routes for AI key profiles."""
    # List empty
    res = list_ai_key_profiles()
    assert res == {"profiles": []}

    # Create profile
    req = AiKeyProfileCreate(name="My Key", provider="openai_compat", api_key="sk-api-test-key")
    res_create = create_ai_key_profile(req)
    assert res_create["ok"] is True
    assert bool(res_create["profile"]["id"])
    assert res_create["profile"]["active"] is True


    # Check GET /api/settings includes profiles count and active name
    settings_res = get_settings()
    assert settings_res["ai_key_profiles_count"] == 1
    assert settings_res["ai_key_active_profile_name"] == "My Key"

    # Create second profile and activate
    req2 = AiKeyProfileCreate(name="Second Key", provider="openai", api_key="sk-api-test-key-2")
    res_create2 = create_ai_key_profile(req2)
    pid2 = res_create2["profile"]["id"]

    act_res = activate_ai_key_profile(pid2)
    assert act_res["ok"] is True
    assert act_res["active_profile_id"] == pid2

    settings_res2 = get_settings()
    assert settings_res2["ai_key_active_profile_name"] == "Second Key"

    # Delete
    del_res = delete_ai_key_profile(pid2)
    assert del_res["ok"] is True

    # 404 on deleting non-existent
    with pytest.raises(HTTPException) as exc:
        delete_ai_key_profile("non_existent_id")
    assert exc.value.status_code == 404

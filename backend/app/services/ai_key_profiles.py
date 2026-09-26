"""AI Key Profile Store — multiple named API key profiles for AI providers.

Data layout:
  - Metadata (names, providers, active flag):
      data/user_data/ai_key_profiles.json
  - Actual keys (one entry per profile id):
      data/user_data/ai_key_profiles_secrets.json  (0600)

The active profile's key is resolved by get_active_ai_key() and used by
snapshot_ai_provider_config() / get_ai_key() instead of the legacy single-key
ai_api_key field. Legacy ai_api_key continues to work when no profiles exist
(backward-compatible upgrade path).

Security invariants:
  - Key values are NEVER returned to the frontend.  The frontend receives only
    masked keys, names, provider labels, active flag, and created_at.
  - Metadata is stored separately from key material to reduce blast radius.
  - Both files are created with 0600 permissions.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_lock = threading.Lock()

_VALID_CHANNELS = frozenset(("line", "telegram"))


# ── Path helpers ────────────────────────────────────────────────────────────

def _profiles_path() -> Path:
    from app.config import settings
    p = settings.data_dir / "user_data" / "ai_key_profiles.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _secrets_path() -> Path:
    from app.config import settings
    p = settings.data_dir / "user_data" / "ai_key_profiles_secrets.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _write_json(path: Path, data: Any, mode: int = 0o600) -> None:
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        with contextlib.suppress(OSError):
            os.chmod(path, mode)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("ai_key_profiles read failed (%s): %s", path.name, e)
        return default



# ── Internal data structures ────────────────────────────────────────────────

def _load_metadata() -> list[dict]:
    return _read_json(_profiles_path(), [])


def _save_metadata(profiles: list[dict]) -> None:
    _write_json(_profiles_path(), profiles)


def _load_secrets() -> dict[str, str]:
    return _read_json(_secrets_path(), {})


def _save_secrets(secrets: dict[str, str]) -> None:
    _write_json(_secrets_path(), secrets)


# ── Public API ───────────────────────────────────────────────────────────────

def mask(key: str, prefix: int = 4, suffix: int = 4) -> str:
    """Mask key for display — never return the real value to the frontend."""
    if not key:
        return ""
    if len(key) <= prefix + suffix:
        return "•" * len(key)
    return f"{key[:prefix]}{'•' * 6}{key[-suffix:]}"


def list_profiles() -> list[dict]:
    """Return profile metadata list (no key values)."""
    with _lock:
        profiles = _load_metadata()
        secrets = _load_secrets()
        result = []
        for p in profiles:
            pid = p.get("id", "")
            key_raw = secrets.get(pid, "")
            result.append({
                "id": pid,
                "name": p.get("name", ""),
                "provider": p.get("provider", "openai_compat"),
                "base_url": p.get("base_url", ""),
                "model": p.get("model", ""),
                "key_masked": mask(key_raw),
                "has_key": bool(key_raw),
                "active": bool(p.get("active", False)),
                "created_at": p.get("created_at", ""),
            })
        return result


def get_active_profile_id() -> str | None:
    """Return the id of the currently active profile, or None."""
    with _lock:
        for p in _load_metadata():
            if p.get("active"):
                return str(p["id"])
    return None


def get_active_ai_key() -> str:
    """Return the API key of the active profile, or empty string."""
    with _lock:
        profiles = _load_metadata()
        secrets = _load_secrets()
        for p in profiles:
            if p.get("active"):
                return str(secrets.get(p.get("id", ""), ""))
        return ""


def get_active_provider() -> str | None:
    """Return the provider string of the active profile, or None."""
    with _lock:
        for p in _load_metadata():
            if p.get("active"):
                return str(p.get("provider", "openai_compat"))
    return None


def get_active_profile_config() -> dict | None:
    """Return complete config of the active profile: {key, base_url, model, provider}.

    Returns None if no active profile exists. Falls back to legacy storage if
    profile lacks base_url/model (profiles created before this field was added).
    """
    with _lock:
        profiles = _load_metadata()
        secrets = _load_secrets()
        for p in profiles:
            if p.get("active"):
                pid = str(p.get("id", ""))
                return {
                    "key": str(secrets.get(pid, "")),
                    "provider": str(p.get("provider", "openai_compat")),
                    "base_url": str(p.get("base_url", "")),
                    "model": str(p.get("model", "")),
                }
    return None


def create_profile(name: str, provider: str, api_key: str, *, base_url: str = "", model: str = "") -> dict:
    """Create a new profile. Returns metadata (no key)."""
    if not name.strip():
        raise ValueError("Profile name cannot be empty")
    if not api_key.strip():
        raise ValueError("API key cannot be empty")
    valid_providers = {"openai_compat", "openai", "codex_cli"}
    if provider not in valid_providers:
        provider = "openai_compat"

    with _lock:
        profiles = _load_metadata()
        secrets = _load_secrets()

        pid = f"aip_{uuid.uuid4().hex[:12]}"
        now = datetime.now(tz=UTC).isoformat()
        profile = {
            "id": pid,
            "name": name.strip()[:64],
            "provider": provider,
            "base_url": base_url.strip(),
            "model": model.strip(),
            "active": len(profiles) == 0,  # first profile auto-activated
            "created_at": now,
        }
        profiles.append(profile)
        secrets[pid] = api_key.strip()

        _save_metadata(profiles)
        _save_secrets(secrets)

        return {
            "id": pid,
            "name": profile["name"],
            "provider": profile["provider"],
            "base_url": profile["base_url"],
            "model": profile["model"],
            "key_masked": mask(api_key),
            "has_key": True,
            "active": profile["active"],
            "created_at": now,
        }


def set_active_profile(profile_id: str) -> bool:
    """Activate a profile by id. Deactivates all others. Returns True if found."""
    with _lock:
        profiles = _load_metadata()
        found = any(p.get("id") == profile_id for p in profiles)
        if not found:
            return False
        for p in profiles:
            p["active"] = (p.get("id") == profile_id)
        _save_metadata(profiles)
        return True


def delete_profile(profile_id: str) -> bool:
    """Delete a profile and its key. Returns True if deleted."""
    with _lock:
        profiles = _load_metadata()
        new_profiles = [p for p in profiles if p.get("id") != profile_id]
        if len(new_profiles) == len(profiles):
            return False

        # If deleted profile was active, activate first remaining profile
        was_active = any(p.get("id") == profile_id and p.get("active") for p in profiles)
        if was_active and new_profiles:
            new_profiles[0]["active"] = True

        secrets = _load_secrets()
        secrets.pop(profile_id, None)

        _save_metadata(new_profiles)
        _save_secrets(secrets)
        return True


def has_any_profile() -> bool:
    """True if at least one profile exists."""
    return bool(_load_metadata())

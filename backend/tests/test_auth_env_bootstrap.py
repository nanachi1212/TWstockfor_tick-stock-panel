from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from app import config as app_config
from app.config import Settings


@pytest.fixture(autouse=True)
def isolated_auth_store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[tuple[ModuleType, Path, Path]]:
    monkeypatch.setattr(app_config.settings, "data_dir", tmp_path)
    from app.services import auth

    auth_path = tmp_path / "user_data" / "auth.json"
    env_path = tmp_path / ".env"
    monkeypatch.setattr(app_config, "_ENV_FILE", env_path)
    monkeypatch.setattr(app_config.settings, "auth_password", "")
    auth._sessions.clear()
    auth._configured_cache = None
    yield auth, auth_path, env_path
    auth._sessions.clear()
    auth._configured_cache = None


def test_bootstrap_recovers_compose_interpolated_password_from_raw_env(
    isolated_auth_store: tuple[ModuleType, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    auth, auth_path, env_path = isolated_auth_store
    password = "pw${special}-secret"
    env_path.write_text(
        f"AUTH_PASSWORD={password}\nDATA_DIR={tmp_path}\n",
        encoding="utf-8",
    )
    configured = Settings(_env_file=env_path)
    configured.auth_password = "pw-secret"  # 模拟 Compose 将未定义的 ${special} 插值为空串
    monkeypatch.setattr(app_config, "settings", configured)

    assert auth.bootstrap_from_env() is True
    assert auth_path.exists()
    assert password not in auth_path.read_text(encoding="utf-8")
    assert auth.verify_and_create_session(password) is not None
    assert auth.verify_and_create_session("pw-secret") is None


def test_bootstrap_from_env_does_not_override_existing_password(
    isolated_auth_store: tuple[ModuleType, Path, Path],
) -> None:
    auth, auth_path, env_path = isolated_auth_store
    auth.set_password("web-managed-secret")
    before = auth_path.read_bytes()
    env_path.write_text("AUTH_PASSWORD=replacement-secret\n", encoding="utf-8")
    app_config.settings.auth_password = "replacement-secret"

    assert auth.bootstrap_from_env() is False
    assert auth_path.read_bytes() == before
    assert auth.verify_and_create_session("web-managed-secret") is not None
    assert auth.verify_and_create_session("replacement-secret") is None


@pytest.mark.parametrize("broken", ['{broken', '{}', '[]', '{"sessions": {}}'])
def test_broken_auth_never_becomes_first_setup(isolated_auth_store, broken):
    auth, path, _ = isolated_auth_store
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(broken, encoding="utf-8")
    with pytest.raises(auth.AuthStoreError):
        auth.is_configured()
    with pytest.raises(auth.AuthStoreError):
        auth.set_password("replacement-secret")
    assert path.read_text(encoding="utf-8") == broken


def test_failed_password_write_preserves_file_and_session(isolated_auth_store, monkeypatch):
    auth, path, _ = isolated_auth_store
    auth.set_password("original-secret")
    token = auth.verify_and_create_session("original-secret")
    before = path.read_bytes()

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(auth.os, "replace", fail)
    with pytest.raises(auth.AuthStoreError):
        auth.set_password("replacement-secret")
    assert path.read_bytes() == before
    assert auth.is_valid_session(token)


def test_cached_auth_rechecks_changed_file(isolated_auth_store):
    auth, path, _ = isolated_auth_store
    auth.set_password("original-secret")
    assert auth.is_configured()
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(auth.AuthStoreError):
        auth.is_configured()


def test_broken_auth_api_is_unavailable_not_first_setup(isolated_auth_store):
    import asyncio

    from httpx import ASGITransport, AsyncClient

    from app.main import app

    _, path, _ = isolated_auth_store
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")

    async def check():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for method, url, body in [
                ("GET", "/api/auth/status", None),
                ("POST", "/api/auth/setup", {"password": "new-secret"}),
                ("GET", "/api/watchlist", None),
            ]:
                response = await client.request(method, url, json=body)
                assert response.status_code == 503
                assert response.json()["code"] == "AUTH_STORE_UNAVAILABLE"
    asyncio.run(check())
    assert path.read_text(encoding="utf-8") == "{broken"

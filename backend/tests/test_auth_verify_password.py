"""驗證專用密碼: 只在本機登入時接受, 與主密碼各自管理。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config as app_config

MAIN = "main-secret-123"
VERIFY = "verify-secret-0123456789"
LOCAL = {"origin": "http://127.0.0.1:3018"}


@pytest.fixture(autouse=True)
def auth_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(app_config.settings, "data_dir", tmp_path)
    monkeypatch.setattr(app_config.settings, "auth_password", "")
    from app.api import auth as auth_api
    from app.services import auth

    auth._sessions.clear()
    auth._configured_cache = None
    auth_api._fail_counter.clear()
    auth.set_password(MAIN)
    auth.set_verify_password(VERIFY)
    yield auth
    auth._sessions.clear()
    auth._configured_cache = None
    auth_api._fail_counter.clear()


def _client() -> TestClient:
    from app.main import app

    return TestClient(app, base_url="http://127.0.0.1:3018", client=("127.0.0.1", 50000))


def test_local_login_accepts_verify_password():
    response = _client().post("/api/auth/login", json={"password": VERIFY}, headers=LOCAL)
    assert response.status_code == 200
    assert response.cookies.get("tf_session")


def test_tailscale_forwarded_login_rejects_verify_password():
    # tailscale serve 以本機身分轉入, 但 X-Forwarded-For 帶 tailnet 位址 (100.64.0.0/10)。
    headers = {"x-forwarded-for": "100.101.102.103", "origin": "https://node.example.ts.net"}
    client = _client()
    assert client.post("/api/auth/login", json={"password": VERIFY}, headers=headers).status_code == 401
    assert client.post("/api/auth/login", json={"password": MAIN}, headers=headers).status_code == 200


def test_cross_site_login_rejects_verify_password():
    headers = {"origin": "https://attacker.example"}
    assert _client().post("/api/auth/login", json={"password": VERIFY}, headers=headers).status_code == 401


def test_changing_main_password_keeps_verify_password(auth_store):
    auth_store.set_password("new-main-secret")
    assert auth_store.verify_and_create_session(VERIFY, allow_verify=True)
    assert auth_store.verify_and_create_session(MAIN) is None


def test_change_password_does_not_accept_verify_password_as_old():
    client = _client()
    assert client.post("/api/auth/login", json={"password": VERIFY}, headers=LOCAL).status_code == 200
    response = client.post(
        "/api/auth/change-password", json={"old_password": VERIFY, "new_password": "hijacked-123"}, headers=LOCAL
    )
    assert response.status_code == 401


def test_cleared_verify_password_is_rejected(auth_store):
    auth_store.set_verify_password(None)
    assert _client().post("/api/auth/login", json={"password": VERIFY}, headers=LOCAL).status_code == 401

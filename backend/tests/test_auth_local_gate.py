"""未設密碼時的本機/內網放行閘門: 防 CSRF、DNS rebinding 與 X-Forwarded-For 偽造。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import config as app_config
from app.api import auth as auth_api

_PROBE = "/api/__auth_gate_probe__"  # 不存在的路由: 過閘 → 404/405, 被攔 → 403


@pytest.fixture(autouse=True)
def no_password(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(app_config.settings, "data_dir", tmp_path)
    monkeypatch.setattr(app_config.settings, "auth_password", "")
    from app.services import auth

    auth._sessions.clear()
    auth._configured_cache = None
    yield
    auth._sessions.clear()
    auth._configured_cache = None


def _client(peer: str = "127.0.0.1") -> TestClient:
    from app.main import app

    return TestClient(app, base_url="http://127.0.0.1:3018", client=(peer, 50000))


@pytest.mark.parametrize(
    "headers",
    [
        {"sec-fetch-site": "cross-site"},
        {"sec-fetch-site": "same-site", "origin": "http://localhost:8080"},
        {"origin": "https://attacker.example"},
        {"origin": "null"},
        # DNS rebinding: Origin 與 Host 同為攻擊者網域
        {"host": "attacker.example:3018", "origin": "http://attacker.example:3018"},
        {"host": "evil_x.example:3018"},  # Starlette host 正則不接受的寫法也須按原始頭判斷
    ],
)
def test_cross_site_and_rebinding_are_rejected(headers):
    response = _client().delete(_PROBE, headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "NOT_INITIALIZED"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"sec-fetch-site": "same-origin", "origin": "http://127.0.0.1:3018"},
        {"sec-fetch-site": "none"},
        {"host": "mypc.home:3018", "origin": "http://mypc.home:3018"},
        {"host": "mypc.localdomain:3018", "sec-fetch-site": "same-origin"},
        {"host": "nas.local", "origin": "http://nas.local"},
        {"host": "[::1]:3018", "origin": "http://[::1]:3018"},
        # 反向代理改寫 Host($proxy_host), 瀏覽器 Origin 是內網地址
        {"origin": "http://192.168.1.5"},
    ],
)
def test_local_same_origin_requests_pass(headers):
    assert _client().delete(_PROBE, headers=headers).status_code in (404, 405)


def test_multi_line_xff_cannot_spoof_local():
    # 反向代理(HAProxy 等)另起一行追加真實客戶端; 攻擊者自填的第一行不能被單獨採信
    for peer in ("127.0.0.1", "172.17.0.1"):
        client = _client(peer)
        headers = [("x-forwarded-for", "127.0.0.1"), ("x-forwarded-for", "203.0.113.9")]
        assert client.delete(_PROBE, headers=headers).status_code == 403
        assert client.post("/api/auth/setup", json={"password": "secret123"}, headers=headers).status_code == 403


def test_setup_rejects_cross_site_and_accepts_local():
    client = _client()
    attacked = client.post(
        "/api/auth/setup", json={"password": "secret123"}, headers={"origin": "https://attacker.example"}
    )
    assert attacked.status_code == 403
    ok = client.post("/api/auth/setup", json={"password": "secret123"}, headers={"origin": "http://127.0.0.1:3018"})
    assert ok.status_code == 200


def _request(peer: str, xff: list[str]) -> Request:
    headers = [(b"x-forwarded-for", value.encode()) for value in xff]
    return Request({"type": "http", "headers": headers, "client": (peer, 1)})


def test_client_ip_uses_rightmost_public_hop_from_trusted_proxy():
    # 公開網路客戶端在左側自填 IP 不能改變限流鍵
    assert auth_api._client_ip(_request("127.0.0.1", ["1.2.3.4, 198.51.100.7"])) == "198.51.100.7"
    # 兩層反向代理: 跳過內網反向代理跳, 每個公開網路客戶端仍有自己的限流鍵
    assert auth_api._client_ip(_request("127.0.0.1", ["198.51.100.7, 172.18.0.2"])) == "198.51.100.7"
    assert auth_api._client_ip(_request("127.0.0.1", ["203.0.113.9, 172.18.0.2"])) == "203.0.113.9"
    # 全為內網跳: 取最左
    assert auth_api._client_ip(_request("127.0.0.1", ["192.168.1.66, 172.18.0.2"])) == "192.168.1.66"
    assert auth_api._client_ip(_request("127.0.0.1", ["127.0.0.1", "203.0.113.9"])) == "203.0.113.9"
    # 公開網路直連: XFF 一律忽略
    assert auth_api._client_ip(_request("203.0.113.9", ["127.0.0.1"])) == "203.0.113.9"
    # 多層本機反向代理: 任一跳為公開網路即非本機
    assert not auth_api._is_local_request(_request("127.0.0.1", ["203.0.113.9, 172.18.0.2"]))
    assert auth_api._is_local_request(_request("127.0.0.1", ["192.168.1.66"]))


def test_cors_wildcard_is_not_served():
    response = _client().get("/health", headers={"origin": "https://attacker.example"})
    assert "access-control-allow-origin" not in response.headers

"""前端 dist 靜態檔服務: 根目錄實體檔原樣回傳, 其餘路徑回退 index.html, 不得逃出 dist。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import _mount_frontend, _resolve_static_file

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
INDEX_HTML = "<!doctype html><title>spa shell</title>"
SECRET = "outside-static-root"


@pytest.fixture
def static_root(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "icons").mkdir()
    (root / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (root / "assets" / "index-abc123.js").write_text("console.log('app')", encoding="utf-8")
    (root / "icons" / "icon-192.png").write_bytes(PNG_BYTES)
    (root / "morandi-app-icon.png").write_bytes(PNG_BYTES)
    (root / "manifest.webmanifest").write_text('{"name": "台股看板"}', encoding="utf-8")
    (root / "sw.js").write_text("self.addEventListener('fetch', () => {})", encoding="utf-8")
    (tmp_path / "secret.txt").write_text(SECRET, encoding="utf-8")
    return root


@pytest.fixture
def client(static_root: Path) -> TestClient:
    app = FastAPI()
    _mount_frontend(app, static_root)
    return TestClient(app)


def _assert_index(response) -> None:
    assert response.status_code == 200
    assert response.text == INDEX_HTML
    assert response.headers["cache-control"] == "no-store, must-revalidate"


def test_root_and_nested_png_are_served_as_images(client: TestClient):
    for path in ("/morandi-app-icon.png", "/icons/icon-192.png"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["content-type"] == "image/png", path
        assert response.content == PNG_BYTES, path
        assert "cache-control" not in response.headers, path


def test_manifest_and_service_worker_revalidate_every_time(client: TestClient):
    manifest = client.get("/manifest.webmanifest")
    assert manifest.status_code == 200
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    assert manifest.headers["cache-control"] == "no-cache"
    assert manifest.json() == {"name": "台股看板"}

    worker = client.get("/sw.js")
    assert worker.status_code == 200
    assert "javascript" in worker.headers["content-type"]
    assert worker.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize(
    "path",
    ["/", "/index.html", "/watchlist", "/some/spa/route", "/missing.png", "/icons", "/api/unknown"],
)
def test_spa_routes_and_non_files_fall_back_to_index(client: TestClient, path: str):
    _assert_index(client.get(path))


@pytest.mark.parametrize(
    "path",
    ["/..%2fsecret.txt", "/icons/..%2f..%2fsecret.txt", "/%2e%2e%2fsecret.txt", "/..%5csecret.txt"],
)
def test_traversal_never_escapes_static_root(client: TestClient, path: str):
    response = client.get(path)
    assert SECRET not in response.text
    _assert_index(response)


def test_assets_mount_is_unchanged(client: TestClient):
    asset = client.get("/assets/index-abc123.js")
    assert asset.status_code == 200
    assert asset.text == "console.log('app')"
    assert client.get("/assets/missing.js").status_code == 404


def test_resolve_static_file_rejects_paths_outside_root(static_root: Path, tmp_path: Path):
    assert _resolve_static_file(static_root, "icons/icon-192.png") == (static_root / "icons" / "icon-192.png").resolve()
    for full_path in (
        "../secret.txt",
        "icons/../../secret.txt",
        str(tmp_path / "secret.txt"),
        "//evil-host/share/secret.txt",
        "icons",
        "missing.png",
        "bad\x00name",
    ):
        assert _resolve_static_file(static_root, full_path) is None, repr(full_path)

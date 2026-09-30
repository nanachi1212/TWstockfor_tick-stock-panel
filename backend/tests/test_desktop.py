from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from app import __version__, desktop
from app.release_seed import SeedInstallResult


def _prepare_main(monkeypatch, *, port_open: bool, project_ready: bool) -> list[str]:
    opened: list[str] = []
    monkeypatch.setattr(desktop, "_guard_streams", lambda: None)
    monkeypatch.setattr(desktop, "_setup_logging", lambda: None)
    monkeypatch.setattr(desktop, "_ensure_data_dir_writable", lambda: None)
    monkeypatch.setattr(desktop, "_acquire_single_instance", lambda: True)
    monkeypatch.setattr(desktop, "_release_single_instance", lambda: None)
    monkeypatch.setattr(desktop, "_frontend_dist_ready", lambda: True)
    monkeypatch.setattr(desktop, "_ui_ready", lambda _port: True)
    monkeypatch.setattr(desktop, "_port_is_open", lambda _port: port_open)
    monkeypatch.setattr(desktop, "_project_backend_ready", lambda _port: project_ready)
    monkeypatch.setattr(desktop, "_open_window", opened.append)
    monkeypatch.setattr(desktop, "_show_crash", lambda _title, _text: None)
    return opened


def test_project_backend_probe_requires_health_and_openapi_identity(monkeypatch):
    responses = {
        "/health": {"status": "ok", "version": __version__, "mode": "free"},
        "/openapi.json": {
            "info": {"title": "Nanachi 的台股監控看板", "version": __version__}
        },
    }
    monkeypatch.setattr(desktop, "_read_json", lambda _port, path: responses[path])
    assert desktop._project_backend_ready()

    responses["/openapi.json"]["info"]["title"] = "foreign service"
    assert not desktop._project_backend_ready()


def test_existing_backend_is_reused_and_not_stopped(monkeypatch):
    opened = _prepare_main(monkeypatch, port_open=True, project_ready=True)
    monkeypatch.setattr(
        desktop,
        "_create_server",
        lambda _port: (_ for _ in ()).throw(AssertionError("must not start backend")),
    )
    monkeypatch.setattr(
        desktop,
        "_stop_owned_server",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not stop external backend")),
    )

    assert desktop.main() == 0
    assert opened == ["http://127.0.0.1:3018/"]


def test_legacy_migration_runs_before_seed_install(monkeypatch):
    _prepare_main(monkeypatch, port_open=True, project_ready=True)
    calls: list[str] = []
    monkeypatch.setattr(desktop, "_migrate_legacy_data_before_seed", lambda: calls.append("migrate"))
    monkeypatch.setattr(
        desktop,
        "_install_bundled_release_seed",
        lambda: (calls.append("seed") or SeedInstallResult(status="missing")),
    )

    assert desktop.main() == 0
    assert calls[:2] == ["migrate", "seed"]


def test_foreign_port_owner_fails_without_starting_or_killing(monkeypatch):
    opened = _prepare_main(monkeypatch, port_open=True, project_ready=False)
    monkeypatch.setattr(
        desktop,
        "_create_server",
        lambda _port: (_ for _ in ()).throw(AssertionError("must not start backend")),
    )

    assert desktop.main() == 1
    assert opened == []


def test_owned_backend_starts_and_is_cleaned_up(monkeypatch):
    opened = _prepare_main(monkeypatch, port_open=False, project_ready=False)

    class FakeServer:
        should_exit = False
        force_exit = False
        ran = False

        def run(self):
            self.ran = True

    server = FakeServer()
    monkeypatch.setattr(desktop, "_create_server", lambda port: server if port == 3018 else None)
    monkeypatch.setattr(desktop, "_wait_for_server", lambda _port, timeout: timeout == 60.0)

    assert desktop.main() == 0
    assert server.ran
    assert server.should_exit
    assert not server.force_exit
    assert opened == ["http://127.0.0.1:3018/"]


def test_backend_health_timeout_still_cleans_owned_server(monkeypatch):
    _prepare_main(monkeypatch, port_open=False, project_ready=False)

    class FakeServer:
        should_exit = False
        force_exit = False

        @staticmethod
        def run():
            return None

    server = FakeServer()
    monkeypatch.setattr(desktop, "_create_server", lambda _port: server)
    monkeypatch.setattr(desktop, "_wait_for_server", lambda _port, timeout: False)

    assert desktop.main() == 1
    assert server.should_exit


def test_pywebview_window_uses_formal_title_and_url(monkeypatch):
    calls: list[tuple[tuple, dict]] = []
    fake_webview = SimpleNamespace(
        create_window=lambda *args, **kwargs: calls.append((args, kwargs)),
        start=lambda **kwargs: calls.append((("start",), kwargs)),
        settings={"ALLOW_DOWNLOADS": False},
    )
    monkeypatch.setitem(sys.modules, "webview", fake_webview)

    desktop._open_window("http://127.0.0.1:3018/")

    assert fake_webview.settings["ALLOW_DOWNLOADS"] is True  # 備份檔下載

    assert calls[0][0][:2] == ("Nanachi 台股看板", "http://127.0.0.1:3018/")
    assert calls[0][1]["width"] == 1440
    assert calls[0][1]["height"] == 900
    assert calls[1] == (("start",), {"debug": False})


def test_log_sanitizer_hides_credentials():
    text = "token=abc API_KEY: secret Authorization: Bearer credential"
    result = desktop._sanitize_log(text)
    assert "abc" not in result
    assert "secret" not in result
    assert "credential" not in result
    assert result.count("[已隱藏]") == 3


def test_offline_incremental_refresh_is_non_fatal(monkeypatch):
    class OfflineBootstrap:
        @staticmethod
        def update_to_latest():
            raise OSError("offline")

    monkeypatch.setattr(
        "app.taiwan.bootstrap.get_bootstrap_service",
        lambda: OfflineBootstrap(),
    )
    thread = desktop._start_seed_incremental_refresh(
        SeedInstallResult(
            status="installed",
            data_as_of="2026-09-29",
            installed_files=2,
            needs_incremental=True,
        )
    )
    assert thread is not None
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_desktop_scripts_are_relative_and_shortcut_targets_formal_entry():
    root = Path(__file__).resolve().parents[2]
    start_script = (root / "scripts" / "start-desktop.ps1").read_text(encoding="utf-8")
    installer = (root / "scripts" / "install-desktop-shortcut.ps1").read_text(
        encoding="utf-8"
    )

    assert "Split-Path -Parent $PSScriptRoot" in start_script
    assert "--project $BackendDir --extra desktop" in start_script
    assert "dev.ps1" not in start_script
    assert "E:\\Git" not in start_script
    assert "start-desktop.ps1" in installer
    assert "-WindowStyle Hidden" in installer
    assert "Nanachi 台股看板.lnk" in installer


def test_packaging_uses_bundled_seed_and_per_user_data():
    root = Path(__file__).resolve().parents[2]
    spec = (root / "packaging" / "tickflow.spec").read_text(encoding="utf-8")
    inno = (root / "packaging" / "tickflow.iss").read_text(encoding="utf-8")

    assert 'RELEASE_SEED = ROOT / "release-assets"' in spec
    assert 'datas += [(str(RELEASE_SEED), "release_seed")]' in spec
    assert "DefaultDirName={localappdata}\\Programs\\NanachiStockPanel" in inno
    assert "OutputBaseFilename=NanachiStockPanel-Setup-x64" in inno
    assert "{localappdata}\\NanachiStockPanel\\data" in inno
    assert "taskkill /F" not in inno
    assert "MB_DEFBUTTON2" in inno
    assert "[UninstallDelete]" not in inno.replace("; [UninstallDelete]", "")

    release_workflow = (root / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert "prepare_release_seed.py" in release_workflow
    assert "test-windows-installer.ps1" in release_workflow
    assert "softprops/action-gh-release" not in release_workflow

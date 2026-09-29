from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from app import __version__, desktop


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
    )
    monkeypatch.setitem(sys.modules, "webview", fake_webview)

    desktop._open_window("http://127.0.0.1:3018/")

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
    assert "台灣股票" in str(root)

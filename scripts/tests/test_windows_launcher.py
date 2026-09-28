from pathlib import Path
import sys
from types import SimpleNamespace
import importlib.util

_LAUNCHER_PATH = Path(__file__).resolve().parents[1] / "windows_launcher.py"
_SPEC = importlib.util.spec_from_file_location("windows_launcher_under_test", _LAUNCHER_PATH)
assert _SPEC and _SPEC.loader
launcher = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = launcher
_SPEC.loader.exec_module(launcher)


def test_resolve_project_root_from_exe_in_dist(tmp_path: Path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    assert launcher.resolve_project_root(executable=tmp_path / "dist" / "Nanachi.exe") == tmp_path


def test_sanitize_displayed_logs_hides_credentials():
    text = "token=abc123 API_KEY: secret-value Authorization: Bearer abc"
    result = launcher.sanitize_log(text)
    assert "abc123" not in result
    assert "secret-value" not in result
    assert "Bearer abc" not in result
    assert "[已隱藏]" in result


def test_service_already_running_is_not_owned(monkeypatch, tmp_path: Path):
    manager = launcher.ServiceManager(tmp_path)
    monkeypatch.setattr(launcher, "port_is_open", lambda _port: True)
    monkeypatch.setattr(launcher, "listening_process_id", lambda _port: 123)
    monkeypatch.setattr(launcher, "process_matches_project", lambda _pid, _root: True)
    monkeypatch.setattr(launcher, "backend_ready", lambda _port=launcher.BACKEND_PORT: True)
    manager._start_one(manager.backend, ["unused"], launcher.BACKEND_PORT, launcher.backend_ready)
    assert manager.backend.status == "運行中"
    assert not manager.backend.owned


def test_foreign_occupied_port_is_not_started(monkeypatch, tmp_path: Path):
    calls = []
    manager = launcher.ServiceManager(tmp_path, popen=lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(launcher, "port_is_open", lambda _port: True)
    monkeypatch.setattr(launcher, "backend_ready", lambda: False)
    monkeypatch.setattr(launcher, "listening_process_id", lambda _port: 9876)
    monkeypatch.setattr(launcher, "process_matches_project", lambda _pid, _root: False)
    manager._start_one(manager.backend, ["unused"], launcher.BACKEND_PORT, launcher.backend_ready)
    assert "其他程式使用" in manager.backend.detail
    assert calls == []


def test_backend_startup_failure(monkeypatch, tmp_path: Path):
    class DeadProcess:
        pid = 123

        @staticmethod
        def poll():
            return 1

    manager = launcher.ServiceManager(tmp_path, popen=lambda *args, **kwargs: DeadProcess())
    monkeypatch.setattr(launcher, "port_is_open", lambda _port: False)
    monkeypatch.setattr(launcher, "backend_ready", lambda: False)
    manager._start_one(manager.backend, ["unused"], launcher.BACKEND_PORT, launcher.backend_ready)
    assert manager.backend.status == "錯誤"
    assert "啟動失敗" in manager.backend.detail


def test_health_timeout(monkeypatch, tmp_path: Path):
    class LiveProcess:
        pid = 123

        @staticmethod
        def poll():
            return None

    manager = launcher.ServiceManager(tmp_path, popen=lambda *args, **kwargs: LiveProcess(), sleep=lambda _seconds: None)
    monkeypatch.setattr(launcher, "STARTUP_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(launcher, "port_is_open", lambda _port: False)
    manager._start_one(manager.backend, ["unused"], launcher.BACKEND_PORT, lambda: False)
    assert "逾時" in manager.backend.detail


def test_stop_only_owned_process(monkeypatch, tmp_path: Path):
    stopped = []
    monkeypatch.setattr(launcher, "stop_process_tree", lambda pid: stopped.append(pid) or True)
    manager = launcher.ServiceManager(tmp_path)
    manager.backend.process = SimpleNamespace(pid=12, poll=lambda: None)
    manager.backend.owned = True
    manager.frontend.process = SimpleNamespace(pid=13, poll=lambda: None)
    manager.frontend.owned = False
    manager.stop_owned()
    assert stopped == [12]


def test_browser_open_uses_default_browser(monkeypatch, tmp_path: Path):
    opened = []
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)
    # Keep this test independent from Tk display availability.
    launcher.webbrowser.open(launcher.FRONTEND_URL)
    assert opened == ["http://localhost:3011"]

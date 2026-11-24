from __future__ import annotations

import base64
import os
import shutil
import subprocess
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree

from apscheduler.schedulers.base import STATE_PAUSED, STATE_RUNNING
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import __version__, after_close_launcher, backend_identity
from app.api import routes

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts" / "install-after-close-schedule.ps1"
TASK_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


class AfterCloseLauncherTests(unittest.TestCase):
    def setUp(self):
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.directory = Path(temp_dir.name).resolve()
        self.data_root = self.directory / "Retain my holdings & settings"
        for part in ("kline_daily", "user_data"):
            (self.data_root / part).mkdir(parents=True)
        self.holdings = self.data_root / "user_data" / "holdings-test.txt"
        self.holdings.write_text("do not modify", encoding="utf-8")

    def test_missing_or_uninitialized_data_root_does_not_create_or_launch(self):
        cwd = self.directory
        with (
            patch.object(after_close_launcher, "start_or_reuse") as launch,
            patch("builtins.print"),
        ):
            for denied in (cwd, cwd / "absent", Path("relative")):
                with (
                    self.subTest(path=denied),
                    patch("sys.argv", ["launcher", "--data-dir", str(denied)]),
                ):
                    self.assertEqual(after_close_launcher.main(), 1)
            launch.assert_not_called()
        self.assertFalse((cwd / "absent").exists())
        self.assertEqual(self.holdings.read_text(encoding="utf-8"), "do not modify")

    def test_readiness_checks_route_identity_store_and_scheduler(self):
        app = FastAPI(title=backend_identity.API_TITLE, version=__version__)
        app.include_router(routes.router)
        app.state.datastore = SimpleNamespace(data_dir=self.data_root)
        app.state.scheduler = SimpleNamespace(state=STATE_RUNNING)
        app.state.startup_catchup_ready = True
        local = TestClient(app, base_url="http://127.0.0.1:3018", client=(backend_identity.LOOPBACK, 12345))
        remote = TestClient(app, client=("192.0.2.5", 12345))

        def read(_port, path, headers):
            return local.get(path, headers=headers).json()

        def ready(data_root=self.data_root, require_scheduler=True):
            return backend_identity.project_backend_ready(
                3018, data_root, __version__, require_scheduler=require_scheduler
            )

        with patch.object(backend_identity, "_read_local_json", side_effect=read):
            self.assertTrue(ready())
            self.assertFalse(ready(self.directory))
            app.state.scheduler.state = STATE_PAUSED
            self.assertFalse(ready())
            self.assertTrue(ready(require_scheduler=False))
            app.state.scheduler.state = STATE_RUNNING
            app.state.startup_catchup_ready = False
            self.assertFalse(ready())
            app.state.startup_catchup_ready = True
            app.openapi_schema["info"]["title"] = "Another application"
            self.assertFalse(ready())

        check = {backend_identity.DATA_CHECK_HEADER: backend_identity.data_root_fingerprint(self.data_root)}
        self.assertEqual(local.get("/health").json(), remote.get("/health", headers=check).json())
        self.assertNotIn("same_data_root", local.get("/health").json())
        proxied = check | {"x-forwarded-for": "192.0.2.5"}
        cross_site = check | {"origin": "https://attacker.example"}
        for headers in (proxied, cross_site):
            self.assertNotIn("same_data_root", local.get("/health", headers=headers).json())


    def test_real_foreign_listener_never_imports_app_or_starts_work(self):
        # A genuine listening socket is intentionally not a responding HTTP backend.
        with backend_identity.reserve_backend_listener(0) as listener:
            port = listener.getsockname()[1]
            with self.assertRaisesRegex(RuntimeError, "No process was started or stopped"):
                after_close_launcher.start_or_reuse(self.data_root, port=port, timeout=0)
            self.assertEqual(listener.getsockname()[1], port)
        self.assertEqual(self.holdings.read_text(encoding="utf-8"), "do not modify")

    def test_warming_same_store_is_reused_only_after_readiness(self):
        with (
            patch.object(after_close_launcher, "reserve_backend_listener", side_effect=OSError),
            patch.object(after_close_launcher, "project_backend_ready", side_effect=[False, True]) as probe,
            patch.object(after_close_launcher.time, "sleep"),
            patch("builtins.print"),
        ):
            self.assertEqual(after_close_launcher.start_or_reuse(self.data_root), 0)
        self.assertEqual(probe.call_count, 2)
        probe.assert_called_with(3018, self.data_root, __version__, require_scheduler=True)

    def test_owned_start_sets_store_and_reserves_port_before_server(self):
        state = SimpleNamespace()
        fake_main = ModuleType("app.main")
        fake_main.app = SimpleNamespace(state=state)

        def run(*, sockets):
            port = sockets[0].getsockname()[1]
            with self.assertRaises(OSError):
                backend_identity.reserve_backend_listener(port)
            self.assertEqual(os.environ["DATA_DIR"], str(self.data_root))
            self.assertTrue(state.require_after_close_scheduler)

        server = SimpleNamespace(started=False, run=run)
        with (
            patch.dict("sys.modules", {"app.main": fake_main}),
            patch.dict(os.environ, {"DATA_DIR": str(self.directory)}),
            patch("uvicorn.Config"),
            patch("uvicorn.Server", return_value=server),
            patch("builtins.print"),
        ):
            self.assertEqual(after_close_launcher.start_or_reuse(self.data_root, port=0), 1)
            server.started = True
            self.assertEqual(after_close_launcher.start_or_reuse(self.data_root, port=0), 0)
        self.assertEqual(self.holdings.read_text(encoding="utf-8"), "do not modify")

    def assert_task_xml(self, xml, expected_store):
        task = ElementTree.fromstring(xml)
        start = datetime.fromisoformat(task.findtext(".//t:StartBoundary", namespaces=TASK_NS))
        self.assertEqual(start.utcoffset(), timedelta(hours=8))
        self.assertEqual((start.hour, start.minute), (16, 35))
        days = task.findall(".//t:DaysOfWeek/*", TASK_NS)
        self.assertEqual(
            [day.tag.split("}")[-1] for day in days],
            ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
        )
        self.assertEqual(task.findtext(".//t:StartWhenAvailable", namespaces=TASK_NS), "true")
        self.assertEqual(task.findtext(".//t:MultipleInstancesPolicy", namespaces=TASK_NS), "IgnoreNew")
        args = task.findtext(".//t:Arguments", namespaces=TASK_NS)
        self.assertIn(f'-DataDir "{expected_store}"', args)

    def test_scheduler_xml_template(self):
        # Cross-platform check of the same XML template used by -Preview and registration.
        from xml.sax.saxutils import escape

        script = INSTALLER.read_text(encoding="ascii")
        xml = script.split('$taskXml = @"\n', 1)[1].split('\n"@', 1)[0]
        variables = {
            "boundary": "2026-11-24T16:35:00+08:00",
            "userSid": "S-1-5-21-1-2-3-1001",
            "commandXml": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            "argumentsXml": escape(f'-DataDir "{self.data_root}"'),
            "rootXml": escape(str(self.directory)),
        }
        for name, value in variables.items():
            xml = xml.replace(f"${name}", value)
        self.assert_task_xml(xml, self.data_root)
        self.assertLess(script.index("if ($Preview) {\n    Write-Output"), script.index("Register-ScheduledTask"))
        self.assertIn('T16:35:00+08:00"', script)
        self.assertIn('-Force:$Replace', script)

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"), "Windows PowerShell required")
    def test_windows_preview_emits_xml_without_any_task_access(self):
        # Shadow task cmdlets inside the process: even an accidental read fails the test.
        installer = str(INSTALLER).replace("'", "''")
        store = str(self.data_root).replace("'", "''")
        command = (
            "function Get-ScheduledTask { throw 'Preview touched Task Scheduler' }; "
            "function Register-ScheduledTask { throw 'Preview touched Task Scheduler' }; "
            "function Unregister-ScheduledTask { throw 'Preview touched Task Scheduler' }; "
            f"& '{installer}' -DataDir '{store}' -Preview"
        )
        encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
        run = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
            capture_output=True, text=True, check=True, encoding="utf-8", timeout=30,
        )
        self.assert_task_xml(run.stdout.strip(), self.data_root)


if __name__ == "__main__":
    unittest.main()

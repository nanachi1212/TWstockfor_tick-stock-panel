"""Nanachi 台股看板 Windows GUI launcher.

This is intentionally a thin launcher around the existing backend and Vite
development server.  It does not load application settings or package user
data; the running services keep owning those responsibilities.
"""
from __future__ import annotations

import http.client
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol


BACKEND_PORT = 3018
FRONTEND_PORT = 3011
FRONTEND_URL = f"http://localhost:{FRONTEND_PORT}"
STARTUP_TIMEOUT_SECONDS = 60.0
POLL_SECONDS = 0.5


class ProcessLike(Protocol):
    pid: int

    def poll(self) -> int | None: ...


@dataclass
class ServiceState:
    name: str
    status: str = "已停止"
    detail: str = ""
    process: ProcessLike | None = None
    owned: bool = False
    log_lines: list[str] = field(default_factory=list)


def resolve_project_root(*, executable: Path | None = None, cwd: Path | None = None) -> Path:
    """Find a checkout/install directory containing backend and frontend.

    A frozen EXE is normally copied to the repository root.  The parent and
    grandparent candidates also support running the PyInstaller output from
    ``dist`` during a local smoke test.
    """
    candidates: list[Path] = []
    if executable:
        candidates.extend((executable.parent, executable.parent.parent))
    candidates.extend((Path(__file__).resolve().parents[1], cwd or Path.cwd()))
    for candidate in candidates:
        candidate = candidate.resolve()
        if (candidate / "backend").is_dir() and (candidate / "frontend").is_dir():
            return candidate
    raise FileNotFoundError("找不到同時包含 backend 與 frontend 的專案目錄")


def sanitize_log(text: str) -> str:
    """Remove common credential-shaped values before showing child output."""
    patterns = (
        r"(?i)(api[_-]?key|token|password|secret|webhook)(\s*[=:]\s*)[^\s,;]+",
        r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+",
        r"(?i)(x-api-key\s*:\s*)[^\s]+",
    )
    for pattern in patterns:
        text = re.sub(pattern, lambda match: f"{match.group(1)}[已隱藏]", text)
    return text


def _http_status(port: int, path: str, timeout: float = 1.5) -> tuple[int, str]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read(8192).decode("utf-8", errors="replace")
        return response.status, body
    finally:
        connection.close()


def backend_ready(port: int = BACKEND_PORT) -> bool:
    try:
        status, _ = _http_status(port, "/health")
        return status == 200
    except (OSError, http.client.HTTPException):
        return False


def frontend_ready(port: int = FRONTEND_PORT) -> bool:
    try:
        status, body = _http_status(port, "/")
        return status == 200 and bool(body)
    except (OSError, http.client.HTTPException):
        return False


def port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def process_matches_project(pid: int, root: Path) -> bool:
    """Best-effort identity check used only to explain a port conflict."""
    try:
        import psutil  # type: ignore[import-not-found]

        process = psutil.Process(pid)
        haystack = " ".join(process.cmdline()) + " " + process.cwd()
        return str(root).lower() in haystack.lower()
    except Exception:  # noqa: BLE001
        return False


def listening_process_id(port: int) -> int | None:
    try:
        import psutil  # type: ignore[import-not-found]

        for connection in psutil.net_connections(kind="tcp"):
            if connection.laddr and connection.laddr.port == port and connection.status == psutil.CONN_LISTEN:
                return connection.pid
    except Exception:  # noqa: BLE001
        return None
    return None


def stop_process_tree(pid: int) -> bool:
    """Stop exactly the process tree rooted at a launcher-owned PID."""
    if os.name != "nt":
        try:
            os.kill(pid, 15)
            return True
        except OSError:
            return False
    taskkill = shutil.which("taskkill")
    if not taskkill:
        return False
    result = subprocess.run(
        [taskkill, "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return result.returncode == 0


class ServiceManager:
    def __init__(
        self,
        root: Path,
        *,
        popen: Callable[..., ProcessLike] = subprocess.Popen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.root = root
        self._popen = popen
        self._sleep = sleep
        self.backend = ServiceState("後端服務")
        self.frontend = ServiceState("前端服務")
        self._lock = threading.RLock()

    def _append_log(self, service: ServiceState, text: str) -> None:
        service.log_lines.extend(sanitize_log(text).splitlines())
        service.log_lines = service.log_lines[-80:]

    def _port_conflict(self, service: ServiceState, port: int, ready: Callable[[], bool]) -> str | None:
        if not port_is_open(port):
            return None
        pid = listening_process_id(port)
        if pid and not process_matches_project(pid, self.root):
            service.status = "錯誤"
            service.detail = f"連接埠 {port} 已被其他程式使用"
            return "conflict"
        if ready():
            service.status = "運行中"
            service.detail = "已使用現有服務"
            service.owned = False
            return "already-running"
        if pid and process_matches_project(pid, self.root):
            service.status = "錯誤"
            service.detail = f"本專案程序 PID {pid} 未回應健康檢查"
        else:
            service.status = "錯誤"
            service.detail = f"連接埠 {port} 已被其他程式使用"
        return "conflict"

    def _start_one(self, service: ServiceState, command: list[str], port: int, ready: Callable[[], bool]) -> None:
        with self._lock:
            service.status = "啟動中"
            service.detail = ""
            conflict = self._port_conflict(service, port, ready)
            if conflict:
                return
            try:
                creationflags = 0
                if os.name == "nt":
                    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
                service.process = self._popen(
                    command,
                    cwd=self.root / ("backend" if service is self.backend else "frontend"),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=creationflags,
                )
                service.owned = True
                output = getattr(service.process, "stdout", None)
                if output is not None:
                    threading.Thread(
                        target=self._capture_output,
                        args=(service, output),
                        daemon=True,
                    ).start()
            except OSError as exc:
                service.status = "錯誤"
                service.detail = f"{service.name}啟動失敗：{exc}"
                return

        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if ready():
                with self._lock:
                    service.status = "運行中"
                return
            if service.process and service.process.poll() is not None:
                with self._lock:
                    service.status = "錯誤"
                    service.detail = f"{service.name}啟動失敗"
                return
            self._sleep(POLL_SECONDS)
        with self._lock:
            service.status = "錯誤"
            service.detail = f"{service.name}健康檢查逾時"

    def _capture_output(self, service: ServiceState, output) -> None:
        try:
            for line in output:
                with self._lock:
                    self._append_log(service, line)
        except (OSError, ValueError):
            pass

    def start(self) -> None:
        backend_python = self.root / "backend" / ".venv" / "Scripts" / "python.exe"
        if not backend_python.exists():
            self.backend.status = "錯誤"
            self.backend.detail = "找不到 backend\\.venv\\Scripts\\python.exe"
            return
        self._start_one(
            self.backend,
            [str(backend_python), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(BACKEND_PORT)],
            BACKEND_PORT,
            backend_ready,
        )
        if self.backend.status != "運行中":
            return
        pnpm = shutil.which("pnpm")
        if not pnpm:
            self.frontend.status = "錯誤"
            self.frontend.detail = "找不到 pnpm，請先安裝 Node.js 與 pnpm"
            return
        self._start_one(
            self.frontend,
            [pnpm, "dev", "--host", "127.0.0.1", "--port", str(FRONTEND_PORT)],
            FRONTEND_PORT,
            frontend_ready,
        )

    def stop_owned(self) -> None:
        for service in (self.frontend, self.backend):
            process = service.process
            if service.owned and process and process.poll() is None:
                stop_process_tree(process.pid)
            service.process = None
            if service.owned:
                service.status = "已停止"
                service.detail = ""
            service.owned = False


class LauncherApp:
    def __init__(self, root: Path) -> None:
        import tkinter as tk
        from tkinter import messagebox

        self.tk = tk
        self.messagebox = messagebox
        self.manager = ServiceManager(root)
        self.root = root
        self.window = tk.Tk()
        self.window.title("Nanachi 台股監控看板")
        self.window.geometry("470x290")
        self.window.resizable(False, False)
        self.auto_open = tk.BooleanVar(value=True)
        self.backend_var = tk.StringVar(value="● 後端服務：已停止")
        self.frontend_var = tk.StringVar(value="● 前端服務：已停止")
        self.realtime_var = tk.StringVar(value="即時行情：未查詢")
        self.date_var = tk.StringVar(value="資料日期：未查詢")
        self._auto_opened = False
        self._build_ui()
        self.window.protocol("WM_DELETE_WINDOW", self._close)
        self.window.after(100, self._start_async)
        self.window.after(500, self._refresh_ui)

    def _build_ui(self) -> None:
        import tkinter as tk

        frame = tk.Frame(self.window, padx=24, pady=20)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="Nanachi 台股監控看板", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        for variable in (self.backend_var, self.frontend_var, self.realtime_var, self.date_var):
            tk.Label(frame, textvariable=variable, anchor="w").pack(fill="x", pady=3)
        tk.Checkbutton(frame, text="啟動後自動開啟看板", variable=self.auto_open).pack(anchor="w", pady=(8, 10))
        buttons = tk.Frame(frame)
        buttons.pack(fill="x")
        tk.Button(buttons, text="開啟台股看板", command=self._open_dashboard).pack(side="left", padx=(0, 6))
        tk.Button(buttons, text="重新啟動服務", command=self._restart_async).pack(side="left", padx=6)
        tk.Button(buttons, text="停止服務", command=self._stop).pack(side="left", padx=6)
        tk.Button(buttons, text="查看錯誤", command=self._show_errors).pack(side="left", padx=6)

    def _start_async(self) -> None:
        threading.Thread(target=self.manager.start, daemon=True).start()

    def _restart_async(self) -> None:
        self.manager.stop_owned()
        self._start_async()

    def _stop(self) -> None:
        self.manager.stop_owned()

    def _open_dashboard(self) -> None:
        webbrowser.open(FRONTEND_URL)

    def _refresh_ui(self) -> None:
        backend = self.manager.backend
        frontend = self.manager.frontend
        self.backend_var.set(f"● 後端服務：{backend.status}" + (f"（{backend.detail}）" if backend.detail else ""))
        self.frontend_var.set(f"● 前端服務：{frontend.status}" + (f"（{frontend.detail}）" if frontend.detail else ""))
        if backend.status == "運行中":
            self.realtime_var.set("即時行情：● 運行中")
            self.date_var.set("資料日期：由看板顯示")
        if self.auto_open.get() and not self._auto_opened and frontend.status == "運行中":
            self._auto_opened = True
            self._open_dashboard()
        self.window.after(500, self._refresh_ui)

    def _show_errors(self) -> None:
        lines = self.manager.backend.log_lines + self.manager.frontend.log_lines
        text = "\n".join(lines[-40:]) or "目前沒有可顯示的錯誤記錄。"
        self.messagebox.showerror("Nanachi 台股看板錯誤", text)

    def _close(self) -> None:
        owned = self.manager.backend.owned or self.manager.frontend.owned
        if not owned:
            self.window.destroy()
            return
        choice = self.messagebox.askyesnocancel("關閉 launcher", "要同時停止台股看板服務嗎？\n\n選「是」停止並離開，選「否」保持服務運行。")
        if choice is None:
            return
        if choice:
            self.manager.stop_owned()
        self.window.destroy()


def main() -> int:
    executable = Path(sys.executable if getattr(sys, "frozen", False) else __file__)
    try:
        root = resolve_project_root(executable=executable)
    except FileNotFoundError as exc:
        import tkinter.messagebox as messagebox

        messagebox.showerror("Nanachi 台股看板", str(exc))
        return 1
    app = LauncherApp(root)
    app.window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

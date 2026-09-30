"""桌面客户端入口 — uvicorn 后台服务 + pywebview 桌面窗口。

运行方式:
  开发模式: python -m app.desktop  (需 pip install pywebview)
  打包后:   双击可执行文件即可

职责:
  1. 单实例锁 — 已运行则不建立重复窗口
  2. 复用 3018 上通过身份检查的既有 backend, 不停止外部服务
  3. 需要时在后台线程启动 owned uvicorn (仅监听 127.0.0.1)
  4. 确认 production frontend ready 后用 pywebview 渲染 React UI
  5. 窗口关闭 → 只优雅停止 owned uvicorn → 进程退出

不含: 业务逻辑、配置持久化、监控告警 (全在 app.main 里)。
"""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import sys
import threading
import time
import traceback
from pathlib import Path

logger = logging.getLogger(__name__)

_APP_NAME = "Nanachi 台股看板"
_API_TITLE = "Nanachi 的台股監控看板"
_BASE_PORT = 3018
_SHUTDOWN_TIMEOUT = 15.0

_SECRET_PATTERNS = (
    r"(?i)(api[_-]?key|token|password|secret|webhook)(\s*[=:]\s*)[^\s,;]+",
    r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+",
    r"(?i)(x-api-key\s*:\s*)[^\s]+",
)


def _sanitize_log(text: str) -> str:
    """遮蔽常見 credential 形狀, 避免桌面 log 與錯誤框洩漏密鑰。"""
    for pattern in _SECRET_PATTERNS:
        text = re.sub(pattern, lambda match: f"{match.group(1)}[已隱藏]", text)
    return text


class _SanitizingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return _sanitize_log(super().format(record))


def _ensure_data_dir_writable() -> None:
    """确保用户数据目录可写 (lifespan 会创建子目录, 这里只验证根目录)。

    data_dir 在 frozen 模式下指向用户目录 (见 config.py), 非可写会导致
    DuckDB 视图 / parquet 落盘全失败。提前失败胜过启动后乱报错。
    """
    from app.config import settings

    data_root = settings.data_dir
    try:
        data_root.mkdir(parents=True, exist_ok=True)
        probe = data_root / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except Exception as e:
        logger.error("数据目录不可写, 桌面版无法运行: %s (%s)", data_root, e)
        raise


def _install_bundled_release_seed():
    """Install the read-only public seed on a pristine frozen profile.

    Verification or installation failure is fail-safe for user data and does
    not block the GUI.  The app starts with an explicit unavailable/stale data
    state so an offline user can still reach settings and diagnostics.
    """
    from app.config import settings
    from app.release_seed import SeedInstallResult, install_release_seed

    if not getattr(sys, "frozen", False):
        return SeedInstallResult(status="missing")
    result = install_release_seed(settings.release_seed_bundle, settings.data_dir)
    if result.status == "failed":
        logger.error(
            "bundled release seed rejected; startup continues without import: %s",
            result.error,
        )
    elif result.status == "installed":
        logger.info(
            "bundled release seed installed: data_as_of=%s files=%d",
            result.data_as_of,
            result.installed_files,
        )
    else:
        logger.info(
            "bundled release seed not applied: status=%s existing=%s",
            result.status,
            result.latest_existing,
        )
    return result


def _migrate_legacy_data_before_seed() -> None:
    """Preserve old installed user data before a fresh profile can receive seed data."""
    if not getattr(sys, "frozen", False):
        return
    from app.config import settings
    from app.repository import migrate_legacy_desktop_data

    migrate_legacy_desktop_data(settings.data_dir)


def _start_seed_incremental_refresh(seed_result) -> threading.Thread | None:
    """Refresh seed date to latest in background; network failure stays non-fatal."""
    if seed_result.status != "installed" or not seed_result.needs_incremental:
        return None

    def refresh() -> None:
        try:
            from app.taiwan.bootstrap import get_bootstrap_service

            result = get_bootstrap_service().update_to_latest()
            logger.info("first-run seed incremental refresh completed: %s", result)
        except Exception as exc:  # offline startup must remain usable
            logger.warning(
                "first-run seed incremental refresh unavailable; seed remains usable: %s",
                exc,
            )

    thread = threading.Thread(
        target=refresh,
        daemon=True,
        name="release-seed-incremental-refresh",
    )
    thread.start()
    return thread


def _acquire_single_instance() -> bool:
    """单实例锁。已运行返回 False (本进程应退出), 否则 True。

    用 data_dir/.desktop.lock 文件锁实现。跨进程, 文件存在即视为已运行
    (简单可靠; 不引入 msvcrt/fcntl 平台差异)。
    """
    from app.config import settings

    lock_path = settings.data_dir / ".desktop.lock"
    for _attempt in range(2):
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                pid_str = lock_path.read_text(encoding="utf-8").strip()
                pid = int(pid_str) if pid_str.isdigit() else None
            except Exception:
                pid = None
            if pid is not None and _pid_alive(pid):
                logger.warning("检测到已有实例运行 (PID %d), 本进程退出", pid)
                return False
            logger.info("清理残留单实例锁 (PID %s 已不存在)", pid)
            lock_path.unlink(missing_ok=True)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(_current_pid()))
        return True
    return False


def _release_single_instance() -> None:
    from app.config import settings

    lock_path = settings.data_dir / ".desktop.lock"
    try:
        if lock_path.read_text(encoding="utf-8").strip() == str(_current_pid()):
            lock_path.unlink(missing_ok=True)
    except Exception:
        pass


def _guard_streams() -> None:
    """windowed 模式 (console=False) 下守护 stdout/stderr。

    PyInstaller console=False 用 runw.exe 启动器, 不分配控制台, 此时 sys.stdout /
    sys.stderr 可能为 None (或底层句柄无效)。后果:
      - app/__init__.py 早期 reconfigure 遇 None 虽有 hasattr 保护, 但若是个「写入即崩」
        的伪 stream 对象, reconfigure 会成功、后续写却崩;
      - logging.basicConfig() 默认建 StreamHandler(sys.stderr), stderr 为 None 时
        首次写日志调 None.write() 抛 AttributeError, 此时往往在导入早期,
        Python 异常处理未就绪 → 进程直接闪退, try/except 都拦不住。

    修法: console=False 下把 stdout/stderr 换成丢弃写入的空对象 (devnull),
    让 logging / reconfigure / 任何 print 都安全落地。console=True 不动 (有真控制台)。
    """
    class _NullStream:
        """丢弃所有写入的空流 (替代 None 的 stdout/stderr)。"""
        def write(self, _s): return 0
        def flush(self): pass
        def reconfigure(self, *a, **kw): pass
        def isatty(self): return False
        def fileno(self): raise OSError("no fileno")

    # 仅在 stdout/stderr 缺失或不可写时替换 (有真控制台时保持原样)
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            setattr(sys, name, _NullStream())


def _setup_logging() -> None:
    """配置日志落盘到 data/desktop.log。

    背景: spec 里 console=False (桌面应用不弹黑窗), 导致 logging 默认输出的
    stderr 被吞掉 —— 启动期任何异常用户都看不到, 表现为「双击一闪退出、查无日志」。
    这里追加一个 FileHandler, 让日志同时落到 data_dir/desktop.log, 事后可查。

    时序注意: data_dir 在 import app.config 时路径已可用, 但目录此刻可能不存在
    (frozen 首次运行), 必须先 mkdir, 否则 FileHandler 打开文件会抛 FileNotFoundError。
    不用第二次 basicConfig (它「首次调用才生效」), 改用 addHandler 追加。
    """
    try:
        from app.config import settings

        log_dir = settings.data_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(
            log_dir / "desktop.log",
            mode="a",            # 追加, 保留历史 (排查时往往需要对比多次启动)
            encoding="utf-8",
            errors="replace",    # 容错: 对齐 __init__.py 的 stderr 重配, 避免中文/emoji 触发 UnicodeEncodeError
        )
        handler.setFormatter(
            _SanitizingFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logging.getLogger().addHandler(handler)
    except Exception as e:
        # 日志落盘失败不阻断启动 (开发模式 data_dir 可能不可写)
        logger.warning("日志文件初始化失败, 仅输出到 stderr: %s", e)


def _show_crash(title: str, text: str) -> None:
    """崩溃时弹原生 MessageBox 提示用户 (仅 Windows)。

    console=False 下用户看不到任何输出, 崩溃时弹一个原生错误框, 让用户至少
    知道「程序崩了 + 原因」, 并可截图反馈。非 Windows 用日志降级, 不调 ctypes。

    ctypes 是 Python 标准库, 不引入新依赖; MessageBoxW 是 Unicode 版本 (W 后缀),
    支持中文标题/正文。0x10 = MB_ICONERROR (红色错误图标)。
    """
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, _sanitize_log(text), title, 0x10)
        except Exception as e:
            logger.error("弹框失败 (已写日志文件): %s", e)
    else:
        logger.error("%s: %s", title, text)


def _pid_alive(pid: int) -> bool:
    """检查指定 PID 的进程是否存活。"""
    import os

    if os.name == "nt":
        # Windows: 0 表示存在, 其它是异常
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    else:
        try:
            os.kill(pid, 0)  # signal 0 = 探测存活, 不实际发信号
            return True
        except OSError:
            return False


def _current_pid() -> int:
    import os

    return os.getpid()


def _port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _create_server(port: int):
    """建立既有 FastAPI app 的 uvicorn server, 不複製 routing。"""
    import uvicorn

    from app.main import app

    config = uvicorn.Config(
        app,
        host="127.0.0.1",  # 仅本机, 不暴露外网 (桌面版无需远程访问)
        port=port,
        log_level="info",
        access_log=False,    # 桌面版不需要访问日志
        loop="auto",
    )
    return uvicorn.Server(config)


def _run_uvicorn(server, done_event: threading.Event) -> None:
    """背景執行 owned uvicorn, 退出時通知 lifecycle 管理端。"""
    try:
        server.run()
    except Exception:
        # server.run() 内部跑 lifespan 启动链, 任一步抛异常都会冒到这里。
        # 不捕获则线程静默死亡, 主线程 _wait_for_server 傻等满 60s 后报「超时」,
        # 真正的崩溃原因 (如某个原生库加载失败 / 缺 hidden import) 永远看不到。
        logger.exception("uvicorn 后端启动/运行失败")
    finally:
        done_event.set()


def _read_json(port: int, path: str) -> dict | None:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else None
    except (urllib.error.URLError, OSError, UnicodeError, ValueError):
        return None


def _project_backend_ready(port: int = _BASE_PORT) -> bool:
    """以 health 與 OpenAPI 身分共同確認 3018 是本專案 backend。"""
    from app import __version__

    health = _read_json(port, "/health")
    schema = _read_json(port, "/openapi.json")
    return bool(
        health
        and health.get("status") == "ok"
        and health.get("version") == __version__
        and schema
        and schema.get("info", {}).get("title") == _API_TITLE
        and schema.get("info", {}).get("version") == __version__
    )


def _wait_for_server(port: int, timeout: float = 60.0) -> bool:
    """轮询 health 接口直到后端就绪或超时。

    比 monkey-patch uvicorn 内部方法更健壮, 不依赖版本内部实现。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _project_backend_ready(port):
            return True
        time.sleep(0.5)
    return False


def _frontend_dist_ready() -> bool:
    from app.config import settings

    static_dir = Path(settings.static_dir)
    return (static_dir / "index.html").is_file() and (static_dir / "assets").is_dir()


def _ui_ready(port: int = _BASE_PORT) -> bool:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:
            body = response.read(8192).decode("utf-8", errors="replace").lower()
            return response.status == 200 and "<html" in body and 'id="root"' in body
    except (urllib.error.URLError, OSError):
        return False


def _stop_owned_server(server, thread: threading.Thread) -> bool:
    """只停止本次 desktop entry 建立的 uvicorn, 並等待 thread 結束。"""
    server.should_exit = True
    thread.join(timeout=_SHUTDOWN_TIMEOUT)
    if thread.is_alive():
        logger.warning("後端未在 %.0f 秒內停止, 要求 uvicorn 強制結束", _SHUTDOWN_TIMEOUT)
        server.force_exit = True
        thread.join(timeout=5.0)
    return not thread.is_alive()


def _open_window(url: str) -> None:
    """主线程: 用 pywebview 打开桌面窗口。"""
    import webview  # type: ignore[import-not-found]

    # 設定 → 備份與轉移 需要把 .twstock-backup 存到使用者選的位置 (pywebview 預設禁止下載)。
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.create_window(
        _APP_NAME,
        url,
        width=1440,
        height=900,
        min_size=(1024, 700),
        # 桌面版固定单窗口, 禁用外部浏览器跳转
        confirm_close=False,
    )
    # pywebview 会阻塞主线程直到窗口关闭
    webview.start(debug=False)


def main() -> int:
    """桌面客户端主入口。返回进程退出码。"""
    # 必须最先执行: console=False 下 stdout/stderr 可能无效, 不守护会导致
    # 后续 logging.basicConfig 创建的 StreamHandler 写日志时进程崩溃。
    _guard_streams()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # 追加文件日志: console=False 下 stderr 被吞, 必须落盘否则查无对证。
    # 放在 basicConfig 之后 (它先建好 root logger 的格式), 这里只追加 handler。
    _setup_logging()

    try:
        _ensure_data_dir_writable()
    except Exception as exc:
        # 数据目录不可写是致命错误, 无法继续
        _show_crash(f"{_APP_NAME} 啟動失敗", str(exc))
        return 1

    _migrate_legacy_data_before_seed()
    seed_result = _install_bundled_release_seed()

    # 单实例: 已运行则退出
    if not _acquire_single_instance():
        return 0

    server = None
    server_thread = None
    try:
        if not _frontend_dist_ready():
            raise RuntimeError("找不到 frontend/dist, 請先執行 frontend 的 pnpm build")

        port = _BASE_PORT
        if _port_is_open(port):
            if not _project_backend_ready(port):
                raise RuntimeError(f"連接埠 {port} 已被其他程式使用, 未啟動也未終止該程序")
            logger.info("使用既有本專案 backend: 127.0.0.1:%d", port)
        else:
            logger.info("啟動 desktop entry owned backend: 127.0.0.1:%d", port)
            server = _create_server(port)
            done = threading.Event()
            server_thread = threading.Thread(
                target=_run_uvicorn,
                args=(server, done),
                daemon=False,
                name="uvicorn-desktop-owned",
            )
            server_thread.start()
            if not _wait_for_server(port, timeout=60.0):
                raise RuntimeError("後端健康檢查逾時, 詳情請查看 desktop.log")

        _start_seed_incremental_refresh(seed_result)

        if not _ui_ready(port):
            raise RuntimeError("backend 已啟動, 但 production React UI 尚未就緒")

        url = f"http://127.0.0.1:{port}/"
        logger.info("打开桌面窗口: %s", url)
        _open_window(url)

        logger.info("窗口已关闭, 桌面版退出")
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        # 顶层兜底: console=False 下未捕获异常会「一闪退出」且无任何反馈。
        # 写完整 traceback 到 data/desktop.log, 并弹原生 MessageBox 让用户截图反馈。
        # 必须排在 KeyboardInterrupt 之后 —— Exception 是基类, 在前会遮蔽它。
        logger.exception("桌面客户端启动失败")
        _show_crash(f"{_APP_NAME} 啟動失敗", traceback.format_exc())
        return 1
    finally:
        if server is not None and server_thread is not None:
            if _stop_owned_server(server, server_thread):
                logger.info("本次 desktop entry 啟動的 backend 已停止")
            else:
                logger.error("本次 desktop entry 啟動的 backend 未能在期限內停止")
        _release_single_instance()


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    sys.exit(main())

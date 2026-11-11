"""桌面客戶端入口 — uvicorn 背景服務 + pywebview 桌面視窗。

執行方式:
  開發模式: python -m app.desktop  (需 pip install pywebview)
  打包後:   雙擊可執行檔案即可

職責:
  1. 單實例鎖 — 已執行則不建立重複視窗
  2. 重用 3018 上通過身份檢查的既有 backend, 不停止外部服務
  3. 需要時在背景執行緒啟動 owned uvicorn (僅監聽 127.0.0.1)
  4. 確認 production frontend ready 後用 pywebview 渲染 React UI
  5. 視窗關閉 → 只優雅停止 owned uvicorn → 行程退出

不含: 業務邏輯、設定持久化、監控告警 (全在 app.main 裡)。
"""
from __future__ import annotations

import json
import logging
import os
import socket
import sys
import threading
import time
import traceback
from pathlib import Path

from app.log_sanitizer import SanitizingFormatter as _SanitizingFormatter
from app.log_sanitizer import sanitize_log as _sanitize_log

logger = logging.getLogger(__name__)

_APP_NAME = "Nanachi 台股看板"
_API_TITLE = "Nanachi 的台股監控看板"
_BASE_PORT = 3018
_EXISTING_BACKEND_TIMEOUT = 30.0
_SHUTDOWN_TIMEOUT = 15.0

def _ensure_data_dir_writable() -> None:
    """確保使用者資料目錄可寫 (lifespan 會建立子目錄, 這裡只驗證根目錄)。

    data_dir 在 frozen 模式下指向使用者目錄 (見 config.py), 非可寫會導致
    DuckDB 檢視表 / parquet 寫入磁碟全失敗。提前失敗勝過啟動後亂出錯。
    """
    from app.config import settings

    data_root = settings.data_dir
    try:
        data_root.mkdir(parents=True, exist_ok=True)
        probe = data_root / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except Exception as e:
        logger.error("資料目錄不可寫, 桌面版無法執行: %s (%s)", data_root, e)
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
    """單實例鎖。已執行回傳 False (本行程應退出), 否則 True。

    用 data_dir/.desktop.lock 檔案鎖實作。跨行程, 檔案存在即視為已執行
    (簡單可靠; 不引入 msvcrt/fcntl 平台差異)。
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
                logger.warning("檢測到已有實例執行 (PID %d), 本行程退出", pid)
                return False
            logger.info("清理殘留單實例鎖 (PID %s 已不存在)", pid)
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
    """windowed 模式 (console=False) 下守護 stdout/stderr。

    PyInstaller console=False 用 runw.exe 啟動器, 不分配控制檯, 此時 sys.stdout /
    sys.stderr 可能為 None (或底層句柄無效)。後果:
      - app/__init__.py 早期 reconfigure 遇 None 雖有 hasattr 保護, 但若是個「寫入即崩」
        的偽 stream 物件, reconfigure 會成功、後續寫卻崩;
      - logging.basicConfig() 預設建 StreamHandler(sys.stderr), stderr 為 None 時
        首次寫日誌調 None.write() 拋 AttributeError, 此時往往在匯入早期,
        Python 異常處理未就緒 → 行程直接閃退, try/except 都攔不住。

    修法: console=False 下把 stdout/stderr 換成丟棄寫入的空物件 (devnull),
    讓 logging / reconfigure / 任何 print 都安全落地。console=True 不動 (有真控制檯)。
    """
    class _NullStream:
        """丟棄所有寫入的空流 (替代 None 的 stdout/stderr)。"""
        def write(self, _s): return 0
        def flush(self): pass
        def reconfigure(self, *a, **kw): pass
        def isatty(self): return False
        def fileno(self): raise OSError("no fileno")

    # 僅在 stdout/stderr 缺失或不可寫時替換 (有真控制檯時保持原樣)
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            setattr(sys, name, _NullStream())


def _setup_logging() -> None:
    """設定日誌寫入磁碟到 data/desktop.log。

    背景: spec 裡 console=False (桌面應用不彈黑窗), 導致 logging 預設輸出的
    stderr 被吞掉 —— 啟動期任何異常使用者都看不到, 表現為「雙擊一閃退出、查無日誌」。
    這裡追加一個 FileHandler, 讓日誌同時落到 data_dir/desktop.log, 事後可查。

    時序注意: data_dir 在 import app.config 時路徑已可用, 但目錄此刻可能不存在
    (frozen 首次執行), 必須先 mkdir, 否則 FileHandler 打開檔案會拋 FileNotFoundError。
    不用第二次 basicConfig (它「首次呼叫才生效」), 改用 addHandler 追加。
    """
    try:
        from app.config import settings

        log_dir = settings.data_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(
            log_dir / "desktop.log",
            mode="a",            # 追加, 保留歷史 (排查時往往需要對比多次啟動)
            encoding="utf-8",
            errors="replace",    # 容錯: 對齊 __init__.py 的 stderr 重配, 避免中文/emoji 觸發 UnicodeEncodeError
        )
        handler.setFormatter(
            _SanitizingFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logging.getLogger().addHandler(handler)
    except Exception as e:
        # 日誌寫入磁碟失敗不阻斷啟動 (開發模式 data_dir 可能不可寫)
        logger.warning("記錄檔初始化失敗, 僅輸出到 stderr: %s", e)


def _show_crash(title: str, text: str) -> None:
    """崩潰時彈原生 MessageBox 提示使用者 (僅 Windows)。

    console=False 下使用者看不到任何輸出, 崩潰時彈一個原生錯誤框, 讓使用者至少
    知道「程式崩了 + 原因」, 並可截圖反饋。非 Windows 用日誌降級, 不調 ctypes。

    ctypes 是 Python 標準庫, 不引入新依賴; MessageBoxW 是 Unicode 版本 (W 後綴),
    支援中文標題/正文。0x10 = MB_ICONERROR (紅色錯誤圖標)。
    """
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, _sanitize_log(text), title, 0x10)
        except Exception as e:
            logger.error("彈框失敗 (已寫記錄檔): %s", e)
    else:
        logger.error("%s: %s", title, text)


def _pid_alive(pid: int) -> bool:
    """檢查指定 PID 的行程是否存活。"""
    import os

    if os.name == "nt":
        # Windows: 0 表示存在, 其它是異常
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    else:
        try:
            os.kill(pid, 0)  # signal 0 = 探測存活, 不實際發訊號
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
        host="127.0.0.1",  # 僅本機, 不暴露外網 (桌面版無需遠端存取)
        port=port,
        log_level="info",
        access_log=False,    # 桌面版不需要存取日誌
        loop="auto",
    )
    return uvicorn.Server(config)


def _run_uvicorn(server, done_event: threading.Event) -> None:
    """背景執行 owned uvicorn, 退出時通知 lifecycle 管理端。"""
    try:
        server.run()
    except Exception:
        # server.run() 內部跑 lifespan 啟動鏈, 任一步拋異常都會冒到這裡。
        # 不捕獲則執行緒靜默死亡, 主執行緒 _wait_for_server 傻等滿 60s 後報「超時」,
        # 真正的崩潰原因 (如某個原生庫載入失敗 / 缺 hidden import) 永遠看不到。
        logger.exception("uvicorn 後端啟動/執行失敗")
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
    """輪詢 health 介面直到後端就緒或超時。

    比 monkey-patch uvicorn 內部方法更健壯, 不依賴版本內部實作。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _project_backend_ready(port):
            return True
        time.sleep(0.1)
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


_SPLASH_HTML = (
    "<!doctype html><meta charset='utf-8'><body style='margin:0;height:100vh;display:flex;"
    "align-items:center;justify-content:center;font:20px sans-serif;color:#555;background:#f6f4ef'>"
    "Nanachi 台股看板 啟動中…</body>"
)


def _open_window(start_backend) -> None:
    """主執行緒: 先用 pywebview 立刻顯示啟動畫面, 後端在背景就緒後再載入正式頁面。

    start_backend() 回傳要載入的 URL; 它失敗時關窗並把例外交回呼叫端處理。
    """
    import webview  # type: ignore[import-not-found]

    # 設定 → 備份與轉移 需要把 .twstock-backup 存到使用者選的位置 (pywebview 預設禁止下載)。
    webview.settings["ALLOW_DOWNLOADS"] = True
    window = webview.create_window(
        _APP_NAME,
        html=_SPLASH_HTML,
        width=1440,
        height=900,
        min_size=(1024, 700),
        # 桌面版固定單視窗, 禁用外部瀏覽器跳轉
        confirm_close=False,
        # pywebview defaults text_select=False, which blocks selecting and copying any text.
        text_select=True,
    )
    # pywebview 預設 private_mode=True: 關窗即清空 localStorage (持股紀錄、字級、主題全丟)。
    # 改用固定的本機儲存位置, 讓瀏覽器端資料跨重啟保留。
    from app.config import settings

    storage = Path(settings.data_dir) / "user_data" / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    failure: list[Exception] = []

    def boot() -> None:
        try:
            url = start_backend()
            logger.info("載入桌面視窗: %s", url)
            window.load_url(url)
        except Exception as exc:
            failure.append(exc)
            window.destroy()

    # pywebview 會阻塞主執行緒直到視窗關閉
    webview.start(boot, debug=False, private_mode=False, storage_path=str(storage))
    if failure:
        raise failure[0]


def main() -> int:
    """桌面客戶端主入口。回傳行程退出碼。"""
    # 必須最先執行: console=False 下 stdout/stderr 可能無效, 不守護會導致
    # 後續 logging.basicConfig 建立的 StreamHandler 寫日誌時行程崩潰。
    _guard_streams()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # 追加檔案日誌: console=False 下 stderr 被吞, 必須寫入磁碟否則查無對證。
    # 放在 basicConfig 之後 (它先建好 root logger 的格式), 這裡只追加 handler。
    _setup_logging()

    try:
        _ensure_data_dir_writable()
    except Exception as exc:
        # 資料目錄不可寫是致命錯誤, 無法繼續
        _show_crash(f"{_APP_NAME} 啟動失敗", str(exc))
        return 1

    _migrate_legacy_data_before_seed()
    seed_result = _install_bundled_release_seed()

    # 單實例: 已執行則退出
    if not _acquire_single_instance():
        return 0

    server = None
    server_thread = None
    try:
        if not _frontend_dist_ready():
            raise RuntimeError("找不到 frontend/dist, 請先執行 frontend 的 pnpm build")

        def start_backend() -> str:
            nonlocal server, server_thread
            port = _BASE_PORT
            if _port_is_open(port):
                # A backend that is still warming up (autostart at login) answers /openapi.json
                # slower than one probe allows; only call the port foreign after retrying.
                if not _wait_for_server(port, timeout=_EXISTING_BACKEND_TIMEOUT):
                    raise RuntimeError(f"連接埠 {port} 已被其他程式使用, 未啟動也未終止該程式")
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

            return f"http://127.0.0.1:{port}/"

        _open_window(start_backend)

        logger.info("視窗已關閉, 桌面版退出")
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        # 頂層保底: console=False 下未捕獲異常會「一閃退出」且無任何反饋。
        # 寫完整 traceback 到 data/desktop.log, 並彈原生 MessageBox 讓使用者截圖反饋。
        # 必須排在 KeyboardInterrupt 之後 —— Exception 是基類, 在前會遮蔽它。
        logger.exception("桌面客戶端啟動失敗")
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

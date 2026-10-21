"""AI 連線燈號與本機 LM Studio 模型自動載入。

- ai_status(): 側欄燈號用的輕量檢查，不呼叫生成 (不花 token)。
- ensure_local_model_loaded(): 看板啟動時，若目前 AI 指向本機 LM Studio 且模型沒載入
  (或載入的上下文小於 AI 設定)，用 LM Studio 自帶的 `lms` CLI 啟動 server / 以設定的上下文載入模型。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _lms_path() -> str | None:
    found = shutil.which("lms")
    if found:
        return found
    candidate = Path.home() / ".lmstudio" / "bin" / ("lms.exe" if os.name == "nt" else "lms")
    return str(candidate) if candidate.exists() else None


def _origin(base_url: str) -> str | None:
    url = urlparse(base_url or "")
    if url.hostname not in _LOCAL_HOSTS:
        return None
    return f"{url.scheme or 'http'}://{url.netloc}"


def _lmstudio_model(origin: str, model: str) -> tuple[str, dict]:
    """('loaded' | 'not-loaded' | 'missing' | 'down' | 'not-lmstudio', the /api/v0/models entry)."""
    try:
        resp = httpx.get(f"{origin}/api/v0/models", timeout=3)
    except httpx.HTTPError:
        return "down", {}
    if resp.status_code != 200:
        return "not-lmstudio", {}
    for item in resp.json().get("data", []):
        if item.get("id") == model:
            return ("loaded" if item.get("state") == "loaded" else "not-loaded"), item
    return "missing", {}


def _context_issue(item: dict, wanted: int) -> str | None:
    """'context-unsupported' when no load can reach *wanted*; 'small-context' when a reload can."""
    if wanted <= 0:
        return None
    maximum = item.get("max_context_length")
    if isinstance(maximum, int) and wanted > maximum:
        return "context-unsupported"
    loaded = item.get("loaded_context_length")
    if item.get("state") == "loaded" and isinstance(loaded, int) and loaded < wanted:
        return "small-context"
    return None


def _lmstudio_state(origin: str, model: str, min_context: int = 0) -> str:
    """_lmstudio_model state, or 'small-context' / 'context-unsupported' for *min_context*."""
    state, item = _lmstudio_model(origin, model)
    return _context_issue(item, min_context) or state


def _lms(*args: str) -> subprocess.CompletedProcess | None:
    lms = _lms_path()
    if not lms:
        return None
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run([lms, *args], capture_output=True, timeout=180, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("lms %s failed: %s", args[0], type(exc).__name__)
        return None
    if proc.returncode != 0:
        logger.warning("lms %s exit %s", args[0], proc.returncode)
    return proc


def _run_lms(*args: str) -> bool:
    proc = _lms(*args)
    return proc is not None and proc.returncode == 0


def _lms_loaded(identifier: str) -> dict | None:
    """The `lms ps` entry for *identifier* (has modelKey / contextLength), if lms sees it."""
    proc = _lms("ps", "--json")
    if proc is None or proc.returncode != 0:
        return None
    try:
        entries = json.loads(proc.stdout)
    except ValueError:
        return None
    return next((e for e in entries if isinstance(e, dict) and e.get("identifier") == identifier), None)


def _reload_with_context(identifier: str, current: int | None, wanted: int) -> bool:
    """Reload a loaded model at *wanted* context; True only if it ends loaded at *wanted*."""
    entry = _lms_loaded(identifier)
    # lms has no --host: it drives its default instance only. Touch nothing unless
    # it sees this very model at the context the configured server reports.
    if not entry or not entry.get("modelKey") or entry.get("contextLength") != current:
        return False
    if not _run_lms("unload", identifier):
        return False
    # Load by modelKey: the configured name may be a custom identifier (alias).
    base = ("load", entry["modelKey"], "--identifier", identifier, "--yes")
    if _run_lms(*base, "--context-length", str(wanted)):
        return True
    # e.g. not enough VRAM: put the previous context back instead of leaving it unloaded.
    _run_lms(*base, "--context-length", str(current))
    return False


def ensure_local_model_loaded() -> str:
    from app.services.ai_provider import snapshot_ai_provider_config

    cfg = snapshot_ai_provider_config()
    origin = _origin(cfg.base_url)
    if origin is None or not cfg.model:
        return "skip"
    # LM Studio otherwise loads with its own per-model default (often 16384), and
    # prompts the AI settings allow would then be rejected by the server.
    ctx = cfg.context_window
    state, item = _lmstudio_model(origin, cfg.model)
    if state == "down" and _run_lms("server", "start"):
        state, item = _lmstudio_model(origin, cfg.model)
    issue = _context_issue(item, ctx)
    if issue == "small-context":
        _reload_with_context(cfg.model, item.get("loaded_context_length"), ctx)
    # An unsupported size keeps LM Studio's default rather than failing the load.
    load_args = ("--context-length", str(ctx)) if ctx > 0 and issue != "context-unsupported" else ()
    if state == "not-loaded":
        _run_lms("load", cfg.model, "--yes", *load_args)
    state = _lmstudio_state(origin, cfg.model, ctx)
    logger.info("local AI model %s: %s", cfg.model, state)
    return state


def start_local_model_autoload() -> None:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    threading.Thread(target=ensure_local_model_loaded, name="local-llm-autoload", daemon=True).start()


def ai_status() -> dict:
    """{'status': 'ok'|'error'|'unconfigured', 'reason': str}"""
    from app.services.ai_provider import (
        CLI_PROVIDER_LABELS,
        is_subscription_cli_provider,
        snapshot_ai_provider_config,
        subscription_cli_available,
    )

    cfg = snapshot_ai_provider_config()
    if is_subscription_cli_provider(cfg.provider):
        label = CLI_PROVIDER_LABELS[cfg.provider]
        if not cfg.model:
            return {"status": "unconfigured", "reason": f"{label} 模型未設定"}
        ok = subscription_cli_available(cfg.provider)
        return {"status": "ok" if ok else "error", "reason": f"{label} 可用" if ok else f"{label} 無法執行"}
    if not cfg.model or not cfg.base_url:
        return {"status": "unconfigured", "reason": "尚未設定 AI 模型"}

    origin = _origin(cfg.base_url)
    if origin is not None:
        state = _lmstudio_state(origin, cfg.model, cfg.context_window)
        reasons = {
            "loaded": ("ok", "本機模型已載入"),
            "small-context": ("error", f"LM Studio 載入的上下文小於 AI 設定的 {cfg.context_window}，重開看板會自動重新載入"),  # noqa: RUF001
            "context-unsupported": ("error", f"AI 設定的上下文 {cfg.context_window} 超過模型上限，請在 AI 設定調小"),  # noqa: RUF001
            "not-loaded": ("error", f"LM Studio 沒有載入模型 {cfg.model}"),
            "missing": ("error", f"LM Studio 找不到模型 {cfg.model}"),
            "down": ("error", "本機 AI 服務沒有開啟"),
        }
        if state in reasons:
            status, reason = reasons[state]
            return {"status": status, "reason": reason}

    # 雲端/其他 OpenAI 相容服務: 列模型清單即可驗證網路與 Key，不花 token。
    try:
        resp = httpx.get(
            cfg.base_url.rstrip("/") + "/models",
            headers={"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {},
            timeout=5,
        )
    except httpx.HTTPError:
        return {"status": "error", "reason": "AI 服務連線失敗"}
    if resp.status_code in (401, 403):
        return {"status": "error", "reason": "AI API Key 無效或沒有權限"}
    # 有些服務不提供 /models (404)，能連上就視為正常。
    return {"status": "ok", "reason": "AI 服務可連線"}

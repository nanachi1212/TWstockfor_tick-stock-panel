"""AI 連線燈號與本機 LM Studio 模型自動載入。

- ai_status(): 側欄燈號用的輕量檢查，不呼叫生成 (不花 token)。
- ensure_local_model_loaded(): 看板啟動時，若目前 AI 指向本機 LM Studio 且模型沒載入，
  用 LM Studio 自帶的 `lms` CLI 啟動 server / 載入模型。
"""
from __future__ import annotations

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


def _lmstudio_state(origin: str, model: str) -> str:
    """'loaded' | 'not-loaded' | 'missing' | 'down' | 'not-lmstudio'."""
    try:
        resp = httpx.get(f"{origin}/api/v0/models", timeout=3)
    except httpx.HTTPError:
        return "down"
    if resp.status_code != 200:
        return "not-lmstudio"
    for item in resp.json().get("data", []):
        if item.get("id") == model:
            return "loaded" if item.get("state") == "loaded" else "not-loaded"
    return "missing"


def _run_lms(*args: str) -> bool:
    lms = _lms_path()
    if not lms:
        return False
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run([lms, *args], capture_output=True, timeout=180, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("lms %s failed: %s", args[0], type(exc).__name__)
        return False
    if proc.returncode != 0:
        logger.warning("lms %s exit %s", args[0], proc.returncode)
    return proc.returncode == 0


def ensure_local_model_loaded() -> str:
    from app.services.ai_provider import snapshot_ai_provider_config

    cfg = snapshot_ai_provider_config()
    origin = _origin(cfg.base_url)
    if origin is None or not cfg.model:
        return "skip"
    state = _lmstudio_state(origin, cfg.model)
    if state == "down" and _run_lms("server", "start"):
        state = _lmstudio_state(origin, cfg.model)
    if state == "not-loaded" and _run_lms("load", cfg.model, "--yes"):
        state = _lmstudio_state(origin, cfg.model)
    logger.info("local AI model %s: %s", cfg.model, state)
    return state


def start_local_model_autoload() -> None:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    threading.Thread(target=ensure_local_model_loaded, name="local-llm-autoload", daemon=True).start()


def ai_status() -> dict:
    """{'status': 'ok'|'error'|'unconfigured', 'reason': str}"""
    from app.services.ai_provider import (
        codex_cli_available,
        is_codex_cli_provider,
        snapshot_ai_provider_config,
    )

    cfg = snapshot_ai_provider_config()
    if is_codex_cli_provider(cfg.provider):
        if not cfg.model:
            return {"status": "unconfigured", "reason": "Codex 模型未設定"}
        ok = codex_cli_available()
        return {"status": "ok" if ok else "error", "reason": "Codex CLI 可用" if ok else "Codex CLI 無法執行"}
    if not cfg.model or not cfg.base_url:
        return {"status": "unconfigured", "reason": "尚未設定 AI 模型"}

    origin = _origin(cfg.base_url)
    if origin is not None:
        state = _lmstudio_state(origin, cfg.model)
        reasons = {
            "loaded": ("ok", "本機模型已載入"),
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

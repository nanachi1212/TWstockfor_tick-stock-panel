"""AI provider adapter for OpenAI-compatible APIs and local subscription CLIs (Codex, Claude Code)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from app import secrets_store
from app.config import settings

OPENAI_COMPAT_PROVIDER = "openai_compat"
OPENAI_PROVIDER = "openai"
CODEX_CLI_PROVIDER = "codex_cli"
CODEX_DEFAULT_COMMAND = "codex"
CLAUDE_CLI_PROVIDER = "claude_cli"
CLAUDE_DEFAULT_COMMAND = "claude"
# Providers that run the user's own logged-in CLI (subscription), not an API key.
SUBSCRIPTION_CLI_PROVIDERS = frozenset({CODEX_CLI_PROVIDER, CLAUDE_CLI_PROVIDER})
CLI_PROVIDER_LABELS = {CODEX_CLI_PROVIDER: "Codex CLI", CLAUDE_CLI_PROVIDER: "Claude Code CLI"}
# Claude Code flags proven to drop user CLAUDE.md, settings, MCP, skills and tools
# (about 57k -> 4k input tokens). --bare is not usable: it never reads the subscription login.
_CLAUDE_ISOLATION_ARGS = (
    "--tools", "",
    "--setting-sources", "",
    "--strict-mcp-config",
    "--disable-slash-commands",
    "--no-session-persistence",
)
CODEX_SUPPORTED_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}
OPENAI_DEFAULT_REASONING_EFFORT = "high"

logger = logging.getLogger(__name__)

_CODEX_ENV_ALLOWLIST = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "TEMP",
    "TMP",
    "TMPDIR",
    "SHELL",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
)

Message = dict[str, str]


class AIOutputTruncated(RuntimeError):  # noqa: N818 - public exception name is retained for compatibility
    """Raised when the provider signals finish_reason='length' (max_tokens hit mid-output)."""

    def __init__(self, partial_content: str = "", message: str = "AI 輸出因 token 上限截斷"):
        super().__init__(message)
        self.partial_content = partial_content


class AIEmptyContentError(RuntimeError):
    """Raised when a provider returns a completion choice without message.content."""


class AIStructuredOutputError(RuntimeError):
    """Raised when a structured task returns malformed JSON."""

    def __init__(self, invalid_content: str = "", message: str = "AI provider returned invalid JSON"):
        super().__init__(message)
        self.invalid_content = invalid_content


@dataclass(frozen=True)
class AIProviderConfigSnapshot:
    provider: str
    model: str
    api_key: str = field(repr=False)
    profile_name: str = ""
    base_url: str = ""
    user_agent: str = ""
    max_output_tokens: int = 0
    context_window: int = 0
    reasoning_effort: str = ""
    codex_command: str = CODEX_DEFAULT_COMMAND
    codex_reasoning_effort: str = ""


def snapshot_ai_provider_config(tier: Literal["quick", "deep"] = "quick") -> AIProviderConfigSnapshot:
    """Read provider settings once so an in-flight research call stays consistent.

    ``tier="deep"`` uses the profile marked as the deep model (judge / final
    decision calls) and falls back to the active profile when none is marked.
    """
    stored = secrets_store.load()

    def configured(name: str, fallback: str) -> str:
        value = stored.get(name)
        return str(value if value else getattr(settings, name, fallback) or fallback)

    # An active profile is the authoritative endpoint/model/credential source.
    # Legacy fields are used only when no profile exists.
    from app.services import ai_key_profiles as _profiles
    active_cfg = (tier == "deep" and _profiles.get_deep_profile_config()) or _profiles.get_active_profile_config()

    if active_cfg:
        resolved_key = active_cfg["key"]
        profile_name = active_cfg.get("name", "")
        resolved_base_url = active_cfg["base_url"]
        active_model = active_cfg["model"]
        active_provider = active_cfg["provider"] or configured("ai_provider", OPENAI_COMPAT_PROVIDER)
    else:
        resolved_key = str(stored.get("ai_api_key") or settings.ai_api_key or "")
        profile_name = "legacy"
        resolved_base_url = configured("ai_base_url", settings.ai_base_url)
        active_model = ""
        active_provider = configured("ai_provider", OPENAI_COMPAT_PROVIDER)

    target = str(active_cfg.get("id", "")) if active_cfg else secrets_store.AI_LEGACY_TARGET
    if secrets_store.ai_target_unconfirmed(target):
        resolved_key = ""  # 端點來自未驗證的還原, 等使用者重新確認這一組前不送任何金鑰

    provider = active_provider
    if provider == CODEX_CLI_PROVIDER and active_cfg and active_model:
        # A subscription profile carries its own model; legacy ai_codex_model is only a fallback.
        model = normalize_codex_model(active_model)
    elif provider == CODEX_CLI_PROVIDER:
        model_value = stored.get("ai_codex_model")
        if model_value is None:
            model_value = stored.get("ai_model")
        model = normalize_codex_model(str(model_value or ""))
    else:
        model = active_model if active_cfg else configured("ai_model", settings.ai_model)
    if "ai_reasoning_effort" in stored:
        reasoning_effort = str(stored.get("ai_reasoning_effort") or "").strip()
    else:
        reasoning_effort = OPENAI_DEFAULT_REASONING_EFFORT
    return AIProviderConfigSnapshot(
        provider=provider,
        model=model,
        api_key=resolved_key,
        profile_name=profile_name,
        base_url=resolved_base_url,
        user_agent=configured("ai_user_agent", settings.ai_user_agent),
        max_output_tokens=current_ai_max_output_tokens(),
        context_window=current_ai_context_window(),
        reasoning_effort=reasoning_effort,
        codex_command=normalize_codex_command(configured("ai_codex_command", settings.ai_codex_command), strict=False),
        codex_reasoning_effort=normalize_codex_reasoning_effort(
            str(stored.get("ai_codex_reasoning_effort", settings.ai_codex_reasoning_effort) or ""),
        ),
    )

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


# ----------------------------------------------------------------
# 使用者 focus 輸入淨化 — 防止透過"特別關注"繞過紅線誘導 AI 給出買賣建議
# 命中任一敏感詞時,整個 focus 被丟棄(回傳空串),由各 analyzer 據此跳過注入。
# ----------------------------------------------------------------
_FOCUS_BLOCKLIST = re.compile(
    r"買入|賣出|加倉|減倉|輕倉|重倉|半倉|全倉|倉位|止損|止盈|"
    r"操作建議|買賣點|買賣區間|建倉|平倉|清倉|調倉|"
    r"追高|低吸|反包|抄底|逃頂|進攻|防守|"
    r"激進|穩健|保守|目標價|能漲|會跌|預測漲|預測跌|"
    r"薦股|推薦買|推薦賣|值得投資|現在買|可以買|能買|要不要買|買嗎|賣嗎|"
    r"明日基調|交易計劃|下單",
    re.IGNORECASE,
)


def current_ai_provider() -> str:
    import contextlib
    with contextlib.suppress(Exception):
        from app.services import ai_key_profiles as _profiles
        active_cfg = _profiles.get_active_profile_config()
        if active_cfg and active_cfg.get("provider"):
            return active_cfg["provider"]
    return secrets_store.get_ai_config("ai_provider", settings.ai_provider) or OPENAI_COMPAT_PROVIDER


def current_openai_model() -> str:
    import contextlib
    with contextlib.suppress(Exception):
        from app.services import ai_key_profiles as _profiles
        active_cfg = _profiles.get_active_profile_config()
        if active_cfg and active_cfg.get("model"):
            return active_cfg["model"]
    return secrets_store.get_ai_config("ai_model", settings.ai_model)


def current_codex_model() -> str:
    stored = secrets_store.load()
    model = stored.get("ai_codex_model")
    # 舊版本的兩種 provider 共用 ai_model。僅在舊設定仍啟用 Codex 時回退讀取,
    # 避免把正常的 OpenAI-compatible 模型誤當作 Codex 模型。
    if model is None and current_ai_provider() == CODEX_CLI_PROVIDER:
        model = stored.get("ai_model")
    return normalize_codex_model(str(model or ""))


def current_ai_model() -> str:
    if current_ai_provider() == CODEX_CLI_PROVIDER:
        return current_codex_model()
    return current_openai_model()


def current_openai_reasoning_effort() -> str:
    stored = secrets_store.load()
    if "ai_reasoning_effort" not in stored:
        return OPENAI_DEFAULT_REASONING_EFFORT
    return str(stored.get("ai_reasoning_effort") or "").strip()


def current_ai_max_output_tokens() -> int:
    """當前 AI 輸出上限 (max_tokens): secrets.json 優先, 否則 config 預設。"""
    return secrets_store.get_ai_config_int("ai_max_output_tokens", settings.ai_max_output_tokens)


def current_ai_request_timeout() -> float:
    """單次 AI 請求逾時(秒): secrets.json 優先, 否則 config 預設; 非正數回退預設。"""
    value = secrets_store.get_ai_config_int("ai_request_timeout", 0) or settings.ai_request_timeout
    return float(value) if value > 0 else settings.ai_request_timeout


def current_ai_context_window() -> int:
    """當前 AI 輸入上下文視窗上限 (約 token): secrets.json 優先, 否則 config 預設。"""
    return secrets_store.get_ai_config_int("ai_context_window", settings.ai_context_window)


def _resolve_max_tokens(max_tokens: int | None, *, cap: int | None = None) -> int | None:
    """顯式傳入的 max_tokens 限制到設定輸出上限; None 保持 None。

    None = 不傳該參數(推理型模型的思考 token 計入 max_tokens 預算,
    顯式限制會擠佔正文, 語意見 generate_ai_text), 不能對應成設定上限。
    """
    if max_tokens is None:
        return None
    cap = current_ai_max_output_tokens() if cap is None else cap
    return max(1, min(int(max_tokens), cap))


def _estimate_input_tokens(messages: Sequence[Message]) -> int:
    """粗略估算輸入 token 數: 中文按 1 字 1 token, 其餘按 4 字元 1 token。"""
    total = 0
    for m in messages:
        text = str(m.get("content") or "")
        cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
        total += cjk + (len(text) - cjk) // 4 + 1
    return max(1, total)


def _check_input_budget(
    messages: Sequence[Message],
    *,
    max_tokens: int | None,
    context_window: int | None = None,
    max_output_tokens: int | None = None,
) -> None:
    """輸入估算超出上下文視窗時給出明確的錯誤訊息, 避免來源 400 或靜默截斷。

    token 計數不精確, 僅作安全網: 用中/英文混合估算, 只有明顯超窗才拒絕;
    使用者可在 AI 設定裡調大『上下文視窗』。max_tokens=None(輸出放開)時用
    設定上限作輸出預留估計。
    """
    context_window = current_ai_context_window() if context_window is None else context_window
    if context_window <= 0:
        return
    configured_output_cap = current_ai_max_output_tokens() if max_output_tokens is None else max_output_tokens
    output_reserve = max_tokens if max_tokens is not None else configured_output_cap
    est = _estimate_input_tokens(messages)
    if est + output_reserve > context_window:
        raise ValueError(
            f"輸入過長: 估算輸入約 {est} tokens, 加上輸出預算 {max_tokens} tokens, "
            f"超過上下文視窗 {context_window}。請縮短輸入, 或在 AI 設定中調大『上下文視窗』。"
        )


def current_codex_command() -> str:
    return normalize_codex_command(
        secrets_store.get_ai_config("ai_codex_command", settings.ai_codex_command),
        strict=False,
    )


def current_codex_reasoning_effort() -> str:
    return normalize_codex_reasoning_effort(
        secrets_store.get_ai_config(
            "ai_codex_reasoning_effort",
            settings.ai_codex_reasoning_effort,
        )
    )


def is_codex_cli_provider(provider: str | None = None) -> bool:
    return (provider or current_ai_provider()) == CODEX_CLI_PROVIDER


def is_subscription_cli_provider(provider: str | None = None) -> bool:
    return (provider or current_ai_provider()) in SUBSCRIPTION_CLI_PROVIDERS


def subscription_cli_available(provider: str | None = None) -> bool:
    provider = provider or current_ai_provider()
    if provider == CLAUDE_CLI_PROVIDER:
        return claude_cli_available()
    return codex_cli_available()


def normalize_codex_model(model: str) -> str:
    value = model.strip()
    aliases = {
        "gpt5.5": "gpt-5.5",
        "gpt5.6": "gpt-5.6-sol",
        "gpt5.6-sol": "gpt-5.6-sol",
        "gpt5.6-terra": "gpt-5.6-terra",
        "gpt5.6-luna": "gpt-5.6-luna",
    }
    return aliases.get(value.lower(), value)


def normalize_codex_reasoning_effort(effort: str | None) -> str:
    value = (effort or "").strip().lower()
    return value if value in CODEX_SUPPORTED_REASONING_EFFORTS else ""


def normalize_codex_command(command: str | None, *, strict: bool = True) -> str:
    value = (command or "").strip()
    if not value or value.lower() == CODEX_DEFAULT_COMMAND:
        return CODEX_DEFAULT_COMMAND
    if strict:
        raise ValueError("Codex CLI 僅支援使用預設 codex 命令自動解析, 不支援自訂可執行路徑")
    return CODEX_DEFAULT_COMMAND


_VERSION_SEGMENT_RE = re.compile(r"/v\d+(?:\.\d+)?$", re.IGNORECASE)


def normalize_openai_base_url(url: str) -> str:
    """Return the OpenAI-compatible base URL expected by the OpenAI SDK.

    識別 URL 中已有的版本段 (/v1、/v2、/v4 等) 時保持原樣 —— 部分 OpenAI 相容
    服務用非 v1 的版本號 (如智譜 GLM 用 /api/paas/v4), 舊實作無條件補 /v1 會拼成
    不存在的 /api/paas/v4/v1/chat/completions 導致 404。僅在無版本段時才補 /v1。
    """
    base = (url or "").strip().rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")].rstrip("/")
    if _VERSION_SEGMENT_RE.search(base):
        return base
    return f"{base}/v1"


def codex_cli_available() -> bool:
    """Codex CLI 是否真正可用: 解析命令後實跑一次 --version。

    僅 which 到二進位不夠 — npm 殼缺平台原生二進位時同樣存在於 PATH,
    但一執行就出錯(如 "Codex CLI not available"), 狀態頁會誤報已連接。
    """
    try:
        base = _codex_base_command()
    except RuntimeError:
        return False
    try:
        proc = subprocess.run(
            [*base, "--version"],
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def claude_cli_available() -> bool:
    """Claude Code CLI 是否可執行 (只檢查 --version, 不代表訂閱登入有效)。"""
    try:
        base = _claude_base_command()
    except RuntimeError:
        return False
    try:
        proc = subprocess.run([*base, "--version"], capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


# The CLI probe spawns `<cli> --version`; status endpoints poll ai_configured(), so the
# answer is kept briefly per provider and dropped whenever AI settings are saved.
_CLI_PROBE_TTL_S = 60.0
_cli_probe_cache: dict[str, tuple[float, bool]] = {}


def reset_ai_configured_cache() -> None:
    """Force the next ai_configured() to probe the CLI again (call after saving AI settings)."""
    _cli_probe_cache.clear()


def ai_configured(provider: str | None = None) -> bool:
    provider = provider or current_ai_provider()
    if not is_subscription_cli_provider(provider):
        return bool(secrets_store.get_ai_key())
    now = time.monotonic()
    cached = _cli_probe_cache.get(provider)
    if cached is not None and now - cached[0] < _CLI_PROBE_TTL_S:
        return cached[1]
    available = subscription_cli_available(provider)
    _cli_probe_cache[provider] = (now, available)
    return available


# ── 本機模型暖機: 同一行程內第一次呼叫某個本機模型時, 逾時放寬到兩倍 (上限 300 秒) ──
_WARMED_UP: set[str] = set()


def _is_local_base_url(base_url: str | None) -> bool:
    url = (base_url or "").lower()
    return any(host in url for host in ("127.0.0.1", "localhost", "0.0.0.0", "host.docker.internal"))


def warmup_timeout(timeout: float, config_snapshot: AIProviderConfigSnapshot) -> float:
    """首檔含模型載入會明顯變慢; 第一次呼叫本機模型時給雙倍逾時, 之後恢復設定值。"""
    key = f"{config_snapshot.base_url}|{config_snapshot.model}"
    if key in _WARMED_UP or not _is_local_base_url(config_snapshot.base_url):
        return timeout
    _WARMED_UP.add(key)
    return max(timeout, min(timeout * 2, 300.0))


async def generate_ai_text(
    messages: Sequence[Message],
    *,
    temperature: float | None = 0.3,
    max_tokens: int | None = 3000,
    timeout: float = 180.0,
    provider: str | None = None,
    model: str | None = None,
    config_snapshot: AIProviderConfigSnapshot | None = None,
    structured_output: bool = False,
    request_attempt: int = 0,
) -> str:
    """Return a complete AI response from the currently configured provider.

    max_tokens=None 表示不傳該參數(輸出上限交給服務端預設) — 推理型模型
    (如 deepseek reasoner 系)的思考 token 計入 max_tokens 預算, 顯式限制
    會擠佔正文甚至全部吃光(正文 0 字 + finish=length), 長分析類呼叫應放開。
    顯式傳入的數值會被限制到設定的輸出上限 (AI 設定可調)。
    """
    if config_snapshot is None:
        config_snapshot = snapshot_ai_provider_config()
        if provider is not None or model is not None:
            config_snapshot = AIProviderConfigSnapshot(
                provider=provider or config_snapshot.provider,
                model=model or config_snapshot.model,
                api_key=config_snapshot.api_key,
                profile_name=config_snapshot.profile_name,
                base_url=config_snapshot.base_url,
                user_agent=config_snapshot.user_agent,
                max_output_tokens=config_snapshot.max_output_tokens,
                context_window=config_snapshot.context_window,
                reasoning_effort=config_snapshot.reasoning_effort,
                codex_command=config_snapshot.codex_command,
                codex_reasoning_effort=config_snapshot.codex_reasoning_effort,
            )
    elif provider is not None or model is not None:
        config_snapshot = AIProviderConfigSnapshot(
            provider=provider or config_snapshot.provider,
            model=model or config_snapshot.model,
            api_key=config_snapshot.api_key,
            profile_name=config_snapshot.profile_name,
            base_url=config_snapshot.base_url,
            user_agent=config_snapshot.user_agent,
            max_output_tokens=config_snapshot.max_output_tokens,
            context_window=config_snapshot.context_window,
            reasoning_effort=config_snapshot.reasoning_effort,
            codex_command=config_snapshot.codex_command,
            codex_reasoning_effort=config_snapshot.codex_reasoning_effort,
        )

    timeout = warmup_timeout(timeout, config_snapshot)
    max_tokens = _resolve_max_tokens(
        max_tokens,
        cap=config_snapshot.max_output_tokens,
    )
    _check_input_budget(
        messages,
        max_tokens=max_tokens,
        context_window=config_snapshot.context_window,
        max_output_tokens=config_snapshot.max_output_tokens,
    )
    provider = config_snapshot.provider
    model = config_snapshot.model
    try:
        codex_provider = is_codex_cli_provider(provider) if provider is not None else is_codex_cli_provider()
    except TypeError:
        codex_provider = is_codex_cli_provider()
    if codex_provider:
        codex_kwargs = {"max_tokens": max_tokens, "timeout": max(timeout, 600.0)}
        if model is not None:
            codex_kwargs["model"] = model
        codex_kwargs["config_snapshot"] = config_snapshot
        return await _run_codex_cli(messages, **codex_kwargs)
    if provider == CLAUDE_CLI_PROVIDER:
        return await _run_claude_cli(
            messages, max_tokens=max_tokens, timeout=max(timeout, 300.0), config_snapshot=config_snapshot,
        )

    import inspect
    sig = inspect.signature(_run_openai_once)
    accepted = set(sig.parameters.keys())
    has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())

    openai_kwargs = {
        "temperature": temperature,
        "max_tokens": max_tokens,
        "timeout": timeout,
    }
    if has_varkw or "structured_output" in accepted:
        openai_kwargs["structured_output"] = structured_output
    if has_varkw or "request_attempt" in accepted:
        openai_kwargs["request_attempt"] = request_attempt
    if (has_varkw or "model" in accepted) and model is not None:
        openai_kwargs["model"] = model
    if (has_varkw or "config_snapshot" in accepted) and config_snapshot is not None:
        openai_kwargs["config_snapshot"] = config_snapshot

    return await _run_openai_once(
        messages,
        **openai_kwargs,
    )


EMPTY_CONTENT_RETRY_MESSAGE = (
    "前次回覆的 message.content 為空。請直接輸出完整 JSON 物件,"
    "不要輸出思考過程、Markdown 或其他文字。"
)
INVALID_JSON_RETRY_MESSAGE = (
    "前次回覆不是合法的完整 JSON。請直接重新輸出完整 JSON 物件,"
    "不要輸出 Markdown、解釋或其他文字。"
)


async def generate_structured_ai_text(
    messages: Sequence[Message],
    *,
    truncated_retry_message: str,
    temperature: float | None,
    max_tokens: int | None,
    timeout: float,
    config_snapshot: AIProviderConfigSnapshot | None = None,
    generate: Callable[..., Any] | None = None,
    validate: Callable[[str], Any] | None = None,
) -> str:
    """Structured JSON task with one controlled retry for an unusable response.

    Truncated and empty responses always use the one-retry budget. When the
    caller supplies ``validate``, malformed JSON shares that same budget. A
    second unusable response raises instead of looping.
    """
    kwargs: dict[str, Any] = {
        "temperature": temperature, "max_tokens": max_tokens, "timeout": timeout,
        "config_snapshot": config_snapshot, "structured_output": True,
    }
    # Callers may pass their module-level reference so existing seams stay patchable.
    generate = generate or generate_ai_text

    async def request(call_messages: Sequence[Message], attempt: int) -> str:
        raw = await generate(call_messages, request_attempt=attempt, **kwargs)
        if validate is not None:
            try:
                validate(raw)
            except (KeyError, TypeError, ValueError):
                raise AIStructuredOutputError(raw) from None
        return raw

    try:
        return await request(messages, 0)
    except (AIOutputTruncated, AIEmptyContentError, AIStructuredOutputError) as first_exc:
        if isinstance(first_exc, AIOutputTruncated):
            retry_content = first_exc.partial_content
            retry_message = truncated_retry_message
        elif isinstance(first_exc, AIStructuredOutputError):
            retry_content = first_exc.invalid_content
            retry_message = INVALID_JSON_RETRY_MESSAGE
        else:
            retry_content = ""
            retry_message = EMPTY_CONTENT_RETRY_MESSAGE
        retry_messages = [
            *messages,
            {"role": "assistant", "content": retry_content},
            {"role": "user", "content": retry_message},
        ]
        try:
            return await request(retry_messages, 1)
        except AIOutputTruncated:
            raise AIOutputTruncated("", "AI 回覆超過輸出長度限制,請重新產生。") from None
        except AIEmptyContentError:
            raise AIEmptyContentError("AI provider returned empty content twice") from None
        except AIStructuredOutputError:
            raise AIStructuredOutputError("", "AI provider returned invalid JSON twice") from None


async def _run_openai_once(
    messages: Sequence[Message],
    *,
    temperature: float | None,
    max_tokens: int | None,
    timeout: float,
    model: str | None = None,
    config_snapshot: AIProviderConfigSnapshot | None = None,
    structured_output: bool = False,
    request_attempt: int = 0,
) -> str:
    ai_key = config_snapshot.api_key if config_snapshot is not None else secrets_store.get_ai_key()
    if not ai_key:
        raise RuntimeError("AI API Key 未設定, 請在設定頁設定")

    client = _openai_client(ai_key, timeout, config_snapshot=config_snapshot)
    model = config_snapshot.model if config_snapshot is not None else (
        model if model is not None else current_ai_model()
    )
    req_messages = list(messages)
    kwargs = _openai_kwargs(
        temperature=temperature,
        max_tokens=max_tokens,
        provider=config_snapshot.provider if config_snapshot is not None else None,
        reasoning_effort=config_snapshot.reasoning_effort if config_snapshot is not None else None,
        structured_output=structured_output,
        base_url=config_snapshot.base_url if config_snapshot is not None else "",
        model=model,
    )
    provider_retry_count = 0
    while True:
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=req_messages,
                **kwargs,
            )
            break
        except Exception as exc:
            retry_kwargs = _openai_retry_kwargs(exc, kwargs)
            if retry_kwargs is not None:
                kwargs = retry_kwargs
                provider_retry_count += 1
                continue
            if _is_openai_transport_error(exc):
                logger.info(
                    "AI provider request failed profile=%s provider=%s model=%s http_status=%s error_type=%s retry_count=%s",
                    config_snapshot.profile_name if config_snapshot is not None else "legacy",
                    config_snapshot.provider if config_snapshot is not None else current_ai_provider(),
                    model,
                    getattr(exc, "status_code", None),
                    type(exc).__name__,
                    request_attempt + provider_retry_count,
                )
                raise RuntimeError(_format_openai_error(exc)) from exc
            raise
    if not resp.choices:
        logger.info(
            "AI provider response profile=%s provider=%s model=%s http_status=200 finish_reason=%s completion_tokens=%s reasoning_tokens=%s content_length=0 reasoning_content_exists=False retry_count=%s",
            config_snapshot.profile_name if config_snapshot is not None else "legacy",
            config_snapshot.provider if config_snapshot is not None else current_ai_provider(),
            model,
            None,
            None,
            None,
            request_attempt + provider_retry_count,
        )
        raise AIEmptyContentError("AI provider returned no completion choices")
    choice = resp.choices[0]
    content = (choice.message.content or "").strip()
    finish_reason = str(getattr(choice, "finish_reason", None) or "").strip().lower()
    usage = getattr(resp, "usage", None)
    details = getattr(usage, "completion_tokens_details", None) if usage is not None else None
    logger.info(
        "AI provider response profile=%s provider=%s model=%s http_status=200 finish_reason=%s completion_tokens=%s reasoning_tokens=%s content_length=%s reasoning_content_exists=%s retry_count=%s",
        config_snapshot.profile_name if config_snapshot is not None else "legacy",
        config_snapshot.provider if config_snapshot is not None else current_ai_provider(),
        model,
        finish_reason or None,
        getattr(usage, "completion_tokens", None),
        getattr(details, "reasoning_tokens", None) if details is not None else None,
        len(content),
        bool(getattr(choice.message, "reasoning_content", None)),
        request_attempt + provider_retry_count,
    )
    if finish_reason == "length":
        raise AIOutputTruncated(partial_content=content)
    if not content:
        raise AIEmptyContentError("AI provider returned empty message.content")
    return content


async def probe_subscription_cli(provider: str, model: str, *, timeout: float = 180.0) -> dict:
    """One tiny real call through the subscription CLI: proves install, login and model together."""
    if not subscription_cli_available(provider):
        label = CLI_PROVIDER_LABELS.get(provider, provider)
        return {"ok": False, "error_code": "CLI_UNAVAILABLE", "error": f"{label} 無法執行，請確認已安裝"}
    snapshot = AIProviderConfigSnapshot(
        provider=provider, model=model, api_key="", profile_name="probe",
        codex_command=CODEX_DEFAULT_COMMAND,
    )
    try:
        text = await generate_ai_text(
            [{"role": "user", "content": "Reply with exactly: OK"}],
            temperature=None, max_tokens=None, timeout=timeout, config_snapshot=snapshot,
        )
    except Exception as exc:
        return {"ok": False, "error_code": "CLI_ERROR", "error": str(exc)[:300]}
    return {"ok": "OK" in text.upper(), "responded": True}


async def probe_openai_profile_connection(
    config_snapshot: AIProviderConfigSnapshot,
    *,
    timeout: float = 30.0,
) -> dict:
    """Run a minimal OpenAI-compatible connection probe for one profile.

    This intentionally bypasses research output validation and global token
    settings. A structurally valid completion is connectivity success even when
    reasoning consumed the 32-token budget and ``finish_reason`` is ``length``.
    """
    if not config_snapshot.api_key:
        return {"ok": False, "error_code": "AUTH_ERROR", "error": "Profile has no stored key"}
    if not config_snapshot.model:
        return {"ok": False, "error_code": "INVALID_MODEL", "error": "Profile model is empty"}
    if not config_snapshot.base_url and config_snapshot.provider != OPENAI_PROVIDER:
        return {"ok": False, "error_code": "ENDPOINT_ERROR", "error": "Profile Base URL is empty"}

    client = _openai_client(config_snapshot.api_key, timeout, config_snapshot=config_snapshot)
    kwargs = _openai_kwargs(
        temperature=0.0,
        max_tokens=32,
        provider=config_snapshot.provider,
        reasoning_effort="",
    )
    try:
        while True:
            try:
                response = await client.chat.completions.create(
                    model=config_snapshot.model,
                    messages=[{"role": "user", "content": "Reply exactly with: OK"}],
                    **kwargs,
                )
                break
            except Exception as exc:
                retry_kwargs = _openai_retry_kwargs(exc, kwargs)
                if retry_kwargs is not None:
                    kwargs = retry_kwargs
                    continue
                raise
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        response = getattr(exc, "response", None)
        if status is None and response is not None:
            status = getattr(response, "status_code", None)
        detail = _openai_error_detail(exc) or _format_openai_error(exc)
        if config_snapshot.api_key:
            detail = detail.replace(config_snapshot.api_key, "***")
        detail_lower = detail.lower()
        if status in (401, 403):
            error_code = "AUTH_ERROR"
        elif status == 400 and any(word in detail_lower for word in ("model", "模型", "not found")):
            error_code = "INVALID_MODEL"
        elif status == 404:
            error_code = "ENDPOINT_ERROR"
        elif status == 429:
            error_code = "RATE_LIMITED"
        elif status is not None and status >= 500:
            error_code = "PROVIDER_UNAVAILABLE"
        else:
            error_code = "CONNECTION_ERROR"
        return {
            "ok": False,
            "error_code": error_code,
            "error": detail,
            "http_status": status,
            "finish_reason": None,
            "output_tokens": 0,
        }

    if not getattr(response, "choices", None):
        return {
            "ok": False,
            "error_code": "INVALID_RESPONSE",
            "error": "Provider response did not contain a completion",
            "http_status": 200,
            "finish_reason": None,
            "output_tokens": int(getattr(getattr(response, "usage", None), "completion_tokens", 0) or 0),
        }
    choice = response.choices[0]
    finish_reason = str(getattr(choice, "finish_reason", None) or "")
    output_tokens = int(getattr(getattr(response, "usage", None), "completion_tokens", 0) or 0)
    return {
        "ok": True,
        "http_status": 200,
        "finish_reason": finish_reason,
        "output_tokens": output_tokens,
        "responded": True,
    }


def _openai_client(
    api_key: str,
    timeout: float,
    *,
    config_snapshot: AIProviderConfigSnapshot | None = None,
):
    from openai import AsyncOpenAI

    user_agent = config_snapshot.user_agent if config_snapshot is not None else (
        secrets_store.get_ai_config("ai_user_agent", "") or settings.ai_user_agent
    )
    base_url = config_snapshot.base_url if config_snapshot is not None else (
        secrets_store.get_ai_config("ai_base_url", settings.ai_base_url)
    )
    return AsyncOpenAI(
        api_key=api_key,
        base_url=normalize_openai_base_url(base_url),
        timeout=timeout,
        max_retries=0,
        default_headers={"User-Agent": user_agent},
    )


# 不同模型可能拒絕 temperature 或 reasoning_effort。這裡不靠模型名猜測,
# 只在 400 明確指出對應參數時移除該參數並重試; 每個參數最多移除一次。
_TEMP_REJECT_HINTS = ("temperature", "only 1 is allowed")
_REASONING_EFFORT_REJECT_HINTS = ("reasoning_effort", "reasoning effort")


def _is_temperature_rejected(exc: Exception) -> bool:
    """True if the upstream 400 is specifically about the temperature param."""
    if getattr(exc, "status_code", None) != 400:
        return False
    text = _openai_error_detail(exc) or str(exc)
    return _openai_error_param(exc) == "temperature" or any(
        h in text.lower() for h in _TEMP_REJECT_HINTS
    )


def _is_reasoning_effort_rejected(exc: Exception) -> bool:
    """True if the upstream 400 specifically rejects reasoning_effort."""
    if getattr(exc, "status_code", None) != 400:
        return False
    text = _openai_error_detail(exc) or str(exc)
    return _openai_error_param(exc) == "reasoning_effort" or any(
        h in text.lower() for h in _REASONING_EFFORT_REJECT_HINTS
    )


def _openai_error_param(exc: Exception) -> str:
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    if isinstance(error, dict):
        body = error
    return str(body.get("param") or "").strip().lower()


def _openai_retry_kwargs(exc: Exception, kwargs: dict) -> dict | None:
    """Remove one explicitly rejected optional argument for a bounded retry."""
    retry_kwargs = dict(kwargs)
    if "temperature" in retry_kwargs and _is_temperature_rejected(exc):
        retry_kwargs.pop("temperature")
        return retry_kwargs
    if "reasoning_effort" in retry_kwargs and _is_reasoning_effort_rejected(exc):
        retry_kwargs.pop("reasoning_effort")
        return retry_kwargs
    return None


_LOCAL_LLM_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "host.docker.internal"})


def _openai_kwargs(
    *,
    temperature: float | None,
    max_tokens: int | None,
    provider: str | None = None,
    reasoning_effort: str | None = None,
    structured_output: bool = False,
    base_url: str = "",
    model: str = "",
) -> dict:
    """Build OpenAI create() kwargs; optional parameters are omitted when empty.

    max_tokens=None 時不傳 — 由服務端預設上限管理(推理模型的思考 token 也
    計入該參數預算, 限制會擠佔正文, 見 generate_ai_text 文件)。
    """
    kwargs: dict = {}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if temperature is not None:
        kwargs["temperature"] = temperature
    if (provider if provider is not None else current_ai_provider()) == OPENAI_PROVIDER:
        if reasoning_effort is None:
            reasoning_effort = current_openai_reasoning_effort()
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
    hostname = (urlsplit(base_url).hostname or "").lower()
    if hostname in _LOCAL_LLM_HOSTS:
        # Local Qwen-style models otherwise spend the token budget thinking and truncate.
        kwargs["reasoning_effort"] = "none"  # LM Studio
        kwargs["top_p"] = 0.95
        # top_k / repeat_penalty are not OpenAI SDK params; they must ride in extra_body.
        kwargs["extra_body"] = {
            "chat_template_kwargs": {"enable_thinking": False},  # llama.cpp
            "top_k": 20,
            "repeat_penalty": 1.0,
        }
    if structured_output:
        normalized_model = model.strip().lower()
        supports_response_format = (
            hostname == "api.deepseek.com"
            or (hostname == "open.bigmodel.cn" and normalized_model.startswith("glm-4.7"))
            or hostname == "apihub.agnes-ai.com"
        )
        if supports_response_format:
            kwargs["response_format"] = {"type": "json_object"}
        if (
            hostname == "api.deepseek.com"
            or (hostname == "open.bigmodel.cn" and normalized_model.startswith("glm-4.7"))
        ):
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    return kwargs


def _is_openai_transport_error(exc: Exception) -> bool:
    try:
        import openai
    except ImportError:
        openai = None

    if openai is not None and isinstance(exc, openai.APIError):
        return True

    try:
        import httpx
    except ImportError:
        return False

    return isinstance(exc, httpx.HTTPError)


def _format_openai_error(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if status is None and response is not None:
        status = getattr(response, "status_code", None)

    class_name = exc.__class__.__name__
    if "Timeout" in class_name:
        return "AI 服務請求超時, 請稍後重試或檢查 AI Base URL / 網路"
    if "Connection" in class_name:
        return "AI 服務連接失敗, 請檢查 AI Base URL / 網路"

    detail = _openai_error_detail(exc)
    status_messages = {
        400: "請求參數無效, 請檢查模型名稱和上下文長度",
        401: "API Key 無效或無權限, 請檢查設定頁設定",
        403: "AI 服務拒絕存取, 請檢查帳號權限或閘道設定",
        404: "模型或介面地址不存在, 請檢查 AI Base URL 和模型名稱",
        408: "AI 服務請求超時, 請稍後重試",
        429: "AI 服務限流或額度不足, 請稍後重試或檢查額度",
        500: "AI 服務內部錯誤, 請稍後重試",
        502: "AI 閘道回傳錯誤, 請稍後重試或檢查 AI Base URL",
        503: "AI 服務暫時不可用, 請稍後重試",
        504: "AI 服務端超時, 請稍後重試或檢查 AI Base URL / 網路",
    }
    # 優先回傳來源真實錯誤 (如 Moonshot 的 "model not found"), 僅在沒有
    # 可讀 detail 時才回落到按狀態碼的通用文案, 避免吞掉排障關鍵資訊。
    message = detail or status_messages.get(status) or "請稍後重試或檢查 AI 服務設定"
    if status:
        return f"AI 服務請求失敗({status}): {message}"
    return f"AI 服務請求失敗: {message}"


def _openai_error_detail(exc: Exception) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            text = error.get("message") or error.get("code") or error.get("type")
            return _compact_error_text(str(text or ""))
        if isinstance(error, str):
            return _compact_error_text(error)

    response = getattr(exc, "response", None)
    content_type = ""
    text = ""
    if response is not None:
        content_type = response.headers.get("content-type", "").lower()
        try:
            text = response.text
        except Exception:
            text = ""
    if not text and isinstance(body, str):
        text = body
    if not text:
        text = str(exc)
    if _looks_like_html(text, content_type):
        return ""
    return _compact_error_text(text)


def _looks_like_html(text: str, content_type: str) -> bool:
    sample = text.lstrip()[:200].lower()
    return "html" in content_type or sample.startswith("<!doctype html") or sample.startswith("<html")


def _compact_error_text(text: str) -> str:
    text = _ANSI_RE.sub("", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:500]


async def _run_codex_cli(
    messages: Sequence[Message],
    *,
    max_tokens: int | None,
    timeout: float,
    model: str | None = None,
    config_snapshot: AIProviderConfigSnapshot | None = None,
) -> str:
    prompt = _codex_prompt(messages, max_tokens=max_tokens)
    run_path = Path(tempfile.mkdtemp(prefix="tsp-codex-run-"))
    try:
        codex_home_path = run_path / "codex-home"
        workspace_path = run_path / "workspace"
        codex_home_path.mkdir()
        workspace_path.mkdir()
        output_path = codex_home_path / "last-message.txt"
        if config_snapshot is None:
            _prepare_codex_home(codex_home_path)
        else:
            _prepare_codex_home(codex_home_path, config_snapshot=config_snapshot)

        # 不傳 --ephemeral: 老版本 codex(如 0.58)無此參數, 傳了直接報
        # unexpected argument; 會話隔離已由一次性臨時 CODEX_HOME 保證(跑完即刪)。
        args = [
            *(
                _codex_base_command(config_snapshot.codex_command)
                if config_snapshot is not None
                else _codex_base_command()
            ),
            "exec",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--color",
            "never",
            "--output-last-message",
            str(output_path),
        ]
        from app.services.ai_key_profiles import validate_model_name

        model = validate_model_name(
            config_snapshot.model if config_snapshot is not None
            else model if model is not None else current_ai_model()
        )
        if model:
            args.extend(["--model", model])
        args.extend(["--cd", str(workspace_path), "-"])

        env = _codex_process_env(codex_home_path)

        returncode, stdout, stderr = await asyncio.to_thread(
            _run_codex_process,
            args,
            prompt,
            env,
            timeout,
        )

        out = _clean_process_text(stdout)
        err = _clean_process_text(stderr)
        final_message = _read_output_file(output_path)
        if returncode != 0:
            detail = err or out or f"exit code {returncode}"
            if _looks_like_login_error(detail):
                raise RuntimeError("Codex 訂閱登入已失效，請在終端機執行 codex login 重新登入")
            raise RuntimeError(f"Codex CLI 呼叫失敗: {detail[-1200:]}")
        result = final_message or out
        if not result:
            raise RuntimeError("Codex CLI 未回傳內容")
        return result
    finally:
        await asyncio.to_thread(_remove_tree_best_effort, run_path)


def _looks_like_login_error(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in ("/login", "not logged in", "please log in", "please login", "codex login",
                       "authentication_error", "invalid api key", "oauth token has expired")
    )


def _claude_base_command() -> list[str]:
    resolved = shutil.which(CLAUDE_DEFAULT_COMMAND)
    if not resolved:
        raise RuntimeError("未找到 Claude Code CLI (claude)；請先安裝並用 claude 登入訂閱")
    if sys.platform == "win32" and resolved.lower().endswith(".ps1"):
        return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", resolved]
    return [resolved]


def _claude_process_env() -> dict[str, str]:
    """Only OS/locale/proxy variables: no ANTHROPIC_API_KEY, so the subscription login is used."""
    env = {name: value for name in _CODEX_ENV_ALLOWLIST if (value := os.environ.get(name))}
    env["NO_COLOR"] = "1"
    return env


async def _run_claude_cli(
    messages: Sequence[Message],
    *,
    max_tokens: int | None,
    timeout: float,
    config_snapshot: AIProviderConfigSnapshot,
) -> str:
    """Run one isolated `claude -p` turn on the user's Claude subscription and return its text."""
    prompt = _codex_prompt(messages, max_tokens=max_tokens)
    workspace = Path(tempfile.mkdtemp(prefix="tsp-claude-run-"))
    try:
        from app.services.ai_key_profiles import validate_model_name

        args = [*_claude_base_command(), "-p", *_CLAUDE_ISOLATION_ARGS, "--output-format", "json"]
        model = validate_model_name(config_snapshot.model)
        if model:
            args.extend(["--model", model])
        returncode, stdout, stderr = await asyncio.to_thread(
            _run_cli_process, args, prompt, _claude_process_env(), timeout, "Claude Code CLI", workspace,
        )
        out = _clean_process_text(stdout)
        err = _clean_process_text(stderr)
        try:
            payload = json.loads(out)
        except ValueError:
            payload = None
        result = payload.get("result") if isinstance(payload, dict) else None
        failed = returncode != 0 or not isinstance(payload, dict) or bool(payload.get("is_error"))
        if failed:
            detail = (result if isinstance(result, str) else "") or err or out or f"exit code {returncode}"
            if _looks_like_login_error(detail):
                raise RuntimeError("Claude 訂閱登入已失效，請在終端機執行 claude 並用 /login 重新登入")
            raise RuntimeError(f"Claude Code CLI 呼叫失敗: {detail[-1200:]}")
        if not isinstance(result, str) or not result.strip():
            raise RuntimeError("Claude Code CLI 未回傳內容")
        return result.strip()
    finally:
        await asyncio.to_thread(shutil.rmtree, workspace, True)


def _run_cli_process(
    args: Sequence[str],
    prompt: str,
    env: dict[str, str],
    timeout: float,
    label: str,
    cwd: Path,
) -> tuple[int, bytes, bytes]:
    """Run a CLI directly (no shell) with the prompt as UTF-8 stdin, so Chinese text survives on Windows."""
    try:
        proc = subprocess.run(
            list(args), input=prompt.encode("utf-8"), capture_output=True, env=env,
            timeout=timeout, check=False, cwd=cwd,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{label} 呼叫逾時，請稍後重試或檢查本機登入狀態") from exc
    return proc.returncode, proc.stdout, proc.stderr


def _run_codex_process(
    args: Sequence[str],
    prompt: str,
    env: dict[str, str],
    timeout: float,
) -> tuple[int, bytes, bytes]:
    try:
        proc = subprocess.run(
            list(args),
            input=prompt.encode("utf-8"),
            capture_output=True,
            env=env,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Codex CLI 呼叫超時, 請稍後重試或檢查本機 Codex 登入狀態") from exc
    return proc.returncode, proc.stdout, proc.stderr


def _codex_process_env(codex_home_path: Path) -> dict[str, str]:
    """Pass only OS, locale, certificate, and proxy settings to Codex."""
    env: dict[str, str] = {}
    seen: set[str] = set()
    for name in _CODEX_ENV_ALLOWLIST:
        normalized = name.casefold() if os.name == "nt" else name
        if normalized in seen:
            continue
        value = os.environ.get(name)
        if value:
            env[name] = value
            seen.add(normalized)
    env["NO_COLOR"] = "1"
    env["CODEX_HOME"] = str(codex_home_path)
    return env


def _remove_tree_best_effort(path: Path) -> None:
    _remove_auth_files(path)
    for attempt in range(4):
        try:
            shutil.rmtree(path, onerror=_make_writable_and_retry)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == 3:
                break
            time.sleep(0.2 * (attempt + 1))
    _remove_auth_files(path)
    shutil.rmtree(path, ignore_errors=True)
    _remove_auth_files(path)


def _remove_auth_files(path: Path) -> None:
    try:
        auth_files = list(path.rglob("auth.json"))
    except OSError:
        return
    for auth_file in auth_files:
        try:
            os.chmod(auth_file, stat.S_IWRITE)
            auth_file.unlink(missing_ok=True)
        except OSError:
            pass


def _make_writable_and_retry(
    func: Callable[[str], object],
    path: str,
    exc_info: tuple[type[BaseException], BaseException, TracebackType],
) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        raise exc_info[1] from None


def _codex_prompt(messages: Sequence[Message], *, max_tokens: int | None) -> str:
    parts = [
        "You are Nanachi 的台股監控看板's local AI provider.",
        "This is a text-generation task. The working directory is intentionally empty.",
        "Use only the user-provided prompt content below; do not inspect or modify local files.",
        "Return only the final requested content; do not include execution logs.",
    ]
    if max_tokens:
        parts.append(f"Keep the final answer within about {max_tokens} output tokens.")
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        parts.append(f"\n<{role}>\n{content}\n</{role}>")
    return "\n".join(parts)


def _codex_base_command(command: str | None = None) -> list[str]:
    command = command if command is not None else current_codex_command()
    resolved = _resolve_command(command)
    if not resolved:
        raise RuntimeError(f"未找到 Codex CLI 命令: {command}")

    if sys.platform == "win32" and resolved.lower().endswith(".ps1"):
        return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", resolved]
    return [resolved]


def _resolve_command(command: str) -> str | None:
    if command.lower() != CODEX_DEFAULT_COMMAND:
        return None

    if sys.platform == "win32":
        desktop_codex = _resolve_windows_desktop_codex()
        if desktop_codex:
            return desktop_codex

    resolved = shutil.which(command)
    if sys.platform == "win32" and resolved:
        resolved_path = Path(resolved)
        if not resolved_path.suffix:
            cmd_path = resolved_path.with_suffix(".cmd")
            if cmd_path.exists():
                return str(cmd_path)
    if not resolved and sys.platform == "win32" and not command.lower().endswith(".cmd"):
        resolved = shutil.which(f"{command}.cmd")
    if not resolved and sys.platform == "win32":
        resolved = _resolve_windows_codex_command(command)
    return resolved


def _resolve_windows_codex_command(command: str) -> str | None:
    """Find npm-installed Codex when the backend process has a minimal PATH."""
    raw = Path(command)
    if raw.parent != Path("."):
        return None

    names = [command]
    if not raw.suffix:
        names = [f"{command}.cmd", f"{command}.exe", f"{command}.bat", f"{command}.ps1", command]

    dirs: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        dirs.append(Path(appdata) / "npm")
    dirs.append(Path.home() / "AppData" / "Roaming" / "npm")

    for env_name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        value = os.environ.get(env_name)
        if value:
            dirs.append(Path(value) / "nodejs")

    for directory in dirs:
        for name in names:
            candidate = directory / name
            if candidate.exists():
                return str(candidate)
    return None


def _resolve_windows_desktop_codex() -> str | None:
    """Prefer the Codex Desktop bundled CLI over an older npm shim."""
    local_appdata = os.environ.get("LOCALAPPDATA")
    if not local_appdata:
        return None

    root = Path(local_appdata) / "OpenAI" / "Codex" / "bin"
    if not root.exists():
        return None

    candidates = list(root.glob("*/codex.exe"))
    direct = root / "codex.exe"
    if direct.exists():
        candidates.append(direct)
    if not candidates:
        return None

    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    return str(newest)


def _prepare_codex_home(
    target: Path,
    *,
    config_snapshot: AIProviderConfigSnapshot | None = None,
) -> None:
    """Create an isolated CODEX_HOME that reuses auth but not fragile config."""
    source = _codex_home()
    auth_file = source / "auth.json"
    if auth_file.exists():
        shutil.copy2(auth_file, target / "auth.json")
    _write_compatible_codex_config(target / "config.toml", config_snapshot=config_snapshot)


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def _write_compatible_codex_config(
    path: Path,
    *,
    config_snapshot: AIProviderConfigSnapshot | None = None,
) -> None:
    config = _read_codex_config()
    lines: list[str] = []
    active_provider = _active_codex_provider(config)

    if active_provider:
        lines.append(_toml_string("model_provider", active_provider[0]))

    openai_base_url = config.get("openai_base_url")
    if isinstance(openai_base_url, str) and openai_base_url:
        lines.append(_toml_string("openai_base_url", openai_base_url))

    model = (
        config_snapshot.model
        if config_snapshot is not None
        else current_ai_model() or normalize_codex_model(str(config.get("model") or ""))
    )
    if model:
        lines.append(_toml_string("model", model))

    effort = (
        config_snapshot.codex_reasoning_effort
        if config_snapshot is not None
        else current_codex_reasoning_effort() or normalize_codex_reasoning_effort(str(config.get("model_reasoning_effort") or ""))
    )
    if effort:
        lines.append(_toml_string("model_reasoning_effort", effort))

    lines.append(_toml_string("approval_policy", "never"))
    lines.append(_toml_string("sandbox_mode", "read-only"))

    if active_provider:
        provider_name, provider = active_provider
        lines.append("")
        lines.append(f"[model_providers.{_toml_key(provider_name)}]")
        for key in ("name", "base_url", "wire_api", "experimental_bearer_token"):
            value = provider.get(key)
            if isinstance(value, str) and value:
                lines.append(_toml_string(key, value))
        for key in ("requires_openai_auth", "supports_websockets"):
            value = provider.get(key)
            if isinstance(value, bool):
                lines.append(f"{key} = {'true' if value else 'false'}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _active_codex_provider(config: dict) -> tuple[str, dict] | None:
    """Return the active custom provider, adapting loopback URLs for Docker."""
    provider_name = config.get("model_provider")
    if not isinstance(provider_name, str) or not provider_name:
        return None

    providers = config.get("model_providers")
    if not isinstance(providers, dict):
        return None
    source = providers.get(provider_name)
    if not isinstance(source, dict):
        return None

    provider = dict(source)
    base_url = str(provider.get("base_url") or "").strip()
    parsed = urlsplit(base_url)
    docker_host = os.environ.get("CODEX_DOCKER_HOST", "").strip()
    if docker_host and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        port = f":{parsed.port}" if parsed.port else ""
        provider["base_url"] = urlunsplit(parsed._replace(netloc=f"{docker_host}{port}"))
    return provider_name, provider


def _read_codex_config() -> dict:
    path = _codex_home() / "config.toml"
    if not path.exists():
        return {}
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError:
        return _read_codex_config_lenient(path)
    except OSError:
        return {}


def _read_codex_config_lenient(path: Path) -> dict:
    config: dict[str, str] = {}
    pattern = re.compile(r'^\s*([A-Za-z0-9_-]+)\s*=\s*"([^"]*)"\s*$')
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = pattern.match(line)
            if match:
                config[match.group(1)] = match.group(2)
    except OSError:
        pass
    return config


def _toml_string(key: str, value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'{key} = "{escaped}"'


def _toml_key(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _clean_process_text(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    return _ANSI_RE.sub("", text).strip()


def _read_output_file(path: Path) -> str:
    if path.exists():
        return _ANSI_RE.sub("", path.read_text(encoding="utf-8", errors="replace")).strip()
    return ""

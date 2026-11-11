"""Settings for Taiwan data sources, AI, and local user preferences."""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app import secrets_store
from app.data_providers.custom.config import MAX_TIMEOUT
from app.tickflow.policy import detect_capabilities

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _ai_profile_count() -> int:
    """Return number of AI key profiles, 0 on error."""
    try:
        from app.services.ai_key_profiles import list_profiles
        return len(list_profiles())
    except Exception:  # noqa: BLE001
        return 0


def _ai_active_profile_name() -> str | None:
    """Return the name of the active AI key profile, or None."""
    try:
        from app.services.ai_key_profiles import list_profiles
        for p in list_profiles():
            if p.get("active"):
                return str(p.get("name", ""))
        return None
    except Exception:  # noqa: BLE001
        return None


@router.get("")
def get_settings() -> dict:
    """返回當前配置概況(Key 脫敏)。"""
    from app.config import settings
    from app.services import preferences
    from app.taiwan import (
        advice_reflection,
        auto_ai_explain,
        event_ai_explain,
        push_digest,
        telegram_bot,
        watchlist_anomaly,
    )
    from app.services.ai_provider import (
        ai_configured,
        current_ai_model,
        current_codex_command,
        current_codex_model,
        current_codex_reasoning_effort,
        current_openai_model,
        current_openai_reasoning_effort,
        current_ai_context_window,
        current_ai_max_output_tokens,
    )

    ai_provider = secrets_store.get_ai_config("ai_provider", settings.ai_provider)
    return {
        "mode": "official",
        # 首次使用引導
        "onboarding_completed": preferences.get_onboarding_completed(),
        # AI 配置
        "ai_provider": ai_provider,
        "ai_base_url": secrets_store.get_ai_config("ai_base_url", settings.ai_base_url),
        "ai_api_key_masked": secrets_store.mask(secrets_store.get_ai_key()),
        "has_ai_key": bool(secrets_store.get_ai_key()),
        "ai_configured": ai_configured(ai_provider),
        "ai_model": current_ai_model(),
        "ai_openai_model": current_openai_model(),
        "ai_reasoning_effort": current_openai_reasoning_effort(),
        "ai_codex_model": current_codex_model(),
        "ai_codex_command": current_codex_command(),
        "ai_codex_reasoning_effort": current_codex_reasoning_effort(),
        "ai_user_agent": secrets_store.get_ai_config("ai_user_agent", settings.ai_user_agent),
        "ai_max_output_tokens": current_ai_max_output_tokens(),
        "ai_context_window": current_ai_context_window(),
        # FinMind 台股基本面與籌碼
        "finmind_token_masked": secrets_store.mask(preferences.get_finmind_token()),
        "finmind_enabled": preferences.get_finmind_enabled(),
        "has_finmind_token": bool(preferences.get_finmind_token()),
        # AI Key Profiles
        "ai_key_profiles_count": _ai_profile_count(),
        "ai_key_active_profile_name": _ai_active_profile_name(),
        "auto_ai_explain_enabled": auto_ai_explain.is_enabled(),
        "auto_ai_explain_last_run": auto_ai_explain.last_run(),
        "event_ai_explain_enabled": event_ai_explain.is_enabled(),
        "auto_advice_reflection_enabled": advice_reflection.is_enabled(),
        "auto_advice_reflection_last_run": advice_reflection.last_run(),
        # 推播與查詢 (預設全部關閉)
        "push_digest_enabled": push_digest.is_enabled(),
        "push_digest_last_run": push_digest.last_run(),
        "watchlist_anomaly_enabled": watchlist_anomaly.is_enabled(),
        "watchlist_anomaly_threshold_pct": watchlist_anomaly.threshold_pct(),
        "telegram_query_enabled": telegram_bot.is_enabled(),
    }


@router.post("/onboarding/complete")
def complete_onboarding() -> dict:
    """標記首次使用嚮導完成。

    寫入 preferences.json,前端守衛據此判斷是否需要再次展示嚮導。
    跨設備/清快取安全 —— 狀態落在後端文件,不依賴瀏覽器本地存儲。
    """
    from app.services import preferences
    done = preferences.set_onboarding_completed(True)
    return {"ok": True, "onboarding_completed": done}


class AiSettingsIn(BaseModel):
    provider: str | None = None
    base_url: str = ""
    api_key: str | None = None
    model: str = ""
    reasoning_effort: str = Field(default="high", max_length=64)
    codex_command: str = ""
    codex_reasoning_effort: str = ""
    user_agent: str = ""
    max_output_tokens: int | None = None   # 輸出上限, 鉗制所有任務的 max_tokens
    context_window: int | None = None      # 輸入上下文窗口上限 (約 token)


@router.post("/ai")
def save_ai_settings(req: AiSettingsIn) -> dict:
    """保存 AI 配置（全部持久化到 secrets.json）"""
    from app.config import settings
    from app.services.ai_provider import (
        OPENAI_PROVIDER,
        ai_configured,
        current_ai_model,
        current_ai_provider,
        current_codex_command,
        current_codex_model,
        current_codex_reasoning_effort,
        current_openai_model,
        current_openai_reasoning_effort,
        current_ai_context_window,
        current_ai_max_output_tokens,
        normalize_codex_command,
        normalize_codex_model,
        normalize_codex_reasoning_effort,
    )

    updates: dict = {}
    if req.provider:
        updates["ai_provider"] = req.provider
        settings.ai_provider = req.provider
    if req.provider == "codex_cli":
        updates["ai_codex_model"] = normalize_codex_model(req.model)
        try:
            codex_command = normalize_codex_command(req.codex_command)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        codex_reasoning_effort = normalize_codex_reasoning_effort(req.codex_reasoning_effort)
        updates["ai_codex_command"] = codex_command
        updates["ai_codex_reasoning_effort"] = codex_reasoning_effort
        settings.ai_codex_command = codex_command
        settings.ai_codex_reasoning_effort = codex_reasoning_effort
    else:
        if req.base_url:
            updates["ai_base_url"] = req.base_url
            settings.ai_base_url = req.base_url
        if req.api_key is not None:
            if req.api_key:
                updates["ai_api_key"] = req.api_key
                settings.ai_api_key = req.api_key
            else:
                secrets_store.clear("ai_api_key")
                settings.ai_api_key = ""
        if req.model:
            updates["ai_model"] = req.model
            settings.ai_model = req.model
        if req.provider == OPENAI_PROVIDER:
            updates["ai_reasoning_effort"] = req.reasoning_effort.strip()
    # user_agent 允許清空(回到預設瀏覽器 UA),故無條件持久化
    updates["ai_user_agent"] = req.user_agent
    settings.ai_user_agent = req.user_agent

    # 輸出上限 / 輸入上下文窗口 (數值配置, 缺省保持原值)
    if req.max_output_tokens is not None:
        if req.max_output_tokens <= 0:
            raise HTTPException(status_code=400, detail="輸出上限必須為正整數")
        updates["ai_max_output_tokens"] = req.max_output_tokens
        settings.ai_max_output_tokens = req.max_output_tokens
    if req.context_window is not None:
        if req.context_window <= 0:
            raise HTTPException(status_code=400, detail="上下文窗口必須為正整數")
        updates["ai_context_window"] = req.context_window
        settings.ai_context_window = req.context_window

    if updates:
        secrets_store.save(updates)
    secrets_store.confirm_ai_target(secrets_store.AI_LEGACY_TARGET)  # 使用者已在設定頁確認舊版單一設定

    provider = current_ai_provider()
    return {
        "ok": True,
        "ai_provider": provider,
        "ai_model": current_ai_model(),
        "ai_openai_model": current_openai_model(),
        "ai_reasoning_effort": current_openai_reasoning_effort(),
        "ai_codex_model": current_codex_model(),
        "ai_codex_command": current_codex_command(),
        "ai_codex_reasoning_effort": current_codex_reasoning_effort(),
        "ai_configured": ai_configured(provider),
        "ai_max_output_tokens": current_ai_max_output_tokens(),
        "ai_context_window": current_ai_context_window(),
    }


@router.delete("/ai")
def clear_ai_settings() -> dict:
    """一鍵清空 AI 配置(provider / base_url / api_key / model)。

    保留 ai_user_agent —— 自定義請求頭與憑證解耦,清空憑證不影響繞過 CDN 攔截的設定。
    """
    from app.config import settings

    secrets_store.clear(
        "ai_provider",
        "ai_base_url",
        "ai_api_key",
        "ai_model",
        "ai_reasoning_effort",
        "ai_codex_model",
        "ai_codex_command",
        "ai_codex_reasoning_effort",
    )
    secrets_store.clear("ai_provider", "ai_base_url", "ai_api_key", "ai_model", "ai_codex_command", "ai_codex_reasoning_effort", "ai_max_output_tokens", "ai_context_window")
    # 同步重置執行時記憶體(provider 回預設值,其餘置空)
    settings.ai_provider = "openai_compat"
    settings.ai_base_url = ""
    settings.ai_api_key = ""
    settings.ai_model = ""
    settings.ai_codex_command = "codex"
    settings.ai_codex_reasoning_effort = ""
    settings.ai_max_output_tokens = 8192
    settings.ai_context_window = 32768

    return {"ok": True}


# ===== AI Key Profiles (Multiple Keys) =====

class AiKeyProfileCreate(BaseModel):
    name: str
    provider: str = "openai_compat"
    api_key: str = ""
    base_url: str = ""
    model: str = ""


class AiKeyProfileUpdate(BaseModel):
    name: str
    provider: str = "openai_compat"
    api_key: str | None = None
    base_url: str = ""
    model: str = ""


@router.get("/ai-key-profiles")
def list_ai_key_profiles() -> dict:
    """List all AI key profiles (metadata only, no key values)."""
    from app.services import ai_key_profiles
    profiles = ai_key_profiles.list_profiles()
    return {"profiles": profiles}


@router.post("/ai-key-profiles")
def create_ai_key_profile(req: AiKeyProfileCreate) -> dict:
    """Create a new AI key profile.

    Keys are stored securely server-side. Never returned to the frontend.
    The first profile created is automatically set as active.
    """
    from app.services import ai_key_profiles
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Profile name cannot be empty")
    from app.services.ai_provider import is_subscription_cli_provider
    if not req.api_key.strip() and not is_subscription_cli_provider(req.provider):
        raise HTTPException(status_code=400, detail="API key cannot be empty")
    try:
        profile = ai_key_profiles.create_profile(
            name=req.name.strip(),
            provider=req.provider or "openai_compat",
            api_key=req.api_key.strip(),
            base_url=req.base_url.strip() if req.base_url else "",
            model=req.model.strip() if req.model else "",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "profile": profile}


@router.post("/ai-key-profiles/{profile_id}/activate")
def activate_ai_key_profile(profile_id: str) -> dict:
    """Set a profile as the active AI key source.

    The next AI request immediately uses the new key without restart.
    """
    from app.services import ai_key_profiles
    ok = ai_key_profiles.set_active_profile(profile_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"ok": True, "active_profile_id": profile_id}


class AiDeepProfileIn(BaseModel):
    profile_id: str | None = None


@router.post("/ai-key-profiles/deep")
def set_ai_deep_profile(req: AiDeepProfileIn) -> dict:
    """Mark one profile for deep calls (debate judge, final Advice); null clears it."""
    from app.services import ai_key_profiles
    if not ai_key_profiles.set_deep_profile(req.profile_id):
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"ok": True, "deep_profile_id": req.profile_id}


@router.patch("/ai-key-profiles/{profile_id}")
def update_ai_key_profile(profile_id: str, req: AiKeyProfileUpdate) -> dict:
    """Update profile metadata and optionally replace its stored key."""
    from app.services import ai_key_profiles
    try:
        profile = ai_key_profiles.update_profile(
            profile_id,
            name=req.name,
            provider=req.provider or "openai_compat",
            base_url=req.base_url,
            model=req.model,
            api_key=req.api_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"ok": True, "profile": profile}


@router.delete("/ai-key-profiles/{profile_id}")
def delete_ai_key_profile(profile_id: str) -> dict:
    """Delete a profile and its stored key.

    If the deleted profile was active and other profiles exist, the first
    remaining profile is automatically activated.
    """
    from app.services import ai_key_profiles
    ok = ai_key_profiles.delete_profile(profile_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"ok": True}


@router.post("/ai-key-profiles/{profile_id}/test")
async def test_ai_key_profile(profile_id: str) -> dict:
    """Test whether the profile's key can reach the AI provider.

    Uses the profile's stored provider (or falls back to openai_compat).
    Returns connectivity result without exposing the key or request content.
    """
    from app.services import ai_key_profiles
    from app.services.ai_provider import (
        AIProviderConfigSnapshot,
        is_subscription_cli_provider,
        probe_openai_profile_connection,
        probe_subscription_cli,
    )

    profiles_meta = ai_key_profiles.list_profiles()
    meta = next((p for p in profiles_meta if p["id"] == profile_id), None)
    if meta is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    profile_config = ai_key_profiles.get_profile_config(profile_id)
    if profile_config is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    if is_subscription_cli_provider(profile_config["provider"]):
        result = await probe_subscription_cli(profile_config["provider"], profile_config["model"])
        return {"profile_id": profile_id, "provider": profile_config["provider"], **result}
    if not profile_config["key"]:
        return {"ok": False, "error_code": "AUTH_ERROR", "error": "Profile has no stored key"}

    provider = profile_config["provider"] or "openai_compat"
    profile_base_url = profile_config["base_url"]
    profile_model = profile_config["model"]
    cfg = AIProviderConfigSnapshot(
        provider=provider,
        model=profile_model,
        api_key=profile_config["key"],
        base_url=profile_base_url,
    )
    result = await probe_openai_profile_connection(cfg)
    logger.info(
        "AI profile connection test profile_id=%s base_url=%s model=%s http_status=%s "
        "finish_reason=%s output_tokens=%s",
        profile_id,
        profile_base_url,
        profile_model,
        result.get("http_status"),
        result.get("finish_reason"),
        result.get("output_tokens"),
    )
    return {"profile_id": profile_id, "provider": provider, **result}


@router.post("/ai/test")
async def test_active_ai() -> dict:
    """Test the AI config currently in use (the 連線狀態 card's 測試 button)."""
    from app.services.ai_provider import (
        is_subscription_cli_provider,
        probe_openai_profile_connection,
        probe_subscription_cli,
        snapshot_ai_provider_config,
    )

    cfg = snapshot_ai_provider_config()
    if is_subscription_cli_provider(cfg.provider):
        result = await probe_subscription_cli(cfg.provider, cfg.model)
    else:
        result = await probe_openai_profile_connection(cfg)
    return {**result, "model": cfg.model}


# ===== 偏好設定 =====

def _realtime_allowed() -> bool:
    """當前檔位是否允許即時行情(none/free 不允許)。"""
    from app.services.quote_service import QuoteService
    return QuoteService.is_realtime_allowed()


class MinuteSyncPrefs(BaseModel):
    minute_sync_enabled: bool
    minute_sync_days: int = 5
    # 單段大小(交易日),None 表示不修改現有值。範圍 [5, 30],預設 20。
    minute_sync_segment_days: int | None = None


class DataProvidersIn(BaseModel):
    daily_data_provider: str | None = None
    adj_factor_provider: str | None = None
    minute_data_provider: str | None = None
    realtime_data_provider: str | None = None
    financial_data_provider: str | None = None


class PluginKeyIn(BaseModel):
    plugin: str
    api_key: str


class DataSourceJobTimeoutPrefs(BaseModel):
    data_source_job_timeout_s: int = Field(ge=60)
    data_source_long_job_timeout_s: int = Field(ge=60)


class DatasetFieldMapItem(BaseModel):
    source: str
    target: str


class DatasetConfigIn(BaseModel):
    url: str
    method: str = "GET"
    batch: int | None = None
    rpm: int | None = None
    response_path: str = ""
    field_map: dict[str, str] = {}
    transforms: dict[str, str] = {}
    symbols_param: str = "symbols"
    start_param: str = "start_time"
    end_param: str = "end_time"
    asset_type_param: str | None = None
    freq_param: str | None = None
    timeout: float | None = Field(
        default=None,
        gt=0,
        le=MAX_TIMEOUT,
        allow_inf_nan=False,
    )


class AuthConfigIn(BaseModel):
    type: str = "none"
    token_env: str | None = None
    header: str = "Authorization"
    param: str = "token"


class CustomSourceIn(BaseModel):
    name: str
    display_name: str = ""
    auth: AuthConfigIn = AuthConfigIn()
    datasets: dict[str, DatasetConfigIn] = {}


class CustomSourceTestIn(BaseModel):
    provider: str
    dataset: str
    symbols: list[str] | None = None
    config: CustomSourceIn | None = None


@router.get("/preferences")
def get_preferences() -> dict:
    """返回使用者偏好設定。"""
    from app.services import preferences, webhook_adapter
    return {
        "realtime_quotes_enabled": preferences.get_realtime_quotes_enabled(),
        "realtime_allowed": _realtime_allowed(),
        "indices_nav_pinned": preferences.get_indices_nav_pinned(),
        "watchlist_groups_in_nav": preferences.get_watchlist_groups_in_nav(),
        "minute_sync_enabled": preferences.get_minute_sync_enabled(),
        "minute_sync_days": preferences.get_minute_sync_days(),
        "minute_sync_segment_days": preferences.get_minute_sync_segment_days(),
        "daily_data_provider": preferences.get_daily_data_provider(),
        "adj_factor_provider": preferences.get_adj_factor_provider(),
        "minute_data_provider": preferences.get_minute_data_provider(),
        "realtime_data_provider": preferences.get_realtime_data_provider(),
        "financial_data_provider": preferences.get_financial_provider(),
        "data_source_job_timeout_s": preferences.get_data_source_job_timeout_s(),
        "data_source_long_job_timeout_s": preferences.get_data_source_long_job_timeout_s(),
        "realtime_watchlist_symbols": preferences.get_realtime_watchlist_symbols(),
        **preferences.get_realtime_quote_scope(),
        "enriched_batch_size": preferences.get_enriched_batch_size(),
        "index_daily_batch_size": preferences.get_index_daily_batch_size(),
        "watchlist_columns": preferences.get_watchlist_columns(),
        "sse_refresh_pages": preferences.get_sse_refresh_pages(),
        "system_notify_enabled": preferences.get_system_notify_enabled(),
        "line_target_id": preferences.get_line_target_id(),
        "line_channel_access_token_masked": secrets_store.mask(preferences.get_line_channel_access_token()),
        "line_configured": bool(preferences.get_line_target_id() and preferences.get_line_channel_access_token()),
        "telegram_chat_id": preferences.get_telegram_chat_id(),
        "telegram_bot_token_masked": secrets_store.mask(preferences.get_telegram_bot_token()),
        "telegram_configured": bool(preferences.get_telegram_chat_id() and preferences.get_telegram_bot_token()),
        "external_notification_channels": preferences.get_external_notification_channels(),
        "external_notification_status": webhook_adapter.delivery_status(),
        "webhook_enabled_default": preferences.get_webhook_enabled_default(),
        "webhook_default_channels": preferences.get_webhook_default_channels(),
        "sidebar_index_symbols": preferences.get_sidebar_index_symbols(),
        "minute_intraday_refresh": preferences.get_minute_intraday_refresh(),
        "minute_intraday_refresh_interval": preferences.get_minute_intraday_refresh_interval(),
        "nav_order": preferences.get_nav_order(),
        "nav_hidden": preferences.get_nav_hidden(),
    }


@router.get("/notification-status")
def get_notification_status() -> dict:
    """Return process-local external notification delivery status."""
    from app.services import webhook_adapter

    return {"external_notification_status": webhook_adapter.delivery_status()}


@router.get("/data-sources")
def list_data_sources() -> dict:
    """列出已載入的資料來源 (內置 / 外掛 / 使用者自定義)。"""
    from app.data_providers import custom as custom_sources
    return {
        "builtin": [{"name": "taiwan", "display_name": "台灣官方資料源 (TWSE/TPEx)", "datasets": ["daily", "realtime", "financial"]}],
        "plugins": custom_sources.list_plugins(),
        "custom": custom_sources.list_sources(),
        "errors": custom_sources.errors(),
        "config_dir": str(custom_sources.data_sources_dir()),
    }


@router.post("/plugin-key")
def save_plugin_key(req: PluginKeyIn) -> dict:
    """保存插件 API Key(先探後存)。

    流程: probe_plugin_key 用候選 Key 實探 → 有效才寫 secrets.json
    ({plugin}_api_key, 優先級高於 .env) → load_all 重掃, 外掛即刻變為可切換。
    """
    from app.data_providers import custom as custom_sources

    name = req.plugin.strip().lower()
    key = req.api_key.strip()
    if not key:
        return {"ok": False, "error": "key empty"}
    ok, message = custom_sources.probe_plugin_key(name, key)
    if not ok:
        return {"ok": False, "reason": "invalid", "error": message}
    secrets_store.save({f"{name}_api_key": key})
    custom_sources.load_all()
    status = next((p for p in custom_sources.list_plugins() if p["name"] == name), None)
    return {
        "ok": True,
        "api_key_masked": secrets_store.mask(key),
        "plugin_available": bool(status and status.get("available")),
        "plugin": status,
    }


@router.delete("/plugin-key/{name}")
def clear_plugin_key(name: str) -> dict:
    """清除外掛的界面配置 Key(secrets.json);.env 裡的同名變數仍然生效。"""
    from app.data_providers import custom as custom_sources

    manifest = custom_sources.plugin_manifest(name)
    if manifest is None or not custom_sources.is_builtin(name):
        raise HTTPException(status_code=404, detail=f"插件 '{name}' 不存在")
    if not manifest.get("api_key_env"):
        raise HTTPException(status_code=400, detail=f"插件 '{name}' 不支持在界面配置 Key")
    secrets_store.clear(f"{name.lower()}_api_key")
    custom_sources.load_all()
    status = next((p for p in custom_sources.list_plugins() if p["name"] == name), None)
    return {
        "ok": True,
        "plugin_available": bool(status and status.get("available")),
        "plugin": status,
    }


@router.post("/data-sources/reload")
def reload_data_sources() -> dict:
    """重新載入 data_sources/*.yaml。"""
    from app.data_providers import custom as custom_sources
    custom_sources.load_all()
    return list_data_sources()


@router.post("/plugins/{name}/install")
def install_plugin(name: str) -> dict:
    """安裝指定外掛的依賴 (npm install / pip install), 完成後重新掃描。

    根據 plugin.yaml 的 runtime 字段決定安裝方式。安裝可能耗時較長 (網路下載),
    客戶端需設較長超時。
    """
    from app.data_providers import custom as custom_sources
    if not custom_sources.is_builtin(name):
        raise HTTPException(status_code=404, detail=f"插件 '{name}' 不存在")
    ok, message = custom_sources.install_plugin(name)
    # 無論成功失敗都重新掃描, 刷新外掛狀態 (安裝可能部分成功)
    custom_sources.load_all()
    result = list_data_sources()
    result["install_ok"] = ok
    result["install_message"] = message
    return result


@router.delete("/plugins/{name}/install")
def uninstall_plugin(name: str) -> dict:
    """解除安裝指定外掛的依賴 (刪除 node_modules / pip uninstall), 完成後重新掃描。

    如果該外掛當前正被使用, 自動回退到 taiwan。
    """
    from app.data_providers import custom as custom_sources
    from app.services import preferences
    if not custom_sources.is_builtin(name):
        raise HTTPException(status_code=404, detail=f"插件 '{name}' 不存在")
    selected = preferences.load()
    ok, message = custom_sources.uninstall_plugin(name)
    updates = {}
    for key in ("daily_data_provider", "minute_data_provider", "realtime_data_provider",
                "financial_data_provider", "adj_factor_provider"):
        if selected.get(key) == name:
            updates[key] = "same_as_daily" if key == "adj_factor_provider" else "taiwan"
    if updates:
        preferences.save(updates)
    custom_sources.load_all()
    result = list_data_sources()
    result["uninstall_ok"] = ok
    result["uninstall_message"] = message
    return result


@router.get("/data-sources/{name}")
def get_data_source(name: str) -> dict:
    """讀取一個自定義資料來源的完整配置(用於前端編輯回填)。"""
    from app.data_providers import custom as custom_sources
    cfg = custom_sources.get_config_dict(name)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"資料來源 '{name}' 不存在")
    return cfg


@router.post("/data-sources")
def save_data_source(req: CustomSourceIn) -> dict:
    """創建或更新一個自定義資料來源 yaml, 保存後自動 reload。"""
    from app.data_providers import custom as custom_sources
    config = req.model_dump()
    config["name"] = (config.get("name") or "").lower()
    try:
        custom_sources.save_config(config["name"], config)
        custom_sources.load_all()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return list_data_sources()


@router.delete("/data-sources/{name}")
def delete_data_source(name: str, request: Request) -> dict:
    """刪除一個自定義資料來源 yaml, 保存後自動 reload。

    若當前總開關選中的就是被刪的源, 回退到 taiwan。
    """
    from app.data_providers import custom as custom_sources
    from app.services import preferences
    selected = preferences.load()
    try:
        custom_sources.delete_config(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    custom_sources.load_all()
    # 回退被刪源的偏好
    updates: dict = {}
    if selected.get("daily_data_provider") == name:
        updates["daily_data_provider"] = "taiwan"
    if selected.get("realtime_data_provider") == name:
        updates["realtime_data_provider"] = "taiwan"
    if selected.get("financial_data_provider") == name:
        updates["financial_data_provider"] = "taiwan"
    if selected.get("minute_data_provider") == name:
        updates["minute_data_provider"] = "taiwan"
    adj = selected.get("adj_factor_provider")
    if adj == name:
        updates["adj_factor_provider"] = "same_as_daily"
    if updates:
        preferences.save(updates)
    # 刪除源可能觸發偏好回退 taiwan, 同步刷新能力快照
    request.app.state.capabilities = detect_capabilities()
    return list_data_sources()


@router.post("/data-sources/test")
def test_data_source(req: CustomSourceTestIn) -> dict:
    """試拉自定義資料來源，不寫盤。"""
    from app.data_providers import custom as custom_sources

    temporary = req.config is not None
    provider = None
    try:
        if req.config:
            config = req.config.model_dump()
            dataset_config = config["datasets"].get(req.dataset)
            if dataset_config is None:
                raise ValueError(f"dataset '{req.dataset}' is not configured")
            config["datasets"] = {req.dataset: dataset_config}
            provider = custom_sources.create_provider(config)
        else:
            provider = custom_sources.get_provider(req.provider)
        return provider.test_dataset(req.dataset, req.symbols)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"自定義資料來源測試失敗: {e}") from e
    finally:
        if temporary and provider is not None:
            provider.close()


@router.put("/preferences/data-providers")
def update_data_providers(req: DataProvidersIn, request: Request) -> dict:
    """保存資料來源選擇。"""
    from app.services import preferences
    updates = req.model_dump(exclude_none=True)
    allowed = preferences._allowed_data_providers()
    for key, value in updates.items():
        normalized = value.strip().lower()
        choices = allowed | {"same_as_daily"} if key == "adj_factor_provider" else allowed
        if normalized not in choices:
            raise HTTPException(status_code=400, detail=f"Unsupported data provider: {value}")
        updates[key] = normalized
    if updates:
        preferences.save(updates)
    # 刷新能力快照: 當前 provider 變化會改變自定義源能力增廣結果 (讀快取, 無網路請求)
    request.app.state.capabilities = detect_capabilities()
    return {
        "daily_data_provider": preferences.get_daily_data_provider(),
        "adj_factor_provider": preferences.get_adj_factor_provider(),
        "minute_data_provider": preferences.get_minute_data_provider(),
        "realtime_data_provider": preferences.get_realtime_data_provider(),
        "financial_data_provider": preferences.get_financial_provider(),
    }


@router.put("/preferences/data-source-job-timeouts")
def update_data_source_job_timeouts(req: DataSourceJobTimeoutPrefs) -> dict:
    """保存普通與長資料後台任務的卡死判定時間。"""
    from app.services import preferences
    preferences.save(req.model_dump())
    return req.model_dump()


@router.get("/preferences/watchlist-columns")
def get_watchlist_columns() -> dict:
    """返回自選列表列配置。"""
    from app.services import preferences
    cols = preferences.get_watchlist_columns()
    return {"columns": cols}


class NavOrderIn(BaseModel):
    nav_order: list[str]


class NavHiddenIn(BaseModel):
    nav_hidden: list[str]


@router.put("/preferences/nav-order")
def update_nav_order(req: NavOrderIn) -> dict:
    """保存左側選單排序（內置頁面 path + 擴展分析選單 id 的有序列表）。"""
    from app.services import preferences
    saved = preferences.set_nav_order(req.nav_order)
    return {"nav_order": saved}


@router.put("/preferences/nav-hidden")
def update_nav_hidden(req: NavHiddenIn) -> dict:
    """保存左側選單隱藏項。"""
    from app.services import preferences
    saved = preferences.set_nav_hidden(req.nav_hidden)
    return {"nav_hidden": saved}


@router.put("/preferences/watchlist-columns")
def update_watchlist_columns(req: dict) -> dict:
    """保存自選列表列配置。"""
    from app.services import preferences
    columns = req.get("columns", [])
    saved = preferences.set_watchlist_columns(columns)
    return {"columns": saved}


@router.put("/preferences/minute-sync")
def update_minute_sync(req: MinuteSyncPrefs) -> dict:
    """保存分鐘 K 同步偏好。

    minute_sync_segment_days 為可選:未傳(None)時不覆蓋現有值,便於開關/天數
    與段大小各自獨立更新。
    """
    from app.services import preferences
    days = max(1, min(30, req.minute_sync_days))
    updates: dict = {
        "minute_sync_enabled": req.minute_sync_enabled,
        "minute_sync_days": days,
    }
    if req.minute_sync_segment_days is not None:
        updates["minute_sync_segment_days"] = max(5, min(30, req.minute_sync_segment_days))
    preferences.save(updates)
    return {
        "minute_sync_enabled": req.minute_sync_enabled,
        "minute_sync_days": days,
        "minute_sync_segment_days": preferences.get_minute_sync_segment_days(),
    }


class RealtimeQuotesPrefs(BaseModel):
    realtime_quotes_enabled: bool


class RealtimeQuoteScopePrefs(BaseModel):
    realtime_pull_stock: bool | None = None
    realtime_pull_etf: bool | None = None
    realtime_pull_index: bool | None = None
    realtime_index_mode: str | None = None
    realtime_index_symbols: list[str] | None = None


@router.put("/preferences/realtime-quotes")
def update_realtime_quotes(req: RealtimeQuotesPrefs, request: Request) -> dict:
    """保存全域即時行情開關。

    none 檔無即時行情權限；free 檔開啟自選股即時；starter+ 開啟全市場即時。
    前端據此把開關置灰 / 回彈。
    """
    from app.services import preferences
    qs = getattr(request.app.state, "quote_service", None)

    allowed = qs.is_realtime_allowed() if qs else True
    if req.realtime_quotes_enabled and not allowed:
        # 當前檔位不允許開啟即時行情 — 強制關閉
        preferences.save({"realtime_quotes_enabled": False})
        if qs:
            qs.disable()
        return {"realtime_quotes_enabled": False, "realtime_allowed": False}
    if req.realtime_quotes_enabled and qs and qs.is_paused():
        # 管道/資料修正執行期間禁止開啟即時行情 — 防止寫盤競態
        raise HTTPException(status_code=409, detail="資料同步執行中，即時行情已臨時暫停，請稍後再開啟")
    if req.realtime_quotes_enabled and qs and qs.realtime_mode() == "watchlist" and not preferences.get_realtime_watchlist_symbols():
        preferences.save({"realtime_quotes_enabled": False})
        return {"realtime_quotes_enabled": False, "realtime_allowed": True, "mode": "watchlist", "error": "watchlist_empty"}

    preferences.save({"realtime_quotes_enabled": req.realtime_quotes_enabled})
    if qs:
        if req.realtime_quotes_enabled:
            qs.enable()
        else:
            qs.disable()
    return {"realtime_quotes_enabled": req.realtime_quotes_enabled, "realtime_allowed": allowed}


@router.put("/preferences/realtime-quote-scope")
def update_realtime_quote_scope(req: RealtimeQuoteScopePrefs) -> dict:
    """保存盤中即時行情範圍；獨立於盤後管道範圍。"""
    from app.services import preferences
    cfg = req.model_dump(exclude_none=True)
    return preferences.set_realtime_quote_scope(cfg)


class RealtimeWatchlistPrefs(BaseModel):
    symbols: list[str] = []


@router.put("/preferences/realtime-watchlist")
def update_realtime_watchlist(req: RealtimeWatchlistPrefs) -> dict:
    """相容舊入口；Free 即時標的由自選頁前 5 個決定。"""
    from app.services import preferences
    symbols = preferences.set_realtime_watchlist_symbols(req.symbols)
    return {"realtime_watchlist_symbols": symbols}


class IndicesNavPinnedPrefs(BaseModel):
    indices_nav_pinned: bool


@router.put("/preferences/indices-nav-pinned")
def update_indices_nav_pinned(req: IndicesNavPinnedPrefs) -> dict:
    """保存側欄指數報價卡片固定顯示開關。
    ON=常駐顯示；OFF=跟隨即時行情開關（僅即時開時顯示）。"""
    from app.services import preferences
    preferences.save({"indices_nav_pinned": req.indices_nav_pinned})
    return {"indices_nav_pinned": req.indices_nav_pinned}


class WatchlistGroupsInNavPrefs(BaseModel):
    watchlist_groups_in_nav: bool


@router.put("/preferences/watchlist-groups-in-nav")
def update_watchlist_groups_in_nav(req: WatchlistGroupsInNavPrefs) -> dict:
    """保存自選分組是否顯示在側邊欄開關。"""
    from app.services import preferences
    preferences.save({"watchlist_groups_in_nav": req.watchlist_groups_in_nav})
    return {"watchlist_groups_in_nav": req.watchlist_groups_in_nav}


class RealtimeMonitorConfigIn(BaseModel):
    sse_refresh_pages: dict[str, bool] | None = None
    sidebar_index_symbols: list[str] | None = None
    minute_intraday_refresh: bool | None = None
    minute_intraday_refresh_interval: int | None = None


@router.put("/preferences/realtime-monitor")
def update_realtime_monitor_config(req: RealtimeMonitorConfigIn, request: Request) -> dict:
    """更新實時監控設定 (SSE 刷新頁面、側欄指數、分時刷新)。"""
    from app.services import preferences

    cfg = req.model_dump(exclude_none=True)
    result = preferences.set_realtime_monitor_config(cfg)

    return result


class QuoteIntervalIn(BaseModel):
    interval: float


class SystemNotifyPrefsIn(BaseModel):
    enabled: bool


@router.put("/preferences/auto-ai-explain")
def update_auto_ai_explain(req: SystemNotifyPrefsIn) -> dict:
    """盤後自動 AI 說明開關 (預設關閉)；開啟後每日資料就緒時為雷達前 5 檔產生並保存。"""
    from app.taiwan import auto_ai_explain
    return {"auto_ai_explain_enabled": auto_ai_explain.set_enabled(req.enabled)}


@router.put("/preferences/auto-advice-reflection")
def update_auto_advice_reflection(req: SystemNotifyPrefsIn) -> dict:
    """盤後自動 AI 反省開關 (預設關閉). 結算照常記錄, 開啟後才呼叫 AI 寫檢討。"""
    from app.taiwan import advice_reflection
    return {"auto_advice_reflection_enabled": advice_reflection.set_enabled(req.enabled)}


@router.put("/preferences/event-ai-explain")
def update_event_ai_explain(req: SystemNotifyPrefsIn) -> dict:
    """提醒觸發後的 AI 解讀開關 (預設關閉)；規則事實提醒一律先送，AI 解讀隨後另發。"""
    from app.taiwan import event_ai_explain
    return {"event_ai_explain_enabled": event_ai_explain.set_enabled(req.enabled)}


@router.put("/preferences/push-digest")
def update_push_digest(req: SystemNotifyPrefsIn) -> dict:
    """早晚各一則「今日一句話」推播開關 (預設關閉)；走全域 LINE / Telegram 通道。"""
    from app.taiwan import push_digest
    return {"push_digest_enabled": push_digest.set_enabled(req.enabled)}


@router.post("/preferences/push-digest/test")
def test_push_digest(kind: str = "morning") -> dict:
    """立即送一則早／晚一句話到已勾選的通道 (需已開啟推播與通道)。"""
    from app.taiwan import push_digest
    if kind not in ("morning", "evening"):
        raise HTTPException(status_code=400, detail="kind 只能是 morning 或 evening")
    if not push_digest.is_enabled():
        raise HTTPException(status_code=409, detail="請先開啟「早晚一句話推播」")
    return push_digest.run(kind)


class WatchlistAnomalyPrefsIn(BaseModel):
    enabled: bool | None = None
    threshold_pct: float | None = Field(default=None, ge=1.0, le=10.0)


@router.put("/preferences/watchlist-anomaly")
def update_watchlist_anomaly(req: WatchlistAnomalyPrefsIn) -> dict:
    """自選股盤中異常摘要 (預設關閉)：漲跌幅超過門檻即提醒，同一天同方向只提醒一次。"""
    from app.taiwan import watchlist_anomaly
    if req.enabled is not None:
        watchlist_anomaly.set_enabled(req.enabled)
    if req.threshold_pct is not None:
        watchlist_anomaly.set_threshold_pct(req.threshold_pct)
    return {
        "watchlist_anomaly_enabled": watchlist_anomaly.is_enabled(),
        "watchlist_anomaly_threshold_pct": watchlist_anomaly.threshold_pct(),
    }


@router.put("/preferences/telegram-query")
def update_telegram_query(req: SystemNotifyPrefsIn) -> dict:
    """Telegram 反向查詢開關 (預設關閉)：在 Telegram 輸入代號即回傳現價與計畫價位。"""
    from app.taiwan import telegram_bot
    enabled = telegram_bot.set_enabled(req.enabled)
    bot = telegram_bot.get_bot()
    started = bot.start() if enabled else (bot.stop() or False)
    return {"telegram_query_enabled": enabled, "bot_started": bool(started)}


@router.put("/preferences/system-notify")
def update_system_notify(req: SystemNotifyPrefsIn) -> dict:
    """系統通知開關 — 開啟後監控告警同時推送到操作系統通知中心。

    純偏好, 無副作用 (不像策略監控要遷移規則), 直接落盤即可。
    quote_service 在每輪告警評估時讀此開關決定是否發系統通知。
    """
    from app.services import preferences
    saved = preferences.set_system_notify_enabled(req.enabled)
    return {"system_notify_enabled": saved}


class NotificationChannelPrefsIn(BaseModel):
    recipient: str = ""
    token: str | None = None
    clear_token: bool = False


class ExternalNotificationChannelsIn(BaseModel):
    channels: list[Literal["line", "telegram"]]


@router.put("/preferences/external-notification-channels")
def update_external_notification_channels(req: ExternalNotificationChannelsIn) -> dict:
    from app.services import preferences

    channels = preferences.set_external_notification_channels([str(channel) for channel in req.channels])
    return {"external_notification_channels": channels}


def _notification_channel_response(channel: str) -> dict:
    from app.services import preferences

    if channel == "line":
        recipient = preferences.get_line_target_id()
        token = preferences.get_line_channel_access_token()
        return {
            "line_target_id": recipient,
            "line_channel_access_token_masked": secrets_store.mask(token),
            "line_configured": bool(recipient and token),
        }
    recipient = preferences.get_telegram_chat_id()
    token = preferences.get_telegram_bot_token()
    return {
        "telegram_chat_id": recipient,
        "telegram_bot_token_masked": secrets_store.mask(token),
        "telegram_configured": bool(recipient and token),
    }


@router.put("/preferences/line-messaging")
def update_line_messaging(req: NotificationChannelPrefsIn) -> dict:
    from app.services import preferences, webhook_adapter

    preferences.set_line_target_id(req.recipient)
    if req.clear_token:
        preferences.set_line_channel_access_token("")
    elif req.token and req.token.strip():
        preferences.set_line_channel_access_token(req.token)
    webhook_adapter.clear_delivery_status("line")
    return _notification_channel_response("line")


@router.put("/preferences/telegram-bot")
def update_telegram_bot(req: NotificationChannelPrefsIn) -> dict:
    from app.services import preferences, webhook_adapter

    preferences.set_telegram_chat_id(req.recipient)
    if req.clear_token:
        preferences.set_telegram_bot_token("")
    elif req.token and req.token.strip():
        preferences.set_telegram_bot_token(req.token)
    webhook_adapter.clear_delivery_status("telegram")
    return _notification_channel_response("telegram")


@router.post("/preferences/line-messaging/test")
def test_line_messaging() -> dict:
    from app.services import preferences, webhook_adapter

    body = webhook_adapter.alert_message(webhook_adapter.build_test_alert_event())
    ok = webhook_adapter.send_line(
        preferences.get_line_channel_access_token(),
        preferences.get_line_target_id(),
        "",
        body,
    )
    return {"ok": ok}


@router.post("/preferences/telegram-bot/test")
def test_telegram_bot() -> dict:
    from app.services import preferences, webhook_adapter

    body = webhook_adapter.alert_message(webhook_adapter.build_test_alert_event())
    ok = webhook_adapter.send_telegram(
        preferences.get_telegram_bot_token(),
        preferences.get_telegram_chat_id(),
        "",
        body,
    )
    return {"ok": ok}


class FinMindSettingsIn(BaseModel):
    token: str | None = None
    enabled: bool | None = None
    clear_token: bool = False


@router.get("/preferences/finmind")
def get_finmind_settings() -> dict:
    from app.services import preferences
    token = preferences.get_finmind_token()
    return {
        "finmind_token_masked": secrets_store.mask(token),
        "has_finmind_token": bool(token),
        "finmind_enabled": preferences.get_finmind_enabled(),
        "token_masked": secrets_store.mask(token),
        "has_token": bool(token),
        "enabled": preferences.get_finmind_enabled(),
    }


@router.put("/preferences/finmind")
def update_finmind_settings(req: FinMindSettingsIn) -> dict:
    from app.services import preferences
    from app.taiwan.fundamental_chips_service import reset_fundamental_chips_service

    if req.clear_token:
        preferences.set_finmind_token("")
    elif req.token is not None and req.token.strip():
        preferences.set_finmind_token(req.token.strip())

    if req.enabled is not None:
        preferences.set_finmind_enabled(req.enabled)

    # Rebuild singleton fundamental chips service to ensure immediate credential refresh
    reset_fundamental_chips_service()

    return get_finmind_settings()


@router.post("/preferences/finmind/test")
def test_finmind_connection() -> dict:
    from app.services import preferences
    from app.taiwan.providers.finmind_provider import FinMindAdapter

    token = preferences.get_finmind_token()
    adapter = FinMindAdapter(token=token, timeout=10)
    return adapter.test_connection(probe_symbol="2330")


@router.get("/ai-status")
def get_ai_status() -> dict:
    """側欄 AI 燈號: 輕量連線檢查，不呼叫生成。"""
    from app.services.ai_health import ai_status

    return ai_status()


# Optional external-context keys. Providers re-read these on next use, so no restart.
_EXTERNAL_KEYS = ("fred_api_key", "finbridge_api_key", "fugle_api_key")


class ExternalKeyIn(BaseModel):
    name: Literal["fred_api_key", "finbridge_api_key", "fugle_api_key"]
    key: str = ""  # empty = clear


@router.get("/preferences/external-keys")
def get_external_keys() -> dict:
    out = {}
    for name in _EXTERNAL_KEYS:
        key = secrets_store.get_ai_config(name)
        out[name] = {"has_key": bool(key), "masked": secrets_store.mask(key)}
    return out


@router.put("/preferences/external-keys")
def update_external_key(req: ExternalKeyIn) -> dict:
    if req.key.strip():
        secrets_store.save({req.name: req.key.strip()})
    else:
        secrets_store.clear(req.name)
    return get_external_keys()


class WebhookEnabledDefaultIn(BaseModel):
    enabled: bool


@router.put("/preferences/webhook-enabled-default")
def update_webhook_enabled_default(req: WebhookEnabledDefaultIn) -> dict:
    """新建監控規則時是否預設勾選推送 (老布爾接口, 相容舊前端)。

    舊布林值無法安全推斷新的 LINE/Telegram 收件者,因此不自動啟用新渠道。
    """
    from app.services import preferences

    saved = preferences.set_webhook_enabled_default(req.enabled)
    return {"webhook_enabled_default": saved}


class WebhookDefaultChannelsIn(BaseModel):
    channels: list[str]  # 多選: ['line','telegram']; 空陣列=預設不推送


@router.put("/preferences/webhook-default-channels")
def update_webhook_default_channels(req: WebhookDefaultChannelsIn) -> dict:
    """新建監控規則時預設勾選的推送渠道 (多選)。

    作為新建規則的預設推送渠道預填, 單條規則仍可在規則編輯頁獨立修改。
    空陣列=預設不推送。白名單外的渠道會被過濾掉。
    """
    from app.services import preferences

    saved = preferences.set_webhook_default_channels(req.channels)
    return {"webhook_default_channels": saved}


@router.put("/preferences/quote-interval")
def update_quote_interval(req: QuoteIntervalIn, request: Request) -> dict:
    """更新行情輪詢間隔。按檔位自動 clamp。"""
    qs = getattr(request.app.state, "quote_service", None)
    if not qs:
        return {"interval": req.interval, "min_interval": 6.0, "max_interval": 60.0}
    clamped = qs.set_interval(req.interval)
    return {
        "interval": clamped,
        "min_interval": qs.get_min_interval(),
        "max_interval": qs.MAX_INTERVAL,
    }


@router.get("/preferences/quote-interval")
def get_quote_interval(request: Request) -> dict:
    """獲取當前行情輪詢間隔和檔位限制。"""
    qs = getattr(request.app.state, "quote_service", None)
    if not qs:
        return {"interval": 6.0, "min_interval": 6.0, "max_interval": 60.0}
    return {
        "interval": qs._interval,
        "min_interval": qs.get_min_interval(),
        "max_interval": qs.MAX_INTERVAL,
    }


class EnrichedBatchSizeIn(BaseModel):
    size: int


@router.put("/preferences/enriched-batch-size")
def update_enriched_batch_size(req: EnrichedBatchSizeIn) -> dict:
    """保存 enriched 全量計算批次大小。"""
    from app.services import preferences
    size = preferences.set_enriched_batch_size(req.size)
    return {"enriched_batch_size": size}


class IndexDailyBatchSizeIn(BaseModel):
    size: int


@router.put("/preferences/index-daily-batch-size")
def update_index_daily_batch_size(req: IndexDailyBatchSizeIn) -> dict:
    """保存指數日 K 同步批次大小。"""
    from app.services import preferences
    size = preferences.set_index_daily_batch_size(req.size)
    return {"index_daily_batch_size": size}

"""Key / 憑據本地儲存(§14)。

儲存位置:`data/user_data/secrets.json`,權限 0600。
優先級:secrets.json > .env > 空。

UI 改 Key 時只動這個檔案,不動 .env。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _path() -> Path:
    from app.config import settings
    p = settings.data_dir / "user_data" / "secrets.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load() -> dict:
    p = _path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning("secrets.json malformed: %s", e)
    return {}


def save(updates: dict) -> dict:
    """合併寫入(不會清掉未提及的欄位)。回傳新內容。"""
    current = load()
    current.update({k: v for k, v in updates.items() if v is not None})
    p = _path()
    p.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return current


def clear(*keys: str) -> dict:
    """清掉指定欄位(留空清全部)。"""
    p = _path()
    if not p.exists():
        return {}
    if not keys:
        p.unlink()
        return {}
    current = load()
    for k in keys:
        current.pop(k, None)
    p.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
    return current


# 還原的備份換了 AI 端點卻沒一起還原金鑰時, 記下被換端點的對象 ("legacy" = 舊版單一設定,
# 其餘為 Key Profile id)。對象未經使用者逐一確認前不送任何金鑰 (含 .env 備用),
# 確認其中一組不會解除其他組。
AI_ENDPOINT_UNCONFIRMED = "ai_endpoint_unconfirmed"
AI_LEGACY_TARGET = "legacy"


def ai_quarantine() -> set[str]:
    value = load().get(AI_ENDPOINT_UNCONFIRMED)
    if isinstance(value, list):
        return {str(v) for v in value}
    return {"*"} if value else set()  # 舊格式 True: 全部停用


def ai_endpoint_unconfirmed() -> bool:
    return bool(ai_quarantine())


def ai_target_unconfirmed(target: str) -> bool:
    quarantine = ai_quarantine()
    return target in quarantine or "*" in quarantine


def confirm_ai_target(target: str) -> None:
    """使用者已重新確認這個對象的端點與金鑰, 只解除它自己。"""
    quarantine = ai_quarantine()
    if target not in quarantine and "*" not in quarantine:
        return
    if "*" in quarantine:
        # 舊格式 (全部停用) 先展開成每個對象, 確認一組不會解除其他組
        from app.services.ai_key_profiles import _load_metadata
        quarantine = {AI_LEGACY_TARGET, *(str(p.get("id")) for p in _load_metadata() if isinstance(p, dict))}
    quarantine.discard(target)
    if quarantine:
        save({AI_ENDPOINT_UNCONFIRMED: sorted(quarantine)})
    else:
        clear(AI_ENDPOINT_UNCONFIRMED)


def get_ai_key() -> str:
    """取當前 AI Key: 優先 active profile → secrets.json 單欄位 → .env。

    多 profile 啟用時直接從 ai_key_profiles 讀取啟用 profile 的 key,
    無需重啟; 不存在任何 profile 時回退到舊的 ai_api_key 單欄位行為。
    """
    import contextlib
    with contextlib.suppress(Exception):
        from app.services.ai_key_profiles import get_active_ai_key, get_active_profile_id, has_any_profile
        if has_any_profile():
            active_id = get_active_profile_id()
            if active_id and ai_target_unconfirmed(active_id):
                return ""  # 不回退到舊版金鑰或 .env: 那會配上未確認的端點
            key = get_active_ai_key()
            if key:
                return key
    if ai_target_unconfirmed(AI_LEGACY_TARGET):
        return ""
    val = load().get("ai_api_key")

    if val:
        return val
    from app.config import settings
    return settings.ai_api_key or ""


def get_ai_config(key: str, default: str = "") -> str:
    """取 AI 設定項:secrets.json 優先,否則 config。"""
    val = load().get(key)
    if val:
        return val
    from app.config import settings
    return getattr(settings, key, default) or default


def get_ai_config_int(key: str, default: int) -> int:
    """取 AI 數值設定項 (如 ai_max_output_tokens): secrets.json 優先,否則 config。"""
    val = load().get(key)
    if val is not None:
        try:
            return int(val)
        except (TypeError, ValueError):
            logger.warning("ai config %s is not an int: %r", key, val)
    from app.config import settings
    return int(getattr(settings, key, default) or default)


def get_env_backed_secret(field: str, env_name: str) -> str:
    """取環境變數後備的金鑰(外掛 API Key 等):secrets.json 優先,否則環境變數。

    UI 寫入 secrets.json 後即覆蓋 .env。
    """
    val = load().get(field)
    if val:
        return str(val).strip()
    return os.environ.get(env_name, "").strip()


def mask(key: str, prefix: int = 4, suffix: int = 4) -> str:
    """脫敏顯示。"""
    if not key:
        return ""
    if len(key) <= prefix + suffix:
        return "•" * len(key)
    return f"{key[:prefix]}{'•' * 6}{key[-suffix:]}"

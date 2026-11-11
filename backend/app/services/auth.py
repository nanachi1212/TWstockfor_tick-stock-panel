"""訪問密碼認證 — 單使用者, 自託管場景。

設計:
  - 密碼用 PBKDF2-HMAC-SHA256 哈希(標準庫 hashlib, 無新依賴), 加隨機 salt。
    即使 auth.json 洩露, 也無法逆向出明文密碼。
  - 會話用隨機 token(token_urlsafe), 記憶體 + 文件雙存(支持多進程/重啟不丟失)。
  - 存儲: data/user_data/auth.json (chmod 0600), 仿 secrets_store 模式。

安全要點:
  - 設密碼接口必須限制本機/內網(見 auth router), 防黑客搶佔域名搶先設密碼。
  - 登入限流: 錯5次鎖5分鐘(見 auth router 記憶體計數)。
  - 單密碼, 不做多使用者(避免重構全項目資料層)。
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import secrets as _secrets
import tempfile
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# PBKDF2 參數(NIST 推薦, 單次校驗 ~100ms, 兼顧安全與回應)
_PBKDF2_ITER = 200_000
_SALT_LEN = 16
_TOKEN_BYTES = 32

# 會話有效期: 30 天(自託管單使用者, 長一點減少重登頻率)
SESSION_TTL = 30 * 24 * 3600

_lock = threading.Lock()
# 記憶體中的有效會話: { token: expire_ts }。進程重啟後從磁盤恢復。
_sessions: dict[str, float] = {}
# 記憶體會話最後一次與 auth.json 同步時的檔案戳記; 檔案被其他程序 (如撤銷腳本) 改過就重讀。
_sessions_stamp: tuple[int, int] | None = None

# 檔案未變更時重用驗證結果; 改密碼後失效, 外部修改則重新驗證。
_configured_cache: bool | None = None
_configured_stamp: tuple[Path, int, int] | None = None


class AuthStoreError(RuntimeError):
    """認證資料不可用時停止認證, 避免誤判為首次設定。"""


def _path() -> Path:
    from app.config import settings
    p = settings.data_dir / "user_data" / "auth.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("sessions", {}), dict):
            raise ValueError("invalid auth structure")
        if (len(bytes.fromhex(data["password_hash"])) != 32
                or len(bytes.fromhex(data["password_salt"])) != _SALT_LEN):
            raise ValueError("invalid password hash or salt")
        if ("verify_hash" in data or "verify_salt" in data) and (
                len(bytes.fromhex(data["verify_hash"])) != 32
                or len(bytes.fromhex(data["verify_salt"])) != _SALT_LEN):
            raise ValueError("invalid verification hash or salt")
        if not all(isinstance(t, str) for t in data.get("verify_sessions", [])):
            raise ValueError("invalid verification sessions")
        return data
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise AuthStoreError("認證檔無法讀取或已損壞, 請修復 auth.json 後重試") from e


def _save(data: dict) -> None:
    tmp: Path | None = None
    try:
        p = _path()
        fd, name = tempfile.mkstemp(prefix=".auth-", suffix=".tmp", dir=p.parent)
        tmp = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
        global _sessions_stamp
        _sessions_stamp = _file_stamp()
    except OSError as e:
        raise AuthStoreError("認證檔無法儲存, 請確認磁碟與檔案權限後重試") from e
    finally:
        if tmp is not None:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)


def _file_stamp() -> tuple[int, int] | None:
    try:
        stat = _path().stat()
    except FileNotFoundError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _sync_sessions_locked() -> None:
    """auth.json 是會話的權威來源: 檔案變了就以磁碟內容取代記憶體會話 (需持鎖呼叫)。"""
    global _sessions_stamp
    stamp = _file_stamp()
    if stamp == _sessions_stamp:
        return
    saved = _load().get("sessions") or {}
    now = time.time()
    _sessions.clear()
    _sessions.update({t: e for t, e in saved.items() if isinstance(e, (int, float)) and e > now})
    _sessions_stamp = stamp


def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """返回 (salt_hex, hash_hex)。salt 為 None 時生成新 salt。"""
    if salt is None:
        salt = os.urandom(_SALT_LEN)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITER)
    return salt.hex(), dk.hex()


def _verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    """恆定時間比較, 防時序攻擊。"""
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITER)
    return _secrets.compare_digest(actual, expected)


# ================================================================
# 密碼管理
# ================================================================

def is_configured() -> bool:
    """檔案變更時重驗內容; 讀取失敗不可沿用快取或視為未設定。"""
    global _configured_cache, _configured_stamp
    try:
        p = _path()
        stat = p.stat()
    except FileNotFoundError:
        return False
    except OSError as e:
        raise AuthStoreError("認證檔無法讀取, 請確認檔案權限後重試") from e
    stamp = (p, stat.st_mtime_ns, stat.st_size)
    if _configured_cache is None or stamp != _configured_stamp:
        _configured_cache = bool(_load().get("password_hash"))
        _configured_stamp = stamp
    return _configured_cache


def set_password(password: str) -> None:
    """設定/修改訪問密碼。清空所有現有會話(強制重新登入)。"""
    global _configured_cache
    if len(password) < 6:
        raise ValueError("密碼至少 6 位")
    salt_hex, hash_hex = _hash_password(password)
    with _lock:
        current = _load()  # 不覆寫已損壞的認證檔。
        _save({
            "password_hash": hash_hex,
            "password_salt": salt_hex,
            "updated_at": int(time.time()),
            "sessions": {},  # 清空持久化會話 (含驗證專用密碼登入的會話)
            # 驗證專用密碼與主密碼各自管理, 改主密碼不清掉它。
            **{k: current[k] for k in ("verify_hash", "verify_salt") if k in current},
        })
        _sessions.clear()  # 儲存成功後才撤銷舊會話。
    _configured_cache = None  # 失效快取, 下次 is_configured 重讀最新真值
    logger.info("access password set")


def bootstrap_from_env() -> bool:
    """首次初始化: 若環境變數 AUTH_PASSWORD 已配置且尚未設過密碼, 則用它設密碼。

    公網伺服器部署場景: 避免每次都要 SSH 端口轉發才能設首個密碼。
    明文密碼只在記憶體/配置中, 經 set_password() 哈希後寫入 auth.json (chmod 0600)。
    一旦設定成功, 後續重啟不再覆蓋 (使用者改密碼走 UI, 不受環境變數影響)。

    Returns:
        True 表示本次用環境變數初始化了密碼; False 表示無需初始化。
    """
    from app.config import _ENV_FILE, settings

    pwd = (settings.auth_password or "").strip()
    # Compose 會對 env_file 中未加單引號的 $VAR 做插值。Docker 部署時同時
    # 只讀掛載原始 .env,首次初始化密碼直接按 dotenv 語義讀取,避免特殊字元被截斷。
    if _ENV_FILE.is_file():
        from dotenv import dotenv_values

        raw_pwd = dotenv_values(_ENV_FILE, encoding="utf-8", interpolate=False).get("AUTH_PASSWORD")
        if isinstance(raw_pwd, str) and raw_pwd.strip():
            pwd = raw_pwd.strip()
    if not pwd:
        return False
    if is_configured():
        # 已設過密碼, 不覆蓋 (避免環境變數反覆重置使用者在 UI 改的密碼)
        return False
    try:
        set_password(pwd)
        logger.info("access password bootstrapped from AUTH_PASSWORD env (one-time)")
        return True
    except ValueError as e:
        # 密碼不合規 (< 6 位), 記日誌但不阻斷啟動
        logger.warning("AUTH_PASSWORD bootstrap skipped: %s", e)
        return False


def set_verify_password(password: str | None) -> None:
    """設定 (或以 None 撤銷) 驗證專用密碼, 並撤銷用舊驗證密碼登入的所有會話。

    驗證專用密碼給本機自動化驗證登入用; 只有 router 判定為本機回環的登入才會呼叫 create_verify_session。
    """
    if password is not None and len(password) < 16:
        raise ValueError("驗證專用密碼至少 16 位")
    with _lock:
        _sync_sessions_locked()
        d = _load()
        if not d.get("password_hash"):
            raise ValueError("尚未設定訪問密碼")
        for token in d.pop("verify_sessions", []):
            _sessions.pop(token, None)
        d.pop("verify_hash", None)
        d.pop("verify_salt", None)
        if password is not None:
            d["verify_salt"], d["verify_hash"] = _hash_password(password)
        d["sessions"] = dict(_sessions)
        _save(d)


def create_verify_session(password: str) -> str | None:
    """驗證專用密碼正確則建立會話 (記在 verify_sessions, 撤銷時一併失效), 否則回傳 None。"""
    with _lock:
        _sync_sessions_locked()
        d = _load()
        if not (d.get("password_hash") and d.get("verify_hash")):
            return None
        if not _verify_password(password, d["verify_salt"], d["verify_hash"]):
            return None
        token = _secrets.token_urlsafe(_TOKEN_BYTES)
        expire = time.time() + SESSION_TTL
        d["sessions"] = {**_sessions, token: expire}
        d["verify_sessions"] = [t for t in d.get("verify_sessions", []) if t in d["sessions"]] + [token]
        _save(d)
        _sessions[token] = expire
    return token


def verify_and_create_session(password: str) -> str | None:
    """驗證主密碼, 成功則創建會話並返回 token, 失敗返回 None。"""
    with _lock:
        _sync_sessions_locked()
        d = _load()
        if not d.get("password_hash"):
            return None
        if not _verify_password(password, d["password_salt"], d["password_hash"]):
            return None
        token = _secrets.token_urlsafe(_TOKEN_BYTES)
        expire = time.time() + SESSION_TTL
        d["sessions"] = {**_sessions, token: expire}
        _save(d)
        _sessions[token] = expire
    return token


def revoke_session(token: str) -> None:
    """註銷會話(登出)。"""
    with _lock:
        _sync_sessions_locked()
        _sessions.pop(token, None)
        _persist_sessions_locked()


def is_valid_session(token: str) -> bool:
    """檢查會話是否有效(存在且未過期)。過期則清理。"""
    if not token:
        return False
    with _lock:
        _sync_sessions_locked()
        expire = _sessions.get(token)
        if expire is None:
            return False
        if time.time() > expire:
            _sessions.pop(token, None)
            _persist_sessions_locked()
            return False
        return True


def _persist_sessions_locked() -> None:
    """把當前記憶體會話寫回 auth.json(需持鎖呼叫)。"""
    d = _load()
    if not d:
        return
    d["sessions"] = {t: exp for t, exp in _sessions.items()}
    _save(d)


def _restore_sessions() -> None:
    """啟動時從 auth.json 恢復未過期會話(支持進程重啟不丟登入態)。"""
    global _sessions_stamp
    with _lock:
        saved = _load().get("sessions") or {}
        _sessions_stamp = None  # 強制從磁碟重讀
        _sync_sessions_locked()
        if len(_sessions) != len(saved):
            # 有過期會話被清理, 落盤一次
            _persist_sessions_locked()


# 模組載入時恢復會話
try:
    _restore_sessions()
except Exception as e:
    logger.warning("restore sessions failed: %s", e)

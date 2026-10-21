"""访问密码认证 — 单用户, 自托管场景。

设计:
  - 密码用 PBKDF2-HMAC-SHA256 哈希(标准库 hashlib, 无新依赖), 加随机 salt。
    即使 auth.json 泄露, 也无法逆向出明文密码。
  - 会话用随机 token(token_urlsafe), 内存 + 文件双存(支持多进程/重启不丢失)。
  - 存储: data/user_data/auth.json (chmod 0600), 仿 secrets_store 模式。

安全要点:
  - 设密码接口必须限制本机/内网(见 auth router), 防黑客抢占域名抢先设密码。
  - 登录限流: 错5次锁5分钟(见 auth router 内存计数)。
  - 单密码, 不做多用户(避免重构全项目数据层)。
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

# PBKDF2 参数(NIST 推荐, 单次校验 ~100ms, 兼顾安全与响应)
_PBKDF2_ITER = 200_000
_SALT_LEN = 16
_TOKEN_BYTES = 32

# 会话有效期: 30 天(自托管单用户, 长一点减少重登频率)
SESSION_TTL = 30 * 24 * 3600

_lock = threading.Lock()
# 内存中的有效会话: { token: expire_ts }。进程重启后从磁盘恢复。
_sessions: dict[str, float] = {}

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
    except OSError as e:
        raise AuthStoreError("認證檔無法儲存, 請確認磁碟與檔案權限後重試") from e
    finally:
        if tmp is not None:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)


def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """返回 (salt_hex, hash_hex)。salt 为 None 时生成新 salt。"""
    if salt is None:
        salt = os.urandom(_SALT_LEN)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITER)
    return salt.hex(), dk.hex()


def _verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    """恒定时间比较, 防时序攻击。"""
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITER)
    return _secrets.compare_digest(actual, expected)


# ================================================================
# 密码管理
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
    """设置/修改访问密码。清空所有现有会话(强制重新登录)。"""
    global _configured_cache
    if len(password) < 6:
        raise ValueError("密码至少 6 位")
    salt_hex, hash_hex = _hash_password(password)
    with _lock:
        current = _load()  # 不覆寫已損壞的認證檔。
        _save({
            "password_hash": hash_hex,
            "password_salt": salt_hex,
            "updated_at": int(time.time()),
            "sessions": {},  # 清空持久化会话 (含驗證專用密碼登入的會話)
            # 驗證專用密碼與主密碼各自管理, 改主密碼不清掉它。
            **{k: current[k] for k in ("verify_hash", "verify_salt") if k in current},
        })
        _sessions.clear()  # 儲存成功後才撤銷舊會話。
    _configured_cache = None  # 失效缓存, 下次 is_configured 重读最新真值
    logger.info("access password set")


def bootstrap_from_env() -> bool:
    """首次初始化: 若环境变量 AUTH_PASSWORD 已配置且尚未设过密码, 则用它设密码。

    公网服务器部署场景: 避免每次都要 SSH 端口转发才能设首个密码。
    明文密码只在内存/配置中, 经 set_password() 哈希后写入 auth.json (chmod 0600)。
    一旦设置成功, 后续重启不再覆盖 (用户改密码走 UI, 不受环境变量影响)。

    Returns:
        True 表示本次用环境变量初始化了密码; False 表示无需初始化。
    """
    from app.config import _ENV_FILE, settings

    pwd = (settings.auth_password or "").strip()
    # Compose 会对 env_file 中未加单引号的 $VAR 做插值。Docker 部署时同时
    # 只读挂载原始 .env,首次初始化密码直接按 dotenv 语义读取,避免特殊字符被截断。
    if _ENV_FILE.is_file():
        from dotenv import dotenv_values

        raw_pwd = dotenv_values(_ENV_FILE, encoding="utf-8", interpolate=False).get("AUTH_PASSWORD")
        if isinstance(raw_pwd, str) and raw_pwd.strip():
            pwd = raw_pwd.strip()
    if not pwd:
        return False
    if is_configured():
        # 已设过密码, 不覆盖 (避免环境变量反复重置用户在 UI 改的密码)
        return False
    try:
        set_password(pwd)
        logger.info("access password bootstrapped from AUTH_PASSWORD env (one-time)")
        return True
    except ValueError as e:
        # 密码不合规 (< 6 位), 记日志但不阻断启动
        logger.warning("AUTH_PASSWORD bootstrap skipped: %s", e)
        return False


def set_verify_password(password: str | None) -> None:
    """設定 (或以 None 撤銷) 驗證專用密碼, 並撤銷用舊驗證密碼登入的所有會話。

    驗證專用密碼給本機自動化驗證登入用; 只有 router 判定為本機回環的登入才會呼叫 create_verify_session。
    """
    if password is not None and len(password) < 16:
        raise ValueError("驗證專用密碼至少 16 位")
    with _lock:
        d = _load()
        if not d.get("password_hash"):
            raise ValueError("尚未设置访问密码")
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
        d = _load()
        if not (d.get("password_hash") and d.get("verify_hash")):
            return None
        if not _verify_password(password, d["verify_salt"], d["verify_hash"]):
            return None
        token = _secrets.token_urlsafe(_TOKEN_BYTES)
        expire = time.time() + SESSION_TTL
        d["sessions"] = {**_sessions, token: expire}
        d["verify_sessions"] = [t for t in d.get("verify_sessions", []) if t in _sessions] + [token]
        _save(d)
        _sessions[token] = expire
    return token


def verify_and_create_session(password: str) -> str | None:
    """验证主密码, 成功则创建会话并返回 token, 失败返回 None。"""
    with _lock:
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
    """注销会话(登出)。"""
    with _lock:
        _sessions.pop(token, None)
        _persist_sessions_locked()


def is_valid_session(token: str) -> bool:
    """检查会话是否有效(存在且未过期)。过期则清理。"""
    if not token:
        return False
    with _lock:
        expire = _sessions.get(token)
        if expire is None:
            return False
        if time.time() > expire:
            _sessions.pop(token, None)
            _persist_sessions_locked()
            return False
        return True


def _persist_sessions_locked() -> None:
    """把当前内存会话写回 auth.json(需持锁调用)。"""
    d = _load()
    if not d:
        return
    d["sessions"] = {t: exp for t, exp in _sessions.items()}
    _save(d)


def _restore_sessions() -> None:
    """启动时从 auth.json 恢复未过期会话(支持进程重启不丢登录态)。"""
    with _lock:
        d = _load()
        now = time.time()
        saved = d.get("sessions") or {}
        for token, expire in saved.items():
            if isinstance(expire, (int, float)) and expire > now:
                _sessions[token] = expire
        if len(_sessions) != len(saved):
            # 有过期会话被清理, 落盘一次
            _persist_sessions_locked()


# 模块加载时恢复会话
try:
    _restore_sessions()
except Exception as e:
    logger.warning("restore sessions failed: %s", e)

"""裝置轉移: 單檔備份 / 還原 (.twstock-backup)。

讓家裡與辦公室兩台電腦搬移使用者自己的設定與私人資料, 不必人工複製 user_data。

格式 (ZIP 容器):
  manifest.json              版本、類別、每個檔案的 sha256; 不含 hostname / username / 路徑
  files/<category>/<name>    允許清單內的 user_data 檔案 (或前端 localStorage 快照)
  secrets.enc                選填; AES-256-GCM, 金鑰由備份密碼經 scrypt 衍生,
                             AAD 綁定整份 manifest, 竄改 manifest 或密文都會解密失敗。

只收允許清單 (allowlist) 內的檔案; 市場資料快取、log、.env、lock、tmp、seed 等
一律不在清單內, 不需逐項排除。
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import logging
import os
import platform
import re
import secrets as _secrets
import shutil
import threading
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

logger = logging.getLogger(__name__)

FORMAT = "twstock-backup"
BACKUP_VERSION = 1
SUPPORTED_BACKUP_VERSIONS = frozenset({1})
FILE_EXTENSION = ".twstock-backup"

MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED = 300 * 1024 * 1024
MAX_ENTRIES = 5000
MIN_PASSWORD_LENGTH = 8
RESTORE_POINTS_KEPT = 5

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**15, 8, 1
_AI_CONFIG = "ai_config.json"         # secrets.json 的非機密部分 (AI model / base_url 等)
_BROWSER_FILE = "local_storage.json"  # 前端 localStorage 快照
_RESTORE_POINTS_DIR = ".restore_points"
_RESTORE_POINT_ID = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")


class TransferError(ValueError):
    """使用者可理解的備份/還原錯誤; 訊息不含密碼或機器路徑。"""


@dataclass(frozen=True)
class Category:
    label: str
    patterns: tuple[str, ...] = ()       # user_data 相對路徑; 目錄只支援單層 "dir/*.json"
    browser_keys: tuple[str, ...] = ()   # 前端 localStorage key (portfolio / UI 偏好住在瀏覽器)


# 顯示與備份順序即此 dict 順序。
CATEGORIES: dict[str, Category] = {
    "app_settings": Category("App 設定", ("preferences.json", "ai_key_profiles.json")),
    "ui_preferences": Category("介面偏好", browser_keys=(
        "tf-theme", "tf-nav-collapsed", "tf-settings-nav-collapsed", "tf-stocks-query-config",
        "monitor_badge_enabled", "alert_toast_enabled", "alert_toast_max", "alert_sound_enabled",
        "alert_sound", "voice_broadcast_enabled", "voice_broadcast_rate", "voice_broadcast_voice",
        "strategy-pool", "watchlist_columns", "stock_info_bar_fields", "stock_volume_compare",
        "stock_preview_intraday_days", "screener_result_columns", "watchlist_view",
        "watchlist_showCandle", "watchlist_showIntraday", "screener_showCandle",
        "screener_showIntraday", "watchlist_boardFilter", "watchlist_excludeST",
        "watchlist_groupStats", "screener-card-size", "strategy-backtest-quick-ranges",
        "data-card-visible", "data-card-order",
    )),
    "monitor_rules": Category("監控規則", ("monitor_rules/*.json",)),
    "buy_point_rules": Category("買點策略", ("taiwan_buy_point_strategies.json",)),
    "strategies": Category("選股策略與自訂訊號", (
        "taiwan_screener_strategies.json", "custom_signals/*.json", "strategy_overrides/*.json",
    )),
    "watchlist": Category("自選股", ("watchlist.parquet", "watchlist_groups.json")),
    "portfolio": Category("投資組合", browser_keys=("portfolio_transactions",)),
    "selection_review": Category("選股回顧快照", ("taiwan_selection_snapshots.json",)),
    "history": Category("提醒與摘要歷史", (
        "alerts.jsonl", "taiwan_ai_research_history.jsonl", "taiwan_daily_briefs.json",
        "research_candidates.json",
    )),
}
SECRETS_CATEGORY = "secrets"
SECRETS_LABEL = "API Keys / Tokens (加密)"

_SETTINGS = ("app_settings", "ui_preferences", "monitor_rules", "buy_point_rules", "strategies")
PRESETS: dict[str, tuple[str, ...]] = {
    "settings": _SETTINGS,
    "settings_portfolio": (*_SETTINGS, "watchlist", "portfolio"),
    "full": tuple(CATEGORIES),
}

# secrets.json 內非機密、但只對本機有意義的欄位 (例如 Codex 執行檔路徑): 永不匯出也不覆蓋。
_MACHINE_LOCAL_KEYS = frozenset({"ai_codex_command"})
_SECRET_SUFFIXES = ("_api_key", "_token", "_secret", "password")

# 絕對路徑: 磁碟代號 (避開 https:)、UNC、常見 Unix 家目錄。
_ABS_PATH = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]|\\\\[^\\\s]+\\|(?:^|[\s\"'])/(?:Users|home)/")

_lock = threading.RLock()  # restore 失敗時會在鎖內呼叫 rollback


def _is_secret_key(key: str) -> bool:
    k = key.lower()
    return k.endswith(_SECRET_SUFFIXES) or k in {"api_key", "token", "secret"}


def _user_dir() -> Path:
    from app.config import settings
    return settings.data_dir / "user_data"


def _app_version() -> str:
    from app import __version__
    return __version__


# ── 路徑淨化 ─────────────────────────────────────────────────────────────────

def _strip_paths(value: Any) -> tuple[Any, int]:
    """移除 JSON 中看起來是本機絕對路徑的字串值; 回傳 (新值, 移除數)。"""
    if isinstance(value, str):
        return value, int(bool(_ABS_PATH.search(value)))
    if isinstance(value, dict):
        out, n = {}, 0
        for k, v in value.items():
            if isinstance(v, str) and _ABS_PATH.search(v):
                n += 1
                continue
            out[k], m = _strip_paths(v)
            n += m
        return out, n
    if isinstance(value, list):
        items, n = [], 0
        for v in value:
            if isinstance(v, str) and _ABS_PATH.search(v):
                n += 1
                continue
            new, m = _strip_paths(v)
            items.append(new)
            n += m
        return items, n
    return value, 0


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def _sanitize_file(rel: str, raw: bytes) -> tuple[bytes, int]:
    if rel.endswith(".json"):
        value, n = _strip_paths(json.loads(raw.decode("utf-8")))
        return (_json_bytes(value) if n else raw), n
    if rel.endswith(".jsonl"):
        lines, total = [], 0
        for line in raw.decode("utf-8").splitlines():
            if not line.strip():
                continue
            value, n = _strip_paths(json.loads(line))
            total += n
            lines.append(json.dumps(value, ensure_ascii=False) if n else line)
        return ("\n".join(lines) + "\n").encode("utf-8") if lines else b"", total
    if rel.endswith(".parquet"):
        import polars as pl
        df = pl.read_parquet(io.BytesIO(raw))
        for col in df.columns:
            if df[col].dtype == pl.Utf8 and any(
                _ABS_PATH.search(v) for v in df[col].drop_nulls().to_list()
            ):
                raise TransferError(f"{rel} 含本機路徑, 已停止匯出")
    return raw, 0


# ── 檔案收集 ─────────────────────────────────────────────────────────────────

def _matches(category: str, rel: str) -> bool:
    return any(fnmatchcase(rel, p) and "/" not in rel[len(p.split("*")[0]):]
               for p in CATEGORIES[category].patterns)


def _current_files(user_dir: Path, category: str) -> list[str]:
    """目前 user_data 中屬於此類別的檔案 (相對 POSIX 路徑)。"""
    out: list[str] = []
    for pattern in CATEGORIES[category].patterns:
        if "*" in pattern:
            folder, glob = pattern.split("/", 1)
            d = user_dir / folder
            if d.is_dir():
                out.extend(f"{folder}/{p.name}" for p in sorted(d.glob(glob)) if p.is_file())
        elif (user_dir / pattern).is_file():
            out.append(pattern)
    return out


def _read_secrets_json(user_dir: Path) -> dict[str, Any]:
    p = user_dir / "secrets.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise TransferError("secrets.json 無法讀取, 已停止以保護現有設定") from e
    return data if isinstance(data, dict) else {}


def _read_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise TransferError(f"{path.name} 無法讀取, 已停止以保護現有設定") from e


def _public_ai_config(secrets_json: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in secrets_json.items()
            if not _is_secret_key(k) and k not in _MACHINE_LOCAL_KEYS}


def _secret_part(secrets_json: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in secrets_json.items() if _is_secret_key(k)}


def _clean_browser(category: str, values: dict[str, Any] | None) -> dict[str, str]:
    allowed = set(CATEGORIES[category].browser_keys)
    out: dict[str, str] = {}
    for key, raw in (values or {}).items():
        if key not in allowed or not isinstance(raw, str):
            continue
        try:
            parsed = json.loads(raw)
        except ValueError:
            if not _ABS_PATH.search(raw):
                out[key] = raw
            continue
        cleaned, n = _strip_paths(parsed)
        if isinstance(cleaned, str) and n:
            continue
        out[key] = json.dumps(cleaned, ensure_ascii=False) if n else raw
    return out


# ── 加密 ─────────────────────────────────────────────────────────────────────

def _canonical(manifest: dict[str, Any]) -> bytes:
    return json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _derive_key(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(password.encode("utf-8"))


def _encrypt(payload: dict[str, Any], password: str, aad: bytes) -> bytes:
    salt, nonce = _secrets.token_bytes(16), _secrets.token_bytes(12)
    key = _derive_key(password, salt, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P)
    ciphertext = AESGCM(key).encrypt(nonce, _json_bytes(payload), aad)
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return _json_bytes({
        "cipher": "AES-256-GCM", "kdf": "scrypt",
        "n": _SCRYPT_N, "r": _SCRYPT_R, "p": _SCRYPT_P,
        "salt": b64(salt), "nonce": b64(nonce), "ciphertext": b64(ciphertext),
    })


def _decrypt(blob: bytes, password: str, aad: bytes) -> dict[str, Any]:
    try:
        env = json.loads(blob.decode("utf-8"))
        n, r, p = int(env["n"]), int(env["r"]), int(env["p"])
        if env.get("cipher") != "AES-256-GCM" or env.get("kdf") != "scrypt":
            raise TransferError("不支援的加密格式")
        # 防止惡意檔案用極端 KDF 參數拖垮本機
        if not (2**14 <= n <= 2**18 and 1 <= r <= 16 and 1 <= p <= 4):
            raise TransferError("加密參數超出允許範圍")
        salt, nonce, ct = (base64.b64decode(env[k]) for k in ("salt", "nonce", "ciphertext"))
    except (KeyError, TypeError, ValueError) as e:
        if isinstance(e, TransferError):
            raise
        raise TransferError("加密區塊格式錯誤") from e
    try:
        plain = AESGCM(_derive_key(password, salt, n, r, p)).decrypt(nonce, ct, aad)
    except InvalidTag as e:
        raise TransferError("密碼錯誤, 或備份檔已被修改") from e
    payload = json.loads(plain.decode("utf-8"))
    if not isinstance(payload, dict):
        raise TransferError("加密區塊格式錯誤")
    return payload


# ── 匯出 ─────────────────────────────────────────────────────────────────────

def resolve_categories(preset: str | None, categories: list[str] | None) -> list[str]:
    if categories:
        unknown = [c for c in categories if c not in CATEGORIES]
        if unknown:
            raise TransferError(f"未知的備份類別: {', '.join(unknown)}")
        return [c for c in CATEGORIES if c in categories]
    if preset not in PRESETS:
        raise TransferError("請選擇備份範圍")
    return list(PRESETS[preset])


def build_backup(
    categories: list[str],
    *,
    browser_storage: dict[str, dict[str, Any]] | None = None,
    include_secrets: bool = False,
    password: str | None = None,
    user_dir: Path | None = None,
) -> bytes:
    """建立 .twstock-backup 檔案內容。"""
    user_dir = user_dir or _user_dir()
    if include_secrets and len(password or "") < MIN_PASSWORD_LENGTH:
        raise TransferError(f"包含 API Keys 時必須設定至少 {MIN_PASSWORD_LENGTH} 字元的備份密碼")

    entries: dict[str, tuple[str, bytes]] = {}  # archive path -> (category, bytes)
    redacted = 0
    for cat in categories:
        spec = CATEGORIES[cat]
        for rel in _current_files(user_dir, cat):
            raw = (user_dir / rel).read_bytes()
            try:
                data, n = _sanitize_file(rel, raw)
            except TransferError:
                raise
            except Exception as e:  # 損壞檔案不可默默略過
                raise TransferError(f"{cat} 的檔案 {rel} 格式損壞, 無法備份") from e
            redacted += n
            entries[f"files/{cat}/{rel}"] = (cat, data)
        if cat == "app_settings":
            public = _public_ai_config(_read_secrets_json(user_dir))
            if public:
                cleaned, n = _strip_paths(public)
                redacted += n
                entries[f"files/{cat}/{_AI_CONFIG}"] = (cat, _json_bytes(cleaned))
        if spec.browser_keys:
            values = _clean_browser(cat, (browser_storage or {}).get(cat))
            if values:
                entries[f"files/{cat}/{_BROWSER_FILE}"] = (cat, _json_bytes(values))

    # 最終防線 (文字檔; parquet 已在 _sanitize_file 逐欄檢查): JSON key 等淨化不到處含絕對路徑即拒絕。
    for name, (_cat, data) in entries.items():
        text = data.decode("utf-8", errors="ignore").replace("\\\\", "\\")  # 還原 JSON 跳脫
        if not name.endswith(".parquet") and _ABS_PATH.search(text):
            raise TransferError(f"{name.split('/', 2)[2]} 含本機路徑, 已停止匯出")

    manifest_categories = list(categories) + ([SECRETS_CATEGORY] if include_secrets else [])
    manifest: dict[str, Any] = {
        "format": FORMAT,
        "backup_version": BACKUP_VERSION,
        "app_version": _app_version(),
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        # 僅作業系統類型; 不含 hostname / username / 路徑。
        "source_machine": {"os": platform.system() or "unknown"},
        "categories": manifest_categories,
        "file_count": len(entries),
        "hashes": {name: hashlib.sha256(data).hexdigest() for name, (_c, data) in sorted(entries.items())},
        "redacted_path_values": redacted,
        "secrets": {"included": include_secrets, "cipher": "AES-256-GCM" if include_secrets else None},
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", _json_bytes(manifest))
        for name, (_cat, data) in sorted(entries.items()):
            zf.writestr(name, data)
        if include_secrets:
            secrets_json = _read_secrets_json(user_dir)
            payload = {
                "secrets_json": _secret_part(secrets_json),
                "ai_key_profiles_secrets": _read_json_file(user_dir / "ai_key_profiles_secrets.json", {}),
            }
            zf.writestr("secrets.enc", _encrypt(payload, password or "", _canonical(manifest)))
    logger.info("device transfer: exported %d files, categories=%s, secrets=%s",
                len(entries), manifest_categories, include_secrets)
    return buf.getvalue()


# ── 解析 / 驗證 ──────────────────────────────────────────────────────────────

@dataclass
class ParsedBackup:
    manifest: dict[str, Any]
    files: dict[str, dict[str, bytes]]       # category -> {rel: bytes}
    secrets_blob: bytes | None
    issues: dict[str, list[str]]             # category -> 不相容原因


def _validate_entry(category: str, rel: str, data: bytes) -> str | None:
    """格式檢查; 回傳問題描述或 None。不做任何 silent migration。"""
    try:
        if rel == _BROWSER_FILE:
            value = json.loads(data.decode("utf-8"))
            allowed = set(CATEGORIES[category].browser_keys)
            if not isinstance(value, dict) or any(
                k not in allowed or not isinstance(v, str) for k, v in value.items()
            ):
                return f"{rel}: 含未知的瀏覽器設定"
        elif rel.endswith(".json"):
            value = json.loads(data.decode("utf-8"))
            if not isinstance(value, (dict, list)):
                return f"{rel}: JSON 結構不符"
            if (rel in {"preferences.json", _AI_CONFIG} or "/" in rel) and not isinstance(value, dict):
                return f"{rel}: JSON 結構不符"
        elif rel.endswith(".jsonl"):
            for line in data.decode("utf-8").splitlines():
                if line.strip():
                    json.loads(line)
        elif rel.endswith(".parquet"):
            import polars as pl
            if "symbol" not in pl.read_parquet_schema(io.BytesIO(data)):
                return f"{rel}: 欄位結構不符"
        else:
            return f"{rel}: 不支援的檔案類型"
    except Exception:  # 任何解析失敗都視為不相容, 不寫入
        return f"{rel}: 內容無法解析"
    return None


def parse_backup(archive: bytes) -> ParsedBackup:
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise TransferError("備份檔過大")
    try:
        zf = zipfile.ZipFile(io.BytesIO(archive))
    except zipfile.BadZipFile as e:
        raise TransferError("不是有效的 .twstock-backup 檔案") from e
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES or sum(i.file_size for i in infos) > MAX_TOTAL_UNCOMPRESSED:
            raise TransferError("備份檔內容過大")
        names = {i.filename for i in infos}
        if "manifest.json" not in names:
            raise TransferError("不是有效的 .twstock-backup 檔案 (缺少 manifest)")
        try:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        except ValueError as e:
            raise TransferError("manifest 格式錯誤") from e
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
            raise TransferError("不是有效的 .twstock-backup 檔案")
        version = manifest.get("backup_version")
        if version not in SUPPORTED_BACKUP_VERSIONS:
            newer = isinstance(version, int) and version > BACKUP_VERSION
            raise TransferError(
                f"此備份格式版本 ({version}) 由較新版本的 App 建立, 請先更新 App 再匯入"
                if newer else f"無法識別的備份格式版本 ({version}), 不進行匯入"
            )
        hashes = manifest.get("hashes")
        categories = manifest.get("categories")
        if not isinstance(hashes, dict) or not isinstance(categories, list):
            raise TransferError("manifest 缺少必要欄位")

        issues: dict[str, list[str]] = {}
        unknown = [c for c in categories if c not in CATEGORIES and c != SECRETS_CATEGORY]
        if unknown:
            issues["_global"] = [f"未知的類別: {', '.join(map(str, unknown))}"]

        files: dict[str, dict[str, bytes]] = {}
        payload_names = {n for n in names if n.startswith("files/")}
        if payload_names != set(hashes):
            raise TransferError("備份檔內容與 manifest 不一致, 檔案已損壞或遭竄改")
        for name, expected in hashes.items():
            parts = PurePosixPath(name).parts
            if len(parts) < 3 or parts[0] != "files" or ".." in parts or "\\" in name:
                raise TransferError("備份檔含不合法的路徑")
            cat, rel = parts[1], "/".join(parts[2:])
            data = zf.read(name)
            if hashlib.sha256(data).hexdigest() != expected:
                raise TransferError(f"{rel} 雜湊不符, 備份檔已損壞或遭竄改")
            if cat not in CATEGORIES or cat not in categories:
                issues.setdefault("_global", []).append(f"未知的檔案: {name}")
                continue
            virtual = (rel == _AI_CONFIG and cat == "app_settings") or (
                rel == _BROWSER_FILE and CATEGORIES[cat].browser_keys)
            if not virtual and not _matches(cat, rel):
                issues.setdefault(cat, []).append(f"{rel}: 不在允許清單")
                continue
            problem = _validate_entry(cat, rel, data)
            if problem:
                issues.setdefault(cat, []).append(problem)
            files.setdefault(cat, {})[rel] = data

        secrets_blob = zf.read("secrets.enc") if "secrets.enc" in names else None
        if (SECRETS_CATEGORY in categories) != (secrets_blob is not None):
            raise TransferError("加密區塊與 manifest 不一致, 備份檔已損壞或遭竄改")
    return ParsedBackup(manifest, files, secrets_blob, issues)


def preview(archive: bytes) -> dict[str, Any]:
    parsed = parse_backup(archive)
    m = parsed.manifest
    current = _app_version()
    cats = []
    for cat in m["categories"]:
        if cat == SECRETS_CATEGORY:
            cats.append({"id": cat, "label": SECRETS_LABEL, "file_count": 0,
                         "compatible": True, "issues": []})
        elif cat in CATEGORIES:
            cat_issues = parsed.issues.get(cat, [])
            cats.append({"id": cat, "label": CATEGORIES[cat].label,
                         "file_count": len(parsed.files.get(cat, {})),
                         "compatible": not cat_issues, "issues": cat_issues})
    global_issues = parsed.issues.get("_global", [])
    return {
        "backup_version": m["backup_version"],
        "app_version": m.get("app_version"),
        "current_app_version": current,
        "version_match": m.get("app_version") == current,
        "created_at": m.get("created_at"),
        "source_machine": m.get("source_machine") or {},
        "file_count": m.get("file_count"),
        "categories": cats,
        "secrets": {"included": parsed.secrets_blob is not None, "encrypted": True},
        "issues": global_issues,
        "compatible": not global_issues and all(c["compatible"] for c in cats),
    }


# ── 還原 ─────────────────────────────────────────────────────────────────────

def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{_secrets.token_hex(4)}.restore-tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def _apply(user_dir: Path, rel: str, data: bytes | None) -> None:
    target = user_dir / rel
    if data is None:
        target.unlink(missing_ok=True)
    else:
        _atomic_write(target, data)


def _create_restore_point(user_dir: Path, rels: list[str]) -> str:
    rp_id = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{_secrets.token_hex(4)}"
    root = user_dir / _RESTORE_POINTS_DIR / rp_id
    existed: dict[str, bool] = {}
    for rel in rels:
        src = user_dir / rel
        existed[rel] = src.is_file()
        if existed[rel]:
            dst = root / "files" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    root.mkdir(parents=True, exist_ok=True)
    _atomic_write(root / "index.json", _json_bytes({"files": existed}))
    return rp_id


def rollback(restore_point_id: str, *, user_dir: Path | None = None) -> dict[str, Any]:
    """把還原點記錄的檔案恢復成還原前的狀態。"""
    user_dir = user_dir or _user_dir()
    if not _RESTORE_POINT_ID.match(restore_point_id or ""):
        raise TransferError("還原點 ID 不合法")
    root = user_dir / _RESTORE_POINTS_DIR / restore_point_id
    index = _read_json_file(root / "index.json", None)
    if not isinstance(index, dict):
        raise TransferError("找不到此還原點")
    with _lock:
        for rel, existed in index["files"].items():
            _apply(user_dir, rel, (root / "files" / rel).read_bytes() if existed else None)
    _invalidate_caches()
    logger.info("device transfer: rolled back %d files", len(index["files"]))
    return {"restore_point": restore_point_id, "files": len(index["files"])}


def _prune_restore_points(user_dir: Path) -> None:
    base = user_dir / _RESTORE_POINTS_DIR
    points = sorted(p for p in base.iterdir() if p.is_dir() and _RESTORE_POINT_ID.match(p.name))
    for old in points[:-RESTORE_POINTS_KEPT]:
        shutil.rmtree(old, ignore_errors=True)


def _invalidate_caches() -> None:
    with contextlib.suppress(Exception):
        from app.services import preferences
        preferences._invalidate_cache()


def restore(
    archive: bytes,
    categories: list[str],
    *,
    password: str | None = None,
    user_dir: Path | None = None,
) -> dict[str, Any]:
    """把所選類別寫回本機。全部成功才生效; 任一步失敗即依還原點 rollback。

    所選類別以備份內容「取代」: 備份中沒有、但本機有的同類檔案會被移除 (已存在還原點)。
    回傳 browser_storage 讓前端寫回 localStorage; 前端寫入失敗時應呼叫 rollback()。
    """
    user_dir = user_dir or _user_dir()
    parsed = parse_backup(archive)
    available = parsed.manifest["categories"]
    if not categories:
        raise TransferError("請選擇要還原的類別")
    missing = [c for c in categories if c not in available]
    if missing:
        raise TransferError(f"備份中沒有這些類別: {', '.join(missing)}")
    if parsed.issues.get("_global"):
        raise TransferError("備份含無法識別的內容, 不進行匯入")
    bad = [c for c in categories if parsed.issues.get(c)]
    if bad:
        raise TransferError(f"這些類別與目前版本不相容: {', '.join(bad)}")

    secret_payload: dict[str, Any] | None = None
    if SECRETS_CATEGORY in categories:
        if not password:
            raise TransferError("還原 API Keys 需要輸入備份密碼")
        secret_payload = _decrypt(parsed.secrets_blob or b"", password, _canonical(parsed.manifest))

    plan: dict[str, bytes | None] = {}
    browser: dict[str, dict[str, str]] = {}
    for cat in categories:
        if cat == SECRETS_CATEGORY:
            continue
        backup_files = parsed.files.get(cat, {})
        if CATEGORIES[cat].browser_keys:
            raw = backup_files.get(_BROWSER_FILE)
            browser[cat] = json.loads(raw.decode("utf-8")) if raw else {}
        for rel in _current_files(user_dir, cat):
            plan[rel] = None
        plan.update({rel: data for rel, data in backup_files.items()
                     if rel not in (_AI_CONFIG, _BROWSER_FILE)})

    # secrets.json 同時存放 AI 設定與金鑰: 分開合併, 未選的部分保留本機現值。
    if "app_settings" in categories or secret_payload is not None:
        current = _read_secrets_json(user_dir)
        public = _public_ai_config(current)
        if "app_settings" in categories:
            raw = parsed.files.get("app_settings", {}).get(_AI_CONFIG)
            public = json.loads(raw.decode("utf-8")) if raw else {}
        secret = _secret_part(current)
        if secret_payload is not None:
            secret = dict(secret_payload.get("secrets_json") or {})
            plan["ai_key_profiles_secrets.json"] = _json_bytes(
                secret_payload.get("ai_key_profiles_secrets") or {})
        local = {k: v for k, v in current.items() if k in _MACHINE_LOCAL_KEYS}
        merged = {**{k: v for k, v in public.items() if not _is_secret_key(k)}, **secret, **local}
        plan["secrets.json"] = _json_bytes(merged) if merged else None

    with _lock:
        rp_id = _create_restore_point(user_dir, sorted(plan))
        try:
            for rel in sorted(plan):
                _apply(user_dir, rel, plan[rel])
        except Exception as e:
            logger.warning("device transfer: restore failed (%s), rolling back", type(e).__name__)
            rollback(rp_id, user_dir=user_dir)
            raise TransferError("寫入失敗, 已自動復原到還原前的狀態") from e
        with contextlib.suppress(OSError):
            _prune_restore_points(user_dir)
    _invalidate_caches()
    logger.info("device transfer: restored categories=%s files=%d", categories, len(plan))
    return {
        "restore_point": rp_id,
        "categories": categories,
        "written": sum(1 for v in plan.values() if v is not None),
        "removed": sum(1 for v in plan.values() if v is None),
        "browser_storage": browser,
    }


def options() -> dict[str, Any]:
    return {
        "categories": [{"id": k, "label": v.label, "browser_keys": list(v.browser_keys)}
                       for k, v in CATEGORIES.items()],
        "presets": {k: list(v) for k, v in PRESETS.items()},
        "min_password_length": MIN_PASSWORD_LENGTH,
        "file_extension": FILE_EXTENSION,
    }

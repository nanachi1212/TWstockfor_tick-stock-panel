"""Load custom data source definitions from user data files."""
from __future__ import annotations

import importlib
import logging
import math
import re
import shutil
import subprocess
from pathlib import Path

import yaml

from app.config import settings
from app.data_providers.custom.config import (
    DEFAULT_TIMEOUT,
    MAX_TIMEOUT,
    CustomSourceConfig,
    config_from_dict,
    load_config,
)
from app.data_providers.custom.provider import GenericHTTPProvider

logger = logging.getLogger(__name__)

_PROVIDERS: dict[str, GenericHTTPProvider] = {}
_LOAD_ERRORS: list[dict] = []

# 內置外掛狀態: {name: {available, status, runtime, ...}} 供設定頁獨立分類展示。
# available=False 的外掛不註冊進 _PROVIDERS (不可切換), 但記錄狀態供 UI 顯示安裝提示。
_PLUGIN_STATUS: dict[str, dict] = {}

_NAME_RE = re.compile(r"^[a-z0-9_]+$")


def plugins_dir() -> Path:
    """內置可選外掛目錄 (app/plugins/, 與現有包結構一致, 開發態/容器態路徑統一)。"""
    return Path(__file__).resolve().parents[2] / "plugins"


def data_sources_dir() -> Path:
    return settings.data_dir / "data_sources"


def load_all(path: Path | None = None) -> None:
    """Load all custom provider YAML files into process memory."""
    global _PROVIDERS, _LOAD_ERRORS
    for provider in _PROVIDERS.values():
        provider.close()
    _PROVIDERS = {}
    _LOAD_ERRORS = []

    base = path or data_sources_dir()
    base.mkdir(parents=True, exist_ok=True)
    for file in sorted([*base.glob("*.yaml"), *base.glob("*.yml")]):
        try:
            config = load_config(file)
            provider = GenericHTTPProvider(config)
            errors = provider.validate()
            if errors:
                _LOAD_ERRORS.append({"path": str(file), "name": config.name, "errors": errors})
                provider.close()
                continue
            _PROVIDERS[config.name] = provider
        except Exception as e:  # noqa: BLE001
            logger.warning("custom data source load failed %s: %s", file, e)
            _LOAD_ERRORS.append({"path": str(file), "errors": [str(e)]})

    # 內置可選外掛 (plugins/ 目錄)。與使用者 YAML 源獨立, 缺依賴只記狀態不報錯。
    _load_builtin_plugins()


def list_sources() -> list[dict]:
    """只列出使用者自定義 (YAML) 源。內置外掛 (builtin=True) 由 list_plugins 獨立呈現。"""
    return [
        {
            "name": provider.name,
            "display_name": provider.config.display_name,
            "datasets": sorted(provider.config.datasets.keys()),
            "path": str(provider.config.path) if provider.config.path else None,
        }
        for provider in _PROVIDERS.values()
        if not getattr(provider, "builtin", False)
    ]


def list_plugins() -> list[dict]:
    """返回所有內置外掛的狀態 (含已裝/未裝), 供設定頁獨立分類顯示。"""
    return list(_PLUGIN_STATUS.values())


def plugin_manifest(name: str) -> dict | None:
    """讀取指定外掛的 plugin.yaml 清單。名稱不合 _NAME_RE 時不碰文件系統(防路徑穿越與 UNC 路徑)。"""
    if not _NAME_RE.fullmatch(name or ""):
        return None
    plugin_dir = plugins_dir() / name
    manifest_path = plugin_dir / "plugin.yaml"
    if not manifest_path.exists():
        return None
    return yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}


def plugin_dir_of(name: str) -> Path:
    """返回外掛目錄路徑。"""
    if not _NAME_RE.fullmatch(name or ""):
        raise ValueError(f"invalid plugin name: {name!r}")
    return plugins_dir() / name


def probe_plugin_key(name: str, api_key: str) -> tuple[bool, str]:
    """呼叫外掛的 probe_api_key(key) 探測候選 Key(不落盤)。

    約定: 聲明了 api_key_env 的外掛, 其 entry 模組提供模組級
    probe_api_key(key) -> (ok, reason)。未聲明或未提供 → (False, 原因)。
    """
    manifest = plugin_manifest(name)
    if manifest is None:
        return False, f"插件 '{name}' 不存在"
    if not manifest.get("api_key_env"):
        return False, f"插件 '{name}' 不支持在界面配置 Key"
    entry = str(manifest.get("entry") or "")
    if ":" not in entry:
        return False, f"插件 '{name}' entry 非法"
    try:
        module = importlib.import_module(entry.split(":", 1)[0])
    except Exception as e:
        return False, f"外掛模組載入失敗: {e}"
    probe = getattr(module, "probe_api_key", None)
    if probe is None:
        return False, f"外掛 '{name}' 未提供 Key 探測"
    try:
        return probe(api_key)
    except Exception as e:
        return False, f"探測失敗: {e}"


def install_plugin(name: str) -> tuple[bool, str]:
    """安裝指定外掛的依賴。根據 runtime 執行 npm install / pip install。

    返回 (是否成功, 消息)。成功後呼叫方應 reload 重新掃描。
    依賴未找到 (npm/pip 缺失) 或命令失敗時返回 False。
    """
    manifest = plugin_manifest(name)
    if manifest is None:
        return False, f"外掛 '{name}' 不存在或無 plugin.yaml"
    runtime = str(manifest.get("runtime", "none")).lower()
    pdir = plugin_dir_of(name)
    if not pdir.exists():
        return False, f"外掛目錄不存在: {pdir}"

    try:
        if runtime == "node":
            npm = shutil.which("npm")
            if not npm:
                return False, "未找到 npm, 請先安裝 Node.js (>=18)"
            # 在外掛目錄執行 npm install
            result = subprocess.run(
                [npm, "install", "--omit=dev", "--no-audit", "--no-fund"],
                cwd=str(pdir),
                capture_output=True,
                text=True,
                timeout=300,
            )
        elif runtime == "python":
            import sys
            # Python 型外掛: 優先用 uv pip install (uv 管理的 venv 無 pip 模組),
            # 回退 python -m pip。都裝進當前後端虛擬環境。
            # 關鍵: uv 分支必須顯式傳 --python sys.executable。dev.ps1 直接跑
            # .venv/Scripts/python.exe 而不 activate, 後端進程 VIRTUAL_ENV 為空,
            # uv pip 會預設選 PATH 上的基礎解釋器 (如 conda base, 常為只讀) →
            # 裝錯環境並 exit 2 (訪問拒絕)。--python 鎖定後端自身 venv, 與 pip
            # 回退路徑 (sys.executable -m pip) 的目標一致。
            # uv 容錯: 使用者全域 uv.toml 配置錯誤時 exit 2, 回退 --no-config 重試。
            # UV_HTTP_TIMEOUT=300: akshare 等含大包(如 mini-racer 14MB), 預設 30s 不夠。
            req = pdir / "requirements.txt"
            if not req.exists():
                return False, "Python 型插件需要 requirements.txt"
            uv_bin = shutil.which("uv")
            if uv_bin:
                result = subprocess.run(
                    [uv_bin, "pip", "install", "--python", sys.executable, "-r", str(req)],
                    capture_output=True, text=True, timeout=300,
                    env={**__import__("os").environ, "UV_HTTP_TIMEOUT": "300"},
                )
                # exit 2 通常是設定檔解析錯誤, 繞過配置重試
                # --no-config 會丟鏡像, 顯式傳國內鏡像加速 (與使用者 uv.toml 意圖一致)
                if result.returncode == 2:
                    result = subprocess.run(
                        [uv_bin, "pip", "install", "--no-config",
                         "--index-url", "https://pypi.tuna.tsinghua.edu.cn/simple",
                         "--python", sys.executable,
                         "-r", str(req)],
                        capture_output=True, text=True, timeout=300,
                        env={**__import__("os").environ, "UV_HTTP_TIMEOUT": "300"},
                    )
            else:
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-r", str(req)],
                    capture_output=True, text=True, timeout=300,
                )
        else:
            return False, f"runtime={runtime} 無需安裝依賴"
    except subprocess.TimeoutExpired:
        return False, "安裝超時 (5分鐘), 請檢查網路後重試"
    except Exception as e:  # noqa: BLE001
        return False, f"安裝失敗: {e}"

    if result.returncode != 0:
        # 取 stderr 的第一個 error: 行(真正的錯誤原因), 避免把 uv 的長字段列表返回給使用者
        raw = (result.stderr or result.stdout or "").strip()
        first_err = ""
        for line in raw.splitlines():
            if line.strip().startswith(("error", "Error", "Caused by")):
                first_err = line.strip()
                break
        msg = first_err or raw[-200:]
        return False, f"安裝失敗 (exit {result.returncode}): {msg}"
    return True, "安裝成功"


def uninstall_plugin(name: str) -> tuple[bool, str]:
    """解除安裝指定外掛的依賴。

    node 型: 刪除 node_modules 目錄 (乾淨徹底, 下次需要重新 npm install)。
    python 型: pip uninstall (包名從 requirements.txt 推斷)。
    """
    import shutil as _shutil

    manifest = plugin_manifest(name)
    if manifest is None:
        return False, f"外掛 '{name}' 不存在或無 plugin.yaml"
    runtime = str(manifest.get("runtime", "none")).lower()
    pdir = plugin_dir_of(name)
    if not pdir.exists():
        return False, f"外掛目錄不存在: {pdir}"

    if runtime == "node":
        nm = pdir / "node_modules"
        if not nm.exists():
            return True, "node_modules 不存在, 無需解除安裝"
        try:
            _shutil.rmtree(nm)
            return True, "已刪除 node_modules"
        except Exception as e:  # noqa: BLE001
            return False, f"刪除 node_modules 失敗: {e}"

    if runtime == "python":
        req = pdir / "requirements.txt"
        if not req.exists():
            return False, "Python 型外掛缺少 requirements.txt, 無法自動解除安裝"
        # 讀 requirements.txt 拿包名, 逐個 pip uninstall -y
        pkgs = [l.strip().split("==")[0].split(">=")[0].strip()
                for l in req.read_text().splitlines()
                if l.strip() and not l.startswith("#")]
        if not pkgs:
            return True, "requirements.txt 無有效包名"
        import sys
        uv_bin = _shutil.which("uv")
        # 與 install 一致: uv 分支顯式 --python 鎖定後端 venv (無 VIRTUAL_ENV 時
        # 會預設落到 PATH 基礎解釋器), 與 pip 回退 (sys.executable -m pip) 目標一致。
        cmd = ([uv_bin, "pip", "uninstall", "--python", sys.executable, *pkgs]
               if uv_bin else [sys.executable, "-m", "pip", "uninstall", "-y", *pkgs])
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                return False, f"解除安裝失敗: {(result.stderr or '').strip()[-300:]}"
            return True, f"已解除安裝 {len(pkgs)} 個包"
        except Exception as e:  # noqa: BLE001
            return False, f"解除安裝失敗: {e}"

    return False, f"runtime={runtime} 無需解除安裝"


def is_builtin(name: str) -> bool:
    """判斷 name 是否為內置外掛 (不可被使用者編輯/刪除)。"""
    return (name or "").lower() in _PLUGIN_STATUS


def names() -> set[str]:
    return set(_PROVIDERS)


def errors() -> list[dict]:
    return list(_LOAD_ERRORS)


def get_provider(name: str) -> GenericHTTPProvider:
    provider = _PROVIDERS.get((name or "").lower())
    if provider is None:
        raise ValueError(f"Custom data source not found or invalid: {name}")
    return provider


def create_provider(config: dict) -> GenericHTTPProvider:
    """Build a validated temporary provider without writing or registering it."""
    parsed = config_from_dict(_sanitize_for_yaml(config))
    provider = GenericHTTPProvider(parsed)
    errors = provider.validate()
    if errors:
        provider.close()
        raise ValueError("; ".join(errors))
    return provider


def is_custom_provider(name: str) -> bool:
    return (name or "").lower() in _PROVIDERS


def provider_has_dataset(name: str, dataset: str) -> bool:
    """判斷某個 custom 源是否配置了指定資料集。

    用於主流程分流: 未配置的資料集不得嘗試呼叫不具備該能力的資料來源。
    """
    provider = _PROVIDERS.get((name or "").lower())
    if provider is None:
        return False
    return dataset in provider.config.datasets


def get_config_dict(name: str) -> dict | None:
    """讀取一個已載入 custom 源的原始配置 dict(用於前端編輯回填)。內置外掛不可編輯。"""
    if is_builtin(name):
        return None
    provider = _PROVIDERS.get((name or "").lower())
    if provider is None:
        return None
    return _config_to_dict(provider.config)


def _config_to_dict(config: CustomSourceConfig) -> dict:
    auth = config.auth
    out: dict = {
        "name": config.name,
        "display_name": config.display_name,
        "auth": {
            "type": auth.type,
            **({"token_env": auth.token_env} if auth.token_env else {}),
            **({"header": auth.header} if auth.type in {"bearer", "header"} and auth.header != "Authorization" else {}),
            **({"param": auth.param} if auth.type == "query" and auth.param != "token" else {}),
        },
        "datasets": {},
    }
    for ds_name, ds in config.datasets.items():
        out["datasets"][ds_name] = {
            "url": ds.url,
            "method": ds.method,
            **({"batch": ds.batch} if ds.batch is not None else {}),
            **({"rpm": ds.rpm} if ds.rpm is not None else {}),
            **({"timeout": ds.timeout} if ds.timeout != DEFAULT_TIMEOUT else {}),
            "response_path": ds.response_path,
            "field_map": dict(ds.field_map),
            **({"transforms": dict(ds.transforms)} if ds.transforms else {}),
            **({
                "symbols_param": ds.symbols_param,
                "start_param": ds.start_param,
                "end_param": ds.end_param,
            } if ds_name != "realtime" else {}),
            **({"asset_type_param": ds.asset_type_param} if ds_name == "minute" and ds.asset_type_param else {}),
            **({"freq_param": ds.freq_param} if ds_name == "minute" and ds.freq_param else {}),
        }
    return out


def save_config(name: str, config: dict) -> Path:
    """把一份配置 dict 寫成 data/data_sources/{name}.yaml, 返回寫入路徑。"""
    if is_builtin(name):
        raise ValueError(f"'{name}' 是內置外掛, 不可編輯")
    if not _NAME_RE.match(name or ""):
        raise ValueError(f"invalid data source name: {name!r} (only lowercase a-z 0-9 _ allowed)")
    base = data_sources_dir()
    base.mkdir(parents=True, exist_ok=True)
    path = (base / f"{name}.yaml").resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError("invalid data source name: path escape detected")
    cleaned = _sanitize_for_yaml(config)
    path.write_text(yaml.safe_dump(cleaned, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def delete_config(name: str) -> bool:
    """刪除 data/data_sources/{name}.yaml。返回是否真的刪除了。"""
    if is_builtin(name):
        raise ValueError(f"'{name}' 是內置外掛, 不可刪除")
    if not _NAME_RE.match(name or ""):
        raise ValueError(f"invalid data source name: {name!r}")
    base = data_sources_dir().resolve()
    path = (base / f"{name}.yaml").resolve()
    if not path.is_relative_to(base):
        raise ValueError("invalid data source name: path escape detected")
    if not path.exists():
        return False
    path.unlink()
    return True


def _sanitize_for_yaml(config: dict) -> dict:
    """剔除前端可能塞進來的空值/未啟用資料集, 保證寫入的 yaml 乾淨。"""
    out: dict = {
        "name": str(config.get("name", "")).lower(),
        "display_name": str(config.get("display_name") or config.get("name", "")),
    }
    auth_raw = config.get("auth") or {}
    auth_type = str(auth_raw.get("type", "none") or "none").lower()
    auth: dict = {"type": auth_type}
    if auth_type != "none" and auth_raw.get("token_env"):
        auth["token_env"] = str(auth_raw["token_env"])
        if auth_type in {"bearer", "header"} and auth_raw.get("header"):
            auth["header"] = str(auth_raw["header"])
        if auth_type == "query" and auth_raw.get("param"):
            auth["param"] = str(auth_raw["param"])
    out["auth"] = auth

    datasets_out: dict = {}
    for ds_name, ds_cfg in (config.get("datasets") or {}).items():
        if ds_name not in {"daily", "adj_factor", "realtime", "minute", "financial"}:
            continue
        if not isinstance(ds_cfg, dict):
            continue
        ds = _sanitize_dataset(ds_name, ds_cfg)
        if ds:
            datasets_out[ds_name] = ds
    out["datasets"] = datasets_out
    return out


def _sanitize_dataset(ds_name: str, ds_cfg: dict) -> dict:
    out: dict = {}
    url = str(ds_cfg.get("url", "") or "").strip()
    if not url:
        return out
    out["url"] = url
    method = str(ds_cfg.get("method", "GET") or "GET").upper()
    out["method"] = method
    if ds_cfg.get("batch") is not None:
        try:
            out["batch"] = int(ds_cfg["batch"])
        except (TypeError, ValueError):
            pass
    if ds_cfg.get("rpm") is not None:
        try:
            out["rpm"] = int(ds_cfg["rpm"])
        except (TypeError, ValueError):
            pass
    if ds_cfg.get("timeout") is not None:
        try:
            timeout = float(ds_cfg["timeout"])
        except (TypeError, ValueError) as e:
            raise ValueError(
                f"{ds_name}: timeout must be a number between 0 and "
                f"{MAX_TIMEOUT:g} seconds"
            ) from e
        if not math.isfinite(timeout) or not 0 < timeout <= MAX_TIMEOUT:
            raise ValueError(
                f"{ds_name}: timeout must be between 0 and {MAX_TIMEOUT:g} seconds"
            )
        if timeout != DEFAULT_TIMEOUT:
            out["timeout"] = timeout
    out["response_path"] = str(ds_cfg.get("response_path", "") or "")
    field_map = {
        str(k): str(v)
        for k, v in (ds_cfg.get("field_map") or {}).items()
        if str(k).strip() and str(v).strip()
    }
    if field_map:
        out["field_map"] = field_map
    transforms = {
        str(k): str(v)
        for k, v in (ds_cfg.get("transforms") or {}).items()
        if str(k).strip() and str(v).strip()
    }
    if transforms:
        out["transforms"] = transforms
    if ds_name != "realtime":
        symbols_param = str(ds_cfg.get("symbols_param") or "").strip()
        start_param = str(ds_cfg.get("start_param") or "").strip()
        end_param = str(ds_cfg.get("end_param") or "").strip()
        if symbols_param:
            out["symbols_param"] = symbols_param
        if start_param:
            out["start_param"] = start_param
        if end_param:
            out["end_param"] = end_param
    if ds_name == "minute":
        asset_type_param = str(ds_cfg.get("asset_type_param") or "").strip()
        freq_param = str(ds_cfg.get("freq_param") or "").strip()
        if asset_type_param:
            out["asset_type_param"] = asset_type_param
        if freq_param:
            out["freq_param"] = freq_param
    request_params = [
        out.get("symbols_param", "symbols"),
        out.get("start_param", "start_time"),
        out.get("end_param", "end_time"),
    ]
    if ds_name == "minute":
        request_params.extend(
            name for name in (out.get("asset_type_param"), out.get("freq_param")) if name
        )
    duplicates = sorted({
        name for name in request_params if request_params.count(name) > 1
    })
    if ds_name != "realtime" and duplicates:
        raise ValueError(
            f"{ds_name}: duplicate request parameter names: {', '.join(duplicates)}"
        )
    return out


# ================================================================
# 內置可選外掛 (plugins/ 目錄) 的發現與註冊
# ================================================================

def _load_builtin_plugins() -> None:
    """掃描 plugins/ 目錄下每個含 plugin.yaml 的子目錄, 動態載入。

    缺依賴時記錄 "不可用" 狀態, 不拋異常, 不影響主流程。
    每次呼叫重建 _PLUGIN_STATUS, 並把可用的外掛註冊進 _PROVIDERS。
    """
    global _PLUGIN_STATUS
    _PLUGIN_STATUS = {}
    pdir = plugins_dir()
    if not pdir.exists():
        return
    for plugin_dir in sorted(pdir.iterdir()):
        if not plugin_dir.is_dir():
            continue
        manifest_path = plugin_dir / "plugin.yaml"
        if not manifest_path.exists():
            continue
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            _register_one_plugin(manifest)
        except Exception as e:  # noqa: BLE001
            logger.warning("外掛 %s 清單解析失敗: %s", plugin_dir.name, e)


def _register_one_plugin(manifest: dict) -> None:
    """註冊單個外掛: 委託自檢 → 可用則動態 import entry 註冊進 _PROVIDERS。"""
    name = manifest.get("name")
    if not name or not _NAME_RE.match(name):
        logger.warning("外掛清單缺少合法 name: %r", name)
        return
    # hidden: 已載入但對 UI 隱藏 (功能未完成/暫不開放), 不註冊不展示
    if manifest.get("hidden"):
        logger.info("外掛 %s 標記為 hidden, 跳過註冊", name)
        return
    runtime = str(manifest.get("runtime", "none")).lower()
    # 委託檢測: 呼叫外掛自己的 check 函式 (node 型/python 型各自實現)
    available, reason = _call_check(manifest.get("check"))
    _PLUGIN_STATUS[name] = {
        "name": name,
        "display_name": manifest.get("display_name", name),
        "datasets": list(manifest.get("datasets", []) or []),
        "runtime": runtime,
        "available": available,
        "status": reason,
        "description": manifest.get("description", ""),
        "install_hint": manifest.get("install_hint", ""),
        "api_key_env": manifest.get("api_key_env", ""),
    }
    if not available:
        return  # 依賴沒裝: 不註冊, 但狀態已記錄供 UI 顯示
    # 可用 → 動態載入 provider 類並實例化
    try:
        provider_cls = _load_entry(manifest["entry"])
        provider = provider_cls() if isinstance(provider_cls, type) else provider_cls
        provider.builtin = True  # 標記為內置 (list_sources 過濾, 不可被使用者編輯/刪除)
        _PROVIDERS[name] = provider
        logger.info("內置外掛 %s 已註冊 (runtime=%s)", name, runtime)
    except Exception as e:  # noqa: BLE001
        # 聲稱可用但 import 失敗 → 標記不可用, 避免啟動崩潰
        _PLUGIN_STATUS[name]["available"] = False
        _PLUGIN_STATUS[name]["status"] = f"載入失敗: {e}"
        logger.warning("外掛 %s provider 載入失敗: %s", name, e)


def _call_check(check_ref: str | None) -> tuple[bool, str]:
    """呼叫外掛清單裡指定的可用性檢測函式, 返回 (是否可用, 原因)。

    check_ref 格式 "module.path:func_name"。無 check 字段時視為可用。
    """
    if not check_ref:
        return True, "ok"
    try:
        func = _load_entry(check_ref)
        result = func()
        # 相容兩種返回: (bool, str) 或 bool
        if isinstance(result, tuple):
            return bool(result[0]), str(result[1])
        return bool(result), "ok" if result else "不可用"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def _load_entry(entry_ref: str):
    """動態載入 'module.path:attr' 形式的引用, 返回屬性對象 (類或函式)。"""
    if ":" not in entry_ref:
        raise ValueError(f"entry 格式應為 'module.path:attr', 得到: {entry_ref!r}")
    module_path, attr = entry_ref.split(":", 1)
    module = importlib.import_module(module_path)
    return getattr(module, attr)


# 模組匯入時即掃描一次, 保證 names()/_allowed_data_providers() 在 startup 前可用。
_load_builtin_plugins()

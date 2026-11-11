"""Load custom data source definitions from user data files."""
from __future__ import annotations

import contextlib
import logging
import math
import re
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

_NAME_RE = re.compile(r"^[a-z0-9_]+$")


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
        except Exception as e:
            logger.warning("custom data source load failed %s: %s", file, e)
            _LOAD_ERRORS.append({"path": str(file), "errors": [str(e)]})


def list_sources() -> list[dict]:
    """列出使用者自訂 (YAML) 源。"""
    return [
        {
            "name": provider.name,
            "display_name": provider.config.display_name,
            "datasets": sorted(provider.config.datasets.keys()),
            "path": str(provider.config.path) if provider.config.path else None,
        }
        for provider in _PROVIDERS.values()
    ]


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
    """判斷某個 custom 源是否設定了指定資料集。

    用於主流程分流: 未設定的資料集不得嘗試呼叫不具備該能力的資料來源。
    """
    provider = _PROVIDERS.get((name or "").lower())
    if provider is None:
        return False
    return dataset in provider.config.datasets


def get_config_dict(name: str) -> dict | None:
    """讀取一個已載入 custom 源的原始設定 dict(用於前端編輯回填)。"""
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
    """把一份設定 dict 寫成 data/data_sources/{name}.yaml, 回傳寫入路徑。"""
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
    """刪除 data/data_sources/{name}.yaml。回傳是否真的刪除了。"""
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
        with contextlib.suppress(TypeError, ValueError):
            out["batch"] = int(ds_cfg["batch"])
    if ds_cfg.get("rpm") is not None:
        with contextlib.suppress(TypeError, ValueError):
            out["rpm"] = int(ds_cfg["rpm"])
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

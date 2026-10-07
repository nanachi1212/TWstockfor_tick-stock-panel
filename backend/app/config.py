"""全局配置 — 从环境变量 / .env 读取。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from platformdirs import user_data_path
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ── 运行环境检测 ──────────────────────────────────────────
# PyInstaller 打包后: __file__ 指向临时解压目录 _MEIPASS, 不能作为路径基准。
# 此时:
#   - 只读资源 (tiers.yaml / 前端 dist) 放在 _MEIPASS 内
#   - 可写用户数据 (data_dir) 放在可执行文件旁的用户目录
# 非 frozen 模式 (开发/Docker): 保持原有 __file__ 推导, 行为完全不变。
_IS_FROZEN = getattr(sys, "frozen", False)


def _user_data_root() -> Path:
    """桌面版用户数据根目录。

    定位策略 (按优先级):
      1. 环境变量 DATA_DIR (pydantic-settings 自动注入到 settings.data_dir, 不在此处理)
      2. 打包桌面版: %LOCALAPPDATA%/NanachiStockPanel/data
         - 与程序安装目录隔离, 升级与卸载默认不触碰使用者数据。
      3. 非 frozen (开发模式): 项目根 data/

    旧版本安装目录内的 data/ 会由 DataStore._migrate_legacy_data_dir() 保守复制到
    新位置; 来源保留, 不会因为迁移失败或升级安装而遗失。
    """
    # 打包桌面版: 固定使用 per-user 可写目录, 绝不把 mutable data 放安装目录。
    if _IS_FROZEN:
        return user_data_path("NanachiStockPanel", appauthor=False) / "data"

    # 开发模式: 项目根 data/
    return _PROJECT_ROOT / "data"


def _resource_root() -> Path:
    """只读资源根目录。

    frozen: PyInstaller 解压目录 (_MEIPASS)
    非 frozen: 项目根目录 (源码树)
    """
    if _IS_FROZEN:
        # sys._MEIPASS 是 PyInstaller 注入的解压根
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent.parent


def _project_root() -> Path:
    """项目根目录 (非 frozen 用)。"""
    return Path(__file__).resolve().parent.parent.parent


_PROJECT_ROOT = _project_root()
_RESOURCE_ROOT = _resource_root()
_ENV_FILE = Path(
    os.environ.get(
        "TICKFLOW_ENV_FILE",
        str(_RESOURCE_ROOT / ".env") if not _IS_FROZEN else ".env",
    )
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # TickFlow
    tickflow_api_key: str = Field(default="", description="留空启用 free 模式")

    # Optional intraday provider. Fugle never replaces official/PIT data sources.
    fugle_api_key: str = Field(default="", repr=False)
    fred_api_key: str = Field(default="", repr=False)
    finbridge_api_key: str = Field(default="", repr=False)

    # AI
    ai_provider: str = "openai_compat"
    ai_base_url: str = "https://api.zhaji.dev/v1"
    ai_api_key: str = ""
    ai_model: str = "gpt-5.5"
    ai_codex_command: str = "codex"
    ai_codex_reasoning_effort: str = ""
    # 默认浏览器风格 UA,绕过 Cloudflare 等 CDN/WAF 的 Bot 拦截(Issue #8)。
    # 用户可在 AI 设置页按需修改。
    ai_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
    # AI 输出上限 (max_tokens) 与输入上下文窗口上限 (约 token)。
    # 任务级 max_tokens 会被钳制到 ai_max_output_tokens; 输入估算超出上下文窗口时给出明确报错。
    # 默认 8192 高于所有现有任务 (最多 4500), 避免默认配置反而截断长报告; 可在 AI 设置里调整。
    ai_max_output_tokens: int = 8192
    ai_context_window: int = 64000
    # 單次 AI 請求逾時秒數；本機小顯卡較慢時調大 (環境變數 AI_REQUEST_TIMEOUT 或 secrets.json 的 ai_request_timeout)。
    ai_request_timeout: float = 55.0

    # Server
    host: str = "0.0.0.0"
    port: int = 3018
    log_level: str = "INFO"
    backtest_range_guard: bool = False
    backtest_matrix_disk_cache_enabled: bool = True
    backtest_matrix_cache_max_mb: int = 512
    backtest_matrix_cache_prewarm: bool = True
    backtest_matrix_cache_prewarm_years: int = 5

    # Auth — 首次启动时预置访问密码(明文, 仅用于初始化, 详见 services/auth.bootstrap_from_env)
    # 公网服务器部署时免去 SSH 端口转发设密码的麻烦。写入 auth.json(哈希)后即不再读取。
    auth_password: str = ""

    # Data — frozen: exe 同级 data/ 子目录; 非 frozen: 项目根 data/
    # (均可被环境变量 DATA_DIR 覆盖, pydantic-settings 自动注入)
    data_dir: Path = _user_data_root()

    # tiers.yaml 路径 — frozen: 资源目录内; 非 frozen: 项目根目录
    tiers_yaml: Path = _RESOURCE_ROOT / "tiers.yaml" if _IS_FROZEN else _PROJECT_ROOT / "tiers.yaml"

    # 静态文件(前端 dist) — frozen: 资源目录的 static/; 非 frozen: frontend/dist
    static_dir: Path = (
        _RESOURCE_ROOT / "static"
        if _IS_FROZEN
        else (_PROJECT_ROOT / "frontend" / "dist")
    )

    # 桌面 release seed - frozen: PyInstaller 只讀資源; 開發模式指向本機 build 產物。
    release_seed_bundle: Path = (
        _RESOURCE_ROOT / "release_seed" / "release-seed.zip"
        if _IS_FROZEN
        else _PROJECT_ROOT / "release-assets" / "release-seed" / "release-seed.zip"
    )

    @model_validator(mode="after")
    def _resolve_paths(self) -> Settings:
        """确保 data_dir 是绝对路径（环境变量传入的相对路径基于项目根目录解析）。"""
        if not self.data_dir.is_absolute():
            # 相对路径基于项目根目录解析，而非 CWD
            self.data_dir = (_PROJECT_ROOT / self.data_dir).resolve()
        if self.backtest_matrix_cache_max_mb <= 0:
            raise ValueError("backtest_matrix_cache_max_mb must be positive")
        if self.backtest_matrix_cache_prewarm_years <= 0:
            raise ValueError("backtest_matrix_cache_prewarm_years must be positive")
        if self.ai_max_output_tokens <= 0:
            raise ValueError("ai_max_output_tokens must be positive")
        if self.ai_request_timeout <= 0:
            raise ValueError("ai_request_timeout must be positive")
        if self.ai_context_window <= 0:
            raise ValueError("ai_context_window must be positive")
        return self

    @property
    def use_free_mode(self) -> bool:
        """是否走 Free 模式。优先看 secrets.json,其次看 .env。"""
        from app import secrets_store
        return not secrets_store.get_tickflow_key()


settings = Settings()

"""全域配置 — 從環境變數 / .env 讀取。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from platformdirs import user_data_path
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ── 執行環境檢測 ──────────────────────────────────────────
# PyInstaller 打包後: __file__ 指向臨時解壓目錄 _MEIPASS, 不能作為路徑基準。
# 此時:
#   - 只讀資源 (tiers.yaml / 前端 dist) 放在 _MEIPASS 內
#   - 可寫使用者資料 (data_dir) 放在可執行文件旁的使用者目錄
# 非 frozen 模式 (開發/Docker): 保持原有 __file__ 推導, 行為完全不變。
_IS_FROZEN = getattr(sys, "frozen", False)


def _user_data_root() -> Path:
    """桌面版使用者資料根目錄。

    定位策略 (按優先級):
      1. 環境變數 DATA_DIR (pydantic-settings 自動注入到 settings.data_dir, 不在此處理)
      2. 打包桌面版: %LOCALAPPDATA%/NanachiStockPanel/data
         - 與程序安裝目錄隔離, 升級與解除安裝預設不觸碰使用者資料。
      3. 非 frozen (開發模式): 項目根 data/

    舊版本安裝目錄內的 data/ 會由 DataStore._migrate_legacy_data_dir() 保守複製到
    新位置; 來源保留, 不會因為遷移失敗或升級安裝而遺失。
    """
    # 打包桌面版: 固定使用 per-user 可寫目錄, 絕不把 mutable data 放安裝目錄。
    if _IS_FROZEN:
        return user_data_path("NanachiStockPanel", appauthor=False) / "data"

    # 開發模式: 項目根 data/
    return _PROJECT_ROOT / "data"


def _resource_root() -> Path:
    """只讀資源根目錄。

    frozen: PyInstaller 解壓目錄 (_MEIPASS)
    非 frozen: 項目根目錄 (源碼樹)
    """
    if _IS_FROZEN:
        # sys._MEIPASS 是 PyInstaller 注入的解壓根
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent.parent


def _project_root() -> Path:
    """項目根目錄 (非 frozen 用)。"""
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

    # Optional intraday provider. Fugle never replaces official/PIT data sources.
    fugle_api_key: str = Field(default="", repr=False)
    fred_api_key: str = Field(default="", repr=False)
    finbridge_api_key: str = Field(default="", repr=False)

    # AI
    ai_provider: str = "openai_compat"
    ai_base_url: str = "http://127.0.0.1:8080/v1"
    ai_api_key: str = ""
    ai_model: str = "gpt-5.5"
    ai_codex_command: str = "codex"
    ai_codex_reasoning_effort: str = ""
    # 預設瀏覽器風格 UA,繞過 Cloudflare 等 CDN/WAF 的 Bot 攔截(Issue #8)。
    # 使用者可在 AI 設定頁按需修改。
    ai_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
    # AI 輸出上限 (max_tokens) 與輸入上下文窗口上限 (約 token)。
    # 任務級 max_tokens 會被鉗制到 ai_max_output_tokens; 輸入估算超出上下文窗口時給出明確報錯。
    # 預設 8192 高於所有現有任務 (最多 4500), 避免預設配置反而截斷長報告; 可在 AI 設定裡調整。
    ai_max_output_tokens: int = 8192
    ai_context_window: int = 32768
    # 單次 AI 請求逾時秒數；本機小顯卡較慢時調大 (環境變數 AI_REQUEST_TIMEOUT 或 secrets.json 的 ai_request_timeout)。
    ai_request_timeout: float = 55.0

    # Server
    host: str = "0.0.0.0"
    port: int = 3018
    log_level: str = "INFO"

    # Auth — 首次啟動時預置訪問密碼(明文, 僅用於初始化, 詳見 services/auth.bootstrap_from_env)
    # 公網伺服器部署時免去 SSH 端口轉發設密碼的麻煩。寫入 auth.json(哈希)後即不再讀取。
    auth_password: str = ""

    # Data — frozen: exe 同級 data/ 子目錄; 非 frozen: 項目根 data/
    # (均可被環境變數 DATA_DIR 覆蓋, pydantic-settings 自動注入)
    data_dir: Path = _user_data_root()


    # 靜態文件(前端 dist) — frozen: 資源目錄的 static/; 非 frozen: frontend/dist
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
        """確保 data_dir 是絕對路徑（環境變數傳入的相對路徑基於項目根目錄解析）。"""
        if not self.data_dir.is_absolute():
            # 相對路徑基於項目根目錄解析，而非 CWD
            self.data_dir = (_PROJECT_ROOT / self.data_dir).resolve()
        if self.ai_max_output_tokens <= 0:
            raise ValueError("ai_max_output_tokens must be positive")
        if self.ai_request_timeout <= 0:
            raise ValueError("ai_request_timeout must be positive")
        if self.ai_context_window <= 0:
            raise ValueError("ai_context_window must be positive")
        return self



settings = Settings()

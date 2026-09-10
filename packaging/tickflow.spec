# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 — 桌面客戶端 (onedir 模式)。

為什麼 onedir 而非 onefile:
  - onefile 每次啟動都解壓到臨時 _MEIxxxxx, 與 APScheduler/多線程衝突
  - onedir 啟動更快, 調試更方便 (可看到目錄結構), 原生庫直接在目錄裡
  - 體積差異通過壓縮安裝包彌補 (CI 裡 zip 打包)

入口: backend/app/desktop.py (桌面版入口, 含 uvicorn + pywebview)

構建 (在項目根目錄):
  cd frontend && pnpm build                     # 先構建前端到 frontend/dist
  pyinstaller packaging/tickflow.spec           # 產物在 dist/NanachiStockPanel/
"""
import sys
from importlib.util import find_spec
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all,
    collect_submodules,
    collect_data_files,
    copy_metadata,
)

_IS_MACOS = sys.platform == "darwin"

block_cipher = None

# ── 資源路徑基準: 項目根 (spec 文件在 packaging/ 下) ──────────────────
ROOT = Path(SPECPATH).parent
FRONTEND_DIST = str(ROOT / "frontend" / "dist")
TIERS_YAML = str(ROOT / "tiers.yaml")
VERSION_FILE = str(ROOT / "VERSION")
BUILTIN_STRATEGIES = str(ROOT / "backend" / "app" / "strategy" / "builtin")
# 圖標按平台選: Windows 用 .ico, macOS 用 .icns (PyInstaller 對 .ico 在
# mac 上靜默忽略, 不換格式 Dock/Finder 會顯示通用圖標)。兩者都由
# packaging/generate_icon.py 一併生成。
APP_ICON = str(ROOT / "packaging" / ("icon.icns" if _IS_MACOS else "icon.ico"))

# ── 收集帶原生庫的依賴 (.libs/ 目錄必須完整, 否則啟動崩) ─────────────
# polars / pyarrow / duckdb / fastexcel 都自帶共享庫子目錄
datas = []
binaries = []
hiddenimports = []

for pkg in ("polars", "pyarrow", "duckdb", "fastexcel"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Polars 的發行包名為 polars-runtime-32 / polars-runtime-compat, 但實際
# Python 導入包帶前導下劃線。release.yml 安裝 legacy-cpu 後必須收集二者，
# 否則 onedir 產物無法在沒有 AVX2/FMA 的舊 CPU 上加載兼容內核。
for pkg in ("_polars_runtime_32", "_polars_runtime_compat"):
    if find_spec(pkg) is not None:
        rt_d, rt_b, rt_h = collect_all(pkg)
        datas += rt_d
        binaries += rt_b
        hiddenimports += rt_h

# Polars 新 ABI 運行時由加載器選擇，需顯式收集子模塊。
hiddenimports += collect_submodules("polars")

# ── pywebview 平台後端 (動態導入, PyInstaller 默認抓不到) ────────────
hiddenimports += collect_submodules("webview")
hiddenimports += collect_submodules("webview.platforms")

# ── 系統通知後端 (winotify/plyer 按平台動態導入) ─────────────────────
if sys.platform == "win32":
    hiddenimports += collect_submodules("winotify")
hiddenimports += collect_submodules("plyer")
hiddenimports += collect_submodules("plyer.platforms")

# ── uvicorn 動態導入的模塊 (loop/protocol/logging 按字符串加載) ──────
hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
]

# ── fastapi / pydantic 元數據 (版本檢測用) ───────────────────────────
# 注意: 任何用 importlib.metadata.version() 讀版本的包, 都必須 copy_metadata,
# 否則 frozen 後報 PackageNotFoundError。tickflow 包內部就是這麼讀的。
# 用容錯寫法: 不存在的包跳過, 避免不同環境 (有無裝某依賴) 導致構建失敗。
def _safe_metadata(pkg):
    """收集包元數據, 包不存在時靜默跳過。"""
    try:
        return copy_metadata(pkg)
    except Exception:
        return []

for pkg in (
    "fastapi", "pydantic", "pydantic_settings", "starlette", "anyio",
    "tickflow",  # tickflow/__version__.py 用 importlib.metadata 讀版本
    "uvicorn", "polars", "duckdb", "pyarrow", "httpx", "numpy", "pandas",
    "openai", "platformdirs", "winotify", "plyer", "apscheduler",
    "python-dotenv", "fastexcel",
):
    datas += _safe_metadata(pkg)

# ── 隨包資源 (只讀, 放進 _MEIPASS) ────────────────────────────────────
# 前端 dist → static/ (config.py frozen 模式讀 _MEIPASS/static)
datas += [(FRONTEND_DIST, "static")]
# tiers.yaml → 包根 (config.py frozen 模式讀 _MEIPASS/tiers.yaml)
datas += [(TIERS_YAML, ".")]
# VERSION → 包根 (app.__version__ 與 UI 的唯一版本來源)
datas += [(VERSION_FILE, ".")]
# 內置策略 → app/strategy/builtin/ (importlib 動態加載, 不能進 PYZ)
datas += [(BUILTIN_STRATEGIES, "app/strategy/builtin")]

# ── 排除不需要的重型依賴 (主包不含 vectorbt 回測鏈) ──────────────────
excludes = [
    "vectorbt",
    "numba",
    "llvmlite",
    "matplotlib",
    "plotly",
    "ipywidgets",
    "nbformat",
    "nbconvert",
    "jupyter",
    "IPython",
    "pytest",
    "pytest_asyncio",
    "ruff",
    "mypy",
]

a = Analysis(
    [str(ROOT / "backend" / "app" / "desktop.py")],
    pathex=[str(ROOT / "backend")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NanachiStockPanel",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # UPX 壓縮原生庫常導致崩潰, 關閉
    console=False,       # 桌面應用: 不顯示控制檯窗口 (_guard_streams 守護 stdout/stderr)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=APP_ICON,      # 應用圖標 (與 favicon/logo 一致)
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="NanachiStockPanel",
)

# ── macOS: 封裝成 .app 包 ────────────────────────────────────────────
# Windows/Linux: 上面 COLLECT 產出的 onedir 目錄即是最終產物。
# macOS: 額外加 BUNDLE, 把目錄包裝成標準 .app (Contents/MacOS/...),
# 這樣 Dock/Finder 能識別, 用戶可雙擊啟動, 並顯示自定義 .icns 圖標。
# BUNDLE 必須引用上面的 COLLECT (coll), 它會把 coll 的產物搬進 .app。
#
# PyInstaller 6 BUNDLE 關鍵參數 (見 building/osx.py):
#   - version:            → CFBundleShortVersionString (默認 0.0.0, 必須顯式傳)
#   - info_plist (單數!):  用戶自定義鍵, update 合併進默認 plist, 可覆蓋任意字段
if _IS_MACOS:
    APP_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip().removeprefix("v")

    app = BUNDLE(
        coll,
        name="NanachiStockPanel.app",
        icon=APP_ICON,
        bundle_identifier="com.nanachi.stockpanel",
        version=APP_VERSION,   # → CFBundleShortVersionString / CFBundleVersion
        info_plist={
            "CFBundleName": "Nanachi 的台股監控看板",
            "CFBundleDisplayName": "Nanachi 的台股監控看板",
            "CFBundleVersion": APP_VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "10.13",
        },
    )

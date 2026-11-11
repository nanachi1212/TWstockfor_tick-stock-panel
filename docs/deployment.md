# 部署指南

本專案的幾種執行方式，按推薦程度排序。設定項詳解見 [configuration.md](./configuration.md)。

> 📌 前置需求:Python ≥ 3.11 · Node ≥ 20 · [`uv`](https://docs.astral.sh/uv/) · `pnpm`（`npm i -g pnpm`）

---

## 方式 A:Dev 模式(二次開發推薦)

推薦用開發模式執行,可隨時 `git pull` 同步最新程式碼。

```bash
git clone https://github.com/nanachi1212/TWstockfor_tick-stock-panel.git
cd TWstockfor_tick-stock-panel
cp .env.example .env       # 日常使用不需要填任何金鑰
./dev.sh                   # Windows: .\dev.ps1
```

`dev.sh` 自動檢查 / 下載相依套件、釋放連接埠、同時起前後端,Ctrl-C 一併關閉。預設:

- 後端 → <http://localhost:3018> · 前端 → <http://localhost:3011>
- 自訂連接埠:`BACKEND_PORT=8000 FRONTEND_PORT=5173 ./dev.sh`

### 手動分別啟動(不想用 dev.sh)

```bash
# 後端
cd backend && uv sync
# 舊 CPU: uv sync --extra legacy-cpu
uv run uvicorn app.main:app --reload --port 3018

# 前端
cd frontend && pnpm install && pnpm dev   # http://localhost:3011
```

---

## 方式 B:Docker(部署最省心)

```bash
cp .env.example .env
docker compose up --build
# 開啟 http://localhost:3018
```

Docker 採用兩階段建置,前端 dist 複製進後端映像檔,**單容器**執行,資料掛載在本機的 `data/` 目錄,完全在自己手裡。

更新到新版本:

```bash
git pull
docker compose up --build -d
```

---

## 舊 CPU 相容(avx2/fma 缺失)

如果執行階段報 `avx2`/`fma` 缺失,或程序 `exit 132`,說明 CPU 不支援 AVX2 指令集(常見於舊主機)。解決:

- **Dev 原始碼啟動**:在根目錄 `.env` 設定後執行 `./dev.sh` 或 Windows 的 `.\dev.ps1`;即使已有 `.venv`,啟動器也會同步相容核心
- **Docker**:在根目錄 `.env` 設定後執行 `docker compose up --build`

```ini
BACKEND_EXTRAS=legacy-cpu          # 相容舊 CPU
```

手動啟動原始碼時，也可以在 `backend/` 目錄直接執行 `uv sync --extra legacy-cpu`。不要設定 `POLARS_SKIP_CPU_CHECK`，它只會隱藏警告，實際執行不支援的指令時仍可能崩潰。

---

## 更新程式碼(已部署使用者必讀)

取得新版本只需一行指令:

```bash
git pull
```

**整個 `data/` 目錄都不納入 git** —— 行情日K、三大法人、融資融券、基本面、自選清單與分組、監控規則與觸發記錄,全部是程式執行時產生或拉取的使用者資料,`git pull` 物理上無法影響它們。新使用者首次啟動後,請到面板 **設定 → 資料來源 → 台股歷史日 K 資料庫** 下載歷史資料包(GitHub Release `data-daily-2026-11-02`, 範圍 2024-01-02 ～ 2026-11-02),之後的日常更新由排程自動處理。

> ⚠️ **切勿使用以下指令「解決衝突」或「清理」,它們會一次性刪光 `data/` 下所有未被 git 追蹤的資料:**
> - `git clean -fdx`(最危險,會刪掉所有 `.gitignore` 忽略的檔案)
> - `git reset --hard`
> - 直接刪除整個專案資料夾後重新 `git clone`
>
> 若 `git pull` 報衝突,通常是本機誤改了被追蹤的檔案,請先 `git stash` 暫存再 pull,或到 GitHub 開 Issue 詢問,不要直接執行上面的指令。

---

## 存取密碼設定(公開網路部署必讀)

面板部署在公開網路伺服器時,首次設定存取密碼必須從本機或內網操作(用 `AUTH_PASSWORD` 環境變數預先設定,或用 SSH 連接埠轉發)。完整步驟、重置密碼與原理說明見 [deploy-password.md](./deploy-password.md)。

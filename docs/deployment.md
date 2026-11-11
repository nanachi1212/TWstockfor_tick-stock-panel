# 部署指南

本專案的幾種執行方式，按推薦程度排序。設定項詳解見 [configuration.md](./configuration.md)。

> 📌 前置依賴:Python ≥ 3.11 · Node ≥ 20 · [`uv`](https://docs.astral.sh/uv/) · `pnpm`（`npm i -g pnpm`）

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

面板部署在公開網路伺服器時,首次設定存取密碼有限制 —— **必須從本機或內網存取**,以防公開網路上陌生人搶先設定密碼鎖死你的面板。

如果你在公開網路瀏覽器直接開啟頁面,會看到提示:

> 首次設定密碼僅允許本機或內網存取,請透過 SSH/本機瀏覽器操作

有兩種方式解決,任選其一。

### 方式一:環境變數預先設定密碼(最簡單,推薦)

在 `.env` 檔案(或 Docker / 系統環境變數)裡設定 `AUTH_PASSWORD`:

```bash
AUTH_PASSWORD='你的密碼'
```

然後重新啟動服務。啟動時會自動:

1. 讀取 `AUTH_PASSWORD`
2. 用 PBKDF2 雜湊後寫入 `auth.json`(`chmod 600`,只存雜湊不存明文)
3. **之後這個環境變數就不再被讀取** —— 是一次性的初始化

設完後即可用公開網路位址 + 這個密碼正常登入。後續改密碼請用頁面 UI(`設定 → 修改密碼`),不受環境變數影響。

**注意事項:**

- **密碼至少 6 位**,否則會被跳過並記一條 warning 記錄檔
- **僅在未設過密碼時生效**。已設過密碼後,改這裡不會覆蓋(避免重新啟動時重置你在 UI 改的密碼)
- 密碼建議使用單引號包裹，避免 Docker Compose 插值 `$VAR`；啟動時也會從唯讀掛載的原始 `.env` 初始化，相容已有的未加引號設定
- `.env` 檔案權限保持 `600`,**不要提交到 Git**
- 明文密碼只存在於 `.env` / 環境變數中,落盤的是雜湊,安全性等同 `auth.json`

**重置密碼(忘密碼時):** 停止服務、備份並移開 `data/user_data/auth.json` 後重新啟動，會回到「未設密碼」狀態，此時 `AUTH_PASSWORD` 會重新生效。空檔、損壞或無法讀取的認證檔會回傳 HTTP 503，請修復原檔，不要清空它。

```bash
mv -i data/user_data/auth.json data/user_data/auth.json.backup   # 停服後執行，不覆蓋已有備份
```

### 方式二:SSH 連接埠轉發

不用改設定,在你**自己電腦**的終端機執行(不是伺服器上):

```bash
ssh -L 3018:127.0.0.1:3018 使用者名稱@伺服器IP
```

例如伺服器是 `123.45.67.89`、使用者名稱 `root`、面板連接埠 `3018`:

```bash
ssh -L 3018:127.0.0.1:3018 root@123.45.67.89
```

保持這個 SSH 連接**不要關**,然後在**自己電腦的瀏覽器**開啟 `http://127.0.0.1:3018`。此時後端看到的用戶端 IP 是 `127.0.0.1`(本機),能通過驗證,正常顯示設定密碼介面。

**設完密碼後**,SSH 連接可以斷開 —— 密碼已存進伺服器,之後直接用公開網路位址 + 剛設的密碼存取即可。

> 如果用 `PORT` 改過連接埠(比如 `PORT=8080`),兩處都要替換:`ssh -L 8080:127.0.0.1:8080 root@IP`。

### 兩種方式怎麼選

| | 環境變數 | SSH 轉發 |
|---|---|---|
| 操作 | 改一行設定 + 重新啟動 | 一行 ssh 指令 |
| 需要改設定 | 是 | 否 |
| 適合 | Docker / 自動化部署 / 不熟 SSH | 臨時設密碼 / 能 SSH 到伺服器 |
| 後續改密碼 | UI(`設定 → 修改密碼`) | 同左 |

推薦**方式一(環境變數)**,一次設定即可,Docker 部署尤其方便。

### 原理說明

- **為什麼限制本機/內網?** 面板部署到公開網路後,任何人都能存取 URL。如果不限制,攻擊者可以在你之前開啟頁面、設定一個密碼,把你的面板鎖死。
- **本機/內網如何判斷?** 後端檢查用戶端 IP 是否屬於 `127.0.0.1 / ::1 / 10.x / 192.168.x / 172.16-31.x`。
- **SSH 轉發為什麼有效?** `-L` 把本機連接埠透過 SSH 通道轉發到伺服器的 `127.0.0.1`,等同於在伺服器本地存取,用戶端 IP 變成 `127.0.0.1`,通過驗證。
- **反向代理注意:** 若面板在 Nginx 等反向代理之後,需正確設定 `X-Forwarded-For` 標頭,後端據此取真實用戶端 IP。

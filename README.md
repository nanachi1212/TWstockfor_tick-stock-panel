
<div align="center">

# 📈 Nanachi 的台股監控看板

[![聲明:個人開源](https://img.shields.io/badge/⚠️_聲明-個人開源-green?style=for-the-badge&labelColor=red)](https://github.com/nanachi1212/TWstockfor_tick-stock-panel)



**自架、零維運的台股「看市場 + 選股 + 監控」面板**

**資料來自 TWSE / TPEx 公開資料,不需要任何付費行情 API Key**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/Python-≥3.11-blue.svg)](https://www.python.org/)
[![React](https://img.shields.io/badge/React-18-61dafb.svg)](https://react.dev/)
[![Deploy: Docker](https://img.shields.io/badge/Deploy-Docker-2496ed.svg)](./Dockerfile)

</div>

<div align="center">

**[快速開始](#-快速開始)** · **[核心功能](#-核心功能)** · **[資料來源](#-資料來源)** · **[設定](#️-設定)** · **[完整文件](#-完整文件)**

</div>


---

**本專案為個人開源專案,與台灣證券交易所、證券櫃檯買賣中心或任何商業資料服務均無官方關聯。僅供學習與研究使用。**

> ⚠️ 這是把公開資料整理成看得懂的畫面的工具,不是投資軟體。
>
> **明確不做**:不預測漲跌、不推薦個股、不做自動下單。AI 只在你主動點擊時才會呼叫。

---

## ✨ 核心功能

側邊欄的順序就是建議的使用流程:

```
看市場  →  找股票  →  加自選  →  多股比較  →  設定監控
 看板      台股選股    自選股     多股比較      監控中心
```

| 頁面 | 一句話 |
| :--- | :--- |
| 📊 **看板** | 今日市場強弱(漲跌家數與偏強/中性/偏弱)、產業強弱最強最弱、自選股動態、台股資料狀態、監控觸發記錄 |
| 🔍 **台股選股** | 全市場量化統計快照、產業類股輪動、10 類客觀異常訊號、5 組常用篩選、技術面與法人籌碼進階條件、AI 自然語言條件解析 |
| ⭐ **自選股** | 自選清單與多對多分組、表格/卡片雙檢視、自訂欄位、板塊與風險警示過濾、迷你日K與分時 |
| ⚖️ **多股比較** | 多檔並列的確定性比較表(報酬率、外資買賣超、本益比、異常訊號數),AI 客觀解讀為選配且需手動觸發 |
| 📈 **個股頁面** | 歷史日K與成交量、五檔即時盤口、三大法人買賣超、融資融券與券資比、近 5 日核心籌碼滾動因子、漲跌幅限制規則 |
| 🚨 **監控中心** | 盤中即時行情與五檔、8 種規則類型、冷卻時間與遲滯防抖、觸發記錄,命中可推播到 LINE 或 Telegram |

<details>
<summary><b>📦 更多細節</b></summary>

**選股頁的異常訊號**:爆量、成交額放大、價格異動、外資異常買賣超、投信異常買賣超、融資激增、融券激增、券資比驟升、價量/法人背離、產業相對強弱異常。全部是規則判定的事實描述,系統不據此給推薦。

**監控規則類型**:價格高於/低於、漲幅高於、跌幅低於、成交量高於、成交量異常放大、接近漲停、接近跌停。後兩者對無漲跌幅限制的商品不適用。

**AI 的兩個用途**:選股頁把中文條件翻譯成篩選欄位(純翻譯層,結果先預覽再填表,不直接出股)、多股比較頁對已呈現的數據做客觀解讀。兩者都要手動觸發,不設定 AI 完全不影響其他功能。

**推播**:LINE Messaging API 與 Telegram Bot,可各自測試,並勾選作為新建規則的預設推播管道。

</details>

---

## 🚀 快速開始

### 方式 A:桌面安裝包(最簡單)

到 [Releases](https://github.com/nanachi1212/TWstockfor_tick-stock-panel/releases) 下載對應平台的安裝檔:

| 平台 | 檔案 |
| :--- | :--- |
| Windows x64 | `NanachiStockPanel-Setup-x64.exe` |
| macOS(Apple Silicon) | `NanachiStockPanel-macos-arm64.dmg` |
| Linux | `NanachiStockPanel-linux-x64.tar.gz` |

Windows 不需要系統管理員權限,預設裝到 `D:\NanachiStockPanel`。使用者資料在安裝目錄下的 `data/`,覆蓋安裝不會遺失。

### 方式 B:Docker

```bash
cp .env.example .env
docker compose up --build
# 開啟 http://localhost:3018
```

### 方式 C:原始碼開發模式

> 前置依賴:Python ≥ 3.11 · Node ≥ 20 · [`uv`](https://docs.astral.sh/uv/) · `pnpm`(`npm i -g pnpm`)

```bash
cp .env.example .env
./dev.sh                   # Windows: .\dev.ps1
```

自動檢查與安裝依賴、釋放連接埠、同時起前後端。後端 → <http://localhost:3018> · 前端 → <http://localhost:3011>。

### 跑起來後的第一次使用

1. 面板要對外開放時,第一次會要求**設定存取密碼**。
2. 走完引導(使用須知 → 歡迎 → 台股資料狀態 → 完成),過程不需要填任何金鑰。
3. 到 **設定 → 資料來源 → 台股歷史日 K 資料庫**,下載歷史資料包(見下一節)。
4. 回到**看板**看今天的市場,到**台股選股**掃出候選,加進**自選股**,在**監控中心**建規則。

完整逐頁操作見 [操作說明書](./操作說明書.md)。

---

## 📡 資料來源

預設且建議的來源是**台灣官方資料源(TWSE / TPEx)**,整合台灣證券交易所與證券櫃檯買賣中心的公開資料,提供日K歷史行情、即時報價、標的清單與基本面資訊,**不需要任何 API Key**。Yahoo Finance 與公開資訊站台作為備援與補充。所有資料在本機建立 Parquet / DuckDB 快取。

在「設定 → 資料來源」可以逐個資料集指定提供方;沒有個別設定的一律由台灣官方資料源提供。

### 歷史日 K 資料包

即時報價隨時可抓,但歷史日 K 要先在本機建立。逐日回補太慢,所以整理好的資料包放在 GitHub Release:

| 項目 | 內容 |
| :--- | :--- |
| Release tag | `data-daily-2026-09-10` |
| 資料範圍 | 2024-01-02 ～ 2026-09-10 |
| 大小 | 約 31 MB |
| 校驗 | 下載後自動比對 SHA256 |

在「設定 → 資料來源 → 台股歷史日 K 資料庫」點下載即可,流程會自動走完下載、校驗、解壓縮、匯入,最後**補上資料包結束日之後到最近交易日的缺口**。

### 資料更新

系統排程在**交易日 16:30(Asia/Taipei)**自動更新日線、法人與資券資料。面板關了幾天回來時,資料狀態會顯示「過期」,按歷史日 K 卡片的**「更新到最新」**即可補齊。

---

## ⚙️ 設定

所有設定從根目錄 `.env` 讀取(複製 `.env.example` 開始),多數項目也可以在面板的**設定**頁調整。日常使用不需要任何 API Key。

```ini
PORT=3018                      # 後端服務連接埠
HOST=0.0.0.0                   # 監聽位址;只給本機用可設 127.0.0.1
DATA_DIR=./data                # 資料存放目錄
AUTH_PASSWORD=''               # 首次啟動預置存取密碼(選填)
AI_API_KEY=                    # 選配;留空 = 關閉 AI 功能
```

> 📖 完整設定項見 [docs/configuration.md](./docs/configuration.md);部署與存取密碼見 [docs/deployment.md](./docs/deployment.md)、[docs/deploy-password.md](./docs/deploy-password.md)。

---

## 🏗️ 技術棧

| 層 | 選型 |
| :--- | :--- |
| **後端** | FastAPI · Pydantic v2 · APScheduler · sse-starlette |
| **資料** | Polars(計算)· DuckDB(查詢)· Parquet(儲存) |
| **資料來源** | TWSE / TPEx 官方公開資料 · 可插件化擴充(YAML 自訂源) |
| **AI**(選配) | OpenAI 或任何 OpenAI 相容介面 |
| **前端** | React 18 · Vite · TypeScript · Tailwind · TanStack Query · Lightweight Charts · ECharts · dnd-kit |
| **桌面版** | PyInstaller · pywebview · Inno Setup(Windows 安裝包) |
| **部署** | Docker 兩階段建置,前端 dist 拷進後端映像檔,**單容器** |

---

## 📚 完整文件

| 文件 | 內容 |
| :--- | :--- |
| [操作說明書](./操作說明書.md) | **逐頁操作、推播設定、資料更新、常見問題、資料與隱私** |
| [docs/deployment.md](./docs/deployment.md) | 部署方式(Dev / Docker / GitHub Actions)、舊 CPU 相容、更新程式、存取密碼 |
| [docs/configuration.md](./docs/configuration.md) | 所有 `.env` 設定項詳解 |
| [docs/taiwan-market-overview.md](./docs/taiwan-market-overview.md) | 台股(TWSE/TPEx)模組開發者指南:啟動、測試、本地資料位置 |
| [docs/taiwan-data-sources.md](./docs/taiwan-data-sources.md) | 台股資料來源與端點說明 |
| [docs/custom-data-source.md](./docs/custom-data-source.md) | 自訂資料來源接入、YAML 設定與 mock 聯調範例 |
| [docs/plugin-development.md](./docs/plugin-development.md) | 資料來源插件開發規範 |
| [docs/secondary-development.md](./docs/secondary-development.md) | 二次開發、前端插槽、後端策略介面 |
| [CONTRIBUTING.md](./CONTRIBUTING.md) | 貢獻、AI 開發與複審規範 |

---

## ⚠️ 免責聲明

本專案僅供**學習與研究**,**不構成任何投資建議**。資料正確性以各來源官方公告為準,系統不保證即時性與完整性。股市有風險,投資決策與後果由使用者自行承擔。

## 📄 License

[MIT](./LICENSE) © Nanachi 的台股監控看板 contributors

本專案由 [tick-stock-panel](https://github.com/shy3130/tick-stock-panel) 改作而來,原專案為 A 股面板,本分支已改為台股定位。

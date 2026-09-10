**Nanachi 的台股監控看板**是一套以台灣股票日常追蹤為核心的開源工具，主要使用 TWSE 臺灣證券交易所、TPEx 證券櫃檯買賣中心等官方公開資料來源，不依賴付費行情 API。

專案聚焦於「看市場 → 找股票 → 加入自選 → 多股比較 → 設定監控」的使用流程，提供台股市場看板、台股選股、自選股、多股比較、個股行情與 K 線、監控提醒，以及 LINE Messaging API／Telegram Bot 通知。

歷史日 K 資料可透過 GitHub Release 快速下載，並可再由程式補齊最新交易日資料。

## 專案說明
**Nanachi 的台股監控看板** 是一個以台灣股票日常追蹤與監控為核心的開源專案。
本專案最初基於上游開源專案進行二次開發，並保留原始 Git 歷史與既有貢獻者紀錄，以尊重原專案的開發脈絡與開源授權。
目前版本由 **nanachi1212** 持續維護，並已針對台灣市場進行大幅重構與在地化，包括：
- 導入 TWSE 臺灣證券交易所與 TPEx 證券櫃檯買賣中心官方公開資料
- 建立台股證券主檔與歷史日 K 資料流程
- 台股市場看板、選股、自選股、多股比較與監控中心
- LINE Messaging API 與 Telegram Bot 通知
- 繁體中文介面與台灣市場用語
- 移除原有商業 API 訂閱與付費資料源依賴
- 建立可下載的台股歷史資料包與資料更新流程

目前產品定位為：
> **看市場 → 找股票 → 加自選 → 多股比較 → 設定監控**
本專案目前以台灣市場為主要使用情境，並優先採用官方公開與免費資料來源，不需要原上游專案的付費行情 API 才能正常使用。

## 維護者與貢獻者
目前主要維護者：
- [nanachi1212](https://github.com/nanachi1212)
GitHub 顯示的其他 Contributors 主要來自本專案所保留的上游 Git 歷史與既有開源貢獻紀錄。
保留這些紀錄是為了尊重原始開源專案與過往貢獻者，並不代表所有歷史 Contributors 目前都持續參與 Nanachi 台股監控看板的維護。
目前專案的台灣市場產品化、官方資料源整合、繁體中文在地化與後續維護工作，主要由目前維護者負責。

## 上游專案

本專案基於既有開源專案進行二次開發，並持續保留原 Git 歷史、授權資訊與貢獻紀錄。
目前版本已針對台灣市場進行大幅度產品化與架構調整，功能方向與原始上游版本已有明顯差異。

原始上游專案：
- https://github.com/shy3130/tick-stock-panel


# Nanachi 的台股監控看板

一套以 **台灣股票日常追蹤、選股、自選、比較與監控** 為核心的開源桌面／Web 看板。

主要整合 **TWSE 臺灣證券交易所**、**TPEx 證券櫃檯買賣中心**官方公開資料，並保留免費資料來源作為必要時的備援。

> **看市場 → 找股票 → 加自選 → 多股比較 → 設定監控**

目前版本已不需要原上游專案的 TickFlow 付費 API、API Key 或訂閱方案，即可使用主要台股功能。

---

## 主要功能

### 台股看板

快速掌握台灣市場每日狀況，包括：

- 市場方向
- 漲跌分布
- 市場強弱
- 成交與價量概況
- 台股重要市場資訊
- 資料更新時間與來源狀態

### 台股選股

提供以台灣股票為核心的選股功能，可依照價格、成交量及其他市場條件篩選標的。

### 自選股

建立自己的觀察清單，集中追蹤常用股票與 ETF。

### 多股比較

將多個台股標的一起比較，快速查看：

- 股價表現
- 漲跌幅
- 成交量
- 相關市場資訊
- 技術資料

### 個股頁面

提供個股基本行情與歷史資料，包括：

- 日 K
- 成交量
- 價格資訊
- 股票名稱與市場別
- 相關台股資料

### 監控中心

可建立股票監控規則，在條件成立時發送通知。

目前支援通知管道：

- LINE Messaging API
- Telegram Bot

### AI 輔助

AI 功能為選用功能，目前提供：

- OpenAI
- 自訂 OpenAI-compatible API

AI 主要用於協助解釋既有資料與訊號。

即使完全沒有設定 AI API，台股行情、自選股、選股、比較與監控等主要功能仍可使用。

---

# 專案定位

Nanachi 的台股監控看板不是量化研究平台，也不是付費行情 API 的銷售前端。

目前產品核心使用流程為：

```text
看市場
   ↓
找股票
   ↓
加入自選
   ↓
多股比較
   ↓
設定監控
```

設計方向以：

- 台灣市場
- 日常使用
- 繁體中文
- 簡單操作
- 官方公開資料
- 免費資料來源
- 本機資料保存

為優先。

---

# 資料來源

本專案目前主要使用台灣官方公開資料。

## 主要資料來源

### TWSE 臺灣證券交易所

用於部分：

- 上市股票資料
- 日 K
- 每日收盤行情
- 股票與 ETF 清單
- 市場資料
- 三大法人相關資料
- 信用交易相關資料

### TPEx 證券櫃檯買賣中心

用於部分：

- 上櫃股票資料
- 日 K
- 每日收盤行情
- 股票與 ETF 清單
- 信用交易及其他市場資料

## 免費備援來源

部分情境可能使用免費第三方來源作為 fallback，例如：

- Yahoo Finance
- FinMind 免費公開能力

第三方來源不應成為主要台股功能的必要付費依賴。

---

# TickFlow 依賴說明

目前 Nanachi 的台股監控看板已解除對 TickFlow 付費 API / SDK 的主要執行期依賴。

你不需要：

- TickFlow API Key
- Starter / Pro / Expert 訂閱
- TickFlow 付費行情方案

即可使用主要台股功能。

Repository 中若仍存在少數 `tickflow` 名稱，主要可能是：

- 舊版本相容路徑
- 舊設定 migration
- Compatibility stub
- 歷史技術名稱

這不代表目前核心台股功能仍需要 TickFlow 付費服務。

---

# 系統需求

目前主要於 Windows 環境開發與驗證。

建議環境：

- Windows 10 / Windows 11
- Git
- Python 3.12
- uv
- Node.js
- Corepack
- pnpm 9.x

目前實際驗證環境包括：

```text
Python 3.12
Node.js 24
pnpm 9.10.0
```

---

# 安裝方式

## 1. Clone Repository

開啟 PowerShell：

```powershell
git clone https://github.com/nanachi1212/TWstockfor_tick-stock-panel.git
cd TWstockfor_tick-stock-panel
```

如果目前台股產品化版本尚未合併進 `main`，可切換至：

```powershell
git switch feat/taiwan-product-localization
```

正式合併進 `main` 後便不需要這一步。

---

# Backend 安裝

進入 Backend：

```powershell
cd backend
```

使用 Python 3.12 建立環境並安裝依賴：

```powershell
uv sync --python 3.12 --extra dev
```

確認 Python 版本：

```powershell
uv run python --version
```

正常應顯示：

```text
Python 3.12.x
```

---

# Frontend 安裝

另外開啟 PowerShell，進入：

```powershell
cd "你的專案路徑\TWstockfor_tick-stock-panel\frontend"
```

啟用 Corepack：

```powershell
corepack enable
```

切換到目前專案驗證的 pnpm：

```powershell
corepack prepare pnpm@9.10.0 --activate
```

確認版本：

```powershell
pnpm --version
```

然後安裝：

```powershell
pnpm install
```

---

# 台股歷史日 K 資料

Git Repository 本身不直接包含完整歷史行情資料，以避免 Repository 體積過大。

目前提供獨立的 GitHub Release 資料包。

## Nanachi 台股日 K 資料包 2024-01 ～ 2026-09

資料範圍：

```text
2024-01-02 ～ 2026-09-10
```

內容：

```text
交易日：652
日 K 總筆數：1,416,933
每日資料筆數：約 2,031 ～ 2,340
資料來源：TWSE / TPEx 官方公開資料
```

GitHub Release：

https://github.com/nanachi1212/TWstockfor_tick-stock-panel/releases/tag/data-daily-2026-09-10

主要檔案：

```text
nanachi-tw-daily-2024-01-02_to_2026-09-10.zip
```

壓縮檔大小：

```text
約 30.88 MiB
```

SHA256：

```text
42e9535214197309f515750e3bc69c58eb6a81b7d8f4eed06e1d363216fa8358
```

Release 同時提供：

```text
data-manifest.json
SHA256SUMS.txt
```

方便驗證資料內容與檔案完整性。

---

# 安裝歷史資料

下載：

```text
nanachi-tw-daily-2024-01-02_to_2026-09-10.zip
```

後，解壓到 Repository 根目錄。

完成後目錄應大致為：

```text
TWstockfor_tick-stock-panel/
│
├─ backend/
├─ frontend/
├─ data/
│  └─ taiwan/
│     ├─ security_master.parquet
│     └─ daily/
│        ├─ date=2024-01-02/
│        │  └─ part.parquet
│        ├─ date=2024-01-03/
│        │  └─ part.parquet
│        ├─ ...
│        └─ date=2026-09-10/
│           └─ part.parquet
│
└─ ...
```

請不要將公開行情資料放進：

```text
data/user_data/
```

這個目錄是保存使用者自己的設定與資料。

---

# 驗證資料完整性

進入 Backend：

```powershell
cd backend
```

查看目前本機歷史資料範圍：

```powershell
uv run python -c "from app.taiwan.daily_store import TaiwanDailyStore; d=TaiwanDailyStore().available_dates(); print('交易日數:',len(d)); print('最早:',min(d)); print('最新:',max(d))"
```

如果使用 `data-daily-2026-09-10` 資料包，應看到：

```text
交易日數: 652
最早: 2024-01-02
最新: 2026-09-10
```

---

# 更新到最新交易日

下載歷史資料包後，不需要每天重新下載整份資料。

進入 Backend：

```powershell
cd backend
```

執行：

```powershell
uv run python -m app.taiwan.daily_update
```

系統會依目前本機資料狀況，嘗試補齊最新可取得的台股資料。

查看資料新鮮度：

```powershell
uv run python -m app.taiwan.daily_update --status-only
```

---

# 手動重新取得日 K

如果不使用 GitHub Release，也可以直接從官方公開來源建立本機日 K。

進入：

```powershell
cd backend
```

例如下載 2024-01-01 至目前日期：

```powershell
uv run python -c "from datetime import date; from app.taiwan.daily_refresh import TaiwanDailyRefreshService; import json; r=TaiwanDailyRefreshService().refresh_dates(date(2024,1,1), date.today()); print(json.dumps(r, ensure_ascii=False, indent=2))"
```

下載器會略過已經存在的日期，因此可以重複執行，用於：

- 補抓中途中斷的日期
- 補最新交易日
- 修復缺少的日 K partition

成功時結果會包含：

```json
{
  "failed_dates": []
}
```

---

# 啟動程式

需要同時啟動：

1. Backend
2. Frontend

---

## 啟動 Backend

開啟第一個 PowerShell：

```powershell
cd "你的專案路徑\TWstockfor_tick-stock-panel\backend"
```

啟動：

```powershell
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 3018
```

看到：

```text
Application startup complete
```

代表 Backend 已成功啟動。

Backend 位址：

```text
http://127.0.0.1:3018
```

這個 PowerShell 視窗需要保持開啟。

---

## 啟動 Frontend

再開啟第二個 PowerShell：

```powershell
cd "你的專案路徑\TWstockfor_tick-stock-panel\frontend"
```

執行：

```powershell
pnpm dev
```

正常會啟動 Vite。

目前預設網址：

```text
http://localhost:3011
```

用瀏覽器開啟：

http://localhost:3011

即可進入：

**Nanachi 的台股監控看板**

---

# 第一次使用

建議第一次啟動後依照以下順序：

```text
1. 確認歷史行情資料
2. 開啟看板
3. 搜尋股票
4. 加入自選
5. 使用多股比較
6. 視需要建立監控規則
7. 視需要設定 LINE / Telegram
8. 視需要設定 AI
```

行情與一般台股功能不需要 AI API Key。

---

# LINE Messaging API 設定

LINE 通知使用的是：

**LINE Messaging API**

不是已停止服務的 LINE Notify。

基本流程：

```text
建立 LINE Official Account
        ↓
啟用 Messaging API
        ↓
取得 Channel Access Token
        ↓
取得 Target ID
        ↓
填入 Nanachi 台股監控看板
        ↓
發送測試通知
```

## Channel Access Token

可透過 LINE Developers Console 建立 Messaging API Channel 並取得 Channel Access Token。

## Target ID

Target ID 不是一般可以搜尋好友的 LINE ID。

自己的開發者 User ID 可在 LINE Developers Console 中查看：

```text
Basic settings
→ Your user ID
```

LINE User ID 一般格式類似：

```text
Uxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

其他使用者或群組 ID 通常可由 webhook event 取得。

設定完成後可以使用系統提供的「測試通知」功能確認。

---

# Telegram Bot 設定

基本流程：

```text
BotFather
   ↓
建立 Bot
   ↓
取得 Bot Token
   ↓
先傳訊息給自己的 Bot
   ↓
透過 getUpdates 找到 chat.id
   ↓
填入 Nanachi 台股監控看板
```

## 建立 Bot

在 Telegram 找：

```text
@BotFather
```

使用：

```text
/newbot
```

依照指示建立 Bot。

建立完成後會取得 Bot Token。

## 取得 Chat ID

建立 Bot 後，必須先從 Telegram 帳號傳一則訊息給 Bot。

接著可透過 Telegram Bot API：

```text
getUpdates
```

查看收到的訊息資料。

其中：

```text
message.chat.id
```

就是可以使用的 Chat ID。

Bot 無法主動與從未互動過的私人使用者建立對話，因此第一次使用前需要先傳訊息給 Bot。

---

# AI 設定

AI 功能位於：

```text
設定
→ AI 設定
```

目前快速設定主要分為：

### OpenAI

適合直接使用 OpenAI API。

### 自訂

支援 OpenAI-compatible API。

可用於具有相容介面的其他服務，例如：

- 自架模型 API
- 部分雲端模型服務
- OpenRouter 類相容端點
- 其他 OpenAI-compatible Provider

通常可以設定：

```text
API Base URL
API Key
Model
User-Agent
Token Limit
Context Window
```

AI 功能不是台股核心行情功能的必要條件。

---

# 使用流程

## 1. 看板

從首頁快速了解目前台股市場。

主要用途：

```text
今天市場強還是弱？
        ↓
哪些方向值得進一步觀察？
```

---

## 2. 台股選股

進入：

```text
台股選股
```

依市場條件找股票。

適合：

- 尋找高成交量股票
- 尋找近期活躍股票
- 縮小每日觀察範圍

---

## 3. 自選股

找到股票後加入：

```text
自選股
```

用於建立自己的每日觀察清單。

---

## 4. 多股比較

選擇多個標的後，可進入：

```text
多股比較
```

比較數檔股票的市場表現。

---

## 5. 監控中心

對需要持續觀察的股票建立條件。

例如：

```text
價格條件
成交量條件
其他支援訊號
```

符合條件後可透過：

```text
系統通知
LINE
Telegram
```

等方式提醒。

---

# 資料目錄

公開台股市場資料主要位於：

```text
data/taiwan/
```

例如：

```text
data/taiwan/security_master.parquet
data/taiwan/daily/
```

使用者資料位於：

```text
data/user_data/
```

可能包括：

```text
preferences.json
watchlist.parquet
monitor_rules/
```

請將：

```text
data/taiwan/
```

視為可重新建立／重新下載的市場資料。

而：

```text
data/user_data/
```

則視為個人資料。

---

# 隱私與安全

請勿將以下內容提交至公開 GitHub Repository：

- API Key
- Access Token
- LINE Channel Access Token
- Telegram Bot Token
- AI API Key
- Secrets
- 個人自選股
- 個人監控規則
- `data/user_data/`
- 私人設定檔

官方提供的歷史行情 Release 不包含：

- 使用者資料
- API Key
- 自選股
- Monitor Rules
- Secrets

---

# 常見問題

## 啟動後沒有 K 線

如果個股頁面顯示：

```text
目前沒有可顯示的日 K 資料
```

通常代表：

```text
data/taiwan/daily/
```

尚未建立。

請：

1. 下載 GitHub Release 歷史資料包

或：

2. 執行官方資料下載指令。

---

## `ModuleNotFoundError: No module named 'app'`

例如：

```text
ModuleNotFoundError: No module named 'app'
```

通常代表你在錯誤的目錄執行 Backend Python 指令。

請先：

```powershell
cd backend
```

再執行：

```powershell
uv run python ...
```

不要在 Repository 根目錄直接執行需要 `app` module 的 Python 指令。

---

## Frontend 打不開

確認 Frontend PowerShell 是否仍在執行：

```powershell
pnpm dev
```

並開啟：

```text
http://localhost:3011
```

---

## 網頁能開，但沒有行情

確認 Backend 是否仍在執行：

```powershell
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 3018
```

Frontend 預設會使用 Backend：

```text
127.0.0.1:3018
```

---

## 是否需要 TickFlow API Key？

**不需要。**

主要台股功能目前已改為使用台灣官方公開與免費資料來源。

---

## 是否需要 OpenAI API Key？

**不需要。**

只有 AI 功能需要 AI Provider。

一般行情、日 K、自選、比較、選股與監控不依賴 OpenAI。

---

## 歷史行情需要每天重新下載嗎？

**不需要。**

完整歷史資料下載一次即可。

之後只需要補新的交易日。

---

## 可以完全重新下載行情嗎？

可以。

刪除或另外備份公開市場資料後，可使用官方資料下載工具重新建立。

但不要隨意刪除：

```text
data/user_data/
```

因為其中可能保存自己的：

- 自選股
- 設定
- Monitor Rules

---

# 專案結構

主要目錄：

```text
TWstockfor_tick-stock-panel/
│
├─ backend/
│  ├─ app/
│  │  ├─ api/
│  │  ├─ services/
│  │  ├─ taiwan/
│  │  └─ ...
│  └─ tests/
│
├─ frontend/
│  ├─ src/
│  │  ├─ components/
│  │  ├─ pages/
│  │  ├─ hooks/
│  │  └─ lib/
│  └─ ...
│
├─ docs/
├─ packaging/
├─ scripts/
├─ data/
│  ├─ taiwan/
│  └─ user_data/
│
└─ README.md
```

其中台股核心 Backend 主要位於：

```text
backend/app/taiwan/
```

---

# 開發與測試

## Backend 測試

```powershell
cd backend
uv run pytest
```

若只修改特定台股功能，建議優先執行相關 focused tests。

---

## Frontend 測試

```powershell
cd frontend
pnpm vitest run
```

---

## TypeScript 檢查

```powershell
pnpm tsc --noEmit
```

---

## Git Diff 檢查

在 Repository 根目錄：

```powershell
git diff --check
```

---

# GitHub 歷史資料 Release

目前歷史日 K 資料採 GitHub Release 獨立發布，而不是直接 Commit 進 Git Repository。

目前資料版本：

```text
Tag:
data-daily-2026-09-10
```

Release：

https://github.com/nanachi1212/TWstockfor_tick-stock-panel/releases/tag/data-daily-2026-09-10

這種方式可以避免：

- Git Repository 體積快速膨脹
- 每次 clone 都下載大量歷史行情
- 大型資料檔污染 Git 歷史

未來可另外發布更新版資料包。

---

# 未來規劃

目前正在持續改善：

- 全新安裝時的一鍵歷史行情初始化
- 自動偵測本機行情資料狀態
- 自動下載 GitHub Release 歷史資料包
- SHA256 完整性驗證
- 自動解壓
- 自動補齊資料包之後的新交易日
- 台股日常使用體驗
- 官方公開資料完整度
- 監控與通知體驗

未完成的功能不代表目前主要台股功能無法使用。

---

# 專案來源

本專案最初基於既有開源專案進行二次開發：

https://github.com/shy3130/tick-stock-panel

並保留原始 Git 歷史與既有 Contributors 紀錄，以尊重原專案的開發脈絡與開源授權。

目前版本已針對台灣市場進行大量重構、產品化與繁體中文在地化，產品方向與原始上游專案已有明顯差異。

---

# 維護者與 Contributors

目前主要維護者：

- [nanachi1212](https://github.com/nanachi1212)

GitHub Repository 顯示的其他 Contributors，部分來自本專案所保留的上游 Git 歷史與原開源專案既有貢獻紀錄。

保留這些紀錄是為了尊重原始開源專案與過往貢獻者，並不代表所有歷史 Contributors 目前仍持續參與 **Nanachi 的台股監控看板** 維護。

目前台灣市場相關的產品化、官方資料源整合、繁體中文在地化與後續維護工作，主要由目前維護者持續進行。

---

# 授權

本專案延續 Repository 內既有開源授權條款。

詳細內容請參閱：

```text
LICENSE
```

使用、修改或重新散布本專案前，請確認並遵守對應授權條款以及上游專案的授權要求。

---

# 免責聲明

本專案僅供：

- 資料整理
- 市場觀察
- 技術研究
- 軟體開發
- 個人投資資訊管理

使用。

本專案提供的行情、指標、篩選結果、AI 說明及監控通知均不構成：

- 投資建議
- 買賣推薦
- 報酬保證
- 金融商品招攬

市場資料可能受到：

- 官方發布時間
- 網路狀況
- API 狀態
- 交易所資料修正
- 免費備援來源延遲

等因素影響。

任何投資決策均應由使用者自行判斷並承擔相關風險。

---

# 快速開始

如果你已經準備好環境，最短流程如下。

## Clone

```powershell
git clone https://github.com/nanachi1212/TWstockfor_tick-stock-panel.git
cd TWstockfor_tick-stock-panel
```

## Backend

```powershell
cd backend
uv sync --python 3.12 --extra dev
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 3018
```

保持這個視窗開啟。

## Frontend

另外開一個 PowerShell：

```powershell
cd "你的專案路徑\TWstockfor_tick-stock-panel\frontend"

corepack enable
corepack prepare pnpm@9.10.0 --activate
pnpm install
pnpm dev
```

瀏覽器開啟：

```text
http://localhost:3011
```

如果沒有歷史 K 線：

1. 前往 GitHub Release
2. 下載 `nanachi-tw-daily-2024-01-02_to_2026-09-10.zip`
3. 解壓至 Repository 根目錄
4. 重新啟動 Backend
5. 執行最新資料更新

```powershell
cd backend
uv run python -m app.taiwan.daily_update
```

完成後即可開始使用。

---

**Nanachi 的台股監控看板**

> 看市場 → 找股票 → 加自選 → 多股比較 → 設定監控

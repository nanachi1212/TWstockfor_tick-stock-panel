# 設定詳解

所有設定從根目錄 `.env` 讀取(複製 `.env.example` 開始),也可在面板 **設定** 頁面修改。本文件解釋每個設定項的作用。

部署相關設定(連接埠/密碼/舊 CPU 相容)的實操見 [deployment.md](./deployment.md)。

---

## 資料來源設定

預設資料來源是**台灣官方資料源(TWSE / TPEx)**,整合台灣證券交易所與證券櫃檯買賣中心的公開資料,提供日K歷史行情、即時報價、標的清單與基本面資訊,**不需要任何 API Key**,`.env` 也不需要為它填任何東西。

資料集的提供方在面板 **設定 → 資料來源** 逐項切換;沒有個別指定的資料集一律由台灣官方資料源提供。系統同時支援插件化接入第三方資料來源(YAML 宣告自有介面見 [custom-data-source.md](./custom-data-source.md),插件開發見 [plugin-development.md](./plugin-development.md))。

歷史日 K 需要先在本機建立,做法見 [操作說明書 → 歷史日 K 與 GitHub Release 資料包](./操作說明書.md#15-歷史日-k-與-github-release-資料包)。

---

## AI(可選)

AI 為選配功能,只在你主動點擊時才會呼叫,用在:今日選股卡片與個股頁的白話解讀、個股頁依買點策略約束的行動建議、台股選股頁把中文條件翻譯成篩選欄位、多股比較頁與每日摘要的客觀解讀。**所有設定留空即跳過**,不影響核心功能。支援任意 OpenAI 相容介面。

```ini
AI_PROVIDER=openai_compat              # openai_compat | openai | codex_cli | claude_cli
AI_BASE_URL=http://127.0.0.1:8080/v1   # 預設指向本機 LM Studio / llama.cpp
AI_API_KEY=                            # 留空 = 關閉 AI
AI_MODEL=gpt-5.5
```

| 設定項 | 說明 |
| :--- | :--- |
| `AI_PROVIDER` | `openai_compat`(任何 OpenAI 相容介面,雲端或本機皆可)、`openai`(OpenAI 官方)、`codex_cli`、`claude_cli`(使用本機已登入的 Codex / Claude Code 訂閱,不需 API Key) |
| `AI_BASE_URL` | 介面位址,如本機 LM Studio 的 `http://127.0.0.1:1234/v1` |
| `AI_API_KEY` | 留空則關閉 AI 功能 |
| `AI_MODEL` | 模型名稱 |
| `AI_REQUEST_TIMEOUT` | 單次請求逾時秒數(預設 55),本機模型較慢時可調大 |

也可以直接在面板 **設定 → AI 設定** 填寫與測試,不必手動編輯 `.env`。

---

## 服務

```ini
HOST=0.0.0.0          # 開發服務監聽位址 / Docker 主機綁定位址
PORT=3018             # 開發後端連接埠 / Docker 主機映射連接埠
LOG_LEVEL=INFO        # DEBUG | INFO | WARNING | ERROR
```

- `HOST`:`0.0.0.0` 監聽所有網卡(容器/公開網路部署需要);僅本機用可設 `127.0.0.1`
- `PORT`:預設 `3018`;開發模式也接受 `BACKEND_PORT` 覆蓋,改連接埠後 SSH 轉發指令也要同步改
- `LOG_LEVEL`:排查問題時改 `DEBUG`

---

## 資料

```ini
DATA_DIR=./data       # Parquet / DuckDB 資料儲存目錄
```

整個 `data/` 目錄都不納入 git —— 行情日K、三大法人、融資融券、基本面、自選清單與分組、監控規則與觸發記錄,全部是程式執行時產生或拉取的使用者資料。

如需搬移資料,直接複製整個 `data/` 目錄即可。詳見 [deployment.md → 更新程式碼](./deployment.md#更新程式碼已部署使用者必讀)。

---

## 存取密碼(公開網路部署)

```ini
AUTH_PASSWORD='你的密碼'  # 至少 6 位;僅首次生效,已設過則不覆蓋
```

面板首次設定存取密碼時,出於安全考慮**僅允許本機或內網存取**(防公開網路陌生人搶先設定鎖死面板)。公開網路伺服器部署可透過此環境變數預先設定首個密碼。
密碼建議使用單引號包裹，Docker 啟動時會把整個原始 `.env` 唯讀掛載到容器內 `/app/.env`，相容已有的未加引號設定。容器可以讀取其中的金鑰但不能修改該檔案，請保持主機檔案權限為 `600` 並僅執行可信映像檔。

詳細步驟、SSH 轉發方案、重置密碼方法見 [deployment.md → 存取密碼設定](./deployment.md#存取密碼設定公開網路部署必讀)。

---

## 後端依賴 Extras(可選)

```ini
BACKEND_EXTRAS=             # 留空使用預設;legacy-cpu 相容舊 CPU
```

舊 CPU 無 AVX2/FMA 支援時設為 `legacy-cpu`,會給 Polars 切到 `rtcompat` 執行階段。Docker 建置和 `./dev.sh` / `.\dev.ps1` 都會讀取此值並同步依賴。詳見 [deployment.md → 舊 CPU 相容](./deployment.md#舊-cpu-相容avx2fma-缺失)。

---

## 設定優先順序

1. **面板設定頁**(`設定 → ...`):UI 修改後立即生效,持久化到 `data/`
2. **`.env` 檔案**:啟動時讀取
3. **環境變數**:Docker / 系統環境變數,優先順序最高

> 多數設定可在面板設定頁修改,無需手動編輯 `.env`。僅 AI Key、API Key 等敏感項建議放 `.env`(不提交到 git)。

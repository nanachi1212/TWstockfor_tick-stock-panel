# Nanachi 的台股監控看板 貢獻、AI 開發與複審指南

本文件適用於整個儲存庫，供貢獻者、AI 編碼代理和 PR 審查者共同使用。目標是讓改動落在正確的模組，保持資料定義、插件化和相容性一致，並透過可復現的驗證減少返工。

`CONTRIBUTING.md` 是專案貢獻與審查規範，不替代 `README.md`、`docs/` 中的使用者文件和領域文件。所有貢獻者和 AI 編碼代理在修改程式碼、提交或審查 PR 前都應閱讀本文件。

涉及程式碼二次開發時，同時遵循 [`docs/secondary-development.md`](docs/secondary-development.md)。前端優先使用真實存在的受控插槽或註冊入口，後端優先使用小粒度策略介面和依賴注入；現有擴展點無法表達核心行為變化時允許直接修改原始碼，但必須保持改動聚焦並補足相容性驗證。

## 1. 基本原則

### 1.1 修改前先理解

- 先定位真實呼叫鏈、資料來源、快取層和現有測試，不根據檔案名稱或介面現象猜測實作。
- 明確使用者問題、輸入、輸出、資產類型、時間週期和相容邊界後再修改。
- 存在多種解釋時必須說明假設；會改變業務定義的歧義必須先確認。
- 優先重用現有介面、服務、類型和元件，不平行實作第二套邏輯。

### 1.2 保持簡單

- 只實作目前需求，不增加未被要求的設定、相容層或通用框架。
- 單次使用的簡單邏輯不提前抽象；出現真實重複或明確模組邊界時再重用。
- 能在現有模組內完成的改動，不新增跨層依賴或新目錄。
- 註解解釋「為什麼」，不復述程式碼行為。

### 1.3 最小範圍修改

- 每一處變更都應能追溯到目前問題。
- 不順手重構、重新命名、格式化或刪除無關程式碼。
- 保留使用者和其他貢獻者尚未提交的改動，不覆蓋、不還原。
- 只清理由本次修改產生的無用匯入、變數、檔案和除錯輸出。

### 1.4 以驗證結果作為完成標準

- 修復 bug 時，優先編寫能復現問題的測試，再修到測試通過。
- 新功能至少覆蓋正常路徑、關鍵邊界和失敗路徑。
- 「程式碼已寫完」「頁面能開啟」或「GitHub 沒有紅燈」都不代表完成。
- 無法執行必要驗證時，必須在 PR 中說明原因和剩餘風險。

## 2. 專案架構

### 2.1 技術棧

- 後端：Python 3.11+、FastAPI、Pydantic v2、Polars、DuckDB、PyArrow。
- 業務資料計算優先使用 Polars；pandas 只在 pyarrow 互轉邊界使用。
- 前端：React 18、TypeScript、Vite、TanStack Query、Tailwind CSS、ECharts、lightweight-charts。
- 包管理和驗證命令：後端使用 `uv`，前端使用 `pnpm`。

### 2.2 主資料流

```text
資料源或資料插件
  -> services 同步、驗證與標準化
  -> DataStore / KlineRepository / Parquet
  -> indicators pipeline 生成 enriched 資料
  -> 台股選股 / 監控 / AI 分析服務
  -> FastAPI API / SSE
  -> frontend API 類型 + TanStack Query
  -> 頁面和共享元件
```

改動前應沿這條鏈路檢查上下游。禁止透過頁面或 API 層繞過標準化、儲存庫、業務服務而直接讀取某個資料源或本機檔案。

### 2.3 模組邊界

| 目錄 | 職責 | 放置要求 |
| --- | --- | --- |
| `backend/app/api/` | HTTP、SSE、參數驗證和回應映射 | 保持薄層，不放重計算、全量掃描或資料源專屬業務 |
| `backend/app/services/` | 同步、即時行情、通知等業務編排 | 組合領域能力，不複製儲存庫或 provider 的實作 |
| `backend/app/tickflow/` | Parquet / DuckDB 資料儲存、儲存庫與能力檢測（沿用舊目錄名） | 對外透過既有抽象暴露能力，避免上層依賴儲存細節 |
| `backend/app/data_providers/` | 資料源介面、標準化模型和自訂資料源 | 新資料能力先定義資料集契約，再由 provider 實作 |
| `backend/app/indicators/` | 指標計算和 enriched 流水線 | 保持向量化，明確原始價、復權價和輸出單位 |
| `backend/app/taiwan/` | 台股資料、選股、監控、AI 研究與量化評估（authoritative） | 子模組邊界見 `AGENTS.md`，缺資料 fail-closed |
| `frontend/src/lib/api.ts` | 前端 API 請求和類型契約 | 後端契約變化必須同步更新類型與呼叫方 |
| `frontend/src/lib/queryKeys.ts` | TanStack Query 查詢鍵 | 查詢鍵集中維護，不在元件內拼出平行鍵 |
| `frontend/src/lib/useSharedQueries.ts` 等 | 跨頁面共享查詢 | 相同資料優先共享快取，避免每張卡片重複請求 |
| `frontend/src/pages/` | 頁面級編排 | 複雜領域邏輯下沉到 hook、元件或後端服務 |
| `frontend/src/components/` | 可重用互動元件 | 保持 props 契約穩定，不把頁面專屬狀態塞進公共元件 |
| `data/` | 使用者執行階段資料 | 不提交到 Git，不把本機樣本、帳號資訊或絕對路徑寫入 PR |

## 3. 不可混用的資料契約

金融資料錯誤經常不會報異常，但會生成看似合理的錯誤結果。涉及下列欄位時，必須在變數名、邊界轉換或測試中證明定義正確。

### 3.1 比例和百分比

| 欄位/場景 | 目前定義 | 範例 |
| --- | --- | --- |
| 自訂即時資料源入口的 `change_pct` | 小數制 | `0.0366` 表示 `3.66%` |
| enriched 和股票監控內部的 `change_pct` | 通常為小數制 | 閾值比較前確認呼叫鏈，不憑欄位名推斷 |
| 提醒事件（`alerts.jsonl`、SSE）的 `change_pct` | 小數制 | `-0.0565` 表示 `-5.65%`；漲跌幅規則的 `trigger_value`/`threshold` 仍為百分點 |
| 指數即時展示快取的漲跌幅 | 存在百分數定義 | 使用前必須顯式轉換，不得直接重用到股票監控 |
| 即時資料源入口的 `turnover_rate` | 小數制 | `0.05` 表示 `5%` |
| enriched 的 `turnover_rate` | 百分數值 | `5` 表示 `5%` |

新增跨邊界映射時必須增加單位測試。禁止用「數值小於 1 就乘 100」一類啟發式轉換，這會掩蓋真實資料錯誤。

### 3.2 價格與復權

- enriched 的 `open/high/low/close` 為前復權價格。
- `raw_close/raw_high/raw_low` 為不復權原始價格。
- 漲停、跌停、鎖漲停、鎖跌停和交易所價格限制必須基於原始價及對應交易日規則判斷。
- 技術指標和收益序列使用哪種價格，必須與現有指標定義一致，不得在同一公式中混用。

### 3.3 日期、交易日和時區

- 窗口、前 N 日和批次回算均按實際交易日，不得用自然日直接替代。
- 台股交易時段統一按台北時間（`Asia/Taipei`）處理；伺服器時區不能成為業務邏輯的隱式輸入。
- 日線、分鐘線和即時快照必須明確交易日期歸屬，尤其注意盤前試撮、收盤後與盤後定價，以及跨日重啟。
- 分鐘 K 的股票、ETF、指數分開儲存和路由，不得僅憑代號格式猜測資產類型。

### 3.4 歷史股本與週轉率

- 歷史週轉率優先使用公告日不晚於目標交易日的歷史股本。
- 缺少可用歷史股本時，才允許降級到最新維表股本。
- 當日維表、財務公告日和報表期是不同概念，不能用報表期提前洩露尚未公告的資料。
- 修改股本補全或 enriched 重算時，必須覆蓋「有歷史股本」和「無歷史股本降級」兩條路徑。

## 4. 資料源插件化要求

資料源已經插件化。任何通用功能都必須透過 provider 能力和標準化資料集存取資料，不能把單一資料來源的 SDK 呼叫硬編碼到選股、監控、API 或前端流程中。

- 使用現有的 `get_provider()`、`provider_has_dataset()` 和 preferences 路由能力。
- 支援的資料集包括但不限於 `daily`、`adj_factor`、`minute`、`realtime`、`financial`；新增資料集應先定義清晰的輸入輸出契約。
- provider 負責把供應商欄位、單位、日期和代號格式轉換為內部標準格式。
- 上層服務依賴標準欄位和能力宣告，不依賴供應商回應結構。
- provider 缺少某項能力時，應明確提示、跳過該功能或 fail-closed；禁止靜默換用錯誤資料或錯誤定義。
- 插件載入失敗、欄位缺失和空資料必須有隔離測試，不能導致應用啟動失敗或其他資料源不可用。
- 新增資料源適配應同步更新 `docs/custom-data-source.md` 或 `docs/plugin-development.md` 中對應契約。

## 5. 領域專項要求

### 5.1 監控與通知

- 監控計算應重用標準化即時行情和統一規則，不為頁面、語音、LINE、Telegram 分別計算。
- 漲跌幅、漲跌停和均價穿越必須明確資料定義、交易時段、資產類型和閾值方向。
- 分鐘訊號只有在 provider 宣告分鐘資料能力且標的進入訂閱池後才啟用；無權限時給出明確狀態。
- 行情回呼執行緒中不得執行全量歷史重算、同步 Webhook、慢磁碟掃描等長耗時任務。
- 通知文案使用使用者可理解的中文名稱，不洩漏內部枚舉名、模組名或除錯欄位。
- 同一事件的去重、冷卻和恢復狀態必須可測試，重啟後的行為要與持久化設計一致。

## 6. 快取、併發與效能

專案同時存在儲存庫快取、enriched 歷史快取、即時聚合、篩選快取、mtime 快取、SSE 和前端 TanStack Query。修改讀寫路徑時必須列出受影響的快取層。

### 6.1 快取一致性

- 寫資料後同步檢查：持久化檔案、記憶體快取、generation/version、SSE 事件和前端 query invalidation。
- 不能只保證「檔案已寫入」，卻繼續回傳舊記憶體物件。
- 多步重新整理優先建置新快照後原子替換，避免 UI 在重新整理期間短暫變成空清單或零結果。
- 快取鍵必須覆蓋資產類型、週期、日期、策略參數和其他會改變結果的維度。
- 失效範圍應精確；不能為了修復單條資料而無條件清空所有歷史快取。

### 6.2 併發安全

- API、背景 worker 和即時行情執行緒共享字典或快取時，使用鎖、不可變快照或原子替換。
- 不在持鎖期間執行網路請求、磁碟全量掃描或重計算。
- 取消、超時和應用關閉路徑必須釋放資源，背景執行緒不得阻止程序退出。

### 6.3 效能約束

- 清單頁避免 N+1 API、每卡片獨立請求和重複全量策略計算，優先批次介面及共享查詢。
- Polars 計算優先表達式和批處理，避免逐行 Python 循環。
- 即時熱路徑不得隨歷史資料量線性增長；需要歷史窗口時只讀取必要範圍。
- 無規則、無訂閱者或 provider 無能力時，不執行對應即時計算。
- 聲稱「提升效能」的 PR 必須給出同一資料規模、同一環境下的修改前後資料，至少包括耗時或請求數。

## 7. 前端改動要求

- 重用 `frontend/src/lib/api.ts` 中的請求封裝和類型，禁止元件直接散落請求位址。
- TanStack Query 查詢鍵使用集中定義；資產類型、日期、週期等影響結果的參數必須進入查詢鍵。
- 切換股票、ETF、日期或策略後，不得把上一上下文的快取結果當作目前結果。
- 即時重新整理保留上一份有效資料直到新資料就緒，避免列、計數和清單閃爍消失。
- 彈窗開啟和關閉必須正確重置臨時表單狀態；新建態與編輯態分離。
- 公共標籤、個股詳情、成分股等跨頁面行為使用共享元件，不在多個頁面複製互動。
- 新增互動必須覆蓋載入、空資料、錯誤、禁用和無權限狀態。
- 改動應在常用桌面和窄屏尺寸下檢查文字截斷、遮擋、滾動區域和彈窗可操作性。
- 不用前端修補掩蓋後端單位或契約錯誤；契約問題應在資料邊界修正。

## 8. 安全、相容與使用者資料

- 不提交 `data/`、金鑰、Token、Webhook、使用者策略、記錄檔、資料庫、Parquet 樣本和機器絕對路徑。
- 記錄檔和異常回應不得包含授權資訊、完整設定或使用者敏感資料。
- 檔案刪除、覆蓋、遷移和批次回算屬於高風險操作：限制目標目錄、驗證路徑、提供清晰失敗原因，並保持 fail-closed。
- 不用不安全的字串拼接執行 SQL、Shell 或動態 Python。
- 設定、策略 JSON、Parquet schema 和 API 欄位變化預設需要向後相容。新增欄位優先提供預設值，讀取歷史資料時允許欄位缺失。
- 必須改變既有行為時，在 PR 中列出遷移方式、降級行為和影響範圍。
- Windows、macOS、Linux 和 Docker 的路徑、編碼、回收站及權限行為不同；涉及檔案系統時至少審查跨平台失敗路徑。

## 9. 驗證矩陣

根據改動範圍執行最小但充分的驗證。以下是最低要求，不是上限。

| 改動類型 | 最低驗證 |
| --- | --- |
| 後端純函式、規則或 bug 修復 | 對應定向 `pytest`，包含復現用例和邊界用例 |
| API 契約 | service 測試 + API 測試；檢查成功、無資料和錯誤回應 |
| 資料源或插件 | provider 契約測試、缺少能力、空資料、欄位/單位標準化測試 |
| 指標、復權、週轉率、漲跌停 | 固定樣本數值斷言，覆蓋歷史邊界和降級路徑 |
| 策略或監控 | 參數變更、快取失效、資產切換、重啟/併發相關路徑 |
| 快取或效能 | 命中/失效測試、併發快照測試；必要時提供前後基準 |
| 前端元件或頁面 | `pnpm build`，並手工檢查載入、空、錯、切換和即時重新整理狀態 |
| 前後端契約同時變化 | 後端定向測試 + 前端建置 + 對應頁面聯調 |
| 檔案、Docker 或跨平台邏輯 | 正常路徑、權限/能力缺失和目標不可用的失敗路徑 |

常用命令：

```bash
cd backend
uv run pytest tests/path/to/test_x.py -q
uv run ruff check app/path.py tests/path.py

cd frontend
pnpm build

git diff --check
```

- 後端至少執行受影響模組的測試，不應只執行新加的單個測試。
- 新增檔案和本次改動不得引入 Ruff 告警。不要為清理歷史告警而在功能 PR 中全倉格式化。
- 前端任何 TypeScript、元件、樣式或 API 類型改動至少執行一次 `pnpm build`。
- 提交前必須執行 `git diff --check`，並檢查最終 diff 不含除錯程式碼和無關檔案。

## 10. PR 提交要求

一個可審查的 PR 應聚焦一個問題。功能、無關重構、批次格式化和依賴升級不得混在一起。

PR 描述必須包含：

1. **問題與復現**：原行為、復現條件和影響使用者。
2. **根因**：具體到呼叫鏈、資料契約或狀態生命週期，不能只寫「修復 bug」。
3. **解決方案**：改動模組、關鍵取捨，以及為什麼沒有破壞插件化或現有定義。
4. **相容性**：對歷史設定、策略、快取、Parquet、API、資料源和不同資產類型的影響。
5. **效能**：是否進入即時或清單熱路徑；有效能主張時附前後資料。
6. **驗證結果**：列出實際執行的命令和結果，不寫計劃執行的測試。
7. **介面證據**：有可見變化時提供修改前後截圖或短影片，並覆蓋淺色/深色或窄屏中相關場景。
8. **風險與還原**：剩餘風險、降級行為和可行的還原方式。

提交前作者自檢：

- [ ] 改動只解決 PR 描述的問題，沒有夾帶無關整理。
- [ ] 已檢查完整呼叫鏈和所有受影響快取。
- [ ] 已核對單位、復權、交易日、時區和資產類型。
- [ ] 未繞過 provider abstraction，缺少能力時行為明確。
- [ ] 歷史設定和舊資料仍可讀取，或已說明遷移方案。
- [ ] 即時執行緒、清單請求和批處理沒有明顯效能退化。
- [ ] 錯誤路徑 fail-closed，不會靜默回傳錯誤金融結果。
- [ ] 測試能在未修復程式碼上失敗，並在修復後通過。
- [ ] 已執行適用的測試、建置、Ruff 和 `git diff --check`。
- [ ] 最終 diff 不含敏感資料、本機路徑、除錯輸出和產生的檔案。

## 11. PR 複審流程

作者自審和維護者複審均按以下順序進行，避免只看頁面效果或新增程式碼：

1. **確認問題**：根據描述和測試復現舊行為，確認 PR 解決的是同一個問題。
2. **檢查邊界**：確認程式碼位於正確模組，沒有反向依賴、平行實作或 API 層重計算。
3. **核對資料契約**：檢查單位、復權、日期、時區、資產類型、空值和排序方向。
4. **核對插件化**：檢查是否依賴 provider 能力，其他資料源能否工作或明確降級。
5. **追蹤狀態變化**：從寫入一路檢查持久化、快取、generation、SSE 和前端查詢失效。
6. **檢查相容性**：使用歷史設定、舊策略 JSON、舊 Parquet schema 和缺欄位資料驗證。
7. **檢查效能和併發**：定位是否進入即時、清單或全量掃描熱路徑，檢查鎖和原子替換。
8. **檢查失敗路徑**：網路失敗、無權限、空資料、能力缺失和部分寫入時不得產生錯誤狀態。
9. **評估測試品質**：測試必須斷言業務結果，覆蓋負例和邊界，不能只斷言介面回傳 200。
10. **檢查最終 diff**：按檔案逐項確認必要性，排除無關格式化、敏感資訊和暫存檔案。
11. **給出結論**：明確寫出「可合併」「修改後合併」或「不建議合併」，區分阻斷項和建議項。

問題嚴重級別：

| 級別 | 定義 | 合併要求 |
| --- | --- | --- |
| `P0` | 資料破壞、安全漏洞、可能導致嚴重錯誤交易結果 | 禁止合併，修復並完整複審 |
| `P1` | 核心流程不可用、錯誤金融定義、未來函式、廣泛迴歸 | 禁止合併，修復並補回歸測試 |
| `P2` | 明確功能錯誤、相容性問題或明顯效能退化 | 修改後合併 |
| `P3` | 維護性、局部體驗、文件或測試補強建議 | 可不阻斷，但應記錄後續處理方式 |

複審意見應包含檔案和行號、觸發條件、實際影響及建議修改方向。不要只寫「這裡可能有問題」或直接給出沒有證據的結論。

### 11.1 追加提交後的複審

- 先逐項核對上輪每個阻斷問題是否在程式碼和測試中真正解決，不以提交者回復「已修改」為依據。
- 查看新增 commit 便於理解修復意圖，但最終必須重新審查 PR 相對目標分支的完整 diff，不能只看最後一次提交。
- 重新執行受影響測試，並檢查修復是否引入新的單位、快取、相容性、效能或插件化問題。
- 已解決的問題明確標記為已驗證；仍存在的問題引用目前最新行號並說明缺失內容。
- 原阻斷項全部解決且沒有新增阻斷項後，才能把結論改為「可合併」。

### 11.2 推薦的複審輸出格式

```text
結論：可合併 / 修改後合併 / 不建議合併

阻斷問題：
- [P1/P2] 檔案:行號 - 觸發條件、實際影響、修改要求

非阻斷建議：
- [P3] 檔案:行號 - 建議及理由

已驗證：
- 執行的測試或建置命令及結果
- 上輪問題的逐項複核結果

剩餘風險：
- 未覆蓋的平台、資料源、資料規模或場景
```

沒有發現問題時也應明確寫「未發現阻斷問題」，並說明尚未驗證的範圍，不能用「看起來沒問題」代替結論。

## 12. 不得合併的情況

出現以下任一情況，PR 不應合併：

- 無法說明根因，修改只是增加延時、重試、強制重新整理或吞掉異常來掩蓋現象。
- 混用百分比、復權價、自然日和交易日，或無法證明金融計算定義。
- 通用功能直接綁定單一資料源，且沒有能力檢測或明確降級。
- 寫入後存在已知快取不一致，需使用者重啟或手動清資料才能正確顯示。
- 歷史評估引入未來資料，或歷史結果和展示使用不同選股邏輯。
- 即時執行緒增加阻塞網路請求、全量歷史計算或無界任務。
- 破壞歷史策略、設定、資料檔案或 API，且沒有相容或遷移方案。
- 檔案操作可能越界刪除、預設覆蓋使用者資料，或失敗後繼續執行。
- 測試沒有復現問題、只覆蓋正常路徑，或必要測試/前端建置失敗。
- PR 包含金鑰、使用者資料、本機資料目錄、機器絕對路徑或大量無關改動。
- 對評審提出的 `P0`、`P1`、`P2` 問題尚未處理或驗證。

## 13. AI 編碼代理額外規則

- 開始前先讀取本文件、目標模組、相鄰實作、呼叫方和相關測試。
- 修改前用簡短計劃寫明每一步及驗證方式；簡單改動可只寫一句。
- 不把猜測當成事實。無法從程式碼、測試或文件確認的業務定義，應明確詢問。
- 不因使用者要求「直接修復」就跳過復現、根因分析和迴歸驗證。
- 不自動執行提交、推送、合併、刪除檔案、資料庫遷移或批次資料覆蓋；需要時按目前會話權限規則取得明確確認。
- 不覆蓋工作區已有改動。遇到同檔案並行修改時先讀取最新內容並保留雙方邏輯。
- 不虛構測試結果、效能資料、GitHub 狀態或線上行為。未執行的驗證必須明確標註。
- 審查 PR 時先報告問題，按嚴重級別排序並引用檔案行號；最後再給合併建議和簡短總結。
- 完成後檢查 `git diff` 和 `git status`，準確說明改了什麼、驗證了什麼、仍有什麼風險。

遵循本指南的衡量標準不是文件寫得多，而是：改動範圍更小、金融定義有證據、插件邊界不被繞過、測試能真實復現問題、複審結論可執行，且相同問題不再反覆返工。
## Agent review and CI efficiency

Use the repository workflow below for agent-authored pull requests.

1. Run `.\scripts\ci.ps1 -Mode Auto` once after the implementation is ready. Accept the lanes selected by `Auto`.
2. Request Codex review at most once per pull request by default. Use `.\scripts\request-codex-review.ps1 -Pr <number>` instead of posting `@codex review` directly.
3. Collect all findings from that review. Fix valid findings in one batch.
4. After review fixes, run targeted validation for the changed behavior. Run `Auto` one more time only when the routing rules require broader validation.
5. Do not request Codex review again after the fix batch. Wait for required GitHub CI and finish the pull request.
6. An exceptional re-review is allowed only when the review-fix batch materially changes security/authentication, schema or migration behavior, release/signing behavior, or a shared contract. State the reason and use `request-codex-review.ps1 -Force`.
7. Pull-request workflows use concurrency cancellation. A newer commit on the same pull request cancels obsolete in-progress runs. Main-branch and manual release runs are not canceled by this policy.
8. Generic `frontend/**` changes use the dedicated `Frontend Tests` workflow. They do not trigger Data Health, Strategy Lab, or Windows Installer by themselves.
9. Windows Installer CI is reserved for desktop/release/packaging code and frontend dependency or build-configuration changes. Normal TS/TSX/CSS UI edits rely on Frontend Tests.
10. Frontend-only pull requests still emit the required Backend Tests status, but they take a lightweight no-backend path instead of running pytest. Backend and shared-contract changes still run the full backend suite.

This policy prevents `review -> fix -> review -> fix` loops and prevents old CI runs from consuming time after a newer commit supersedes them.

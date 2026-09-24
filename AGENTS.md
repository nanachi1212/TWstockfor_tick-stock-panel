# TWstockfor_tick-stock-panel Repository Instructions

本檔補充 repository 的產品、架構與台股資料語意規則。修改、調試或審查本倉庫前，必須完整閱讀 [`CONTRIBUTING.md`](CONTRIBUTING.md)，遵循其中的架構、數據契約、測試與審查規範。涉及代碼二次開發、前端插槽、後端可替換策略、擴展註冊或上游升級兼容時，另須閱讀 [`docs/secondary-development.md`](docs/secondary-development.md) 相關章節，並以實際程式碼確認 API 存在。

## Product Direction

- 本 repository 是唯一長期維護的台股主產品與最終使用介面。
- 市場觀察、量化選股、個股分析、AI research、Portfolio、研究歷史、事件提醒與 Dashboard 應逐步整合於同一個程式。
- `mystocktracer` 僅作為既有功能、資料契約與 UX 的參考來源；不要建立 TWStock → mystocktracer 的長期 shared-dataset adapter，也不要維持兩套平行 authoritative implementation。
- 移植既有功能時，應適配本 repository 的架構，不要把舊專案不必要的結構一起複製進來。

## Module Ownership

同一項核心計算只能有一個 authoritative implementation。責任邊界如下，實作前須確認程式庫中實際存在的模組與調用鏈，不可憑名稱虛構 API 或目錄：

- `market_data`：市場及來源資料取得與標準化。
- `quant`：因子、IC、ML、walk-forward 評估與選股評分。
- `research`：AI 輔助解讀與研究工作流。
- `portfolio`：持倉與投資組合計算。
- `alerts`：事件與條件通知。

## Taiwan Market Contracts

- 保留既有市場語意、schema、symbol、日期、交易規則與 availability 狀態；使用市場當地時間時採 `Asia/Taipei`。
- 區分原始價與復權價、百分比單位、實際交易日與自然日，以及公告日和報表期。
- 因子與歷史評估必須符合 point-in-time 語意，避免使用未來資料；Universe selection 不得使用當時不可得的資訊。
- 保留資料來源、`as_of`、provenance 與 fallback 狀態。`available`、`stale`、`partial`、`unavailable` 與未查詢不可互相偽裝。
- 缺資料不能靜默轉成 0、最新值或看似正常的新鮮資料；金融結果無法可靠計算時，應 fail-closed 或回傳明確不可用狀態。
- 修改共享台股資料契約前，先確認所有實際 consumer，再修改唯一 authoritative contract。

## Architecture

- 優先擴充既有模組、服務、provider、repository、query cache 與 UI component，不建立第二套平行資料流或重複權威計算。
- API 層保持薄；不要繞過既有標準化、資料倉庫或 service 直接讀取來源或本機檔案。
- 避免只為維持 `mystocktracer` 為第二套長期產品而新增相容層。
- 修改寫入或刷新流程時，檢查持久化、記憶體快取、generation/version、SSE 與前端 query invalidation 是否一致。
- 前端不要用局部補丁掩蓋後端資料單位或契約錯誤。

## Validation

- 驗證範圍應與改動相稱；先執行最小且有意義的測試，必要時再擴大至 backend、frontend 或 integration checks。
- 修改金融計算、單位、PIT、交易日或 market rules 時，使用固定樣本與邊界測試證明結果。
- 修改前後端共同契約時，驗證 backend contract 與 frontend consumer；修改共享快取、啟動或跨模組契約時，擴大 regression/build 驗證。
- Taiwan backend 測試需注意其 working directory 與本機資料路徑語意。
- 外部上游資料失敗必須與本次 regression 分開判定；不得虛報 CI 或審查結果。

## Context Routing

除必讀的 `CONTRIBUTING.md` 外，只讀取任務涉及的領域文件：

- 架構邊界或 authoritative ownership：閱讀相關 architecture 文件。
- provider、plugin 或自訂資料源：閱讀 `docs/custom-data-source.md`、`docs/plugin-development.md` 或對應契約。
- 台股 symbol、交易規則、稅費、settlement 或資料語意：閱讀相關 Taiwan market / data-source 文件。
- 二次開發、前端插槽、後端擴展點或上游升級：閱讀 `docs/secondary-development.md` 對應章節。
- deployment、packaging 或 release：只在任務涉及時閱讀對應文件。

## User Data and Runtime Data

- 不提交 `data/`、使用者 runtime data、密鑰、Token、Webhook、日誌、資料庫、Parquet 樣本或機器絕對路徑。
- 配置、歷史資料與 persisted schema 優先保持向後相容；必要 migration 必須可安全失敗並保留可恢復性。

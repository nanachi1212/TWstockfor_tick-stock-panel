# TWstockfor_tick-stock-panel Repository Instructions

本檔只補充此 repository 的產品、架構與資料語意規則。
一般開發流程、Git/GitHub、安全、驗證與回報方式遵循使用者層 Global `AGENTS.md`。

## Product Direction

- 本 repository 是唯一長期維護的台股主產品與最終使用介面。
- 市場觀察、量化選股、個股分析、AI research、Portfolio、研究歷史、事件提醒與 Dashboard 應逐步整合於同一個程式。
- `mystocktracer` 僅作為既有功能、資料契約與 UX 的參考來源；不要建立 TWStock → mystocktracer 的長期 shared-dataset adapter，也不要維持兩套平行 authoritative implementation。
- 移植既有功能時，應適配本 repository 的架構，不要把舊專案不必要的結構一起複製進來。

## Module Ownership

同一項核心計算只能有一個 authoritative implementation。

概念 ownership：

- `market_data`：資料取得、標準化、來源狀態與快取
- `quant`：因子、IC、ML、walk-forward、回測與選股分數
- `research`：AI 解讀與研究工作流
- `portfolio`：持倉、成本、損益、權重與組合分析
- `alerts`：事件與條件提醒

實作時先確認 repository 中真實存在的模組與調用鏈；以上是責任邊界，不代表可以憑名稱虛構不存在的 API 或目錄。

## Taiwan Market Contracts

涉及台股資料與計算時，必須保留既有市場契約與來源語意。

特別注意：

- TWSE / TPEx symbol 與商品類型
- 原始價、復權價與百分比單位
- 實際交易日而非自然日
- `Asia/Taipei` 市場時間
- Point-in-Time 語意，避免 future-data leakage
- 資料來源、`as_of`、provenance 與 fallback
- `available` / `stale` / `partial` / `unavailable` / 未查詢等狀態不得互相偽裝
- 缺資料不能靜默轉成 0、最新值或看似正常的新鮮資料

修改共享資料契約時，先確認所有實際 consumer，再修改 authoritative contract。

## Architecture

- 優先擴充既有服務、provider、repository、query cache 與 UI component，不建立第二套平行資料流。
- API 層保持薄；不要繞過既有標準化、資料倉庫或 service 直接讀取來源或本機檔案。
- 前端不要用局部補丁掩蓋後端資料單位或契約錯誤。
- 修改寫入或刷新流程時，檢查持久化、記憶體快取、generation/version、SSE 與前端 query invalidation 是否一致。
- 金融結果無法可靠計算時應 fail-closed 或回傳明確不可用狀態，不要產生看似合理的錯誤結果。

## Context Routing

不要每次工作都完整閱讀所有文件；只在任務涉及對應領域時讀取必要內容。

- 台股 symbol、交易規則、稅費、settlement 或資料語意：讀相關 Taiwan market contract / data-source 文件。
- provider、plugin 或自訂資料源：讀 `docs/custom-data-source.md`、`docs/plugin-development.md` 或相關契約。
- 二次開發、前端插槽、後端擴展點或上游升級：讀 `docs/secondary-development.md` 的相關章節，並以實際程式碼確認 API 真實存在。
- deployment / packaging / release：只在任務涉及部署、打包或 release 時讀對應文件。
- `CONTRIBUTING.md` 作為架構與驗證參考；不要求每個微小任務開始前無條件全文重讀。

## Validation

驗證應與改動範圍相稱。

- Taiwan backend 測試需注意其 working directory 與本機資料路徑語意。
- 修改金融計算、單位、PIT、交易日或 market rules 時，優先使用固定樣本與邊界測試證明結果。
- 修改前後端共同契約時，驗證 backend contract 與 frontend consumer。
- 修改共享快取、啟動、打包或跨模組契約時，再擴大到較完整的 regression / build。
- 外部上游資料失敗必須與本次 regression 分開判定。

## User Data and Runtime Data

- 不提交 `data/`、使用者 runtime data、密鑰、Token、Webhook、日誌、資料庫、Parquet 樣本或機器絕對路徑。
- 對配置、歷史資料與 persisted schema 的修改應優先保持向後相容；必要 migration 必須可安全失敗並保留可恢復性。

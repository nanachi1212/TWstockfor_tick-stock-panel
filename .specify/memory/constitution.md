<!--
Sync Impact Report
- Version change: placeholder scaffold (未定義) -> 1.0.0
- Modified principles: template placeholders -> 五項台股產品長期原則
- Added sections: 產品與架構約束；開發與驗證方式
- Removed sections: none
- Follow-up TODOs: none
-->

# Nanachi 台股監控看板 Constitution

## Core Principles

### I. 使用者可見的端到端價值優先

每項功能 MUST 以實際可用的端到端流程與使用者體驗為主要完成標準，從資料取得、標準化、服務、API 到畫面或通知都必須能完成需求所需的最小閉環。純框架、抽象層、provenance、state machine、CLI 或其他使用者不可見的基礎設施，只有在目前功能不可缺少、能降低已確認風險，或直接支撐可驗收行為時才建立。理由是本專案由單人維護，有限時間應優先投入使用者能操作、理解與驗證的產品能力。

### II. 最小完整解法與現有架構

新增或修改功能 MUST 優先沿用目前的模組責任、資料格式、服務邊界、provider 契約、前端 API 型別、共用查詢鍵與既有 UI 模式。能在現有架構內完成時，不得建立平行資料流、第二套權威計算、無必要的相容層或大型未來框架；不得無必要新增依賴、改變公開介面或改變既有使用者行為。無關重構、批量格式化與假想需求的預留 MUST 排除在改動之外。

### III. 可驗證且與風險相稱

每項變更 MUST 依影響範圍選擇最小但充分的驗證，優先使用受影響模組的 targeted tests、lint、type check、build 或 smoke test，並沿用 repository 既有的 `uv`、`pytest`、`ruff`、`pnpm` 與 CI 慣例。涉及跨層契約、共享快取、啟動流程或使用者可見狀態時，驗證 MUST 擴大到實際 consumer；外部服務不穩定的 live integration MUST 與一般 PR 驗證分開。未執行的檢查不得描述為已通過，無法執行必要驗證時 MUST 明確記錄原因與剩餘風險。

### IV. 台股資料與金融計算可重現

選股、回測、績效與推薦相關功能 MUST 避免未來資料洩漏、回看偏誤、錯誤單位、交易日誤用與不可重現的計算。實作 MUST 遵守既有的台股 symbol、Asia/Taipei 時區、實際交易日、原始價與復權價、公告可得時間及 provider availability 語意；缺資料、未知值或能力不足 MUST 保持明確的 unavailable、partial 或 degraded 狀態，不得靜默轉成零或最新值。輸出 MUST 保留足以核對當時決策的資料日期、版本與必要依據，但只建立目前功能需要的最小紀錄，不以治理系統取代產品價值。

### V. 兼容、失敗可見與使用者資料安全

變更 MUST 維持既有 API、設定、策略、Parquet schema、查詢快取與使用者資料的向後相容，除非需求明確要求破壞性改變並同時定義遷移或降級行為。provider 缺少能力、資料源失敗、空資料、權限不足與部分更新 MUST 產生可理解的狀態或 fail-closed 行為，不得以錯誤金融結果掩蓋失敗。程式與文件 MUST 不提交 runtime data、密鑰、token、Webhook、日誌、樣本資料或機器絕對路徑；涉及刪除、覆寫與批量回算時 MUST 限制目標範圍並保留可恢復性。

## 產品與架構約束

- 本產品以 TWSE、TPEx 等公開資料為基礎，核心用途是看市場、選股、比較、管理自選與監控；系統不得把規則訊號或 AI 解讀包裝成投資建議、預測、推薦或自動下單。
- 後端 API 保持薄層，資料來源經既有 provider 與服務標準化後，再由資料儲存、指標、策略、回測或監控模組使用；API 與前端不得繞過既有服務直接讀取來源或本機資料。
- 前端沿用 React/Vite、集中 API 型別與 TanStack Query 查詢鍵；同一資料與互動優先共享既有 query、元件和狀態模式，並呈現載入、空資料、錯誤、不可用與無權限狀態。
- 修改資料寫入或刷新路徑時，實作者 MUST 檢查持久化、記憶體快取、generation/version、SSE 與前端 query invalidation 是否仍一致；無關的跨模組重構不屬於本原則要求。

## 開發與驗證方式

- Spec Kit MUST 用於大型功能、新子系統、架構變更、需求容易偏移或需要明確驗收標準的工作；小型 bug、簡單 UI 修改、README 與普通維護不強制使用完整 Spec Kit 流程。
- 實作前 MUST 以實際程式碼、調用方、資料契約與測試確認責任邊界，不得只根據檔名、介面現象或假想 API 設計方案。
- 驗證最低限度依變更類型調整：後端行為執行對應 pytest 與必要的 Ruff；前端 TypeScript、組件、樣式或 API 型別變更執行 lint、測試或 `pnpm build`；前後端契約變更同時驗證兩端。涉及台股計算時，固定樣本與邊界案例 MUST 證明日期、單位、空值與降級路徑正確。
- 使用者可見的改動 MUST 檢查常用桌面與窄屏下的載入、空、錯、切換、刷新與權限狀態；文件、CI 與實作的驗證結論 MUST 以實際執行結果為準。

## Governance

本 constitution 定義產品設計、架構取捨與實作品質的長期原則。`AGENTS.md`、`CONTRIBUTING.md`、領域文件、資料契約與程式碼中仍然有效且更具體的規則，於具體情境下優先適用；constitution 不重複完整的 Git、commit、push、PR、CI、Codex Review 或 merge 流程。

修訂 MUST 直接更新本檔，並在檔案頂端的 Sync Impact Report 說明版本變更、原則異動、增刪章節與待辦事項。修訂者 MUST 同時檢查新原則與現有架構、測試及專案規則是否一致，完成相稱驗證後才可採用。版本依語意化規則遞增：移除或重新定義既有原則等不相容治理變更使用 MAJOR；新增原則或大幅擴充要求使用 MINOR；澄清文字、修正錯誤或非語意調整使用 PATCH。

每次設計或複審涉及本 constitution 的工作，都 MUST 檢查需求是否仍有端到端價值、是否引入不必要複雜度、是否破壞既有資料與服務邊界、是否具備相稱驗證，以及台股計算是否保留 point-in-time 正確性。偏離原則時 MUST 在對應設計或變更說明中記錄具體原因、影響與降級或回復方式。

**Version**: 1.0.0 | **Ratified**: 2026-09-28 | **Last Amended**: 2026-09-28

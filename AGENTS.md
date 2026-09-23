# AI 開發入口

修改、調試或審查本倉庫前，必須完整閱讀並遵循 [`CONTRIBUTING.md`](CONTRIBUTING.md) 的架構、數據契約、測試與審查規範。例行交付流程依本檔及使用者當次要求執行。

涉及代碼二次開發、前端插槽、後端可替換策略、擴展註冊或上游升級兼容時，還必須閱讀 [`docs/secondary-development.md`](docs/secondary-development.md)。該文檔區分當前已實現能力與目標擴展契約；不得根據設計示例虛構尚不存在的 API。

## 實作原則

- 先理解調用鏈與現有測試，再做最小範圍修改；保留工作區既有變更。
- 執行適用的本機測試，據實回報結果；不得虛構 CI 或審查狀態。

## 預設交付流程

一般功能開發、修正與重構，在使用者要求完成交付且未指定其他流程時：

1. 本機實作與驗證後，建立 feature / fix branch，commit、push，向主分支建立 PR。
2. 等待既有 GitHub Actions / CI checks，優先請 Codex GitHub Code Review 審查 PR；避免在本機重複完整 PR review。CI 負責測試、lint、typecheck、build 等機械驗證；Review 負責邏輯、回歸、邊界、錯誤處理、安全與契約問題。
3. 對 CI 或 Review 的明確問題，在同一 branch 修正、測試、push 並重新確認結果。只有 checks 通過、Review 無待處理 blocker 且 working tree clean，才以 squash merge 為優先方式合併。
4. 合併後切回主分支，執行 `git pull --ff-only`，清理已完成的 feature branch，確認 local main 與 origin/main 一致。

- 不以 `--admin` 繞過 required checks；不 force push。若尚無 CI 且已有穩定測試套件，補最小的依賴安裝與主要測試 workflow；當次仍須完成本機驗證。若 Codex GitHub Review 未啟用，據實回報未執行。
- 本機額外 review 僅用於診斷 CI / Review 問題、高風險變更或 GitHub Review 無法涵蓋的環境問題。history rewrite、大量刪除、資料 migration、正式 release、權限、secrets、安全性及使用者資料風險，先提高驗證與確認層級。

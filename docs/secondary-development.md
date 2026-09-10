# 代碼二次開發與 AI 擴展指南

本文面向需要在當前倉庫中二次開發的維護者、團隊和 AI 編碼代理。目標不是禁止修改源碼，而是讓新增頁面、業務規則和定製邏輯儘量通過穩定邊界接入，使後續合併上游版本時衝突更少、風險可驗證。

修改任何代碼前，仍須先完整閱讀根目錄的 [`CONTRIBUTING.md`](../CONTRIBUTING.md)。金融數據口徑、緩存、併發、數據源和測試要求以該文檔為準。

## 1. 文檔狀態

本文同時描述現有能力和後續按需建設的代碼擴展契約。兩者不能混用：

| 狀態 | 含義 |
| --- | --- |
| 已可用 | 當前倉庫中已經存在，可以在確認調用鏈後直接複用 |
| 按需擴展 | 尚未實現；出現真實用例後才能增加，不能提前假設 API 存在 |

當前已可用的主要擴展能力：

- 自定義、AI 和疊加策略目錄：`data/strategies/`。
- 數據源 Provider 與 `plugin.yaml` 機制：詳見 [`plugin-development.md`](plugin-development.md)。
- 擴展數據與聲明式分析頁面：適合不需要自定義 React 交互的頁面。
- 前端源碼擴展註冊：`frontend/src/custom/<namespace>/extension.tsx`，支持靜態頁面、導航和已開放插槽。
- 後端源碼擴展註冊：`backend/app/custom/<module>.py`，支持 FastAPI 路由、啟動鉤子和通知格式化器。
- 當前前端插槽：`layout.navigation.extra`、`stock-preview.footer`、`watchlist.toolbar`。
- 當前後端繼承點：`NotificationFormatter`。
- 台灣市場官方數據統一經 `app/taiwan` 既有 adapter → normalizer/model → consumer
  路徑接入；法人與融資融券須擴展 `enrichment` 現有 parser，不得另建平行 provider。

台灣官方法人／融資融券的二開邊界：TWSE T86、TPEx dailyTrade、TWSE
MI_MARGN 與 TPEx margin/balance 只在既有 adapter 解析；raw 數量統一為 shares，
來源、URL、抓取時間、交易日與 Contract status 保存在每筆 record 的 `SourceMeta`。
FinMind 當前沒有可靠的法人／融資融券 adapter，因此官方失敗必須顯式拋錯，不能
以空結果或 Yahoo 抓取偽裝 fallback。滾動買賣超與券資比屬於 `enrichment/factors.py`
的 derived factor，不應放回 raw provider。

尚未實現、只能在真實需求出現後增加的能力：

- 更多頁面局部插槽。
- 候選過濾、評分、倉位、風控和回測成本等後端業務策略接口。
- 配置 schema 遷移註冊表。

AI 在開始任務前必須通過代碼搜索確認能力是否已經實現。找不到定義和測試時，應把示例視為設計規範，不得虛構導入路徑或調用結果。

## 2. 二次開發分級

按升級風險從低到高選擇實現方式：

| 級別 | 實現方式 | 適用場景 | 升級風險 |
| --- | --- | --- | --- |
| L1 | 配置、策略文件、擴展數據 | 已有契約能夠完成需求 | 最低 |
| L2 | 前端插槽、路由註冊；後端策略接口、註冊替換 | 新頁面、局部 UI、可替換業務規則 | 較低 |
| L3 | 直接修改核心源碼 | 核心流程本身必須變化，現有擴展點無法表達 | 最高 |

選擇原則：

1. 先確認現有功能能否複用，禁止平行實現第二套數據、策略、緩存或請求邏輯。
2. 只在存在真實二開需求的位置增加擴展點，不為未來可能出現的需求預埋通用框架。
3. 插槽或接口無法表達核心行為變化時，可以修改源碼；必須縮小改動範圍並補回歸測試。
4. 不為了避開一次衝突複製完整頁面、服務或引擎。複製會把一次顯式衝突變成長期的隱式分叉。

## 3. 前端擴展規範

### 3.1 何時使用插槽

插槽適合在既有頁面中增加局部內容：

- 操作按鈕或工具欄命令。
- 篩選條件或表單字段。
- 表格列和詳情面板。
- 個股詳情、策略詳情中的附加標籤頁。
- 設置頁面中的獨立配置區。

新增完整頁面時應使用路由和導航註冊，不要把整頁塞入某個插槽。改變核心頁面的數據流、狀態模型或主要佈局時，應直接修改核心代碼並按 L3 管理。

### 3.2 當前插槽契約

核心頁面通過已經實現的 `ExtensionSlot` 提供受控上下文：

```tsx
<ExtensionSlot
  name="strategy.monitor.filters"
  context={{
    apiVersion: 1,
    rule: draft,
    updateRule,
    readOnly,
  }}
/>
```

二開模塊通過默認導出的註冊清單接入，不修改核心文件：

```tsx
import type { FrontendExtension } from '@/extensions/types'

function NavigationExtra({ collapsed }: { collapsed: boolean; pathname: string }) {
  return collapsed ? null : <div>二開內容</div>
}

const extension: FrontendExtension = {
  id: 'company.navigation',
  apiVersion: 1,
  slots: [{
    name: 'layout.navigation.extra',
    id: 'company-summary',
    order: 100,
    component: NavigationExtra,
  }],
}

export default extension
```

插槽設計必須滿足：

- `name` 和註冊項 `id` 全局穩定、唯一。
- `context` 使用明確的 TypeScript 類型，幷包含契約版本。
- 只暴露完成該插槽職責所需的數據和操作，不傳遞整個頁面狀態。
- 插槽通過公開回調修改狀態，不直接訪問父組件內部 store 或緩存。
- 單個擴展渲染失敗應由錯誤邊界隔離，並顯示可定位的擴展 ID。
- 註冊順序確定，使用 `order` 後再按 `id` 排序，避免加載順序導致界面漂移。
- 插槽內容必須遵守項目現有設計系統、響應式和可訪問性要求。

當前開放：

```text
layout.navigation.extra
stock-preview.footer
watchlist.toolbar
```

各插槽 context 契約（均要求 `apiVersion: 1`，定義見 `frontend/src/extensions/types.ts` 的 `FrontendSlotContextMap`）：

- `layout.navigation.extra`：`{ collapsed, pathname }`，側邊欄導航底部。
- `stock-preview.footer`：`{ symbol, name, view }`，個股詳情對話框底部（日K/分時圖表下方）；`view` 為 `'daily' | 'intraday'`。適合個股附加面板：龍虎榜、資金流、外部研究鏈接等。
- `watchlist.toolbar`：`{ symbols, viewMode, selectedGroup, refresh }`，自選頁工具欄末尾；`symbols` 為當前篩選視圖中的標的，`refresh` 在擴展修改數據後調用以刷新自選增強數據。適合批量操作入口：自定義分析、導出、組合計算等。

新增插槽前必須有真實用例，並同時定義 context 類型、異常隔離和測試；不能只在類型表中預留名字。

### 3.3 當前路由與導航契約

完整頁面通過註冊表接入：

```tsx
const extension: FrontendExtension = {
  id: 'company.risk',
  apiVersion: 1,
  routes: [
    { id: 'company-risk', path: '/company/risk', component: CompanyRiskPage },
  ],
  navigation: [
    {
      id: 'company-risk',
      routeId: 'company-risk',
      label: '風險分析',
      icon: ShieldCheck,
      order: 500,
    },
  ],
}
```

路由註冊與菜單註冊已經解耦：頁面可以存在但不顯示在菜單中；菜單隻能引用同一擴展內的已註冊路由。首版只支持靜態絕對路徑。擴展路由不得覆蓋核心或其他擴展路徑，衝突時只禁用該擴展並輸出明確錯誤。

完整前端模板位於 [`frontend/src/custom/_template/extension.tsx.example`](../frontend/src/custom/_template/extension.tsx.example)。

### 3.4 前端禁止事項

- 不直接在多個組件中拼接後端 URL，統一使用 `frontend/src/lib/api.ts` 或未來公開客戶端。
- 不自行創建與現有 TanStack Query 重複的緩存；查詢鍵仍由 `queryKeys.ts` 集中管理。
- 不用插槽繞過權限、數據口徑或表單校驗。
- 不通過 DOM 查詢、全局事件或 monkey patch 修改核心組件。
- 不把整個核心頁面複製到二開目錄後長期獨立維護。

## 4. 後端擴展規範

### 4.1 使用小粒度繼承

後端允許二開類繼承穩定、職責單一的抽象基類，再通過註冊表或依賴注入接入。不要繼承並覆蓋大型編排服務。

當前已經實現的繼承點：

- `NotificationFormatter`：在監控規則完成評估後統一調整通知文案，不改變事件結構和觸發語義。

下列是可能適合的小粒度接口，但目前沒有實現，不能直接導入：

- `CandidateFilter`：候選池過濾。
- `ScoringPolicy`：評分計算。
- `PositionSizingPolicy`：倉位計算。
- `RiskPolicy`：風險約束。
- `NotificationFormatter`：通知文案。
- `StrategyProvider`：策略發現。
- `MonitorConditionEvaluator`：自定義監控條件。
- `BacktestCostModel`：手續費和滑點模型。

不適合作為公共繼承點的核心類：

- `StrategyEngine`。
- `BacktestEngine`。
- `ScreenerService`。
- `StrategyMonitorService`。
- `DataStore` 和倉庫實現。
- FastAPI 主應用及生命週期函數。

這些類管理流程、緩存、併發或生命週期。覆蓋其中的內部方法會讓上游調整執行順序後產生難以發現的語義錯誤。

### 4.2 當前基類與註冊契約

後端模塊從 `app.extensions` 導入穩定契約：

```python
from app.extensions import (
    BACKEND_EXTENSION_API_VERSION,
    BackendExtensionRegistrar,
    NotificationFormatContext,
    NotificationFormatter,
)

EXTENSION_ID = 'company.notice'
EXTENSION_API_VERSION = BACKEND_EXTENSION_API_VERSION


class CompanyNotificationFormatter(NotificationFormatter):
    def format_message(self, event: dict, context: NotificationFormatContext) -> str:
        return f"[公司規則] {event.get('message', '')}".strip()


def setup(registrar: BackendExtensionRegistrar) -> None:
    registrar.register_notification_formatter(
        'company.notification',
        CompanyNotificationFormatter(),
    )
```

同一個 `setup` 可以通過 `registrar.include_router(router)` 註冊獨立 FastAPI 路由。核心路由衝突、重複 ID 或契約版本不匹配時，該擴展整體不註冊，不留下半註冊狀態。

核心數據層初始化完成後，可選的 `startup(context: ExtensionContext)` 會收到數據目錄和只讀倉庫協議。啟動鉤子失敗只記錄錯誤，不阻止主程序啟動。

完整後端模板位於 [`backend/app/custom/_template.py.example`](../backend/app/custom/_template.py.example)。

### 4.3 後端契約要求

- 上下文優先使用不可變 `dataclass` 或 `Protocol`，不向擴展暴露整個 `app.state`。
- 抽象方法參數、返回值、單位、空值和異常行為必須有文檔及契約測試。
- 註冊 ID 全局唯一；重複註冊默認拒絕，不允許靜默覆蓋官方實現。
- 默認實現必須存在。沒有啟用二開實現時，核心行為與當前版本一致。
- 單個可選實現加載失敗時應禁用自身；金融結果無法可靠計算時必須 fail-closed。
- 註冊表在啟動完成後凍結，實時線程中不得動態替換實現。
- 破壞性契約變化必須提升 `api_version`，舊版本至少保留一個大版本的兼容期。

### 4.4 繼承與組合的邊界

繼承只用於表達穩定的“是一種策略實現”關係。需要同時組合過濾、評分、通知等能力時，分別註冊多個小實現，不創建擁有大量可選方法的萬能基類。

優先組合的場景：

- 一個服務需要多個獨立規則。
- 行為需要按資產類型或運行上下文選擇。
- 擴展只需要裝飾默認結果，而不是完全替換算法。
- 依賴緩存、倉庫或通知服務，需要通過明確構造參數注入。

## 5. 直接修改源碼

擴展點不是限制。核心流程必須變化時允許直接修改源碼，但要把升級成本顯式管理。

### 5.1 修改要求

- 一個提交只包含一個二開目的，不混入格式化、依賴升級和無關重構。
- 優先新增獨立模塊，再對核心入口做最小接線。
- 修改公共契約時同時更新後端模型、前端類型、調用方和測試。
- 修改數據寫入時列出持久化、內存緩存、版本、SSE 和前端查詢失效鏈路。
- 保留舊配置和舊數據讀取能力；必須遷移時提供冪等遷移和回滾說明。
- 在 PR 描述中標記被修改的核心熱點及未來合併上游時的複核點。

### 5.2 高衝突熱點

以下文件集中管理啟動、路由或公共契約，直接修改時需要重點複核：

```text
backend/app/main.py
backend/app/strategy/engine.py
backend/app/backtest/engine.py
frontend/src/router.tsx
frontend/src/components/Layout.tsx
frontend/src/lib/api.ts
frontend/src/lib/queryKeys.ts
```

高衝突不代表禁止修改，而是要求改動更小、測試更完整。若多個二開需求反覆修改同一熱點，應把共同接線能力提升為正式插槽或策略接口。

## 6. AI 開發工作流

AI 必須按以下順序工作：

1. 完整閱讀 `AGENTS.md`、`CONTRIBUTING.md` 和本文。
2. 檢查 `git status`，保留工作區已有修改。
3. 搜索目標調用鏈、相鄰實現、現有擴展點和測試。
4. 明確需求屬於 L1、L2 還是 L3，並說明選擇依據。
5. 判斷目標插槽或後端基類是否真實存在，不依據本文示例虛構代碼。
6. 寫出最小改動計劃和完成標準。
7. 先補能證明行為的測試，再實施必要改動。
8. 執行對應驗證矩陣，檢查最終 diff 和兼容性。

### 6.1 可直接使用的任務模板

```text
請在 Nanachi 的台股監控看板 當前倉庫中實現：[具體需求]。

開始前完整閱讀 AGENTS.md、CONTRIBUTING.md 和
docs/secondary-development.md，並先檢查 git status、真實調用鏈和現有測試。

約束：
1. 先判斷現有功能能否複用，並將方案歸類為 L1/L2/L3。
2. 前端優先使用已經存在的受控插槽、路由或導航註冊；後端優先使用已經存在的
   小粒度抽象基類和註冊機制。必須用搜索和測試證明接口真實存在，不能根據設計文檔
   虛構 API。
3. 若擴展點尚未實現，先說明最小可行方案；只有該需求確實需要時才新增擴展點。
4. 允許直接修改源碼，但保持改動最小，不複製完整頁面或核心服務。
5. 複用現有 API、數據倉庫、緩存、查詢鍵、組件和領域口徑，不創建第二套邏輯。
6. 保持歷史配置和數據兼容，擴展失敗不能破壞未啟用擴展的主流程。
7. 不覆蓋已有修改，不提交、不推送，除非我單獨確認。

完成後請列出：
- 方案分級及原因；
- 修改文件和關鍵契約；
- 對緩存、數據、API 和升級兼容性的影響；
- 實際執行的測試、構建和結果；
- 仍需人工確認的風險。
```

### 6.2 讓 AI 設計擴展點的模板

```text
請只設計並評審以下二次開發需求的擴展邊界，暫不修改代碼：[具體需求]。

請基於當前倉庫真實調用鏈回答：
1. 現有能力是否已經可以實現；
2. 前端應使用局部插槽、路由註冊還是直接改源碼；
3. 後端應使用哪個小粒度策略接口，為什麼不繼承大型核心服務；
4. 最小 context/Protocol 應包含哪些字段；
5. 默認實現、失敗隔離、契約版本和測試如何設計；
6. 哪些抽象屬於當前不需要的過度設計。

不要假設本文中的目標 API 已經實現，請給出代碼證據和文件位置。
```

## 7. 驗證矩陣

除 `CONTRIBUTING.md` 的通用要求外，二開還應按接入方式驗證：

| 改動 | 最低驗證 |
| --- | --- |
| 前端插槽 | 無註冊、單註冊、多註冊排序、異常隔離、窄屏、前端構建 |
| 路由/導航註冊 | 路徑衝突、隱藏頁面、無權限、直接刷新、未知路由 |
| 後端策略實現 | 默認實現、二開實現、重複 ID、版本不兼容、加載失敗隔離 |
| 配置或契約變化 | 舊字段缺失、未知字段、更高版本拒寫、遷移冪等 |
| 直接修改核心 | 受影響模塊完整迴歸、緩存失效、歷史配置、前後端聯調 |

常用命令：

```bash
cd backend
uv run --frozen pytest tests/path/to/test_x.py -q
uv run --frozen ruff check app/path.py tests/path.py

cd ../frontend
pnpm build

cd ..
git diff --check
git status --short --branch
```

不得把“擴展已加載”當作業務驗證。測試必須斷言真實過濾結果、評分、路由輸出、界面狀態或失敗隔離行為。

## 8. 版本與升級約定

- 二開分支應記錄開始開發時的上游 Git Tag 或 commit，不能只寫“基於 v0.x”。
- 正式發佈使用不可變 Tag；二開升級優先合併 Tag，而不是持續變化的開發分支頭。
- 公共插槽和後端擴展接口使用獨立的 `api_version`，不要直接等同應用版本。
- 同一 `api_version` 內只做向後兼容的字段新增；刪除、改名或改變語義必須提升主版本。
- 廢棄字段先標記並保留兼容讀取，至少跨一個大版本後再移除。
- 升級後必須重新運行二開契約測試，不能只依賴 Git 顯示“無衝突”。

直接修改核心源碼的二開分支可在升級前運行只讀預檢：

```bash
python3 scripts/upgrade_check.py <目標Tag或分支>
```

腳本不會執行 merge、修改索引或工作區。它會報告共同基線、雙方修改的同一文件以及 Git 三方預演可識別的文本衝突。未提交內容不會進入預演，因此正式評估前應先提交到臨時二開分支。

## 9. 完成檢查表

- [ ] 已確認需求屬於 L1、L2 或 L3。
- [ ] 已證明使用的插槽、基類和註冊 API 在當前代碼中真實存在。
- [ ] 沒有複製已有數據讀取、緩存、API 客戶端或完整核心頁面。
- [ ] 前端擴展只獲得必要 context，後端沒有繼承大型編排服務。
- [ ] 默認實現和未啟用二開時的行為保持不變。
- [ ] 重複註冊、加載失敗、版本不兼容和空數據均有明確行為。
- [ ] 歷史配置、策略和用戶數據仍可讀取。
- [ ] 已執行適用的測試、構建、Ruff 和 `git diff --check`。
- [ ] 最終說明包含升級風險和未來合併上游時的複核點。

## 10. 後續擴展原則

統一註冊基礎設施已經完成。後續只按真實業務需求增加能力：

1. 頁面需要局部定製時，在真實位置增加一個類型化插槽及測試。
2. 後端業務規則需要替換時，從該調用鏈提取一個小粒度接口、默認實現和契約測試。
3. 不繼承大型編排服務，不暴露整個 `app.state`，不複製核心流程。
4. 只有出現需要持久化的二開配置後，再增加 schema 遷移註冊表。
5. 需要升級直接修改源碼的分支時，使用 `scripts/upgrade_check.py` 預檢，再執行真實合併和迴歸。

這種順序遵循 KISS 和 YAGNI：先解決已經存在的升級衝突，不提前建設完整插件平台，也不阻止開發者在必要時直接修改源碼。

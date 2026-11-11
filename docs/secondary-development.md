# 程式碼二次開發與 AI 擴充指南

本文面向需要在當前儲存庫中二次開發的維護者、團隊和 AI 編碼代理。目標不是禁止修改原始碼，而是讓新增頁面、業務規則和定製邏輯儘量透過穩定邊界接入，使後續合併上游版本時衝突更少、風險可驗證。

修改任何程式碼前，仍須先完整閱讀根目錄的 [`CONTRIBUTING.md`](../CONTRIBUTING.md)。金融資料定義、快取、併發、資料源和測試要求以該文件為準。

## 1. 文件狀態

本文同時描述現有能力和後續按需建設的程式碼擴充契約。兩者不能混用：

| 狀態 | 含義 |
| --- | --- |
| 已可用 | 當前儲存庫中已經存在，可以在確認呼叫鏈後直接重用 |
| 按需擴充 | 尚未實作；出現真實用例後才能增加，不能提前假設 API 存在 |

當前已可用的主要擴充能力：

- 前端原始碼擴充註冊：`frontend/src/custom/<namespace>/extension.tsx`，支援靜態頁面、導覽和已開放插槽。
- 後端原始碼擴充註冊：`backend/app/custom/<module>.py`，支援 FastAPI 路由、啟動鉤子和通知格式化器。
- 當前前端插槽：`layout.navigation.extra`、`watchlist.toolbar`。
- 當前後端繼承點：`NotificationFormatter`。
- 台灣市場官方資料統一經 `app/taiwan` 既有 adapter → normalizer/model → consumer
  路徑接入；法人與融資融券須擴充 `enrichment` 現有 parser，不得另建平行 provider。

台灣官方法人／融資融券的二開邊界：TWSE T86、TPEx dailyTrade、TWSE
MI_MARGN 與 TPEx margin/balance 只在既有 adapter 解析；raw 數量統一為 shares，
來源、URL、抓取時間、交易日與 Contract status 儲存在每筆 record 的 `SourceMeta`。
FinMind 當前沒有可靠的法人／融資融券 adapter，因此官方失敗必須顯式拋錯，不能
以空結果或 Yahoo 抓取偽裝 fallback。滾動買賣超與券資比屬於 `enrichment/factors.py`
的 derived factor，不應放回 raw provider。

尚未實作、只能在真實需求出現後增加的能力：

- 更多頁面局部插槽。
- 候選過濾、評分、倉位、風控和交易成本等後端業務策略介面。
- 設定 schema 遷移註冊表。

AI 在開始任務前必須透過程式碼搜尋確認能力是否已經實作。找不到定義和測試時，應把範例視為設計規範，不得虛構匯入路徑或呼叫結果。

## 2. 二次開發分級

按升級風險從低到高選擇實作方式：

| 級別 | 實作方式 | 適用場景 | 升級風險 |
| --- | --- | --- | --- |
| L1 | 設定、策略文件、擴充資料 | 已有契約能夠完成需求 | 最低 |
| L2 | 前端插槽、路由註冊；後端策略介面、註冊替換 | 新頁面、局部 UI、可替換業務規則 | 較低 |
| L3 | 直接修改核心原始碼 | 核心流程本身必須變化，現有擴充點無法表達 | 最高 |

選擇原則：

1. 先確認現有功能能否重用，禁止平行實作第二套資料、策略、快取或請求邏輯。
2. 只在存在真實二開需求的位置增加擴充點，不為未來可能出現的需求預埋通用框架。
3. 插槽或介面無法表達核心行為變化時，可以修改原始碼；必須縮小改動範圍並補回歸測試。
4. 不為了避開一次衝突複製完整頁面、服務或引擎。複製會把一次顯式衝突變成長期的隱式分叉。

## 3. 前端擴充規範

### 3.1 何時使用插槽

插槽適合在既有頁面中增加局部內容：

- 操作按鈕或工具欄命令。
- 篩選條件或表單欄位。
- 表格列和詳情面板。
- 個股詳情、策略詳情中的附加分頁。
- 設定頁面中的獨立設定區。

新增完整頁面時應使用路由和導覽註冊，不要把整頁塞入某個插槽。改變核心頁面的資料流、狀態模型或主要佈局時，應直接修改核心程式碼並按 L3 管理。

### 3.2 當前插槽契約

核心頁面透過已經實作的 `ExtensionSlot` 提供受控上下文：

```tsx
<ExtensionSlot
  name="layout.navigation.extra"
  context={{
    apiVersion: 1,
    collapsed,
    pathname,
  }}
/>
```

二開模組透過預設匯出的註冊清單接入，不修改核心文件：

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
- `context` 使用明確的 TypeScript 類型，並包含契約版本。
- 只暴露完成該插槽職責所需的資料和操作，不傳遞整個頁面狀態。
- 插槽透過公開回呼修改狀態，不直接存取父元件內部 store 或快取。
- 單個擴充渲染失敗應由錯誤邊界隔離，並顯示可定位的擴充 ID。
- 註冊順序確定，使用 `order` 後再按 `id` 排序，避免載入順序導致介面漂移。
- 插槽內容必須遵守專案現有設計系統、響應式和可存取性要求。

當前開放：

```text
layout.navigation.extra
watchlist.toolbar
```

各插槽 context 契約（均要求 `apiVersion: 1`，定義見 `frontend/src/extensions/types.ts` 的 `FrontendSlotContextMap`）：

- `layout.navigation.extra`：`{ collapsed, pathname }`，側邊欄導覽底部。
- `watchlist.toolbar`：`{ symbols, viewMode, selectedGroup, refresh }`，自選頁工具欄末尾；`symbols` 為當前篩選檢視中的標的，`refresh` 在擴充修改資料後呼叫以重新整理自選增強資料。適合批次操作入口：自訂分析、匯出、組合計算等。

新增插槽前必須有真實用例，並同時定義 context 類型、異常隔離和測試；不能只在類型表中預留名字。

### 3.3 當前路由與導覽契約

完整頁面透過註冊表接入：

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

路由註冊與選單註冊已經解耦：頁面可以存在但不顯示在選單中；選單隻能引用同一擴充內的已註冊路由。首版只支援靜態絕對路徑。擴充路由不得覆蓋核心或其他擴充路徑，衝突時只禁用該擴充並輸出明確錯誤。

完整前端範本位於 [`frontend/src/custom/_template/extension.tsx.example`](../frontend/src/custom/_template/extension.tsx.example)。

### 3.4 前端禁止事項

- 不直接在多個元件中拼接後端 URL，統一使用 `frontend/src/lib/api.ts` 或未來公開用戶端。
- 不自行建立與現有 TanStack Query 重複的快取；查詢鍵仍由 `queryKeys.ts` 集中管理。
- 不用插槽繞過權限、資料定義或表單驗證。
- 不通過 DOM 查詢、全局事件或 monkey patch 修改核心元件。
- 不把整個核心頁面複製到二開目錄後長期獨立維護。

## 4. 後端擴充規範

### 4.1 使用小粒度繼承

後端允許二開類繼承穩定、職責單一的抽象基類，再透過註冊表或依賴注入接入。不要繼承並覆蓋大型編排服務。

當前已經實作的繼承點：

- `NotificationFormatter`：在監控規則完成評估後統一調整通知文案，不改變事件結構和觸發語意。

下列是可能適合的小粒度介面，但目前沒有實作，不能直接匯入：

- `CandidateFilter`：候選池過濾。
- `ScoringPolicy`：評分計算。
- `PositionSizingPolicy`：倉位計算。
- `RiskPolicy`：風險約束。
- `NotificationFormatter`：通知文案。
- `StrategyProvider`：策略發現。
- `MonitorConditionEvaluator`：自訂監控條件。
- `TradingCostModel`：手續費、證交稅和滑價模型。

不適合作為公共繼承點的核心類：

- `TaiwanScreenerService`。
- 儲存庫實作（`repository.py`）。
- FastAPI 主應用及生命週期函式。

這些類管理流程、快取、併發或生命週期。覆蓋其中的內部方法會讓上游調整執行順序後產生難以發現的語意錯誤。

### 4.2 當前基類與註冊契約

後端模組從 `app.extensions` 匯入穩定契約：

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

同一個 `setup` 可以透過 `registrar.include_router(router)` 註冊獨立 FastAPI 路由。核心路由衝突、重複 ID 或契約版本不匹配時，該擴充整體不註冊，不留下半註冊狀態。

核心資料層初始化完成後，可選的 `startup(context: ExtensionContext)` 會收到資料目錄和唯讀儲存庫協議。啟動鉤子失敗只記錄錯誤，不阻止主程序啟動。

完整後端範本位於 [`backend/app/custom/_template.py.example`](../backend/app/custom/_template.py.example)。

### 4.3 後端契約要求

- 上下文優先使用不可變 `dataclass` 或 `Protocol`，不向擴充暴露整個 `app.state`。
- 抽象方法參數、回傳值、單位、空值和異常行為必須有文件及契約測試。
- 註冊 ID 全局唯一；重複註冊預設拒絕，不允許靜默覆蓋官方實作。
- 預設實作必須存在。沒有啟用二開實作時，核心行為與當前版本一致。
- 單個可選實作載入失敗時應禁用自身；金融結果無法可靠計算時必須 fail-closed。
- 註冊表在啟動完成後凍結，即時執行緒中不得動態替換實作。
- 破壞性契約變化必須提升 `api_version`，舊版本至少保留一個大版本的相容期。

### 4.4 繼承與組合的邊界

繼承只用於表達穩定的“是一種策略實作”關係。需要同時組合過濾、評分、通知等能力時，分別註冊多個小實作，不建立擁有大量可選方法的萬能基類。

優先組合的場景：

- 一個服務需要多個獨立規則。
- 行為需要按資產類型或執行上下文選擇。
- 擴充只需要裝飾預設結果，而不是完全替換演算法。
- 依賴快取、儲存庫或通知服務，需要透過明確構造參數注入。

## 5. 直接修改原始碼

擴充點不是限制。核心流程必須變化時允許直接修改原始碼，但要把升級成本顯式管理。

### 5.1 修改要求

- 一個提交只包含一個二開目的，不混入格式化、依賴升級和無關重構。
- 優先新增獨立模組，再對核心入口做最小接線。
- 修改公共契約時同時更新後端模型、前端類型、呼叫方和測試。
- 修改資料寫入時列出持久化、記憶體快取、版本、SSE 和前端查詢失效鏈路。
- 保留舊設定和舊資料讀取能力；必須遷移時提供冪等遷移和還原說明。
- 在 PR 描述中標記被修改的核心熱點及未來合併上游時的複核點。

### 5.2 高衝突熱點

以下文件集中管理啟動、路由或公共契約，直接修改時需要重點複核：

```text
backend/app/main.py
frontend/src/router.tsx
frontend/src/components/Layout.tsx
frontend/src/lib/api.ts
frontend/src/lib/queryKeys.ts
```

高衝突不代表禁止修改，而是要求改動更小、測試更完整。若多個二開需求反覆修改同一熱點，應把共同接線能力提升為正式插槽或策略介面。

## 6. AI 開發工作流

AI 必須按以下順序工作：

1. 完整閱讀 `AGENTS.md`、`CONTRIBUTING.md` 和本文。
2. 檢查 `git status`，保留工作區已有修改。
3. 搜尋目標呼叫鏈、相鄰實作、現有擴充點和測試。
4. 明確需求屬於 L1、L2 還是 L3，並說明選擇依據。
5. 判斷目標插槽或後端基類是否真實存在，不依據本文範例虛構程式碼。
6. 寫出最小改動計劃和完成標準。
7. 先補能證明行為的測試，再實施必要改動。
8. 執行對應驗證矩陣，檢查最終 diff 和相容性。

### 6.1 可直接使用的任務範本

```text
請在 Nanachi 的台股監控看板 當前儲存庫中實作：[具體需求]。

開始前完整閱讀 AGENTS.md、CONTRIBUTING.md 和
docs/secondary-development.md，並先檢查 git status、真實呼叫鏈和現有測試。

約束：
1. 先判斷現有功能能否重用，並將方案歸類為 L1/L2/L3。
2. 前端優先使用已經存在的受控插槽、路由或導覽註冊；後端優先使用已經存在的
   小粒度抽象基類和註冊機制。必須用搜尋和測試證明介面真實存在，不能根據設計文件
   虛構 API。
3. 若擴充點尚未實作，先說明最小可行方案；只有該需求確實需要時才新增擴充點。
4. 允許直接修改原始碼，但保持改動最小，不複製完整頁面或核心服務。
5. 重用現有 API、資料儲存庫、快取、查詢鍵、元件和領域定義，不建立第二套邏輯。
6. 保持歷史設定和資料相容，擴充失敗不能破壞未啟用擴充的主流程。
7. 不覆蓋已有修改，不提交、不推送，除非我單獨確認。

完成後請列出：
- 方案分級及原因；
- 修改文件和關鍵契約；
- 對快取、資料、API 和升級相容性的影響；
- 實際執行的測試、建置和結果；
- 仍需人工確認的風險。
```

### 6.2 讓 AI 設計擴充點的範本

```text
請只設計並評審以下二次開發需求的擴充邊界，暫不修改程式碼：[具體需求]。

請基於當前儲存庫真實呼叫鏈回答：
1. 現有能力是否已經可以實作；
2. 前端應使用局部插槽、路由註冊還是直接改原始碼；
3. 後端應使用哪個小粒度策略介面，為什麼不繼承大型核心服務；
4. 最小 context/Protocol 應包含哪些欄位；
5. 預設實作、失敗隔離、契約版本和測試如何設計；
6. 哪些抽象屬於當前不需要的過度設計。

不要假設本文中的目標 API 已經實作，請給出程式碼證據和文件位置。
```

## 7. 驗證矩陣

除 `CONTRIBUTING.md` 的通用要求外，二開還應按接入方式驗證：

| 改動 | 最低驗證 |
| --- | --- |
| 前端插槽 | 無註冊、單註冊、多註冊排序、異常隔離、窄屏、前端建置 |
| 路由/導覽註冊 | 路徑衝突、隱藏頁面、無權限、直接重新整理、未知路由 |
| 後端策略實作 | 預設實作、二開實作、重複 ID、版本不相容、載入失敗隔離 |
| 設定或契約變化 | 舊欄位缺失、未知欄位、更高版本拒寫、遷移冪等 |
| 直接修改核心 | 受影響模組完整迴歸、快取失效、歷史設定、前後端聯調 |

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

不得把“擴充已載入”當作業務驗證。測試必須斷言真實過濾結果、評分、路由輸出、介面狀態或失敗隔離行為。

## 8. 版本與升級約定

- 二開分支應記錄開始開發時的上游 Git Tag 或 commit，不能只寫“基於 v0.x”。
- 正式發佈使用不可變 Tag；二開升級優先合併 Tag，而不是持續變化的開發分支頭。
- 公共插槽和後端擴充介面使用獨立的 `api_version`，不要直接等同應用版本。
- 同一 `api_version` 內只做向後相容的欄位新增；刪除、改名或改變語意必須提升主版本。
- 廢棄欄位先標記並保留相容讀取，至少跨一個大版本後再移除。
- 升級後必須重新執行二開契約測試，不能只依賴 Git 顯示“無衝突”。

直接修改核心原始碼的二開分支可在升級前執行唯讀預檢：

```bash
python3 scripts/upgrade_check.py <目標Tag或分支>
```

腳本不會執行 merge、修改索引或工作區。它會報告共同基線、雙方修改的同一文件以及 Git 三方預演可識別的文字衝突。未提交內容不會進入預演，因此正式評估前應先提交到臨時二開分支。

## 9. 完成檢查表

- [ ] 已確認需求屬於 L1、L2 或 L3。
- [ ] 已證明使用的插槽、基類和註冊 API 在當前程式碼中真實存在。
- [ ] 沒有複製已有資料讀取、快取、API 用戶端或完整核心頁面。
- [ ] 前端擴充只獲得必要 context，後端沒有繼承大型編排服務。
- [ ] 預設實作和未啟用二開時的行為保持不變。
- [ ] 重複註冊、載入失敗、版本不相容和空資料均有明確行為。
- [ ] 歷史設定、策略和使用者資料仍可讀取。
- [ ] 已執行適用的測試、建置、Ruff 和 `git diff --check`。
- [ ] 最終說明包含升級風險和未來合併上游時的複核點。

## 10. 後續擴充原則

統一註冊基礎設施已經完成。後續只按真實業務需求增加能力：

1. 頁面需要局部定製時，在真實位置增加一個類型化插槽及測試。
2. 後端業務規則需要替換時，從該呼叫鏈提取一個小粒度介面、預設實作和契約測試。
3. 不繼承大型編排服務，不暴露整個 `app.state`，不複製核心流程。
4. 只有出現需要持久化的二開設定後，再增加 schema 遷移註冊表。
5. 需要升級直接修改原始碼的分支時，使用 `scripts/upgrade_check.py` 預檢，再執行真實合併和迴歸。

這種順序遵循 KISS 和 YAGNI：先解決已經存在的升級衝突，不提前建設完整外掛平台，也不阻止開發者在必要時直接修改原始碼。

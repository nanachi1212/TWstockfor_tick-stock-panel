// Phase 8B-2.1 — 導覽 metadata 單一事實來源(Single Source of Truth)。
//
// Phase 8C-D — Legacy Product Removal: 原本並存的「中國 A 股 legacy 功能」
// (ASHARE_LEGACY_NAV: 策略選股/A 股回測/因子挖掘, 受 show_ashare_legacy_features
// 總開關控制)已隨對應 route/page 正式移除產品介面, 本檔案不再匯出該陣列。
//
// CORE_NAV: 已確認可靠支援台股 / 市場中立的功能, 走既有的 nav_order /
//   nav_hidden 偏好機制(可拖曳排序、可個別顯示/隱藏)。
import {
  Star,
  LayoutDashboard,
  RadioTower,
  Filter,
  Scale,
  type LucideIcon,
} from 'lucide-react'

export interface NavMeta {
  to: string
  label: string
  icon: LucideIcon
}

// Phase 8B-4.2.1 — /backtest 移出 CORE_NAV。原因(見報告 A 節):
//   /backtest 的 StrategyBacktest 標的選擇器呼叫 A 股 instrumentSearch,
//     使用者實際無法選取台股標的(如 2330.TWSE)進行回測 —— 不是語言問題,
//     是功能尚未支援台股, Taiwan-first 導航不該推薦一個選不到台股的回測。
// route / component / backend 完全保留, 只搬到 ASHARE_LEGACY_NAV, 受
// show_ashare_legacy_features 控制(HIDE, NOT DELETE, 與既有機制一致)。
//
// Phase 8B-5.8 — /data(A 股資料管理頁)已整頁刪除(非僅隱藏): 一般台股
// 使用者不需要理解或操作 daily pipeline / enriched rebuild / minute K sync
// 等資料工程操作, 資料更新改由 backend scheduler 全自動處理。backend
// pipeline / scheduler / sync services 完全未變更。
//
// Phase 8B-5.10 — 舊連板梯隊頁已整頁刪除: 連板/打板/炸板/
// 封板率等皆為中國 A 股漲跌停製度衍生的投機文化術語, 對台股使用者無意義,
// 不硬改名成台股功能。depth_service 的封板判定(sealed cache)仍被
// market_overview_builder / quote_service / monitor_rules 等多方真實消費,
// 完全保留未動。
//
// Phase 8B-5.11 — /review(盤後檢討/AI 大盤復盤頁)已整頁刪除, 連同其專屬的
// 定時復盤 scheduler job、飛書/企業微信推送設定一併移除(非僅隱藏 UI,
// 避免曾開啟過的使用者留下無 UI 可關的背景任務)。A 股四大指數/漲停封板率
// 情緒分析對台股無意義;台股「今天強不強」由 TaiwanScreener / Market
// Intelligence / Industry Intelligence / Dashboard 提供, 不建立替代頁面。
// GET /api/overview/market、market_overview_builder、depth_service、
// ai_provider、generic WeCom/Feishu webhook 基礎設施完全未動。
// Phase 8C-A — 順序改為看市場(看板)→找股票(台股選股)→加自選(自選股)→比較→
// 監控, 對齊產品核心使用流程(見 Phase 8C-0 審查 C 節); route path 完全不動,
// 只調整陣列順序(即側欄/選單設定顯示順序)。
export const CORE_NAV: readonly NavMeta[] = [
  { to: '/',                label: '看板',     icon: LayoutDashboard },
  { to: '/taiwan-screener', label: '台股選股', icon: Filter },
  { to: '/watchlist',  label: '自選股',   icon: Star },
  { to: '/stocks/compare', label: '多股比較', icon: Scale },
  { to: '/monitor', label: '監控中心', icon: RadioTower },
] as const

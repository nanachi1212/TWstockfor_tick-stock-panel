// 導覽 metadata 單一事實來源: Layout 側欄與「設定 → 選單設定」共用。
import {
  Star,
  Compass,
  LayoutDashboard,
  RadioTower,
  Filter,
  Scale,
  CalendarDays,
  Sparkles,
  History,
  MessageCircleMore,
  BellRing,
  RefreshCw,
  FlaskConical,
  Database,
  BarChart3,
  BookOpen,
  BrainCircuit,
  type LucideIcon,
} from 'lucide-react'

export interface NavMeta {
  to: string
  label: string
  icon: LucideIcon
}


// 核心流程 (側欄常駐): 看市場 → 今日候選 → 自己篩 → 加自選 → 盯盤 → 比較。
export const CORE_NAV: readonly NavMeta[] = [
  { to: '/',                label: '看板',     icon: LayoutDashboard },
  { to: '/picks',           label: '今日選股', icon: Compass },
  { to: '/taiwan-screener', label: '台股選股', icon: Filter },
  { to: '/watchlist',       label: '自選股',   icon: Star },
  { to: '/monitor',         label: '監控中心', icon: RadioTower },
  { to: '/stocks/compare',  label: '多股比較', icon: Scale },
] as const

// 進階 / 研究功能: 側欄預設收合在「進階功能」群組內, 不佔新手的視線。
export const ADVANCED_NAV: readonly NavMeta[] = [
  { to: '/daily-brief',     label: '每日摘要', icon: Sparkles },
  { to: '/market-research', label: '大盤研究', icon: BarChart3 },
  { to: '/research',        label: '研究歷史', icon: BookOpen },
  { to: '/events',          label: '事件中心', icon: CalendarDays },
  { to: '/buy-points',      label: '買點策略', icon: BellRing },
  { to: '/selection-review',label: '選股復盤', icon: History },
  { to: '/strategy-lab',    label: '策略實驗室', icon: FlaskConical },
  { to: '/social-sentiment',label: '社群聲量', icon: MessageCircleMore },
  { to: '/social-fetch',    label: '社群即時撈取', icon: RefreshCw },
  { to: '/model',           label: '模型驗證', icon: BrainCircuit },
  { to: '/data-health',     label: '資料健康', icon: Database },
] as const

/** 所有內建頁面 (核心 + 進階), 供選單設定排序/隱藏使用。 */
export const ALL_NAV: readonly NavMeta[] = [...CORE_NAV, ...ADVANCED_NAV]

/** 側欄「進階功能」群組標題所用的虛擬 id (不是真實路由)。 */
export const ADVANCED_GROUP_ID = '#advanced'

export function isAdvancedNavPath(pathname: string): boolean {
  return ADVANCED_NAV.some(n => pathname === n.to || pathname.startsWith(n.to + '/'))
}

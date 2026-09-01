// Phase 8B-2.1 — 導覽 metadata 單一事實來源(Single Source of Truth)。
//
// 背景: Phase 8B-2 把主導覽拆成「台股核心功能」(CORE_NAV) 與「中國 A 股
// legacy 功能」(ASHARE_LEGACY_NAV) 两组, Layout.tsx(实际 sidebar 渲染)与
// MenuSettings.tsx(设定 → 选单设定 的显示/隐藏管理)原本各自维护一份重复
// 且容易漂移的清单 —— 本档案抽出共用定义, 两处改为 import 同一份, 避免
// 「设定页说已隐藏, sidebar 却还显示」这类不同步问题。
//
// CORE_NAV: 已确认可靠支援台股 / 市场中立的功能, 走既有的 nav_order /
//   nav_hidden 偏好机制(可拖曳排序、可个别显示/隐藏)。
// ASHARE_LEGACY_NAV: 已确认仅适用中国 A 股制度的 legacy 功能, 受
//   show_ashare_legacy_features 总开关控制(默认 false); 开关打开后,
//   仍可用既有 nav_hidden 个别隐藏其中某几项(见 Layout.tsx / MenuSettings.tsx
//   的用法) —— 但不参与 nav_order 拖曳排序, 因为它们固定渲染在独立的
//   「中國 A 股（選配）」小节, 排序对它们没有意义。
import {
  Siren,
  Star,
  ScanSearch,
  History,
  Pickaxe,
  FileText,
  Database,
  LayoutDashboard,
  TrendingUp,
  Flame,
  BarChart3,
  Gauge,
  Layers3,
  Landmark,
  RadioTower,
  BookOpenCheck,
  Filter,
  Scale,
  type LucideIcon,
} from 'lucide-react'

export interface NavMeta {
  to: string
  label: string
  icon: LucideIcon
}

export const CORE_NAV: readonly NavMeta[] = [
  { to: '/',                label: '看板',     icon: LayoutDashboard },
  { to: '/watchlist',  label: '自選股',   icon: Star },
  { to: '/taiwan-screener', label: '台股選股', icon: Filter },
  { to: '/stocks/compare', label: '多股比較', icon: Scale },
  { to: '/backtest',   label: '回測', icon: History },
  { to: '/data',       label: '資料管理',   icon: Database },
  { to: '/monitor', label: '監控中心', icon: RadioTower },
] as const

export const ASHARE_LEGACY_NAV: readonly NavMeta[] = [
  { to: '/screener',   label: '策略選股',   icon: ScanSearch },
  { to: '/stock-analysis',    label: '個股分析', icon: TrendingUp },
  { to: '/financials', label: '財務分析', icon: FileText },
  { to: '/mining',     label: '因子挖掘', icon: Pickaxe },
  { to: '/regime', label: '市場環境', icon: Gauge },
  { to: '/abnormal', label: '異動監控', icon: Siren },
  { to: '/review',      label: '盤後檢討',   icon: BookOpenCheck },
  { to: '/indices', label: '指數', icon: BarChart3 },
  { to: '/limit-ladder', label: '連板梯隊', icon: Flame },
  { to: '/concept-analysis', label: '概念分析', icon: Layers3 },
  { to: '/industry-analysis', label: '行業分析', icon: Landmark },
] as const

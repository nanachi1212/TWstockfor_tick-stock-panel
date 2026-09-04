import { useEffect, useMemo, useRef, useState, Suspense } from 'react'
import { NavLink, Outlet, useNavigate, useLocation } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { useQuoteStream, useQuoteStreamStatus } from '@/lib/useQuoteStream'
import { ToastContainer, toast } from '@/components/Toast'
import { AlertToastContainer } from '@/components/AlertToast'
import { StockAnalysisHost } from '@/components/stock-analysis/StockAnalysisHost'
import { StockAnalysisBubble } from '@/components/stock-analysis/StockAnalysisBubble'
import {
  useCapabilities,
  useSettings,
  usePreferences,
  useQuoteStatus,
  useVersion,
} from '@/lib/useSharedQueries'
import {
  useToggleRealtimeQuotes,
} from '@/lib/useSharedMutations'
import { QK } from '@/lib/queryKeys'
import {
  Settings,
  DatabaseZap,
  Loader2,
  Tags,
  BarChart3,
  Sparkles,
  CheckCircle2,
  ChevronRight,
  ChevronDown,
  Sun,
  Moon,
  X,
  WifiOff,
  PanelLeftClose,
  PanelLeftOpen,
} from 'lucide-react'
import { Logo } from './Logo'
import { api, type IndexQuote } from '@/lib/api'
import { cn } from '@/lib/cn'
import { resolveWatchlistGroupColor } from '@/lib/watchlist-group-colors'
import { computeGroupPcts, groupPctColor, groupPctTitle } from '@/lib/watchlistGroupStats'
import { fmtPct } from '@/lib/format'
import { toggleTheme, useTheme } from '@/lib/theme'
import { setCurrentTotal as setAlertTotal, useUnreadAlerts } from '@/lib/monitorBadge'
import { ExtensionSlot } from '@/extensions/ExtensionSlot'
import { getFrontendExtensionNavigation } from '@/extensions/registry'
import { CORE_NAV as nav, ASHARE_LEGACY_NAV as ashareLegacyNav } from '@/lib/navigation'
import type { LucideIcon } from 'lucide-react'

// 品牌色 — 只用于 logo / brand 区域,不影响功能语义色
const BRAND = '#8B5CF6'

// Phase 8B-2: 这 4 个是 A 股指数, 侧边栏卡片(SidebarIndexQuotes)只在
// show_ashare_legacy_features 偏好开启时才渲染(见下方渲染处)。数组本身与
// sidebar_index_symbols 偏好保留, 不删除既有能力 —— 目前没有可靠的台股大盘
// 指数数据源(REQUIRES BACKEND SUPPORT), 不假造台股指数, 详见 Phase 8B-2 报告 E 节。
const CORE_INDEXES = [
  { symbol: '000001.SH', name: '上證指數' },
  { symbol: '399001.SZ', name: '深證成指' },
  { symbol: '399006.SZ', name: '創業板指' },
  { symbol: '000680.SH', name: '科創綜指' },
] as const

type CoreIndex = (typeof CORE_INDEXES)[number]

// Phase 8B-2.1: nav / ashareLegacyNav 的定义已抽到 @/lib/navigation.ts
// (CORE_NAV / ASHARE_LEGACY_NAV), 与 MenuSettings.tsx 共用同一份 metadata,
// 避免两处各自维护清单造成显示/隐藏不同步。此处用别名 import 保持下方
// 既有代码(nav.findIndex 等)不必改名。

/** 亮/暗主题切换 — 状态存 localStorage, 生效见 lib/theme.ts */
function ThemeToggle() {
  const theme = useTheme()
  const dark = theme === 'dark'
  return (
    <button
      onClick={() => toggleTheme()}
      className="flex items-center justify-center rounded-btn p-2 text-foreground/80 transition-colors duration-150 ease-smooth hover:bg-elevated hover:text-foreground cursor-pointer"
      title={dark ? '切換到亮色模式' : '切換到暗色模式'}
    >
      {dark ? <Sun className="h-4 w-4 shrink-0" /> : <Moon className="h-4 w-4 shrink-0" />}
    </button>
  )
}

function fmtIndexValue(v: number | null | undefined) {
  if (v == null || Number.isNaN(Number(v))) return '--'
  return Number(v).toFixed(2)
}

function fmtIndexPct(v: number | null | undefined) {
  if (v == null || Number.isNaN(Number(v))) return '--'
  return `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`
}

function indexPctClass(v: number | null | undefined) {
  if (v == null || Number.isNaN(Number(v))) return 'text-muted'
  const n = Number(v)
  if (n === 0) return 'text-foreground'
  return n > 0 ? 'text-bull' : 'text-bear'
}

/** 监控中心未读徽标 — 仅在非监控页且有未读时显示。 */
function MonitorBadge({ active }: { active: boolean }) {
  const unread = useUnreadAlerts()
  // 尊重用户设置: 可在菜单设置里关闭数字提示
  const badgeEnabled = (() => {
    try { return localStorage.getItem('monitor_badge_enabled') !== '0' } catch { return true }
  })()
  if (active || unread <= 0 || !badgeEnabled) return null
  return (
    <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-danger px-1 text-[9px] font-bold text-white animate-pulse">
      {unread > 99 ? '99+' : unread}
    </span>
  )
}

function SidebarIndexQuotes({ rows, items }: { rows: IndexQuote[] | undefined; items: CoreIndex[] }) {
  if (items.length === 0) return null
  const quoteBySymbol = new Map((rows ?? []).map(q => [q.symbol, q]))
  return (
    <div className="mt-2 grid grid-cols-2 gap-1.5 border-t border-border/60 pt-2">
      {items.map(item => {
        const q = quoteBySymbol.get(item.symbol)
        const value = q?.last_price ?? q?.close
        const pct = q?.change_pct
        return (
          <NavLink
            key={item.symbol}
            to={`/indices?symbol=${encodeURIComponent(item.symbol)}`}
            className="block rounded bg-elevated/60 px-2 py-1.5 transition-colors hover:bg-elevated"
            title={`${item.name} ${item.symbol}`}
          >
            <div className="flex items-center justify-between gap-1">
              <span className="text-[10px] text-secondary">{item.name}</span>
              <span className={`text-[10px] font-mono ${indexPctClass(pct)}`}>{fmtIndexPct(pct)}</span>
            </div>
            <div className={`mt-0.5 truncate font-mono text-[10px] ${indexPctClass(pct)}`}>
              {fmtIndexValue(value)}
            </div>
          </NavLink>
        )
      })}
    </div>
  )
}

// ===== 档位卡片 =====
function TierBadge({ label, hasKey, providerName, isTickflow }: { label: string; hasKey?: boolean; providerName: string; isTickflow: boolean }) {
  const base = label.split(' ')[0].split('+')[0].toLowerCase()
  const isNone = base === 'none'

  const tierConfig: Record<string, {
    desc: string
    dotStyle: React.CSSProperties
    tagBg: React.CSSProperties
    labelTextStyle: React.CSSProperties
  }> = {
    none: {
      desc: '未設定 Key · 僅歷史日K',
      dotStyle: { background: '#52525b' },
      tagBg: { background: 'rgba(113,113,122,0.15)' },
      labelTextStyle: { color: '#71717a' },
    },
    free: {
      desc: '基礎日K · 自選即時',
      dotStyle: { background: '#71717a' },
      tagBg: { background: 'rgba(113,113,122,0.3)' },
      labelTextStyle: { color: '#a1a1aa' },
    },
    starter: {
      desc: '批量同步 · 行情池',
      dotStyle: { background: '#3b82f6' },
      tagBg: { background: 'rgba(59,130,246,0.2)' },
      labelTextStyle: { color: '#60a5fa' },
    },
    pro: {
      desc: '分鐘K · 即時行情 · 盤口',
      dotStyle: { background: 'linear-gradient(135deg, #a855f7, #7c3aed)' },
      tagBg: { background: 'linear-gradient(135deg, rgba(168,85,247,0.2), rgba(124,58,237,0.15))' },
      labelTextStyle: { background: 'linear-gradient(135deg, #c084fc, #a855f7)', WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent' },
    },
    expert: {
      desc: 'WebSocket · 財務資料',
      dotStyle: { background: 'linear-gradient(135deg, #3b82f6, #a855f7, #f59e0b)' },
      tagBg: { background: 'linear-gradient(135deg, rgba(59,130,246,0.2), rgba(168,85,247,0.2), rgba(245,158,11,0.2))' },
      labelTextStyle: { background: 'linear-gradient(135deg, #60a5fa, #c084fc, #fbbf24)', WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent' },
    },
  }

  const t = tierConfig[base] || tierConfig.none
  const displayLabel = isNone ? 'None' : (label || 'None')
  const descText = isNone && !hasKey ? '設定 Key 解鎖更多能力' : t.desc

  return (
    <NavLink
      to="/settings?tab=data-sources"
      className="group relative flex items-center gap-2 overflow-hidden rounded-md py-1.5 pl-2.5 pr-2 transition-colors duration-150 hover:bg-elevated/70"
      title={`資料來源 · ${providerName} — ${descText}`}
    >
      <span
        className="pointer-events-none absolute inset-y-1.5 left-0 w-[2px] rounded-full bg-accent/50 transition-colors group-hover:bg-accent"
        style={base === 'expert' ? { background: 'linear-gradient(180deg, #60a5fa, #c084fc, #fbbf24)' } : undefined}
      />
      <DatabaseZap className="h-3.5 w-3.5 shrink-0 text-muted group-hover:text-accent transition-colors" />
      <span className="min-w-0 truncate text-[11px] font-medium text-secondary group-hover:text-foreground transition-colors">
        {providerName || '資料來源'}
      </span>
      <span
        className="h-1.5 w-1.5 rounded-full shrink-0"
        style={{ ...t.dotStyle, ...(base === 'expert' ? { animation: 'pulse 2s infinite' } : {}) }}
      />
      {isTickflow && (
        <span
          className="ml-auto inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-bold font-mono leading-none shrink-0"
          style={t.tagBg}
        >
          <span className="truncate" style={t.labelTextStyle}>{displayLabel}</span>
        </span>
      )}
    </NavLink>
  )
}

function AIConfigBadge({ configured, model }: { configured?: boolean; model?: string }) {
  const descText = configured ? (model || '已接入模型') : '接入策略生成模型'
  return (
    <NavLink
      to="/settings?tab=ai"
      className="group relative flex items-center gap-2 overflow-hidden rounded-md py-1.5 pl-2.5 pr-2 transition-colors duration-150 hover:bg-elevated/70"
      title={`AI 設定 — ${descText}`}
    >
      <span className="pointer-events-none absolute inset-y-1.5 left-0 w-[2px] rounded-full bg-purple-400/50 transition-colors group-hover:bg-purple-400" />
      <Sparkles className="h-3.5 w-3.5 shrink-0 text-muted group-hover:text-purple-400 transition-colors" />
      {configured ? (
        <span className="truncate text-[11px] font-medium text-secondary group-hover:text-foreground transition-colors">
          {model || '已接入模型'}
        </span>
      ) : (
        <>
          <span className="text-[11px] text-secondary group-hover:text-foreground transition-colors">AI 設定</span>
          <span className="ml-auto text-[11px] font-mono leading-none text-muted">未設定</span>
        </>
      )}
      <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${configured ? 'bg-bear' : 'bg-warning'}`} />
    </NavLink>
  )
}

export function Layout() {
  // ===== 共享 hooks (替代内联 useQuery) =====
  const { data: caps } = useCapabilities()
  const { data: settingsState } = useSettings()
  const { data: versionData } = useVersion()
  const { data: prefs } = usePreferences()
  // 数据源列表 (用于实时行情状态显示当前数据源名称)
  const { data: dataSources } = useQuery({
    queryKey: QK.dataSources,
    queryFn: api.dataSources,
    staleTime: 60_000,
  })
  // poll=true: 全局唯一开启条件轮询 (非交易时段 60s 兜底, 交易时段靠 SSE)
  const { data: quoteStatus } = useQuoteStatus({ poll: true })
  const { data: analysisMenus } = useQuery({
    queryKey: QK.analysisMenus,
    queryFn: api.analysisMenus,
  })

  // 自选分组 — 仅当用户开启「显示在侧边栏」时拉取
  const groupsInNav = prefs?.watchlist_groups_in_nav ?? false
  const location = useLocation()
  const { data: watchlistGroupsData } = useQuery({
    queryKey: QK.watchlistGroups,
    queryFn: api.watchlistGroups,
    enabled: groupsInNav,
    staleTime: 60_000,
  })
  const watchlistGroups = watchlistGroupsData?.groups ?? []
  // 自选二级菜单展开状态 — 默认当前在自选页时展开
  const [watchlistNavExpanded, setWatchlistNavExpanded] = useState(location.pathname === '/watchlist')

  // 侧边栏收起状态 — 持久化到 localStorage
  const [navCollapsed, setNavCollapsed] = useState(() => {
    if (typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches) return true
    try { return localStorage.getItem('tf-nav-collapsed') === '1' } catch { return false }
  })

  // 分组等权平均涨跌幅 — 复用 watchlist/enriched 查询缓存(与自选页同 key,
  // 盘中随 SSE 刷新)。可见性门控: 子菜单实际可见(侧栏展开 + 二级菜单展开)
  // 时才拉取, 收起状态下不为隐藏 UI 发请求。
  const navGroupPctVisible = groupsInNav && !navCollapsed && watchlistNavExpanded
  const { data: navWatchlist } = useQuery({
    queryKey: QK.watchlist,
    queryFn: api.watchlistList,
    enabled: navGroupPctVisible,
    staleTime: 60_000,
  })
  const { data: navEnriched } = useQuery({
    queryKey: QK.watchlistEnriched(undefined),
    queryFn: () => api.watchlistEnriched(),
    enabled: navGroupPctVisible,
    staleTime: 60_000,
  })
  const navGroupPcts = useMemo(
    () => computeGroupPcts(
      navWatchlist?.symbols ?? [],
      new Map((navEnriched?.rows ?? []).map((r: any) => [r.symbol as string, r])),
    ),
    [navWatchlist, navEnriched],
  )

  // 数据同步状态轮询: 有活跃 job 时「数据」菜单项显示转圈
  const { data: pipelineJobs } = useQuery({
    queryKey: QK.pipelineJobs,
    queryFn: () => api.pipelineJobs(1),
    refetchInterval: (query) => (query.state.data?.active_id ? 2000 : 15000),
    refetchIntervalInBackground: true,
  })
  const isDataSyncing = !!pipelineJobs?.active_id

  // 数据同步完成的"瞬时反馈": isDataSyncing 从 true→false 时显示绿色对勾,
  // 闪烁约 3 秒后自动消失。
  const [dataSyncJustDone, setDataSyncJustDone] = useState(false)
  const prevSyncingRef = useRef(false)
  useEffect(() => {
    // 仅在"刚结束"(true→false)且非首次挂载时触发
    if (prevSyncingRef.current && !isDataSyncing) {
      setDataSyncJustDone(true)
      const t = setTimeout(() => setDataSyncJustDone(false), 3000)
      prevSyncingRef.current = isDataSyncing
      return () => clearTimeout(t)
    }
    prevSyncingRef.current = isDataSyncing
  }, [isDataSyncing])

  const qc = useQueryClient()
  const navigate = useNavigate()
  const version = versionData?.version
  const realtimeEnabled = prefs?.realtime_quotes_enabled ?? false
  // 自选实时模式限制提示: 可手动关闭, 不持久化 (刷新后恢复显示)
  const [dismissFreeHint, setDismissFreeHint] = useState(false)
  useEffect(() => {
    const compact = window.matchMedia('(max-width: 767px)')
    const syncSidebarWithViewport = (event: MediaQueryListEvent | MediaQueryList) => {
      if (event.matches) {
        setNavCollapsed(true)
        return
      }
      try { setNavCollapsed(localStorage.getItem('tf-nav-collapsed') === '1') } catch {}
    }
    syncSidebarWithViewport(compact)
    compact.addEventListener('change', syncSidebarWithViewport)
    return () => compact.removeEventListener('change', syncSidebarWithViewport)
  }, [])
  const toggleNavCollapsed = () => {
    setNavCollapsed(prev => {
      const next = !prev
      try { localStorage.setItem('tf-nav-collapsed', next ? '1' : '0') } catch {}
      return next
    })
  }
  // Phase 8B-3.2 — CORE_INDEXES(上證/深證/創業板/科創)是純 A 股 legacy 指數,
  // 這裡提前到 sidebarIndexQuotes 查詢之前聲明, 用來在 A 股關閉時連 REST 請求
  // 都不發(而不只是不渲染)。與 Menu Settings/Dashboard 讀同一份偏好, 非新開關。
  const showAshareLegacy = prefs?.show_ashare_legacy_features ?? false
  const indicesPinned = prefs?.indices_nav_pinned ?? true
  const sidebarIndexSymbols = prefs?.sidebar_index_symbols ?? CORE_INDEXES.map(p => p.symbol)
  const sidebarIndexes = CORE_INDEXES.filter(item => sidebarIndexSymbols.includes(item.symbol))
  // 卡片数据：固定显示时也拉取（即使实时行情关闭）
  const showSidebarQuotes = indicesPinned || realtimeEnabled
  const { data: sidebarIndexQuotes } = useQuery({
    queryKey: [...QK.indexQuotes, 'sidebar', sidebarIndexSymbols.join(',')] as const,
    queryFn: () => api.indexQuotes(sidebarIndexes.map(p => p.symbol)),
    // showAshareLegacy 闸门: sidebarIndexes 恒为 CORE_INDEXES(A 股指数)的子集,
    // 这个 query 是 ASHARE_ONLY —— A 股关闭时连 REST 请求都不该发, 不只是不渲染。
    enabled: showAshareLegacy && showSidebarQuotes && sidebarIndexes.length > 0,
    placeholderData: (prev) => prev,
  })

  // SSE: 行情更新时自动刷新相关 queries + 告警通知
  useQuoteStream(realtimeEnabled, prefs?.sse_refresh_pages)
  // 实时 SSE 连接状态 — 断开时底部显示提示, 提示可能漏策略告警
  const streamStatus = useQuoteStreamStatus()

  const toggleQuote = useToggleRealtimeQuotes()
  const isRunning = quoteStatus?.running ?? false
  const isTrading = quoteStatus?.is_trading_hours ?? false
  // 管道/数据修正运行期间实时行情被临时暂停 — 此时禁止开启
  const isPaused = quoteStatus?.paused ?? false
  // 实时模式以 quote_status 为准 (数据源无关): none=不可用 / watchlist=自选实时 / full_market=全市场
  const quoteMode = quoteStatus?.mode ?? 'none'
  const realtimeUnavailable = quoteMode === 'none'
  const isWatchlistMode = quoteMode === 'watchlist'
  const realtimeModeLabel = isWatchlistMode ? '自選股' : '全市場'
  // 当前实时行情数据源名称 (custom 时显示源名, tickflow 时不显示)
  const realtimeProvider = prefs?.realtime_data_provider
  const realtimeProviderName = realtimeProvider && realtimeProvider !== 'tickflow'
    ? (dataSources?.custom?.find(s => s.name === realtimeProvider)?.display_name || realtimeProvider)
    : null
  const realtimeToggleDisabled = toggleQuote.isPending || isPaused
  const realtimeActive = realtimeEnabled && isRunning && isTrading
  const realtimeStatusLabel = toggleQuote.isPending
    ? '正在更新'
    : isPaused
      ? '同步期間暫停'
      : realtimeActive
        ? '執行中'
        : realtimeEnabled
          ? (isTrading ? '正在連線' : '等待交易時段')
          : '已關閉'
  const realtimeStatusClass = realtimeActive
    ? 'text-accent'
    : realtimeEnabled || isPaused
      ? 'text-warning/80'
      : 'text-muted'
  const realtimeIndicatorClass = realtimeActive
    ? 'bg-accent animate-pulse'
    : realtimeEnabled || isPaused
      ? 'bg-warning/70'
      : 'bg-muted'
  const realtimeToggleTitle = isPaused
    ? '資料同步進行中,即時行情已暫時暫停'
    : toggleQuote.isPending
      ? '正在更新即時行情設定'
      : realtimeEnabled
        ? '關閉即時行情'
        : '開啟即時行情'

  // 当前主数据源 (用于侧边栏数据源状态卡)
  const activeProvider = prefs?.daily_data_provider || 'tickflow'
  const activeProviderName = activeProvider === 'tickflow'
    ? 'TickFlow'
    : (dataSources?.custom?.find(s => s.name === activeProvider)?.display_name || activeProvider)
  const isCustomActive = activeProvider !== 'tickflow'

  // 轮询触发记录总数 → 更新监控中心徽标 (每 15 秒; 后台标签页由 SSE 事件驱动, 不轮询)
  const alertsTotalQuery = useQuery({
    queryKey: ['alerts-total'],
    queryFn: () => api.alertsList({ days: 7, limit: 1 }),
    refetchInterval: 15000,
    select: (data) => data.total,
  })
  // 只在拿到真实总数时同步徽标 (避免 data=undefined 时传 0 重置 lastSeen)
  const alertsTotal = alertsTotalQuery.data
  useEffect(() => {
    if (alertsTotal != null) setAlertTotal(alertsTotal)
  }, [alertsTotal])

  // 合并内置页面 + 可见的扩展分析菜单
  type NavItem = { to: string; label: string; icon: LucideIcon; badge?: string }
  const analysisNav: NavItem[] = (analysisMenus?.items ?? [])
    .filter(m => m.visible)
    .map(m => ({ to: `/analysis/${m.id}`, label: m.label, icon: m.icon === 'tags' ? Tags : BarChart3 }))
  const extensionNav: NavItem[] = getFrontendExtensionNavigation().map(item => ({
    to: item.route.path,
    label: item.label,
    icon: item.icon,
    badge: item.badge,
  }))

  const allNav: NavItem[] = [...nav, ...analysisNav, ...extensionNav]
  const savedOrder = prefs?.nav_order ?? []

  const navItems = savedOrder.length > 0
    ? (() => {
        const byTo = new Map(allNav.map(n => [n.to, n]))
        const ordered = (savedOrder
          .map(id => byTo.get(id) ?? byTo.get(`/analysis/${id}`))
          .filter(Boolean)) as typeof allNav
        const seen = new Set(ordered.map(n => n.to))
        const merged = [...ordered]
        for (const item of allNav) {
          if (seen.has(item.to)) continue
          // 未保存过排序的新条目: 内置页插回默认位置(排在已保存的默认前驱之后),
          // 分析/扩展菜单仍追加到末尾
          const defaultIndex = nav.findIndex(n => n.to === item.to)
          let anchor = -1
          if (defaultIndex > 0) {
            for (let i = defaultIndex - 1; i >= 0 && anchor < 0; i -= 1) {
              anchor = merged.findIndex(n => n.to === nav[i].to)
            }
          }
          if (anchor >= 0) merged.splice(anchor + 1, 0, item)
          else if (defaultIndex >= 0) merged.unshift(item)
          else merged.push(item)
        }
        return merged
      })()
    : allNav

  const hiddenIds = new Set(prefs?.nav_hidden ?? [])
  const visibleNavItems = navItems.filter(n => !hiddenIds.has(n.to) && !hiddenIds.has(n.to.replace(/^\/analysis\//, '')))
  // Phase 8B-2.1 — 中國 A 股 legacy 功能區塊: 固定渲染在獨立小節, 不參與
  // nav_order 拖曳排序(排序對固定小節沒有意義), 但沿用同一份 nav_hidden 個別
  // 隱藏 —— 與 MenuSettings.tsx「中國 A 股功能」小節的個別 eye 開關是同一組
  // 資料, 不是第二套顯示邏輯。總開關 show_ashare_legacy_features 決定整個小節
  // 是否出現, 個別 hiddenIds 決定小節內哪幾項出現。(showAshareLegacy 已在上方
  // sidebarIndexQuotes 查詢前聲明, Phase 8B-3.2, 此處不再重複宣告)
  const visibleAshareLegacyNav = showAshareLegacy
    ? ashareLegacyNav.filter(n => !hiddenIds.has(n.to))
    : []

  const handleToggle = async (enabled: boolean) => {
    // 开启时重新校验实时权限 (以 quote_status 的数据源无关判定为准)
    if (enabled) {
      const fresh = await qc.fetchQuery({
        queryKey: QK.quoteStatus,
        queryFn: api.quoteStatus,
      })
      if (!fresh.realtime_allowed) {
        toast('目前資料來源無即時行情能力,請先設定資料來源', 'error')
        return
      }
      if (fresh.mode === 'watchlist' && (prefs?.realtime_watchlist_symbols?.length ?? 0) === 0) {
        navigate('/watchlist')
        return
      }
    }
    await toggleQuote.mutateAsync(enabled)
    // 仅在交易时段立即获取一次行情
    if (enabled && isTrading) {
      api.intradayRefresh().catch(() => {})
    }
  }

  return (
    <div
      className="h-screen grid bg-base text-foreground overflow-hidden transition-[grid-template-columns] duration-200 ease-smooth"
      style={{ gridTemplateColumns: navCollapsed ? '3.5rem 1fr' : '14rem 1fr' }}
    >
      <aside className="border-r border-border bg-surface flex flex-col h-full min-h-0 overflow-hidden">
        <div className={cn('border-b border-border shrink-0', navCollapsed ? 'px-2 pt-3 pb-2' : 'px-4 pt-4 pb-3')}>
          {/* Brand block — 收起时只显 logo 居中 */}
          <div className={cn('flex', navCollapsed ? 'flex-col items-center gap-2' : 'items-center gap-2')}>
            <Logo
              size={navCollapsed ? 24 : 26}
              className="shrink-0 drop-shadow-[0_0_8px_rgba(139,92,246,0.4)]"
              style={{ color: BRAND }}
            />
            {!navCollapsed && (
              <div
                className="font-bold text-[11px] uppercase tracking-[0.14em] text-foreground whitespace-nowrap"
                style={{ textShadow: `0 0 10px ${BRAND}44` }}
              >
                TickFlow 台股面板
              </div>
            )}
            {/* 收起/展开 按钮 */}
            <button
              onClick={toggleNavCollapsed}
              className={cn(
                'flex items-center rounded-btn text-muted hover:text-foreground hover:bg-elevated/60 transition-colors duration-150 ease-smooth',
                navCollapsed ? 'justify-center p-1.5' : 'ml-auto p-1.5',
              )}
              title={navCollapsed ? '展開選單' : '收起選單'}
            >
              {navCollapsed
                ? <PanelLeftOpen className="h-3.5 w-3.5 shrink-0" />
                : <PanelLeftClose className="h-3.5 w-3.5 shrink-0" />
              }
            </button>
          </div>

          {/* 状态卡 — 收起时隐藏 */}
          {!navCollapsed && (
            <div className="mt-2.5 border-t border-border/60 pt-1">
              <TierBadge
                label={caps?.label ?? ''}
                hasKey={settingsState?.mode !== 'none'}
                providerName={activeProviderName}
                isTickflow={!isCustomActive}
              />
              <div className="mx-2 border-t border-border/45" aria-hidden="true" />
              <AIConfigBadge
                configured={settingsState?.ai_configured ?? settingsState?.has_ai_key}
                model={settingsState?.ai_model}
              />
            </div>
          )}
        </div>

        <nav className="flex-1 min-h-0 overflow-y-auto px-2 py-3 space-y-0.5">
          {visibleNavItems.map(({ to, label, icon: Icon, badge }) => {
            // 「自选」项 — 开启分组侧栏且未整体收起时, 渲染为可展开父项 + 二级分组
            const isWatchlistExpandable = to === '/watchlist' && groupsInNav && !navCollapsed && watchlistGroups.length > 0
            return (
              <div key={to}>
                {isWatchlistExpandable ? (
                  /* 可展开的自选父项 — 点击切换展开, 不直接跳页 */
                  <button
                    onClick={() => setWatchlistNavExpanded(v => !v)}
                    className={cn(
                      'group relative flex w-full items-center gap-3 rounded-btn px-3 py-2 text-sm transition-all duration-150 ease-smooth',
                      location.pathname === '/watchlist'
                        ? 'bg-elevated text-foreground font-medium'
                        : 'text-foreground/75 hover:bg-elevated/70 hover:text-foreground',
                    )}
                  >
                    <span
                      className={cn(
                        'pointer-events-none absolute left-0 top-1/2 h-4 -translate-y-1/2 w-[2.5px] rounded-full bg-accent transition-opacity duration-150',
                        location.pathname === '/watchlist' ? 'opacity-100 shadow-[0_0_8px_rgba(59,130,246,0.6)]' : 'opacity-0',
                      )}
                    />
                    <Icon className={cn('h-4 w-4 shrink-0 transition-colors', location.pathname === '/watchlist' ? 'text-accent' : 'text-foreground/60 group-hover:text-foreground/85')} />
                    <span className="flex-1 text-left">{label}</span>
                    {watchlistNavExpanded
                      ? <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted" />
                      : <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted" />
                    }
                  </button>
                ) : (
                  /* 普通菜单项 */
                  <NavLink
                    to={to}
                    title={navCollapsed ? label : undefined}
                    className={({ isActive }) =>
                      cn(
                        'group relative flex items-center rounded-btn text-sm transition-all duration-150 ease-smooth',
                        navCollapsed ? 'justify-center px-0 py-2' : 'gap-3 px-3 py-2',
                        isActive
                          ? 'bg-elevated text-foreground font-medium'
                          : 'text-foreground/75 hover:bg-elevated/70 hover:text-foreground',
                      )
                    }
                  >
                    {({ isActive }) => (
                      <>
                        {/* active 左侧 accent 竖条指示 */}
                        <span
                          className={cn(
                            'pointer-events-none absolute left-0 top-1/2 h-4 -translate-y-1/2 w-[2.5px] rounded-full bg-accent transition-opacity duration-150',
                            isActive ? 'opacity-100 shadow-[0_0_8px_rgba(59,130,246,0.6)]' : 'opacity-0',
                          )}
                        />
                        <Icon className={cn('h-4 w-4 shrink-0 transition-colors', isActive ? 'text-accent' : 'text-foreground/60 group-hover:text-foreground/85')} />
                        {!navCollapsed && <span className="flex-1">{label}</span>}
                        {!navCollapsed && badge && (
                          <span className="ml-auto inline-flex items-center rounded-full border border-amber-400/30 bg-amber-400/10 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-amber-400 shrink-0">
                            {badge}
                          </span>
                        )}
                        {/* 数据同步状态: 同步中转圈, 刚完成显示绿色对勾闪烁 3 秒 */}
                        {to === '/data' && isDataSyncing && !navCollapsed && (
                          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-accent" />
                        )}
                        {to === '/data' && !isDataSyncing && dataSyncJustDone && !navCollapsed && (
                          <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-bull animate-pulse" />
                        )}
                        {/* 监控中心徽标: 仅非监控页且有未读时显示 */}
                        {to === '/monitor' && !navCollapsed && <MonitorBadge active={isActive} />}
                      </>
                    )}
                  </NavLink>
                )}

                {/* 自选分组二级子菜单 — 展开时显示 */}
                {isWatchlistExpandable && watchlistNavExpanded && (
                  <div className="mt-0.5 space-y-0.5">
                    <NavLink
                      to="/watchlist"
                      className={({ isActive }) => cn(
                        'flex items-center gap-2 rounded-btn py-1.5 pl-9 pr-3 text-[12px] transition-colors duration-150 ease-smooth',
                        isActive && !location.search
                          ? 'text-accent font-medium'
                          : 'text-foreground/60 hover:text-foreground hover:bg-elevated/50',
                      )}
                    >
                      <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-muted" />
                      <span>全部</span>
                      {(() => {
                        const info = navGroupPcts['all']
                        return info && info.pct != null ? (
                          <span className={`ml-auto font-mono text-[10px] tabular-nums ${groupPctColor(info.pct)}`} title={groupPctTitle(info)}>
                            {fmtPct(info.pct)}
                          </span>
                        ) : null
                      })()}
                    </NavLink>
                    {watchlistGroups.map(group => {
                      const color = resolveWatchlistGroupColor(group.color)
                      const groupPath = `/watchlist?group=${group.id}`
                      const isGroupActive = location.pathname === '/watchlist' && location.search === `?group=${group.id}`
                      const pctInfo = navGroupPcts[group.id]
                      return (
                        <NavLink
                          key={group.id}
                          to={groupPath}
                          className={cn(
                            'flex items-center gap-2 rounded-btn py-1.5 pl-9 pr-3 text-[12px] transition-colors duration-150 ease-smooth',
                            isGroupActive
                              ? 'text-accent font-medium'
                              : 'text-foreground/60 hover:text-foreground hover:bg-elevated/50',
                          )}
                        >
                          <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${color.dot}`} />
                          <span className="truncate">{group.name}</span>
                          {pctInfo && pctInfo.pct != null && (
                            <span className={`ml-auto font-mono text-[10px] tabular-nums ${groupPctColor(pctInfo.pct)}`} title={groupPctTitle(pctInfo)}>
                              {fmtPct(pctInfo.pct)}
                            </span>
                          )}
                        </NavLink>
                      )
                    })}
                  </div>
                )}
              </div>
            )
          })}
          <ExtensionSlot
            name="layout.navigation.extra"
            context={{ collapsed: navCollapsed, pathname: location.pathname }}
            compact
          />

          {/* Phase 8B-2 — 中國 A 股（選配）區塊: 預設隱藏, 設定 → 系統 開啟後才顯示。
              獨立小節, 不與上方台股核心導航混排、不參與拖曳排序。 */}
          {visibleAshareLegacyNav.length > 0 && (
            <div className="mt-2 pt-2 border-t border-border/60">
              {!navCollapsed && (
                <div className="px-3 pb-1 text-[10px] font-medium uppercase tracking-wider text-muted">
                  中國 A 股（選配）
                </div>
              )}
              {visibleAshareLegacyNav.map(({ to, label, icon: Icon }) => (
                <NavLink
                  key={to}
                  to={to}
                  title={navCollapsed ? label : undefined}
                  className={({ isActive }) =>
                    cn(
                      'group relative flex items-center rounded-btn text-sm transition-all duration-150 ease-smooth',
                      navCollapsed ? 'justify-center px-0 py-2' : 'gap-3 px-3 py-2',
                      isActive
                        ? 'bg-elevated text-foreground font-medium'
                        : 'text-foreground/60 hover:bg-elevated/70 hover:text-foreground',
                    )
                  }
                >
                  {({ isActive }) => (
                    <>
                      <span
                        className={cn(
                          'pointer-events-none absolute left-0 top-1/2 h-4 -translate-y-1/2 w-[2.5px] rounded-full bg-accent transition-opacity duration-150',
                          isActive ? 'opacity-100 shadow-[0_0_8px_rgba(59,130,246,0.6)]' : 'opacity-0',
                        )}
                      />
                      <Icon className={cn('h-4 w-4 shrink-0 transition-colors', isActive ? 'text-accent' : 'text-foreground/50 group-hover:text-foreground/80')} />
                      {!navCollapsed && <span className="flex-1">{label}</span>}
                    </>
                  )}
                </NavLink>
              ))}
            </div>
          )}
        </nav>

        {/* 全局行情开关 — 收起时只显示状态指示点 */}
        {navCollapsed ? (
          <div className="border-t border-border px-2 py-2.5 shrink-0 flex justify-center">
            <button
              onClick={() => handleToggle(!realtimeEnabled)}
              disabled={realtimeToggleDisabled}
              aria-label={realtimeToggleTitle}
              aria-busy={toggleQuote.isPending}
              title={realtimeToggleTitle}
              className="flex items-center justify-center rounded-btn p-1.5 transition-colors hover:bg-elevated/70 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <span className={`inline-block h-2 w-2 rounded-full ${realtimeIndicatorClass}`} />
            </button>
          </div>
        ) : (
        <div className="border-t border-border px-3 py-2.5 shrink-0">
          {realtimeUnavailable && !realtimeProviderName ? (
            <div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-secondary truncate">即時行情</span>
                <span className="text-[10px] text-muted/80 bg-elevated px-1.5 py-0.5 rounded">
                  不可用
                </span>
              </div>
              <div className="mt-1.5 text-[10px] leading-snug text-muted">
                目前資料來源無即時行情權限,
                <button
                  type="button"
                  onClick={() => navigate('/settings?tab=data-sources')}
                  className="mx-0.5 text-accent/80 hover:text-accent hover:underline"
                >
                  前往設定資料來源
                </button>
              </div>
            </div>
          ) : (
            /* 实时可用 — 开关 + 跳转设置 */
            <div className="flex items-center gap-2">
              <div className="flex min-w-0 flex-1 items-center gap-2">
                <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${realtimeIndicatorClass}`} />
                <div className="min-w-0">
                  <div className="text-xs font-medium leading-none text-foreground">即時行情</div>
                  <div className="mt-1 flex min-w-0 items-center gap-1 text-[10px] leading-none">
                    <span className="truncate text-muted">{realtimeProviderName || realtimeModeLabel}</span>
                    <span className="shrink-0 text-border" aria-hidden="true">·</span>
                    <span className={`shrink-0 ${realtimeStatusClass}`}>{realtimeStatusLabel}</span>
                  </div>
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <button
                  onClick={() => navigate('/settings?tab=monitoring')}
                  aria-label="開啟即時監控設定"
                  className="flex h-7 w-7 items-center justify-center rounded-btn text-muted transition-colors hover:bg-elevated hover:text-foreground"
                  title="即時監控設定"
                >
                  <Settings className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  role="switch"
                  aria-checked={realtimeEnabled}
                  aria-label={realtimeToggleTitle}
                  aria-busy={toggleQuote.isPending}
                  onClick={() => handleToggle(!realtimeEnabled)}
                  disabled={realtimeToggleDisabled}
                  title={realtimeToggleTitle}
                  className={cn(
                    'relative inline-flex h-5 w-9 items-center rounded-full border transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 focus-visible:ring-offset-1 focus-visible:ring-offset-surface',
                    realtimeEnabled
                      ? 'border-accent/50 bg-accent shadow-[0_0_6px_rgba(59,130,246,0.25)]'
                      : 'border-border bg-elevated hover:border-muted',
                    realtimeToggleDisabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer',
                  )}
                >
                  <span className={cn(
                    'inline-block h-3.5 w-3.5 rounded-full border border-black/5 bg-white shadow-sm transition-transform duration-200',
                    realtimeEnabled ? 'translate-x-[18px]' : 'translate-x-0.5',
                  )} />
                </button>
              </div>
            </div>
          )}

          {/* 状态提示 */}
          {realtimeEnabled
            && (!realtimeUnavailable || realtimeProviderName)
            && (isPaused || (isWatchlistMode && !dismissFreeHint && !realtimeProviderName))
            && (
              <div className="mt-1.5 text-[10px] leading-snug space-y-0.5">
                {isWatchlistMode && !dismissFreeHint && !realtimeProviderName && (
                  <div className="flex items-start gap-1 text-amber-400/80">
                    <span className="flex-1">自選即時模式監控前 5 檔,全市場即時依賴資料來源支援</span>
                    <button
                      onClick={() => setDismissFreeHint(true)}
                      className="text-amber-400/50 hover:text-amber-400 shrink-0 transition-colors"
                      title="關閉提示"
                    >
                      <X className="h-2.5 w-2.5" />
                    </button>
                  </div>
                )}
                {isPaused && (
                  <div className="text-warning/80">資料同步進行中,即時行情已暫時暫停</div>
                )}
              </div>
            )}
          {/* Phase 8B-2: 侧边栏指数卡片目前只有 A 股指数(CORE_INDEXES)可选,
              尚无可靠台股大盘指数数据源(REQUIRES BACKEND SUPPORT)。跟随「中國 A 股
              （選配）」开关: 关闭时不展示 A 股指数、也不假造台股指数;开启后原样可用。 */}
          {showAshareLegacy && showSidebarQuotes && !isWatchlistMode && (!realtimeUnavailable || !!realtimeProviderName) && (
            <SidebarIndexQuotes rows={sidebarIndexQuotes?.rows} items={sidebarIndexes} />
          )}
        </div>
        )}

        <div className={cn('border-t border-border py-3 shrink-0', navCollapsed ? 'px-2 flex flex-col items-center gap-1' : 'px-2')}>
          <div className={navCollapsed ? 'flex flex-col items-center gap-1' : 'flex items-center gap-1'}>
            <ThemeToggle />
            <NavLink
              to="/settings"
              title={navCollapsed ? '設定' : undefined}
              className={({ isActive }) =>
                cn(
                  'group relative flex items-center rounded-btn text-sm transition-all duration-150 ease-smooth',
                  navCollapsed ? 'justify-center px-0 py-2' : 'flex-1 gap-3 px-3 py-2',
                  isActive
                    ? 'bg-elevated text-foreground font-medium'
                    : 'text-foreground/75 hover:bg-elevated/70 hover:text-foreground',
                )
              }
            >
              {({ isActive }) => (
                <>
                  <span
                    className={cn(
                      'pointer-events-none absolute left-0 top-1/2 h-4 -translate-y-1/2 w-[2.5px] rounded-full bg-accent transition-opacity duration-150',
                      isActive ? 'opacity-100 shadow-[0_0_8px_rgba(59,130,246,0.6)]' : 'opacity-0',
                    )}
                  />
                  <Settings className={cn('h-4 w-4 shrink-0 transition-colors', isActive ? 'text-accent' : 'text-foreground/60 group-hover:text-foreground/85')} />
                  {!navCollapsed && <span>設定</span>}
                  {!navCollapsed && version && (
                    <span className="ml-auto font-mono text-[10px] text-muted/50 select-none shrink-0">
                      {version}
                    </span>
                  )}
                </>
              )}
            </NavLink>
          </div>
        </div>
      </aside>

      <motion.main
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
        className="h-full overflow-auto scrollbar-gutter-stable"
      >
        {streamStatus === 'reconnecting' && (
          <div
            role="status"
            aria-live="polite"
            className="fixed bottom-4 left-1/2 z-[9998] flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-warning/30 bg-warning/10 px-2.5 py-1 text-[11px] font-medium text-warning shadow-lg backdrop-blur-md"
          >
            <WifiOff className="h-3 w-3 shrink-0 animate-pulse" />
            与服务连接已断开 · 正在重连
          </div>
        )}
        <Suspense
          fallback={
            <div className="flex items-center justify-center py-24">
              <Loader2 className="h-5 w-5 animate-spin text-muted" />
            </div>
          }
        >
          <Outlet />
        </Suspense>
      </motion.main>
      <ToastContainer />
      <AlertToastContainer />
      <StockAnalysisHost />
      <StockAnalysisBubble />
    </div>
  )
}

import { useEffect, useMemo, useRef, useState, Suspense } from 'react'
import { NavLink, Outlet, useNavigate, useLocation } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { useQuoteStream, useQuoteStreamStatus } from '@/lib/useQuoteStream'
import { ToastContainer, toast } from '@/components/Toast'
import { AlertToastContainer } from '@/components/AlertToast'
import {
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
import { api } from '@/lib/api'
import { cn } from '@/lib/cn'
import { resolveWatchlistGroupColor } from '@/lib/watchlist-group-colors'
import { computeGroupPcts, groupPctColor, groupPctTitle } from '@/lib/watchlistGroupStats'
import { fmtPct } from '@/lib/format'
import { toggleTheme, useTheme } from '@/lib/theme'
import { setCurrentTotal as setAlertTotal, useUnreadAlerts } from '@/lib/monitorBadge'
import { ExtensionSlot } from '@/extensions/ExtensionSlot'
import { getFrontendExtensionNavigation } from '@/extensions/registry'
import { CORE_NAV as nav } from '@/lib/navigation'
import type { LucideIcon } from 'lucide-react'

// 品牌色 — 只用於 logo / brand 區域,不影響功能語義色
const BRAND = '#8B5CF6'

// Phase 8B-2.1: nav 的定義已抽到 @/lib/navigation.ts (CORE_NAV), 與
// MenuSettings.tsx 共用同一份 metadata。此處用別名 import 保持下方既有
// 代碼(nav.findIndex 等)不必改名。

/** 亮/暗主題切換 — 狀態存 localStorage, 生效見 lib/theme.ts */
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

/** 監控中心未讀徽標 — 僅在非監控頁且有未讀時顯示。 */
function MonitorBadge({ active }: { active: boolean }) {
  const unread = useUnreadAlerts()
  // 尊重用戶設置: 可在菜單設置裡關閉數字提示
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

// ===== 資料來源卡片 =====
function DataSourceBadge({ providerName }: { providerName: string }) {
  return (
    <NavLink
      to="/settings?tab=data-sources"
      className="group relative flex items-center gap-2 overflow-hidden rounded-md py-1.5 pl-2.5 pr-2 transition-colors duration-150 hover:bg-elevated/70"
      title={`資料來源 · ${providerName}`}
    >
      <span
        className="pointer-events-none absolute inset-y-1.5 left-0 w-[2px] rounded-full bg-accent/50 transition-colors group-hover:bg-accent"
      />
      <DatabaseZap className="h-3.5 w-3.5 shrink-0 text-muted group-hover:text-accent transition-colors" />
      <span className="min-w-0 truncate text-[11px] font-medium text-secondary group-hover:text-foreground transition-colors">
        {providerName || '台灣官方資料源'}
      </span>
      <span className="h-1.5 w-1.5 rounded-full shrink-0 bg-emerald-500" />
      <span className="ml-auto inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium leading-none text-muted bg-muted/20">
        官方公開
      </span>
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
  // ===== 共享 hooks (替代內聯 useQuery) =====
  const { data: settingsState } = useSettings()
  const { data: versionData } = useVersion()
  const { data: prefs } = usePreferences()
  // 數據源列表 (用於實時行情狀態顯示當前數據源名稱)
  const { data: dataSources } = useQuery({
    queryKey: QK.dataSources,
    queryFn: api.dataSources,
    staleTime: 60_000,
  })
  // poll=true: 全局唯一開啟條件輪詢 (非交易時段 60s 兜底, 交易時段靠 SSE)
  const { data: quoteStatus } = useQuoteStatus({ poll: true })

  // 自選分組 — 僅當用戶開啟「顯示在側邊欄」時拉取
  const groupsInNav = prefs?.watchlist_groups_in_nav ?? false
  const location = useLocation()
  const { data: watchlistGroupsData } = useQuery({
    queryKey: QK.watchlistGroups,
    queryFn: api.watchlistGroups,
    enabled: groupsInNav,
    staleTime: 60_000,
  })
  const watchlistGroups = watchlistGroupsData?.groups ?? []
  // 自選二級菜單展開狀態 — 默認當前在自選頁時展開
  const [watchlistNavExpanded, setWatchlistNavExpanded] = useState(location.pathname === '/watchlist')

  // 側邊欄收起狀態 — 持久化到 localStorage
  const [navCollapsed, setNavCollapsed] = useState(() => {
    if (typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches) return true
    try { return localStorage.getItem('tf-nav-collapsed') === '1' } catch { return false }
  })

  // 分組等權平均漲跌幅 — 複用 watchlist/enriched 查詢緩存(與自選頁同 key,
  // 盤中隨 SSE 刷新)。可見性門控: 子菜單實際可見(側欄展開 + 二級菜單展開)
  // 時才拉取, 收起狀態下不為隱藏 UI 發請求。
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

  // 數據同步狀態輪詢: 有活躍 job 時「數據」菜單項顯示轉圈
  const { data: pipelineJobs } = useQuery({
    queryKey: QK.pipelineJobs,
    queryFn: () => api.pipelineJobs(1),
    refetchInterval: (query) => (query.state.data?.active_id ? 2000 : 15000),
    refetchIntervalInBackground: true,
  })
  const isDataSyncing = !!pipelineJobs?.active_id

  // 數據同步完成的"瞬時反饋": isDataSyncing 從 true→false 時顯示綠色對勾,
  // 閃爍約 3 秒後自動消失。
  const [dataSyncJustDone, setDataSyncJustDone] = useState(false)
  const prevSyncingRef = useRef(false)
  useEffect(() => {
    // 僅在"剛結束"(true→false)且非首次掛載時觸發
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
  // 自選實時模式限制提示: 可手動關閉, 不持久化 (刷新後恢復顯示)
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
  // SSE: 行情更新時自動刷新相關 queries + 告警通知
  useQuoteStream(realtimeEnabled, prefs?.sse_refresh_pages)
  // 實時 SSE 連接狀態 — 斷開時底部顯示提示, 提示可能漏策略告警
  const streamStatus = useQuoteStreamStatus()

  const toggleQuote = useToggleRealtimeQuotes()
  const isRunning = quoteStatus?.running ?? false
  const isTrading = quoteStatus?.is_trading_hours ?? false
  // 管道/數據修正運行期間實時行情被臨時暫停 — 此時禁止開啟
  const isPaused = quoteStatus?.paused ?? false
  // 實時模式以 quote_status 為準 (數據源無關): none=不可用 / watchlist=自選實時 / full_market=全市場
  const quoteMode = quoteStatus?.mode ?? 'none'
  const realtimeUnavailable = quoteMode === 'none'
  const isWatchlistMode = quoteMode === 'watchlist'
  const realtimeModeLabel = isWatchlistMode ? '自選股' : '全市場'
  // 當前實時行情數據源名稱 (custom 時顯示源名, tickflow 時不顯示)
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

  // 當前主數據源 (用於側邊欄數據源狀態卡)
  const activeProvider = prefs?.daily_data_provider || 'taiwan'
  const activeProviderName = (activeProvider === 'taiwan' || activeProvider === 'tickflow')
    ? '台灣官方資料源'
    : (dataSources?.custom?.find(s => s.name === activeProvider)?.display_name || activeProvider)

  // 輪詢觸發記錄總數 → 更新監控中心徽標 (每 15 秒; 後台標籤頁由 SSE 事件驅動, 不輪詢)
  const alertsTotalQuery = useQuery({
    queryKey: ['alerts-total'],
    queryFn: () => api.alertsList({ days: 7, limit: 1 }),
    refetchInterval: 15000,
    select: (data) => data.total,
  })
  // 只在拿到真實總數時同步徽標 (避免 data=undefined 時傳 0 重置 lastSeen)
  const alertsTotal = alertsTotalQuery.data
  useEffect(() => {
    if (alertsTotal != null) setAlertTotal(alertsTotal)
  }, [alertsTotal])

  // 合併內置頁面 + 擴展導航
  type NavItem = { to: string; label: string; icon: LucideIcon; badge?: string }
  const extensionNav: NavItem[] = getFrontendExtensionNavigation().map(item => ({
    to: item.route.path,
    label: item.label,
    icon: item.icon,
    badge: item.badge,
  }))

  const allNav: NavItem[] = [...nav, ...extensionNav]
  const savedOrder = prefs?.nav_order ?? []

  const navItems = savedOrder.length > 0
    ? (() => {
        const byTo = new Map(allNav.map(n => [n.to, n]))
        const ordered = (savedOrder
          .map(id => byTo.get(id))
          .filter(Boolean)) as typeof allNav
        const seen = new Set(ordered.map(n => n.to))
        const merged = [...ordered]
        for (const item of allNav) {
          if (seen.has(item.to)) continue
          // 未保存過排序的新條目: 內置頁插回默認位置(排在已保存的默認前驅之後),
          // 分析/擴展菜單仍追加到末尾
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
  const visibleNavItems = navItems.filter(n => !hiddenIds.has(n.to))

  const handleToggle = async (enabled: boolean) => {
    // 開啟時重新校驗實時權限 (以 quote_status 的數據源無關判定為準)
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
    // 僅在交易時段立即獲取一次行情
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
          {/* Brand block — 收起時只顯 logo 居中 */}
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
                Nanachi 的台股監控看板
              </div>
            )}
            {/* 收起/展開 按鈕 */}
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

          {/* 狀態卡 — 收起時隱藏 */}
          {!navCollapsed && (
            <div className="mt-2.5 border-t border-border/60 pt-1">
              <DataSourceBadge
                providerName={activeProviderName}
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
            // 「自選」項 — 開啟分組側欄且未整體收起時, 渲染為可展開父項 + 二級分組
            const isWatchlistExpandable = to === '/watchlist' && groupsInNav && !navCollapsed && watchlistGroups.length > 0
            return (
              <div key={to}>
                {isWatchlistExpandable ? (
                  /* 可展開的自選父項 — 點擊切換展開, 不直接跳頁 */
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
                  /* 普通菜單項 */
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
                        {/* active 左側 accent 豎條指示 */}
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
                        {/* 數據同步狀態: 同步中轉圈, 剛完成顯示綠色對勾閃爍 3 秒 */}
                        {to === '/data' && isDataSyncing && !navCollapsed && (
                          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-accent" />
                        )}
                        {to === '/data' && !isDataSyncing && dataSyncJustDone && !navCollapsed && (
                          <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-bull animate-pulse" />
                        )}
                        {/* 監控中心徽標: 僅非監控頁且有未讀時顯示 */}
                        {to === '/monitor' && !navCollapsed && <MonitorBadge active={isActive} />}
                      </>
                    )}
                  </NavLink>
                )}

                {/* 自選分組二級子菜單 — 展開時顯示 */}
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
        </nav>

        {/* 全局行情開關 — 收起時只顯示狀態指示點 */}
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
            /* 實時可用 — 開關 + 跳轉設置 */
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

          {/* 狀態提示 */}
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
            與服務連線已中斷 · 正在重連
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
    </div>
  )
}

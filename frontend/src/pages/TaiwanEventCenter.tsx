import { useState, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  CalendarDays,
  AlertTriangle,
  Info,
  ShieldAlert,
  ArrowUpRight,
  Search,
  BellRing,
  CheckCircle2,
  RefreshCw,
  ExternalLink,
  Loader2,
  TrendingUp,
} from 'lucide-react'
import { api, type MarketEventSeverity } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { toast } from '@/components/Toast'
import { cn } from '@/lib/cn'
import { storage } from '@/lib/storage'
import { buildPortfolioPositions, isPortfolioTransaction, type PortfolioTransaction } from '@/lib/portfolio'

type EventScope = 'today' | 'week' | 'portfolio' | 'watchlist' | 'all'

const SCOPE_TABS: Array<{ id: EventScope; label: string }> = [
  { id: 'today', label: '今日重大' },
  { id: 'week', label: '本週事件' },
  { id: 'portfolio', label: '我的持股' },
  { id: 'watchlist', label: '我的觀察' },
  { id: 'all', label: '全部市場' },
]

const SEVERITY_CONFIG: Record<
  MarketEventSeverity,
  { label: string; badgeCls: string; icon: typeof AlertTriangle }
> = {
  risk: {
    label: '重大風險',
    badgeCls: 'bg-rose-500/10 text-rose-500 border-rose-500/30 dark:bg-rose-950/40 dark:text-rose-400',
    icon: ShieldAlert,
  },
  attention: {
    label: '需注意',
    badgeCls: 'bg-amber-500/10 text-amber-500 border-amber-500/30 dark:bg-amber-950/40 dark:text-amber-400',
    icon: AlertTriangle,
  },
  info: {
    label: '參考資訊',
    badgeCls: 'bg-sky-500/10 text-sky-500 border-sky-500/30 dark:bg-sky-950/40 dark:text-sky-400',
    icon: Info,
  },
}

export function TaiwanEventCenter() {
  const qc = useQueryClient()
  const [activeScope, setActiveScope] = useState<EventScope>('today')
  const [severityFilter, setSeverityFilter] = useState<'all' | MarketEventSeverity>('all')
  const [typeFilter, setTypeFilter] = useState<string>('all')
  const [searchQuery, setSearchQuery] = useState('')

  // Watchlist query for scope filtering
  const watchlist = useQuery({
    queryKey: QK.watchlist,
    queryFn: api.watchlistList,
    staleTime: 60_000,
  })

  // Portfolio positions from storage for scope filtering
  const portfolioSymbols = useMemo(() => {
    try {
      const raw = storage.portfolioTransactions.get([])
      if (Array.isArray(raw) && raw.every(isPortfolioTransaction)) {
        return buildPortfolioPositions(raw as PortfolioTransaction[])
          .filter(p => p.shares > 0)
          .map(p => p.symbol)
      }
    } catch {
      // Local ledger unreadable, fallback to empty
    }
    return []
  }, [])

  // Target symbols based on scope
  const targetSymbols = useMemo(() => {
    if (activeScope === 'watchlist') {
      return (watchlist.data?.symbols ?? []).map(s => s.symbol)
    }
    if (activeScope === 'portfolio') {
      return portfolioSymbols
    }
    return undefined
  }, [activeScope, watchlist.data, portfolioSymbols])

  // Events query
  const eventsQuery = useQuery({
    queryKey: QK.taiwanEvents(activeScope, targetSymbols?.join(','), undefined),
    queryFn: () =>
      api.taiwanEvents({
        scope: activeScope,
        symbols: targetSymbols,
        limit: 300,
      }),
    staleTime: 5 * 60 * 1000,
  })

  // Event candidates query
  const candidatesQuery = useQuery({
    queryKey: QK.taiwanEventCandidates(10),
    queryFn: () => api.taiwanEventCandidates(10),
    staleTime: 5 * 60 * 1000,
  })

  // Check alerts mutation
  const checkAlertsMutation = useMutation({
    mutationFn: () => {
      const symbolsToAlert = Array.from(
        new Set([
          ...(watchlist.data?.symbols ?? []).map(s => s.symbol),
          ...portfolioSymbols,
        ]),
      )
      return api.checkTaiwanEventAlerts(symbolsToAlert)
    },
    onSuccess: res => {
      if (res.triggered_count > 0) {
        toast(`已成功檢查並觸發 ${res.triggered_count} 則重大事件提醒`, 'success')
      } else {
        toast('持股與自選股目前無新增重大事件')
      }
      void qc.invalidateQueries({ queryKey: QK.alerts(undefined) })
    },
    onError: err => {
      toast(`檢查事件提醒失敗: ${err.message}`, 'error')
    },
  })

  // Filtered events
  const filteredEvents = useMemo(() => {
    let list = eventsQuery.data?.events ?? []
    if (severityFilter !== 'all') {
      list = list.filter(e => e.severity === severityFilter)
    }
    if (typeFilter !== 'all') {
      list = list.filter(e => e.event_type === typeFilter)
    }
    if (searchQuery.trim()) {
      const q = searchQuery.trim().toLowerCase()
      list = list.filter(
        e =>
          e.symbol.toLowerCase().includes(q) ||
          e.code.toLowerCase().includes(q) ||
          e.name.toLowerCase().includes(q) ||
          e.title.toLowerCase().includes(q) ||
          e.summary.toLowerCase().includes(q),
      )
    }
    return list
  }, [eventsQuery.data?.events, severityFilter, typeFilter, searchQuery])

  // Count by severity
  const severityCounts = useMemo(() => {
    const raw = eventsQuery.data?.events ?? []
    return {
      all: raw.length,
      risk: raw.filter(e => e.severity === 'risk').length,
      attention: raw.filter(e => e.severity === 'attention').length,
      info: raw.filter(e => e.severity === 'info').length,
    }
  }, [eventsQuery.data?.events])

  return (
    <div className="mx-auto min-h-screen max-w-7xl p-3 md:p-6 space-y-4">
      {/* Top Banner & Title */}
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between rounded-card border border-border bg-surface/85 p-4 shadow-sm backdrop-blur-sm">
        <div>
          <div className="flex items-center gap-2">
            <span className="h-4 w-1 rounded-full bg-gradient-to-b from-accent to-accent/40" />
            <CalendarDays className="h-5 w-5 text-accent" />
            <h1 className="text-lg font-bold text-foreground tracking-tight">台股事件中心</h1>
            <span className="rounded bg-accent/10 px-2 py-0.5 text-xs font-semibold text-accent">
              A11 官方資料源
            </span>
          </div>
          <p className="mt-1 text-xs text-secondary">
            整合證交所／櫃買中心官方處置股、注意股、暫停交易、減資、面額變更與除權息公告，100% 確定性權威追蹤。
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void eventsQuery.refetch()}
            disabled={eventsQuery.isFetching}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface px-3 py-1.5 text-xs font-medium text-foreground hover:bg-elevated transition-colors disabled:opacity-50"
          >
            <RefreshCw className={cn('h-3.5 w-3.5', eventsQuery.isFetching && 'animate-spin')} />
            重新整理
          </button>
          <button
            type="button"
            onClick={() => checkAlertsMutation.mutate()}
            disabled={checkAlertsMutation.isPending}
            className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-accent-foreground hover:bg-accent/90 transition-colors shadow-sm disabled:opacity-50"
          >
            <BellRing className="h-3.5 w-3.5" />
            檢查持股事件提醒
          </button>
        </div>
      </div>

      {/* Scope Navigation Tabs */}
      <div className="flex flex-wrap items-center gap-1.5 border-b border-border pb-2">
        {SCOPE_TABS.map(tab => {
          const isActive = activeScope === tab.id
          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveScope(tab.id)}
              className={cn(
                'rounded-lg px-3.5 py-1.5 text-xs font-medium transition-all',
                isActive
                  ? 'bg-accent text-accent-foreground shadow-sm'
                  : 'bg-elevated/60 text-secondary hover:bg-elevated hover:text-foreground',
              )}
            >
              {tab.label}
              {activeScope === tab.id && eventsQuery.data?.total !== undefined && (
                <span className="ml-1.5 rounded-full bg-base/20 px-1.5 py-0.2 text-[10px] font-mono">
                  {eventsQuery.data.total}
                </span>
              )}
            </button>
          )
        })}
      </div>

      {/* Filter Toolbar */}
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between rounded-card border border-border bg-surface/60 p-3 text-xs">
        {/* Severity Filter Pills */}
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-muted mr-1">嚴重等級：</span>
          <button
            type="button"
            onClick={() => setSeverityFilter('all')}
            className={cn(
              'rounded-md px-2.5 py-1 text-xs transition-colors',
              severityFilter === 'all'
                ? 'bg-foreground text-background font-semibold'
                : 'bg-elevated text-secondary hover:text-foreground',
            )}
          >
            全部 ({severityCounts.all})
          </button>
          <button
            type="button"
            onClick={() => setSeverityFilter('risk')}
            className={cn(
              'inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs border transition-colors',
              severityFilter === 'risk'
                ? 'bg-rose-500 text-white font-semibold border-rose-500'
                : 'border-rose-500/30 bg-rose-500/5 text-rose-500 hover:bg-rose-500/10',
            )}
          >
            <ShieldAlert className="h-3 w-3" />
            重大風險 ({severityCounts.risk})
          </button>
          <button
            type="button"
            onClick={() => setSeverityFilter('attention')}
            className={cn(
              'inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs border transition-colors',
              severityFilter === 'attention'
                ? 'bg-amber-500 text-white font-semibold border-amber-500'
                : 'border-amber-500/30 bg-amber-500/5 text-amber-500 hover:bg-amber-500/10',
            )}
          >
            <AlertTriangle className="h-3 w-3" />
            需注意 ({severityCounts.attention})
          </button>
          <button
            type="button"
            onClick={() => setSeverityFilter('info')}
            className={cn(
              'inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs border transition-colors',
              severityFilter === 'info'
                ? 'bg-sky-500 text-white font-semibold border-sky-500'
                : 'border-sky-500/30 bg-sky-500/5 text-sky-500 hover:bg-sky-500/10',
            )}
          >
            <Info className="h-3 w-3" />
            參考訊息 ({severityCounts.info})
          </button>
        </div>

        {/* Search input & Event Type Selector */}
        <div className="flex items-center gap-2">
          <select
            value={typeFilter}
            onChange={e => setTypeFilter(e.target.value)}
            className="rounded-md border border-border bg-base px-2.5 py-1 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
          >
            <option value="all">全部事件類型</option>
            <option value="disposition">處置證券</option>
            <option value="warning">注意股票</option>
            <option value="suspended_trading">暫停交易</option>
            <option value="resume_trading">恢復交易</option>
            <option value="capital_reduction">減資</option>
            <option value="par_change">面額變更/分割</option>
            <option value="cash_dividend">除息 (現金股利)</option>
            <option value="stock_dividend">除權 (股票股利)</option>
            <option value="delisting">終止上市/下市</option>
          </select>

          <div className="relative">
            <Search className="absolute left-2.5 top-2 h-3.5 w-3.5 text-muted" />
            <input
              type="text"
              placeholder="搜尋代號、名稱或內容…"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              className="w-48 rounded-md border border-border bg-base pl-8 pr-2 py-1 text-xs text-foreground placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </div>
        </div>
      </div>

      {/* Main Grid: Left Event List, Right Candidate Sidebar */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* Left: Events Stream (2 Columns on large screens) */}
        <div className="lg:col-span-2 space-y-3">
          {eventsQuery.isLoading ? (
            <div className="flex flex-col items-center justify-center py-16 text-muted">
              <Loader2 className="h-6 w-6 animate-spin text-accent mb-2" />
              <div className="text-xs">正在從官方來源整理市場事件…</div>
            </div>
          ) : eventsQuery.isError ? (
            <div className="rounded-card border border-rose-500/20 bg-rose-500/5 p-6 text-center text-xs text-rose-500">
              事件載入發生異常，請確認後端連線或點擊右上角重試。
            </div>
          ) : filteredEvents.length === 0 ? (
            <div className="rounded-card border border-border bg-surface/50 p-12 text-center text-muted">
              <CheckCircle2 className="mx-auto h-8 w-8 text-muted/60 mb-2" />
              <div className="text-sm font-medium text-foreground">目前範圍無相關事件</div>
              <p className="mt-1 text-xs text-muted">
                {activeScope === 'portfolio' || activeScope === 'watchlist'
                  ? '您的持股或自選股清單目前均無處置、注意或重大除權息事件。'
                  : '目前沒有符合篩選條件的市場事件。'}
              </p>
            </div>
          ) : (
            filteredEvents.map(ev => {
              const sevConfig = SEVERITY_CONFIG[ev.severity] || SEVERITY_CONFIG.info
              const IconComp = sevConfig.icon
              return (
                <div
                  key={ev.id}
                  className="rounded-card border border-border bg-surface/85 p-3.5 shadow-sm transition-all hover:border-accent/40 hover:shadow-md"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <span className={cn('inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-semibold', sevConfig.badgeCls)}>
                        <IconComp className="h-3 w-3" />
                        {sevConfig.label}
                      </span>
                      <span className="rounded bg-elevated px-2 py-0.5 text-[11px] font-medium text-secondary">
                        {ev.event_type_label}
                      </span>
                      <span className="font-mono text-xs text-muted">{ev.event_date}</span>
                    </div>

                    <Link
                      to={`/stocks/${encodeURIComponent(ev.symbol)}`}
                      className="inline-flex items-center gap-1 text-xs font-semibold text-accent hover:underline"
                    >
                      <span>前往個股分析</span>
                      <ArrowUpRight className="h-3.5 w-3.5" />
                    </Link>
                  </div>

                  {/* Stock Headline */}
                  <div className="mt-2 flex items-baseline gap-2">
                    <Link
                      to={`/stocks/${encodeURIComponent(ev.symbol)}`}
                      className="text-base font-bold text-foreground hover:text-accent transition-colors"
                    >
                      {ev.name}
                    </Link>
                    <span className="font-mono text-xs text-muted">{ev.code}</span>
                    <span className="text-[10px] text-muted">({ev.exchange})</span>
                  </div>

                  {/* Event Title & Summary */}
                  <div className="mt-1.5 text-xs font-medium text-foreground leading-snug">
                    {ev.title}
                  </div>
                  <div className="mt-1 text-xs text-secondary leading-relaxed bg-elevated/30 rounded p-2 border border-border/40">
                    {ev.summary}
                  </div>

                  {/* Source & Provenance Footer */}
                  <div className="mt-2.5 flex items-center justify-between text-[10px] text-muted">
                    <div className="flex items-center gap-1.5">
                      <span>來源：{ev.source}</span>
                      {ev.source_url && (
                        <a
                          href={ev.source_url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-0.5 text-accent hover:underline"
                        >
                          官方公告 <ExternalLink className="h-2.5 w-2.5" />
                        </a>
                      )}
                    </div>
                    <span>檢索時間：{new Date(ev.retrieved_at).toLocaleTimeString('zh-TW')}</span>
                  </div>
                </div>
              )
            })
          )}
        </div>

        {/* Right Sidebar: Candidates & Guidance */}
        <div className="space-y-4">
          {/* Candidate Stocks Card */}
          <div className="rounded-card border border-border bg-surface/85 p-3.5 shadow-sm">
            <div className="flex items-center justify-between mb-2.5">
              <div className="flex items-center gap-1.5">
                <TrendingUp className="h-4 w-4 text-accent" />
                <h2 className="text-xs font-bold text-foreground">值得進一步查看標的</h2>
              </div>
              <span className="text-[10px] text-muted">事件驅動清單</span>
            </div>

            <p className="text-[11px] text-secondary mb-3 leading-relaxed">
              根據近期除權息、處置解列、注意股票與重大公司行動確定性彙整，點擊可直接深入研究個股：
            </p>

            {candidatesQuery.isLoading ? (
              <div className="py-6 text-center text-xs text-muted">
                <Loader2 className="mx-auto h-4 w-4 animate-spin text-accent mb-1" />
                正在分析候選股票…
              </div>
            ) : (candidatesQuery.data?.candidates ?? []).length === 0 ? (
              <div className="py-4 text-center text-xs text-muted">暫無事件候選標的</div>
            ) : (
              <div className="space-y-2">
                {candidatesQuery.data?.candidates.map(c => (
                  <Link
                    key={c.symbol}
                    to={`/stocks/${encodeURIComponent(c.symbol)}`}
                    className="block rounded-lg border border-border/60 bg-elevated/40 p-2.5 hover:border-accent/40 hover:bg-elevated transition-all"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-1.5">
                        <span className="font-bold text-xs text-foreground">{c.name}</span>
                        <span className="font-mono text-[11px] text-muted">{c.code}</span>
                      </div>
                      <span className="rounded bg-accent/10 px-1.5 py-0.5 text-[10px] font-medium text-accent">
                        {c.tag}
                      </span>
                    </div>
                    <div className="mt-1 text-[11px] text-secondary leading-snug">
                      {c.reason}
                    </div>
                  </Link>
                ))}
              </div>
            )}
          </div>

          {/* Authority and Compliance Guidance */}
          <div className="rounded-card border border-border bg-surface/50 p-3.5 text-xs text-muted leading-relaxed space-y-2">
            <div className="flex items-center gap-1 font-semibold text-foreground text-[11px]">
              <Info className="h-3.5 w-3.5 text-accent" />
              台股事件中心資料原則
            </div>
            <ul className="list-disc pl-4 space-y-1 text-[11px]">
              <li>
                <strong>官方事實權威</strong>：處置股與注意股直接串接台灣證券交易所（TWSE）與櫃買中心（TPEx）開放資料，無第三方二手扭曲。
              </li>
              <li>
                <strong>嚴重度純確定性</strong>：嚴重等級僅依制度規定劃分（暫停交易與下市為 risk、處置與注意為 attention、除權息與營收為 info），無黑箱 AI 自行評分。
              </li>
              <li>
                <strong>單一入口導航</strong>：點擊任意事件股票皆無縫跳轉既有權威個股分析工作台（<code>/stocks/:symbol</code>），保持單一產品體驗。
              </li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  )
}

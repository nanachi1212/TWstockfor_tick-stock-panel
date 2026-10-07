import { useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowUpRight, ChevronDown, ChevronUp, Compass, Loader2, Plus, Sparkles } from 'lucide-react'
import {
  api,
  type BeginnerCandidate,
  type BeginnerDirection,
  type BeginnerMarketState,
  type BeginnerMarketSummary,
  type BeginnerSelectionState,
  type BeginnerSignalStrength,
  type BeginnerTechnicalPanel,
  type RadarLive,
  type RadarLiveStatus,
  type RadarSource,
  type TaiwanAIResearchResponse,
} from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import { FredAttribution } from '@/components/ExternalContextCard'

export const STATE_LABEL: Record<BeginnerSelectionState, string> = {
  watch: '值得關注',
  wait_pullback: '等待回檔',
  wait_breakout: '等待突破',
  no_chase: '不宜追價',
  skip: '暫時略過',
}

const STATE_CLASS: Record<BeginnerSelectionState, string> = {
  watch: 'border-accent/40 bg-accent/10 text-accent',
  wait_pullback: 'border-warning/40 bg-warning/10 text-warning',
  wait_breakout: 'border-warning/40 bg-warning/10 text-warning',
  no_chase: 'border-danger/40 bg-danger/10 text-danger',
  skip: 'border-border bg-elevated text-muted',
}

const STRENGTH_LABEL: Record<BeginnerSignalStrength, string> = { weak: '弱', medium: '中', strong: '強' }

export const MARKET_LABEL: Record<BeginnerMarketState, string> = {
  favorable: '適合找機會',
  neutral: '中性',
  cautious: '偏保守',
  unavailable: '資料不足',
}

const MARKET_CLASS: Record<BeginnerMarketState, string> = {
  favorable: 'bg-bull/10 text-bull',
  neutral: 'bg-elevated text-foreground',
  cautious: 'bg-bear/10 text-bear',
  unavailable: 'bg-elevated text-muted',
}

const DIRECTION_LABEL: Record<BeginnerDirection, string> = {
  positive: '偏正向',
  neutral: '中性',
  negative: '偏負向',
  unavailable: '不可用',
}

const TREND_LABEL = { strong: '偏強', neutral: '中性', weak: '偏弱', unavailable: '資料不足' } as const
const VOLATILITY_LABEL = { low: '低', normal: '一般', high: '偏大', unavailable: '資料不足' } as const

function formatPrice(value: number | null) {
  return value == null ? '資料不足' : value.toLocaleString('zh-TW', { maximumFractionDigits: 2 })
}

function formatPct(value: number | null) {
  return value == null ? '資料不足' : `${value >= 0 ? '+' : ''}${value.toFixed(1)}%`
}

function formatShares(value: number | null) {
  return value == null ? '資料不足' : `${Math.round(value / 1000).toLocaleString('zh-TW')} 張`
}

function formatTaipeiTime(value: string | null) {
  if (!value) return null
  const stamp = new Date(value)
  return Number.isNaN(stamp.getTime()) ? null : stamp.toLocaleTimeString('zh-TW', {
    timeZone: 'Asia/Taipei', hour12: false,
  })
}

function TechnicalSection({ number, title, children }: { number: string; title: string; children: React.ReactNode }) {
  return (
    <section className="min-w-0 rounded-xl border border-border/70 bg-base/45 p-3" aria-label={title}>
      <h3 className="mb-2 text-xs font-semibold text-muted"><span className="mr-1 text-accent">{number}</span>{title}</h3>
      {children}
    </section>
  )
}

export const STRENGTH_DISCLAIMER = '訊號強度代表目前條件符合程度，不代表上漲機率。'

export function StateBadge({ state }: { state: BeginnerSelectionState }) {
  return (
    <span className={cn('inline-flex shrink-0 items-center rounded-full border px-2 py-0.5 text-xs font-semibold', STATE_CLASS[state])}>
      {STATE_LABEL[state]}
    </span>
  )
}

export function MarketSummaryCard({ market, compact = false }: { market: BeginnerMarketSummary; compact?: boolean }) {
  return (
    <section aria-label="今天市場怎麼看" className="rounded-card border border-border bg-surface/85 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Compass className="h-4 w-4 text-accent" />
        <h2 className="text-sm font-semibold text-foreground">今天市場怎麼看？</h2>
        <span className={cn('rounded-full px-2 py-0.5 text-xs font-semibold', MARKET_CLASS[market.state])}>
          {MARKET_LABEL[market.state]}
        </span>
        {market.as_of && <span className="text-[11px] text-muted">資料日 {market.as_of}</span>}
      </div>
      <p className="mt-1.5 text-sm font-medium text-foreground">{market.headline}</p>
      <p className="mt-0.5 break-words text-xs text-muted">{market.explanation}</p>
      {!compact && <p className="mt-1 break-words text-xs text-foreground/90">{market.guidance}</p>}
    </section>
  )
}

function ReasonList({ items, empty, tone }: { items: string[]; empty: string; tone: 'reason' | 'risk' }) {
  if (items.length === 0) return <p className="text-xs text-muted">{empty}</p>
  return (
    <ul className="space-y-0.5">
      {items.map(text => (
        <li key={text} className="flex gap-1.5 break-words text-xs text-foreground/90">
          <span className={tone === 'reason' ? 'text-bull' : 'text-warning'}>•</span>
          <span className="min-w-0">{text}</span>
        </li>
      ))}
    </ul>
  )
}

const LIVE_CLASS: Record<RadarLiveStatus, string> = {
  in_zone: 'border-accent/40 bg-accent/10 text-accent',
  breakout: 'border-accent/40 bg-accent/10 text-accent',
  near_zone: 'border-warning/40 bg-warning/10 text-warning',
  near_breakout: 'border-warning/40 bg-warning/10 text-warning',
  below_zone: 'border-warning/40 bg-warning/10 text-warning',
  below_stop: 'border-danger/40 bg-danger/10 text-danger',
  waiting: 'border-border bg-elevated text-muted',
  unavailable: 'border-border bg-elevated text-muted',
}

const AI_SETUP_HINT = '請到「設定 → AI」確認模型服務已開啟後重試。'

/** Plain-language AI reading of the same evidence; it never changes the rule-based group or plan levels. */
function AiExplain({ symbol }: { symbol: string }) {
  const query = useQuery({
    queryKey: QK.beginnerAiExplain(symbol),
    queryFn: () => api.taiwanStockAIResearch(symbol) as Promise<TaiwanAIResearchResponse>,
    staleTime: Infinity,
    retry: false,
  })
  const report = query.data?.status === 'success' ? query.data.report : null
  return (
    <div className="rounded-md border border-purple-500/30 bg-purple-500/5 p-2" aria-label="AI 白話說明">
      {query.isFetching && !report ? (
        <p className="flex items-center gap-1.5 text-xs text-muted"><Loader2 className="h-3 w-3 animate-spin" /> AI 正在整理說明，本機模型約需 10～60 秒…</p>
      ) : report ? (
        <div className="space-y-1.5">
          <p className="break-words text-xs text-foreground">{report.overview}</p>
          <div>
            <p className="mb-0.5 text-[11px] font-semibold text-muted">主要風險</p>
            <ReasonList items={report.risk_factors.slice(0, 3).map(r => r.text)} empty="AI 沒有列出明顯風險。" tone="risk" />
          </div>
          <div>
            <p className="mb-0.5 text-[11px] font-semibold text-muted">接下來看什麼</p>
            <ReasonList items={(report.watch_next ?? []).slice(0, 3).map(r => r.text)} empty="AI 沒有列出觀察重點。" tone="reason" />
          </div>
          <p className="text-[10px] text-muted">資料截至 {report.evidence_as_of}・{query.data?.model}・AI 只做解讀，不改變上面的分組與價位。</p>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <p role="alert" className="min-w-0 break-words text-xs text-danger">
            AI 說明暫時無法產生。{query.data?.error_message || AI_SETUP_HINT}
          </p>
          <button type="button" onClick={() => void query.refetch()}
            className="inline-flex min-h-8 items-center rounded-md border border-border px-2.5 text-xs text-foreground hover:border-accent/50">
            重試
          </button>
        </div>
      )}
    </div>
  )
}

const SOURCE_LABEL: Record<Exclude<RadarSource, 'pick'>, string> = { holding: '持股', watchlist: '自選' }

function RadarStrip({ live, plan }: { live: RadarLive; plan: BeginnerCandidate['trade_plan'] }) {
  const time = formatTaipeiTime(live.quote_time)
  return (
    <div className="rounded-md border border-border/70 bg-base/60 p-2" aria-label="盤中狀態">
      <div className="flex flex-wrap items-center gap-2">
        <span className={cn('inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold', LIVE_CLASS[live.status])}>{live.label}</span>
        {live.price != null && (
          <span className="font-mono text-xs text-foreground">現價 {formatPrice(live.price)}{time && <span className="ml-1 text-[10px] text-muted">（{time}）</span>}</span>
        )}
      </div>
      {live.note && <p className="mt-1 break-words text-[11px] text-muted">{live.note}</p>}
      {live.source && (
        <p className="mt-0.5 break-all text-[10px] text-muted">報價來源：{live.source}{live.freshness_class && `・${live.freshness_class}`}</p>
      )}
      {plan && (
        <dl className="mt-1.5 grid grid-cols-[3.5rem_minmax(0,1fr)] gap-x-2 gap-y-0.5 text-xs">
          {plan.entry_semantics === 'breakout_stop'
            ? <><dt className="text-muted">突破價</dt><dd className="font-mono text-foreground">{formatPrice(plan.breakout_trigger)}</dd></>
            : <><dt className="text-muted">承接區</dt><dd className="font-mono text-foreground">{formatPrice(plan.entry_zone_low)}～{formatPrice(plan.entry_zone_high)}</dd></>}
          <dt className="text-muted">失效位</dt><dd className="font-mono text-warning">{formatPrice(plan.stop_price)}</dd>
        </dl>
      )}
    </div>
  )
}

export function PickCard({ candidate, comparison, radar }: {
  candidate: BeginnerCandidate
  comparison?: { checked: boolean; disabled: boolean; onToggle: () => void }
  radar?: { live: RadarLive; sources: RadarSource[] }
}) {
  const qc = useQueryClient()
  const [aiOpen, setAiOpen] = useState(false)
  const [isAdding, setIsAdding] = useState(false)
  const [syncError, setSyncError] = useState<string | null>(null)

  const [addedToWatchlist, setAddedToWatchlist] = useState(false)
  const inWatchlist = addedToWatchlist || Boolean(radar?.sources.includes('watchlist'))
  // The badge follows the real monitor rules (shared query with the Monitor page),
  // not just "in watchlist + has a plan".
  const rulesQuery = useQuery({
    queryKey: QK.taiwanRules,
    queryFn: () => api.taiwanRulesList(),
    enabled: Boolean(radar && inWatchlist && candidate.trade_plan),
  })
  const planRules = (rulesQuery.data?.rules ?? []).filter(rule => rule.source === 'trade_plan'
    && rule.symbol === candidate.symbol && rule.plan_identity === candidate.trade_plan?.plan_identity)
  const isAutoWatched = !isAdding && planRules.some(rule => rule.enabled)
  // Retry only when no rule exists; rules the user turned off stay off (sync keeps them off).
  const canAddAndAutoWatch = Boolean(radar && candidate.trade_plan)
    && (!inWatchlist || (rulesQuery.isSuccess && planRules.length === 0))

  const handleAddAndAutoWatch = async () => {
    if (isAdding) return
    setIsAdding(true)
    setSyncError(null)
    let added = inWatchlist
    try {
      if (!added) {
        await api.watchlistAdd(candidate.symbol, '')
        added = true
        setAddedToWatchlist(true)
      }
      const result = await api.syncPlanRules()
      if (result.skipped.some(item => item.symbol === candidate.symbol)) {
        setSyncError('已加入自選，目前沒有可用的承接計畫。請稍後重試自動監控。')
      }
    } catch {
      setSyncError(added
        ? '已加入自選，但自動監控同步失敗。請重試自動監控。'
        : '加入自選失敗，請稍後再試。')
    } finally {
      if (added) {
        await Promise.all([
          qc.invalidateQueries({ queryKey: QK.beginnerRadarRoot }),
          qc.invalidateQueries({ queryKey: QK.taiwanRules }),
          qc.invalidateQueries({ queryKey: QK.watchlist }),
        ])
      }
      setIsAdding(false)
    }
  }

  const href = `/stocks/${encodeURIComponent(candidate.symbol)}`
  return (
    <article
      aria-label={`${candidate.name} ${candidate.symbol}`}
      className="flex min-w-0 flex-col gap-2 rounded-card border border-border bg-surface/85 p-3"
    >
      {comparison && (
        <label className="flex min-h-9 cursor-pointer items-center gap-2 text-xs text-foreground">
          <input type="checkbox" checked={comparison.checked} disabled={comparison.disabled}
            onChange={comparison.onToggle} aria-label={`比較 ${candidate.name || candidate.symbol} ${candidate.symbol}`}
            className="h-4 w-4 accent-accent" />
          加入比較
        </label>
      )}
      <header className="flex min-w-0 items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-foreground">{candidate.name || candidate.symbol}</p>
          <div className="flex flex-wrap items-center gap-1 font-mono text-[11px] text-muted">
            <span>{candidate.symbol}</span>
            {radar?.sources.filter((s): s is Exclude<RadarSource, 'pick'> => s !== 'pick').map(s => (
              <span key={s} className="ml-1.5 rounded bg-elevated px-1 py-px font-sans text-[10px] text-foreground">{SOURCE_LABEL[s]}</span>
            ))}
            {isAutoWatched && (
              <span className="inline-flex items-center rounded-full bg-emerald-500/10 px-2 py-0.5 font-sans text-[10px] font-semibold text-emerald-600 dark:text-emerald-400 border border-emerald-500/30">
                自動監控中
              </span>
            )}
          </div>
        </div>
        <div className="flex flex-col items-end gap-1">
          <StateBadge state={candidate.selection_state} />
          {candidate.selection_state !== 'skip' && (
            <span className="text-[11px] text-muted" title={STRENGTH_DISCLAIMER}>訊號強度：{STRENGTH_LABEL[candidate.signal_strength]}</span>
          )}
        </div>
      </header>
      {radar && <RadarStrip live={radar.live} plan={candidate.trade_plan} />}
      {candidate.selection_state !== 'skip' ? (
        <div>
          <p className="mb-0.5 text-[11px] font-semibold text-muted">為什麼被選中</p>
          <ReasonList items={candidate.reasons.map(r => r.display_text)} empty="沒有明確的正向理由。" tone="reason" />
        </div>
      ) : (
        <div>
          <p className="mb-0.5 text-[11px] font-semibold text-muted">為什麼暫不操作</p>
          <ReasonList items={candidate.exclusion_reasons.map(r => r.display_text)} empty={candidate.action_summary} tone="risk" />
        </div>
      )}
      <div>
        <p className="mb-0.5 text-[11px] font-semibold text-muted">要注意</p>
        <ReasonList
          items={[...candidate.risks.map(r => r.display_text), ...candidate.data_gaps.slice(0, 1)]}
          empty="目前沒有明顯的負面訊號。"
          tone="risk"
        />
      </div>
      <div className="rounded-md bg-base/60 p-2">
        <p className="text-[11px] font-semibold text-muted">下一步</p>
        <p className="break-words text-sm text-foreground">{candidate.action_summary}</p>
        {candidate.invalidation && <p className="mt-1 break-words text-xs text-muted">失效條件：{candidate.invalidation}</p>}
      </div>
      <div className="mt-auto flex flex-wrap gap-2">
        <Link to={href} className="inline-flex min-h-8 items-center gap-1 rounded-md border border-border px-2.5 text-xs text-foreground hover:border-accent/50 hover:text-accent">
          查看詳細分析 <ArrowUpRight className="h-3 w-3" />
        </Link>
        <button
          type="button"
          onClick={() => setAiOpen(open => !open)}
          aria-expanded={aiOpen}
          className="inline-flex min-h-8 items-center gap-1 rounded-md border border-purple-500/30 px-2.5 text-xs text-purple-400 hover:bg-purple-500/10"
        >
          <Sparkles className="h-3 w-3" /> {aiOpen ? '收起 AI 說明' : 'AI 白話說明'}
        </button>
        {(canAddAndAutoWatch || (inWatchlist && syncError) || isAdding) && (
          <button
            type="button"
            onClick={handleAddAndAutoWatch}
            disabled={isAdding}
            className="inline-flex min-h-8 items-center gap-1 rounded-md bg-accent/10 border border-accent/40 px-2.5 text-xs font-semibold text-accent hover:bg-accent/20 transition-colors disabled:opacity-50 cursor-pointer"
          >
            {isAdding ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />}
            {isAdding ? '同步中…' : inWatchlist ? '重試自動監控' : '加入自選並自動監控'}
          </button>
        )}
      </div>
      {syncError && (
        <p role="alert" className="break-words text-[11px] text-danger">{syncError}</p>
      )}
      {aiOpen && <AiExplain symbol={candidate.symbol} />}
    </article>
  )
}

/** Comparison selection kept in the URL (`?compare=`), shared by the pick list and the entry radar. */
export function useCompareSelection(symbols: string[]) {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const navigating = useRef(false)
  const [opening, setOpening] = useState(false)
  const available = new Set(symbols)
  const selected = [...new Set((params.get('compare') ?? '').split(','))].filter(s => available.has(s)).slice(0, 5)
  const toggle = (symbol: string) => {
    const next = selected.includes(symbol) ? selected.filter(s => s !== symbol) : [...selected, symbol]
    if (next.length > 5) return
    setParams(prev => {
      const updated = new URLSearchParams(prev)
      if (next.length) updated.set('compare', next.join(','))
      else updated.delete('compare')
      return updated
    }, { replace: true })
  }
  const open = () => {
    if (navigating.current || selected.length < 2) return
    navigating.current = true
    setOpening(true)
    navigate(`/picks/compare?${new URLSearchParams({ symbols: [...selected].sort().join(',') })}`)
  }
  const comparisonFor = (symbol: string) => ({
    checked: selected.includes(symbol), disabled: opening || (selected.length === 5 && !selected.includes(symbol)),
    onToggle: () => toggle(symbol),
  })
  return { selected, opening, open, comparisonFor }
}

export function CompareBar({ selected, opening, open }: { selected: string[]; opening: boolean; open: () => void }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 rounded-card border border-accent/25 bg-surface p-3">
      <p className="text-xs text-muted" role="status">已選 {selected.length} / 5 檔 · 請勾選 2 至 5 檔{selected.length === 5 && '，已達上限'}</p>
      <button type="button" disabled={selected.length < 2 || opening}
        className="min-h-9 rounded-md bg-accent px-3 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
        onClick={open}>比較這些股票</button>
    </div>
  )
}

export function PickComparisonList({ candidates, compact = false }: { candidates: BeginnerCandidate[]; compact?: boolean }) {
  const compare = useCompareSelection(candidates.map(c => c.symbol))
  return (
    <div className="space-y-2">
      <CompareBar {...compare} />
      <div className={cn('grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3', compact ? 'gap-1.5' : 'gap-2')}>
        {candidates.map(c => <PickCard key={c.symbol} candidate={c} comparison={compare.comparisonFor(c.symbol)} />)}
      </div>
    </div>
  )
}

export function NotSelectedList({ items }: { items: BeginnerCandidate[] }) {
  const [open, setOpen] = useState(false)
  if (items.length === 0) return null
  return (
    <section aria-label="為什麼沒選" className="rounded-card border border-border bg-surface/85 p-3">
      <button type="button" onClick={() => setOpen(v => !v)} aria-expanded={open} className="flex w-full items-center justify-between text-left">
        <span className="text-sm font-semibold text-foreground">為什麼沒選？（成交最活躍的 {items.length} 檔）</span>
        {open ? <ChevronUp className="h-4 w-4 text-muted" /> : <ChevronDown className="h-4 w-4 text-muted" />}
      </button>
      {open && (
        <ul className="mt-2 divide-y divide-border/50">
          {items.map(item => (
            <li key={item.symbol} className="flex min-w-0 flex-col gap-0.5 py-1.5 sm:flex-row sm:items-center sm:gap-3">
              <Link to={`/stocks/${encodeURIComponent(item.symbol)}`} className="shrink-0 text-xs font-medium text-foreground hover:text-accent">
                {item.name || item.symbol} <span className="font-mono text-muted">{item.symbol}</span>
              </Link>
              <span className="min-w-0 break-words text-xs text-muted">{item.exclusion_reasons[0]?.display_text ?? item.action_summary}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

export function useBeginnerSelection() {
  return useQuery({
    queryKey: QK.beginnerSelection,
    queryFn: () => api.beginnerSelection(20),
    staleTime: 5 * 60 * 1000,
  })
}

export function EmptyPicks({ gaps }: { gaps: string[] }) {
  return (
    <div className="rounded-card border border-dashed border-border bg-surface/60 p-3 text-xs text-muted">
      <p className="text-sm text-foreground">今天沒有符合條件的股票。</p>
      <p className="mt-0.5">寧可不選，也不勉強湊數。{gaps.length > 0 && `原因可能是：${gaps.join('、')}。`}</p>
    </div>
  )
}

export function BeginnerDashboardWidget() {
  const query = useBeginnerSelection()
  const data = query.data
  return (
    <section aria-label="今日選股" className="mb-1.5 space-y-1.5">
      <div className="flex items-center justify-between gap-2 px-0.5">
        <h2 className="text-sm font-semibold text-foreground">今日選股</h2>
        <Link to="/picks" className="text-xs text-accent hover:underline">查看全部</Link>
      </div>
      {query.isLoading && (
        <div className="flex items-center gap-2 rounded-card border border-border bg-surface/80 p-3 text-xs text-muted">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> 正在整理今天的選股…
        </div>
      )}
      {query.isError && <p className="rounded-card border border-border bg-surface/80 p-3 text-xs text-danger">今日選股暫時無法讀取，請稍後再試。</p>}
      {data && (
        <>
          <MarketSummaryCard market={data.market} compact />
          {data.candidates.length === 0 ? (
            <EmptyPicks gaps={data.data_gaps} />
          ) : (
            <PickComparisonList candidates={data.candidates.slice(0, 5)} compact />
          )}
          <p className="px-0.5 text-[11px] text-muted">{STRENGTH_DISCLAIMER}</p>
        </>
      )}
    </section>
  )
}

export function BeginnerStockView({
  symbol,
  advanced,
  onToggleAdvanced,
}: {
  symbol: string
  advanced: boolean
  onToggleAdvanced: () => void
}) {
  const query = useQuery({
    queryKey: QK.beginnerSelectionSymbol(symbol),
    queryFn: () => api.beginnerSelectionSymbol(symbol),
    staleTime: 5 * 60 * 1000,
  })
  const contextQuery = useQuery({
    queryKey: ['external-context', symbol],
    queryFn: () => api.taiwanExternalContext(symbol),
    staleTime: 24 * 60 * 60 * 1000,
    refetchInterval: query => query.state.data?.intraday_context.error_reason === 'config_missing' ? false : 5000,
  })
  const candidate = query.data?.candidate
  const skipped = candidate?.selection_state === 'skip'
  const panel = candidate?.technical_panel
  const liveInnerOuter = contextQuery.data?.intraday_context.data?.inner_outer as BeginnerTechnicalPanel['inner_outer'] | undefined
  const innerOuter = liveInnerOuter ?? panel?.inner_outer
  const innerOuterTime = formatTaipeiTime(innerOuter?.as_of ?? null)
  const fxContext = contextQuery.data?.fx_context ?? candidate?.fx_context
  const macroContext = contextQuery.data?.macro_context ?? candidate?.macro_context
  const secondaryContext = contextQuery.data?.secondary_cross_checks ?? candidate?.secondary_cross_checks
  const fxSummary = typeof fxContext?.data?.summary === 'string' ? fxContext.data.summary : null
  const macroSummary = typeof macroContext?.data?.summary === 'string' ? macroContext.data.summary : null
  const secondaryData = secondaryContext?.data
  const crossCheck = secondaryData && typeof secondaryData.cross_check === 'object' && secondaryData.cross_check !== null
    ? secondaryData.cross_check as Record<string, unknown> : null
  const crossCheckStatus = typeof crossCheck?.status === 'string' ? crossCheck.status : 'unavailable'
  return (
    <section aria-label="這檔股票現在怎麼看" className="rounded-2xl border border-accent/30 bg-surface p-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold text-foreground">這檔股票現在怎麼看？</h2>
        <button
          type="button"
          onClick={onToggleAdvanced}
          aria-expanded={advanced}
          className="inline-flex min-h-8 items-center gap-1 rounded-md border border-border px-2.5 text-xs text-muted hover:border-accent/50 hover:text-accent"
        >
          {advanced ? '收合進階資料' : '展開進階資料'}
          {advanced ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
        </button>
      </div>
      {query.isLoading && <p className="mt-2 flex items-center gap-2 text-xs text-muted"><Loader2 className="h-3.5 w-3.5 animate-spin" /> 整理結論中…</p>}
      {query.isError && <p className="mt-2 text-xs text-danger">初學者結論暫時無法讀取，可展開進階資料查看原始數據。</p>}
      {candidate && panel && innerOuter && (
        <div className="mt-3 grid min-w-0 grid-cols-1 gap-3 lg:grid-cols-2 xl:grid-cols-4">
          <TechnicalSection number="①" title="現在怎麼做">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-semibold text-muted">結論</span>
              <StateBadge state={candidate.selection_state} />
              {!skipped && <span className="text-xs text-muted">訊號強度：{STRENGTH_LABEL[candidate.signal_strength]}</span>}
            </div>
            <p className="mt-2 break-words text-sm font-medium leading-relaxed text-foreground">{panel.summary}</p>
            <div className="mt-2">
              <p className="mb-0.5 text-xs font-semibold text-muted">{skipped ? '為什麼沒被選？' : '理由'}</p>
              <ReasonList
                items={(skipped ? candidate.exclusion_reasons : candidate.reasons).slice(0, 2).map(r => r.display_text)}
                empty={candidate.action_summary}
                tone={skipped ? 'risk' : 'reason'}
              />
            </div>
            <p className="mt-2 text-xs font-semibold text-muted">下一步</p>
            <p className="break-words text-sm text-foreground">{candidate.action_summary}</p>
          </TechnicalSection>

          <TechnicalSection number="②" title="價格位置">
            <dl className="grid grid-cols-[5.25rem_minmax(0,1fr)] gap-x-2 gap-y-1 text-sm">
              <dt className="text-muted">支撐區</dt><dd className="min-w-0 font-mono text-foreground">{panel.support.support_zone_low == null || panel.support.support_zone_high == null ? '資料不足' : `${formatPrice(panel.support.support_zone_low)}～${formatPrice(panel.support.support_zone_high)}`}</dd>
              <dt className="text-muted">最近收盤價</dt><dd className="font-mono text-foreground">{formatPrice(panel.current_price)}{candidate.as_of && <span className="ml-1 text-[10px] text-muted">（資料日 {candidate.as_of}）</span>}</dd>
              {innerOuter.status === 'available' && innerOuter.last_price != null && <><dt className="text-muted">盤中即時價</dt><dd className="font-mono text-foreground">{formatPrice(innerOuter.last_price)}{innerOuterTime && <span className="ml-1 text-[10px] text-muted">（{innerOuterTime}）</span>}</dd></>}
              <dt className="text-muted">壓力位</dt><dd className="font-mono text-foreground">{formatPrice(panel.resistance.resistance)}</dd>
              <dt className="text-muted">失效位置</dt><dd className="font-mono text-warning">{formatPrice(panel.invalidation.invalidation)}</dd>
            </dl>
            {panel.support.status !== 'available' && panel.resistance.status !== 'available' && panel.invalidation.status !== 'available' && (
              <p className="mt-2 text-xs text-muted">目前資料不足，無法可靠計算支撐／壓力。</p>
            )}
            {panel.support.support_distance_high_pct != null && panel.support.support_distance_low_pct != null && (
              <p className="mt-2 text-xs text-muted">距支撐：約 {formatPct(panel.support.support_distance_high_pct)} ～ {formatPct(panel.support.support_distance_low_pct)}</p>
            )}
            {panel.resistance.resistance_distance_pct != null && <p className="text-xs text-muted">距壓力：約 {formatPct(panel.resistance.resistance_distance_pct)}</p>}
            {panel.range_position.status === 'available' && <p className="mt-2 break-words text-xs text-foreground/90">{panel.range_position.explanation}</p>}
          </TechnicalSection>

          <TechnicalSection number="③" title="趨勢與均線">
            <p className="text-sm font-semibold text-foreground">均線趨勢：{TREND_LABEL[panel.moving_averages.state]}</p>
            <p className="mt-1 break-words text-xs leading-relaxed text-muted">{panel.moving_averages.explanation}</p>
            <p className="mt-2 text-xs text-muted">波動：<span className="font-medium text-foreground">{VOLATILITY_LABEL[panel.volatility.level]}</span></p>
            <p className="mt-1 break-words text-xs text-muted">{panel.volatility.explanation}</p>
          </TechnicalSection>

          <TechnicalSection number="④" title="買賣力道">
            {innerOuter.status === 'available' ? (
              <>
                <div className="grid grid-cols-2 gap-2 text-center">
                  <div className="rounded-md bg-emerald-500/10 p-2"><div className="text-xs text-muted">外盤</div><div className="text-lg font-semibold text-emerald-600 dark:text-emerald-400">{innerOuter.outer_pct?.toFixed(1)}%</div></div>
                  <div className="rounded-md bg-warning/10 p-2"><div className="text-xs text-muted">內盤</div><div className="text-lg font-semibold text-warning">{innerOuter.inner_pct?.toFixed(1)}%</div></div>
                </div>
                <p className="mt-2 text-sm font-medium text-foreground">{innerOuter.explanation}</p>
                {innerOuterTime && <p className="mt-1 text-xs text-muted">資料時間：{innerOuterTime}</p>}
              </>
            ) : innerOuter.freshness === 'stale' ? (
              <p className="text-sm text-warning">即時買賣力道資料已過期</p>
            ) : (
              <p className="text-sm text-muted">{innerOuter.explanation}</p>
            )}
            <p className="mt-1 break-words text-[11px] leading-relaxed text-muted">{innerOuter.disclaimer}</p>
            <div className="mt-2 border-t border-border/50 pt-2">
              <p className="text-xs text-muted">今日成交量 {formatShares(panel.volume.today_volume)} · 20 日均量 {formatShares(panel.volume.average_20d)}</p>
              <p className="text-xs text-muted">量比 {panel.volume.ratio == null ? '資料不足' : `${panel.volume.ratio.toFixed(2)} 倍`}</p>
              <p className="mt-1 break-words text-xs text-foreground/90">{panel.volume.explanation}</p>
            </div>
          </TechnicalSection>

          <TechnicalSection number="⑤" title="資金籌碼">
            <p className="text-sm font-semibold text-foreground">法人資金：{panel.institutional.state === 'buy' ? '偏買' : panel.institutional.state === 'sell' ? '偏賣' : panel.institutional.state === 'neutral' ? '中性' : '資料不足'}</p>
            <p className="mt-1 break-words text-xs text-muted">{panel.institutional.explanation}</p>
            {panel.institutional.as_of && <p className="text-[11px] text-muted">資料日 {panel.institutional.as_of}</p>}
            <div className="mt-2 border-t border-border/50 pt-2 text-xs text-muted">
              <p>融資：{panel.margin.margin_state === 'increase_fast' ? '快速增加' : panel.margin.margin_state === 'decrease' ? '減少' : panel.margin.margin_state === 'stable' ? '變化不大' : '資料不足'}</p>
              <p>融券：{panel.margin.short_state === 'increase_fast' ? '快速增加' : panel.margin.short_state === 'decrease' ? '減少' : panel.margin.short_state === 'stable' ? '變化不大' : '資料不足'}</p>
              <p className="mt-1 break-words">{panel.margin.explanation}</p>
              {panel.margin.as_of && <p className="text-[11px]">資料日 {panel.margin.as_of}</p>}
            </div>
          </TechnicalSection>

          <TechnicalSection number="⑥" title="相對強弱">
            <p className="text-sm font-semibold text-foreground">相對大盤：{panel.relative_strength.state === 'stronger' ? '明顯較強' : panel.relative_strength.state === 'weaker' ? '明顯較弱' : panel.relative_strength.state === 'similar' ? '接近大盤' : '資料不足'}</p>
            {panel.relative_strength.status === 'available' && (
              <dl className="mt-1 grid grid-cols-2 gap-x-2 text-xs text-muted">
                <dt>個股 20 日</dt><dd className="text-right font-mono text-foreground">{formatPct(panel.relative_strength.stock_return_pct)}</dd>
                <dt>0050 20 日</dt><dd className="text-right font-mono text-foreground">{formatPct(panel.relative_strength.benchmark_return_pct)}</dd>
              </dl>
            )}
            <p className="mt-1 break-words text-xs text-muted">{panel.relative_strength.explanation}</p>
            <p className="mt-2 text-xs text-muted">所屬類股：<span className="text-foreground">{panel.market_context.industry_state === 'strong' ? '相對強' : panel.market_context.industry_state === 'neutral' ? '中性' : '資料不足'}</span></p>
            <p className="break-words text-xs text-muted">{panel.market_context.explanation}</p>
            {fxSummary && <p className="mt-2 border-t border-border/50 pt-2 text-xs text-muted">{fxSummary}</p>}
            {macroSummary && <p className="mt-2 border-t border-border/50 pt-2 text-xs text-muted">{macroSummary}</p>}
            {macroContext?.data != null && <FredAttribution />}
          </TechnicalSection>

          <TechnicalSection number="⑦" title="基本面">
            <p className="break-words text-sm text-foreground">{panel.fundamentals.explanation}</p>
            {panel.fundamentals.warning && <p className="mt-2 break-words rounded-md bg-warning/10 p-2 text-xs text-warning">{panel.fundamentals.warning}</p>}
            {panel.fundamentals.pe != null && <p className="mt-1 text-xs text-muted">本益比 {panel.fundamentals.pe.toFixed(1)}</p>}
            {crossCheckStatus === 'matched' && <p className="mt-2 text-xs text-emerald-600 dark:text-emerald-400">第二來源與官方資料一致</p>}
            {crossCheckStatus === 'partial' && <p className="mt-2 text-xs text-muted">第二來源僅完成部分比對，未比對欄位維持不可用</p>}
            {crossCheckStatus === 'mismatch' && <p className="mt-2 text-xs text-warning">第二來源與官方資料不一致，請以官方資料為準</p>}
            {secondaryContext?.error_reason === 'config_missing' && <p className="mt-2 text-xs text-muted">FinBridge 未設定 API Key，第二來源比對維持不可用。</p>}
          </TechnicalSection>

          <TechnicalSection number="⑧" title="主要風險">
            <ReasonList items={panel.key_risks.map(risk => risk.text)} empty="目前沒有可辨識的主要風險。" tone="risk" />
            {candidate.invalidation && <p className="mt-2 break-words text-xs text-warning">失效條件：{candidate.invalidation}</p>}
          </TechnicalSection>
        </div>
      )}
      {candidate && !panel && (
        <div className="mt-3 rounded-md bg-base/60 p-3">
          <div className="mb-2"><StateBadge state={candidate.selection_state} /></div>
          {skipped && <p className="mb-1 text-xs font-semibold text-muted">為什麼沒被選？</p>}
          <ReasonList
            items={(skipped ? candidate.exclusion_reasons : candidate.reasons).map(r => r.display_text)}
            empty="技術面資料不足，暫時無法建立初學者面板。"
            tone={skipped ? 'risk' : 'reason'}
          />
        </div>
      )}
      <p className="mt-2 text-[11px] text-muted">{STRENGTH_DISCLAIMER}</p>
      {advanced && candidate && (
        <div className="mt-3 border-t border-border/50 pt-2">
          <p className="mb-1 text-xs font-semibold text-muted">判斷依據（{query.data?.version}）</p>
          <ul className="space-y-1">
            {candidate.dimensions.map(d => (
              <li key={d.key} className="grid grid-cols-[4.5rem_3.5rem_1fr] gap-2 text-xs">
                <span className="text-foreground">{d.label}</span>
                <span className={d.status === 'unavailable' ? 'text-muted' : d.status === 'negative' ? 'text-warning' : 'text-foreground/80'}>{DIRECTION_LABEL[d.status]}</span>
                <span className="min-w-0 break-words text-muted">{d.explanation} <span className="font-mono text-[10px] text-muted/70">[{d.source}]</span></span>
              </li>
            ))}
          </ul>
          {candidate.data_gaps.length > 0 && <p className="mt-1 break-words text-[11px] text-muted">資料缺口：{candidate.data_gaps.join('；')}</p>}
          {panel && innerOuter && (
            <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 rounded-md bg-base/50 p-2 text-xs text-muted sm:grid-cols-4">
              <dt>MA5</dt><dd className="font-mono text-foreground">{formatPrice(panel.moving_averages.ma5)}</dd>
              <dt>MA20</dt><dd className="font-mono text-foreground">{formatPrice(panel.moving_averages.ma20)}</dd>
              <dt>MA60</dt><dd className="font-mono text-foreground">{formatPrice(panel.moving_averages.ma60)}</dd>
              <dt>ATR / ATR%</dt><dd className="font-mono text-foreground">{formatPrice(panel.volatility.atr_14)} / {formatPct(panel.volatility.atr_pct)}</dd>
              <dt>法人 5 日</dt><dd className="font-mono text-foreground">{formatShares(panel.institutional.total_net_5d)}</dd>
              <dt>融資增減</dt><dd className="font-mono text-foreground">{formatShares(panel.margin.margin_change)}</dd>
              <dt>融券增減</dt><dd className="font-mono text-foreground">{formatShares(panel.margin.short_change)}</dd>
              <dt>技術來源</dt><dd className="break-all font-mono text-[10px] text-foreground">{panel.moving_averages.source}</dd>
              <dt>即時價 / 成交量</dt><dd className="font-mono text-foreground">{formatPrice(innerOuter.last_price)} / {formatShares(innerOuter.trade_volume)}</dd>
              <dt>成交值</dt><dd className="font-mono text-foreground">{innerOuter.trade_value == null ? '資料不足' : innerOuter.trade_value.toLocaleString('zh-TW')}</dd>
              <dt>五檔委買</dt><dd className="font-mono text-[10px] text-foreground">{innerOuter.bids.length ? innerOuter.bids.map(([p, s]) => `${p}/${s}`).join(' · ') : '資料不足'}</dd>
              <dt>五檔委賣</dt><dd className="font-mono text-[10px] text-foreground">{innerOuter.asks.length ? innerOuter.asks.map(([p, s]) => `${p}/${s}`).join(' · ') : '資料不足'}</dd>
              <dt>FRED series</dt><dd className="break-all font-mono text-[10px] text-foreground">{macroContext?.data?.series ? JSON.stringify(macroContext.data.series) : '資料不足'}</dd>
              <dt>第二來源比對</dt><dd className="break-all font-mono text-[10px] text-foreground">{secondaryContext?.data ? JSON.stringify(secondaryContext.data) : '資料不足'}</dd>
            </dl>
          )}
        </div>
      )}
    </section>
  )
}

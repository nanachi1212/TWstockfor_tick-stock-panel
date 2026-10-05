import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowUpRight, ChevronDown, ChevronUp, Compass, Loader2, Sparkles } from 'lucide-react'
import {
  api,
  type BeginnerCandidate,
  type BeginnerDirection,
  type BeginnerMarketState,
  type BeginnerMarketSummary,
  type BeginnerSelectionState,
  type BeginnerSignalStrength,
} from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'

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

export function PickCard({ candidate }: { candidate: BeginnerCandidate }) {
  const navigate = useNavigate()
  const href = `/stocks/${encodeURIComponent(candidate.symbol)}`
  return (
    <article
      aria-label={`${candidate.name} ${candidate.symbol}`}
      className="flex min-w-0 flex-col gap-2 rounded-card border border-border bg-surface/85 p-3"
    >
      <header className="flex min-w-0 items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-foreground">{candidate.name || candidate.symbol}</p>
          <p className="font-mono text-[11px] text-muted">{candidate.symbol}</p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <StateBadge state={candidate.selection_state} />
          {candidate.selection_state !== 'skip' && (
            <span className="text-[11px] text-muted" title={STRENGTH_DISCLAIMER}>訊號強度：{STRENGTH_LABEL[candidate.signal_strength]}</span>
          )}
        </div>
      </header>
      {candidate.selection_state !== 'skip' && (
        <div>
          <p className="mb-0.5 text-[11px] font-semibold text-muted">為什麼被選中</p>
          <ReasonList items={candidate.reasons.map(r => r.display_text)} empty="沒有明確的正向理由。" tone="reason" />
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
          onClick={() => navigate(href, { state: { aiResearchRequested: true } })}
          className="inline-flex min-h-8 items-center gap-1 rounded-md border border-purple-500/30 px-2.5 text-xs text-purple-400 hover:bg-purple-500/10"
        >
          <Sparkles className="h-3 w-3" /> AI 深入分析
        </button>
      </div>
    </article>
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
            <div className="grid grid-cols-1 gap-1.5 md:grid-cols-2 xl:grid-cols-3">
              {data.candidates.slice(0, 5).map(c => <PickCard key={c.symbol} candidate={c} />)}
            </div>
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
  const candidate = query.data?.candidate
  const skipped = candidate?.selection_state === 'skip'
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
      {candidate && (
        <div className="mt-2 grid grid-cols-1 gap-3 md:grid-cols-2">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-semibold text-muted">結論</span>
              <StateBadge state={candidate.selection_state} />
              {!skipped && <span className="text-xs text-muted">訊號強度：{STRENGTH_LABEL[candidate.signal_strength]}</span>}
            </div>
            {skipped ? (
              <div>
                <p className="mb-0.5 text-xs font-semibold text-muted">為什麼沒被選？</p>
                <ReasonList items={candidate.exclusion_reasons.map(r => r.display_text)} empty={candidate.action_summary} tone="risk" />
              </div>
            ) : (
              <div>
                <p className="mb-0.5 text-xs font-semibold text-muted">理由</p>
                <ReasonList items={candidate.reasons.slice(0, 3).map(r => r.display_text)} empty="沒有明確的正向理由。" tone="reason" />
              </div>
            )}
          </div>
          <div className="space-y-2">
            <div>
              <p className="mb-0.5 text-xs font-semibold text-muted">主要風險</p>
              <ReasonList items={candidate.risks.map(r => r.display_text)} empty="目前沒有明顯的負面訊號。" tone="risk" />
            </div>
            <div className="rounded-md bg-base/60 p-2">
              <p className="text-xs font-semibold text-muted">下一步</p>
              <p className="break-words text-sm text-foreground">{candidate.action_summary}</p>
              {candidate.invalidation && <p className="mt-1 break-words text-xs text-muted">失效條件：{candidate.invalidation}</p>}
            </div>
          </div>
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
        </div>
      )}
    </section>
  )
}

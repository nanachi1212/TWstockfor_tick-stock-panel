import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  ArrowDown,
  ArrowUp,
  ArrowUpRight,
  CircleAlert,
  Loader2,
  MessageCircleMore,
  RefreshCw,
} from 'lucide-react'
import { api, type TaiwanSocialSentimentRow } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { CopyButton } from '@/components/CopyButton'
import { formatSocialSentimentPrompt } from '@/lib/copy-formatters'
import { storage } from '@/lib/storage'
import { buildPortfolioPositions, isPortfolioTransaction, type PortfolioTransaction } from '@/lib/portfolio'
import { cn } from '@/lib/cn'

type SortKey = 'social_heat_score' | 'total_mentions' | 'sentiment_score'

const SOURCE_LABELS: Record<string, string> = { ptt: 'PTT', dcard: 'Dcard' }

function number(value: number | null | undefined, digits = 0) {
  return typeof value === 'number' && Number.isFinite(value)
    ? value.toLocaleString('zh-TW', { maximumFractionDigits: digits })
    : '不可用'
}

function percentage(value: number, total: number) {
  return total > 0 ? `${((value / total) * 100).toFixed(0)}%` : '不可用'
}

function sentimentLabel(value: TaiwanSocialSentimentRow['sentiment']) {
  return { bullish: '偏多', neutral: '中性', bearish: '偏空', unavailable: '不可用' }[value]
}

function sentimentClass(value: TaiwanSocialSentimentRow['sentiment']) {
  if (value === 'bullish') return 'text-bull'
  if (value === 'bearish') return 'text-bear'
  return 'text-muted'
}

function portfolioSymbols() {
  try {
    const raw = storage.portfolioTransactions.get([])
    if (!Array.isArray(raw) || !raw.every(isPortfolioTransaction)) return new Set<string>()
    return new Set(
      buildPortfolioPositions(raw as PortfolioTransaction[])
        .filter(position => position.shares > 0)
        .map(position => position.symbol),
    )
  } catch {
    return new Set<string>()
  }
}

export function SocialSentiment() {
  const [sortKey, setSortKey] = useState<SortKey>('social_heat_score')
  const [onlyAi, setOnlyAi] = useState(false)
  const [onlyWatchlist, setOnlyWatchlist] = useState(false)
  const [onlyPortfolio, setOnlyPortfolio] = useState(false)
  const [selectedHistory, setSelectedHistory] = useState('latest')
  const [selectedRow, setSelectedRow] = useState<TaiwanSocialSentimentRow | null>(null)

  const [targetDate, snapshotSlot] = selectedHistory === 'latest'
    ? [undefined, undefined]
    : selectedHistory.split('|') as [string, 'pre_open' | 'after_close']
  const sentiment = useQuery({
    queryKey: QK.taiwanSocialSentiment(targetDate, snapshotSlot),
    queryFn: () => api.taiwanSocialSentiment(targetDate, snapshotSlot),
    staleTime: 5 * 60_000,
  })
  const history = useQuery({
    queryKey: QK.taiwanSocialSentimentHistory,
    queryFn: () => api.taiwanSocialSentimentHistory(30),
    staleTime: 5 * 60_000,
  })
  const watchlist = useQuery({ queryKey: QK.watchlist, queryFn: api.watchlistList, staleTime: 60_000 })

  const watchlistSet = useMemo(
    () => new Set((watchlist.data?.symbols ?? []).map(item => item.symbol)),
    [watchlist.data],
  )
  const portfolioSet = useMemo(portfolioSymbols, [])
  const rows = useMemo(() => {
    const filtered = (sentiment.data?.rankings ?? []).filter(row => {
      if (onlyAi && row.sentiment_status !== 'available') return false
      if (onlyWatchlist && !watchlistSet.has(row.symbol)) return false
      if (onlyPortfolio && !portfolioSet.has(row.symbol)) return false
      return true
    })
    return [...filtered].sort((left, right) => {
      const leftValue = left[sortKey]
      const rightValue = right[sortKey]
      if (leftValue == null) return 1
      if (rightValue == null) return -1
      return rightValue - leftValue || left.code.localeCompare(right.code)
    })
  }, [onlyAi, onlyPortfolio, onlyWatchlist, portfolioSet, sentiment.data, sortKey, watchlistSet])

  const generatedAt = sentiment.data?.generated_at ? new Date(sentiment.data.generated_at) : null
  const isStale = generatedAt != null && Date.now() - generatedAt.getTime() > 26 * 60 * 60 * 1000
  const sources = sentiment.data?.sources ?? {}

  function SortButton({ value, label }: { value: SortKey; label: string }) {
    const active = sortKey === value
    return (
      <button
        type="button"
        onClick={() => setSortKey(value)}
        className={cn('inline-flex min-h-9 items-center gap-1 rounded-lg border px-3 text-xs transition-colors', active ? 'border-accent/50 bg-accent/10 text-accent' : 'border-border bg-surface text-muted hover:text-foreground')}
        aria-pressed={active}
      >
        {label}<ArrowDown className="h-3 w-3" />
      </button>
    )
  }

  return (
    <main className="min-h-full bg-base p-3 sm:p-4">
      <div className="mx-auto max-w-7xl space-y-3">
        <header className="rounded-card border border-border bg-surface/90 p-4 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <MessageCircleMore className="h-5 w-5 text-accent" />
                <h1 className="text-lg font-bold text-foreground">社群聲量</h1>
                {sentiment.data && (
                  <span className={cn('rounded-full px-2 py-0.5 text-[10px] font-semibold', sentiment.data.status === 'available' ? 'bg-bull/10 text-bull' : sentiment.data.status === 'partial' ? 'bg-warning/10 text-warning' : 'bg-danger/10 text-danger')}>
                    {sentiment.data.status === 'available' ? '來源完整' : sentiment.data.status === 'partial' ? '部分來源可用' : '來源不可用'}
                  </span>
                )}
              </div>
              <p className="mt-1 text-xs leading-relaxed text-muted">社群討論與 AI 情緒分析僅供研究，可能有抽樣與群體偏誤，不代表公司基本面或買賣建議。</p>
            </div>
            {sentiment.data && (
              <CopyButton
                label="複製社群排行榜給 AI"
                getText={() => formatSocialSentimentPrompt({
                  as_of: sentiment.data.as_of,
                  generated_at: sentiment.data.generated_at,
                  source_statuses: Object.fromEntries(Object.entries(sources).map(([source, data]) => [source, data.status])),
                  rankings: rows.slice(0, 20).map(row => ({
                    rank: row.rank, symbol: row.symbol, name: row.company_name,
                    mentions: row.total_mentions, heat: row.social_heat_score,
                    sentiment: sentimentLabel(row.sentiment), score: row.sentiment_score,
                    confidence: row.sentiment_confidence,
                  })),
                })}
              />
            )}
          </div>
        </header>

        <section className="rounded-card border border-border bg-surface/85 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <select
              aria-label="社群聲量歷史快照"
              value={selectedHistory}
              onChange={event => setSelectedHistory(event.target.value)}
              className="min-h-9 rounded-lg border border-border bg-base px-3 text-xs text-foreground"
            >
              <option value="latest">最新結果</option>
              {(history.data?.items ?? []).map(item => (
                <option key={`${item.as_of}-${item.snapshot_slot}`} value={`${item.as_of}|${item.snapshot_slot}`}>
                  {item.as_of} · {item.snapshot_slot === 'pre_open' ? '盤前' : '盤後'}
                </option>
              ))}
            </select>
            <SortButton value="social_heat_score" label="熱度排序" />
            <SortButton value="total_mentions" label="聲量排序" />
            <SortButton value="sentiment_score" label="情緒分數排序" />
            {[
              [onlyAi, setOnlyAi, '只看有 AI 情緒'],
              [onlyWatchlist, setOnlyWatchlist, '只看自選股'],
              [onlyPortfolio, setOnlyPortfolio, '只看持倉'],
            ].map(([checked, setter, label]) => (
              <label key={label as string} className="inline-flex min-h-9 cursor-pointer items-center gap-2 rounded-lg border border-border bg-base px-3 text-xs text-secondary">
                <input type="checkbox" checked={checked as boolean} onChange={event => (setter as (value: boolean) => void)(event.target.checked)} />
                {label as string}
              </label>
            ))}
          </div>
          {sentiment.data && (
            <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
              {Object.entries(sources).map(([source, data]) => (
                <span key={source} className={cn('rounded-md border px-2 py-1', data.status === 'available' ? 'border-bull/30 bg-bull/5 text-bull' : data.status === 'partial' ? 'border-warning/30 bg-warning/5 text-warning' : 'border-danger/30 bg-danger/5 text-danger')}>
                  {SOURCE_LABELS[source] ?? source}：{data.status === 'available' ? `可用 · ${data.posts} 篇` : data.status === 'partial' ? '部分可用' : '目前來源不可用'}
                </span>
              ))}
              <span className="rounded-md border border-border bg-base px-2 py-1 text-muted">資料日期 {sentiment.data.as_of} · {sentiment.data.snapshot_slot === 'pre_open' ? '盤前' : '盤後'}</span>
              {isStale && <span className="rounded-md border border-warning/30 bg-warning/5 px-2 py-1 text-warning">資料可能過期</span>}
            </div>
          )}
        </section>

        {sentiment.isLoading ? (
          <div className="flex items-center justify-center gap-2 rounded-card border border-border bg-surface p-12 text-sm text-muted"><Loader2 className="h-4 w-4 animate-spin" />正在讀取社群聲量…</div>
        ) : sentiment.isError ? (
          <div className="rounded-card border border-danger/30 bg-danger/5 p-8 text-center text-sm text-muted">
            <CircleAlert className="mx-auto mb-2 h-6 w-6 text-danger" />社群聲量資料目前無法讀取。
            <button type="button" onClick={() => sentiment.refetch()} className="mx-auto mt-3 flex min-h-9 items-center gap-1 rounded-lg border border-border px-3 text-xs text-foreground"><RefreshCw className="h-3 w-3" />重試</button>
          </div>
        ) : rows.length === 0 ? (
          <div className="rounded-card border border-border bg-surface p-10 text-center text-sm text-muted">目前篩選條件下沒有可顯示的社群聲量資料。</div>
        ) : (
          <section className="overflow-hidden rounded-card border border-border bg-surface/90">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1120px] text-left text-xs">
                <thead className="border-b border-border bg-elevated/40 text-[11px] text-muted">
                  <tr>
                    {['排名', '股票', '總聲量', 'PTT', 'Dcard', '討論串', '互動', '熱度', '24h 變化', '偏多', '中性', '偏空', '情緒分數', '信心', '狀態'].map(label => <th key={label} className="px-3 py-2 font-medium">{label}</th>)}
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/60">
                  {rows.map(row => {
                    const sentimentTotal = row.bullish_count + row.neutral_count + row.bearish_count
                    return (
                      <tr key={row.symbol} className="hover:bg-elevated/30">
                        <td className="px-3 py-2 font-mono text-muted">{row.rank}</td>
                        <td className="px-3 py-2">
                          <button type="button" onClick={() => setSelectedRow(row)} className="min-h-9 text-left hover:text-accent" aria-label={`查看 ${row.company_name} 社群明細`}>
                            <span className="block font-semibold text-foreground">{row.company_name}</span><span className="font-mono text-[11px] text-muted">{row.symbol}</span>
                          </button>
                        </td>
                        <td className="px-3 py-2 font-mono font-semibold">{number(row.total_mentions)}</td>
                        <td className="px-3 py-2 font-mono">{number(row.ptt_mentions)}</td>
                        <td className="px-3 py-2 font-mono">{sources.dcard?.status === 'unavailable' ? <span className="text-danger">不可用</span> : number(row.dcard_mentions)}</td>
                        <td className="px-3 py-2 font-mono">{number(row.unique_posts)}</td>
                        <td className="px-3 py-2 font-mono">{number(row.engagement)}</td>
                        <td className="px-3 py-2 font-mono font-semibold text-accent">{number(row.social_heat_score, 1)}</td>
                        <td className="px-3 py-2 font-mono">{row.volume_change_24h == null ? '不可比' : <span className={row.volume_change_24h >= 0 ? 'text-bull' : 'text-bear'}>{row.volume_change_24h >= 0 ? <ArrowUp className="inline h-3 w-3" /> : <ArrowDown className="inline h-3 w-3" />}{Math.abs(row.volume_change_24h * 100).toFixed(0)}%</span>}</td>
                        <td className="px-3 py-2 font-mono">{row.sentiment_status === 'available' ? percentage(row.bullish_count, sentimentTotal) : '不可用'}</td>
                        <td className="px-3 py-2 font-mono">{row.sentiment_status === 'available' ? percentage(row.neutral_count, sentimentTotal) : '不可用'}</td>
                        <td className="px-3 py-2 font-mono">{row.sentiment_status === 'available' ? percentage(row.bearish_count, sentimentTotal) : '不可用'}</td>
                        <td className={cn('px-3 py-2 font-mono', sentimentClass(row.sentiment))}>{row.sentiment_score == null ? '不可用' : row.sentiment_score.toFixed(2)}</td>
                        <td className="px-3 py-2 font-mono">{row.sentiment_confidence == null ? '不可用' : `${(row.sentiment_confidence * 100).toFixed(0)}%`}</td>
                        <td className="px-3 py-2"><span className={sentimentClass(row.sentiment)}>{sentimentLabel(row.sentiment)}</span></td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {selectedRow && (
          <section className="rounded-card border border-border bg-surface p-4" aria-live="polite">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div><h2 className="font-semibold text-foreground">{selectedRow.company_name} 社群明細</h2><p className="font-mono text-xs text-muted">{selectedRow.symbol}</p></div>
              <Link to={`/stocks/${encodeURIComponent(selectedRow.symbol)}`} className="inline-flex min-h-9 items-center gap-1 rounded-lg border border-accent/40 px-3 text-xs text-accent hover:bg-accent/10">前往個股分析<ArrowUpRight className="h-3 w-3" /></Link>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
              {[
                ['總聲量', number(selectedRow.total_mentions)], ['來源', `PTT ${number(selectedRow.ptt_mentions)} · Dcard ${sources.dcard?.status === 'unavailable' ? '不可用' : number(selectedRow.dcard_mentions)}`],
                ['互動', number(selectedRow.engagement)], ['熱度', number(selectedRow.social_heat_score, 1)],
                ['情緒', sentimentLabel(selectedRow.sentiment)], ['AI 信心', selectedRow.sentiment_confidence == null ? '不可用' : `${(selectedRow.sentiment_confidence * 100).toFixed(0)}%`],
                ['24h 變化', selectedRow.volume_change_24h == null ? '不可比' : `${(selectedRow.volume_change_24h * 100).toFixed(0)}%`], ['資料日期', sentiment.data?.as_of ?? '不可用'],
              ].map(([label, value]) => <div key={label} className="rounded-lg border border-border/60 bg-base p-2"><span className="block text-[10px] text-muted">{label}</span><span className="text-xs font-medium text-foreground">{value}</span></div>)}
            </div>
            <p className="mt-3 text-[11px] leading-relaxed text-muted">為避免保存不必要的使用者內容，現有 history 不儲存原始文章、留言或標題，因此此處不顯示代表貼文。</p>
          </section>
        )}
      </div>
    </main>
  )
}

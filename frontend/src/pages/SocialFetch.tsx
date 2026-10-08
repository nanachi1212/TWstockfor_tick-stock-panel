import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { CircleAlert, ExternalLink, Loader2, RefreshCw } from 'lucide-react'
import {
  api,
  type TaiwanSocialSentimentDiscussion,
  type TaiwanSocialSentimentJob,
} from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'

const PAGE_SIZE = 50

function statusLabel(status: string) {
  return {
    not_queried: '尚未執行',
    available: '可用',
    degraded: '部分可用',
    unavailable: '目前來源不可用',
    queued: '排隊中',
    running: '執行中',
    completed: '完成',
    partial: '部分完成',
    failed: '失敗',
  }[status] ?? status
}

function dateTime(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString('zh-TW', { hour12: false }) : '尚未完成'
}

function sentimentLabel(value: string) {
  return { bullish: '偏多', neutral: '中性', bearish: '偏空', unavailable: '不可用' }[value] ?? value
}

export function SocialFetch() {
  const [jobId, setJobId] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [source, setSource] = useState('')
  const [symbol, setSymbol] = useState('')
  const [keyword, setKeyword] = useState('')
  const [offset, setOffset] = useState(0)
  const [discussionItems, setDiscussionItems] = useState<TaiwanSocialSentimentDiscussion[]>([])

  const run = useMutation({
    mutationFn: () => api.taiwanSocialSentimentRun({ mode: 'manual' }),
    onMutate: () => setNotice('正在啟動社群資料更新…'),
    onSuccess: result => {
      if (result.status === 'already_running' || !result.job_id) {
        setNotice('社群資料正在更新中')
        return
      }
      setJobId(result.job_id)
      setDiscussionItems([])
      setOffset(0)
      setNotice(null)
    },
    onError: () => setNotice('無法啟動社群資料更新'),
  })

  const job = useQuery({
    queryKey: QK.taiwanSocialSentimentJob(jobId ?? '', source, symbol, keyword, offset),
    queryFn: () => api.taiwanSocialSentimentJob(jobId!, {
      source: source || undefined,
      symbol: symbol || undefined,
      q: keyword || undefined,
      offset,
      limit: PAGE_SIZE,
    }),
    enabled: Boolean(jobId),
    placeholderData: previous => previous,
    refetchInterval: query => ['queued', 'running'].includes(query.state.data?.status ?? '') ? 2_000 : false,
  })

  useEffect(() => {
    const page = job.data?.discussions.items
    if (!page) return
    setDiscussionItems(current => {
      if (offset === 0) return page
      const merged = new Map(current.map(item => [item.id, item]))
      page.forEach(item => merged.set(item.id, item))
      return [...merged.values()]
    })
  }, [job.data, offset])

  const terminal = job.data && !['queued', 'running'].includes(job.data.status)
  const isRunning = run.isPending || job.data?.status === 'queued' || job.data?.status === 'running'
  const rows = job.data?.rankings ?? []
  const dcardUnavailable = job.data?.dcard_status === 'unavailable'
  const symbols = useMemo(
    () => rows.map(row => ({ value: row.symbol, label: `${row.company_name} ${row.code}` })),
    [rows],
  )

  function resetDiscussionFilter(change: () => void) {
    change()
    setOffset(0)
    setDiscussionItems([])
  }

  return (
    <main className="min-h-full bg-base p-3 sm:p-4">
      <div className="mx-auto max-w-7xl space-y-4">
        <header className="rounded-card border border-border bg-surface/90 p-4 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h1 className="text-lg font-bold text-foreground">社群即時撈取</h1>
              <p className="mt-1 text-xs text-muted">手動執行既有 PTT／Dcard pipeline，查看這一次的來源、排行與代表討論。</p>
            </div>
            <button
              type="button"
              disabled={Boolean(isRunning)}
              onClick={() => run.mutate()}
              className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-accent px-4 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isRunning ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              立即撈取 PTT / Dcard
            </button>
          </div>
          {notice && <p className="mt-3 text-sm text-secondary" aria-live="polite">{notice}</p>}
          {job.isError && <p className="mt-3 text-sm text-danger">無法讀取本次工作狀態。</p>}
        </header>

        {job.data && <StatusCards job={job.data} />}

        {job.data?.status === 'failed' && (
          <section className="rounded-card border border-danger/30 bg-danger/5 p-4 text-sm text-danger">
            <CircleAlert className="mr-2 inline h-4 w-4" />本次撈取失敗{job.data.error_summary ? `：${job.data.error_summary}` : ''}
          </section>
        )}

        {terminal && job.data?.status !== 'failed' && (
          <>
            <section className="overflow-hidden rounded-card border border-border bg-surface/90">
              <div className="border-b border-border px-4 py-3">
                <h2 className="font-semibold text-foreground">本次結果排行榜</h2>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[920px] text-left text-xs">
                  <thead className="bg-elevated/40 text-muted">
                    <tr>{['排名', '股票', '代號', 'PTT mentions', 'Dcard mentions', 'unique posts', 'engagement', 'heat score', '情緒', 'confidence'].map(label => <th key={label} className="px-3 py-2 font-medium">{label}</th>)}</tr>
                  </thead>
                  <tbody className="divide-y divide-border/60">
                    {rows.map(row => (
                      <tr key={row.symbol}>
                        <td className="px-3 py-2">{row.rank}</td>
                        <td className="px-3 py-2 font-semibold text-foreground">{row.company_name}</td>
                        <td className="px-3 py-2 font-mono">{row.code}</td>
                        <td className="px-3 py-2 font-mono">{row.ptt_mentions}</td>
                        <td className="px-3 py-2 font-mono">{dcardUnavailable ? <span className="text-danger">不可用</span> : row.dcard_mentions}</td>
                        <td className="px-3 py-2 font-mono">{row.unique_posts}</td>
                        <td className="px-3 py-2 font-mono">{row.engagement}</td>
                        <td className="px-3 py-2 font-mono text-accent">{row.social_heat_score.toFixed(1)}</td>
                        <td className="px-3 py-2">{sentimentLabel(row.sentiment)}</td>
                        <td className="px-3 py-2 font-mono">{row.sentiment_confidence == null ? '不可用' : `${(row.sentiment_confidence * 100).toFixed(0)}%`}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="rounded-card border border-border bg-surface/90 p-4">
              <h2 className="font-semibold text-foreground">本次討論內容</h2>
              <div className="mt-3 grid gap-2 sm:grid-cols-3">
                <select
                  aria-label="來源篩選"
                  value={source}
                  onChange={event => resetDiscussionFilter(() => setSource(event.target.value))}
                  className="min-h-9 rounded-lg border border-border bg-base px-3 text-xs"
                >
                  <option value="">全部來源</option><option value="ptt">PTT</option><option value="dcard">Dcard</option>
                </select>
                <select
                  aria-label="股票篩選"
                  value={symbol}
                  onChange={event => resetDiscussionFilter(() => setSymbol(event.target.value))}
                  className="min-h-9 rounded-lg border border-border bg-base px-3 text-xs"
                >
                  <option value="">全部股票</option>
                  {symbols.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
                </select>
                <input
                  aria-label="關鍵字搜尋"
                  value={keyword}
                  onChange={event => resetDiscussionFilter(() => setKeyword(event.target.value))}
                  placeholder="搜尋標題、節錄或留言"
                  className="min-h-9 rounded-lg border border-border bg-base px-3 text-xs"
                />
              </div>
              <div className="mt-3 space-y-3">
                {discussionItems.map(item => (
                  <article key={item.id} className="rounded-lg border border-border/70 bg-base p-3">
                    <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted">
                      <span className="rounded bg-elevated px-2 py-0.5 uppercase">{item.source}</span>
                      <span>{dateTime(item.published_at)}</span>
                      <span>{item.stock_names.join('、') || '未辨識股票'}</span>
                      <span>留言／互動 {item.comments_count}／{item.engagement}</span>
                    </div>
                    <a href={item.url} target="_blank" rel="noreferrer" className="mt-2 inline-flex items-center gap-1 font-semibold text-foreground hover:text-accent">
                      {item.title}<ExternalLink className="h-3 w-3" />
                    </a>
                    <p className="mt-1 text-xs leading-relaxed text-secondary">{item.excerpt || '無文章節錄'}</p>
                    {item.representative_comments.length > 0 && (
                      <ul className="mt-2 space-y-1 border-l-2 border-border pl-3 text-xs text-muted">
                        {item.representative_comments.map((comment, index) => <li key={`${item.id}-${index}`}>{comment}</li>)}
                      </ul>
                    )}
                    <p className="mt-2 font-mono text-[10px] text-muted">辨識股票：{item.symbols.join('、') || '無'}</p>
                  </article>
                ))}
                {discussionItems.length === 0 && <p className="py-8 text-center text-sm text-muted">目前篩選條件下沒有討論內容。</p>}
              </div>
              {job.data.discussions.has_more && (
                <button type="button" onClick={() => setOffset(value => value + PAGE_SIZE)} className="mt-3 min-h-9 rounded-lg border border-border px-3 text-xs text-foreground">
                  載入更多討論
                </button>
              )}
            </section>
          </>
        )}
      </div>
    </main>
  )
}

function StatusCards({ job }: { job: TaiwanSocialSentimentJob }) {
  const cards = [
    ['工作狀態', statusLabel(job.status)],
    ['PTT', statusLabel(job.ptt_status)],
    ['Dcard', job.dcard_status === 'unavailable' ? 'Dcard：目前來源不可用' : statusLabel(job.dcard_status)],
    ['AI 情緒', statusLabel(job.ai_status) + (job.ai_skipped_symbols ? `（熱度較低的 ${job.ai_skipped_symbols} 檔未分析）` : '')],
    ['開始時間', dateTime(job.started_at)],
    ['完成時間', dateTime(job.finished_at)],
    ['本次抓取文章數', job.posts.toLocaleString('zh-TW')],
    ['留言數', job.comments.toLocaleString('zh-TW')],
    ['辨識股票數', job.symbols_identified.toLocaleString('zh-TW')],
  ]
  return (
    <section className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4" aria-live="polite">
      {cards.map(([label, value]) => (
        <div key={label} className={cn('rounded-card border border-border bg-surface p-3', label === 'Dcard' && job.dcard_status === 'unavailable' && 'border-danger/30 bg-danger/5')}>
          <span className="text-[11px] text-muted">{label}</span>
          <p className="mt-1 text-sm font-semibold text-foreground">{value}</p>
        </div>
      ))}
    </section>
  )
}

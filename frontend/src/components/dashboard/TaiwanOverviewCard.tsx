import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Database, Loader2 } from 'lucide-react'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

const TAIWAN_FRESHNESS_LABEL: Record<string, string> = {
  current: '最新',
  stale: '過期',
  unavailable: '尚無資料',
}

export function TaiwanOverviewCard() {
  const status = useQuery({
    queryKey: QK.taiwanDataStatus,
    queryFn: api.taiwanDataStatus,
    staleTime: 60_000,
  })

  const rows = status.data
    ? [
        { label: '日K', asOf: status.data.daily_as_of, freshness: status.data.daily_status },
        { label: '三大法人', asOf: status.data.institutional_as_of, freshness: status.data.institutional_status },
        { label: '融資融券', asOf: status.data.margin_as_of, freshness: status.data.margin_status },
      ]
    : []
  const hasAnyData = rows.some(r => r.asOf)

  return (
    <section className="mb-3 rounded-card border border-border bg-surface/85 p-3.5">
      <div className="flex items-center justify-between gap-2 mb-2.5">
        <div className="flex items-center gap-1.5">
          <Database className="h-3.5 w-3.5 text-accent" />
          <h2 className="text-xs font-semibold text-foreground">台股資料狀態</h2>
        </div>
        <div className="flex items-center gap-3 text-[11px]">
          <Link to="/taiwan-screener" className="text-secondary hover:text-accent transition-colors">台股選股</Link>
          <Link to="/stocks/compare" className="text-secondary hover:text-accent transition-colors">多股比較</Link>
          <Link to="/watchlist" className="text-secondary hover:text-accent transition-colors">自選股</Link>
        </div>
      </div>

      {status.isLoading ? (
        <div className="flex items-center gap-2 text-xs text-muted">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          正在讀取台股資料狀態…
        </div>
      ) : status.isError ? (
        <p className="text-xs text-muted leading-relaxed">
          目前無法讀取台股資料狀態,不影響其他功能使用,請稍後再試。
        </p>
      ) : hasAnyData ? (
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5">
          {rows.map(r => (
            <div key={r.label} className="flex items-center gap-1.5 text-xs">
              <span className="text-secondary">{r.label}</span>
              <span className="font-mono text-muted">{r.asOf ?? '—'}</span>
              <span className="text-[11px] font-medium text-muted">
                {TAIWAN_FRESHNESS_LABEL[r.freshness] ?? r.freshness}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-xs text-secondary leading-relaxed">
          目前尚未下載台股資料,仍可先使用「台股選股」「多股比較」等功能,資料將於每日排程自動更新。
        </p>
      )}
    </section>
  )
}

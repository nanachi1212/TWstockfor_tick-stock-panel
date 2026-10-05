import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

export function ExternalContextCard({ compact = false }: { compact?: boolean }) {
  const query = useQuery({
    queryKey: ['external-context', 'global'],
    queryFn: () => api.taiwanExternalContext(),
    staleTime: 60 * 60 * 1000,
  })
  const fx = query.data?.fx_context
  const summary = typeof fx?.data?.summary === 'string' ? fx.data.summary : null
  return (
    <section aria-label="外部市場環境" className="rounded-card border border-border bg-surface p-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className={compact ? 'text-sm font-semibold' : 'font-semibold'}>匯率環境</h2>
        <span className="text-[11px] text-muted">Frankfurter / CBC</span>
      </div>
      {query.isLoading && <p className="mt-2 text-xs text-muted">讀取每日匯率 context…</p>}
      {query.isError && <p className="mt-2 text-xs text-muted">匯率 context 暫時不可用，不影響台股資料。</p>}
      {fx?.status === 'available' && <p className="mt-2 text-sm leading-relaxed text-foreground">{summary}</p>}
      {fx?.status === 'stale' && <p className="mt-2 text-sm text-warning">匯率資料已過期，暫不視為目前市場狀態。</p>}
      {fx?.status === 'unavailable' && <p className="mt-2 text-sm text-muted">匯率 context 暫時不可用，不影響台股核心功能。</p>}
      {fx?.as_of && <p className="mt-1 text-[11px] text-muted">資料日 {fx.as_of} · 僅供市場背景，不參與 Beginner 排名</p>}
    </section>
  )
}

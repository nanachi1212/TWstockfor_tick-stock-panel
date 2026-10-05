import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

export function FredAttribution() {
  return (
    <p className="mt-1 text-[11px] text-muted">
      This product uses the FRED® API but is not endorsed or certified by the Federal Reserve Bank of St. Louis. 使用本功能即同意
      {' '}<a className="underline" href="https://fred.stlouisfed.org/docs/api/terms_of_use.html" target="_blank" rel="noreferrer">FRED API Terms of Use</a>。
    </p>
  )
}

export function ExternalContextCard({ compact = false }: { compact?: boolean }) {
  const query = useQuery({
    queryKey: ['external-context', 'global'],
    queryFn: () => api.taiwanExternalContext(),
    staleTime: 60 * 60 * 1000,
  })
  const fx = query.data?.fx_context
  const summary = typeof fx?.data?.summary === 'string' ? fx.data.summary : null
  const macro = query.data?.macro_context
  const macroSummary = typeof macro?.data?.summary === 'string' ? macro.data.summary : null
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
      <div className="mt-3 border-t border-border/60 pt-3">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-semibold">全球總經環境</h3>
          <span className="text-[11px] text-muted">FRED / ALFRED</span>
        </div>
        {macro?.status === 'available' || macro?.status === 'partial'
          ? <p className="mt-2 text-sm leading-relaxed text-foreground">{macroSummary}</p>
          : <p className="mt-2 text-sm text-muted">{macro?.error_reason === 'config_missing' ? 'FRED 未設定 API Key，不影響台股核心功能。' : 'Macro Context 暫時不可用。'}</p>}
        {macro?.as_of && <p className="mt-1 text-[11px] text-muted">最新觀測日 {macro.as_of} · UI context only</p>}
        <FredAttribution />
      </div>
    </section>
  )
}

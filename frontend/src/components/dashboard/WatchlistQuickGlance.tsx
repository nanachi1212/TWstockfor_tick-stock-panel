import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Eye, Loader2, Star, X } from 'lucide-react'
import { api, type TaiwanAbnormalDiagnosticsSnapshot } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { selectionReasons, useTodayQuantSelection } from '@/components/quant/TodaySelection'
import { type TaiwanDailyStatus } from './MarketOverviewCard'
import { SectionTitle } from './SectionTitle'
import { fmtPrice, pctClass, fmtStockPct } from './dashboardFormat'

export function WatchlistQuickGlance({ onStockClick, anomalies, diagnosticsLoading, diagnosticsError, marketDailyStatus }: {
  onStockClick: (symbol: string, name?: string) => void
  anomalies: TaiwanAbnormalDiagnosticsSnapshot | undefined
  diagnosticsLoading: boolean
  diagnosticsError: boolean
  marketDailyStatus: TaiwanDailyStatus
}) {
  const qc = useQueryClient()
  const watchlist = useQuery({
    queryKey: QK.watchlist,
    queryFn: api.watchlistList,
    staleTime: 60_000,
  })
  const enriched = useQuery({
    queryKey: QK.watchlistEnriched(''),
    queryFn: () => api.watchlistEnriched(''),
    staleTime: 30_000,
  })
  const quant = useTodayQuantSelection()
  const alerts = useQuery({
    queryKey: QK.alertsToday,
    queryFn: () => api.alertsList({ days: 1, limit: 5000 }),
    enabled: !watchlist.isLoading,
    staleTime: 30_000,
  })
  const remove = useMutation({
    mutationFn: (symbol: string) => api.watchlistRemove(symbol),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.watchlist })
      qc.invalidateQueries({ queryKey: QK.watchlistEnriched() })
    },
  })
  const rowsBySymbol = new Map<string, any>()
  for (const entry of watchlist.data?.symbols ?? []) rowsBySymbol.set(entry.symbol, { ...entry })
  for (const row of enriched.data?.rows ?? []) rowsBySymbol.set(row.symbol, { ...rowsBySymbol.get(row.symbol), ...row })
  const rows = [...rowsBySymbol.values()]
  const attention = rows
    .sort((a, b) => Math.abs(b.change_pct ?? 0) - Math.abs(a.change_pct ?? 0))
  const rankedBySymbol = new Map(quant.signals.map(signal => [signal.symbol, signal]))
  const diagnosticsCurrent = anomalies?.data_quality.daily_status === 'current' && marketDailyStatus === 'current'
  const diagnosticsComplete = diagnosticsCurrent && anomalies?.data_quality.overall_status === 'complete'
  const anomalyBySymbol = new Map((diagnosticsComplete ? anomalies.items : []).map(item => [item.symbol, item]))
  const todayTaipei = new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Taipei' })
  const watchlistAlerts = (alerts.data?.alerts ?? []).filter(event => {
    const eventDate = new Date(event.ts).toLocaleDateString('sv-SE', { timeZone: 'Asia/Taipei' })
    return event.symbol && rowsBySymbol.has(event.symbol) && eventDate === todayTaipei
  })
  const watchlistTop10Entries = new Set(watchlistAlerts
    .filter(event => event.source === 'quant' && event.type === 'quant_top10_enter')
    .map(event => event.symbol)
    .filter((symbol): symbol is string => Boolean(symbol))).size

  return (
    <section className="rounded-card border border-border bg-surface/80 p-2.5 shadow-[0_1px_2px_hsl(var(--border)/0.4)] backdrop-blur-sm">
      <SectionTitle icon={Eye} title="我的觀察" hint={rows.length ? `${rows.length} 檔 · 行情 ${enriched.data?.as_of ?? '—'}` : undefined} />
      {rows.length > 0 && <div className="mb-1 flex flex-wrap gap-1 text-[9px] text-secondary">
        <span className="rounded bg-elevated px-1.5 py-0.5">今日 Top 10 {quant.loading ? '—' : quant.error || !quant.validRun ? 'unavailable' : `${rows.filter(row => rankedBySymbol.has(row.symbol)).length} 檔`}</span>
        <span className="rounded bg-elevated px-1.5 py-0.5">新進 Top 10 {alerts.isPending ? '—' : alerts.isError ? 'unavailable' : `${watchlistTop10Entries} 檔`}</span>
        <span className="rounded bg-elevated px-1.5 py-0.5">異常 {diagnosticsLoading ? '—' : diagnosticsError || !diagnosticsCurrent ? 'unavailable' : !diagnosticsComplete ? '資料不完整' : `${rows.filter(row => (anomalyBySymbol.get(row.symbol)?.signal_count ?? 0) > 0).length} 檔 · ${anomalies.trade_date}`}</span>
        <span className="rounded bg-elevated px-1.5 py-0.5">今日提醒 {alerts.isPending ? '—' : alerts.isError ? 'unavailable' : `${watchlistAlerts.length} 則`}</span>
      </div>}
      {watchlist.isLoading || enriched.isLoading ? (
        <div className="flex items-center gap-2 py-4 text-xs text-muted">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />正在讀取自選股資料…
        </div>
      ) : watchlist.isError && enriched.isError && rows.length === 0 ? (
        <p className="py-4 text-xs text-muted">目前無法讀取自選股資料,不影響其他功能使用。</p>
      ) : rows.length === 0 ? (
        <div className="py-4 text-center">
          <p className="text-xs text-secondary">尚未加入任何自選股</p>
          <Link to="/watchlist" className="mt-1.5 inline-block text-[11px] text-accent hover:text-accent/80 transition-colors">
            前往自選股 →
          </Link>
        </div>
      ) : (
        <div className="space-y-1">
          {attention.map(r => (
            <div
              key={r.symbol}
              className="flex w-full items-center justify-between gap-2 rounded-md bg-elevated/40 px-2 py-1.5 text-left border border-transparent hover:border-border/60"
            >
              <button type="button" onClick={() => onStockClick(r.symbol, r.name ?? undefined)} aria-label={`查看 ${r.name || r.symbol} 走勢`} className="min-w-0 flex-1 text-left hover:brightness-110 transition-colors">
                <div className="truncate text-[11px] text-foreground">{r.name || r.symbol}</div>
                <div className="font-mono text-[9px] text-muted">{r.symbol}</div>
              </button>
              <div className="text-right shrink-0">
                <div className="font-mono text-[11px] text-foreground">{fmtPrice(r.close)}</div>
                <div className={`font-mono text-[10px] font-semibold ${pctClass(r.change_pct)}`}>{fmtStockPct(r.change_pct)}</div>
                {(() => {
                  const signal = rankedBySymbol.get(r.symbol)
                  const summary = signal
                    ? `#${signal.rank} · Quant ${fmtStockPct(signal.score)} · ${selectionReasons(signal).slice(0, 1).join('') || '符合既有動能條件'}`
                    : quant.validRun ? '今日未進入 Top 10' : '今日 Quant 排名 unavailable'
                  const anomaly = anomalyBySymbol.get(r.symbol)
                  return <div className="max-w-[240px] truncate text-[9px] text-secondary">{summary}{anomaly?.signal_count ? ` · 異常 ${anomaly.signal_count} 項` : ''}</div>
                })()}
              </div>
              <Link to={`/stocks/${encodeURIComponent(r.symbol)}`} title="查看詳情" aria-label={`查看 ${r.symbol} 詳情`} className="inline-flex shrink-0 items-center gap-1 rounded border border-border px-1.5 py-1 text-[10px] text-muted hover:text-accent">
                <Star className="h-3 w-3" />詳情
              </Link>
              <button type="button" onClick={() => remove.mutate(r.symbol)} disabled={remove.isPending} title="移除觀察" aria-label={`移除 ${r.symbol} 觀察`} className="inline-flex shrink-0 items-center justify-center rounded p-1 text-muted hover:bg-danger/10 hover:text-danger disabled:opacity-50">
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
          {remove.isError && <p role="alert" className="text-[10px] text-danger">移除觀察失敗，請重試。</p>}
        </div>
      )}
    </section>
  )
}

// ===== Phase 8B-2: 台股資料狀態卡 =====
// 只讀既有 /api/taiwan/data-status(與 Onboarding 台股資料狀態步驟同一個 API,
// 不建立重複 backend 邏輯)。沒有資料時顯示清楚的繁體 empty state, 不假造台股
// 指數或任何資料。Phase 8C-B: 不再是首頁第一眼內容, 改列於監控中心之後的最下層
// (資料新鮮度)。Phase 8C-D: 中國 A 股 legacy Dashboard 區塊已隨產品介面整體
// 移除, 此卡不再有任何 legacy 開關依賴。

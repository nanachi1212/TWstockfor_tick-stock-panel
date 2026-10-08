import { Link } from 'react-router-dom'
import { Activity } from 'lucide-react'
import { type AlertEvent, type TaiwanAbnormalDiagnosticsSnapshot } from '@/lib/api'
import { type TaiwanDailyStatus } from './MarketOverviewCard'
import { SectionTitle } from './SectionTitle'
import { pctClass, fmtStockPct } from './dashboardFormat'

export function MarketAnomalyCard({ snapshot, loading, error, alerts, alertsLoading, alertsError, marketDailyStatus }: {
  snapshot?: TaiwanAbnormalDiagnosticsSnapshot
  loading: boolean
  error: boolean
  alerts: AlertEvent[]
  alertsLoading: boolean
  alertsError: boolean
  marketDailyStatus: TaiwanDailyStatus
}) {
  const fresh = snapshot?.data_quality.daily_status === 'current' && marketDailyStatus === 'current'
  const rows = fresh ? [...(snapshot?.items ?? [])]
    .filter(item => item.signal_count > 0)
    .sort((a, b) => b.signal_count - a.signal_count || Math.abs(b.change_pct ?? 0) - Math.abs(a.change_pct ?? 0))
    .slice(0, 8) : []
  const todayTaipei = new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Taipei' })
  const quantEvents = alerts.filter(event => {
    const eventDate = new Date(event.ts).toLocaleDateString('sv-SE', { timeZone: 'Asia/Taipei' })
    return event.source === 'quant' && ['quant_top10_enter', 'quant_top10_exit'].includes(event.type) && eventDate === todayTaipei
  })
  const displayedQuantEvents = quantEvents.slice(0, 5)
  const signalLabel: Record<string, string> = {
    PRICE_MOVE: '單日大漲/跌', VOLUME_SPIKE: '爆量', TURNOVER_SPIKE: '成交額放大',
    FOREIGN_FLOW_SPIKE: '外資異常', TRUST_FLOW_SPIKE: '投信異常', DEALER_FLOW_SPIKE: '自營商異常',
    PRICE_FLOW_DIVERGENCE: '價量背離', RELATIVE_STRENGTH_OUTLIER: '相對強弱異常',
    MARGIN_SURGE: '融資異常', SHORT_SURGE: '融券異常', SHORT_MARGIN_RATIO_SPIKE: '券資比異常',
  }
  return (
    <section className="rounded-card border border-border bg-surface/80 p-2.5 shadow-[0_1px_2px_hsl(var(--border)/0.4)]">
      <SectionTitle icon={Activity} title="市場異常" hint={snapshot ? `資料交易日 ${snapshot.trade_date}` : undefined} />
      {loading ? <div className="py-4 text-xs text-muted">正在讀取市場異常…</div>
        : error || !snapshot ? <div className="py-4 text-xs text-muted">目前無法讀取市場異常資料。</div>
          : !fresh ? <div className="rounded border border-warning/40 bg-warning/10 px-2 py-2 text-[11px] text-warning">異常雷達資料 {marketDailyStatus !== 'current' ? marketDailyStatus : snapshot.data_quality.daily_status}，不以過期行情判定今日異常。</div>
            : <>
              {snapshot.data_quality.overall_status !== 'complete' && (
                <div className="mb-1 rounded border border-warning/40 bg-warning/10 px-2 py-2 text-[11px] text-warning">診斷資料不完整，部分異常規則無法判定。</div>
              )}
              {rows.length === 0 ? (
                snapshot.data_quality.overall_status === 'complete'
                  ? <div className="py-4 text-xs text-muted">最近交易日沒有觸發異常規則。</div>
                  : null
              ) : <div className="space-y-1">{rows.map(item => (
                <Link key={item.symbol} to={`/stocks/${encodeURIComponent(item.symbol)}`} className="flex w-full items-center justify-between gap-2 rounded-md bg-elevated/40 px-2 py-1.5 text-left hover:bg-elevated/80">
                  <span className="min-w-0"><span className="text-[11px] font-medium text-foreground">{item.name}</span><span className="ml-1 font-mono text-[9px] text-muted">{item.symbol}</span><span className="ml-1 block truncate text-[9px] text-secondary">{item.signals.map(signal => signalLabel[signal.type] ?? signal.type).join('、')}</span></span>
                  <span className={`shrink-0 font-mono text-[10px] ${pctClass(item.change_pct)}`}>{fmtStockPct(item.change_pct)}</span>
                </Link>
              ))}</div>}
            </>}
      {alertsLoading ? <div className="mt-1 text-[10px] text-muted">正在讀取 Quant 提醒…</div>
        : alertsError ? <div className="mt-1 text-[10px] text-warning">今日 Quant 提醒目前無法讀取。</div>
          : <>
      {displayedQuantEvents.map(event => (
        <Link key={`${event.ts}-${event.symbol}-${event.type}`} to={event.symbol ? `/stocks/${encodeURIComponent(event.symbol)}` : '/'} className="mt-1 flex items-center justify-between rounded-md border border-accent/20 bg-accent/5 px-2 py-1 text-[10px] hover:bg-accent/10">
          <span>{event.type === 'quant_top10_enter' ? '新進 Quant Top 10' : '跌出 Quant Top 10'} · {event.name ?? event.symbol}</span>
          <span className="font-mono text-muted">{event.symbol}</span>
        </Link>
      ))}
      {quantEvents.length > displayedQuantEvents.length && (
        <Link to="/monitor" className="mt-1 block text-right text-[10px] text-accent hover:underline">
          還有 {quantEvents.length - displayedQuantEvents.length} 則 Quant 提醒 · 查看監控中心
        </Link>
      )}
          </>}
      {snapshot && <div className="mt-1 text-[9px] text-muted">診斷狀態：{snapshot.data_quality.overall_status} · 日行情 {snapshot.data_quality.daily_status} · {snapshot.data_quality.evaluated_symbol_count} 檔</div>}
    </section>
  )
}

// ===== A5: 我的觀察 =====
// 自選清單是持久化資料，enriched 只負責補行情；兩者合併後即使某檔
// 沒有今日排名或行情，也保留該檔，避免使用者的觀察標的靜默消失。

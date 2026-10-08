import { Link } from 'react-router-dom'
import { Layers, Loader2 } from 'lucide-react'
import { type IndustryMetrics, type TaiwanIndustryIntelligenceSnapshot } from '@/lib/api'
import { fmtBigNum } from '@/lib/format'
import { useTodayQuantSelection } from '@/components/quant/TodaySelection'
import { type TaiwanDailyStatus } from './MarketOverviewCard'
import { SectionTitle } from './SectionTitle'
import { pctClass, fmtStockPct } from './dashboardFormat'

export function IndustryStrengthList({ title, rows, tone, quantBySymbol }: { title: string; rows: IndustryMetrics[]; tone: 'bull' | 'bear'; quantBySymbol: Map<string, number> }) {
  return (
    <div className="min-w-0 space-y-1">
      <div className={`text-[10px] font-medium ${tone === 'bull' ? 'text-bull' : 'text-bear'}`}>{title}</div>
      {rows.map(ind => (
        <div key={ind.industry} className="rounded-md bg-elevated/40 px-2 py-1.5 text-[11px]">
          <Link to={`/taiwan-screener?industry=${encodeURIComponent(ind.industry)}`} aria-label={`查看${ind.industry}類股股票`} className="block rounded hover:text-accent">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-foreground" title={ind.industry}>{ind.industry}</span>
              <span className={`shrink-0 font-mono font-semibold ${pctClass(ind.average_change_pct)}`}>{fmtStockPct(ind.average_change_pct)}</span>
            </div>
            <div className="mt-0.5 flex flex-wrap gap-x-2 text-[9px] text-muted">
              <span>中位 {fmtStockPct(ind.median_change_pct)}</span>
              <span>漲/跌 {ind.advance_count}/{ind.decline_count}</span>
              <span>成交 {fmtBigNum(ind.turnover)}</span>
              <span>占大盤 {(ind.turnover_share * 100).toFixed(1)}%</span>
            </div>
          </Link>
          {ind.top_turnover.slice(0, 2).length > 0 && <div className="mt-0.5 flex gap-2 truncate text-[9px] text-secondary"><span className="shrink-0">活躍：</span>{ind.top_turnover.slice(0, 2).map(stock => <Link key={stock.symbol} to={`/stocks/${encodeURIComponent(stock.symbol)}`} className="truncate hover:text-accent">{stock.name} {fmtStockPct(stock.change_pct)}</Link>)}</div>}
          {ind.top_turnover.some(stock => quantBySymbol.has(stock.symbol)) && <div className="mt-0.5 flex gap-2 truncate text-[9px] text-accent"><span className="shrink-0">Quant Top：</span>{ind.top_turnover.filter(stock => quantBySymbol.has(stock.symbol)).map(stock => <Link key={stock.symbol} to={`/stocks/${encodeURIComponent(stock.symbol)}`} className="truncate hover:underline">{stock.name} #{quantBySymbol.get(stock.symbol)}</Link>)}</div>}
        </div>
      ))}
      {rows.length === 0 && <div className="rounded border border-dashed border-border py-3 text-center text-[11px] text-muted">暫無資料</div>}
    </div>
  )
}

export function IndustryStrengthCard({ snapshot, loading, error, marketDailyStatus }: { snapshot?: TaiwanIndustryIntelligenceSnapshot | null; loading: boolean; error: boolean; marketDailyStatus: TaiwanDailyStatus }) {
  const quant = useTodayQuantSelection()
  const ind = { data: snapshot, isLoading: loading, isError: error }
  const comparable = (ind.data?.industries ?? []).filter(i => i.average_change_pct != null)
  const sorted = [...comparable].sort((a, b) => (b.average_change_pct ?? 0) - (a.average_change_pct ?? 0))
  const topCount = Math.min(5, sorted.length)
  const bottomCount = Math.min(5, Math.max(0, sorted.length - topCount))
  const top = sorted.slice(0, topCount)
  const bottom = sorted.slice(sorted.length - bottomCount).reverse()
  const quantBySymbol = new Map(quant.signals.map(signal => [signal.symbol, signal.rank]))
  const tradeDateIsToday = ind.data?.trade_date === new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Taipei' })

  return (
    <section className="rounded-card border border-border bg-surface/80 p-2.5 shadow-[0_1px_2px_hsl(var(--border)/0.4)] backdrop-blur-sm">
      <SectionTitle
        icon={Layers}
        title={tradeDateIsToday && marketDailyStatus === 'current' ? '今日類股熱度' : '最近交易日類股熱度'}
        hint={ind.data ? `資料交易日 ${ind.data.trade_date} · ${ind.data.industries.length} 大類股` : undefined}
      />
      {ind.isLoading ? (
        <div className="flex items-center gap-2 py-4 text-xs text-muted">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />正在讀取產業資料…
        </div>
      ) : ind.isError || !ind.data ? (
        <p className="py-4 text-xs text-muted">目前無法讀取產業強弱資料,不影響其他功能使用。</p>
      ) : sorted.length === 0 ? (
        <p className="py-4 text-xs text-muted">目前尚無可比較的產業資料。</p>
      ) : (
        <>
          {marketDailyStatus !== 'current' && <p className="mb-1 text-[10px] text-warning">日行情狀態 {marketDailyStatus}，資料交易日 {ind.data.trade_date}。</p>}
          <div className="grid grid-cols-2 gap-2">
            <IndustryStrengthList title="最強" rows={top} tone="bull" quantBySymbol={quantBySymbol} />
            <IndustryStrengthList title="最弱" rows={bottom} tone="bear" quantBySymbol={quantBySymbol} />
          </div>
        </>
      )}
    </section>
  )
}

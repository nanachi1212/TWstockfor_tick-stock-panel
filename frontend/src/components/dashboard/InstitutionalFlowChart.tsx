import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import ReactECharts from 'echarts-for-react'
import { ArrowUpRight, BarChart3 } from 'lucide-react'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { useTheme } from '@/lib/theme'
import { INVESTOR_LABELS, type Investor } from '@/lib/marketResearch'

const COLORS: Record<Investor, string> = { foreign: '#3b82f6', investment_trust: '#f59e0b', dealer: '#8b5cf6' }
const INVESTORS = Object.keys(INVESTOR_LABELS) as Investor[]

const lots = (v: number | null | undefined) => (v == null ? '資料不完整' : `${Math.round(v).toLocaleString('zh-TW')} 張`)

/** 首頁第一張圖：全市場三大法人每日買賣超（張）。缺資料的交易日留空，不畫成 0。 */
export function InstitutionalFlowChart() {
  const theme = useTheme()
  const query = useQuery({ queryKey: QK.institutionalFlowSeries, queryFn: api.taiwanInstitutionalFlowSeries, staleTime: 5 * 60 * 1000 })
  const days = query.data?.days ?? []
  const latest = days[days.length - 1]
  const textColor = theme === 'dark' ? '#a1a1aa' : '#52525b'
  const option = {
    animation: false, backgroundColor: 'transparent', textStyle: { color: textColor },
    grid: { left: 48, right: 12, top: 30, bottom: 24 },
    legend: { top: 0, textStyle: { color: textColor, fontSize: 11 } },
    tooltip: { trigger: 'axis', valueFormatter: lots },
    xAxis: { type: 'category', data: days.map(d => d.date.slice(5)), axisLabel: { color: textColor } },
    yAxis: { type: 'value', axisLabel: { color: textColor, formatter: (v: number) => `${v / 10000}萬` } },
    series: INVESTORS.map(i => ({ name: INVESTOR_LABELS[i], type: 'bar', data: days.map(d => d[i]), itemStyle: { color: COLORS[i] } })),
  }
  return (
    <section className="mb-1.5 rounded-card border border-border bg-surface/80 p-2">
      <div className="mb-1 flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <BarChart3 className="h-3.5 w-3.5 text-accent" />
          <h2 className="text-sm font-semibold text-foreground">三大法人買賣超</h2>
          <span className="text-[11px] text-muted">近 {days.length || 20} 個交易日 · 全市場 · 單位：張</span>
        </div>
        <Link to="/market-research" className="inline-flex items-center gap-0.5 text-[11px] text-muted hover:text-accent" title="法人統計明細">
          明細 <ArrowUpRight className="h-3.5 w-3.5" />
        </Link>
      </div>
      {latest && (
        <div className="mb-1 flex flex-wrap gap-x-4 text-xs text-secondary">
          <span>{latest.date}</span>
          {INVESTORS.map(i => (
            <span key={i}>{INVESTOR_LABELS[i]} <b className={latest[i] == null ? 'text-muted' : latest[i]! >= 0 ? 'text-bull' : 'text-bear'}>{lots(latest[i])}</b></span>
          ))}
        </div>
      )}
      {query.isLoading ? <p className="py-16 text-center text-xs text-muted">正在讀取法人資料…</p>
        : query.isError ? <p className="py-16 text-center text-xs text-muted">目前無法讀取法人資料，不影響其他功能使用。</p>
        : days.length ? <ReactECharts option={option} style={{ height: 240 }} notMerge />
        : <p className="py-16 text-center text-xs text-muted">尚無法人資料。</p>}
    </section>
  )
}

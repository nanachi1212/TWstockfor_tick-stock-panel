import { useState } from 'react'
import ReactECharts from 'echarts-for-react'
import { useNavigate } from 'react-router-dom'
import { signed, toneClass } from '@/components/ChipRankingTab'
import type { DayCoverage, MarketHeatmapSnapshot } from '@/lib/marketResearch'
import { useChartTheme } from '@/lib/theme'

// ECharts cannot read CSS variables; these mirror --bear / --bull in index.css (Taiwan: red up, green down).
const DOWN = '#12B76A', NEUTRAL = '#6B7280', UP = '#F04438'

export const yi = (twd: number) => (twd / 1e8).toLocaleString('zh-TW', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
export const pct = (v: number | null, digits = 1) => signed(v == null ? null : v * 100, digits, '%')
const industryLabel = (industry: string) => industry === 'UNCLASSIFIED' ? '未分類' : industry
export const previousIncomplete = (coverage: DayCoverage | null) => coverage?.previous_status === 'partial' || coverage?.previous_status === 'unknown'

/** Warning row for a partial snapshot: says what is missing and what the numbers then do not mean. */
export function CoverageNotice({ date, reason, coverage, impact }: { date: string; reason: string | null; coverage: DayCoverage | null; impact: string }) {
  if (!coverage) return null
  const exchanges = Object.entries(coverage.exchanges)
  const head = reason === 'census_unknown'
    ? `${date} 尚無 ${exchanges.filter(([, c]) => c?.missing == null).map(([ex]) => ex).join('、')} 官方日報名單可核對，無法確認日資料完整`
    : `${date} 日資料不完整：缺 ${coverage.missing_count} 檔（${exchanges.map(([ex, c]) => `${ex} ${c?.missing}`).join('／')}）`
  return <p role="status" title={coverage.missing_symbols.join('、') || undefined} className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm text-warning">{head}，{impact}</p>
}

interface Tile {
  name: string
  /** [amount TWD, change_pct with null drawn as 0 (neutral grey)] */
  value: [number, number]
  change: number | null
  share: number
  symbol?: string
  breadth?: string
  children?: Tile[]
}

export function MarketHeatmap({ data }: { data: MarketHeatmapSnapshot }) {
  const navigate = useNavigate()
  const chart = useChartTheme()
  const [chartHeight] = useState(() => window.matchMedia('(max-width: 767px)').matches ? Math.min(520, Math.round(window.innerHeight * 0.55)) : 520)
  if (data.status === 'unavailable') return <p role="status" className="rounded-lg border border-border bg-surface p-6 text-muted">{data.date} 尚無日資料</p>

  const totalChange = data.previous_total_amount ? data.total_amount / data.previous_total_amount - 1 : null
  const stats: Array<[string, string, string]> = [
    ['總成交金額', `${yi(data.total_amount)} 億`, ''],
    [`較前日${previousIncomplete(data.coverage) ? '（前日資料不完整）' : data.previous_date ? `（${data.previous_date}）` : ''}`, pct(totalChange, 2), toneClass(totalChange)],
    ['上漲', `${data.advance} 家`, 'text-bull'],
    ['下跌', `${data.decline} 家`, 'text-bear'],
    ['平盤', `${data.flat} 家`, ''],
    ['無法比較', `${data.uncompared} 家`, 'text-muted'],
  ]
  const tiles: Tile[] = data.industries.map(i => ({
    name: industryLabel(i.industry), value: [i.amount, i.change_pct ?? 0], change: i.change_pct, share: i.share,
    breadth: `上漲 ${i.advance} 家／下跌 ${i.decline} 家`,
    children: i.stocks.map(s => ({ name: s.name || s.symbol, value: [s.amount, s.change_pct ?? 0], change: s.change_pct, share: s.amount / data.total_amount, symbol: s.symbol })),
  }))
  const option = {
    animation: false, backgroundColor: 'transparent',
    tooltip: {
      renderMode: 'richText', backgroundColor: chart.tooltipBg, borderColor: chart.tooltipBorder, textStyle: { color: chart.tooltipText },
      formatter: ({ data: t }: { data: Tile }) => [t.name, `成交金額 ${yi(t.value[0])} 億`, `占比 ${(t.share * 100).toFixed(2)}%`,
        `漲跌 ${t.change == null ? '無法比較' : pct(t.change)}`, t.breadth].filter(Boolean).join('\n'),
    },
    visualMap: { type: 'continuous', dimension: 1, min: -0.1, max: 0.1, inRange: { color: [DOWN, NEUTRAL, UP] }, text: ['+10%', '-10%'],
      orient: 'horizontal', top: 0, right: 0, itemHeight: 140, itemWidth: 10, textStyle: { color: chart.text } },
    series: [{
      type: 'treemap', data: tiles, visualDimension: 1, leafDepth: 1, nodeClick: 'zoomToNode', roam: false,
      left: 0, right: 0, top: 36, bottom: 32, breadcrumb: { left: 0, bottom: 0 },
      itemStyle: { borderColor: chart.border, borderWidth: 1, gapWidth: 1 },
      label: { formatter: ({ name, data: t }: { name: string; data: Tile }) => `${name}\n${pct(t.change)}` },
    }],
  }
  const onEvents = { click: ({ data: t }: { data?: Tile }) => { if (t?.symbol && !t.children) navigate(`/stocks/${encodeURIComponent(t.symbol)}`) } }

  return <div className="space-y-4">
    {data.status === 'partial' && <CoverageNotice date={data.date} reason={data.reason} coverage={data.coverage} impact="總成交金額與占比不代表全市場" />}
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
      {stats.map(([label, value, tone]) => <div key={label} className="rounded-lg border border-border bg-surface p-3">
        <div className="text-xs text-muted">{label}</div><div className={`mt-1 text-base sm:text-lg font-semibold tabular-nums ${tone}`}>{value}</div>
      </div>)}
    </div>
    <p className="text-xs text-muted leading-relaxed">面積＝成交金額、顏色＝原始收盤價漲跌幅（成交金額加權，不是加權指數）；ETF 歸為一個板塊；除權息日漲跌幅會失真。點產業放大看成分股，點個股進個股頁。</p>
    {tiles.length === 0 ? <p className="rounded-lg border border-border bg-surface p-6 text-muted">當日沒有成交資料。</p> : <>
      <div className="rounded-lg border border-border bg-surface p-3"><ReactECharts option={option} onEvents={onEvents} style={{ height: chartHeight }} /></div>
      <div className="overflow-x-auto rounded-lg border border-border"><table className="w-full text-sm md:min-w-[640px] max-md:[&_th]:px-2 max-md:[&_td]:px-2">
        <thead className="bg-elevated text-xs text-secondary"><tr>{[['產業', ''], ['成交金額（億）', ''], ['占比', 'hidden md:table-cell'], ['加權漲跌', ''], ['上漲', ''], ['下跌', ''], ['平盤', 'hidden md:table-cell']].map(([t, hide]) => <th key={t} className={`p-3 text-right first:text-left ${hide}`}>{t}</th>)}</tr></thead>
        <tbody>{data.industries.map(i => <tr key={i.industry} className="border-t border-border">
          <td className="p-3">{industryLabel(i.industry)}</td>
          <td className="p-3 text-right tabular-nums">{yi(i.amount)}</td>
          <td className="hidden p-3 text-right tabular-nums md:table-cell">{(i.share * 100).toFixed(2)}%</td>
          <td className={`p-3 text-right tabular-nums ${toneClass(i.change_pct)}`}>{pct(i.change_pct, 2)}</td>
          <td className="p-3 text-right tabular-nums text-bull">{i.advance}</td>
          <td className="p-3 text-right tabular-nums text-bear">{i.decline}</td>
          <td className="hidden p-3 text-right tabular-nums md:table-cell">{i.flat}</td>
        </tr>)}</tbody>
      </table></div>
    </>}
  </div>
}

import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import ReactECharts from 'echarts-for-react'
import { MarketBreadthValuationTab } from '@/components/MarketBreadthValuationTab'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { useTheme } from '@/lib/theme'
import {
  INVESTOR_LABELS, MARKET_RESEARCH_TABS, rotationPoints,
  type Investor, type ResearchMetric, type InstitutionalWindow,
  type InstitutionalStatisticsRow, type IndustryRotationRow,
} from '@/lib/marketResearch'

const STATUS_LABELS = { available: '完整', partial: '部分資料', unavailable: '不可用' }

export function Metric({ metric, percent = false, signed = false }: { metric: ResearchMetric; percent?: boolean; signed?: boolean }) {
  const value = metric.value
  const display = value == null ? '—' : `${signed && value > 0 ? '+' : ''}${(percent ? value * 100 : value).toLocaleString('zh-TW', { maximumFractionDigits: 3 })}${percent ? '%' : ''}`
  const coverage = metric.coverage
  return <div className="tabular-nums">
    <div className={value != null && signed ? (value > 0 ? 'text-bull' : value < 0 ? 'text-bear' : '') : ''}>{display}</div>
    <details className="text-[10px] text-muted font-normal">
      <summary className="cursor-pointer whitespace-nowrap">{STATUS_LABELS[metric.status]} · {coverage.coverage_days}/{coverage.expected_days} 日</summary>
      <div className="min-w-40 py-1 whitespace-normal">
        <div>來源：{metric.source.join('、') || '未取得'}</div>
        <div>資料截至：{metric.as_of || '未取得'}（目標 {metric.date}）</div>
        <div>覆蓋：{coverage.observed_observations}/{coverage.expected_observations} 筆</div>
        {coverage.missing_dates.length > 0 && <div>缺日：{coverage.missing_dates.join('、')}</div>}
      </div>
    </details>
  </div>
}

function InstitutionalTable({ rows, investor, unit }: { rows: InstitutionalStatisticsRow[]; investor: Investor; unit: 'shares' | 'lots' }) {
  return <div className="overflow-x-auto rounded-lg border border-border">
    <table className="w-full text-sm min-w-[720px]">
      <thead className="bg-elevated text-secondary text-xs"><tr>
        <th className="p-3 text-left">標的／範圍</th><th className="p-3 text-right">買賣超（{unit === 'shares' ? '股' : '張'}）</th>
        <th className="p-3 text-right">淨買賣超／成交量</th><th className="p-3 text-right">連買（交易日）</th><th className="p-3 text-right">連賣（交易日）</th>
      </tr></thead>
      <tbody>{rows.map(row => {
        const stats = row.investors[investor]
        return <tr key={row.symbol} className="border-t border-border hover:bg-elevated/50">
          <td className="p-3">{row.symbol.includes('.') ? <Link className="text-accent" to={`/stocks/${encodeURIComponent(row.symbol)}`}>{row.symbol} {row.name}</Link> : row.symbol === 'ALL' ? '上市＋上櫃' : row.symbol === 'TWSE' ? '上市' : '上櫃'}</td>
          <td className="p-3 text-right"><Metric metric={unit === 'shares' ? stats.net_shares : stats.net_lots} signed /></td>
          <td className="p-3 text-right"><Metric metric={stats.net_volume_ratio} percent signed /></td>
          <td className="p-3 text-right"><Metric metric={stats.buy_streak} />{stats.streak_capped && stats.buy_streak.value !== 0 && <span className="text-[10px] text-muted">至少，達窗口上限</span>}</td>
          <td className="p-3 text-right"><Metric metric={stats.sell_streak} />{stats.streak_capped && stats.sell_streak.value !== 0 && <span className="text-[10px] text-muted">至少，達窗口上限</span>}</td>
        </tr>
      })}</tbody>
    </table>
    {rows.length === 0 && <p className="p-6 text-muted">沒有符合條件的標的。</p>}
  </div>
}

function Rotation({ rows }: { rows: IndustryRotationRow[] }) {
  const theme = useTheme()
  const points = rotationPoints(rows)
  const textColor = theme === 'dark' ? '#a1a1aa' : '#52525b'
  const option = {
    animation: false, backgroundColor: 'transparent', textStyle: { color: textColor },
    grid: { left: 65, right: 25, top: 40, bottom: 65 },
    tooltip: { trigger: 'item', formatter: (point: { name: string; value: number[] }) => `${point.name}\n20D RS ${point.value[0].toFixed(2)}%\n占比變化 ${point.value[1].toFixed(2)} pp`, renderMode: 'richText' },
    xAxis: { type: 'value', name: '20D RS（%）', nameLocation: 'middle', nameGap: 32, min: (v: { min: number }) => Math.min(-0.5, v.min * 1.15), max: (v: { max: number }) => Math.max(0.5, v.max * 1.15), axisLabel: { color: textColor, formatter: (v: number) => v.toFixed(1) } },
    yAxis: { type: 'value', name: '成交值占比變化（pp）', min: (v: { min: number }) => Math.min(-0.5, v.min * 1.15), max: (v: { max: number }) => Math.max(0.5, v.max * 1.15), axisLabel: { color: textColor, formatter: (v: number) => v.toFixed(1) } },
    series: [{ type: 'scatter', symbolSize: 14, data: points, itemStyle: { color: '#8b5cf6' }, label: { show: true, formatter: '{b}', position: 'top', color: textColor }, markLine: { silent: true, symbol: 'none', label: { show: false }, lineStyle: { color: textColor }, data: [{ xAxis: 0 }, { yAxis: 0 }] } }],
  }
  return <div className="space-y-4">
    <p className="text-xs text-muted leading-relaxed">RS＝產業等權報酬－全市場股票等權報酬，採原始收盤價，未排除除權息影響。成交值占比採每日產業成交值／全市場股票成交值，20D 為每日占比的算術平均；變化＝當日占比－20D 平均（百分點）。ETF 不納入產業輪動。</p>
    <div className="rounded-lg border border-border bg-surface p-3">
      <div className="grid grid-cols-2 gap-2 text-xs text-secondary"><span>左上：落後、資金增加</span><span className="text-right">右上：領先、資金增加</span></div>
      {points.length ? <ReactECharts option={option} style={{ height: 390 }} /> : <p className="py-12 text-center text-muted">完整資料不足，暫無可繪製的產業。</p>}
      <div className="grid grid-cols-2 gap-2 text-xs text-secondary"><span>左下：落後、資金減少</span><span className="text-right">右下：領先、資金減少</span></div>
      <p className="mt-3 text-xs text-muted">僅繪製 20D RS 與占比變化皆完整的產業（{points.length}/{rows.length}）。部分資料可於下表查看。</p>
    </div>
    <div className="overflow-x-auto rounded-lg border border-border"><table className="w-full min-w-[800px] text-sm">
      <thead className="bg-elevated text-xs text-secondary"><tr>{['產業', '5D RS', '20D RS', '當日成交值占比', '20D 平均占比', '變化（pp）'].map(t => <th key={t} className="p-3 text-right first:text-left">{t}</th>)}</tr></thead>
      <tbody>{rows.map(r => <tr className="border-t border-border" key={r.industry}>
        <td className="p-3">{r.industry === 'UNCLASSIFIED' ? '未分類' : r.industry}</td>
        <td className="p-3 text-right"><Metric metric={r.relative_strength_5d} percent signed /></td>
        <td className="p-3 text-right"><Metric metric={r.relative_strength_20d} percent signed /></td>
        <td className="p-3 text-right"><Metric metric={r.turnover_share} percent /></td>
        <td className="p-3 text-right"><Metric metric={r.average_turnover_share_20d} percent /></td>
        <td className="p-3 text-right"><Metric metric={r.turnover_share_delta_pp} signed /></td>
      </tr>)}</tbody></table>{rows.length === 0 && <p className="p-6 text-muted">尚無產業資料。</p>}</div>
  </div>
}

export function MarketResearch() {
  const [params, setParams] = useSearchParams()
  const selected = params.get('tab') || 'institutional'
  const tab = MARKET_RESEARCH_TABS.find(t => t.id === selected) || MARKET_RESEARCH_TABS[0]
  const date = params.get('date') || ''
  const rawMarket = params.get('market')
  const researchMarket = rawMarket === 'TWSE' || rawMarket === 'TPEX' ? rawMarket : 'composite'
  const rawWindow = Number(params.get('window') || 5)
  const window = ([5, 10, 20, 45, 60].includes(rawWindow) ? rawWindow : 5) as InstitutionalWindow
  const [investor, setInvestor] = useState<Investor>('foreign')
  const [unit, setUnit] = useState<'shares' | 'lots'>('lots')
  const [search, setSearch] = useState('')
  const [exchange, setExchange] = useState('ALL')
  const [page, setPage] = useState(1)
  const update = (key: string, value: string) => { const next = new URLSearchParams(params); if (value) next.set(key, value); else next.delete(key); setParams(next) }
  const institutional = useQuery({ queryKey: QK.institutionalStatistics(date, window), queryFn: () => api.taiwanInstitutionalStatistics(date || undefined, window), enabled: tab.id === 'institutional', staleTime: 30_000 })
  const rotation = useQuery({ queryKey: QK.industryRotation(date), queryFn: () => api.taiwanIndustryRotation(date || undefined), enabled: tab.id === 'rotation', staleTime: 30_000 })
  const rows = useMemo(() => (institutional.data?.securities || []).filter(r => (exchange === 'ALL' || r.exchange === exchange) && `${r.symbol} ${r.name}`.includes(search.trim())).sort((a, b) => (b.investors[investor].net_shares.value ?? -Infinity) - (a.investors[investor].net_shares.value ?? -Infinity) || a.symbol.localeCompare(b.symbol)), [institutional.data, search, exchange, investor])
  const currentPage = Math.min(page, Math.max(1, Math.ceil(rows.length / 50)))
  const query = tab.id === 'rotation' ? rotation : institutional
  const inputClass = 'rounded-md border border-border bg-surface px-3 py-2 text-sm'
  return <div className="p-4 md:p-6 space-y-5 text-foreground">
    <div><h1 className="text-xl font-semibold">大盤研究</h1><p className="mt-1 text-sm text-muted">法人籌碼、產業輪動、大盤寬度與估值</p></div>
    <div role="tablist" aria-label="大盤研究" className="flex flex-wrap gap-2 border-b border-border pb-3">
      {MARKET_RESEARCH_TABS.map(t => <button key={t.id} role="tab" aria-selected={tab.id === t.id} onClick={() => update('tab', t.id)} className={`rounded-md px-3 py-2 text-sm ${tab.id === t.id ? 'bg-accent/15 text-accent' : 'text-secondary hover:bg-elevated'}`}>{t.label}{!t.enabled && <span className="ml-1 text-[10px] text-muted">規劃中</span>}</button>)}
    </div>
    <div role="tabpanel" aria-label={tab.label} className="space-y-4">
      {tab.id === 'breadth' || tab.id === 'valuation' ? <>
        <div className="flex flex-wrap items-center gap-3">
          <label className="text-sm">研究日期 <input aria-label="研究日期" type="date" value={date} onChange={e => update('date', e.target.value)} className={inputClass} /></label>
          <button className={inputClass} onClick={() => update('date', '')}>最新可觀測交易日</button>
          <label className="text-sm">市場 <select aria-label="研究市場" value={researchMarket} onChange={e => update('market', e.target.value)} className={inputClass}>
            <option value="composite">上市＋上櫃</option><option value="TWSE">上市 TWSE</option><option value="TPEX">上櫃 TPEx</option>
          </select></label>
        </div>
        <MarketBreadthValuationTab asOf={date} market={researchMarket} view={tab.id} />
      </> : <>
        <div className="flex flex-wrap items-center gap-3">
          <label className="text-sm">交易日 <input aria-label="交易日" type="date" value={date} onChange={e => update('date', e.target.value)} className={inputClass} /></label>
          <button className={inputClass} onClick={() => update('date', '')}>最新應有交易日</button>
          <button className={inputClass} disabled={query.isFetching} onClick={() => void query.refetch()}>重新讀取</button>
          <span className="text-xs text-muted">目標日期：{query.data?.date || date || '載入中'}</span>
          {query.isFetching && !query.isLoading && <span role="status" className="text-xs text-muted">更新中…</span>}
        </div>
        {tab.id === 'institutional' && <>
          <div className="flex flex-wrap gap-3">
            <label>窗口 <select aria-label="交易日窗口" value={window} onChange={e => update('window', e.target.value)} className={inputClass}>{[5, 10, 20, 45, 60].map(d => <option value={d} key={d}>{d} 交易日</option>)}</select></label>
            <label>法人 <select aria-label="法人" value={investor} onChange={e => setInvestor(e.target.value as Investor)} className={inputClass}>{Object.entries(INVESTOR_LABELS).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
            <label>單位 <select aria-label="數量單位" value={unit} onChange={e => setUnit(e.target.value as 'shares' | 'lots')} className={inputClass}><option value="lots">張</option><option value="shares">股</option></select></label>
          </div>
          <p className="text-xs text-muted leading-relaxed">彙總範圍為目前支援的上市、上櫃股票與 ETF，非交易所發布的法人買賣金額。1 張＝1,000 股，不估算金額。占比採同一標的、同一日期的淨買賣超／成交股數；部分資料只累計已取得資料。連買／連賣以窗口內連續交易日計算，缺日或市場資料不完整即中斷。點選每格狀態可查看來源與覆蓋。</p>
        </>}
        {query.isLoading && <p role="status" className="py-8 text-muted">載入資料中…</p>}
        {query.isError && <div role="alert" className="rounded-lg border border-danger/30 p-4 text-danger">資料讀取失敗：{query.error instanceof Error ? query.error.message : '請稍後重試'}<button className="ml-3 underline" onClick={() => void query.refetch()}>重試</button></div>}
        {tab.id === 'institutional' && institutional.data && !institutional.isError && <>
          <h2 className="font-medium">{INVESTOR_LABELS[investor]}市場彙總</h2><InstitutionalTable rows={institutional.data.aggregates} investor={investor} unit={unit} />
          <div className="flex flex-wrap gap-3"><input aria-label="搜尋標的" placeholder="代號或名稱" value={search} onChange={e => { setSearch(e.target.value); setPage(1) }} className={inputClass} /><select aria-label="交易所" className={inputClass} value={exchange} onChange={e => { setExchange(e.target.value); setPage(1) }}><option value="ALL">上市＋上櫃</option><option value="TWSE">上市</option><option value="TPEX">上櫃</option></select><span className="self-center text-xs text-muted">{rows.length} 檔，買賣超由大至小</span></div>
          <InstitutionalTable rows={rows.slice((currentPage - 1) * 50, currentPage * 50)} investor={investor} unit={unit} />
          <div className="flex gap-3 items-center text-sm"><button className={inputClass} disabled={currentPage === 1} onClick={() => setPage(currentPage - 1)}>上一頁</button><span>{currentPage}/{Math.max(1, Math.ceil(rows.length / 50))}</span><button className={inputClass} disabled={currentPage * 50 >= rows.length} onClick={() => setPage(currentPage + 1)}>下一頁</button></div>
        </>}
        {tab.id === 'rotation' && rotation.data && !rotation.isError && <Rotation rows={rotation.data.industries} />}
      </>}
    </div>
  </div>
}

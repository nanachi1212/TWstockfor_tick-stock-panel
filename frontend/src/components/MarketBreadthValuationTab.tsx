import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import type { ResearchMarket, ResearchMetric, ValuationMetric } from '@/lib/marketBreadthTypes'

export interface MarketBreadthValuationTabProps {
  asOf?: string
  market?: ResearchMarket
  view?: 'breadth' | 'valuation' | 'all'
}

const labels: Record<string, string> = {
  ma20: '高於 MA20', ma60: '高於 MA60', ma240: '高於 MA240',
  new_high_52w: '52 週新高', new_low_52w: '52 週新低', ad_net: '漲跌家數差', ad_line: 'A/D Line',
  pe: '個股本益比中位數', pb: '個股股價淨值比中位數', dividend_yield: '個股殖利率中位數',
}
const reasons: Record<string, string> = {
  insufficient_history: '新上市或歷史不足', missing_session_price: '交易日日 K 缺漏',
  missing_target_session: '查詢交易日缺漏', invalid_price: '價格無法比較',
  calendar_unverified: '交易日窗口尚未確認', not_ordinary_stock: '已驗證非普通股',
  corporate_action_coverage_unavailable: '公司行動涵蓋證據不足',
  incomparable_corporate_action: '公司行動調整無法可靠比較', missing_valuation_record: '缺少估值紀錄',
  missing_value: '估值缺值', nonpositive_pe: '本益比非正值（不可當 0）',
  invalid_value: '估值無效', source_unavailable: '來源不可用',
}
const statuses: Record<string, string> = { available: '可用', partial: '部分可用', unavailable: '不可用', stale: '資料較舊' }

function format(value: number | null, unit: string) {
  if (value == null) return '無法計算'
  return unit === 'ratio' ? `${(value * 100).toFixed(1)}%` : value.toLocaleString('zh-TW', { maximumFractionDigits: 2 })
}

function MetricCard({ name, data, valuation = false }: { name: string; data: ResearchMetric; valuation?: boolean }) {
  const rank = data as ValuationMetric
  return <article className="rounded-lg border border-border bg-surface p-4 min-w-0">
    <h3 className="text-sm text-secondary">{labels[name] ?? name}</h3>
    <p className="my-2 text-2xl font-semibold">{valuation
      ? data.value == null ? '無法計算' : `${data.value.toFixed(2)}${name === 'dividend_yield' ? '%' : ' 倍'}`
      : format(data.value, data.unit)}</p>
    <p className="text-sm">{statuses[data.status]} · 納入 {data.included_count} / 排除 {data.excluded_count}</p>
    <p className="text-sm text-secondary">樣本涵蓋率 {data.coverage == null ? '未知' : `${(data.coverage * 100).toFixed(1)}%`}</p>
    {valuation && <p className="mt-2 text-sm">歷史 percentile：{rank.percentile == null ? '歷史不足' : `${(rank.percentile * 100).toFixed(1)}%`}
      <span className="block text-secondary">{rank.percentile_sample_count} 個歷史交易日
        {rank.percentile_start && `（${rank.percentile_start} 至 ${rank.percentile_end}）`}</span></p>}
    {!!data.excluded_count && <details className="mt-2 text-sm">
      <summary className="cursor-pointer">排除原因</summary>
      <ul className="mt-1 space-y-1">{Object.entries(data.excluded_reason_counts).map(([reason, count]) =>
        <li key={reason}>{reasons[reason] ?? reason}：{count}</li>)}</ul>
    </details>}
  </article>
}

/** Shared shell context, with independent section queries so valuation stays fast. */
export function MarketBreadthValuationTab({ asOf, market: controlledMarket, view = 'all' }: MarketBreadthValuationTabProps) {
  const [localMarket, setMarket] = useState<ResearchMarket>('composite')
  const [localDate, setDate] = useState('')
  const market = controlledMarket ?? localMarket
  const selectedDate = (asOf ?? localDate) || undefined
  const client = useQueryClient()
  const query = useQuery({
    queryKey: QK.marketBreadthValuation(selectedDate, market, 20, view),
    queryFn: () => api.marketBreadthValuation(selectedDate, market, 20, view),
    staleTime: 60_000,
  })
  const refresh = useMutation({
    mutationFn: api.refreshMarketValuation,
    onSuccess: () => client.invalidateQueries({ queryKey: ['market-breadth-valuation'] }),
  })
  const data = query.data
  return <section aria-label="大盤寬度與估值" className="space-y-4">
    <div className="flex flex-wrap items-center gap-3">
      {!controlledMarket && <label>市場 <select aria-label="研究市場" value={market}
        onChange={e => setMarket(e.target.value as ResearchMarket)} className="rounded border border-border bg-elevated text-foreground [color-scheme:light] dark:[color-scheme:dark] p-2">
        <option value="composite">上市＋上櫃</option><option value="TWSE">上市 TWSE</option><option value="TPEX">上櫃 TPEx</option>
      </select></label>}
      {asOf === undefined && <label>研究日期 <input type="date" aria-label="研究日期" value={localDate}
        onChange={e => setDate(e.target.value)} className="rounded border border-border bg-elevated text-foreground [color-scheme:light] dark:[color-scheme:dark] p-2" /></label>}
      <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}
        className="rounded border border-border px-3 py-2 disabled:opacity-50">重新讀取</button>
      {view !== 'breadth' && <button type="button" onClick={() => refresh.mutate()} disabled={refresh.isPending}
        className="rounded border border-border px-3 py-2 disabled:opacity-50">{refresh.isPending ? '更新估值中…' : '更新官方估值'}</button>}
      {query.isFetching && !query.isLoading && <span role="status" className="text-sm text-secondary">更新中…</span>}
    </div>
    <p className="text-sm text-secondary">{view === 'valuation'
      ? '以目前保存的官方個股估值計算描述性統計。'
      : '依可觀測日 K 計算的研究統計。涵蓋率為可觀測樣本的可計算比例，完整歷史普通股 universe 涵蓋率未知。'}</p>
    {refresh.isError && <p role="alert">估值更新失敗，請稍後重試。</p>}
    {refresh.data && <p role="status">估值更新：{statuses[refresh.data.status]}，保存 {refresh.data.records_saved} 筆。
      {refresh.data.failed.length > 0 && ` ${refresh.data.failed.map(f => f.market).join('、')} 官方來源未成功，保留原有資料。`}</p>}
    {query.isLoading && <p role="status">載入大盤研究資料中…</p>}
    {query.isError && <div role="alert">大盤研究資料讀取失敗。
      <button type="button" onClick={() => void query.refetch()} className="ml-2 underline">重試</button></div>}
    {data && <>
      <p className="text-sm">資料日期：{data.as_of ?? '尚無資料'} · {statuses[data.status] ?? data.status}
        {data.stale && `（查詢日期 ${data.requested_as_of}，此為較舊快照）`}</p>
      <div className="rounded-lg border border-border p-3 text-sm text-secondary">
        {data.warnings.map(text => <p key={text}>{text}</p>)}
      </div>
      {view !== 'valuation' && !data.latest && <p>尚無可觀測日 K，請先完成台股資料更新。</p>}
      {view === 'valuation' && !data.valuation && <p>尚無保存的官方估值，請更新官方估值。</p>}
      {view !== 'valuation' && data.latest && <>
        {!!data.latest.missing_markets.length && <p className="text-sm">尚無 {data.latest.missing_markets.join('、')} 可觀測樣本，合併市場涵蓋不完整。</p>}
        <p className="text-sm">歷史普通股資格已驗證 {data.latest.eligibility_verified_count} 家，尚未驗證 {data.latest.eligibility_unknown_count} 家。
          漲 {data.latest.advances ?? '未知'} / 跌 {data.latest.declines ?? '未知'} / 平 {data.latest.unchanged ?? '未知'}。
          A/D 區段起點：{data.latest.ad_segment_start ?? '尚無可比較資料'}。</p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {Object.entries(data.latest.metrics).map(([name, value]) => <MetricCard key={name} name={name} data={value} />)}
        </div>
        <p className="text-sm text-secondary">價格來源：本地台股日 K，舊日 K 未保存可驗證的來源抓取時間。
          歷史資格證據抓取時間：{data.latest.eligibility_retrieved_at ?? '未知'}。</p>
      </>}
      {view !== 'breadth' && data.valuation && <>
        {selectedDate && <p className="text-sm text-secondary">歷史日期估值使用目前保存的最新 revision，非 Historical PIT。</p>}
        <h3 className="font-semibold">同交易日個股估值中位數</h3>
        <p className="text-sm text-secondary">缺失與非正本益比排除，不當成 0；合併市場需同日兩個交易所資料。Percentile 至少需要 20 個先前保存交易日，採相同口徑中位數的 ties midrank。</p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {Object.entries(data.valuation.metrics).map(([name, value]) => <MetricCard key={name} name={name} data={value} valuation />)}
        </div>
        <p className="break-words text-sm text-secondary">估值來源：{data.valuation.source.join('、') || '尚無同日官方估值'}；抓取時間：{data.valuation.retrieved_at ?? '未知'}；首次發布時間尚未驗證。</p>
      </>}
      {view !== 'valuation' && !!data.history.length && <details>
        <summary className="cursor-pointer">檢視逐日寬度與 A/D Line</summary>
        <div className="mt-2 overflow-x-auto"><table className="w-full text-sm">
          <caption className="text-left text-secondary">描述性歷史；A/D 數值僅在同一區段內比較。</caption>
          <thead><tr>{['日期', 'MA20', 'MA60', 'MA240', '新高', '新低', 'A/D Line', '納入/排除（A/D）'].map(s => <th key={s} className="p-2 text-left whitespace-nowrap">{s}</th>)}</tr></thead>
          <tbody>{data.history.map(row => <tr key={row.as_of} className="border-t border-border">
            <td className="p-2 whitespace-nowrap">{row.as_of}</td>
            {['ma20', 'ma60', 'ma240', 'new_high_52w', 'new_low_52w', 'ad_line'].map(name =>
              <td key={name} className="p-2 whitespace-nowrap">{format(row.metrics[name]?.value ?? null, row.metrics[name]?.unit ?? '')}</td>)}
            <td className="p-2">{row.metrics.ad_net.included_count}/{row.metrics.ad_net.excluded_count}</td>
          </tr>)}</tbody>
        </table></div>
      </details>}
    </>}
  </section>
}

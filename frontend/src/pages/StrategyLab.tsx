import { useState } from 'react'
import { Link } from 'react-router-dom'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { FlaskConical } from 'lucide-react'
import { api, type StrategyLabFilters, type StrategyLabHorizon, type StrategyLabObservation, type StrategyLabStats } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { STRATEGY_GUIDE } from '@/lib/strategyGuide'

const HORIZONS = ['1D', '5D', '20D'] as const
const SLICES = [
  ['source', 'Selection source'], ['exchange', 'TWSE / TPEX'], ['industry', '產業'],
  ['market_regime', '市場環境'], ['liquidity_bucket', '流動性分組'], ['risk_status', '風險 gate'],
] as const
const pct = (value: number | null) => value === null ? '樣本不足或不可用' : `${value.toFixed(2)}%`
const basis = (value: string) => value === 'next_open' ? '下一交易日開盤' : '訊號日參考收盤'

function Horizon({ stats }: { stats: StrategyLabHorizon }) {
  return <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
    <dt className="text-muted-foreground">matured / N</dt><dd>{stats.matured} / {stats.hit_rate_denominator}</dd>
    <dt className="text-muted-foreground">命中率</dt><dd>{pct(stats.hit_rate_pct)}</dd>
    <dt className="text-muted-foreground">平均報酬</dt><dd>{pct(stats.average_return_pct)}</dd>
    <dt className="text-muted-foreground">benchmark / N</dt><dd>{pct(stats.average_benchmark_return_pct)} / {stats.benchmark_n}</dd>
    <dt className="text-muted-foreground">超額報酬 / N</dt><dd>{pct(stats.average_excess_pct)} / {stats.excess_n}</dd>
    <dt className="text-muted-foreground">pending / unavailable</dt><dd>{stats.pending} / {stats.unavailable}</dd>
  </dl>
}

function ReviewLink({ row }: { row: StrategyLabObservation }) {
  const q = new URLSearchParams()
  if (row.identity.source === 'Daily recommendation') {
    q.set('tab', 'live')
    q.set('model_key', String(row.provenance.model_key))
    q.set('session', row.signal_date)
  } else if (row.identity.entry_basis === 'next_open') {
    q.set('tab', 'forward'); q.set('batch_id', row.snapshot_id); q.set('strategy_id', row.identity.strategy_id)
  } else {
    q.set('snapshot_id', row.snapshot_id)
  }
  return <Link className="text-primary hover:underline" to={`/selection-review?${q}`}>Selection Review</Link>
}

function Comparison({ strategies }: { strategies: StrategyLabStats[] }) {
  return <section aria-label="策略比較" className="space-y-3">
    <h2 className="font-semibold">策略並排比較（{strategies.length} / 4）</h2>
    {strategies.length < 2 ? <p className="text-sm text-muted-foreground">勾選 2–4 個策略查看客觀統計。</p> :
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">{strategies.map(item => <article key={item.identity.key} className="space-y-4 rounded-xl border border-border bg-card p-4">
        <div><h3 className="font-semibold">{item.identity.strategy_name}</h3><p className="mt-1 text-xs text-muted-foreground">{item.identity.source} · {item.identity.version ?? '設定版本'} · {basis(item.identity.entry_basis)} · 樣本 {item.sample_count}</p>{STRATEGY_GUIDE[item.identity.strategy_id] && <p className="mt-2 text-xs text-foreground/80">{STRATEGY_GUIDE[item.identity.strategy_id].plain}</p>}</div>
        {HORIZONS.map(h => <div key={h}><h4 className="mb-2 text-sm font-medium">{h}</h4><Horizon stats={item.horizons[h]} /></div>)}
      </article>)}</div>}
  </section>
}

export function StrategyLab() {
  const [filters, setFilters] = useState<StrategyLabFilters>({ minimum_sample: 5 })
  const [compared, setCompared] = useState<string[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [offset, setOffset] = useState(0)
  const overview = useQuery({ queryKey: QK.strategyLab(filters), queryFn: () => api.strategyLab(filters), placeholderData: keepPreviousData })
  const detailFilters = { ...filters, strategy_key: selected ?? undefined }
  const detail = useQuery({
    queryKey: QK.strategyLabObservations(detailFilters, offset),
    queryFn: () => api.strategyLabObservations(detailFilters, offset), enabled: selected !== null,
  })
  const updateFilter = (key: keyof StrategyLabFilters, value: string | number) => {
    setFilters(previous => ({ ...previous, [key]: value === '' ? undefined : value }))
    setOffset(0); setSelected(null); setCompared([])
  }
  const data = overview.data
  const strategies = data?.strategies ?? []
  return <main className="mx-auto max-w-[1600px] space-y-6 px-4 py-6">
    <header className="space-y-2">
      <h1 className="flex items-center gap-2 text-2xl font-bold"><FlaskConical className="h-6 w-6 text-primary" />策略實驗室</h1>
      <p className="text-sm text-muted-foreground">Forward / OOS observed，僅使用保存的前瞻觀察，不是 historical backtest。</p>
      <p className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-sm">Historical PIT research：未完全可用。各策略保留原始進場基準；觀察報酬未扣成本與滑價，並非可成交的投資組合績效。</p>
    </header>
    <section aria-label="切片篩選" className="flex flex-wrap items-end gap-3 rounded-xl border border-border bg-card p-4">
      <label className="text-xs">Minimum sample<select aria-label="Minimum sample" value={filters.minimum_sample} onChange={e => updateFilter('minimum_sample', Number(e.target.value))} className="mt-1 block rounded border border-border bg-background p-2">{[5, 10, 20, 30, 50].map(n => <option key={n} value={n}>{n}</option>)}</select></label>
      <label className="max-w-72 text-xs">策略<select aria-label="策略篩選" value={filters.strategy_key ?? ''} onChange={e => updateFilter('strategy_key', e.target.value)} className="mt-1 block max-w-full rounded border border-border bg-background p-2"><option value="">全部策略</option>{data?.strategy_options.map(item => <option key={item.key} value={item.key}>{item.source} · {item.strategy_name} · {item.key.slice(0, 8)}</option>)}</select></label>
      {SLICES.map(([key, label]) => <label key={key} className="text-xs">{label}<select aria-label={label} value={filters[key] ?? ''} disabled={!data?.filter_options[key]?.length} onChange={e => updateFilter(key, e.target.value)} className="mt-1 block rounded border border-border bg-background p-2 disabled:opacity-50"><option value="">{data?.filter_options[key]?.length ? '全部' : '未保存可驗證背景'}</option>{data?.filter_options[key]?.map(value => <option key={value} value={value}>{value}</option>)}</select></label>)}
      <p className="w-full text-xs text-muted-foreground">每個 horizon 及切片各自檢查 N ≥ {filters.minimum_sample}；pending / unavailable 排除於命中率分母，未四捨五入 return &gt; 0 才算命中。超額報酬只計入同期 benchmark 可用的配對樣本，另列 N。</p>
    </section>
    {(overview.isLoading || overview.isPlaceholderData) && <p role="status">載入策略觀察…</p>}
    {overview.isError && <div role="alert">策略觀察目前無法讀取。<button className="ml-2 text-primary" onClick={() => overview.refetch()}>重試</button></div>}
    {data && !overview.isError && !overview.isPlaceholderData && <>
      <p className="text-sm text-muted-foreground">樣本 {data.sample_count}（每份快照的標的各計一次） · {strategies.length} 個策略身分 · 重複快照已排除 {data.duplicate_snapshots} · 重複觀察已排除 {data.duplicate_samples} · 衝突 {data.integrity_conflicts} · 非前瞻研究快照已排除 {data.excluded_research_snapshots}</p>
      {!strategies.length ? <p className="rounded-xl border border-dashed border-border p-8 text-center text-muted-foreground">尚無符合條件的前瞻觀察；尚未成熟或缺資料不會顯示為 0% 績效。</p> : <div className="overflow-x-auto rounded-xl border border-border bg-card"><table aria-label="策略總覽" className="w-full min-w-[1200px] text-left">
        <thead className="border-b border-border text-xs text-muted-foreground"><tr><th className="p-3">比較 / 策略 / 樣本數</th>{HORIZONS.map(h => <th key={h} className="p-3">{h}</th>)}</tr></thead>
        <tbody>{strategies.map(item => <tr key={item.identity.key} className="border-b border-border/50 align-top last:border-b-0"><td className="max-w-72 space-y-2 p-3">
          <label className="flex items-center gap-2 text-xs"><input type="checkbox" aria-label={`比較 ${item.identity.strategy_name} ${item.identity.key.slice(0, 8)}`} checked={compared.includes(item.identity.key)} disabled={compared.length >= 4 && !compared.includes(item.identity.key)} onChange={e => setCompared(previous => e.target.checked ? [...previous, item.identity.key] : previous.filter(key => key !== item.identity.key))} />加入比較</label>
          <button className="text-left font-semibold text-primary hover:underline" onClick={() => { setSelected(item.identity.key); setOffset(0) }}>{item.identity.strategy_name}</button>
          <p className="text-xs text-muted-foreground">{item.identity.source} · {item.identity.version ?? '設定版本'} · {item.identity.key.slice(0, 8)}</p>
          {STRATEGY_GUIDE[item.identity.strategy_id] && <p className="text-xs text-foreground/80">{STRATEGY_GUIDE[item.identity.strategy_id].plain}</p>}
          <p className="text-xs">樣本 {item.sample_count} · 快照 {item.snapshot_count} · {basis(item.identity.entry_basis)}</p>
          <p className="break-words text-xs text-muted-foreground">{item.identity.price_semantics.includes('pit_price') ? '公司行動價格正規化（非總報酬）' : '原始參考價（非總報酬）'}</p>
        </td>{HORIZONS.map(h => <td key={h} className="p-3"><Horizon stats={item.horizons[h]} /></td>)}</tr>)}</tbody>
      </table></div>}
      <Comparison strategies={strategies.filter(item => compared.includes(item.identity.key))} />
    </>}
    {selected && <section aria-label="策略觀察明細" className="space-y-3">
      <div className="flex items-center justify-between"><h2 className="font-semibold">Individual forward observations</h2><button className="text-sm text-primary" onClick={() => setSelected(null)}>關閉明細</button></div>
      {detail.isLoading && <p role="status">載入觀察明細…</p>}
      {detail.isError && <p role="alert">觀察明細目前無法讀取。</p>}
      {detail.data && <>
        <p className="text-xs text-muted-foreground">共 {detail.data.total} 筆 horizon 觀察。來源證據不足的快照僅供追查，全部 horizon 均列 unavailable。</p>
        {detail.data.total === 0 && <p>尚無個別觀察。</p>}
        <div className="overflow-x-auto rounded-xl border border-border bg-card"><table className="w-full min-w-[1200px] text-left text-xs"><thead className="text-muted-foreground"><tr>{['標的 / signal_date', 'Entry basis', 'Horizon / status', 'Return', 'Benchmark return', 'Excess', 'Strategy identity / provenance'].map(label => <th className="p-3" key={label}>{label}</th>)}</tr></thead><tbody>{detail.data.observations.map(row => <tr key={row.observation_id} className="border-t border-border/50 align-top">
          <td className="p-3"><Link className="text-primary" to={`/stocks/${encodeURIComponent(row.symbol)}`}>{row.name || row.symbol}</Link><p>{row.symbol}</p><p>{row.signal_date}</p></td>
          <td className="p-3">{basis(row.identity.entry_basis)}<p>{row.entry_date ?? '未確認'} · {row.entry_price ?? '不可用'}</p></td>
          <td className="p-3">{row.horizon}D · {row.status}<p className="text-muted-foreground">{row.reason}</p></td>
          <td className="p-3">{row.return_pct === null ? '不可用 / pending' : pct(row.return_pct)}</td>
          <td className="p-3">{row.benchmark_return_pct === null ? '不可用' : pct(row.benchmark_return_pct)}<p className="text-muted-foreground">{row.benchmark_symbol ?? row.benchmark_reason}</p></td>
          <td className="p-3">{row.excess_pct === null ? '不可用' : pct(row.excess_pct)}</td>
          <td className="max-w-sm space-y-1 p-3"><p>{row.identity.source} · {row.identity.strategy_id} · {row.identity.version ?? '未保存版本'}</p><p>{row.evidence_label}</p><ReviewLink row={row} /><details><summary className="cursor-pointer text-primary">來源證據</summary><dl className="mt-2 space-y-1 break-all"><dt>snapshot id</dt><dd>{row.snapshot_id}</dd><dt>as_of</dt><dd>{row.as_of}</dd><dt>outcome date</dt><dd>{row.outcome_date ?? '未確認'}</dd><dt>strategy identity</dt><dd>{row.identity.key}</dd><dt>provenance</dt><dd><pre className="whitespace-pre-wrap break-all">{JSON.stringify(row.provenance, null, 2)}</pre></dd></dl></details></td>
        </tr>)}</tbody></table></div>
        <div className="flex gap-3 text-sm"><button disabled={offset === 0} onClick={() => setOffset(previous => Math.max(0, previous - 50))}>上一頁</button><span>{detail.data.total === 0 ? '0 / 0' : `${offset + 1}–${Math.min(offset + 50, detail.data.total)} / ${detail.data.total}`}</span><button disabled={offset + 50 >= detail.data.total} onClick={() => setOffset(previous => previous + 50)}>下一頁</button></div>
      </>}
    </section>}
  </main>
}

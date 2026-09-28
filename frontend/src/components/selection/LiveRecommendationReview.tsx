import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Clock, ShieldCheck } from 'lucide-react'
import { api, type TaiwanLiveQuantHorizonSummary, type TaiwanLiveQuantRunSummary } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

const HORIZONS = ['1D', '5D', '20D'] as const

function pct(value: number | null | undefined) {
  return value == null ? '尚無樣本' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
}

function statusLabel(status: string | undefined) {
  if (status === 'formal_available') return '正式可用'
  if (status === 'available_zero_candidates') return '資料可用，0 檔候選'
  if (status === 'tracking') return '追蹤中'
  if (status === 'conflict') return '衝突，不作正式結果'
  return '資料不可用'
}

function HorizonCard({ label, summary }: { label: string; summary?: TaiwanLiveQuantHorizonSummary }) {
  return <section className="rounded-lg border border-border/60 bg-card p-3 text-xs">
    <h3 className="font-semibold">{label}</h3>
    {!summary || summary.evaluated_count === 0 ? <p className="mt-2 text-muted-foreground">尚無可評估樣本，追蹤中 {summary?.pending_count ?? 0} 檔，不可評估 {summary?.unavailable_count ?? 0} 檔</p> : <dl className="mt-2 grid grid-cols-2 gap-y-1">
      <dt className="text-muted-foreground">命中率／N</dt><dd className="text-right">{summary.hit_rate_pct == null ? '尚無樣本' : `${summary.hit_rate_pct.toFixed(1)}%`}／{summary.evaluated_count}</dd>
      <dt className="text-muted-foreground">平均報酬</dt><dd className="text-right">{pct(summary.average_return_pct)}</dd>
      <dt className="text-muted-foreground">追蹤中／不可評估</dt><dd className="text-right">{summary.pending_count}／{summary.unavailable_count}</dd>
    </dl>}
  </section>
}

export function LiveRecommendationReview() {
  const modelsQuery = useQuery({ queryKey: QK.taiwanQuantLiveModels, queryFn: api.taiwanQuantLiveModels })
  const runsQuery = useQuery({ queryKey: QK.taiwanQuantLiveRuns, queryFn: () => api.taiwanQuantLiveRuns(30) })
  const runs = useMemo(() => runsQuery.data?.runs ?? [], [runsQuery.data?.runs])
  const [selected, setSelected] = useState<TaiwanLiveQuantRunSummary | null>(null)
  useEffect(() => {
    if (!selected && runs.length) setSelected(runs[0])
  }, [runs, selected])
  const detailQuery = useQuery({
    queryKey: QK.taiwanQuantLiveRun(selected?.model_key ?? '', selected?.session ?? ''),
    queryFn: () => api.taiwanQuantLiveRun(selected!.model_key, selected!.session),
    enabled: Boolean(selected),
  })
  const outcomes = useMemo(() => new Map((detailQuery.data?.outcomes ?? []).map(item => [`${item.symbol}:${item.horizon}`, item])), [detailQuery.data])

  if (runsQuery.isLoading) return <p className="py-8 text-center text-sm text-muted-foreground">載入每日推薦歷史…</p>
  if (runsQuery.isError) return <div role="alert" className="rounded-lg border border-destructive/30 p-4 text-sm">每日推薦歷史目前無法讀取。</div>
  const currentStatus = modelsQuery.data?.recommendation_status
  const currentReason = modelsQuery.data?.recommendation_reason ?? modelsQuery.data?.current_run_reason
  return <section aria-label="每日推薦與命中率" className="space-y-4">
    <div className="rounded-xl border border-primary/25 bg-card p-4">
      <div className="flex flex-wrap items-center gap-2"><ShieldCheck className="h-4 w-4 text-primary" /><h2 className="font-semibold">每日推薦與命中率</h2><span className="text-xs text-muted-foreground">實驗性初測，不構成投資建議</span></div>
      {currentStatus && currentStatus !== 'formal_available' && currentStatus !== 'tracking' ? <p className="mt-3 text-sm text-muted-foreground">目前狀態：{statusLabel(currentStatus)}{currentReason ? `（${currentReason}）` : ''}</p> : null}
      {!runs.length ? <p className="mt-4 text-sm text-muted-foreground">尚無正式推薦快照；資料不足時不以歷史結果代替。</p> : <div className="mt-4 grid gap-2 md:grid-cols-3 lg:grid-cols-5">{runs.map(run => <button key={`${run.model_key}:${run.session}`} type="button" onClick={() => setSelected(run)} className={`rounded-lg border p-3 text-left text-xs ${selected?.session === run.session && selected?.model_key === run.model_key ? 'border-primary bg-primary/5' : 'border-border/60'}`}><span className="font-semibold">{run.session}</span><span className="mt-1 block text-muted-foreground">{statusLabel(run.recommendation_status)} · {run.signal_count} 檔</span></button>)}</div>}
    </div>
    {selected && detailQuery.isLoading ? <p className="py-8 text-center text-sm text-muted-foreground">載入快照詳情…</p> : null}
    {selected && detailQuery.isError ? <div role="alert" className="rounded-lg border border-destructive/30 p-4 text-sm">每日推薦快照詳情目前無法讀取。</div> : null}
    {selected && detailQuery.data ? <>
      <div className="rounded-xl border border-border/60 bg-card p-4"><div className="flex flex-wrap items-center gap-2"><h2 className="font-semibold">{selected.session} · {statusLabel(detailQuery.data.recommendation_status)}</h2><span className="text-xs text-muted-foreground">資料截止 {detailQuery.data.snapshot.data_cutoff ?? '未保存'}</span></div><p className="mt-1 text-xs text-muted-foreground">快照版本 {detailQuery.data.snapshot.model?.version ?? '既有快照未保存'} · {detailQuery.data.recommendation_reason ?? 'current'}</p><div className="mt-3 grid gap-3 md:grid-cols-3">{HORIZONS.map(horizon => <HorizonCard key={horizon} label={horizon} summary={detailQuery.data.outcome_summary?.[horizon]} />)}</div></div>
      <div className="overflow-x-auto rounded-xl border border-border/60 bg-card"><table className="w-full min-w-[760px] text-left text-xs"><thead className="text-muted-foreground"><tr><th className="px-3 py-2">排名／標的</th><th className="px-3 py-2">快照基準價</th><th className="px-3 py-2">分數</th><th className="px-3 py-2">入選原因</th>{HORIZONS.map(h => <th key={h} className="px-3 py-2">{h}</th>)}</tr></thead><tbody>{detailQuery.data.snapshot.signals.slice(0, 10).map(signal => <tr key={signal.symbol} className="border-t border-border/60"><td className="px-3 py-2"><Link className="text-primary hover:underline" to={`/stocks/${encodeURIComponent(signal.symbol)}`}>#{signal.rank} {signal.name || signal.symbol}</Link><span className="ml-1 text-muted-foreground">{signal.symbol}</span></td><td className="px-3 py-2 font-mono">{signal.reference_close.toFixed(2)}</td><td className="px-3 py-2 font-mono">{(signal.score * 100).toFixed(2)}%</td><td className="px-3 py-2 text-muted-foreground">{signal.reason_summary || '既有動能選取條件'}</td>{HORIZONS.map(h => { const outcome = outcomes.get(`${signal.symbol}:${Number.parseInt(h, 10)}`); return <td key={h} className="px-3 py-2">{outcome?.status === 'verified' ? pct((outcome.value ?? 0) * 100) : outcome?.status === 'data_insufficient' || outcome?.status === 'conflict' ? `不可評估（${outcome.reason || '資料不足'}）` : <span className="inline-flex items-center gap-1 text-muted-foreground"><Clock className="h-3 w-3" />追蹤中</span>}</td> })}</tr>)}</tbody></table></div>
    </> : null}
  </section>
}

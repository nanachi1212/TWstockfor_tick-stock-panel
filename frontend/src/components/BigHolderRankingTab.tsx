import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { signed, toneClass } from '@/components/ChipRankingTab'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import type { BigHolderRow, BigHolderTone } from '@/lib/marketResearch'

const VIEWS = [
  { id: 'concentrating', label: '籌碼集中' },
  { id: 'dispersing', label: '籌碼渙散' },
  { id: 'watchlist', label: '自選' },
] as const
type View = typeof VIEWS[number]['id']

const TONES: Record<BigHolderTone, { label: string; className: string }> = {
  concentrating: { label: '籌碼集中', className: 'text-bull' },
  dispersing: { label: '籌碼渙散', className: 'text-bear' },
  flat: { label: '大致持平', className: '' },
  insufficient: { label: '資料累積中', className: 'text-muted' },
}

const count = (v: number) => v.toLocaleString('zh-TW')

export function BigHolderRankingTab() {
  const [view, setView] = useState<View>('concentrating')
  const query = useQuery({ queryKey: QK.bigHolderRanking, queryFn: api.taiwanBigHolderRanking, staleTime: 5 * 60_000 })
  const inputClass = 'rounded-md border border-border bg-surface px-3 py-2 text-sm'
  if (query.isLoading) return <p role="status" className="py-8 text-muted">讀取集保股權分散資料中…</p>
  if (query.isError || !query.data) return <div role="alert" className="rounded-lg border border-danger/30 p-4 text-danger">大戶集中度讀取失敗<button className="ml-3 underline" onClick={() => void query.refetch()}>重試</button></div>
  const data = query.data
  if (!data.date) return <p className="py-8 text-muted">尚無集保股權分散資料。每週集保資料更新後會開始累積。</p>
  const hasBase = Boolean(data.previous_1w_date || data.previous_4w_date)
  const empty = view === 'watchlist' ? '自選股中沒有在範圍內的股票。'
    : hasBase ? '沒有符合條件的股票。' : '還沒有可比較的前一週資料，下次集保資料更新後才有排行。'
  return <div className="space-y-4">
    <div role="tablist" aria-label="大戶集中度清單" className="flex flex-wrap gap-1">
      {VIEWS.map(v => <button key={v.id} role="tab" aria-selected={view === v.id} onClick={() => setView(v.id)} className={`${inputClass} ${view === v.id ? 'bg-accent/15 text-accent' : ''}`}>{v.label} {data[v.id].length}</button>)}
    </div>
    <p className="text-xs text-muted leading-relaxed">
      集保資料週 {data.date}，比較基準：前 1 週 {data.previous_1w_date ?? '尚未累積'}、前 4 週 {data.previous_4w_date ?? '尚未累積'}。
      範圍：上市櫃普通股、股東人數 ≥ {count(data.min_holders)} 人，共 {count(data.universe_count)} 檔。
      千張大戶是持股超過 1,000 張的股東，變化單位為百分點（pp）。
      4 週增加 ≥ {data.threshold_pp} pp 且股東人數沒有增加，判讀為籌碼集中；減少 ≥ {data.threshold_pp} pp 且股東人數沒有減少，判讀為籌碼渙散。
      {!data.previous_4w_date && '4 週變化資料累積中，目前依 1 週變化排序。'}
      前一週沒有該股票資料時顯示「—」，不當成 0。只提供最新一週。這是籌碼變化的觀察，不是買賣訊號。
    </p>
    <BigHolderTable rows={data[view]} empty={empty} />
  </div>
}

function BigHolderTable({ rows, empty }: { rows: BigHolderRow[]; empty: string }) {
  if (!rows.length) return <p className="text-xs text-muted">{empty}</p>
  return <div className="overflow-x-auto rounded-lg border border-border"><table className="w-full min-w-[720px] text-sm">
    <thead className="bg-elevated text-xs text-muted"><tr>
      <th className="px-2 py-2 text-left">#</th><th className="px-2 py-2 text-left">股票</th><th className="px-2 py-2 text-right">千張大戶</th>
      <th className="px-2 py-2 text-right">1 週變化（pp）</th><th className="px-2 py-2 text-right">4 週變化（pp）</th>
      <th className="px-2 py-2 text-right">股東人數</th><th className="px-2 py-2 text-right">人數 4 週變化</th><th className="px-2 py-2 text-left">判讀</th>
    </tr></thead>
    <tbody>{rows.map((r, i) => <tr key={r.symbol} className="border-t border-border/60">
      <td className="px-2 py-1.5 text-muted">{i + 1}</td>
      <td className="px-2 py-1.5"><Link to={`/stocks/${r.symbol}`} className="hover:underline">{r.symbol.split('.')[0]} {r.name}</Link>{r.in_watchlist && <span className="ml-1 rounded bg-accent/15 px-1 text-[10px] text-accent">自選</span>}</td>
      <td className="px-2 py-1.5 text-right tabular-nums">{r.big_holder_pct.toFixed(1)}%</td>
      <td className={`px-2 py-1.5 text-right tabular-nums ${toneClass(r.change_1w_pp)}`} title={`股東人數 1 週變化 ${signed(r.holders_change_1w_pct, 1, '%')}`}>{signed(r.change_1w_pp)}</td>
      <td className={`px-2 py-1.5 text-right tabular-nums ${toneClass(r.change_4w_pp)}`}>{signed(r.change_4w_pp)}</td>
      <td className="px-2 py-1.5 text-right tabular-nums">{count(r.total_holders)}</td>
      <td className="px-2 py-1.5 text-right tabular-nums">{signed(r.holders_change_4w_pct, 1, '%')}</td>
      <td className={`px-2 py-1.5 ${TONES[r.tone].className}`}>{TONES[r.tone].label}</td>
    </tr>)}</tbody>
  </table></div>
}

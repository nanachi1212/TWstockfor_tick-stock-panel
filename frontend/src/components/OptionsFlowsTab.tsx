import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { toneClass } from '@/components/ChipRankingTab'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import type { ForeignPositions, OptionsContract } from '@/lib/marketResearch'

const lots = (v: number | null | undefined) => v == null ? '—' : v.toLocaleString('zh-TW')
const change = (v: number | null | undefined) => v == null ? '—' : `${v > 0 ? '+' : ''}${v.toLocaleString('zh-TW')}`
const contractLabel = (c: string) => c.length === 6 ? `${c.slice(0, 4)}/${c.slice(4)} 月選` : `${c.slice(0, 4)}/${c.slice(4, 6)} 週選 ${c.slice(6)}`
// Strikes far from the action carry almost no open interest; hide those under this share of the peak.
const MIN_SHARE_OF_PEAK = 0.05

export function OptionsFlowsTab() {
  const query = useQuery({ queryKey: QK.optionsFlows, queryFn: api.taiwanOptionsFlows, staleTime: 5 * 60_000 })
  const [picked, setPicked] = useState<string | null>(null)
  const inputClass = 'rounded-md border border-border bg-surface px-3 py-2 text-sm'
  if (query.isLoading) return <p role="status" className="py-8 text-muted">讀取期交所選擇權資料中…</p>
  if (query.isError || !query.data) return <div role="alert" className="rounded-lg border border-danger/30 p-4 text-danger">期權籌碼讀取失敗<button className="ml-3 underline" onClick={() => void query.refetch()}>重試</button></div>
  const data = query.data
  // A picked contract that expired out of a refreshed snapshot falls back to the default.
  const contract = data.contracts.find(c => c.contract === picked) ?? data.contracts.find(c => c.contract === data.default_contract)
  return <div className="space-y-4">
    {data.foreign && <ForeignPanel foreign={data.foreign} />}
    {data.status !== 'available' || !contract ? <p className="text-sm text-muted">{data.message ?? '尚無選擇權未平倉資料。'}</p> : <>
      <div className="flex flex-wrap items-center gap-3">
        <label className="text-sm">到期 <select aria-label="選擇權到期" className={inputClass} value={contract.contract} onChange={e => setPicked(e.target.value)}>
          {data.contracts.map(c => <option key={c.contract} value={c.contract}>{contractLabel(c.contract)}（{lots(c.total_oi)} 口）</option>)}
        </select></label>
        <span className="text-sm">買權最大未平倉 <b className="text-bull tabular-nums">{lots(contract.call_peak)}</b></span>
        <span className="text-sm">賣權最大未平倉 <b className="text-bear tabular-nums">{lots(contract.put_peak)}</b></span>
      </div>
      <p className="text-xs text-muted leading-relaxed">
        台指選擇權一般盤收盤後未平倉量（口），資料日 {data.date}，來源：期交所 OpenAPI 選擇權每日交易行情。
        增減和前一個有存檔的交易日 {data.previous_date ?? '（尚未累積）'} 比較；期交所只提供最新一天，看板沒開的日子無法回補，沒有前一日資料時顯示「—」。
        只列未平倉達該到期最大值 {MIN_SHARE_OF_PEAK * 100}% 以上的履約價。這是籌碼分布的觀察，不是買賣訊號。
        {data.message && <span className="text-warning"> {data.message}，目前顯示已存的最新資料。</span>}
      </p>
      <StrikeTable contract={contract} />
    </>}
  </div>
}

function ForeignPanel({ foreign }: { foreign: ForeignPositions }) {
  const f = foreign.futures
  const o = foreign.options
  return <div className="grid gap-3 md:grid-cols-2">
    <div className="rounded-lg border border-border p-3">
      <h3 className="text-sm font-medium">外資台指期淨未平倉</h3>
      {f ? <p className="mt-1 text-sm tabular-nums">
        <b className={`text-lg ${toneClass(f.net_oi)}`}>{change(f.net_oi)} 口</b>
        <span className={`ml-2 ${toneClass(f.net_oi_change)}`}>較前日 {change(f.net_oi_change)}</span>
        <span className="block text-xs text-muted">多單 {lots(f.long_oi)}／空單 {lots(f.short_oi)}，{String(f.date).slice(0, 10)}</span>
      </p> : <p className="mt-1 text-xs text-muted">{foreign.futures_message ?? '暫無資料'}</p>}
    </div>
    <div className="rounded-lg border border-border p-3">
      <h3 className="text-sm font-medium">外資台指選擇權淨部位</h3>
      {o ? <p className="mt-1 text-sm tabular-nums">
        買權 <b>{change(o.call_net_oi)}</b> 口，賣權 <b>{change(o.put_net_oi)}</b> 口
        <span className="block text-xs text-muted">淨部位＝買方未平倉－賣方未平倉，{String(o.date).slice(0, 10)}</span>
      </p> : <p className="mt-1 text-xs text-muted">{foreign.options_message ?? '暫無資料'}</p>}
    </div>
  </div>
}

function StrikeTable({ contract }: { contract: OptionsContract }) {
  const peak = Math.max(1, ...contract.strikes.map(r => Math.max(r.call ?? 0, r.put ?? 0)))
  const rows = contract.strikes.filter(r => Math.max(r.call ?? 0, r.put ?? 0) >= peak * MIN_SHARE_OF_PEAK)
  const bar = (v: number | null, cls: string, align: string) => <div className={`flex ${align}`}><div className={`h-3 rounded-sm ${cls}`} style={{ width: `${((v ?? 0) / peak) * 100}%` }} /></div>
  return <div className="overflow-x-auto rounded-lg border border-border"><table className="w-full text-sm md:min-w-[720px]">
    <thead className="bg-elevated text-xs text-muted"><tr>
      <th className="px-2 py-2 text-right">買權增減</th><th className="px-2 py-2 text-right">買權未平倉</th><th className="w-1/4 px-2 py-2" />
      <th className="px-2 py-2 text-center">履約價</th>
      <th className="w-1/4 px-2 py-2" /><th className="px-2 py-2 text-left">賣權未平倉</th><th className="px-2 py-2 text-left">賣權增減</th>
    </tr></thead>
    <tbody>{rows.map(r => <tr key={r.strike} className="border-t border-border/60 tabular-nums">
      <td className={`px-2 py-1 text-right ${toneClass(r.call_change)}`}>{change(r.call_change)}</td>
      <td className="px-2 py-1 text-right">{lots(r.call)}</td>
      <td className="px-2 py-1">{bar(r.call, 'bg-bull/70', 'justify-end')}</td>
      <td className={`px-2 py-1 text-center font-medium ${r.strike === contract.call_peak || r.strike === contract.put_peak ? 'text-accent' : ''}`}>{lots(r.strike)}</td>
      <td className="px-2 py-1">{bar(r.put, 'bg-bear/70', 'justify-start')}</td>
      <td className="px-2 py-1 text-left">{lots(r.put)}</td>
      <td className={`px-2 py-1 text-left ${toneClass(r.put_change)}`}>{change(r.put_change)}</td>
    </tr>)}</tbody>
  </table></div>
}

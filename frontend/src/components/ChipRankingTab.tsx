import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import type { ChipRankingItem } from '@/lib/marketResearch'

const COMPONENT_LABELS: Record<string, string> = {
  flow_today: '法人當日買超÷5日均量',
  flow_5d: '法人5日累計買超÷5日量',
  streak: '外資＋投信連買天數',
  big_holder_change: '千張大戶週增減',
  margin_drop: '融資減少',
}

const lots = (shares: number | null | undefined) => shares == null ? '—' : Math.round(shares / 1000).toLocaleString('zh-TW')
const signed = (v: number | null | undefined, digits = 2, suffix = '') => v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}${suffix}`
const toneClass = (v: number | null | undefined) => v == null || v === 0 ? '' : v > 0 ? 'text-bull' : 'text-bear'

export function ChipRankingTab({ date }: { date: string }) {
  const [exchange, setExchange] = useState<'TWSE' | 'TPEX'>('TWSE')
  const query = useQuery({ queryKey: QK.chipRanking(date), queryFn: () => api.taiwanChipRanking(date || undefined), staleTime: 5 * 60_000 })
  const inputClass = 'rounded-md border border-border bg-surface px-3 py-2 text-sm'
  if (query.isLoading) return <p role="status" className="py-8 text-muted">計算籌碼分數中…</p>
  if (query.isError || !query.data) return <div role="alert" className="rounded-lg border border-danger/30 p-4 text-danger">籌碼排行讀取失敗<button className="ml-3 underline" onClick={() => void query.refetch()}>重試</button></div>
  const data = query.data
  const rows = data[exchange]
  return <div className="space-y-4">
    <div className="flex flex-wrap items-center gap-3">
      <div role="tablist" aria-label="交易所" className="flex gap-1">
        {(['TWSE', 'TPEX'] as const).map(id => <button key={id} role="tab" aria-selected={exchange === id} onClick={() => setExchange(id)} className={`${inputClass} ${exchange === id ? 'bg-accent/15 text-accent' : ''}`}>{id === 'TWSE' ? '上市' : '上櫃'} 前 {data[id].length}</button>)}
      </div>
      <span className="text-xs text-muted">交易日 {data.date}{data.big_holder_date && ` · 千張大戶資料週 ${data.big_holder_date}`}</span>
    </div>
    <p className="text-xs text-muted leading-relaxed">
      條件：普通股、5 日均量 ≥ {lots(data.min_avg_volume_5d)} 張、股價 ≥ {data.min_close} 元、當日三大法人合計買超。
      分數 0–100，上市與上櫃各自排名；權重：{Object.entries(data.weights).map(([k, w]) => `${COMPONENT_LABELS[k] ?? k} ${w}`).join('、')}。
      缺少的項目不計分並重新分配權重{data.big_holder_previous_date ? '' : '（千張大戶需累積兩週資料才有週增減，目前不計入）'}。這是排序參考，沒有回測驗證。
    </p>
    <ChipTable rows={rows} />
    <h2 className="font-medium">隔日沖賣壓（推估）</h2>
    <p className="text-xs text-muted">當日漲 ≥ 5%、量 ≥ 前 4 日均量 2 倍、收在最高價 1% 內，而且三大法人沒有買超。隔天可能出現短線賣壓。</p>
    {data.day_trade_pressure.length ? <div className="overflow-x-auto rounded-lg border border-border"><table className="w-full text-sm">
      <thead className="bg-elevated text-xs text-muted"><tr><th className="px-2 py-2 text-left">股票</th><th className="px-2 py-2 text-right">收盤</th><th className="px-2 py-2 text-right">漲幅</th><th className="px-2 py-2 text-right">量倍數</th><th className="px-2 py-2 text-right">法人買賣超（張）</th></tr></thead>
      <tbody>{data.day_trade_pressure.map(r => <tr key={r.symbol} className="border-t border-border/60">
        <td className="px-2 py-1.5"><Link to={`/stocks/${r.symbol}`} className="hover:underline">{r.symbol.split('.')[0]} {r.name}</Link></td>
        <td className="px-2 py-1.5 text-right tabular-nums">{r.close}</td>
        <td className="px-2 py-1.5 text-right tabular-nums text-bull">{signed(r.change_pct * 100, 1, '%')}</td>
        <td className="px-2 py-1.5 text-right tabular-nums">{r.volume_multiple.toFixed(1)}×</td>
        <td className={`px-2 py-1.5 text-right tabular-nums ${toneClass(r.institutional_net)}`}>{lots(r.institutional_net)}</td>
      </tr>)}</tbody>
    </table></div> : <p className="text-xs text-muted">今天沒有符合條件的股票。</p>}
  </div>
}

function ChipTable({ rows }: { rows: ChipRankingItem[] }) {
  if (!rows.length) return <p className="text-xs text-muted">今天沒有符合條件的股票。</p>
  return <div className="overflow-x-auto rounded-lg border border-border"><table className="w-full text-sm">
    <thead className="bg-elevated text-xs text-muted"><tr>
      <th className="px-2 py-2 text-left">#</th><th className="px-2 py-2 text-left">股票</th><th className="px-2 py-2 text-right">分數</th>
      <th className="px-2 py-2 text-right">收盤</th><th className="px-2 py-2 text-right">法人當日（張）</th><th className="px-2 py-2 text-right">法人5日（張）</th>
      <th className="px-2 py-2 text-right">外資/投信連買</th><th className="px-2 py-2 text-right">融資增減（張）</th><th className="px-2 py-2 text-right">千張大戶</th>
    </tr></thead>
    <tbody>{rows.map((r, i) => <tr key={r.symbol} className="border-t border-border/60" title={`計分項目：${r.components.map(c => COMPONENT_LABELS[c] ?? c).join('、')}`}>
      <td className="px-2 py-1.5 text-muted">{i + 1}</td>
      <td className="px-2 py-1.5"><Link to={`/stocks/${r.symbol}`} className="hover:underline">{r.symbol.split('.')[0]} {r.name}</Link></td>
      <td className="px-2 py-1.5 text-right font-semibold tabular-nums">{r.chip_score.toFixed(1)}</td>
      <td className="px-2 py-1.5 text-right tabular-nums">{r.close}</td>
      <td className={`px-2 py-1.5 text-right tabular-nums ${toneClass(r.institutional_net)}`}>{lots(r.institutional_net)}</td>
      <td className={`px-2 py-1.5 text-right tabular-nums ${toneClass(r.institutional_net_5d)}`}>{lots(r.institutional_net_5d)}</td>
      <td className="px-2 py-1.5 text-right tabular-nums">{r.foreign_buy_streak ?? '—'} / {r.trust_buy_streak ?? '—'}</td>
      <td className={`px-2 py-1.5 text-right tabular-nums ${toneClass(r.margin_change)}`}>{lots(r.margin_change)}</td>
      <td className="px-2 py-1.5 text-right tabular-nums">{r.big_holder_pct == null ? '—' : `${r.big_holder_pct.toFixed(1)}%`}{r.big_holder_change != null && <span className={`ml-1 text-xs ${toneClass(r.big_holder_change)}`}>{signed(r.big_holder_change)}</span>}</td>
    </tr>)}</tbody>
  </table></div>
}

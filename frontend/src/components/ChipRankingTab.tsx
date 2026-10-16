import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import type { ChipRankingItem, ChipRankingTracking, ChipTrackingDay, ChipTrackingHorizon } from '@/lib/marketResearch'

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

export function ChipRankingTab() {
  const [exchange, setExchange] = useState<'TWSE' | 'TPEX'>('TWSE')
  const query = useQuery({ queryKey: QK.chipRanking, queryFn: api.taiwanChipRanking, staleTime: 5 * 60_000 })
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
      <span className="text-xs text-muted">最新已入庫交易日 {data.date}{data.big_holder_date && ` · 千張大戶資料週 ${data.big_holder_date}`}</span>
    </div>
    <p className="text-xs text-muted leading-relaxed">
      條件：普通股、5 日均量 ≥ {lots(data.min_avg_volume_5d)} 張、股價 ≥ {data.min_close} 元、當日三大法人合計買超。
      分數 0–100，上市與上櫃各自排名；權重：{Object.entries(data.weights).map(([k, w]) => `${COMPONENT_LABELS[k] ?? k} ${w}`).join('、')}。
      缺少的項目不計分並重新分配權重{data.big_holder_previous_date ? '' : '（千張大戶需累積兩週資料才有週增減，目前不計入）'}。只提供最新一天，不提供過去日期：集保資料沒有記錄當時的公布時間，股票池也是現在仍上市的股票，回看過去會失真。這是排序參考，沒有回測驗證，不能證明比較準。
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
    <ChipTracking />
  </div>
}

const pct = (v: number | null | undefined) => signed(v == null ? null : v * 100, 2, '%')
const rate = (v: number | null | undefined) => v == null ? '—' : `${(v * 100).toFixed(0)}%`
const DAY_STATUS: Record<ChipTrackingDay['status'], string> = { tracked: '追蹤中', late: '存檔太晚，不計入', pending: '等待隔日開盤' }

const UNAVAILABLE_REASON: Record<string, string> = {
  incomplete_picks: '有股票缺價格',
  price_data: '缺價格',
  corporate_action_coverage: '除權息資料不足',
  benchmark_unavailable: '0050 缺價格',
}

function horizonCell(h: ChipTrackingHorizon) {
  if (h.status === 'pending') return <span className="text-muted">尚未到期</span>
  if (h.status === 'unavailable') return <span className="text-muted" title={h.reason}>無法計算{h.reason ? `（${UNAVAILABLE_REASON[h.reason] ?? h.reason}）` : ''}</span>
  return <><span className={toneClass(h.avg_return)}>{pct(h.avg_return)}</span><span className="ml-1 text-xs text-muted">{h.status === 'partial' ? UNAVAILABLE_REASON[h.reason ?? ''] ?? h.reason ?? '0050 無法計算' : `0050 ${pct(h.benchmark_return)}`}</span></>
}

function ChipTracking() {
  const query = useQuery({ queryKey: QK.chipRankingTracking, queryFn: api.taiwanChipRankingTracking, staleTime: 5 * 60_000 })
  return <section className="space-y-3 border-t border-border pt-4">
    <h2 className="font-medium">排行追蹤成績（往後追蹤）</h2>
    <p className="text-xs text-muted leading-relaxed">
      每天收盤更新後，把當天上市、上櫃各前 20 名存檔，之後不再重算。以「隔天開盤價買進」計算 1 天（當天收盤）與 5 天（第 5 個交易日收盤）的還原價漲跌。未計手續費與交易稅。只在融資資料公布後存檔；存檔時間晚於隔天開盤或任何一檔缺價格，該期間不計入平均。0050 基準使用同期間的還原價報酬；基準缺價或還原資料不足時，保留個股報酬，該期間不計入彙總。平均報酬、超額報酬與勝過 0050 比例使用同一批完整樣本，累積滿 20 個交易日才顯示。
    </p>
    {query.isLoading ? <p role="status" className="text-xs text-muted">讀取追蹤紀錄中…</p>
      : query.isError || !query.data ? <div role="alert" className="text-xs text-danger">追蹤紀錄讀取失敗<button className="ml-3 underline" onClick={() => void query.refetch()}>重試</button></div>
      : !query.data.days.length ? <p className="text-xs text-muted">還沒有存檔紀錄。下一次收盤更新完成後會開始累積。</p>
      : <ChipTrackingBody data={query.data} />}
  </section>
}

function ChipTrackingBody({ data }: { data: ChipRankingTracking }) {
  const horizons = [['h1', '持有 1 天'], ['h5', '持有 5 天']] as const
  return <>
    <div className="grid gap-3 sm:grid-cols-2">
      {horizons.map(([key, label]) => {
        const s = data.summary[key]
        return <div key={key} className="rounded-lg border border-border p-3 text-sm">
          <div className="font-medium">{label}</div>
          {s.sample_sufficient ? <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
            <dt className="text-muted">平均報酬</dt><dd className={`text-right tabular-nums ${toneClass(s.avg_return)}`}>{pct(s.avg_return)}</dd>
            <dt className="text-muted">0050 同期</dt><dd className="text-right tabular-nums">{pct(s.avg_benchmark)}</dd>
            <dt className="text-muted">平均超額報酬</dt><dd className={`text-right tabular-nums ${toneClass(s.avg_excess)}`}>{pct(s.avg_excess)}</dd>
            <dt className="text-muted">上漲比例</dt><dd className="text-right tabular-nums">{rate(s.win_rate)}</dd>
            <dt className="text-muted">贏過 0050 比例</dt><dd className="text-right tabular-nums">{rate(s.beat_rate)}</dd>
            <dt className="text-muted">樣本</dt><dd className="text-right tabular-nums">{s.days} 天 / {s.picks} 檔次</dd>
          </dl> : <p className="mt-2 text-xs text-muted">已累積 {s.days} / {data.min_days} 個交易日，樣本不足，先不顯示平均。</p>}
        </div>
      })}
    </div>
    <div className="overflow-x-auto rounded-lg border border-border"><table className="w-full text-sm">
      <thead className="bg-elevated text-xs text-muted"><tr>
        <th className="px-2 py-2 text-left">排行日</th><th className="px-2 py-2 text-left">狀態</th><th className="px-2 py-2 text-right">檔數</th>
        <th className="px-2 py-2 text-right">1 天平均</th><th className="px-2 py-2 text-right">5 天平均</th>
      </tr></thead>
      <tbody>{data.days.slice(0, 20).map(d => <tr key={d.date} className="border-t border-border/60">
        <td className="px-2 py-1.5 tabular-nums">{d.date}</td>
        <td className="px-2 py-1.5 text-xs">{DAY_STATUS[d.status]}</td>
        <td className="px-2 py-1.5 text-right tabular-nums"><details><summary className="cursor-pointer">{d.picks.length} 檔／查看存檔</summary><p className="text-xs text-muted">存檔 {d.recorded_at}；進場 {d.entry_date ?? '未確認'}</p><ul className="space-y-1 text-left text-xs">{d.picks.map(p => <li key={p.symbol}>#{p.rank} <Link className="text-accent hover:underline" to={`/stocks/${encodeURIComponent(p.symbol)}`}>{p.name || p.symbol}</Link> · 1 天 {pct(p.return_1)}／5 天 {pct(p.return_5)} · <Link className="text-accent hover:underline" to={`/research?symbol=${encodeURIComponent(p.symbol)}`}>研究歷史</Link></li>)}</ul></details></td>
        <td className="px-2 py-1.5 text-right tabular-nums">{d.status === 'tracked' ? horizonCell(d.h1) : '—'}</td>
        <td className="px-2 py-1.5 text-right tabular-nums">{d.status === 'tracked' ? horizonCell(d.h5) : '—'}</td>
      </tr>)}</tbody>
    </table></div>
  </>
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

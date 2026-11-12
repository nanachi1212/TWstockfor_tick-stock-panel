import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import type { VcpExecutionState, VcpScanItem } from '@/lib/marketResearch'

const HEADERS = ['股票', '評級', '執行狀態', '樞紐（突破價）', '停損', '距樞紐', 'RS', '收縮', '行動']
const STATE_LABELS: Record<VcpExecutionState, string> = {
  'Pre-breakout': '突破前',
  Breakout: '放量突破',
  'Early-post-breakout': '剛突破',
  Extended: '已延伸',
  Overextended: '過度延伸',
  Damaged: '型態受損',
  Invalid: '非上升趨勢',
}
const RATING_LABELS: Record<string, string> = {
  'Textbook VCP': '教科書級', 'Strong VCP': '強', 'Good VCP': '良好',
  'Developing VCP': '發展中', 'Weak VCP': '偏弱', 'No VCP': '不成立',
}
const TREND_LABELS: Record<string, string> = {
  c1_price_above_sma150_200: '股價高於 150、200 日均線',
  c2_sma150_above_sma200: '150 日均線高於 200 日均線',
  c3_sma200_trending_up: '200 日均線比 22 日前高',
  c4_price_above_sma50: '股價高於 50 日均線',
  c5_25pct_above_52w_low: '比 52 週低點高 25% 以上',
  c6_within_25pct_52w_high: '距 52 週高點 25% 以內',
  c7_rs_rank_above_70: 'RS 百分位高於 70',
}
const EXCLUDED_LABELS: Record<string, string> = {
  insufficient_history: '有成交日不足 260 日',
  missing_target_session: '目標日無資料',
  incomplete_sessions: '中間缺交易日資料',
  no_trade_on_target: '目標日無成交',
  invalid_ohlc: '價格資料異常',
  corporate_action_unverified: '除權息未驗證',
  share_count_event_in_volume_window: '近 120 日有股數變動（量能不可比）',
  insufficient_data: '資料不足',
  invalid_data: '資料異常',
  data_unverified: 'RS 無法計算',
}
const REASON_LABELS: Record<string, string> = {
  no_daily_data: '尚無日資料',
  corporate_action_coverage_unavailable: '除權息資料未驗證，無法還原價格，暫不提供 VCP',
}

const price = (value: number | null) => value == null ? '—' : value.toLocaleString('zh-TW', { maximumFractionDigits: 2 })
const pct = (value: number | null) => value == null ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(1)}%`

function action(item: VcpScanItem): string {
  if (item.signal) return '符合進場條件，仍需事件檢查'
  switch (item.execution_state) {
    case 'Pre-breakout': return '等待放量突破樞紐'
    case 'Breakout': return '已突破，但未通過其他條件'
    case 'Damaged':
    case 'Invalid': return '型態失效，不進場'
    default: return '已離開買點，不追價'
  }
}

function VcpRow({ item }: { item: VcpScanItem }) {
  return <tbody className="border-t border-border">
    <tr>
      <td className="p-3"><Link className="text-accent hover:underline" to={`/stocks/${encodeURIComponent(item.symbol)}`}>{item.code} {item.name}</Link>
        <div className="text-xs text-muted">{item.industry ?? '未分類'}，收盤 {price(item.price)}</div></td>
      <td className="p-3">{RATING_LABELS[item.rating] ?? item.rating}<div className="text-xs text-muted tabular-nums">綜合分 {item.composite_score.toFixed(1)}</div></td>
      <td className="p-3"><span className={`rounded px-1.5 py-0.5 text-xs ${item.signal ? 'bg-bull/15 text-bull' : 'bg-elevated text-secondary'}`}>{STATE_LABELS[item.execution_state]}</span></td>
      <td className="p-3 text-right tabular-nums">{price(item.pivot)}</td>
      <td className="p-3 text-right tabular-nums">{price(item.stop)}<div className="text-xs text-muted">風險 {item.risk_pct == null ? '—' : `${item.risk_pct.toFixed(1)}%`}</div></td>
      <td className="p-3 text-right tabular-nums">{pct(item.distance_from_pivot_pct)}</td>
      <td className="p-3 text-right tabular-nums">{item.rs_percentile}</td>
      <td className="p-3 text-right tabular-nums">{item.contractions.map(c => `${c.depth_pct.toFixed(0)}%`).join(' → ')}</td>
      <td className="p-3 text-sm">{action(item)}</td>
    </tr>
    <tr><td colSpan={HEADERS.length} className="px-3 pb-3">
      <details>
        <summary className="cursor-pointer text-xs text-muted">收縮明細與條件</summary>
        <div className="mt-2 space-y-1 text-xs text-secondary leading-relaxed">
          <p>收縮：{item.contractions.map(c => `${c.label} ${c.high_date} 高 ${price(c.high_price)} → ${c.low_date} 低 ${price(c.low_price)}（回檔 ${c.depth_pct.toFixed(1)}%，${c.duration_days} 日）`).join('；')}</p>
          <p>趨勢樣板：{Object.entries(item.trend_criteria).map(([k, ok]) => `${ok ? '✓' : '✗'} ${TREND_LABELS[k] ?? k}`).join('、')}（{item.trend_passed ? '通過' : '未通過'}，至少需 6 項）</p>
          <p>量縮比 {item.dry_up_ratio?.toFixed(2) ?? '—'}（近 10 日均量 ÷ 50 日均量，越低越好）；當日量 {item.breakout_volume_ratio?.toFixed(2) ?? '—'} 倍 50 日均量{item.breakout_volume_detected ? '，突破量確認' : ''}</p>
          <p>執行狀態：{item.execution_state_reason}{item.cap_reason ? `；評級上限：${item.cap_reason}` : ''}</p>
          <p>分項分數：{Object.entries(item.component_scores).map(([k, v]) => `${k} ${v}`).join('／')}</p>
          {!item.prefilter_passed && <p className="text-muted">注意：未通過初篩（股價、流動性或距 52 週高低點）</p>}
          {item.no_trade_skipped > 0 && <p className="text-muted">注意：視窗內略過 {item.no_trade_skipped} 個無成交日</p>}
        </div>
      </details>
    </td></tr>
  </tbody>
}

export function VcpScanSection({ date }: { date: string }) {
  // 日資料一天更新一次，全市場計算約 5 秒，不需要頻繁重抓
  const query = useQuery({ queryKey: QK.vcpScan(date), queryFn: () => api.taiwanVcpScan(date || undefined), staleTime: 5 * 60_000 })
  const data = query.data
  return <section aria-label="VCP 波動收縮" className="space-y-3 border-t border-border pt-4">
    <h2 className="font-medium">VCP 波動收縮</h2>
    <p className="text-xs text-muted leading-relaxed">
      上升趨勢中每次回檔越來越淺、量能越縮越少，突破最後一段收縮的高點（樞紐）且放量時才是買點；停損設在最後收縮低點下方 1%。
      先過 Minervini 趨勢樣板，RS 為全市場加權報酬百分位。規則移植自 tradermonty/claude-trading-skills（MIT）。只列出收縮結構成立的股票；這是型態偵測，不是買賣訊號，未經回測。
    </p>
    {query.isLoading && <p role="status" className="py-4 text-muted">VCP 計算中（全市場約需數秒）…</p>}
    {query.isError && <div role="alert" className="rounded-lg border border-danger/30 p-4 text-danger">VCP 讀取失敗：{query.error instanceof Error ? query.error.message : '請稍後重試'}<button className="ml-3 underline" onClick={() => void query.refetch()}>重試</button></div>}
    {data && data.status === 'unavailable' && <p role="status" className="rounded-lg border border-border bg-surface p-6 text-muted">{data.date} {REASON_LABELS[data.reason ?? ''] ?? '尚無可用的 VCP 資料'}</p>}
    {data && data.status === 'available' && <>
      <p className="text-sm">
        收縮結構成立 <span className="tabular-nums">{data.matched}</span> 檔，其中符合進場條件 <span className="tabular-nums">{data.signals}</span> 檔／可計算 <span className="tabular-nums">{data.eligible}</span> 檔／掃描範圍 <span className="tabular-nums">{data.universe}</span> 檔
        {Object.keys(data.excluded).length > 0 && <span className="text-xs text-muted"> 未列入：{Object.entries(data.excluded).map(([k, n]) => `${EXCLUDED_LABELS[k] ?? k} ${n}`).join('、')}</span>}
      </p>
      {data.items.length === 0
        ? <p className="rounded-lg border border-border bg-surface p-6 text-muted">目前沒有收縮結構成立的股票。</p>
        : <div className="overflow-x-auto rounded-lg border border-border"><table className="w-full min-w-[1040px] text-sm">
          <thead className="bg-elevated text-xs text-secondary"><tr>{HEADERS.map((t, i) => <th key={t} className={`p-3 ${i <= 2 || i === 8 ? 'text-left' : 'text-right'}`}>{t}</th>)}</tr></thead>
          {data.items.map(item => <VcpRow key={item.symbol} item={item} />)}
        </table></div>}
    </>}
  </section>
}

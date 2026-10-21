import { Link } from 'react-router-dom'
import type { PatternScanItem, PatternScanSnapshot } from '@/lib/marketResearch'

const HEADERS = ['股票', '型態', '狀態', '分數', '進場帶', '結構防守', '量度觀察位', '報酬風險比', '行動']
const EXCLUDED_LABELS: Record<string, string> = {
  incomplete_sessions: '90 日資料缺日',
  no_trade_sessions: '90 日內有無成交日',
  invalid_ohlc: '價格或量能資料異常',
  low_liquidity: '五日均量不足 300 張',
  corporate_action_unverified: '除權息未驗證',
  industry_excluded: '排除產業',
  unclassified: '未分類',
}
const REASON_LABELS: Record<string, string> = {
  no_daily_data: '尚無日資料',
  insufficient_sessions: '交易日不足 90 日',
  corporate_action_coverage_unavailable: '除權息資料未驗證，無法還原價格，暫不提供型態',
}

const price = (value: number) => value.toLocaleString('zh-TW', { maximumFractionDigits: 2 })

function Status({ item }: { item: PatternScanItem }) {
  const confirmed = item.pattern.status === '量價確認'
  return <span className={`rounded px-1.5 py-0.5 text-xs ${confirmed ? 'bg-bull/15 text-bull' : 'bg-elevated text-secondary'}`}>{item.pattern.status}</span>
}

function PatternRow({ item }: { item: PatternScanItem }) {
  const p = item.pattern
  const pl = p.plan
  return <tbody className="border-t border-border">
    <tr>
      <td className="p-3"><Link className="text-accent hover:underline" to={`/stocks/${encodeURIComponent(item.symbol)}`}>{item.code} {item.name}</Link>
        <div className="text-xs text-muted">{item.industry} · 收盤 {price(item.price)}</div></td>
      <td className="p-3">{p.name}<div className="text-xs text-muted">{p.category}</div></td>
      <td className="p-3"><Status item={item} /></td>
      <td className="p-3 text-right tabular-nums">{p.score}</td>
      <td className="p-3 text-right tabular-nums">{price(pl.entry)}–{price(pl.entry_max)}</td>
      <td className="p-3 text-right tabular-nums">{price(pl.stop)}<div className="text-xs text-muted">風險 {pl.risk_percent.toFixed(1)}%</div></td>
      <td className="p-3 text-right tabular-nums">{price(pl.target)}</td>
      <td className={`p-3 text-right tabular-nums ${pl.rr >= 1.5 ? 'text-bull' : 'text-muted'}`}>{pl.rr.toFixed(2)}</td>
      <td className="p-3 text-sm">{pl.action}</td>
    </tr>
    <tr><td colSpan={HEADERS.length} className="px-3 pb-3">
      <details>
        <summary className="cursor-pointer text-xs text-muted">型態說明與計畫條件</summary>
        <div className="mt-2 space-y-1 text-xs text-secondary leading-relaxed">
          <p>{p.description}</p>
          <p>確認：{pl.confirmation}</p>
          <p>防守：{pl.defense}</p>
          <p>時程：{pl.timing}</p>
          <p>量度觀察位：{pl.target_basis}</p>
          <p>未核對：{pl.missing}</p>
          <p>分數：幾何 {p.score_parts.geometry.toFixed(1)}／確認 {p.score_parts.confirmation}／量能 {p.score_parts.volume.toFixed(1)}／趨勢 {p.score_parts.trend}；突破量 {p.volume_ratio == null ? '不可比（股數變動）' : `${p.volume_ratio.toFixed(2)} 倍`}</p>
          {p.warnings.map(w => <p key={w} className="text-muted">注意：{w}</p>)}
        </div>
      </details>
    </td></tr>
  </tbody>
}

export function PatternScanTab({ data }: { data: PatternScanSnapshot }) {
  if (data.status === 'unavailable') return <p role="status" className="rounded-lg border border-border bg-surface p-6 text-muted">
    {data.date} {REASON_LABELS[data.reason ?? ''] ?? '暫無可用的型態資料'}
  </p>
  const excluded = Object.entries(data.excluded).filter(([, n]) => n > 0)
  return <div className="space-y-4">
    <p className="text-xs text-muted leading-relaxed">
      辨識最近 {data.rules.window} 個交易日（自 {data.from}）內的 10 種經典型態，以還原價判讀幾何，進場、防守與量度位換算為原始價。
      掃描上市櫃普通股（排除 {data.rules.excluded_industries.join('、')}，以及 ETF、權證），五日平均成交量至少 {data.rules.min_avg_volume_lots} 張，90 日還原與原始價必須完整對齊。
      量度觀察位為頸線加型態高度，不是目標價；報酬風險比扣約 {(data.rules.cost_rate * 100).toFixed(1)}% 成本，低於 {data.rules.rr_threshold} 建議暫不進場。條件式技術計畫，不是買賣訊號。
    </p>
    <p className="text-sm">
      符合 <span className="tabular-nums">{data.matched}</span> 檔（<span className="tabular-nums">{data.pattern_count}</span> 個型態）／可計算 <span className="tabular-nums">{data.eligible}</span> 檔／掃描範圍 <span className="tabular-nums">{data.universe}</span> 檔
      {excluded.length > 0 && <span className="text-xs text-muted"> 未列入：{excluded.map(([k, n]) => `${EXCLUDED_LABELS[k] ?? k} ${n}`).join('、')}</span>}
    </p>
    {data.items.length === 0
      ? <p className="rounded-lg border border-border bg-surface p-6 text-muted">目前沒有符合條件的型態。</p>
      : <div className="overflow-x-auto rounded-lg border border-border"><table className="w-full min-w-[1040px] text-sm">
        <thead className="bg-elevated text-xs text-secondary"><tr>{HEADERS.map((t, i) => <th key={t} className={`p-3 ${i === 0 || i === 1 || i === 2 || i === 8 ? 'text-left' : 'text-right'}`}>{t}</th>)}</tr></thead>
        {data.items.map(item => <PatternRow key={`${item.symbol}-${item.pattern.id}`} item={item} />)}
      </table></div>}
  </div>
}

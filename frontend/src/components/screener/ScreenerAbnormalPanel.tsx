import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { AlertCircle, Zap } from 'lucide-react'
import { api } from '@/lib/api'

export function ScreenerAbnormalPanel() {
  const [abnormalFilter, setAbnormalFilter] = useState<string>('ALL')
  const { data: abnormalData, isLoading: isAbnormalLoading, isError: isAbnormalError } = useQuery({
    queryKey: ['taiwanAbnormalDiagnostics', abnormalFilter],
    queryFn: () => api.taiwanAbnormalDiagnostics({
      signal_type: abnormalFilter !== 'ALL' ? abnormalFilter : undefined,
    }),
    staleTime: 5 * 60 * 1000,
  })

  return (
    <>
  {/* Taiwan Abnormal Diagnostics Panel (Phase 7D) */}
  <div className="bg-zinc-900/80 border border-amber-900/40 rounded-xl p-4 text-xs space-y-3">
      <div className="flex items-center justify-between border-b border-zinc-800 pb-2">
        <div className="flex items-center gap-2">
          <Zap className="w-4 h-4 text-amber-400" />
          <span className="font-semibold text-zinc-200 text-sm">台股市場異常異動與資金流向診斷 (Abnormal Diagnostics)</span>
          {abnormalData && (
            <span className="text-zinc-500 font-mono text-[11px]">
              全市場偵測到 {abnormalData.diagnostic_count} 檔標的觸發客觀異常訊號 (純確定性無推薦)
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 text-[11px] text-zinc-400">
          <span>訊號篩選:</span>
          <select
            value={abnormalFilter}
            onChange={e => setAbnormalFilter(e.target.value)}
            className="bg-zinc-950 border border-zinc-800 rounded px-2 py-0.5 text-zinc-200 focus:outline-none"
          >
            <option value="ALL">全部異常訊號</option>
            <option value="VOLUME_SPIKE">爆量 (&gt;= 2x)</option>
            <option value="TURNOVER_SPIKE">成交額放大 (&gt;= 2x)</option>
            <option value="PRICE_MOVE">價格異動 (&gt;= 5%)</option>
            <option value="FOREIGN_FLOW_SPIKE">外資異常買賣超</option>
            <option value="TRUST_FLOW_SPIKE">投信異常買賣超</option>
            <option value="MARGIN_SURGE">融資激增</option>
            <option value="SHORT_SURGE">融券激增</option>
            <option value="SHORT_MARGIN_RATIO_SPIKE">券資比驟升</option>
            <option value="PRICE_FLOW_DIVERGENCE">價量/法人背離</option>
            <option value="RELATIVE_STRENGTH_OUTLIER">產業相對強弱異常</option>
          </select>
        </div>
      </div>

      <div className="overflow-x-auto max-h-72 overflow-y-auto">
        <table className="w-full text-left border-collapse">
          <thead className="sticky top-0 bg-zinc-950/90 text-zinc-400 text-[11px] border-b border-zinc-800">
            <tr>
              <th className="py-1.5 px-2">股票名稱</th>
              <th className="py-1.5 px-2">所屬產業</th>
              <th className="py-1.5 px-2 text-right">收盤價</th>
              <th className="py-1.5 px-2 text-right">今日漲跌</th>
              <th className="py-1.5 px-2 text-right">成交金額</th>
              <th className="py-1.5 px-2 text-right">5D量比</th>
              <th className="py-1.5 px-2 text-right">外資買賣超</th>
              <th className="py-1.5 px-2 text-right">融資變化</th>
              <th className="py-1.5 px-2 text-right">訊號數</th>
              <th className="py-1.5 px-2">異常訊號特徵標籤</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/40 text-[11px] font-mono">
            {/* Data-first branching: cached/populated abnormalData always wins over a
                concurrent/subsequent background loading or error state (Phase 7L §3) */}
            {abnormalData ? (
              abnormalData.items.length === 0 ? (
                <tr>
                  <td colSpan={10} className="py-6 text-center text-zinc-500">
                    目前沒有偵測到異常訊號
                  </td>
                </tr>
              ) : (
                abnormalData.items.slice(0, 50).map(item => (
              <tr key={item.symbol} className="hover:bg-zinc-800/30 transition-colors">
                <td className="py-1.5 px-2 font-sans font-medium text-zinc-200">
                  <Link
                    to={`/stocks/${encodeURIComponent(item.symbol)}`}
                    className="hover:text-amber-400 hover:underline inline-flex items-center gap-1"
                  >
                    {item.name}
                    <span className="text-zinc-500 font-mono text-[10px]">({item.code})</span>
                  </Link>
                </td>
                <td className="py-1.5 px-2 font-sans text-zinc-400">{item.industry || '—'}</td>
                <td className="py-1.5 px-2 text-right text-zinc-200">{item.close ?? '—'}</td>
                <td className="py-1.5 px-2 text-right">
                  {item.change_pct !== null ? (
                    <span className={item.change_pct > 0 ? 'text-red-400 font-semibold' : item.change_pct < 0 ? 'text-emerald-400 font-semibold' : 'text-zinc-300'}>
                      {item.change_pct > 0 ? '+' : ''}{(item.change_pct * 100).toFixed(2)}%
                    </span>
                  ) : '—'}
                </td>
                <td className="py-1.5 px-2 text-right text-zinc-200">
                  {item.amount !== null ? `${(item.amount / 100_000_000).toFixed(1)} 億` : '—'}
                </td>
                <td className="py-1.5 px-2 text-right text-amber-300">
                  {item.volume_ratio_5d !== null ? `${item.volume_ratio_5d}x` : '—'}
                </td>
                <td className="py-1.5 px-2 text-right">
                  {item.foreign_net !== null ? (
                    <span className={item.foreign_net > 0 ? 'text-red-400' : item.foreign_net < 0 ? 'text-emerald-400' : 'text-zinc-300'}>
                      {item.foreign_net > 0 ? '+' : ''}{(item.foreign_net / 1000).toLocaleString()} 張
                    </span>
                  ) : '—'}
                </td>
                <td className="py-1.5 px-2 text-right">
                  {item.margin_balance_change !== null ? (
                    <span className={item.margin_balance_change > 0 ? 'text-red-400' : item.margin_balance_change < 0 ? 'text-emerald-400' : 'text-zinc-300'}>
                      {item.margin_balance_change > 0 ? '+' : ''}{(item.margin_balance_change / 1000).toLocaleString()} 張
                    </span>
                  ) : '—'}
                </td>
                <td className="py-1.5 px-2 text-right font-bold text-amber-400">
                  {item.signal_count}
                </td>
                <td className="py-1.5 px-2 font-sans">
                  <div className="flex flex-wrap gap-1">
                    {item.signals.map((sig, idx) => (
                      <span
                        key={idx}
                        className="px-1.5 py-0.5 rounded text-[10px] bg-zinc-800 border border-zinc-700 text-zinc-300"
                        title={`${sig.formula} (觀測值: ${sig.observed}, 基期: ${sig.baseline})`}
                      >
                        {sig.type === 'VOLUME_SPIKE' && '爆量'}
                        {sig.type === 'TURNOVER_SPIKE' && '成交額倍增'}
                        {sig.type === 'PRICE_MOVE' && (sig.subtype === 'UP' ? '大幅上漲' : '大幅下跌')}
                        {sig.type === 'FOREIGN_FLOW_SPIKE' && (sig.subtype === 'BUY' ? '外資爆量買超' : '外資爆量賣超')}
                        {sig.type === 'TRUST_FLOW_SPIKE' && (sig.subtype === 'BUY' ? '投信爆量買超' : '投信爆量賣超')}
                        {sig.type === 'MARGIN_SURGE' && (sig.subtype === 'INCREASE' ? '融資激增' : '融資大減')}
                        {sig.type === 'SHORT_SURGE' && '融券激增'}
                        {sig.type === 'SHORT_MARGIN_RATIO_SPIKE' && '券資比驟升'}
                        {sig.type === 'PRICE_FLOW_DIVERGENCE' && '價量背離'}
                        {sig.type === 'RELATIVE_STRENGTH_OUTLIER' && '產業強弱極端'}
                      </span>
                    ))}
                  </div>
                </td>
              </tr>
                ))
              )
            ) : isAbnormalLoading ? (
              <tr>
                <td colSpan={10} className="py-6 text-center text-zinc-500">
                  <div role="status" aria-live="polite" className="flex items-center justify-center gap-2">
                    <div className="w-3.5 h-3.5 border-2 border-amber-500 border-t-transparent rounded-full animate-spin" />
                    <span>正在載入異常診斷…</span>
                  </div>
                </td>
              </tr>
            ) : isAbnormalError ? (
              <tr>
                <td colSpan={10} className="py-6 text-center text-red-400">
                  <div role="alert" className="flex items-center justify-center gap-1.5">
                    <AlertCircle className="w-4 h-4 text-red-500" />
                    <span>異常診斷載入失敗，請稍後再試</span>
                  </div>
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
  </div>

    </>
  )
}

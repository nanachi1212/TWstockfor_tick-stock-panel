import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Layers } from 'lucide-react'
import { api } from '@/lib/api'

export function ScreenerIndustryPanel({ setIndustry, setPage }: { setIndustry: (value: string) => void; setPage: (page: number) => void }) {
  const [indSortBy, setIndSortBy] = useState<string>('turnover')
  const [indOrder, setIndOrder] = useState<'desc' | 'asc'>('desc')
  // Phase 8C-C: 34 檔預設收合只顯示前 10 檔, 避免一次全部展開
  const [showAllIndustries, setShowAllIndustries] = useState(false)
  const { data: indData } = useQuery({
    queryKey: ['taiwanIndustryIntelligence', indSortBy, indOrder],
    queryFn: () => api.taiwanIndustryIntelligence({ sort_by: indSortBy, order: indOrder }),
    staleTime: 5 * 60 * 1000,
  })
  return (
    <>
  {/* Taiwan Industry / Sector Intelligence Panel (Phase 7B) */}
  {indData && (
    <div className="bg-zinc-900/80 border border-zinc-800/90 rounded-xl p-4 text-xs space-y-3">
      <div className="flex items-center justify-between border-b border-zinc-800 pb-2">
        <div className="flex items-center gap-2">
          <Layers className="w-4 h-4 text-cyan-400" />
          <span className="font-semibold text-zinc-200 text-sm">台股產業類股輪動 (Industry Intelligence)</span>
          <span className="text-zinc-500 font-mono text-[11px]">{indData.industries.length} 大類股統計 (點擊產業名稱可套用篩選)</span>
        </div>
        <div className="flex items-center gap-2 text-[11px] text-zinc-400">
          <span>排序:</span>
          <select
            value={indSortBy}
            onChange={e => setIndSortBy(e.target.value)}
            className="bg-zinc-950 border border-zinc-800 rounded px-2 py-0.5 text-zinc-200 focus:outline-none"
          >
            <option value="turnover">成交金額</option>
            <option value="relative_strength_5d">5日相對強弱 (RS 5D)</option>
            <option value="relative_strength_20d">20日相對強弱 (RS 20D)</option>
            <option value="median_change_pct">今日漲跌中位數</option>
            <option value="advance_ratio">上漲家數比例</option>
            <option value="foreign_net">外資買賣超</option>
            <option value="investment_trust_net">投信買賣超</option>
          </select>
          <button
            onClick={() => setIndOrder(prev => prev === 'desc' ? 'asc' : 'desc')}
            className="px-2 py-0.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded"
          >
            {indOrder === 'desc' ? '降序 ↓' : '升序 ↑'}
          </button>
        </div>
      </div>

      <div id="industry-intelligence-table" className={`overflow-x-auto ${showAllIndustries ? 'max-h-96 overflow-y-auto' : ''}`}>
        <table className="w-full text-left border-collapse">
          <thead className="sticky top-0 bg-zinc-950/90 text-zinc-400 text-[11px] border-b border-zinc-800">
            <tr>
              <th className="py-1.5 px-2">產業名稱</th>
              <th className="py-1.5 px-2 text-right">標的數</th>
              <th className="py-1.5 px-2 text-right">漲 / 跌 / 平</th>
              <th className="py-1.5 px-2 text-right">上漲比</th>
              <th className="py-1.5 px-2 text-right">中位漲跌</th>
              <th className="py-1.5 px-2 text-right">成交金額</th>
              <th className="py-1.5 px-2 text-right">成交佔比</th>
              <th className="py-1.5 px-2 text-right">5D 相對強弱</th>
              <th className="py-1.5 px-2 text-right">20D 相對強弱</th>
              <th className="py-1.5 px-2 text-right">外資買賣超</th>
              <th className="py-1.5 px-2 text-right">投信買賣超</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/40 text-[11px] font-mono">
            {(showAllIndustries ? indData.industries : indData.industries.slice(0, 10)).map(ind => (
              <tr key={ind.industry} className="hover:bg-zinc-800/30 transition-colors">
                <td className="py-1.5 px-2 font-sans font-medium text-zinc-200">
                  <button
                    onClick={() => {
                      setIndustry(ind.industry)
                      setPage(1)
                    }}
                    className="hover:text-purple-400 hover:underline text-left cursor-pointer"
                    title="點擊套用此產業至選股篩選"
                  >
                    {ind.industry}
                  </button>
                </td>
                <td className="py-1.5 px-2 text-right text-zinc-400">{ind.supported_symbol_count}</td>
                <td className="py-1.5 px-2 text-right">
                  <span className="text-red-400">{ind.advance_count}</span>
                  <span className="text-zinc-600 mx-0.5">/</span>
                  <span className="text-emerald-400">{ind.decline_count}</span>
                  <span className="text-zinc-600 mx-0.5">/</span>
                  <span className="text-zinc-400">{ind.flat_count}</span>
                </td>
                <td className="py-1.5 px-2 text-right text-zinc-300">
                  {ind.advance_ratio !== null ? `${(ind.advance_ratio * 100).toFixed(0)}%` : '—'}
                </td>
                <td className="py-1.5 px-2 text-right">
                  {ind.median_change_pct !== null ? (
                    <span className={ind.median_change_pct > 0 ? 'text-red-400 font-semibold' : ind.median_change_pct < 0 ? 'text-emerald-400 font-semibold' : 'text-zinc-300'}>
                      {ind.median_change_pct > 0 ? '+' : ''}{(ind.median_change_pct * 100).toFixed(2)}%
                    </span>
                  ) : <span className="text-zinc-500">—</span>}
                </td>
                <td className="py-1.5 px-2 text-right text-zinc-200 font-semibold">
                  {(ind.turnover / 100_000_000).toFixed(1)} 億
                </td>
                <td className="py-1.5 px-2 text-right text-zinc-400">
                  {(ind.turnover_share * 100).toFixed(1)}%
                </td>
                <td className="py-1.5 px-2 text-right">
                  {ind.relative_strength_5d !== null ? (
                    <span className={ind.relative_strength_5d > 0 ? 'text-red-400 font-semibold' : ind.relative_strength_5d < 0 ? 'text-emerald-400 font-semibold' : 'text-zinc-300'}>
                      {ind.relative_strength_5d > 0 ? '+' : ''}{(ind.relative_strength_5d * 100).toFixed(2)}%
                    </span>
                  ) : <span className="text-zinc-500">—</span>}
                </td>
                <td className="py-1.5 px-2 text-right">
                  {ind.relative_strength_20d !== null ? (
                    <span className={ind.relative_strength_20d > 0 ? 'text-red-400 font-semibold' : ind.relative_strength_20d < 0 ? 'text-emerald-400 font-semibold' : 'text-zinc-300'}>
                      {ind.relative_strength_20d > 0 ? '+' : ''}{(ind.relative_strength_20d * 100).toFixed(2)}%
                    </span>
                  ) : <span className="text-zinc-500">—</span>}
                </td>
                <td className="py-1.5 px-2 text-right">
                  {ind.foreign_net !== null ? (
                    <span className={ind.foreign_net > 0 ? 'text-red-400' : ind.foreign_net < 0 ? 'text-emerald-400' : 'text-zinc-300'}>
                      {ind.foreign_net > 0 ? '+' : ''}{(ind.foreign_net / 1000).toLocaleString()} 張
                    </span>
                  ) : <span className="text-zinc-500">—</span>}
                </td>
                <td className="py-1.5 px-2 text-right">
                  {ind.investment_trust_net !== null ? (
                    <span className={ind.investment_trust_net > 0 ? 'text-red-400' : ind.investment_trust_net < 0 ? 'text-emerald-400' : 'text-zinc-300'}>
                      {ind.investment_trust_net > 0 ? '+' : ''}{(ind.investment_trust_net / 1000).toLocaleString()} 張
                    </span>
                  ) : <span className="text-zinc-500">—</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Phase 8C-C: 34 檔預設只顯示前 10 檔, 避免一次展開完整分析表格
          (Dashboard 已有 Top/Bottom 摘要, 這裡保留完整分析能力供進階使用者展開)。 */}
      {indData.industries.length > 10 && (
        <button
          type="button"
          onClick={() => setShowAllIndustries(prev => !prev)}
          aria-expanded={showAllIndustries}
          aria-controls="industry-intelligence-table"
          className="w-full py-1.5 rounded-md bg-zinc-800/60 hover:bg-zinc-800 text-zinc-400 hover:text-zinc-200 text-[11px] font-medium transition-colors"
        >
          {showAllIndustries ? '收合 ▴' : `顯示全部 ${indData.industries.length} 檔 ▾`}
        </button>
      )}
    </div>
  )}

    </>
  )
}

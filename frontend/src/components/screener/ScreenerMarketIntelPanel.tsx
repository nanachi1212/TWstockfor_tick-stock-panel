import { useQuery } from '@tanstack/react-query'
import { BarChart3, TrendingUp, TrendingDown } from 'lucide-react'
import { api } from '@/lib/api'

export function ScreenerMarketIntelPanel() {
  const { data: intelData } = useQuery({
    queryKey: ['taiwanMarketIntelligence'],
    queryFn: () => api.taiwanMarketIntelligence(),
    staleTime: 5 * 60 * 1000,
  })
  return (
    <>
  {/* Market Intelligence Snapshot Panel (Phase 7A) */}
  {intelData && (
    <div className="bg-zinc-900/80 border border-zinc-800/90 rounded-xl p-4 text-xs space-y-3">
      <div className="flex items-center justify-between border-b border-zinc-800 pb-2">
        <div className="flex items-center gap-2">
          <BarChart3 className="w-4 h-4 text-purple-400" />
          <span className="font-semibold text-zinc-200 text-sm">台股全市場量化統計快照 (Market Intelligence)</span>
          <span className="text-zinc-500 font-mono text-[11px]">交易日: {intelData.trade_date}</span>
          {/* Data Freshness & Source Labels batch: trade_date 是「應有最新
              交易日」(resolve_target_latest_trading_date, 依當下時間與收盤
              時間推算)，不等於本地實際持有資料的日期。當該日尚無完整市場
              資料時 (data_quality.overall_status !== 'complete')，下方統計
              會全部是 0 —— 不加註明容易被誤讀為「今日零成交」。沿用本頁
              上方既有的 待更新 badge 視覺樣式，不新造一套。 */}
          {intelData.data_quality?.overall_status !== 'complete' && (
            <span className="px-1.5 py-0.5 rounded bg-amber-900/50 text-amber-400 font-medium text-[10px]">
              尚無完整市場資料，以下統計非今日實際行情
            </span>
          )}
        </div>
        <div className="text-[11px] text-zinc-400">
          全市場成交額: <strong className="text-zinc-200 font-mono">{(intelData.market_totals.turnover / 100_000_000).toFixed(1)}</strong> 億元
          <span className="text-zinc-600 mx-2">|</span>
          有效撮合標的: <strong className="text-purple-400 font-mono">{intelData.market_totals.traded_count}</strong> / {intelData.market_totals.supported_count} 檔
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        {/* Advance / Decline / Flat (Taiwan colors: Up=Red, Down=Green) */}
        <div className="bg-zinc-950/70 border border-zinc-800/80 rounded-lg p-2.5 flex flex-col justify-between">
          <span className="text-zinc-400 text-[11px]">全市場漲跌家數</span>
          <div className="flex items-center gap-2 mt-1 font-mono font-medium">
            <span className="text-red-400 flex items-center gap-0.5">
              <TrendingUp className="w-3 h-3" /> {intelData.market_totals.advance_count}
            </span>
            <span className="text-zinc-600">/</span>
            <span className="text-emerald-400 flex items-center gap-0.5">
              <TrendingDown className="w-3 h-3" /> {intelData.market_totals.decline_count}
            </span>
            <span className="text-zinc-600">/</span>
            <span className="text-zinc-400">{intelData.market_totals.flat_count}</span>
          </div>
          <span className="text-[10px] text-zinc-500 mt-1">上漲 / 下跌 / 平盤</span>
        </div>

        {/* Limit Up / Limit Down */}
        <div className="bg-zinc-950/70 border border-zinc-800/80 rounded-lg p-2.5 flex flex-col justify-between">
          <span className="text-zinc-400 text-[11px]">漲跌停家數</span>
          <div className="flex items-center gap-2 mt-1 font-mono font-medium">
            <span className="text-red-400 font-bold">{intelData.market_totals.upper_limit_count} 漲停</span>
            <span className="text-zinc-600">|</span>
            <span className="text-emerald-400 font-bold">{intelData.market_totals.lower_limit_count} 跌停</span>
          </div>
          <span className="text-[10px] text-zinc-500 mt-1">法定價格限制 (排除無限制)</span>
        </div>

        {/* Exchange Breakdown (TWSE vs TPEx) */}
        <div className="bg-zinc-950/70 border border-zinc-800/80 rounded-lg p-2.5 flex flex-col justify-between">
          <span className="text-zinc-400 text-[11px]">交易所成交額</span>
          <div className="space-y-0.5 font-mono text-[11px] mt-1">
            <div className="flex justify-between">
              <span className="text-zinc-400">上市:</span>
              <span className="text-zinc-200">{(intelData.by_exchange.twse.turnover / 100_000_000).toFixed(0)} 億</span>
            </div>
            <div className="flex justify-between">
              <span className="text-zinc-400">上櫃:</span>
              <span className="text-zinc-200">{(intelData.by_exchange.tpex.turnover / 100_000_000).toFixed(0)} 億</span>
            </div>
          </div>
          <span className="text-[10px] text-zinc-500 mt-1">TWSE vs TPEx</span>
        </div>

        {/* Foreign Net (三大法人 - 外資) */}
        <div className="bg-zinc-950/70 border border-zinc-800/80 rounded-lg p-2.5 flex flex-col justify-between">
          <span className="text-zinc-400 text-[11px]">外資買賣超</span>
          <div className="font-mono text-sm font-semibold mt-1">
            {intelData.institutional.foreign_net !== null ? (
              <span className={intelData.institutional.foreign_net > 0 ? 'text-red-400' : intelData.institutional.foreign_net < 0 ? 'text-emerald-400' : 'text-zinc-300'}>
                {intelData.institutional.foreign_net > 0 ? '+' : ''}{(intelData.institutional.foreign_net / 1000).toLocaleString()} 張
              </span>
            ) : <span className="text-zinc-500">—</span>}
          </div>
          <span className="text-[10px] text-zinc-500 mt-1">全市場總計 (股數/1000)</span>
        </div>

        {/* Investment Trust Net (三大法人 - 投信) */}
        <div className="bg-zinc-950/70 border border-zinc-800/80 rounded-lg p-2.5 flex flex-col justify-between">
          <span className="text-zinc-400 text-[11px]">投信買賣超</span>
          <div className="font-mono text-sm font-semibold mt-1">
            {intelData.institutional.investment_trust_net !== null ? (
              <span className={intelData.institutional.investment_trust_net > 0 ? 'text-red-400' : intelData.institutional.investment_trust_net < 0 ? 'text-emerald-400' : 'text-zinc-300'}>
                {intelData.institutional.investment_trust_net > 0 ? '+' : ''}{(intelData.institutional.investment_trust_net / 1000).toLocaleString()} 張
              </span>
            ) : <span className="text-zinc-500">—</span>}
          </div>
          <span className="text-[10px] text-zinc-500 mt-1">全市場總計 (股數/1000)</span>
        </div>

        {/* Margin Change & Ratio (信用交易) */}
        <div className="bg-zinc-950/70 border border-zinc-800/80 rounded-lg p-2.5 flex flex-col justify-between">
          <span className="text-zinc-400 text-[11px]">融資餘額增減</span>
          <div className="font-mono text-sm font-semibold mt-1">
            {intelData.margin.margin_balance_change !== null ? (
              <span className={intelData.margin.margin_balance_change > 0 ? 'text-red-400' : intelData.margin.margin_balance_change < 0 ? 'text-emerald-400' : 'text-zinc-300'}>
                {intelData.margin.margin_balance_change > 0 ? '+' : ''}{(intelData.margin.margin_balance_change / 1000).toLocaleString()} 張
              </span>
            ) : <span className="text-zinc-500">—</span>}
          </div>
          <span className="text-[10px] text-zinc-500 mt-1">
            全市場券資比: {intelData.margin.aggregate_short_margin_ratio ? `${intelData.margin.aggregate_short_margin_ratio.toFixed(2)}%` : '—'}
          </span>
        </div>
      </div>
    </div>
  )}

    </>
  )
}

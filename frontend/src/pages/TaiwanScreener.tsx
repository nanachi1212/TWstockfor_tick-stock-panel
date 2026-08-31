import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  Filter,
  ArrowUp,
  ArrowDown,
  RotateCcw,
  ExternalLink,
  ChevronLeft,
  ChevronRight,
  AlertCircle,
  Database,
} from 'lucide-react'
import { api, type TaiwanScreenerRequest, type ScreenerResultItem } from '@/lib/api'

export function TaiwanScreener() {
  // Filter states
  const [exchange, setExchange] = useState<'ALL' | 'TWSE' | 'TPEX'>('ALL')
  const [instrument, setInstrument] = useState<'ALL' | 'stock' | 'etf'>('ALL')
  const [industry, setIndustry] = useState<string>('ALL')
  const [priceMin, setPriceMin] = useState<string>('')
  const [priceMax, setPriceMax] = useState<string>('')
  const [changePctMin, setChangePctMin] = useState<string>('')
  const [changePctMax, setChangePctMax] = useState<string>('')
  const [volumeMinLots, setVolumeMinLots] = useState<string>('') // UI in lots (張) -> backend in shares (x1000)
  const [amountMinMln, setAmountMinMln] = useState<string>('') // UI in 百萬 TWD -> backend in TWD (x10^6)
  const [rsiMin, setRsiMin] = useState<string>('')
  const [rsiMax, setRsiMax] = useState<string>('')
  const [momentumMin, setMomentumMin] = useState<string>('')
  const [volRatioMin, setVolRatioMin] = useState<string>('')
  const [aboveMa5, setAboveMa5] = useState<boolean | null>(null)
  const [aboveMa20, setAboveMa20] = useState<boolean | null>(null)
  const [nearUpperLimit, setNearUpperLimit] = useState<boolean>(false)
  const [nearLowerLimit, setNearLowerLimit] = useState<boolean>(false)

  // Institutional filters (UI in 張 -> backend in shares x1000)
  const [foreignNetMinLots, setForeignNetMinLots] = useState<string>('')
  const [foreignNetMaxLots, setForeignNetMaxLots] = useState<string>('')
  const [investmentTrustNetMinLots, setInvestmentTrustNetMinLots] = useState<string>('')
  const [dealerNetMinLots, setDealerNetMinLots] = useState<string>('')

  // Margin filters (UI in 張 -> backend in shares x1000, ratio: 10 = 10%)
  const [marginBalanceChangeMinLots, setMarginBalanceChangeMinLots] = useState<string>('')
  const [shortBalanceMinLots, setShortBalanceMinLots] = useState<string>('')
  const [shortMarginRatioMin, setShortMarginRatioMin] = useState<string>('')

  // Sorting & Pagination
  const [sortBy, setSortBy] = useState<string>('symbol')
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc')
  const [page, setPage] = useState<number>(1)
  const pageSize = 50

  const handleReset = () => {
    setExchange('ALL')
    setInstrument('ALL')
    setIndustry('ALL')
    setPriceMin('')
    setPriceMax('')
    setChangePctMin('')
    setChangePctMax('')
    setVolumeMinLots('')
    setAmountMinMln('')
    setRsiMin('')
    setRsiMax('')
    setMomentumMin('')
    setVolRatioMin('')
    setAboveMa5(null)
    setAboveMa20(null)
    setNearUpperLimit(false)
    setNearLowerLimit(false)
    setForeignNetMinLots('')
    setForeignNetMaxLots('')
    setInvestmentTrustNetMinLots('')
    setDealerNetMinLots('')
    setMarginBalanceChangeMinLots('')
    setShortBalanceMinLots('')
    setShortMarginRatioMin('')
    setSortBy('symbol')
    setSortOrder('asc')
    setPage(1)
  }

  // Request payload construction
  const payload = useMemo<TaiwanScreenerRequest>(() => {
    const p: TaiwanScreenerRequest = {
      exchange,
      instrument,
      industry: industry !== 'ALL' ? industry : null,
      price_min: priceMin ? parseFloat(priceMin) : null,
      price_max: priceMax ? parseFloat(priceMax) : null,
      // change_pct: UI percent to decimal (e.g. 5% -> 0.05)
      change_pct_min: changePctMin ? parseFloat(changePctMin) / 100.0 : null,
      change_pct_max: changePctMax ? parseFloat(changePctMax) / 100.0 : null,
      // volume: UI lots (張) -> backend shares (x 1000)
      volume_min: volumeMinLots ? parseFloat(volumeMinLots) * 1000.0 : null,
      amount_min: amountMinMln ? parseFloat(amountMinMln) * 1_000_000.0 : null,
      rsi_14_min: rsiMin ? parseFloat(rsiMin) : null,
      rsi_14_max: rsiMax ? parseFloat(rsiMax) : null,
      momentum_5d_min: momentumMin ? parseFloat(momentumMin) / 100.0 : null,
      vol_ratio_5d_min: volRatioMin ? parseFloat(volRatioMin) : null,
      above_ma5: aboveMa5,
      above_ma20: aboveMa20,
      near_upper_limit: nearUpperLimit || null,
      near_lower_limit: nearLowerLimit || null,
      // Institutional: UI lots (張) -> backend shares (x 1000)
      foreign_net_min: foreignNetMinLots ? parseFloat(foreignNetMinLots) * 1000.0 : null,
      foreign_net_max: foreignNetMaxLots ? parseFloat(foreignNetMaxLots) * 1000.0 : null,
      investment_trust_net_min: investmentTrustNetMinLots ? parseFloat(investmentTrustNetMinLots) * 1000.0 : null,
      dealer_net_min: dealerNetMinLots ? parseFloat(dealerNetMinLots) * 1000.0 : null,
      // Margin: UI lots (張) -> backend shares (x 1000), ratio: 10.0 = 10%
      margin_balance_change_min: marginBalanceChangeMinLots ? parseFloat(marginBalanceChangeMinLots) * 1000.0 : null,
      short_balance_min: shortBalanceMinLots ? parseFloat(shortBalanceMinLots) * 1000.0 : null,
      short_margin_ratio_min: shortMarginRatioMin ? parseFloat(shortMarginRatioMin) : null,
      sort_by: sortBy,
      sort_order: sortOrder,
      page,
      page_size: pageSize,
    }
    return p
  }, [
    exchange, instrument, industry, priceMin, priceMax, changePctMin, changePctMax,
    volumeMinLots, amountMinMln, rsiMin, rsiMax, momentumMin, volRatioMin,
    aboveMa5, aboveMa20, nearUpperLimit, nearLowerLimit,
    foreignNetMinLots, foreignNetMaxLots, investmentTrustNetMinLots, dealerNetMinLots,
    marginBalanceChangeMinLots, shortBalanceMinLots, shortMarginRatioMin,
    sortBy, sortOrder, page,
  ])

  // Screener query
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['taiwanScreener', payload],
    queryFn: () => api.taiwanScreenerRun(payload),
    staleTime: 10_000,
  })

  const totalPages = data ? Math.ceil(data.total / data.page_size) : 1

  const handleSort = (col: string) => {
    if (sortBy === col) {
      setSortOrder(prev => (prev === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortBy(col)
      setSortOrder('desc')
    }
    setPage(1)
  }

  // Formatters (Taiwan standard: Up is Red, Down is Green)
  const formatChangePct = (val?: number | null) => {
    if (val === null || val === undefined) return '-'
    const pct = val * 100
    const text = `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%`
    const color = pct > 0 ? 'text-red-500 font-medium' : pct < 0 ? 'text-emerald-500 font-medium' : 'text-zinc-400'
    return <span className={color}>{text}</span>
  }

  const formatPrice = (val?: number | null) => {
    if (val === null || val === undefined) return '-'
    return val >= 1000 ? val.toFixed(0) : val.toFixed(2)
  }

  const formatVolumeLots = (shares?: number | null) => {
    if (shares === null || shares === undefined) return '-'
    const lots = shares / 1000
    return `${lots.toLocaleString(undefined, { maximumFractionDigits: 0 })} 張`
  }

  const formatAmount = (amt?: number | null) => {
    if (amt === null || amt === undefined) return '-'
    if (amt >= 100_000_000) {
      return `${(amt / 100_000_000).toFixed(2)} 億`
    }
    return `${(amt / 10_000).toFixed(0)} 萬`
  }

  // Institutional / Margin signed flow formatter (Taiwan color: >0 Red, <0 Green, null '—')
  const formatSignedSharesLots = (shares?: number | null) => {
    if (shares === null || shares === undefined) return <span className="text-zinc-500">—</span>
    const lots = Math.round(shares / 1000)
    const formatted = lots.toLocaleString(undefined)
    if (lots > 0) {
      return <span className="text-red-500 font-medium">+{formatted} 張</span>
    } else if (lots < 0) {
      return <span className="text-emerald-500 font-medium">{formatted} 張</span>
    }
    return <span className="text-zinc-400">0 張</span>
  }

  // Short balance formatter (neutral, null '—')
  const formatShortBalanceLots = (shares?: number | null) => {
    if (shares === null || shares === undefined) return <span className="text-zinc-500">—</span>
    const lots = Math.round(shares / 1000)
    return <span className="text-zinc-300 font-mono">{lots.toLocaleString(undefined)} 張</span>
  }

  // Short margin ratio formatter (10.0 = 10%, null '—')
  const formatShortMarginRatio = (ratio?: number | null) => {
    if (ratio === null || ratio === undefined) return <span className="text-zinc-500">—</span>
    return <span className="text-zinc-300 font-mono">{ratio.toFixed(2)}%</span>
  }

  return (
    <div className="flex-1 flex flex-col p-6 space-y-6 max-w-7xl mx-auto w-full">
      {/* Top Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-zinc-800 pb-4">
        <div>
          <h1 className="text-2xl font-bold text-zinc-100 flex items-center gap-2">
            <Filter className="w-6 h-6 text-purple-400" />
            台股策略選股工作台
          </h1>
          <p className="text-sm text-zinc-400 mt-1">
            基於 Security Master 全市場標的與本地持久化分區日線、法人及資券資料庫的高效批次選股 (Taiwan Market Screener)
          </p>
        </div>

        {/* Data Provenance Badges (Independent daily, institutional, margin dates) */}
        {data && (
          <div className="flex flex-wrap items-center gap-2 text-xs bg-zinc-900 border border-zinc-800 px-3 py-2 rounded-lg text-zinc-400">
            <Database className="w-4 h-4 text-purple-400 shrink-0" />
            <span>日線: <strong className="text-zinc-200">{data.data_dates.daily_as_of || '—'}</strong></span>
            <span className="text-zinc-600">|</span>
            <span>法人: <strong className="text-zinc-200">{data.data_dates.institutional_as_of || '—'}</strong></span>
            <span className="text-zinc-600">|</span>
            <span>資券: <strong className="text-zinc-200">{data.data_dates.margin_as_of || '—'}</strong></span>
            <span className="text-zinc-600">|</span>
            <span>符合: <strong className="text-purple-400">{data.total}</strong> 檔</span>
          </div>
        )}
      </div>

      {/* Filter Control Panel */}
      <div className="bg-zinc-900/70 border border-zinc-800/80 rounded-xl p-5 space-y-4">
        {/* Row 1: Exchange & Instrument Types */}
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <label className="text-xs font-semibold text-zinc-400 block mb-1.5">市場交易所</label>
            <div className="flex rounded-lg bg-zinc-800 p-1">
              {(['ALL', 'TWSE', 'TPEX'] as const).map(ex => (
                <button
                  key={ex}
                  onClick={() => { setExchange(ex); setPage(1) }}
                  className={`flex-1 py-1.5 text-xs font-medium rounded-md transition-all ${
                    exchange === ex ? 'bg-purple-600 text-white shadow-sm' : 'text-zinc-400 hover:text-zinc-200'
                  }`}
                >
                  {ex === 'ALL' ? '全部' : ex === 'TWSE' ? '上市 (TWSE)' : '上櫃 (TPEx)'}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="text-xs font-semibold text-zinc-400 block mb-1.5">標的型態</label>
            <div className="flex rounded-lg bg-zinc-800 p-1">
              {(['ALL', 'stock', 'etf'] as const).map(inst => (
                <button
                  key={inst}
                  onClick={() => { setInstrument(inst); setPage(1) }}
                  className={`flex-1 py-1.5 text-xs font-medium rounded-md transition-all ${
                    instrument === inst ? 'bg-purple-600 text-white shadow-sm' : 'text-zinc-400 hover:text-zinc-200'
                  }`}
                >
                  {inst === 'ALL' ? '全部' : inst === 'stock' ? '一般個股' : 'ETF'}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="text-xs font-semibold text-zinc-400 block mb-1.5">漲跌幅區間 (%)</label>
            <div className="flex items-center gap-2">
              <input
                type="number"
                placeholder="最小 %"
                value={changePctMin}
                onChange={e => { setChangePctMin(e.target.value); setPage(1) }}
                className="w-full bg-zinc-800 border border-zinc-700 rounded-md px-2.5 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-purple-500"
              />
              <span className="text-zinc-500">~</span>
              <input
                type="number"
                placeholder="最大 %"
                value={changePctMax}
                onChange={e => { setChangePctMax(e.target.value); setPage(1) }}
                className="w-full bg-zinc-800 border border-zinc-700 rounded-md px-2.5 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-purple-500"
              />
            </div>
          </div>

          <div>
            <label className="text-xs font-semibold text-zinc-400 block mb-1.5">價格區間 (TWD)</label>
            <div className="flex items-center gap-2">
              <input
                type="number"
                placeholder="最低價"
                value={priceMin}
                onChange={e => { setPriceMin(e.target.value); setPage(1) }}
                className="w-full bg-zinc-800 border border-zinc-700 rounded-md px-2.5 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-purple-500"
              />
              <span className="text-zinc-500">~</span>
              <input
                type="number"
                placeholder="最高價"
                value={priceMax}
                onChange={e => { setPriceMax(e.target.value); setPage(1) }}
                className="w-full bg-zinc-800 border border-zinc-700 rounded-md px-2.5 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-purple-500"
              />
            </div>
          </div>
        </div>

        {/* Row 2: Volume, Amount, RSI, Momentum */}
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <label className="text-xs font-semibold text-zinc-400 block mb-1.5">最低成交量 (張)</label>
            <input
              type="number"
              placeholder="例: 1000 張"
              value={volumeMinLots}
              onChange={e => { setVolumeMinLots(e.target.value); setPage(1) }}
              className="w-full bg-zinc-800 border border-zinc-700 rounded-md px-2.5 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-purple-500"
            />
          </div>

          <div>
            <label className="text-xs font-semibold text-zinc-400 block mb-1.5">最低成交金額 (百萬 TWD)</label>
            <input
              type="number"
              placeholder="例: 50 百萬"
              value={amountMinMln}
              onChange={e => { setAmountMinMln(e.target.value); setPage(1) }}
              className="w-full bg-zinc-800 border border-zinc-700 rounded-md px-2.5 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-purple-500"
            />
          </div>

          <div>
            <label className="text-xs font-semibold text-zinc-400 block mb-1.5">RSI (14) 區間</label>
            <div className="flex items-center gap-2">
              <input
                type="number"
                placeholder="最小"
                value={rsiMin}
                onChange={e => { setRsiMin(e.target.value); setPage(1) }}
                className="w-full bg-zinc-800 border border-zinc-700 rounded-md px-2.5 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-purple-500"
              />
              <span className="text-zinc-500">~</span>
              <input
                type="number"
                placeholder="最大"
                value={rsiMax}
                onChange={e => { setRsiMax(e.target.value); setPage(1) }}
                className="w-full bg-zinc-800 border border-zinc-700 rounded-md px-2.5 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-purple-500"
              />
            </div>
          </div>

          <div>
            <label className="text-xs font-semibold text-zinc-400 block mb-1.5">5日動量 / 量比條件</label>
            <div className="flex items-center gap-2">
              <input
                type="number"
                placeholder="5日動量 (%)"
                value={momentumMin}
                onChange={e => { setMomentumMin(e.target.value); setPage(1) }}
                className="w-full bg-zinc-800 border border-zinc-700 rounded-md px-2.5 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-purple-500"
              />
              <input
                type="number"
                placeholder="5日量比"
                value={volRatioMin}
                onChange={e => { setVolRatioMin(e.target.value); setPage(1) }}
                className="w-full bg-zinc-800 border border-zinc-700 rounded-md px-2.5 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-purple-500"
              />
            </div>
          </div>
        </div>

        {/* Row 3: Institutional Filters (外資買賣超區間、投信買超、自營商買超) */}
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4 pt-2 border-t border-zinc-800/60">
          <div>
            <label className="text-xs font-semibold text-zinc-400 block mb-1.5">外資買賣超區間 (張)</label>
            <div className="flex items-center gap-2">
              <input
                type="number"
                placeholder="最小 (張)"
                value={foreignNetMinLots}
                onChange={e => { setForeignNetMinLots(e.target.value); setPage(1) }}
                className="w-full bg-zinc-800 border border-zinc-700 rounded-md px-2.5 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-purple-500"
              />
              <span className="text-zinc-500">~</span>
              <input
                type="number"
                placeholder="最大 (張)"
                value={foreignNetMaxLots}
                onChange={e => { setForeignNetMaxLots(e.target.value); setPage(1) }}
                className="w-full bg-zinc-800 border border-zinc-700 rounded-md px-2.5 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-purple-500"
              />
            </div>
          </div>

          <div>
            <label className="text-xs font-semibold text-zinc-400 block mb-1.5">投信最低買超 (張)</label>
            <input
              type="number"
              placeholder="例: 500 張"
              value={investmentTrustNetMinLots}
              onChange={e => { setInvestmentTrustNetMinLots(e.target.value); setPage(1) }}
              className="w-full bg-zinc-800 border border-zinc-700 rounded-md px-2.5 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-purple-500"
            />
          </div>

          <div>
            <label className="text-xs font-semibold text-zinc-400 block mb-1.5">自營商最低買超 (張)</label>
            <input
              type="number"
              placeholder="例: 100 張"
              value={dealerNetMinLots}
              onChange={e => { setDealerNetMinLots(e.target.value); setPage(1) }}
              className="w-full bg-zinc-800 border border-zinc-700 rounded-md px-2.5 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-purple-500"
            />
          </div>

          <div>
            <label className="text-xs font-semibold text-zinc-400 block mb-1.5">融資餘額最低增加 (張)</label>
            <input
              type="number"
              placeholder="例: 100 張"
              value={marginBalanceChangeMinLots}
              onChange={e => { setMarginBalanceChangeMinLots(e.target.value); setPage(1) }}
              className="w-full bg-zinc-800 border border-zinc-700 rounded-md px-2.5 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-purple-500"
            />
          </div>
        </div>

        {/* Row 4: Margin & Short Filters (融券餘額、券資比) */}
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <label className="text-xs font-semibold text-zinc-400 block mb-1.5">最低融券餘額 (張)</label>
            <input
              type="number"
              placeholder="例: 100 張"
              value={shortBalanceMinLots}
              onChange={e => { setShortBalanceMinLots(e.target.value); setPage(1) }}
              className="w-full bg-zinc-800 border border-zinc-700 rounded-md px-2.5 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-purple-500"
            />
          </div>

          <div>
            <label className="text-xs font-semibold text-zinc-400 block mb-1.5">券資比最低 (%)</label>
            <input
              type="number"
              placeholder="例: 10 (%)"
              value={shortMarginRatioMin}
              onChange={e => { setShortMarginRatioMin(e.target.value); setPage(1) }}
              className="w-full bg-zinc-800 border border-zinc-700 rounded-md px-2.5 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-purple-500"
            />
          </div>
        </div>

        {/* Row 5: Quick Toggles (MA & Near Limit) & Actions */}
        <div className="flex flex-wrap items-center justify-between gap-4 pt-2 border-t border-zinc-800/60">
          <div className="flex flex-wrap items-center gap-3">
            <button
              onClick={() => { setAboveMa5(prev => (prev === true ? null : true)); setPage(1) }}
              className={`px-3 py-1.5 text-xs font-medium rounded-md border transition-all ${
                aboveMa5 === true
                  ? 'bg-purple-600 border-purple-500 text-white'
                  : 'bg-zinc-800/80 border-zinc-700 text-zinc-300 hover:bg-zinc-700'
              }`}
            >
              站上 MA5
            </button>

            <button
              onClick={() => { setAboveMa20(prev => (prev === true ? null : true)); setPage(1) }}
              className={`px-3 py-1.5 text-xs font-medium rounded-md border transition-all ${
                aboveMa20 === true
                  ? 'bg-purple-600 border-purple-500 text-white'
                  : 'bg-zinc-800/80 border-zinc-700 text-zinc-300 hover:bg-zinc-700'
              }`}
            >
              站上 MA20 (月線)
            </button>

            <button
              onClick={() => { setNearUpperLimit(prev => !prev); setPage(1) }}
              className={`px-3 py-1.5 text-xs font-medium rounded-md border transition-all ${
                nearUpperLimit
                  ? 'bg-red-600 border-red-500 text-white'
                  : 'bg-zinc-800/80 border-zinc-700 text-zinc-300 hover:bg-zinc-700'
              }`}
            >
              逼近漲停 (&le; 3%)
            </button>

            <button
              onClick={() => { setNearLowerLimit(prev => !prev); setPage(1) }}
              className={`px-3 py-1.5 text-xs font-medium rounded-md border transition-all ${
                nearLowerLimit
                  ? 'bg-emerald-600 border-emerald-500 text-white'
                  : 'bg-zinc-800/80 border-zinc-700 text-zinc-300 hover:bg-zinc-700'
              }`}
            >
              逼近跌停 (&le; 3%)
            </button>
          </div>

          <button
            onClick={handleReset}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 rounded-md transition-colors"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            重置所有篩選
          </button>
        </div>
      </div>

      {/* Results Table Section */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden shadow-lg flex flex-col flex-1">
        {/* Table View */}
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs text-zinc-300">
            <thead className="bg-zinc-800/90 text-zinc-400 border-b border-zinc-700/80 sticky top-0">
              <tr>
                <th className="py-3 px-4 font-semibold cursor-pointer hover:text-zinc-100" onClick={() => handleSort('symbol')}>
                  <div className="flex items-center gap-1">
                    標的代碼
                    {sortBy === 'symbol' && (sortOrder === 'asc' ? <ArrowUp className="w-3 h-3 text-purple-400" /> : <ArrowDown className="w-3 h-3 text-purple-400" />)}
                  </div>
                </th>
                <th className="py-3 px-3 font-semibold">名稱</th>
                <th className="py-3 px-3 font-semibold">市場 / 型態</th>
                <th className="py-3 px-3 font-semibold cursor-pointer hover:text-zinc-100 text-right" onClick={() => handleSort('close')}>
                  <div className="flex items-center justify-end gap-1">
                    收盤價 (TWD)
                    {sortBy === 'close' && (sortOrder === 'asc' ? <ArrowUp className="w-3 h-3 text-purple-400" /> : <ArrowDown className="w-3 h-3 text-purple-400" />)}
                  </div>
                </th>
                <th className="py-3 px-3 font-semibold cursor-pointer hover:text-zinc-100 text-right" onClick={() => handleSort('change_pct')}>
                  <div className="flex items-center justify-end gap-1">
                    漲跌幅
                    {sortBy === 'change_pct' && (sortOrder === 'asc' ? <ArrowUp className="w-3 h-3 text-purple-400" /> : <ArrowDown className="w-3 h-3 text-purple-400" />)}
                  </div>
                </th>
                <th className="py-3 px-3 font-semibold cursor-pointer hover:text-zinc-100 text-right" onClick={() => handleSort('volume')}>
                  <div className="flex items-center justify-end gap-1">
                    成交量 (張)
                    {sortBy === 'volume' && (sortOrder === 'asc' ? <ArrowUp className="w-3 h-3 text-purple-400" /> : <ArrowDown className="w-3 h-3 text-purple-400" />)}
                  </div>
                </th>
                <th className="py-3 px-3 font-semibold cursor-pointer hover:text-zinc-100 text-right" onClick={() => handleSort('amount')}>
                  <div className="flex items-center justify-end gap-1">
                    成交金額
                    {sortBy === 'amount' && (sortOrder === 'asc' ? <ArrowUp className="w-3 h-3 text-purple-400" /> : <ArrowDown className="w-3 h-3 text-purple-400" />)}
                  </div>
                </th>
                <th className="py-3 px-3 font-semibold cursor-pointer hover:text-zinc-100 text-right" onClick={() => handleSort('foreign_net')}>
                  <div className="flex items-center justify-end gap-1">
                    外資買賣超
                    {sortBy === 'foreign_net' && (sortOrder === 'asc' ? <ArrowUp className="w-3 h-3 text-purple-400" /> : <ArrowDown className="w-3 h-3 text-purple-400" />)}
                  </div>
                </th>
                <th className="py-3 px-3 font-semibold cursor-pointer hover:text-zinc-100 text-right" onClick={() => handleSort('investment_trust_net')}>
                  <div className="flex items-center justify-end gap-1">
                    投信買賣超
                    {sortBy === 'investment_trust_net' && (sortOrder === 'asc' ? <ArrowUp className="w-3 h-3 text-purple-400" /> : <ArrowDown className="w-3 h-3 text-purple-400" />)}
                  </div>
                </th>
                <th className="py-3 px-3 font-semibold cursor-pointer hover:text-zinc-100 text-right" onClick={() => handleSort('dealer_net')}>
                  <div className="flex items-center justify-end gap-1">
                    自營商
                    {sortBy === 'dealer_net' && (sortOrder === 'asc' ? <ArrowUp className="w-3 h-3 text-purple-400" /> : <ArrowDown className="w-3 h-3 text-purple-400" />)}
                  </div>
                </th>
                <th className="py-3 px-3 font-semibold cursor-pointer hover:text-zinc-100 text-right" onClick={() => handleSort('margin_balance_change')}>
                  <div className="flex items-center justify-end gap-1">
                    融資變化
                    {sortBy === 'margin_balance_change' && (sortOrder === 'asc' ? <ArrowUp className="w-3 h-3 text-purple-400" /> : <ArrowDown className="w-3 h-3 text-purple-400" />)}
                  </div>
                </th>
                <th className="py-3 px-3 font-semibold cursor-pointer hover:text-zinc-100 text-right" onClick={() => handleSort('short_balance')}>
                  <div className="flex items-center justify-end gap-1">
                    融券餘額
                    {sortBy === 'short_balance' && (sortOrder === 'asc' ? <ArrowUp className="w-3 h-3 text-purple-400" /> : <ArrowDown className="w-3 h-3 text-purple-400" />)}
                  </div>
                </th>
                <th className="py-3 px-3 font-semibold cursor-pointer hover:text-zinc-100 text-right" onClick={() => handleSort('short_margin_ratio')}>
                  <div className="flex items-center justify-end gap-1">
                    券資比
                    {sortBy === 'short_margin_ratio' && (sortOrder === 'asc' ? <ArrowUp className="w-3 h-3 text-purple-400" /> : <ArrowDown className="w-3 h-3 text-purple-400" />)}
                  </div>
                </th>
                <th className="py-3 px-3 font-semibold text-right">漲跌停幅度</th>
                <th className="py-3 px-3 font-semibold text-right">MA5 / MA20</th>
                <th className="py-3 px-3 font-semibold cursor-pointer hover:text-zinc-100 text-right" onClick={() => handleSort('rsi_14')}>
                  <div className="flex items-center justify-end gap-1">
                    RSI (14)
                    {sortBy === 'rsi_14' && (sortOrder === 'asc' ? <ArrowUp className="w-3 h-3 text-purple-400" /> : <ArrowDown className="w-3 h-3 text-purple-400" />)}
                  </div>
                </th>
                <th className="py-3 px-3 font-semibold cursor-pointer hover:text-zinc-100 text-right" onClick={() => handleSort('momentum_5d')}>
                  <div className="flex items-center justify-end gap-1">
                    5日動量
                    {sortBy === 'momentum_5d' && (sortOrder === 'asc' ? <ArrowUp className="w-3 h-3 text-purple-400" /> : <ArrowDown className="w-3 h-3 text-purple-400" />)}
                  </div>
                </th>
                <th className="py-3 px-4 font-semibold text-center">操作</th>
              </tr>
            </thead>

            <tbody className="divide-y divide-zinc-800/60">
              {isLoading ? (
                <tr>
                  <td colSpan={18} className="py-12 text-center text-zinc-500">
                    <div className="flex items-center justify-center gap-2">
                      <div className="w-4 h-4 border-2 border-purple-500 border-t-transparent rounded-full animate-spin" />
                      <span>正在批次掃描篩選台股市場...</span>
                    </div>
                  </td>
                </tr>
              ) : isError ? (
                <tr>
                  <td colSpan={18} className="py-12 text-center text-red-400">
                    <AlertCircle className="w-6 h-6 mx-auto mb-2 text-red-500" />
                    <span>選股執行失敗: {String(error)}</span>
                  </td>
                </tr>
              ) : !data || data.items.length === 0 ? (
                <tr>
                  <td colSpan={18} className="py-12 text-center text-zinc-500">
                    <span>沒有符合當前條件的台股標的</span>
                  </td>
                </tr>
              ) : (
                data.items.map((item: ScreenerResultItem) => (
                  <tr key={item.symbol} className="hover:bg-zinc-800/40 transition-colors">
                    {/* Symbol */}
                    <td className="py-3 px-4 font-mono font-medium text-zinc-100">
                      <Link
                        to={`/stocks/${encodeURIComponent(item.symbol)}`}
                        className="text-purple-400 hover:underline hover:text-purple-300"
                      >
                        {item.symbol}
                      </Link>
                    </td>

                    {/* Name */}
                    <td className="py-3 px-3 text-zinc-200 font-medium whitespace-nowrap">{item.name}</td>

                    {/* Exchange & Instrument */}
                    <td className="py-3 px-3 whitespace-nowrap">
                      <span className="text-[10px] bg-zinc-800 text-zinc-400 px-1.5 py-0.5 rounded border border-zinc-700 mr-1.5">
                        {item.exchange}
                      </span>
                      <span className="text-[10px] bg-zinc-800/60 text-purple-400 px-1.5 py-0.5 rounded border border-purple-900/40">
                        {item.instrument_type === 'etf' ? 'ETF' : '股票'}
                      </span>
                    </td>

                    {/* Price */}
                    <td className="py-3 px-3 text-right font-mono text-zinc-100 font-semibold whitespace-nowrap">
                      {formatPrice(item.close)}
                    </td>

                    {/* Change Pct */}
                    <td className="py-3 px-3 text-right font-mono whitespace-nowrap">
                      {formatChangePct(item.change_pct)}
                    </td>

                    {/* Volume (Lots) */}
                    <td className="py-3 px-3 text-right font-mono text-zinc-300 whitespace-nowrap">
                      {formatVolumeLots(item.volume)}
                    </td>

                    {/* Amount */}
                    <td className="py-3 px-3 text-right font-mono text-zinc-400 whitespace-nowrap">
                      {formatAmount(item.amount)}
                    </td>

                    {/* Foreign Net (Lots) */}
                    <td className="py-3 px-3 text-right font-mono whitespace-nowrap">
                      {formatSignedSharesLots(item.foreign_net)}
                    </td>

                    {/* Investment Trust Net (Lots) */}
                    <td className="py-3 px-3 text-right font-mono whitespace-nowrap">
                      {formatSignedSharesLots(item.investment_trust_net)}
                    </td>

                    {/* Dealer Net (Lots) */}
                    <td className="py-3 px-3 text-right font-mono whitespace-nowrap">
                      {formatSignedSharesLots(item.dealer_net)}
                    </td>

                    {/* Margin Balance Change (Lots) */}
                    <td className="py-3 px-3 text-right font-mono whitespace-nowrap">
                      {formatSignedSharesLots(item.margin_balance_change)}
                    </td>

                    {/* Short Balance (Lots) */}
                    <td className="py-3 px-3 text-right font-mono whitespace-nowrap">
                      {formatShortBalanceLots(item.short_balance)}
                    </td>

                    {/* Short Margin Ratio */}
                    <td className="py-3 px-3 text-right font-mono whitespace-nowrap">
                      {formatShortMarginRatio(item.short_margin_ratio)}
                    </td>

                    {/* Price Limit */}
                    <td className="py-3 px-3 text-right font-mono whitespace-nowrap">
                      {item.is_no_limit ? (
                        <span className="text-zinc-500 text-[10px]">無限制</span>
                      ) : item.price_limit_pct ? (
                        <span className="text-zinc-400 text-xs">&plusmn;{(item.price_limit_pct * 100).toFixed(0)}%</span>
                      ) : (
                        '-'
                      )}
                    </td>

                    {/* MA5 / MA20 */}
                    <td className="py-3 px-3 text-right font-mono text-zinc-400 whitespace-nowrap">
                      <span>{item.ma5 ? item.ma5.toFixed(1) : '-'}</span>
                      <span className="text-zinc-600 mx-1">/</span>
                      <span>{item.ma20 ? item.ma20.toFixed(1) : '-'}</span>
                    </td>

                    {/* RSI 14 */}
                    <td className="py-3 px-3 text-right font-mono text-zinc-300 whitespace-nowrap">
                      {item.rsi_14 ? (
                        <span className={item.rsi_14 >= 70 ? 'text-red-400' : item.rsi_14 <= 30 ? 'text-emerald-400' : ''}>
                          {item.rsi_14.toFixed(1)}
                        </span>
                      ) : (
                        '-'
                      )}
                    </td>

                    {/* 5d Momentum */}
                    <td className="py-3 px-3 text-right font-mono whitespace-nowrap">
                      {formatChangePct(item.momentum_5d)}
                    </td>

                    {/* Action button */}
                    <td className="py-3 px-4 text-center whitespace-nowrap">
                      <Link
                        to={`/stocks/${encodeURIComponent(item.symbol)}`}
                        className="inline-flex items-center gap-1 text-[11px] bg-zinc-800 hover:bg-purple-600 text-zinc-300 hover:text-white px-2 py-1 rounded transition-colors"
                      >
                        <span>研究</span>
                        <ExternalLink className="w-3 h-3" />
                      </Link>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination Footer */}
        {data && data.total > 0 && (
          <div className="flex items-center justify-between px-4 py-3 bg-zinc-900 border-t border-zinc-800 text-xs text-zinc-400">
            <div>
              顯示第 {(page - 1) * pageSize + 1} 至 {Math.min(page * pageSize, data.total)} 筆，共 {data.total} 筆
            </div>

            <div className="flex items-center gap-2">
              <button
                onClick={() => setPage(p => Math.max(p - 1, 1))}
                disabled={page <= 1}
                className="p-1.5 rounded-md bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40 disabled:cursor-not-allowed text-zinc-300 transition-colors"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>

              <span className="px-2 font-mono">
                {page} / {totalPages}
              </span>

              <button
                onClick={() => setPage(p => Math.min(p + 1, totalPages))}
                disabled={page >= totalPages}
                className="p-1.5 rounded-md bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40 disabled:cursor-not-allowed text-zinc-300 transition-colors"
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

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
  Clock,
  CheckCircle2,
  AlertTriangle,
  Sparkles,
  Loader2,
  BarChart3,
  TrendingUp,
  TrendingDown,
  Layers,
} from 'lucide-react'
import {
  api,
  type TaiwanScreenerRequest,
  type ScreenerResultItem,
  type TaiwanScreenerTranslation,
} from '@/lib/api'

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

  // Natural-Language Translation State (Phase 6D)
  const [nlQuery, setNlQuery] = useState<string>('')
  const [nlTranslating, setNlTranslating] = useState<boolean>(false)
  const [nlTranslation, setNlTranslation] = useState<TaiwanScreenerTranslation | null>(null)
  const [nlError, setNlError] = useState<string | null>(null)

  const handleTranslate = async () => {
    if (!nlQuery.trim()) return
    setNlTranslating(true)
    setNlError(null)
    try {
      const res = await api.taiwanScreenerTranslate(nlQuery.trim())
      setNlTranslation(res)
    } catch (e: any) {
      setNlError(e?.message || '翻譯解析失敗，請確認 AI 模組設定')
    } finally {
      setNlTranslating(false)
    }
  }

  const handleApplyTranslation = () => {
    if (!nlTranslation || !nlTranslation.request) return
    const req = nlTranslation.request
    if (req.exchange) setExchange(req.exchange)
    if (req.instrument) setInstrument(req.instrument)
    if (req.industry !== undefined) setIndustry(req.industry || 'ALL')
    if (req.price_min !== undefined) setPriceMin(req.price_min !== null ? String(req.price_min) : '')
    if (req.price_max !== undefined) setPriceMax(req.price_max !== null ? String(req.price_max) : '')
    if (req.change_pct_min !== undefined) setChangePctMin(req.change_pct_min !== null ? String(req.change_pct_min * 100) : '')
    if (req.change_pct_max !== undefined) setChangePctMax(req.change_pct_max !== null ? String(req.change_pct_max * 100) : '')
    if (req.volume_min !== undefined) setVolumeMinLots(req.volume_min !== null ? String(req.volume_min / 1000) : '')
    if (req.amount_min !== undefined) setAmountMinMln(req.amount_min !== null ? String(req.amount_min / 1_000_000) : '')
    if (req.rsi_14_min !== undefined) setRsiMin(req.rsi_14_min !== null ? String(req.rsi_14_min) : '')
    if (req.rsi_14_max !== undefined) setRsiMax(req.rsi_14_max !== null ? String(req.rsi_14_max) : '')
    if (req.momentum_5d_min !== undefined) setMomentumMin(req.momentum_5d_min !== null ? String(req.momentum_5d_min * 100) : '')
    if (req.vol_ratio_5d_min !== undefined) setVolRatioMin(req.vol_ratio_5d_min !== null ? String(req.vol_ratio_5d_min) : '')
    if (req.above_ma5 !== undefined) setAboveMa5(req.above_ma5)
    if (req.above_ma20 !== undefined) setAboveMa20(req.above_ma20)
    if (req.near_upper_limit !== undefined) setNearUpperLimit(Boolean(req.near_upper_limit))
    if (req.near_lower_limit !== undefined) setNearLowerLimit(Boolean(req.near_lower_limit))
    if (req.foreign_net_min !== undefined) setForeignNetMinLots(req.foreign_net_min !== null ? String(req.foreign_net_min / 1000) : '')
    if (req.foreign_net_max !== undefined) setForeignNetMaxLots(req.foreign_net_max !== null ? String(req.foreign_net_max / 1000) : '')
    if (req.investment_trust_net_min !== undefined) setInvestmentTrustNetMinLots(req.investment_trust_net_min !== null ? String(req.investment_trust_net_min / 1000) : '')
    if (req.dealer_net_min !== undefined) setDealerNetMinLots(req.dealer_net_min !== null ? String(req.dealer_net_min / 1000) : '')
    if (req.margin_balance_change_min !== undefined) setMarginBalanceChangeMinLots(req.margin_balance_change_min !== null ? String(req.margin_balance_change_min / 1000) : '')
    if (req.short_balance_min !== undefined) setShortBalanceMinLots(req.short_balance_min !== null ? String(req.short_balance_min / 1000) : '')
    if (req.short_margin_ratio_min !== undefined) setShortMarginRatioMin(req.short_margin_ratio_min !== null ? String(req.short_margin_ratio_min) : '')
    setPage(1)
    setNlTranslation(null)
  }

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

  // Taiwan Data Freshness & Automation Status query (5-minute staleTime, no rapid polling)
  const {
    data: statusData,
    isLoading: isStatusLoading,
    isError: isStatusError,
  } = useQuery({
    queryKey: ['taiwanDataStatus'],
    queryFn: () => api.taiwanDataStatus(),
    staleTime: 5 * 60 * 1000,
  })

  // Taiwan Market Intelligence Snapshot query (Phase 7A)
  const { data: intelData } = useQuery({
    queryKey: ['taiwanMarketIntelligence'],
    queryFn: () => api.taiwanMarketIntelligence(),
    staleTime: 5 * 60 * 1000,
  })

  // Taiwan Industry Intelligence Snapshot query (Phase 7B)
  const [indSortBy, setIndSortBy] = useState<string>('turnover')
  const [indOrder, setIndOrder] = useState<'desc' | 'asc'>('desc')
  const { data: indData } = useQuery({
    queryKey: ['taiwanIndustryIntelligence', indSortBy, indOrder],
    queryFn: () => api.taiwanIndustryIntelligence({ sort_by: indSortBy, order: indOrder }),
    staleTime: 5 * 60 * 1000,
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

      {/* Data Operations Visibility Panel (Phase 6C) */}
      <div className="bg-zinc-900/90 border border-zinc-800 rounded-xl p-4 text-xs">
        <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-3">
          {/* Left: Overall Freshness & Datasets */}
          <div className="flex flex-wrap items-center gap-4">
            <div className="flex items-center gap-2 pr-2 border-r border-zinc-800">
              <Database className="w-4 h-4 text-purple-400" />
              <span className="font-semibold text-zinc-200">資料更新狀態</span>
              {isStatusLoading ? (
                <span className="text-zinc-500 flex items-center gap-1">
                  <Clock className="w-3.5 h-3.5 animate-spin" /> 載入中...
                </span>
              ) : isStatusError ? (
                <span className="text-amber-400 flex items-center gap-1">
                  <AlertTriangle className="w-3.5 h-3.5" /> 資料狀態暫時無法取得
                </span>
              ) : statusData ? (
                statusData.is_fully_current ? (
                  <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-emerald-950/80 text-emerald-400 border border-emerald-800/60">
                    <CheckCircle2 className="w-3 h-3" /> 資料已是最新
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-amber-950/80 text-amber-400 border border-amber-800/60">
                    <AlertCircle className="w-3 h-3" /> 資料尚未完全更新
                  </span>
                )
              ) : null}
            </div>

            {/* Individual Datasets Status */}
            {statusData && (
              <div className="flex flex-wrap items-center gap-3">
                {/* Daily OHLCV */}
                <div className="flex items-center gap-1.5">
                  <span className="text-zinc-400">日線:</span>
                  <span className="font-mono text-zinc-200">{statusData.daily_as_of || '—'}</span>
                  {statusData.daily_status === 'current' ? (
                    <span className="px-1.5 py-0.5 rounded bg-emerald-900/50 text-emerald-400 font-medium text-[10px]">最新</span>
                  ) : statusData.daily_status === 'stale' ? (
                    <span className="px-1.5 py-0.5 rounded bg-amber-900/50 text-amber-400 font-medium text-[10px]">
                      待更新{statusData.daily_days_behind > 0 ? ` (${statusData.daily_days_behind}日)` : ''}
                    </span>
                  ) : (
                    <span className="px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 text-[10px]">無資料</span>
                  )}
                </div>

                <span className="text-zinc-700">•</span>

                {/* Institutional */}
                <div className="flex items-center gap-1.5">
                  <span className="text-zinc-400">法人:</span>
                  <span className="font-mono text-zinc-200">{statusData.institutional_as_of || '—'}</span>
                  {statusData.institutional_status === 'current' ? (
                    <span className="px-1.5 py-0.5 rounded bg-emerald-900/50 text-emerald-400 font-medium text-[10px]">最新</span>
                  ) : statusData.institutional_status === 'stale' ? (
                    <span className="px-1.5 py-0.5 rounded bg-amber-900/50 text-amber-400 font-medium text-[10px]">
                      待更新{statusData.institutional_days_behind > 0 ? ` (${statusData.institutional_days_behind}日)` : ''}
                    </span>
                  ) : (
                    <span className="px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 text-[10px]">無資料</span>
                  )}
                </div>

                <span className="text-zinc-700">•</span>

                {/* Margin */}
                <div className="flex items-center gap-1.5">
                  <span className="text-zinc-400">資券:</span>
                  <span className="font-mono text-zinc-200">{statusData.margin_as_of || '—'}</span>
                  {statusData.margin_status === 'current' ? (
                    <span className="px-1.5 py-0.5 rounded bg-emerald-900/50 text-emerald-400 font-medium text-[10px]">最新</span>
                  ) : statusData.margin_status === 'stale' ? (
                    <span className="px-1.5 py-0.5 rounded bg-amber-900/50 text-amber-400 font-medium text-[10px]">
                      待更新{statusData.margin_days_behind > 0 ? ` (${statusData.margin_days_behind}日)` : ''}
                    </span>
                  ) : (
                    <span className="px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 text-[10px]">無資料</span>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Right: Target Date & Schedule Info */}
          {statusData && (
            <div className="flex items-center gap-3 text-zinc-400 shrink-0 text-[11px]">
              <div>
                應有最新交易日: <strong className="text-zinc-200 font-mono">{statusData.target_latest_trading_date}</strong>
              </div>
              <span className="text-zinc-700">|</span>
              <div className="flex items-center gap-1">
                <Clock className="w-3 h-3 text-purple-400" />
                <span>自動更新: <strong className="text-zinc-300">交易日 {statusData.scheduled_update_time}</strong> ({statusData.scheduled_timezone})</span>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Market Intelligence Snapshot Panel (Phase 7A) */}
      {intelData && (
        <div className="bg-zinc-900/80 border border-zinc-800/90 rounded-xl p-4 text-xs space-y-3">
          <div className="flex items-center justify-between border-b border-zinc-800 pb-2">
            <div className="flex items-center gap-2">
              <BarChart3 className="w-4 h-4 text-purple-400" />
              <span className="font-semibold text-zinc-200 text-sm">台股全市場量化統計快照 (Market Intelligence)</span>
              <span className="text-zinc-500 font-mono text-[11px]">交易日: {intelData.trade_date}</span>
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

      {/* Taiwan Industry / Sector Intelligence Panel (Phase 7B) */}
      {indData && (
        <div className="bg-zinc-900/80 border border-zinc-800/90 rounded-xl p-4 text-xs space-y-3">
          <div className="flex items-center justify-between border-b border-zinc-800 pb-2">
            <div className="flex items-center gap-2">
              <Layers className="w-4 h-4 text-cyan-400" />
              <span className="font-semibold text-zinc-200 text-sm">台股產業類股輪動 (Industry Intelligence)</span>
              <span className="text-zinc-500 font-mono text-[11px]">34 大類股統計 (點擊產業名稱可套用篩選)</span>
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

          <div className="overflow-x-auto max-h-72 overflow-y-auto">
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
                {indData.industries.map(ind => (
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
        </div>
      )}

      {/* Natural Language Translation Panel (Phase 6D) */}
      <div className="bg-zinc-900/80 border border-purple-900/40 rounded-xl p-4 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-purple-400" />
            <span className="font-semibold text-sm text-zinc-100">AI 自然語言條件解析 (選股條件轉換層)</span>
            <span className="text-[11px] text-zinc-500">純翻譯層：解析結果將映射至下方篩選表單，不直接出股</span>
          </div>
        </div>

        <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
          <input
            type="text"
            value={nlQuery}
            onChange={e => setNlQuery(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') handleTranslate() }}
            placeholder="例如：找外資買超 1000 張以上、券資比低於 5% 的股票，或是 100 元以下的 ETF"
            className="flex-1 bg-zinc-950 border border-zinc-800 focus:border-purple-500 rounded-lg px-3 py-2 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none transition-colors"
          />
          <button
            onClick={handleTranslate}
            disabled={nlTranslating || !nlQuery.trim()}
            className="px-4 py-2 bg-purple-600 hover:bg-purple-500 disabled:bg-zinc-800 disabled:text-zinc-500 text-white rounded-lg text-xs font-medium flex items-center justify-center gap-1.5 transition-colors shrink-0 shadow-sm"
          >
            {nlTranslating ? (
              <>
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                解析中...
              </>
            ) : (
              <>
                <Sparkles className="w-3.5 h-3.5" />
                解析條件
              </>
            )}
          </button>
        </div>

        {/* Translation Error */}
        {nlError && (
          <div className="text-xs text-amber-400 bg-amber-950/40 border border-amber-900/60 rounded-lg p-2.5 flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
            <div>
              <span className="font-medium">解析未成功：</span> {nlError}
            </div>
          </div>
        )}

        {/* Translation Preview Box */}
        {nlTranslation && (
          <div className="bg-zinc-950/90 border border-zinc-800 rounded-lg p-3 space-y-2.5 text-xs">
            <div className="flex items-center justify-between border-b border-zinc-800/80 pb-2">
              <span className="font-semibold text-zinc-200">條件解析預覽 (確認後填入表單)</span>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setNlTranslation(null)}
                  className="px-2 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-[11px] transition-colors"
                >
                  取消
                </button>
                {nlTranslation.request && (
                  <button
                    onClick={handleApplyTranslation}
                    className="px-3 py-1 rounded bg-purple-600 hover:bg-purple-500 text-white font-medium text-[11px] transition-colors shadow-sm"
                  >
                    套用條件至表單
                  </button>
                )}
              </div>
            </div>

            {/* Recognized Conditions */}
            {nlTranslation.recognized_conditions.length > 0 && (
              <div>
                <span className="text-zinc-400 block mb-1">已成功解析條件：</span>
                <div className="flex flex-wrap gap-1.5">
                  {nlTranslation.recognized_conditions.map((cond, idx) => (
                    <span
                      key={idx}
                      className="px-2 py-0.5 rounded bg-emerald-950/60 border border-emerald-800/50 text-emerald-300 text-[11px] font-mono"
                    >
                      ✓ {cond}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Unsupported Conditions */}
            {nlTranslation.unsupported_conditions.length > 0 && (
              <div>
                <span className="text-amber-400 block mb-1">未支援或無法量化條件：</span>
                <div className="flex flex-wrap gap-1.5">
                  {nlTranslation.unsupported_conditions.map((cond, idx) => (
                    <span
                      key={idx}
                      className="px-2 py-0.5 rounded bg-amber-950/60 border border-amber-800/50 text-amber-300 text-[11px]"
                    >
                      ⚠ {cond}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Clarification Message */}
            {nlTranslation.clarification_message && (
              <div className="text-zinc-400 bg-zinc-900/60 rounded p-2 text-[11px] border border-zinc-800">
                <span className="text-purple-400 font-medium">提示說明：</span> {nlTranslation.clarification_message}
              </div>
            )}
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

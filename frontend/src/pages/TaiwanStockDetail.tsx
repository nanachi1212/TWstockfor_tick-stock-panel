import { useState, useMemo } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  ArrowLeft,
  RefreshCw,
  RadioTower,
  Search,
  AlertTriangle,
  ShieldAlert,
  BarChart3,
  FileText,
  Sparkles,
  Loader2,
} from 'lucide-react'
import {
  api,
  type TaiwanSearchResult,
  type TaiwanAIStockResearchReport,
} from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import { TaiwanRuleEditorDialog } from '@/components/monitor/TaiwanRuleEditorDialog'
import { EChartsCandlestick, type OHLC } from '@/components/EChartsCandlestick'
import { TaiwanReferenceData } from '@/components/taiwan/TaiwanReferenceData'

const RANGE_OPTIONS = [
  { label: '1 個月', days: 30 },
  { label: '3 個月', days: 90 },
  { label: '6 個月', days: 180 },
  { label: '1 年', days: 360 },
]

export function TaiwanStockDetail() {
  const { symbol: routeSymbol } = useParams<{ symbol: string }>()
  const navigate = useNavigate()

  const rawSymbol = routeSymbol || '2330.TWSE'
  const symbol = rawSymbol.toUpperCase()

  const [selectedRange, setSelectedRange] = useState<number>(180)
  const [isRuleEditorOpen, setIsRuleEditorOpen] = useState<boolean>(false)
  const [volUnit, setVolUnit] = useState<'lots' | 'shares'>('lots')

  // Search state inside stock detail
  const [searchQuery, setSearchQuery] = useState('')
  const [isSearchOpen, setIsSearchOpen] = useState(false)

  // Query search results
  const searchQueryResult = useQuery({
    queryKey: QK.taiwanSearch(searchQuery),
    queryFn: () => api.taiwanSearch(searchQuery, 10),
    enabled: searchQuery.trim().length > 0,
    staleTime: 60_000,
  })

  // Query unified stock detail
  const detailQuery = useQuery({
    queryKey: QK.taiwanStockDetail(symbol, selectedRange),
    queryFn: () => api.taiwanStockDetail(symbol, selectedRange),
    refetchInterval: 10_000,
  })

  const currentDataQuery = useQuery({
    queryKey: QK.taiwanCurrentData(symbol),
    queryFn: () => api.taiwanCurrentData(symbol),
    staleTime: 5 * 60_000,
  })

  // Phase 7C: Structured Research Context Query
  const researchQuery = useQuery({
    queryKey: ['taiwanStockResearchContext', symbol],
    queryFn: () => api.taiwanStockResearchContext(symbol),
    staleTime: 5 * 60_000,
  })

  // Phase 7E: Grounded AI Stock Research Report state (MANUAL ONLY, NEVER AUTO-TRIGGER)
  const [aiReport, setAiReport] = useState<TaiwanAIStockResearchReport | null>(null)
  const [isAiLoading, setIsAiLoading] = useState<boolean>(false)
  const [aiError, setAiError] = useState<string | null>(null)

  const handleGenerateAiReport = async () => {
    setIsAiLoading(true)
    setAiError(null)
    try {
      const res = await api.taiwanStockAIResearch(symbol)
      if (res.status === 'success' && res.report) {
        setAiReport(res.report)
      } else {
        setAiError(res.error_message || 'AI 研究報告生成失敗')
      }
    } catch (e: any) {
      setAiError(e?.message || 'AI 服務調用失敗，請稍後重試')
    } finally {
      setIsAiLoading(false)
    }
  }

  const data = detailQuery.data
  const isLoading = detailQuery.isLoading
  const isError = detailQuery.isError

  // Format K-line rows for EChartsCandlestick
  const ohlcRows: OHLC[] = useMemo(() => {
    if (!data?.daily_history?.rows) return []
    return data.daily_history.rows.map(r => ({
      date: r.date,
      open: r.open,
      high: r.high,
      low: r.low,
      close: r.close,
      volume: r.volume,
    }))
  }, [data?.daily_history?.rows])

  // Price movement classes
  const isUp = (data?.realtime?.change || 0) > 0
  const isDown = (data?.realtime?.change || 0) < 0
  const priceColor = isUp ? 'text-rose-500' : isDown ? 'text-emerald-500' : 'text-foreground'
  const sign = isUp ? '+' : ''

  // Volume display helper
  const formatVol = (shares: number | null | undefined) => {
    if (shares == null) return '--'
    if (volUnit === 'lots') {
      const lots = Math.round(shares / 1000)
      return `${lots.toLocaleString()} 張`
    }
    return `${shares.toLocaleString()} 股`
  }

  return (
    <div className="flex flex-col min-h-screen bg-base text-foreground pb-12">
      {/* 頂部導航列與搜尋 */}
      <div className="sticky top-0 z-30 flex items-center justify-between border-b border-border bg-surface/90 px-4 py-2.5 backdrop-blur-md">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/monitor')}
            className="flex items-center gap-1.5 rounded-lg border border-border/80 bg-base px-2.5 py-1 text-xs font-medium text-muted hover:border-accent/50 hover:text-foreground transition-all cursor-pointer"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            <span>返回即時監控</span>
          </button>
          <div className="h-4 w-px bg-border" />
          <div className="flex items-center gap-2">
            <span className="text-xs font-mono font-bold text-accent">{symbol}</span>
            <span className="text-sm font-semibold">{data?.identity?.name || '--'}</span>
            <span className="rounded bg-elevated px-1.5 py-0.5 text-[10px] font-mono text-muted">
              {data?.identity?.exchange === 'TPEX' ? '上櫃' : '上市'}
            </span>
            <span className="rounded bg-accent/10 px-1.5 py-0.5 text-[10px] font-medium text-accent">
              {data?.identity?.instrument_type === 'etf' ? 'ETF' : '股票'}
            </span>
          </div>
        </div>

        {/* 搜尋列 */}
        <div className="relative flex items-center gap-2">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted" />
            <input
              type="text"
              value={searchQuery}
              onChange={e => {
                setSearchQuery(e.target.value)
                setIsSearchOpen(true)
              }}
              onFocus={() => setIsSearchOpen(true)}
              placeholder="搜尋台股代號或名稱..."
              className="w-48 sm:w-64 rounded-lg border border-border bg-base pl-8 pr-3 py-1 text-xs text-foreground placeholder:text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </div>

          {/* 搜尋結果下拉選單 */}
          {isSearchOpen && searchQuery.trim() && (
            <div className="absolute top-full right-0 mt-1.5 w-72 max-h-80 overflow-y-auto rounded-xl border border-border bg-elevated p-1.5 shadow-2xl z-50">
              <div className="flex items-center justify-between px-2 py-1 text-[11px] font-medium text-muted border-b border-border/50">
                <span>搜尋結果</span>
                <button
                  onClick={() => setIsSearchOpen(false)}
                  className="text-[10px] hover:text-foreground cursor-pointer"
                >
                  關閉
                </button>
              </div>
              {searchQueryResult.isLoading && (
                <div className="p-4 text-center text-xs text-muted">搜尋中...</div>
              )}
              {searchQueryResult.data?.results?.length === 0 && (
                <div className="p-4 text-center text-xs text-muted">查無符合標的</div>
              )}
              {searchQueryResult.data?.results?.map((item: TaiwanSearchResult) => (
                <button
                  key={item.symbol}
                  onClick={() => {
                    setIsSearchOpen(false)
                    setSearchQuery('')
                    navigate(`/stocks/${item.symbol}`)
                  }}
                  className="flex w-full items-center justify-between rounded-lg p-2 text-left hover:bg-surface transition-colors cursor-pointer"
                >
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs font-bold text-accent">{item.symbol}</span>
                    <span className="text-xs font-medium text-foreground">{item.name}</span>
                  </div>
                  <span className="text-[10px] text-muted">{item.exchange === 'TPEX' ? '上櫃' : '上市'}</span>
                </button>
              ))}
            </div>
          )}

          <button
            onClick={() => {
              detailQuery.refetch()
              currentDataQuery.refetch()
            }}
            disabled={detailQuery.isFetching || currentDataQuery.isFetching}
            className="flex items-center gap-1 rounded-lg border border-border bg-surface px-2 py-1 text-xs text-muted hover:text-foreground cursor-pointer transition-colors"
            title="手動重新整理"
          >
            <RefreshCw className={cn('h-3.5 w-3.5', (detailQuery.isFetching || currentDataQuery.isFetching) && 'animate-spin')} />
          </button>
        </div>
      </div>

      {/* 載入與錯誤處理 */}
      {isLoading && (
        <div className="flex flex-1 items-center justify-center p-24">
          <div className="flex flex-col items-center gap-3 text-muted">
            <RefreshCw className="h-6 w-6 animate-spin text-accent" />
            <span className="text-xs">載入 {symbol} 個股研究工作台資料中...</span>
          </div>
        </div>
      )}

      {isError && (
        <div className="m-6 rounded-xl border border-rose-500/30 bg-rose-500/10 p-6 text-center">
          <AlertTriangle className="mx-auto h-8 w-8 text-rose-500 mb-2" />
          <h3 className="text-sm font-bold text-rose-500">個股資料載入失敗</h3>
          <p className="text-xs text-muted mt-1">請確認標的代碼是否為標準台股規範 (如 2330.TWSE, 8069.TPEX)。</p>
          <button
            onClick={() => detailQuery.refetch()}
            className="mt-4 rounded-lg bg-accent px-4 py-1.5 text-xs font-medium text-white cursor-pointer"
          >
            重試連線
          </button>
        </div>
      )}

      {data && !isLoading && (
        <div className="px-4 py-4 space-y-4 max-w-7xl mx-auto w-full">
          {/* 未支援標的提示列 (例如權證/ETN) */}
          {!data.identity.is_supported && (
            <div className="flex items-center gap-2 rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-2.5 text-xs text-amber-400">
              <ShieldAlert className="h-4 w-4 shrink-0" />
              <span>本標的為權證、衍生品或未支援監控之標的，僅提供基本身份資訊與參考報價。</span>
            </div>
          )}

          {/* 區塊 1: 核心即時報價、身分與漲跌幅限制 Header */}
          <div className="rounded-2xl border border-border bg-surface p-4 shadow-sm">
            <div className="flex flex-wrap items-start justify-between gap-4">
              {/* 左側: 名稱、代號、價格 */}
              <div>
                <div className="flex items-center gap-2">
                  <h1 className="text-2xl font-bold tracking-tight text-foreground">{data.identity.name}</h1>
                  <span className="font-mono text-sm font-semibold text-accent">{data.symbol}</span>
                  <span className="rounded bg-elevated px-2 py-0.5 text-xs font-mono text-muted">
                    {data.identity.exchange === 'TPEX' ? 'TPEx 上櫃' : 'TWSE 上市'}
                  </span>
                  {data.identity.industry && (
                    <span className="rounded bg-surface px-2 py-0.5 text-xs text-muted border border-border">
                      {data.identity.industry}
                    </span>
                  )}
                  {data.identity.etf_category && (
                    <span className="rounded bg-accent/15 px-2 py-0.5 text-xs font-medium text-accent border border-accent/30">
                      ETF · {data.identity.etf_category}
                    </span>
                  )}
                </div>

                {/* 核心價格數字 */}
                <div className="mt-3 flex items-baseline gap-3">
                  <span className={cn('text-3xl font-mono font-bold tracking-tight', priceColor)}>
                    {data.realtime.last_price != null ? data.realtime.last_price.toFixed(2) : '--'}
                  </span>
                  {data.realtime.change != null && (
                    <div className={cn('flex items-center gap-1 text-sm font-mono font-semibold', priceColor)}>
                      <span>{sign}{data.realtime.change.toFixed(2)}</span>
                      <span>({sign}{data.realtime.change_pct?.toFixed(2)}%)</span>
                    </div>
                  )}
                  {data.realtime.meta?.status && (
                    <span className="rounded-full bg-elevated px-2.5 py-0.5 text-[11px] text-muted border border-border/80">
                      {data.realtime.meta.status === 'official_snapshot' ? '盤後快照' : '近即時行情'}
                    </span>
                  )}
                </div>
              </div>

              {/* 右側: 漲跌幅限制卡片與市場基準 */}
              <div className="flex flex-wrap items-center gap-3">
                {/* 漲跌幅卡片 */}
                <div className="flex flex-col rounded-xl border border-border/80 bg-base p-2.5 min-w-[140px]">
                  <span className="text-[10px] font-medium text-muted">漲跌幅限制規則</span>
                  <span className="text-xs font-bold text-foreground mt-0.5">
                    {data.price_limit.rule_type}
                  </span>
                  {!data.price_limit.is_no_limit && (
                    <div className="mt-1 flex items-center justify-between text-[11px] font-mono">
                      <span className="text-rose-500">漲停 {data.price_limit.limit_up?.toFixed(2) || '--'}</span>
                      <span className="text-emerald-500">跌停 {data.price_limit.limit_down?.toFixed(2) || '--'}</span>
                    </div>
                  )}
                  {data.price_limit.is_no_limit && (
                    <span className="mt-1 text-[10px] text-muted">此標的無漲跌幅限制</span>
                  )}
                </div>

                {/* 市場基準 Context 卡片 */}
                <div className="flex flex-col rounded-xl border border-border/80 bg-base p-2.5 min-w-[140px]">
                  <span className="text-[10px] font-medium text-muted">所屬大盤基準</span>
                  <span className="text-xs font-bold text-foreground mt-0.5">
                    {data.market_context.benchmark_name} ({data.market_context.benchmark_symbol})
                  </span>
                  <div className="mt-1 flex items-center justify-between text-[11px] font-mono">
                    <span>{data.market_context.close?.toFixed(2) || '--'}</span>
                    <span className={cn(
                      (data.market_context.change || 0) >= 0 ? 'text-rose-500' : 'text-emerald-500'
                    )}>
                      {(data.market_context.change || 0) >= 0 ? '+' : ''}
                      {data.market_context.change_pct?.toFixed(2) || '0.00'}%
                    </span>
                  </div>
                </div>

                {/* 操作按鈕 */}
                <div className="flex flex-col gap-1.5">
                  <button
                    onClick={() => setIsRuleEditorOpen(true)}
                    className="flex items-center justify-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white shadow-sm hover:bg-accent/90 transition-colors cursor-pointer"
                  >
                    <RadioTower className="h-3.5 w-3.5" />
                    <span>新增監控</span>
                  </button>
                  <Link
                    to={`/backtest?symbol=${encodeURIComponent(data.symbol)}`}
                    className="flex items-center justify-center gap-1.5 rounded-lg border border-border bg-elevated px-3 py-1.5 text-xs font-medium text-muted hover:text-foreground transition-colors"
                  >
                    <BarChart3 className="h-3.5 w-3.5" />
                    <span>查看回測</span>
                  </Link>
                </div>
              </div>
            </div>

            {/* 即時盤面數值 (昨收、開、高、低、成交量、金額) */}
            <div className="mt-4 grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-2 pt-3 border-t border-border/60 text-xs">
              <div>
                <span className="text-muted text-[11px]">昨收</span>
                <p className="font-mono font-medium mt-0.5">{data.realtime.prev_close?.toFixed(2) || '--'}</p>
              </div>
              <div>
                <span className="text-muted text-[11px]">今開</span>
                <p className="font-mono font-medium mt-0.5">{data.realtime.open?.toFixed(2) || '--'}</p>
              </div>
              <div>
                <span className="text-muted text-[11px]">最高</span>
                <p className="font-mono font-medium text-rose-500 mt-0.5">{data.realtime.high?.toFixed(2) || '--'}</p>
              </div>
              <div>
                <span className="text-muted text-[11px]">最低</span>
                <p className="font-mono font-medium text-emerald-500 mt-0.5">{data.realtime.low?.toFixed(2) || '--'}</p>
              </div>
              <div>
                <div className="flex items-center justify-between">
                  <span className="text-muted text-[11px]">成交量</span>
                  <button
                    onClick={() => setVolUnit(v => v === 'lots' ? 'shares' : 'lots')}
                    className="text-[10px] text-accent hover:underline cursor-pointer"
                  >
                    {volUnit === 'lots' ? '張' : '股'}
                  </button>
                </div>
                <p className="font-mono font-medium mt-0.5">{formatVol(data.realtime.volume)}</p>
              </div>
              <div>
                <span className="text-muted text-[11px]">行情時間</span>
                <p className="font-mono text-[11px] mt-0.5 truncate text-muted" title={data.realtime.quote_time || ''}>
                  {data.realtime.quote_time ? data.realtime.quote_time.split('T')[1]?.slice(0, 8) : '--'}
                </p>
              </div>
            </div>
          </div>

          {/* 區塊 2: K 線圖表與五檔盤口 (兩欄佈局) */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {/* 左側 2 欄: K 線圖表 */}
            <div className="lg:col-span-2 rounded-2xl border border-border bg-surface p-4">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <h3 className="text-sm font-bold text-foreground">歷史日 K 線與成交量</h3>
                  {data.daily_history.meta?.trade_date && (
                    <span className="text-[10px] text-muted font-mono">
                      資料截至 {data.daily_history.meta.trade_date}
                    </span>
                  )}
                </div>
                {/* 範圍按鈕 */}
                <div className="flex items-center gap-1 rounded-lg bg-base p-1 border border-border">
                  {RANGE_OPTIONS.map(opt => (
                    <button
                      key={opt.days}
                      onClick={() => setSelectedRange(opt.days)}
                      className={cn(
                        'rounded-md px-2 py-0.5 text-xs font-medium transition-all cursor-pointer',
                        selectedRange === opt.days
                          ? 'bg-accent text-white shadow-xs'
                          : 'text-muted hover:text-foreground'
                      )}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* K 線圖表主體 */}
              {ohlcRows.length > 0 ? (
                <div className="w-full h-[400px]">
                  <EChartsCandlestick
                    data={ohlcRows}
                    height={400}
                    showMA={true}
                    symbol={data.symbol}
                  />
                </div>
              ) : (
                <div className="flex h-[400px] items-center justify-center text-xs text-muted border border-dashed border-border rounded-xl">
                  暫無歷史 K 線資料
                </div>
              )}
            </div>

            {/* 右側 1 欄: 五檔即時盤口 */}
            <div className="rounded-2xl border border-border bg-surface p-4 flex flex-col justify-between">
              <div>
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-sm font-bold text-foreground">五檔即時盤口 (Level 2)</h3>
                  <span className="text-[10px] text-muted">
                    單位: {volUnit === 'lots' ? '張 (1,000股)' : '股'}
                  </span>
                </div>

                {/* 賣五檔 */}
                <div className="space-y-1 my-2">
                  {data.realtime.asks && data.realtime.asks.length > 0 ? (
                    data.realtime.asks.slice().reverse().map(([price, vol], idx) => (
                      <div key={`ask-${idx}`} className="flex items-center justify-between text-xs font-mono py-0.5 px-2 rounded bg-emerald-500/5">
                        <span className="text-muted text-[10px]">賣 {5 - idx}</span>
                        <span className="text-emerald-500 font-semibold">{price.toFixed(2)}</span>
                        <span className="text-muted">{formatVol(vol)}</span>
                      </div>
                    ))
                  ) : (
                    <div className="text-center py-4 text-xs text-muted">無賣盤報價</div>
                  )}
                </div>

                <div className="h-px bg-border my-2" />

                {/* 買五檔 */}
                <div className="space-y-1 my-2">
                  {data.realtime.bids && data.realtime.bids.length > 0 ? (
                    data.realtime.bids.map(([price, vol], idx) => (
                      <div key={`bid-${idx}`} className="flex items-center justify-between text-xs font-mono py-0.5 px-2 rounded bg-rose-500/5">
                        <span className="text-muted text-[10px]">買 {idx + 1}</span>
                        <span className="text-rose-500 font-semibold">{price.toFixed(2)}</span>
                        <span className="text-muted">{formatVol(vol)}</span>
                      </div>
                    ))
                  ) : (
                    <div className="text-center py-4 text-xs text-muted">無買盤報價</div>
                  )}
                </div>
              </div>

              {/* 來源與新鮮度 */}
              <div className="mt-4 pt-3 border-t border-border/60 text-[11px] text-muted flex items-center justify-between">
                <span>來源: {data.realtime.meta?.source || '官方即時資訊'}</span>
                <span>{data.realtime.meta?.is_stale ? '⚠️ 資料已過期' : '即時更新正常'}</span>
              </div>
            </div>
          </div>

          {/* 區塊 3: 三大法人、融資融券與因子卡片 */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* 1. 三大法人 */}
            <div className="rounded-2xl border border-border bg-surface p-4">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-bold text-foreground">三大法人買賣超</h3>
                {data.institutional.meta?.trade_date && (
                  <span className="text-[10px] text-muted font-mono">{data.institutional.meta.trade_date}</span>
                )}
              </div>
              {data.institutional.status === 'available' ? (
                <div className="space-y-2.5 text-xs">
                  <div className="flex items-center justify-between">
                    <span className="text-muted">外資及陸資</span>
                    <span className={cn('font-mono font-bold', (data.institutional.foreign_net || 0) >= 0 ? 'text-rose-500' : 'text-emerald-500')}>
                      {(data.institutional.foreign_net || 0) >= 0 ? '+' : ''}
                      {formatVol(data.institutional.foreign_net)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-muted">投信基金</span>
                    <span className={cn('font-mono font-bold', (data.institutional.investment_trust_net || 0) >= 0 ? 'text-rose-500' : 'text-emerald-500')}>
                      {(data.institutional.investment_trust_net || 0) >= 0 ? '+' : ''}
                      {formatVol(data.institutional.investment_trust_net)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-muted">自營商合計</span>
                    <span className={cn('font-mono font-bold', (data.institutional.dealer_net || 0) >= 0 ? 'text-rose-500' : 'text-emerald-500')}>
                      {(data.institutional.dealer_net || 0) >= 0 ? '+' : ''}
                      {formatVol(data.institutional.dealer_net)}
                    </span>
                  </div>
                  <div className="pt-2 border-t border-border flex items-center justify-between">
                    <span className="font-semibold text-foreground">三大法人合計</span>
                    <span className={cn('font-mono font-bold text-sm', (data.institutional.total_net || 0) >= 0 ? 'text-rose-500' : 'text-emerald-500')}>
                      {(data.institutional.total_net || 0) >= 0 ? '+' : ''}
                      {formatVol(data.institutional.total_net)}
                    </span>
                  </div>
                </div>
              ) : (
                <div className="py-8 text-center text-xs text-muted">目前暫無法人買賣超資料</div>
              )}
            </div>

            {/* 2. 融資融券 */}
            <div className="rounded-2xl border border-border bg-surface p-4">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-bold text-foreground">融資融券餘額與變化</h3>
                {data.margin.meta?.trade_date && (
                  <span className="text-[10px] text-muted font-mono">{data.margin.meta.trade_date}</span>
                )}
              </div>
              {data.margin.status === 'available' ? (
                <div className="space-y-2.5 text-xs">
                  <div className="flex items-center justify-between">
                    <span className="text-muted">融資餘額</span>
                    <span className="font-mono font-medium">{formatVol(data.margin.margin_balance)}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-muted">融資增減</span>
                    <span className={cn('font-mono font-bold', (data.margin.margin_change || 0) >= 0 ? 'text-rose-500' : 'text-emerald-500')}>
                      {(data.margin.margin_change || 0) >= 0 ? '+' : ''}
                      {formatVol(data.margin.margin_change)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-muted">融券餘額</span>
                    <span className="font-mono font-medium">{formatVol(data.margin.short_balance)}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-muted">融券增減</span>
                    <span className={cn('font-mono font-bold', (data.margin.short_change || 0) >= 0 ? 'text-rose-500' : 'text-emerald-500')}>
                      {(data.margin.short_change || 0) >= 0 ? '+' : ''}
                      {formatVol(data.margin.short_change)}
                    </span>
                  </div>
                  <div className="pt-2 border-t border-border flex items-center justify-between">
                    <span className="font-semibold text-foreground">券資比</span>
                    <span className="font-mono font-bold text-sm text-accent">
                      {data.margin.short_margin_ratio != null ? `${data.margin.short_margin_ratio.toFixed(2)}%` : '--'}
                    </span>
                  </div>
                </div>
              ) : (
                <div className="py-8 text-center text-xs text-muted">目前暫無資券籌碼資料</div>
              )}
            </div>

            {/* 3. 核心量化因子 */}
            <div className="rounded-2xl border border-border bg-surface p-4">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-bold text-foreground">核心籌碼滾動因子</h3>
                <span className="text-[10px] text-muted">近 5 日指標</span>
              </div>
              {data.factors.status === 'available' ? (
                <div className="space-y-2 text-xs">
                  <div className="rounded-lg bg-base p-2 border border-border/60">
                    <div className="flex items-center justify-between text-muted text-[11px]">
                      <span>外資 5 日累計</span>
                      <span>投信 5 日累計</span>
                    </div>
                    <div className="flex items-center justify-between font-mono font-bold mt-1">
                      <span className={(data.factors.foreign_net_5d || 0) >= 0 ? 'text-rose-500' : 'text-emerald-500'}>
                        {formatVol(data.factors.foreign_net_5d)}
                      </span>
                      <span className={(data.factors.investment_trust_net_5d || 0) >= 0 ? 'text-rose-500' : 'text-emerald-500'}>
                        {formatVol(data.factors.investment_trust_net_5d)}
                      </span>
                    </div>
                  </div>

                  <div className="rounded-lg bg-base p-2 border border-border/60">
                    <div className="flex items-center justify-between text-muted text-[11px]">
                      <span>自營商 5 日累計</span>
                      <span>融資變動量</span>
                    </div>
                    <div className="flex items-center justify-between font-mono font-bold mt-1">
                      <span className={(data.factors.dealer_net_5d || 0) >= 0 ? 'text-rose-500' : 'text-emerald-500'}>
                        {formatVol(data.factors.dealer_net_5d)}
                      </span>
                      <span className={(data.factors.margin_balance_change || 0) >= 0 ? 'text-rose-500' : 'text-emerald-500'}>
                        {formatVol(data.factors.margin_balance_change)}
                      </span>
                    </div>
                  </div>

                  <div className="rounded-lg bg-base p-2 border border-border/60 flex items-center justify-between">
                    <span className="text-muted text-[11px]">最新券資比因子</span>
                    <span className="font-mono font-bold text-accent">
                      {data.factors.short_margin_ratio != null ? `${data.factors.short_margin_ratio.toFixed(2)}%` : '--'}
                    </span>
                  </div>
                </div>
              ) : (
                <div className="py-8 text-center text-xs text-muted">目前暫無計算完成之因子</div>
              )}
            </div>
          </div>

          <TaiwanReferenceData
            response={currentDataQuery.data}
            isLoading={currentDataQuery.isLoading}
            isError={currentDataQuery.isError}
            isFetching={currentDataQuery.isFetching}
            onRetry={() => currentDataQuery.refetch()}
          />

          {/* 區塊 4: 監控規則與最近警報 */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* 監控規則列表 */}
            <div className="rounded-2xl border border-border bg-surface p-4">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <h3 className="text-sm font-bold text-foreground">標的監控規則</h3>
                  <span className="rounded-full bg-accent/15 px-2 py-0.5 text-[10px] font-bold text-accent">
                    {data.monitor_summary.rule_count} 條
                  </span>
                </div>
                <button
                  onClick={() => setIsRuleEditorOpen(true)}
                  className="text-xs text-accent hover:underline cursor-pointer"
                >
                  + 新增規則
                </button>
              </div>

              {data.monitor_summary.rules.length > 0 ? (
                <div className="space-y-2">
                  {data.monitor_summary.rules.map(r => (
                    <div key={r.rule_id} className="flex items-center justify-between rounded-xl bg-base p-2.5 border border-border/80 text-xs">
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-foreground">{r.name}</span>
                          <span className={cn('h-2 w-2 rounded-full', r.enabled ? 'bg-emerald-500' : 'bg-muted')} />
                        </div>
                        <p className="text-[11px] text-muted mt-0.5">門檻: {r.threshold}</p>
                      </div>
                      <span className="text-[10px] font-mono uppercase bg-elevated px-2 py-0.5 rounded text-muted">
                        {r.severity}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="py-8 text-center text-xs text-muted">
                  此標的尚未建立任何監控規則，點擊右上角新增。
                </div>
              )}
            </div>

            {/* 最近警報觸發紀錄 */}
            <div className="rounded-2xl border border-border bg-surface p-4">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-bold text-foreground">最近警報事件</h3>
                <span className="text-[10px] text-muted font-mono">近 7 日觸發記錄</span>
              </div>

              {data.recent_alerts.length > 0 ? (
                <div className="space-y-2 max-h-64 overflow-y-auto">
                  {data.recent_alerts.map(a => (
                    <div key={a.alert_id} className="rounded-xl bg-base p-2.5 border border-border/80 text-xs">
                      <div className="flex items-center justify-between">
                        <span className="font-semibold text-foreground">{a.rule_name}</span>
                        <span className="text-[10px] text-muted font-mono">{a.triggered_at?.slice(11, 19)}</span>
                      </div>
                      <p className="text-[11px] text-muted mt-1">{a.message}</p>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="py-8 text-center text-xs text-muted">
                  近期無任何警報觸發記錄。
                </div>
              )}
            </div>

            {/* 結構化個股研究證據上下文 (Phase 7C) */}
            {researchQuery.data && (
              <div className="rounded-2xl border border-border bg-surface p-4 space-y-3">
                <div className="flex items-center justify-between border-b border-border/60 pb-2">
                  <div className="flex items-center gap-2">
                    <FileText className="w-4 h-4 text-purple-400" />
                    <h3 className="text-sm font-bold text-foreground">結構化研究證據上下文</h3>
                    <span className="text-[10px] text-muted font-mono">
                      (狀態: {researchQuery.data.data_quality.overall_status === 'complete' ? '完整' : '部分'})
                    </span>
                  </div>
                  <span className="text-[10px] text-muted font-mono">
                    已認證事實: {researchQuery.data.evidence_summary.known_fields_count} 項 | 衍生計算: {researchQuery.data.evidence_summary.derived_fields_count} 項
                  </span>
                </div>

                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs font-mono">
                  <div className="bg-base p-2 rounded-lg border border-border/50">
                    <span className="text-[10px] text-muted block">5日報酬率</span>
                    <span className={researchQuery.data.price_context.return_5d !== null ? (researchQuery.data.price_context.return_5d > 0 ? 'text-up font-semibold' : researchQuery.data.price_context.return_5d < 0 ? 'text-down font-semibold' : 'text-foreground') : 'text-muted'}>
                      {researchQuery.data.price_context.return_5d !== null ? `${(researchQuery.data.price_context.return_5d * 100).toFixed(2)}%` : '—'}
                    </span>
                  </div>
                  <div className="bg-base p-2 rounded-lg border border-border/50">
                    <span className="text-[10px] text-muted block">20日報酬率</span>
                    <span className={researchQuery.data.price_context.return_20d !== null ? (researchQuery.data.price_context.return_20d > 0 ? 'text-up font-semibold' : researchQuery.data.price_context.return_20d < 0 ? 'text-down font-semibold' : 'text-foreground') : 'text-muted'}>
                      {researchQuery.data.price_context.return_20d !== null ? `${(researchQuery.data.price_context.return_20d * 100).toFixed(2)}%` : '—'}
                    </span>
                  </div>
                  <div className="bg-base p-2 rounded-lg border border-border/50">
                    <span className="text-[10px] text-muted block">距 MA20 乖離</span>
                    <span className={researchQuery.data.technical_context.distance_to_ma20 !== null ? (researchQuery.data.technical_context.distance_to_ma20 > 0 ? 'text-up' : researchQuery.data.technical_context.distance_to_ma20 < 0 ? 'text-down' : 'text-foreground') : 'text-muted'}>
                      {researchQuery.data.technical_context.distance_to_ma20 !== null ? `${(researchQuery.data.technical_context.distance_to_ma20 * 100).toFixed(2)}%` : '—'}
                    </span>
                  </div>
                  <div className="bg-base p-2 rounded-lg border border-border/50">
                    <span className="text-[10px] text-muted block">5日均量比</span>
                    <span className="text-foreground">
                      {researchQuery.data.technical_context.vol_ratio_5d !== null ? `${researchQuery.data.technical_context.vol_ratio_5d}x` : '—'}
                    </span>
                  </div>
                </div>

                {researchQuery.data.industry_context.industry && (
                  <div className="bg-base p-2.5 rounded-lg border border-border/50 text-xs flex items-center justify-between">
                    <div>
                      <span className="text-muted text-[11px]">產業輪動背景: </span>
                      <span className="font-semibold text-foreground">{researchQuery.data.industry_context.industry}</span>
                    </div>
                    <div className="flex gap-4 font-mono text-[11px]">
                      <span>成交佔比: {researchQuery.data.industry_context.turnover_share !== null ? `${(researchQuery.data.industry_context.turnover_share * 100).toFixed(1)}%` : '—'}</span>
                      <span>5D RS: {researchQuery.data.industry_context.relative_strength_5d !== null ? `${(researchQuery.data.industry_context.relative_strength_5d * 100).toFixed(2)}%` : '—'}</span>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Phase 7E: Grounded AI Stock Research Report (客觀事實解讀，無買賣推薦) */}
      <div className="bg-surface border border-purple-900/40 rounded-xl p-5 shadow-sm space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-border/40 pb-3">
          <div className="flex items-center gap-2.5">
            <Sparkles className="w-5 h-5 text-purple-400" />
            <div>
              <h3 className="font-semibold text-sm text-foreground flex items-center gap-2">
                AI 客觀研究報告 (Grounded Research Interpretation)
                <span className="text-[10px] bg-purple-950/60 border border-purple-800 text-purple-300 px-2 py-0.5 rounded font-mono">
                  PROMPT v1 (無買賣推薦)
                </span>
              </h3>
              <p className="text-[11px] text-muted">
                僅依據上方結構化事實證據與異常診斷進行事實摘要與客觀解讀，絕不自創事實或提供進出場操作建議
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={handleGenerateAiReport}
            disabled={isAiLoading}
            className={cn(
              "px-3.5 py-1.5 rounded-lg text-xs font-medium flex items-center justify-center gap-1.5 transition-colors border",
              isAiLoading
                ? "bg-purple-950/40 border-purple-800/40 text-purple-400 cursor-not-allowed"
                : "bg-purple-600 hover:bg-purple-500 text-white border-purple-500 shadow-sm"
            )}
          >
            {isAiLoading ? (
              <>
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                正在客觀分析中...
              </>
            ) : (
              <>
                <Sparkles className="w-3.5 h-3.5" />
                {aiReport ? '重新生成 AI 研究報告' : '生成 AI 研究報告'}
              </>
            )}
          </button>
        </div>

        {aiError && (
          <div className="p-3 bg-red-950/30 border border-red-900/50 rounded-lg text-xs text-red-400 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0 text-red-400" />
            <span>{aiError}</span>
          </div>
        )}

        {!aiReport && !isAiLoading && !aiError && (
          <div className="py-8 text-center text-muted text-xs border border-dashed border-border/40 rounded-xl bg-base/30">
            <Sparkles className="w-8 h-8 mx-auto mb-2 text-purple-400/40" />
            <p className="font-medium text-foreground">尚未生成 AI 研究報告</p>
            <p className="text-[11px] text-muted mt-1">點擊上方「生成 AI 研究報告」按鈕以進行封閉事實邊界之客觀解讀 (不自動呼叫)</p>
          </div>
        )}

        {aiReport && (
          <div className="space-y-4 text-xs">
            {/* Overview */}
            <div className="bg-base/60 p-3.5 rounded-lg border border-border/50">
              <span className="text-[11px] font-semibold text-purple-300 block mb-1">【研究摘要】</span>
              <p className="text-foreground leading-relaxed text-xs">{aiReport.overview}</p>
            </div>

            {/* Sectional Interpretations Grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {aiReport.market_interpretation && (
                <div className="bg-base/40 p-3 rounded-lg border border-border/40">
                  <span className="text-[11px] font-semibold text-muted block mb-1">大盤環境解讀</span>
                  <p className="text-foreground text-[11px] leading-normal">{aiReport.market_interpretation}</p>
                </div>
              )}
              {aiReport.industry_interpretation && (
                <div className="bg-base/40 p-3 rounded-lg border border-border/40">
                  <span className="text-[11px] font-semibold text-muted block mb-1">產業輪動解讀</span>
                  <p className="text-foreground text-[11px] leading-normal">{aiReport.industry_interpretation}</p>
                </div>
              )}
              {aiReport.price_technical_interpretation && (
                <div className="bg-base/40 p-3 rounded-lg border border-border/40">
                  <span className="text-[11px] font-semibold text-muted block mb-1">量價與技術位階解讀</span>
                  <p className="text-foreground text-[11px] leading-normal">{aiReport.price_technical_interpretation}</p>
                </div>
              )}
              {aiReport.institutional_interpretation && (
                <div className="bg-base/40 p-3 rounded-lg border border-border/40">
                  <span className="text-[11px] font-semibold text-muted block mb-1">三大法人籌碼解讀</span>
                  <p className="text-foreground text-[11px] leading-normal">{aiReport.institutional_interpretation}</p>
                </div>
              )}
              {aiReport.margin_interpretation && (
                <div className="bg-base/40 p-3 rounded-lg border border-border/40">
                  <span className="text-[11px] font-semibold text-muted block mb-1">融資融券信用交易解讀</span>
                  <p className="text-foreground text-[11px] leading-normal">{aiReport.margin_interpretation}</p>
                </div>
              )}
              {aiReport.fundamentals_interpretation && (
                <div className="bg-base/40 p-3 rounded-lg border border-border/40">
                  <span className="text-[11px] font-semibold text-muted block mb-1">基本面 / 財務營收解讀</span>
                  <p className="text-foreground text-[11px] leading-normal">{aiReport.fundamentals_interpretation}</p>
                </div>
              )}
              {aiReport.abnormal_diagnostics_interpretation && (
                <div className="bg-base/40 p-3 rounded-lg border border-border/40 md:col-span-2">
                  <span className="text-[11px] font-semibold text-muted block mb-1">異常異動與資金流向解讀</span>
                  <p className="text-foreground text-[11px] leading-normal">{aiReport.abnormal_diagnostics_interpretation}</p>
                </div>
              )}
            </div>

            {/* Key Observations with Evidence Chips */}
            {aiReport.key_observations && aiReport.key_observations.length > 0 && (
              <div className="bg-base/40 p-3.5 rounded-lg border border-border/40 space-y-2">
                <span className="text-[11px] font-semibold text-emerald-400 block">【重點客觀觀察 (Grounded Observations)】</span>
                <div className="space-y-2">
                  {aiReport.key_observations.map((obs, idx) => (
                    <div key={idx} className="flex flex-col gap-1 bg-surface/60 p-2 rounded border border-border/30">
                      <span className="text-foreground text-xs leading-normal">{obs.text}</span>
                      <div className="flex flex-wrap gap-1 mt-0.5">
                        {obs.evidence_refs.map((ref, rIdx) => (
                          <span key={rIdx} className="text-[10px] font-mono px-1.5 py-0.2 bg-emerald-950/40 border border-emerald-800/40 text-emerald-300 rounded">
                            證據: {ref}
                          </span>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Risk Factors with Evidence Chips */}
            {aiReport.risk_factors && aiReport.risk_factors.length > 0 && (
              <div className="bg-base/40 p-3.5 rounded-lg border border-border/40 space-y-2">
                <span className="text-[11px] font-semibold text-amber-400 block">【數據揭示之風險特徵 (Risk Evidence)】</span>
                <div className="space-y-2">
                  {aiReport.risk_factors.map((rsk, idx) => (
                    <div key={idx} className="flex flex-col gap-1 bg-surface/60 p-2 rounded border border-border/30">
                      <span className="text-foreground text-xs leading-normal">{rsk.text}</span>
                      <div className="flex flex-wrap gap-1 mt-0.5">
                        {rsk.evidence_refs.map((ref, rIdx) => (
                          <span key={rIdx} className="text-[10px] font-mono px-1.5 py-0.2 bg-amber-950/40 border border-amber-800/40 text-amber-300 rounded">
                            依據: {ref}
                          </span>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Deterministic Missing Information */}
            {aiReport.missing_information && aiReport.missing_information.length > 0 && (
              <div className="bg-base/30 p-2.5 rounded-lg border border-border/30 text-[11px] text-muted">
                <span className="font-semibold text-zinc-400 block mb-1">【系統數據覆蓋度說明】:</span>
                <ul className="list-disc list-inside space-y-0.5 font-mono text-[10px]">
                  {aiReport.missing_information.map((item, idx) => (
                    <li key={idx} className="text-zinc-400">{item}</li>
                  ))}
                </ul>
              </div>
            )}

            {/* Disclaimer */}
            <div className="p-2.5 bg-zinc-950/40 border border-zinc-800/60 rounded text-[10px] text-zinc-500 font-sans text-center">
              {aiReport.disclaimer}
            </div>
          </div>
        )}
      </div>

      {/* 新增台股監控規則彈窗 */}
      <TaiwanRuleEditorDialog
        open={isRuleEditorOpen}
        rule={null}
        presetSymbol={symbol}
        onClose={() => {
          setIsRuleEditorOpen(false)
          detailQuery.refetch()
        }}
      />
    </div>
  )
}

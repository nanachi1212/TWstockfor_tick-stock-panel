import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  AlertCircle,
  Clock,
  Layers,
  TrendingDown,
  TrendingUp,
} from 'lucide-react'
import { api, type TaiwanRealtimeQuote, type TaiwanSourceMeta } from '@/lib/api'

import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'

interface TaiwanQuotePanelProps {
  onSelectSymbol?: (symbol: string) => void
  onAddRuleForSymbol?: (quote: TaiwanRealtimeQuote) => void
}

const PRESET_SYMBOLS = [
  '2330.TWSE',
  '0050.TWSE',
  '00631L.TWSE',
  '00632R.TWSE',
  '00646.TWSE',
  '8069.TPEX',
]

/** 格式化金額為易讀的 萬 / 億 或原始數值 */
function formatAmount(amt: number | null | undefined): string {
  if (amt == null) return '--'
  if (Math.abs(amt) >= 100_000_000) {
    return `${(amt / 100_000_000).toFixed(2)} 億`
  }
  if (Math.abs(amt) >= 10_000) {
    return `${(amt / 10_000).toFixed(2)} 萬`
  }
  return amt.toLocaleString()
}

/** 格式化股數 (可選擇切換顯示 張數 = 股數 / 1000) */
function formatVolume(shares: number | null | undefined, unit: 'shares' | 'lots'): string {
  if (shares == null) return '--'
  if (unit === 'lots') {
    const lots = (shares / 1000).toFixed(shares % 1000 === 0 ? 0 : 2)
    return `${Number(lots).toLocaleString()} 張`
  }
  return `${shares.toLocaleString()} 股`
}

/** 格式化時間 (HH:mm:ss 或 上一交易日 13:30) */
function formatQuoteTime(timeStr: string | null | undefined, isFallback: boolean): string {
  if (!timeStr) return isFallback ? '上一交易日 13:30' : '時間不可用'
  try {
    const d = new Date(timeStr)
    if (isNaN(d.getTime())) return timeStr
    const hh = String(d.getHours()).padStart(2, '0')
    const mm = String(d.getMinutes()).padStart(2, '0')
    const ss = String(d.getSeconds()).padStart(2, '0')
    return `${hh}:${mm}:${ss}`
  } catch {
    return timeStr
  }
}

/** 台灣市場狀態徽章 */
export function MarketStatusBadge({ status }: { status: string | undefined }) {
  const s = (status || '').toLowerCase()
  let text = '休市'
  let cls = 'bg-surface border-border text-muted'

  if (s === 'open') {
    text = '開盤'
    cls = 'bg-emerald-500/10 border-emerald-500/30 text-emerald-600 dark:text-emerald-400'
  } else if (s === 'pre_open') {
    text = '盤前'
    cls = 'bg-amber-500/10 border-amber-500/30 text-amber-600 dark:text-amber-400'
  } else if (s === 'post_close') {
    text = '盤後'
    cls = 'bg-sky-500/10 border-sky-500/30 text-sky-600 dark:text-sky-400'
  } else if (s === 'scheduled_open_unverified') {
    text = '預定開盤・尚未確認'
    cls = 'bg-amber-500/15 border-amber-500/40 text-amber-600 dark:text-amber-300 font-medium'
  } else if (s === 'non_trading_day') {
    text = '休市 (非交易日)'
    cls = 'bg-surface border-border text-muted'
  }

  return (
    <span className={cn('inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium border', cls)}>
      <span className={cn('h-1.5 w-1.5 rounded-full', s === 'open' ? 'bg-emerald-500 animate-pulse' : 'bg-current opacity-60')} />
      {text}
    </span>
  )
}

/** 台灣資料品質徽章 (帶 tooltip 詳細資訊) */
export function DataQualityBadge({ meta }: { meta: TaiwanSourceMeta | undefined }) {
  if (!meta) return null

  let label = '未知來源'
  let color = 'bg-surface text-muted border-border'

  if (meta.is_stale) {
    label = '資料過期'
    color = 'bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/30'
  } else if (meta.status === 'daily_fallback' || meta.source_type === 'local_store') {
    label = '日線備援'
    color = 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30'
  } else if (meta.freshness_class === 'delayed_15m' || meta.freshness_class.includes('delayed')) {
    label = '延遲 15m'
    color = 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30'
  } else if (meta.freshness_class === 'eod_snapshot') {
    label = '盤後快照'
    color = 'bg-sky-500/10 text-sky-600 dark:text-sky-400 border-sky-500/30'
  } else if (meta.freshness_class === 'best_effort_near_realtime') {
    label = '近即時'
    color = 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30'
  } else if (meta.is_realtime) {
    label = '即時'
    color = 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30 font-medium'
  }

  const tooltip = `來源: ${meta.source} (${meta.source_type})\n等級: ${meta.freshness_class}\n抓取時間: ${meta.fetched_at || '--'}${meta.fallback_reason ? `\n備援原因: ${meta.fallback_reason}` : ''}`

  return (
    <span
      title={tooltip}
      className={cn('cursor-help inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] border', color)}
    >
      <Activity className="h-2.5 w-2.5" />
      {label}
    </span>
  )
}

/** 五檔盤口卡片 (Level 2 Order Book) */
function FiveLevelBook({
  bids = [],
  asks = [],
  prevClose,
  unit,
}: {
  bids?: [number, number][]
  asks?: [number, number][]
  prevClose?: number | null
  unit: 'shares' | 'lots'
}) {
  const paddedAsks = [...asks].slice(0, 5).reverse() // 賣五到賣一
  const paddedBids = [...bids].slice(0, 5)            // 買一到買五

  // 找五檔中最大量做條狀背景
  const maxVol = Math.max(
    ...paddedAsks.map(([, v]) => v || 0),
    ...paddedBids.map(([, v]) => v || 0),
    1
  )

  const getPriceClass = (p: number) => {
    if (!prevClose) return 'text-foreground'
    if (p > prevClose) return 'text-rose-500 font-medium'
    if (p < prevClose) return 'text-emerald-500 font-medium'
    return 'text-foreground/80'
  }

  return (
    <div className="rounded-lg border border-border/50 bg-elevated/40 p-2 text-xs">
      <div className="mb-1 flex items-center justify-between text-[10px] text-muted font-medium px-1">
        <span>檔位</span>
        <span>價格 (TWD)</span>
        <span>掛單 ({unit === 'lots' ? '張' : '股'})</span>
      </div>

      {/* 賣五 ~ 賣一 */}
      <div className="space-y-0.5 mb-1.5">
        {paddedAsks.map(([p, v], i) => {
          const depthIdx = 5 - i
          const widthPct = Math.min(100, Math.round(((v || 0) / maxVol) * 100))
          return (
            <div key={`ask-${depthIdx}`} className="relative flex items-center justify-between px-1 py-0.5 rounded overflow-hidden">
              <div
                className="absolute right-0 top-0 bottom-0 bg-emerald-500/10 dark:bg-emerald-500/15 pointer-events-none transition-all duration-300"
                style={{ width: `${widthPct}%` }}
              />
              <span className="text-[10px] text-muted z-10">賣{depthIdx}</span>
              <span className={cn('z-10 font-mono text-xs', getPriceClass(p))}>{p != null ? p.toFixed(2) : '--'}</span>
              <span className="z-10 font-mono text-[11px] text-foreground/80">
                {formatVolume(v, unit)}
              </span>
            </div>
          )
        })}
      </div>

      <div className="my-1 border-t border-dashed border-border/60" />

      {/* 買一 ~ 買五 */}
      <div className="space-y-0.5">
        {paddedBids.map(([p, v], i) => {
          const depthIdx = i + 1
          const widthPct = Math.min(100, Math.round(((v || 0) / maxVol) * 100))
          return (
            <div key={`bid-${depthIdx}`} className="relative flex items-center justify-between px-1 py-0.5 rounded overflow-hidden">
              <div
                className="absolute right-0 top-0 bottom-0 bg-rose-500/10 dark:bg-rose-500/15 pointer-events-none transition-all duration-300"
                style={{ width: `${widthPct}%` }}
              />
              <span className="text-[10px] text-muted z-10">買{depthIdx}</span>
              <span className={cn('z-10 font-mono text-xs', getPriceClass(p))}>{p != null ? p.toFixed(2) : '--'}</span>
              <span className="z-10 font-mono text-[11px] text-foreground/80">
                {formatVolume(v, unit)}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export function TaiwanQuotePanel({ onSelectSymbol, onAddRuleForSymbol }: TaiwanQuotePanelProps) {
  const [selectedSymbol, setSelectedSymbol] = useState<string>('2330.TWSE')
  const [volUnit, setVolUnit] = useState<'shares' | 'lots'>('lots')

  // 1. 即時行情 Query (受 QK.taiwanQuotes 驅動，SSE 廣播時自動 Invalidate)
  const quotesQuery = useQuery({
    queryKey: QK.taiwanQuotes(PRESET_SYMBOLS.join(',')),
    queryFn: () => api.taiwanQuotes(PRESET_SYMBOLS),
    staleTime: 3000,
  })


  const quotes = quotesQuery.data?.quotes || []
  const currentQuote = quotes.find(q => q.symbol === selectedSymbol) || quotes[0]

  const isUp = (currentQuote?.change || 0) > 0
  const isDown = (currentQuote?.change || 0) < 0
  const colorCls = isUp ? 'text-rose-500 dark:text-rose-400' : isDown ? 'text-emerald-500 dark:text-emerald-400' : 'text-foreground'
  const bgCls = isUp ? 'bg-rose-500/10' : isDown ? 'bg-emerald-500/10' : 'bg-surface'

  return (
    <div className="flex flex-col h-full space-y-3">
      {/* 頂部: 標的切換與搜尋列 */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1">
          {PRESET_SYMBOLS.map(sym => {
            const q = quotes.find(item => item.symbol === sym)
            const active = sym === selectedSymbol
            const qUp = (q?.change || 0) > 0
            const qDown = (q?.change || 0) < 0
            return (
              <button
                key={sym}
                onClick={() => {
                  setSelectedSymbol(sym)
                  onSelectSymbol?.(sym)
                }}
                className={cn(
                  'rounded-lg px-2.5 py-1 text-xs font-medium transition-all border cursor-pointer',
                  active
                    ? 'border-accent bg-accent/15 text-accent shadow-sm'
                    : 'border-border/60 bg-surface text-muted hover:border-accent/40 hover:text-foreground'
                )}
              >
                <div className="flex items-center gap-1.5">
                  <span>{q?.name || sym.split('.')[0]}</span>
                  <span className="text-[10px] opacity-70">{sym.split('.')[1]}</span>
                  {q?.last_price != null && (
                    <span className={cn('text-[11px] font-mono', qUp ? 'text-rose-500' : qDown ? 'text-emerald-500' : 'text-muted')}>
                      {q.last_price.toFixed(1)}
                    </span>
                  )}
                </div>
              </button>
            )
          })}
        </div>

        {/* 單位切換: 股 / 張 */}
        <div className="flex items-center gap-1 bg-elevated/40 rounded-lg p-0.5 border border-border/50">
          <button
            onClick={() => setVolUnit('lots')}
            className={cn('px-2 py-0.5 text-[10px] rounded font-medium cursor-pointer', volUnit === 'lots' ? 'bg-accent text-accent-foreground' : 'text-muted hover:text-foreground')}
          >
            張 (1,000股)
          </button>
          <button
            onClick={() => setVolUnit('shares')}
            className={cn('px-2 py-0.5 text-[10px] rounded font-medium cursor-pointer', volUnit === 'shares' ? 'bg-accent text-accent-foreground' : 'text-muted hover:text-foreground')}
          >
            原始股數
          </button>
        </div>
      </div>

      {/* 主面板內容 */}
      {currentQuote ? (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 flex-1 min-h-0">
          {/* 左區: 即時報價與核心統計 */}
          <div className="md:col-span-2 flex flex-col justify-between rounded-xl border border-border/70 bg-surface/80 p-4 shadow-sm">
            <div>
              {/* 股票名稱、市場、代號、狀態徽章 */}
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/40 pb-3">
                <div className="flex items-center gap-2">
                  <span className="text-lg font-bold text-foreground">{currentQuote.name}</span>
                  <span className="font-mono text-sm font-semibold text-accent">{currentQuote.symbol}</span>
                  <span className="rounded bg-elevated px-1.5 py-0.5 text-[10px] font-medium text-secondary">
                    {currentQuote.exchange === 'TWSE' ? '上市 (TWSE)' : '上櫃 (TPEx)'}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <MarketStatusBadge status={currentQuote.market_status} />
                  <DataQualityBadge meta={currentQuote.source_meta} />
                  <span className="flex items-center gap-1 text-[11px] text-muted font-mono" title="台灣時間 (Asia/Taipei)">
                    <Clock className="h-3 w-3" />
                    {formatQuoteTime(currentQuote.quote_time, currentQuote.source_meta?.status === 'daily_fallback')}
                  </span>
                </div>
              </div>

              {/* 現價與漲跌幅 Banner */}
              <div className="my-3 flex flex-wrap items-baseline gap-4">
                <span className={cn('text-3xl font-bold font-mono tracking-tight', colorCls)}>
                  {currentQuote.last_price != null ? currentQuote.last_price.toFixed(2) : '--'}
                </span>
                <div className="flex items-center gap-2">
                  <span className={cn('flex items-center gap-0.5 text-base font-semibold font-mono', colorCls)}>
                    {isUp && <TrendingUp className="h-4 w-4" />}
                    {isDown && <TrendingDown className="h-4 w-4" />}
                    {currentQuote.change != null ? (currentQuote.change > 0 ? `+${currentQuote.change.toFixed(2)}` : currentQuote.change.toFixed(2)) : '--'}
                  </span>
                  <span className={cn('rounded px-1.5 py-0.5 text-sm font-semibold font-mono', bgCls, colorCls)}>
                    {currentQuote.change_pct != null ? (currentQuote.change_pct > 0 ? `+${currentQuote.change_pct.toFixed(2)}%` : `${currentQuote.change_pct.toFixed(2)}%`) : '--'}
                  </span>
                </div>
                {currentQuote.is_no_limit && (
                  <span className="rounded bg-purple-500/10 px-1.5 py-0.5 text-[10px] font-medium text-purple-600 dark:text-purple-400 border border-purple-500/20">
                    無漲跌幅限制 (NO LIMIT)
                  </span>
                )}
              </div>

              {/* 報價詳細四格表 */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
                <div className="rounded-lg bg-elevated/40 p-2 border border-border/30">
                  <div className="text-muted text-[11px]">開盤價</div>
                  <div className="font-mono font-medium text-sm mt-0.5">{currentQuote.open != null ? currentQuote.open.toFixed(2) : '--'}</div>
                </div>
                <div className="rounded-lg bg-elevated/40 p-2 border border-border/30">
                  <div className="text-muted text-[11px]">最高價</div>
                  <div className="font-mono font-medium text-sm text-rose-500 mt-0.5">{currentQuote.high != null ? currentQuote.high.toFixed(2) : '--'}</div>
                </div>
                <div className="rounded-lg bg-elevated/40 p-2 border border-border/30">
                  <div className="text-muted text-[11px]">最低價</div>
                  <div className="font-mono font-medium text-sm text-emerald-500 mt-0.5">{currentQuote.low != null ? currentQuote.low.toFixed(2) : '--'}</div>
                </div>
                <div className="rounded-lg bg-elevated/40 p-2 border border-border/30">
                  <div className="text-muted text-[11px]">昨收價</div>
                  <div className="font-mono font-medium text-sm mt-0.5">{currentQuote.prev_close != null ? currentQuote.prev_close.toFixed(2) : '--'}</div>
                </div>
                <div className="rounded-lg bg-elevated/40 p-2 border border-border/30">
                  <div className="text-muted text-[11px]">成交量</div>
                  <div className="font-mono font-medium text-sm mt-0.5">{formatVolume(currentQuote.volume, volUnit)}</div>
                </div>
                <div className="rounded-lg bg-elevated/40 p-2 border border-border/30">
                  <div className="text-muted text-[11px]">成交金額</div>
                  <div className="font-mono font-medium text-sm mt-0.5" title={`TWD ${currentQuote.amount?.toLocaleString() || '--'}`}>
                    {formatAmount(currentQuote.amount)}
                  </div>
                </div>
                <div className="rounded-lg bg-elevated/40 p-2 border border-border/30">
                  <div className="text-muted text-[11px]">漲停價 ({currentQuote.price_limit_pct != null ? `+${currentQuote.price_limit_pct}%` : '無限制'})</div>
                  <div className="font-mono font-medium text-sm text-rose-600 dark:text-rose-400 mt-0.5">
                    {currentQuote.limit_up != null ? currentQuote.limit_up.toFixed(2) : '--'}
                  </div>
                </div>
                <div className="rounded-lg bg-elevated/40 p-2 border border-border/30">
                  <div className="text-muted text-[11px]">跌停價 ({currentQuote.price_limit_pct != null ? `-${currentQuote.price_limit_pct}%` : '無限制'})</div>
                  <div className="font-mono font-medium text-sm text-emerald-600 dark:text-emerald-400 mt-0.5">
                    {currentQuote.limit_down != null ? currentQuote.limit_down.toFixed(2) : '--'}
                  </div>
                </div>
              </div>
            </div>

            {/* 底部操作快捷鍵 */}
            <div className="mt-3 pt-3 border-t border-border/40 flex items-center justify-between">
              <span className="text-[11px] text-muted">
                {currentQuote.source_meta?.is_stale ? '⚠️ 目前顯示過期快照，請確認網路連線' : '即時行情自動同步中 (SSE)'}
              </span>
              <button
                onClick={() => onAddRuleForSymbol?.(currentQuote)}
                className="inline-flex items-center gap-1.5 rounded-lg bg-accent/15 px-3 py-1.5 text-xs font-semibold text-accent hover:bg-accent/25 transition-colors cursor-pointer"
              >
                針對此標的建立監控規則
              </button>
            </div>
          </div>

          {/* 右區: 五檔買賣盤口 (Level 2 Order Book) */}
          <div className="flex flex-col rounded-xl border border-border/70 bg-surface/80 p-3 shadow-sm">
            <div className="mb-2 flex items-center justify-between px-1">
              <div className="flex items-center gap-1.5">
                <Layers className="h-4 w-4 text-accent" />
                <span className="text-xs font-bold text-foreground">五檔即時盤口</span>
              </div>
              <span className="text-[10px] text-muted font-mono">
                買 {formatVolume(currentQuote.bid_volume, volUnit)} / 賣 {formatVolume(currentQuote.ask_volume, volUnit)}
              </span>
            </div>

            <FiveLevelBook
              bids={currentQuote.bids}
              asks={currentQuote.asks}
              prevClose={currentQuote.prev_close}
              unit={volUnit}
            />
          </div>
        </div>
      ) : (
        <div className="flex flex-col items-center justify-center p-8 rounded-xl border border-dashed border-border text-center text-muted">
          <AlertCircle className="h-8 w-8 text-muted mb-2" />
          <p className="text-sm">尚未載入即時報價，請稍候或檢查後端服務</p>
        </div>
      )}
    </div>
  )
}

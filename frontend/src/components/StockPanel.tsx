import { useEffect, useState, useCallback, useRef, useMemo } from 'react'
import { X } from 'lucide-react'
import { type KlineRow, type FinancialMetricRecord } from '@/lib/api'
import { StockInfoBar } from '@/components/StockInfoBar'
import { StockDailyKChart, getDefaultRange, type StockDailyKChartResult } from '@/components/StockDailyKChart'
import { StockIntradayChart } from '@/components/StockIntradayChart'
import { useFinancialMetrics } from '@/lib/useFinancials'
import { useCapabilities } from '@/lib/useSharedQueries'
import type { ChartMarker, ChartPriceLine, ChartRange } from '@/components/EChartsCandlestick'
import {
  loadInfoFields,
  saveInfoFields,
  buildInfoExtColumnsParam,
  type ColumnConfig,
} from '@/lib/stock-info-fields'

interface Props {
  symbol: string
  height?: number
  showIntraday?: boolean
  className?: string
  /** 當用戶點擊蠟燭選中日期時回調（用於外部自動開啟分時圖）。 */
  onSelectDate?: (date: string) => void
  /** 外部傳入的日期範圍 */
  dateRange?: { start: string; end: string }
  markers?: ChartMarker[]
  ranges?: ChartRange[]
  priceLines?: ChartPriceLine[]
  showLimitMarkers?: boolean
  showMarkerToggle?: boolean
  /** 加監控回調 (傳入後信息條顯示 RadioTower 圖標) */
  onMonitor?: () => void
  onPriceDoubleClick?: (price: number, currentPrice: number) => void
  /** 自選操作（傳入後信息條顯示 Star 圖標） */
  inWatchlist?: boolean
  onAddToWatchlist?: (groupId: string | null) => void
  onRemoveFromWatchlist?: () => void
  watchlistPending?: boolean
  /** 分時圖自動刷新間隔(ms)。undefined = 不輪詢。個股對話框盤中實時刷新時傳入。 */
  refetchIntervalMs?: number
  /** 只渲染信息條, 隱藏圖表 (用於分時 tab 共享信息條) */
  infoBarOnly?: boolean
}

export { getDefaultRange }

export function StockPanel({
  symbol,
  height = 520,
  showIntraday = true,
  className,
  onSelectDate,
  dateRange: externalDateRange,
  markers,
  ranges,
  priceLines,
  showLimitMarkers = true,
  showMarkerToggle = true,
  onMonitor,
  onPriceDoubleClick,
  inWatchlist,
  onAddToWatchlist,
  onRemoveFromWatchlist,
  watchlistPending,
  refetchIntervalMs,
  infoBarOnly = false,
}: Props) {
  const [linkedPrice, setLinkedPrice] = useState<number | null>(null)
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const [intradayDismissed, setIntradayDismissed] = useState(false)
  const [dailyResult, setDailyResult] = useState<StockDailyKChartResult | null>(null)
  // 信息條指標配置提升到此層：同時供 StockInfoBar 渲染與 StockDailyKChart 請求 ext 數據
  const [fields, setFields] = useState<ColumnConfig[]>(loadInfoFields)
  const extColumns = useMemo(() => buildInfoExtColumnsParam(fields), [fields])

  const handleFieldsChange = useCallback((next: ColumnConfig[]) => {
    setFields(next)
    saveInfoFields(next)
  }, [])

  // 財務指標：僅當信息條配置含可見的財務字段且用戶具備財務數據能力 (financial) 時才請求
  // 無能力時跳過請求, 避免後端拋 CapabilityDenied (403) 導致 free/starter 檔彈錯誤提示
  const { data: caps } = useCapabilities()
  const hasFinancialCap = !!caps?.capabilities?.['financial']
  const hasFinanceField = useMemo(
    () => fields.some(f => f.visible && f.source.type === 'builtin'
      && ['eps', 'bps', 'roe', 'pe_ttm', 'pb', 'gross_margin', 'net_margin', 'debt_ratio', 'revenue_yoy', 'net_income_yoy'].includes(f.source.key)),
    [fields],
  )
  const financials = useFinancialMetrics(hasFinanceField && hasFinancialCap ? symbol : undefined)

  const dateRange = externalDateRange ?? getDefaultRange()

  const handleDateClick = useCallback((date: string) => {
    setSelectedDate(date)
    setIntradayDismissed(false)
    onSelectDate?.(date)
  }, [onSelectDate])

  const rows = dailyResult?.rows ?? []
  const stockInfo = dailyResult?.stockInfo
  const rawRows: KlineRow[] = dailyResult?.rawRows ?? []

  // symbol 變化時重置分時相關狀態，避免切股後殘留舊日期。
  // 注意：必須跳過首次掛載——重開彈窗時 kline 命中 react-query 緩存，
  // 子組件 onDataChange effect（先於父 effect 執行）會把 dailyResult 置為有效數據，
  // 若此處再無條件清空，會把剛加載的數據抹掉，導致信息條整行消失。
  const prevSymbol = useRef<string | null>(symbol)
  useEffect(() => {
    if (prevSymbol.current === symbol) return
    prevSymbol.current = symbol
    setSelectedDate(null)
    setLinkedPrice(null)
    setDailyResult(null)
  }, [symbol])

  // 當分時開啟、無選中日期時，自動選中最新日期
  useEffect(() => {
    if (showIntraday && !selectedDate && rows.length > 0) {
      setSelectedDate(rows[rows.length - 1].date)
    }
  }, [showIntraday, selectedDate, rows])

  const selectedIdx = selectedDate ? rows.findIndex(r => r.date === selectedDate) : -1
  const prevClose = selectedIdx > 0
    ? rows[selectedIdx - 1].close
    : rows.length >= 2
      ? rows[rows.length - 2].close
      : undefined
  if (!symbol) return null

  // 財務指標最新一期（metrics 按 period_end 排序，取首項）
  const financialMetrics: FinancialMetricRecord | undefined = financials.data?.data?.[0]

  return (
    <div className={className}>
      <StockInfoBar
        symbol={symbol}
        name={dailyResult?.name}
        stockInfo={stockInfo}
        rows={rawRows}
        fields={fields}
        onFieldsChange={handleFieldsChange}
        financialMetrics={financialMetrics}
        onMonitor={onMonitor}
        inWatchlist={inWatchlist}
        onAddToWatchlist={onAddToWatchlist}
        onRemoveFromWatchlist={onRemoveFromWatchlist}
        watchlistPending={watchlistPending}
      />

      {infoBarOnly ? null : (
      <div className="flex gap-3 items-start">
        <StockDailyKChart
          symbol={symbol}
          height={height}
          className="flex-1 min-w-0"
          dateRange={dateRange}
          markers={markers}
          ranges={ranges}
          priceLines={priceLines}
          showLimitMarkers={showLimitMarkers}
          showMarkerToggle={showMarkerToggle}
          linkedPrice={linkedPrice}
          onDateClick={handleDateClick}
          onPriceDoubleClick={onPriceDoubleClick}
          onDataChange={setDailyResult}
          visibleBars={showIntraday ? 40 : 60}
          extColumns={extColumns}
        />

        {showIntraday && selectedDate && !intradayDismissed && (
          <div className="relative flex-1 min-w-0 border-l border-border pl-3">
            <button
              onClick={() => setIntradayDismissed(true)}
              className="absolute -left-1.5 -top-1.5 z-10 flex h-5 w-5 items-center justify-center rounded-full border border-border bg-surface text-muted shadow-sm transition-colors hover:text-foreground hover:bg-elevated"
              title="收起分時圖"
              aria-label="收起分時圖"
            >
              <X className="h-3 w-3" />
            </button>
            <StockIntradayChart
              symbol={symbol}
              date={selectedDate}
              height={height}
              prevClose={prevClose}
              onPriceHover={setLinkedPrice}
              onPriceDoubleClick={onPriceDoubleClick}
              currentPrice={rows[rows.length - 1]?.close}
              priceLines={priceLines}
              refetchIntervalMs={refetchIntervalMs}
            />
          </div>
        )}
      </div>
      )}
    </div>
  )
}

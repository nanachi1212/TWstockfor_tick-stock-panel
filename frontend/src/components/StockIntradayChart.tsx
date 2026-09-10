import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { api, type MinuteKlineRow } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { EChartsIntraday } from '@/components/EChartsIntraday'

interface Props {
  symbol: string
  date: string | null
  height?: number
  prevClose?: number
  className?: string
  onPriceHover?: (price: number | null) => void
  onPriceDoubleClick?: (price: number, currentPrice: number) => void
  currentPrice?: number
  priceLines?: { value: number; label?: string; color?: string }[]
  /** 自動刷新間隔(ms)。undefined/0 = 不輪詢(默認)。個股對話框盤中實時刷新時傳入。 */
  refetchIntervalMs?: number
}

export function StockIntradayChart({
  symbol,
  date,
  height = 520,
  prevClose,
  className,
  onPriceHover,
  onPriceDoubleClick,
  currentPrice,
  priceLines,
  refetchIntervalMs,
}: Props) {
  const qc = useQueryClient()
  const [minuteDismissed, setMinuteDismissed] = useState(false)

  const minute = useQuery({
    queryKey: QK.klineMinute(symbol, date ?? ''),
    queryFn: () => api.klineMinute(symbol, date ?? undefined),
    enabled: !!symbol && !!date,
    refetchInterval: refetchIntervalMs,
  })

  const fetchMinute = useMutation({
    mutationFn: () => api.syncMinuteSingle(symbol),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['kline-minute', symbol] })
      qc.invalidateQueries({ queryKey: QK.klineMinute(symbol, date ?? '') })
      setMinuteDismissed(false)
    },
  })

  const minuteRows: MinuteKlineRow[] = useMemo(() => minute.data?.rows ?? [], [minute.data?.rows])
  // source=none 表示本地無數據且 TickFlow 也拉不到 (停牌/復牌延遲/非交易日)
  // 此時不彈"是否獲取"詢問窗, 只做靜態提示, 避免誤導用戶去拉明知拉不到的數據
  const sourceIsNone = minute.data?.source === 'none'
  // 指數分鐘K無本地存儲且不支持落庫獲取 (後端 sync_minute_single 顯式拒絕), 不顯示獲取按鈕
  const isIndex = minute.data?.asset_type === 'index'

  useEffect(() => {
    setMinuteDismissed(false)
    onPriceHover?.(null)
  }, [date, onPriceHover])

  if (!symbol || !date) return null

  return (
    <div className={className} style={{ height, flexShrink: 0 }}>
      {minute.isLoading && <div className="text-xs text-muted py-2">分時載入中…</div>}
      {!minute.isLoading && minuteRows.length === 0 && (
        <>
          {fetchMinute.isPending ? (
            <div className="flex items-center justify-center h-full gap-2 text-xs text-accent">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              <span>正在獲取分鐘K數據…</span>
            </div>
          ) : isIndex ? (
            // 指數: 分鐘K僅支持實時讀取, 無落庫獲取入口
            <div className="flex items-center justify-center h-full text-xs text-muted">指數暫無分鐘數據</div>
          ) : sourceIsNone ? (
            // 數據源確認無此日分鐘數據 (停牌/復牌延遲等): 靜態提示 + 保留重試
            <div className="flex flex-col items-center justify-center h-full gap-3">
              <div className="text-xs text-muted">該日暫無分鐘數據（數據源未提供）</div>
              <button
                onClick={() => fetchMinute.mutate()}
                className="px-4 py-1.5 rounded-btn bg-elevated text-secondary text-xs font-medium hover:bg-elevated/80 transition-colors duration-150"
              >
                重新獲取
              </button>
            </div>
          ) : minuteDismissed ? (
            <div className="flex flex-col items-center justify-center h-full gap-3">
              <div className="text-xs text-muted">暫無分鐘數據</div>
              <button
                onClick={() => setMinuteDismissed(false)}
                className="px-4 py-1.5 rounded-btn bg-accent/90 text-base text-xs font-medium hover:bg-accent transition-colors duration-150"
              >
                獲取分鐘K
              </button>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-full gap-4">
              <div className="text-sm text-foreground">是否立即獲取最近5日分鐘K？</div>
              <div className="flex items-center gap-3">
                <button
                  onClick={() => fetchMinute.mutate()}
                  className="px-4 py-1.5 rounded-btn bg-accent/90 text-base text-xs font-medium hover:bg-accent transition-colors duration-150"
                >
                  確定
                </button>
                <button
                  onClick={() => setMinuteDismissed(true)}
                  className="px-4 py-1.5 rounded-btn bg-elevated text-secondary text-xs hover:bg-elevated/80 transition-colors duration-150"
                >
                  取消
                </button>
              </div>
            </div>
          )}
        </>
      )}
      {minuteRows.length > 0 && (
        <EChartsIntraday
          data={minuteRows}
          height={height}
          prevClose={prevClose}
          date={date}
          priceLimit={minute.data?.price_limit ?? undefined}
          onPriceHover={onPriceHover}
          onPriceDoubleClick={onPriceDoubleClick}
          currentPrice={currentPrice}
          priceLines={priceLines}
        />
      )}
    </div>
  )
}

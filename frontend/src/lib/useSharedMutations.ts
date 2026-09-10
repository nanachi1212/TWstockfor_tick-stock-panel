/**
 * 共享 mutation hooks — 消除多頁面重複的 useMutation 調用。
 */
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from './api'
import { QK } from './queryKeys'

/** 切換實時行情 — Layout / Data 共用 */
export function useToggleRealtimeQuotes() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (enabled: boolean) => api.updateRealtimeQuotes(enabled),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.preferences })
      qc.invalidateQueries({ queryKey: QK.quoteStatus })
    },
  })
}

/** 更新行情輪詢間隔 — Layout / Data 共用 */
export function useUpdateQuoteInterval() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: number) => api.updateQuoteInterval(v),
    onSuccess: (data) => {
      qc.setQueryData(QK.quoteInterval, data)
      qc.invalidateQueries({ queryKey: QK.quoteStatus })
    },
  })
}

interface WatchlistBatchAddInput {
  symbols: string[]
  groupId?: string | null
}

/** 批量添加自選 — Screener / 截圖導入共用 */
export function useWatchlistBatchAdd() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ symbols, groupId }: WatchlistBatchAddInput) =>
      api.watchlistBatchAdd(symbols, '', groupId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.watchlist })
      // 前綴匹配: 實際 key 為 ['watchlist-enriched', extColumnsParam],
      // 不能用 QK.watchlistEnriched()(= undefined) 精確匹配, 否則列表不刷新。
      qc.invalidateQueries({ queryKey: ['watchlist-enriched'] })
    },
  })
}

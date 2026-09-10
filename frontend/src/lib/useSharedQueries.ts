/**
 * 共享 query hooks — 消除多頁面重複的 useQuery 調用。
 *
 * 實時數據走 SSE invalidation，無需前端輪詢。
 * 只有管線進度等非 SSE 數據才用 refetchInterval。
 */
import { useQuery } from '@tanstack/react-query'
import { api } from './api'
import { QK } from './queryKeys'

// ===== 全局共享 =====

/** 能力檢測 — Layout / Data / Keys 共用 */
export function useCapabilities() {
  return useQuery({
    queryKey: QK.capabilities,
    queryFn: api.capabilities,
  })
}

/** 設置狀態 — Layout / Data / Keys 共用 */
export function useSettings() {
  return useQuery({
    queryKey: QK.settings,
    queryFn: api.settings,
  })
}

/** 用戶偏好 — Layout / Data / Intraday 共用 */
export function usePreferences() {
  return useQuery({
    queryKey: QK.preferences,
    queryFn: api.preferences,
  })
}

/** 行情狀態 — SSE quotes_updated 自動刷新。

 * poll=true 時啟用 60s 狀態輪詢兜底, 用於在交易時段邊界
 * (11:30午休 / 13:00開盤 / 15:00收盤) 同步 quote status。
 * SSE 會在行情更新時即時刷新, 輪詢負責沒有 SSE 的休盤邊界。
 * 只應在全局唯一掛載處 (Layout) 傳 poll=true, 避免多頁面重複輪詢;
 * 其他調用方共享同一 queryKey 緩存, 無需自行輪詢。
 */
export function useQuoteStatus(opts?: { enabled?: boolean; poll?: boolean }) {
  return useQuery({
    queryKey: QK.quoteStatus,
    queryFn: api.quoteStatus,
    enabled: opts?.enabled ?? true,
    refetchInterval: opts?.poll ? 60_000 : false,
  })
}

/** 行情間隔 — Layout / Data 共用 */
export function useQuoteInterval() {
  return useQuery({
    queryKey: QK.quoteInterval,
    queryFn: api.quoteInterval,
  })
}

/** 版本號 — Layout 專用 */
export function useVersion() {
  return useQuery({
    queryKey: QK.version,
    queryFn: api.version,
    staleTime: Infinity,
  })
}

/** 數據狀態 — Data / Screener 共用 */
export function useDataStatus(opts?: {
  staleTime?: number
  refetchInterval?: number | false | ((query: any) => number | false | undefined)
}) {
  return useQuery({
    queryKey: QK.dataStatus,
    queryFn: api.dataStatus,
    staleTime: opts?.staleTime,
    refetchInterval: opts?.refetchInterval,
  })
}

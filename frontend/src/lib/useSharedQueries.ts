/**
 * 共享 query hooks — 消除多頁面重複的 useQuery 呼叫。
 *
 * 即時資料走 SSE invalidation，無需前端輪詢。
 * 只有管線進度等非 SSE 資料才用 refetchInterval。
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

/** 設定狀態 — Layout / Data / Keys 共用 */
export function useSettings() {
  return useQuery({
    queryKey: QK.settings,
    queryFn: api.settings,
  })
}

/** 使用者偏好 — Layout / Data / Intraday 共用 */
export function usePreferences() {
  return useQuery({
    queryKey: QK.preferences,
    queryFn: api.preferences,
  })
}

/** 行情狀態 — SSE quotes_updated 自動重新整理。

 * poll=true 時啟用 60s 狀態輪詢備援, 用於在交易時段邊界
 * (09:00 開盤 / 13:30 收盤) 同步 quote status。
 * SSE 會在行情更新時即時重新整理, 輪詢負責沒有 SSE 的休盤邊界。
 * 只應在全局唯一掛載處 (Layout) 傳 poll=true, 避免多頁面重複輪詢;
 * 其他呼叫方共享同一 queryKey 快取, 無需自行輪詢。
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

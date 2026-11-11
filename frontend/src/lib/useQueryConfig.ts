/**
 * SSE 設定 — 執行時參數。
 *
 * 目前只保留 SSE 重連延遲，其他資料重新整理全部走 SSE invalidation。
 * 儲存在 localStorage。
 */
import { storage } from '@/lib/storage'

// ===== 設定結構 =====

export interface QueryConfig {
  /** SSE 設定 */
  sse: {
    reconnectDelay: number
  }
}

export const DEFAULT_QUERY_CONFIG: QueryConfig = {
  sse: {
    reconnectDelay: 5_000,
  },
}

// ===== localStorage 持久化 =====

function loadConfig(): QueryConfig {
  const raw = storage.queryConfig.get(null) as QueryConfig | null
  if (!raw) return DEFAULT_QUERY_CONFIG
  return {
    sse: { ...DEFAULT_QUERY_CONFIG.sse, ...raw.sse },
  }
}

/**
 * 輕量版：只讀取當前設定。
 * 供 useQuoteStream 使用。
 */
export function getQueryConfig(): QueryConfig {
  return loadConfig()
}

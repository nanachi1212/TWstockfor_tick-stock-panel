/**
 * SSE 配置 — 運行時參數。
 *
 * 目前只保留 SSE 重連延遲，其他數據刷新全部走 SSE invalidation。
 * 存儲在 localStorage。
 */
import { storage } from '@/lib/storage'

// ===== 配置結構 =====

export interface QueryConfig {
  /** SSE 配置 */
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
 * 輕量版：只讀取當前配置。
 * 供 useQuoteStream 使用。
 */
export function getQueryConfig(): QueryConfig {
  return loadConfig()
}

/**
 * 集中管理所有 localStorage 持久化。
 *
 * - key 在此註冊，各頁面只通過 storage.xxx.get/set 呼叫。
 * - 類型安全，不再散落 try/catch。
 */

function kv<T>(key: string, throwOnWriteError = false, throwOnReadError = false) {
  return {
    get(fallback: T): T {
      try {
        const raw = localStorage.getItem(key)
        if (raw !== null) return JSON.parse(raw) as T
      } catch (error) {
        if (throwOnReadError) throw error
      }
      return fallback
    },
    set(val: T) {
      try { localStorage.setItem(key, JSON.stringify(val)) } catch (error) {
        if (throwOnWriteError) throw error
      }
    },
  }
}

export const storage = {
  /** 個股頁是否展開進階資料（預設初學者模式） */
  stockDetailAdvanced:  kv<boolean>('stock-detail-advanced'),

  /** 自選列表列設定 */
  watchlistColumns:     kv<unknown[]>('watchlist_columns'),

  /** Portfolio 成交紀錄，持倉與損益由成交資料重建 */
  portfolioTransactions: kv<unknown[]>('portfolio_transactions', true, true),
  portfolioImportPrevious: kv<unknown[] | null>('portfolio_import_previous', true, true),

  /** 自選列表視圖模式 table | card (分組卡片為臨時模式, 不持久化) */
  watchlistView:        kv<string>('watchlist_view'),

  /** 自選列表日K蠟燭圖顯示狀態 */
  watchlistCandle:      kv<boolean>('watchlist_showCandle'),

  /** 自選列表分時圖顯示狀態 */
  watchlistIntraday:    kv<boolean>('watchlist_showIntraday'),

  /** 自選列表板塊篩選 */
  watchlistBoardFilter: kv<string[]>('watchlist_boardFilter'),

  /** 自選分組統計條設定 (metric: 統計指標, sort: 排序方式, card*: 分組卡片顯示項) */
  watchlistGroupStats: kv<{ metric: string; sort: string; cardTopN?: number; cardColorBar?: boolean; cardRank?: boolean }>('watchlist_groupStats'),
} as const

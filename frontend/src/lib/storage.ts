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

  /** 查詢輪詢 / SSE 設定 */
  queryConfig:          kv<unknown>('tf-stocks-query-config'),

  /** 策略池 (screener) */
  strategyPool:         kv<string[]>('strategy-pool'),

  /** 自選列表列設定 */
  watchlistColumns:     kv<unknown[]>('watchlist_columns'),

  /** Portfolio 成交紀錄，持倉與損益由成交資料重建 */
  portfolioTransactions: kv<unknown[]>('portfolio_transactions', true, true),
  portfolioImportPrevious: kv<unknown[] | null>('portfolio_import_previous', true, true),

  /** 個股日K資訊條指標設定 */
  stockInfoBarFields:   kv<unknown[]>('stock_info_bar_fields'),

  /** 個股日K成交量對比設定 */
  stockVolumeCompare:   kv<{ enabled: boolean; days: number }>('stock_volume_compare'),

  /** 個股詳情多日分時週期 */
  stockPreviewIntradayDays: kv<number>('stock_preview_intraday_days'),

  /** 策略結果列表列設定 */
  screenerResultColumns: kv<unknown[]>('screener_result_columns'),

  /** 自選列表視圖模式 table | card (分組卡片為臨時模式, 不持久化) */
  watchlistView:        kv<string>('watchlist_view'),

  /** 自選列表日K蠟燭圖顯示狀態 */
  watchlistCandle:      kv<boolean>('watchlist_showCandle'),

  /** 自選列表分時圖顯示狀態 */
  watchlistIntraday:    kv<boolean>('watchlist_showIntraday'),

  /** 策略結果列表日K蠟燭圖顯示狀態 */
  screenerCandle:       kv<boolean>('screener_showCandle'),

  /** 策略結果列表分時圖顯示狀態 */
  screenerIntraday:     kv<boolean>('screener_showIntraday'),

  /** 自選列表板塊篩選 */
  watchlistBoardFilter: kv<string[]>('watchlist_boardFilter'),

  /** 自選列表排除 ST 標的 (預設不排除) */

  /** 自選分組統計條設定 (metric: 統計指標, sort: 排序方式, card*: 分組卡片顯示項) */
  watchlistGroupStats: kv<{ metric: string; sort: string; cardTopN?: number; cardColorBar?: boolean; cardRank?: boolean }>('watchlist_groupStats'),

  /** Screener 卡片尺寸 */
  screenerCardSize:     kv<string>('screener-card-size'),

  /** 策略回測快捷區間按鈕設定 */
  strategyBacktestQuickRanges: kv<unknown>('strategy-backtest-quick-ranges'),

  /** 資料頁畫像卡片顯隱 (卡片key → 是否顯示) */
  dataCardVisible: kv<Record<string, boolean>>('data-card-visible'),
  /** 資料頁畫像卡片順序 (卡片key 陣列, 長度=卡片總數) */
  dataCardOrder: kv<string[]>('data-card-order'),
} as const

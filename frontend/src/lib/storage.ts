/**
 * 集中管理所有 localStorage 持久化。
 *
 * - key 在此註冊，各頁面只通過 storage.xxx.get/set 調用。
 * - 類型安全，不再散落 try/catch。
 */

function kv<T>(key: string) {
  return {
    get(fallback: T): T {
      try {
        const raw = localStorage.getItem(key)
        if (raw !== null) return JSON.parse(raw) as T
      } catch { /* ignore */ }
      return fallback
    },
    set(val: T) {
      try { localStorage.setItem(key, JSON.stringify(val)) } catch { /* ignore */ }
    },
  }
}

export const storage = {
  /** 查詢輪詢 / SSE 配置 */
  queryConfig:          kv<unknown>('tf-stocks-query-config'),

  /** 策略池 (screener) */
  strategyPool:         kv<string[]>('strategy-pool'),

  /** 自選列表列配置 */
  watchlistColumns:     kv<unknown[]>('watchlist_columns'),

  /** 個股日K信息條指標配置 */
  stockInfoBarFields:   kv<unknown[]>('stock_info_bar_fields'),

  /** 個股日K成交量對比設置 */
  stockVolumeCompare:   kv<{ enabled: boolean; days: number }>('stock_volume_compare'),

  /** 個股詳情多日分時週期 */
  stockPreviewIntradayDays: kv<number>('stock_preview_intraday_days'),

  /** 策略結果列表列配置 */
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

  /** 自選列表排除 ST 標的 (默認不排除) */
  watchlistExcludeST:    kv<boolean>('watchlist_excludeST'),

  /** 自選分組統計條配置 (metric: 統計指標, sort: 排序方式, card*: 分組卡片顯示項) */
  watchlistGroupStats: kv<{ metric: string; sort: string; cardTopN?: number; cardColorBar?: boolean; cardRank?: boolean }>('watchlist_groupStats'),

  /** Screener 卡片尺寸 */
  screenerCardSize:     kv<string>('screener-card-size'),

  /** 策略創建草稿（新建專用） */
  strategyDraft: kv<{ name: string; description: string; direction: string; style?: string; rules: string; code: string; step: number; strategyId: string; source?: 'ai' | 'custom' } | null>('strategy-draft'),

  /** 策略修改草稿（AI修改專用，不影響創建按鈕） */
  strategyModify: kv<{ name: string; description: string; direction: string; style?: string; rules: string; code: string; step: number; strategyId: string; source?: 'ai' | 'custom' } | null>('strategy-modify'),

  /** 策略構建器草稿（舊版兼容，逐漸廢棄） */
  strategyBuilderDraft: kv<{ name: string; description: string; direction: string; style?: string; rules: string; code: string; step: number; strategyId: string; source?: 'ai' | 'custom' } | null>('strategy-builder-draft'),

  /** 已保存策略的原始規則（策略ID → 規則文本） */
  strategyRules: kv<Record<string, string>>('strategy-rules'),

  /** 策略回測快捷區間按鈕配置 */
  strategyBacktestQuickRanges: kv<unknown>('strategy-backtest-quick-ranges'),

  /** 策略回測最後一次成功結果和參數 */
  strategyBacktestLast: kv<{
    selectedStrategy: string | null
    symbols: string
    assetType?: 'stock' | 'etf'
    start: string
    end: string
    matching: 'close_t' | 'open_t+1'
    entryFill: 'close_t' | 'open_t+1'
    exitFill: 'close_t' | 'open_t+1' | 'signal_next_minute'
    fees: string
    stampTax?: string
    slippage: string
    maxPositions: string
    maxExposure: string
    initialCapital: string
    positionSizing: 'equal' | 'score_weight'
    mode: 'position' | 'full'
    holdingDays: string
    minuteFill?: boolean
    regimeStates?: string[]
    regimeMinScore?: number | ''
    params?: Record<string, any>
    overrides?: Record<string, any>
    strategyConfigSignature?: string
    result: any
  } | null>('strategy-backtest-last'),

  /** 數據頁畫像卡片顯隱 (卡片key → 是否顯示) */
  dataCardVisible: kv<Record<string, boolean>>('data-card-visible'),
  /** 數據頁畫像卡片順序 (卡片key 數組, 長度=卡片總數) */
  dataCardOrder: kv<string[]>('data-card-order'),
} as const

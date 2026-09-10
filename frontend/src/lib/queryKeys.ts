/**
 * 集中管理所有 React Query key。
 *
 * - 新增查詢只需在此加一行，所有消費方自動引用。
 * - SSE invalidation 基於 SSE_INVALIDATE_PREFIXES 列表，新增 key 無需改 useQuoteStream。
 */

// ===== Query Key 工廠 =====

export const QK = {
  // 全局 / 共享 (Layout 預取)
  capabilities:   ['capabilities'] as const,
  settings:       ['settings'] as const,
  version:        ['version'] as const,
  preferences:    ['preferences'] as const,
  dataSources:    ['data-sources'] as const,
  taiwanDataStatus: ['taiwan-data-status'] as const,
  taiwanHistoryStatus: ['taiwan-history-status'] as const,
  quoteStatus:    ['quote-status'] as const,
  quoteInterval:  ['quote-interval'] as const,
  overviewMarket: (asOf?: string) => ['overview-market', asOf ?? 'latest'] as const,
  indexQuotes:    ['index-quotes'] as const,

  // Watchlist
  watchlist:            ['watchlist'] as const,
  watchlistGroups:      ['watchlist-groups'] as const,
  watchlistQuotes:      ['watchlist-quotes'] as const,
  watchlistEnriched:    (ext?: string) => ['watchlist-enriched', ext] as const,
  // 不用 watchlist- 前綴: 日K歷史盤中幾乎不變, 若被 SSE quotes_updated 高頻失效
  // (expert 1s) 會導致全自選日K每秒重拉, staleTime 形同虛設。
  // 刷新點: staleTime 過期 + Watchlist 增刪自選/改蠟燭天數時的手動失效;
  // 當日最後一根蠟燭由 Watchlist 用 enriched 實時 OHLC 前端修補 (零額外請求)。
  watchlistKlineBatch:  (symbols: string) => ['kline-batch', symbols] as const,
  // 不用 watchlist- 前綴: 避免被 SSE quotes_updated 高頻失效(expert 1s/pro 2s)
  // 導致每次都拉 TickFlow 觸限流。分時圖用固定 refetchInterval 刷新即可。
  minuteBatch:          (symbols: string) => ['minute-batch', symbols] as const,
  instrumentSearch:     (q: string, assetTypes?: string, market?: 'ashare' | 'taiwan' | 'all') =>
    ['instrument-search', q, assetTypes ?? 'stock', market ?? 'ashare'] as const,

  // Screener
  screener:             ['screener'] as const,
  screenerStrategies:   (assetType: string = 'stock') => ['screener-strategies', assetType] as const,
  screenerCachedSummary: ['screener-cached', 'summary'] as const,
  screenerCachedResult: (strategyId: string, asOf?: string, ext?: string) => ['screener-cached', 'strategy', strategyId, asOf ?? '', ext ?? ''] as const,
  screenerCached:       (asOf?: string, ext?: string) => ['screener-cached', 'all', asOf ?? '', ext ?? ''] as const,
  screenerKlineBatch:   (symbols: string) => ['screener-kline-batch', symbols] as const,
  marketSnapshot:       ['market-snapshot'] as const,

  // Backtest
  backtestStatus:       ['backtest-status'] as const,
  factorColumns:        ['backtest-factor-columns'] as const,
  miningRuns:           ['backtest-mining-runs'] as const,
  miningAvailability:   (assetType: string, profile: string, start: string, end: string) =>
                          ['backtest-mining-availability', assetType, profile, start, end] as const,
  miningRun:            (id: string) => ['backtest-mining-run', id] as const,
  miningResult:         (id: string) => ['backtest-mining-result', id] as const,
  miningConfig:         ['backtest-mining-config'] as const,
  researchCandidates:  ['research-candidates'] as const,
  strategyLinkOptions: (assetType?: 'stock' | 'etf') => assetType
    ? ['strategy-link-options', assetType] as const
    : ['strategy-link-options'] as const,
  strategyDetail:       (id: string) => ['strategy-detail', id] as const,

  // Data / Pipeline
  dataStatus:           ['data-status'] as const,
  pipelineJobs:         ['pipeline-jobs'] as const,
  pipelineJob:          (id: string) => ['pipeline-job', id] as const,
  dimensionMembers:     (id: string, field: string, value: string, date?: string) => ['dimension-members', id, field, value, date] as const,

  // Kline
  kline:                (symbol: string, start: string, end: string, extColumns?: string) =>
                           ['kline', symbol, start, end, extColumns ?? ''] as const,
  stockLevels:          (symbol: string, days?: number) => ['stock-levels', symbol, days ?? 120] as const,
  klineMinute:          (symbol: string, date: string) =>
                             ['kline-minute', symbol, date] as const,
  klineMinuteRange:     (symbol: string, days: number) =>
                             ['kline-minute-range', symbol, days] as const,

  // Schema
  extDataSchemaAll:     ['ext-data-schema-all'] as const,

  // Custom Signals
  customSignals:        ['custom-signals'] as const,
  customSignalsOptions: ['custom-signals-options'] as const,

  // Monitor (監控規則 + 觸發記錄)
  monitorRules:         ['monitor-rules'] as const,
  monitorRuleOptions:   ['monitor-rule-options'] as const,
  alerts:               (source?: string) => ['alerts', source ?? ''] as const,
  taiwanRules:          ['taiwan-rules'] as const,
  taiwanQuotes:         (symbols: string) => ['taiwan-quotes', symbols] as const,
  taiwanSearch:         (q: string) => ['taiwan-search', q] as const,
  taiwanStockDetail:    (symbol: string, days?: number) => ['taiwan-stock-detail', symbol, days ?? 120] as const,
  taiwanCurrentData:    (symbol: string) => ['taiwan-current-data', symbol] as const,
  taiwanCapabilities:   ['taiwan-capabilities'] as const,

  // 市場環境(Regime) — 日級離線計算, 不進 SSE 刷新
  // Phase 8B-5.7: 僅保留仍有真實 consumer 的 regimeLatest(Mining)/
  // regimeCoverage(Data) —— history/states/phases/mainline 隨已刪除的
  // Regime.tsx 一併移除。
  regimeLatest:         ['regime-latest'] as const,
  regimeCoverage:       ['regime-coverage'] as const,
} as const

// ===== SSE 應該 invalidate 的 key 前綴列表 =====
// 新增需要 SSE 推送的查詢，只需在此加一行
//
// 注意: 策略頁 (screener-cached) 不在此列表 —— 行情刷新時策略結果不變
// (非監控策略讀盤後靜態緩存, 監控策略由獨立的 strategy_results_updated 事件在
// 重算完成後刷新)。若加入 'screener', 會導致每個行情 tick 雙重刷新策略頁,
// 且在 monitor "重算" 窗口內讀到空結果, 造成策略列表閃爍 (變 0 → 空失效 → 又出現)。

export const SSE_INVALIDATE_PREFIXES = [
  // 精確前綴: 只命中自選頁的實時數據 (quotes/enriched)。不能用寬泛的 'watchlist' ——
  // 會誤傷 ['watchlist'] (自選列表) 和 ['watchlist-groups'] (分組配置, 只隨手動操作變化)。
  // 舊設置裡的 'watchlist' 單開關由 useQuoteStream 兼容讀取。
  'watchlist-quotes',
  'watchlist-enriched',
  'quote-status',
  'index-quotes',
  'overview-market',
  'taiwan-quotes',
  'taiwan-stock-detail',
] as const


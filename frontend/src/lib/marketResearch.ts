export type ResearchStatus = 'available' | 'partial' | 'unavailable'
export interface ResearchMetric {
  value: number | null
  unit: string
  source: string[]
  date: string
  as_of: string | null
  status: ResearchStatus
  coverage: {
    expected_days: number
    coverage_days: number
    expected_observations: number
    observed_observations: number
    missing_dates: string[]
  }
}
export type InstitutionalWindow = 5 | 10 | 20 | 45 | 60
export type Investor = 'foreign' | 'investment_trust' | 'dealer'
export const INVESTOR_LABELS: Record<Investor, string> = { foreign: '外資', investment_trust: '投信', dealer: '自營商' }
export interface InvestorStatistics {
  net_shares: ResearchMetric
  net_lots: ResearchMetric
  net_volume_ratio: ResearchMetric
  buy_streak: ResearchMetric
  sell_streak: ResearchMetric
  streak_capped: boolean
}
export interface InstitutionalStatisticsRow {
  symbol: string
  name: string
  exchange: string
  investors: Record<Investor, InvestorStatistics>
}
export interface InstitutionalFlowSeries {
  date: string
  window: InstitutionalWindow
  unit: 'lots'
  universe: string
  days: Array<{ date: string } & Record<Investor, number | null>>
}
export interface InstitutionalStatisticsSnapshot {
  date: string
  window: InstitutionalWindow
  sessions: string[]
  universe: string
  aggregates: InstitutionalStatisticsRow[]
  securities: InstitutionalStatisticsRow[]
}
export interface IndustryRotationRow {
  industry: string
  relative_strength_5d: ResearchMetric
  relative_strength_20d: ResearchMetric
  turnover_share: ResearchMetric
  average_turnover_share_20d: ResearchMetric
  turnover_share_delta_pp: ResearchMetric
}
export interface IndustryRotationSnapshot {
  date: string
  price_semantics: string
  benchmark: string
  delta_definition: string
  industries: IndustryRotationRow[]
}

// Stable URL tab IDs are reserved for the subsequent phases as well.
export const MARKET_RESEARCH_TABS = [
  { id: 'institutional', label: '法人統計', enabled: true },
  { id: 'rotation', label: '產業輪動', enabled: true },
  { id: 'heatmap', label: '資金熱浪', enabled: true },
  { id: 'breadth', label: '大盤寬度', enabled: true },
  { id: 'valuation', label: '估值', enabled: true },
  { id: 'chips', label: '籌碼排行', enabled: true },
  { id: 'big-holders', label: '大戶集中度', enabled: true },
] as const

/** amount is TWD; change_pct is decimal vs the previous session's raw close; null means not comparable, never 0. */
export interface HeatmapStock {
  symbol: string
  name: string
  close: number | null
  change_pct: number | null
  amount: number
  volume: number | null
}

/** change_pct is turnover-weighted over comparable constituents only; share is 0..1 of total_amount. */
export interface HeatmapIndustry {
  industry: string
  amount: number
  share: number
  change_pct: number | null
  advance: number
  decline: number
  flat: number
  count: number
  stocks: HeatmapStock[]
}

export interface MarketHeatmapSnapshot {
  date: string
  previous_date: string | null
  status: 'available' | 'unavailable'
  reason: 'no_daily_data' | null
  price_semantics: 'raw_close'
  total_amount: number
  previous_total_amount: number | null
  advance: number
  decline: number
  flat: number
  uncompared: number
  industries: HeatmapIndustry[]
}

export type BigHolderTone = 'concentrating' | 'dispersing' | 'flat' | 'insufficient'

/** big_holder_pct is percent; change_*_pp are percentage points; holders_change_*_pct are percent. null means not computable, never 0. */
export interface BigHolderRow {
  symbol: string
  name: string
  exchange: 'TWSE' | 'TPEX'
  big_holder_pct: number
  total_holders: number
  change_1w_pp: number | null
  holders_change_1w_pct: number | null
  change_4w_pp: number | null
  holders_change_4w_pct: number | null
  tone: BigHolderTone
  in_watchlist: boolean
}

export interface BigHolderRankingSnapshot {
  /** null only when no TDCC week is stored yet. */
  date: string | null
  previous_1w_date: string | null
  previous_4w_date: string | null
  min_holders: number
  threshold_pp: number
  universe_count: number
  concentrating: BigHolderRow[]
  dispersing: BigHolderRow[]
  watchlist: BigHolderRow[]
}

/** Product ranking; change_pct and big-holder values: change_pct is decimal, big_holder_* are percent points. */
export interface ChipRankingItem {
  symbol: string
  name: string
  exchange: 'TWSE' | 'TPEX'
  close: number
  change_pct: number | null
  avg_volume_5d: number
  chip_score: number
  components: string[]
  institutional_net: number
  institutional_net_5d: number | null
  foreign_buy_streak: number | null
  trust_buy_streak: number | null
  margin_change: number | null
  big_holder_pct: number | null
  big_holder_change: number | null
}

export interface DayTradePressureItem {
  symbol: string
  name: string
  exchange: 'TWSE' | 'TPEX'
  close: number
  change_pct: number
  volume: number
  volume_multiple: number
  institutional_net: number | null
}

export interface ChipRankingSnapshot {
  date: string
  sessions: string[]
  weights: Record<string, number>
  min_avg_volume_5d: number
  min_close: number
  big_holder_date: string | null
  big_holder_previous_date: string | null
  TWSE: ChipRankingItem[]
  TPEX: ChipRankingItem[]
  day_trade_pressure: DayTradePressureItem[]
}

/** Returns are decimals (0.03 = 3%); null means not computable, never 0. */
export interface ChipTrackingHorizon {
  status: 'available' | 'partial' | 'unavailable' | 'pending'
  exit_date: string | null
  reason?: string
  picks?: number
  avg_return?: number
  benchmark_return?: number | null
  avg_excess?: number | null
}

export interface ChipTrackingDay {
  date: string
  recorded_at: string
  entry_date: string | null
  status: 'tracked' | 'late' | 'pending'
  picks: { symbol: string; name: string | null; exchange: 'TWSE' | 'TPEX'; rank: number; return_1?: number | null; return_5?: number | null }[]
  h1: ChipTrackingHorizon
  h5: ChipTrackingHorizon
}

export interface ChipTrackingSummary {
  days: number
  picks: number
  sample_sufficient: boolean
  avg_return: number | null
  avg_benchmark: number | null
  avg_excess: number | null
  win_rate: number | null
  beat_rate: number | null
}

export interface ChipRankingTracking {
  benchmark_symbol: string
  min_days: number
  summary: { h1: ChipTrackingSummary; h5: ChipTrackingSummary }
  /** Summary covers only rankings with this definition; older ones are counted here, not mixed in. */
  other_definition_days: number
  days: ChipTrackingDay[]
}
export type MarketResearchTab = typeof MARKET_RESEARCH_TABS[number]['id']

export function rotationPoints(rows: IndustryRotationRow[]) {
  return rows.filter(r => r.relative_strength_20d.status === 'available'
    && r.turnover_share_delta_pp.status === 'available'
    && r.relative_strength_20d.value !== null && r.turnover_share_delta_pp.value !== null)
    .map(r => ({ name: r.industry, value: [r.relative_strength_20d.value! * 100, r.turnover_share_delta_pp.value!] }))
}

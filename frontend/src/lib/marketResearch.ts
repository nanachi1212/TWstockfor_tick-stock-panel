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
  { id: 'breadth', label: '大盤寬度', enabled: true },
  { id: 'valuation', label: '估值', enabled: true },
] as const
export type MarketResearchTab = typeof MARKET_RESEARCH_TABS[number]['id']

export function rotationPoints(rows: IndustryRotationRow[]) {
  return rows.filter(r => r.relative_strength_20d.status === 'available'
    && r.turnover_share_delta_pp.status === 'available'
    && r.relative_strength_20d.value !== null && r.turnover_share_delta_pp.value !== null)
    .map(r => ({ name: r.industry, value: [r.relative_strength_20d.value! * 100, r.turnover_share_delta_pp.value!] }))
}

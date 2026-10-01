export type ResearchMarket = 'TWSE' | 'TPEX' | 'composite'
export type ResearchSections = 'breadth' | 'valuation' | 'all'

export interface ResearchMetric {
  value: number | null
  included_count: number
  excluded_count: number
  excluded_reason_counts: Record<string, number>
  coverage: number | null
  status: 'available' | 'partial' | 'unavailable'
  unit: string
  numerator: number | null
}

export interface MarketBreadthStats {
  price_retrieved_at_status: 'unavailable_in_legacy_daily_store'
  eligibility_retrieved_at: string | null
  missing_markets: string[]
  as_of: string
  market: ResearchMarket
  source: string[]
  retrieved_at: string | null
  available_at: null
  universe_label: string
  universe_status: 'observed'
  historical_eligibility_status: string
  eligibility_verified_count: number
  eligibility_unknown_count: number
  universe_complete: false
  coverage_denominator: string
  usage_scope: 'descriptive_history'
  strategy_lab_eligible: false
  metrics: Record<string, ResearchMetric>
  advances: number | null
  declines: number | null
  unchanged: number | null
  ad_segment_start: string | null
}

export interface ValuationMetric extends ResearchMetric {
  percentile: number | null
  percentile_sample_count: number
  percentile_start: string | null
  percentile_end: string | null
  percentile_status: string
}

export interface MarketValuationStats {
  market: ResearchMarket
  as_of: string
  source: string[]
  source_urls: string[]
  retrieved_at: string | null
  available_at: null
  publication_time_status: 'unverified'
  usage_scope: 'descriptive_history'
  strategy_lab_eligible: false
  methodology: 'individual_stock_median'
  composite_method: 'pooled_same_session_individual_records'
  coverage_denominator: string
  universe_complete: false
  status: string
  metrics: Record<string, ValuationMetric>
}

export interface BreadthValuationResponse {
  contract_version: 1
  generated_at: string
  requested_as_of: string
  as_of: string | null
  market: ResearchMarket
  sections: ResearchSections
  status: string
  stale: boolean
  history: MarketBreadthStats[]
  latest: MarketBreadthStats | null
  valuation: MarketValuationStats | null
  warnings: string[]
  usage_scope: 'descriptive_history'
  strategy_lab_eligible: false
}

export interface ValuationRefreshResult {
  status: 'available' | 'partial' | 'unavailable'
  records_saved: number
  failed: { market: string; reason: string }[]
  as_of: string[]
  usage_scope: 'descriptive_history'
  available_at: null
}

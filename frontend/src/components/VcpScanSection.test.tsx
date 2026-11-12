import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { VcpScanItem, VcpScanSnapshot } from '@/lib/marketResearch'
import { VcpScanSection } from './VcpScanSection'

vi.mock('@/lib/api', () => ({ api: { taiwanVcpScan: vi.fn() } }))

const rules = { min_bars: 260, lookback_days: 120, prefilter_min_price: 10, prefilter_min_avg_volume_shares: 200_000, max_above_pivot_pct: 3, max_risk_pct: 15 }
const item: VcpScanItem = {
  symbol: '8415.TPEX', code: '8415', name: '大國鋼', industry: '鋼鐵工業', price: 38.2, signal: true, entry_ready: true,
  composite_score: 77.5, rating: 'Good VCP', quality_rating: 'Good VCP', cap_reason: null,
  execution_state: 'Breakout', execution_state_reason: '突破且量能確認', pattern_type: 'Post-breakout',
  pivot: 37.8, stop: 35.25, risk_pct: 8.44, distance_from_pivot_pct: 1.06, trade_status: 'BREAKOUT CONFIRMED',
  rs_percentile: 77, dry_up_ratio: 0.62, breakout_volume_ratio: 2.1, breakout_volume_detected: true,
  trend_criteria: { c1_price_above_sma150_200: true, c7_rs_rank_above_70: true }, trend_passed: true, prefilter_passed: true,
  contractions: [{ label: 'T1', high_date: '2026-08-01', high_price: 40, low_date: '2026-08-20', low_price: 33, depth_pct: 17.5, duration_days: 13 },
    { label: 'T2', high_date: '2026-09-10', high_price: 37.8, low_date: '2026-10-01', low_price: 35.6, depth_pct: 5.8, duration_days: 15 }],
  component_scores: { trend_template: 100 }, no_trade_skipped: 0,
}

function renderSection(snapshot: VcpScanSnapshot) {
  vi.mocked(api.taiwanVcpScan).mockResolvedValue(snapshot)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter><VcpScanSection date="2026-11-11" /></MemoryRouter></QueryClientProvider>)
}

describe('VcpScanSection', () => {
  it('lists a signal with Chinese state, pivot, stop and exclusion reasons', async () => {
    renderSection({ date: '2026-11-11', status: 'available', reason: null, rules, universe: 1990, eligible: 1681, rs_universe: 1836,
      matched: 1, signals: 1, excluded: { share_count_event_in_volume_window: 155 }, items: [item] })
    expect(await screen.findByText('放量突破')).toBeInTheDocument()
    expect(screen.getByText('符合進場條件，仍需事件檢查')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '8415 大國鋼' })).toHaveAttribute('href', '/stocks/8415.TPEX')
    expect(screen.getByText('18% → 6%')).toBeInTheDocument()
    expect(screen.getByText(/近 120 日有股數變動（量能不可比） 155/)).toBeInTheDocument()
  })

  it('explains why the scan is unavailable instead of showing an empty table', async () => {
    renderSection({ date: '2026-11-12', status: 'unavailable', reason: 'no_daily_data', rules, universe: 1990, eligible: 0,
      rs_universe: 0, matched: 0, signals: 0, excluded: {}, items: [] })
    expect(await screen.findByText('2026-11-12 尚無日資料')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
})

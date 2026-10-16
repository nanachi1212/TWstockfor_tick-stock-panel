import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { ChipRankingSnapshot, ChipRankingTracking, ChipTrackingSummary } from '@/lib/marketResearch'
import { ChipRankingTab } from './ChipRankingTab'

vi.mock('@/lib/api', () => ({ api: { taiwanChipRanking: vi.fn(), taiwanChipRankingTracking: vi.fn() } }))

const snapshot: ChipRankingSnapshot = {
  date: '2026-10-15', sessions: [], weights: { flow_today: 25 }, min_avg_volume_5d: 1_000_000, min_close: 10,
  big_holder_date: null, big_holder_previous_date: null, TWSE: [], TPEX: [], day_trade_pressure: [],
}
const thin: ChipTrackingSummary = { days: 3, picks: 120, sample_sufficient: false, avg_return: null,
  avg_benchmark: null, avg_excess: null, win_rate: null, beat_rate: null }

function renderTab(tracking: ChipRankingTracking) {
  vi.mocked(api.taiwanChipRanking).mockResolvedValue(snapshot)
  vi.mocked(api.taiwanChipRankingTracking).mockResolvedValue(tracking)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter><ChipRankingTab /></MemoryRouter></QueryClientProvider>)
}

describe('ChipRankingTab tracking', () => {
  it('shows partial stock returns and the frozen picks with research links', async () => {
    renderTab({ benchmark_symbol: '0050.TWSE', min_days: 20, other_definition_days: 0, summary: { h1: thin, h5: thin }, days: [
      { date: '2026-10-14', recorded_at: '2026-10-14T16:40:00+08:00', entry_date: '2026-10-15', status: 'tracked',
        picks: [{ symbol: '2330.TWSE', name: '台積電', exchange: 'TWSE', rank: 1, return_1: 0.03 }],
        h1: { status: 'partial', reason: 'benchmark_distribution_unverified', exit_date: '2026-10-15', avg_return: 0.03, benchmark_return: null },
        h5: { status: 'pending', exit_date: null } },
    ] })
    expect(await screen.findByText('0050 配息涵蓋未驗證')).toBeInTheDocument()
    const details = screen.getByText('1 檔／查看存檔').closest('details')!
    details.open = true
    expect(screen.getByRole('link', { name: '研究歷史' })).toHaveAttribute('href', '/research?symbol=2330.TWSE')
    expect(details).toHaveTextContent('存檔 2026-10-14T16:40:00+08:00')
  })
  it('hides averages until the minimum sample is reached', async () => {
    renderTab({ benchmark_symbol: '0050.TWSE', min_days: 20, other_definition_days: 0, summary: { h1: thin, h5: { ...thin, days: 0 } }, days: [
      { date: '2026-10-14', recorded_at: '2026-10-14T16:40:00+08:00', entry_date: '2026-10-15', status: 'tracked', picks: [],
        h1: { status: 'available', exit_date: '2026-10-15', picks: 40, avg_return: 0.012, benchmark_return: -0.003, avg_excess: 0.015 },
        h5: { status: 'pending', exit_date: null } },
      { date: '2026-10-15', recorded_at: '2026-10-16T09:30:00+08:00', entry_date: '2026-10-16', status: 'late', picks: [],
        h1: { status: 'pending', exit_date: null }, h5: { status: 'pending', exit_date: null } },
    ] })
    expect(await screen.findByText('已累積 3 / 20 個交易日，樣本不足，先不顯示平均。')).toBeInTheDocument()
    expect(screen.getByText('+1.20%')).toBeInTheDocument()
    expect(screen.getByText('0050 -0.30%')).toBeInTheDocument()
    expect(screen.getByText('尚未到期')).toBeInTheDocument()
    expect(screen.getByText('存檔太晚，不計入')).toBeInTheDocument()
  })

  it('shows an empty state before the first freeze', async () => {
    renderTab({ benchmark_symbol: '0050.TWSE', min_days: 20, other_definition_days: 0, summary: { h1: thin, h5: thin }, days: [] })
    expect(await screen.findByText('還沒有存檔紀錄。下一次收盤更新完成後會開始累積。')).toBeInTheDocument()
  })

  it('renders summary numbers when there are tracked days', async () => {
    const full: ChipTrackingSummary = { days: 20, picks: 800, sample_sufficient: true, avg_return: 0.004,
      avg_benchmark: 0.001, avg_excess: 0.003, win_rate: 0.55, beat_rate: 0.52 }
    renderTab({ benchmark_symbol: '0050.TWSE', min_days: 20, other_definition_days: 0, summary: { h1: full, h5: thin }, days: [
      { date: '2026-10-14', recorded_at: '2026-10-14T16:40:00+08:00', entry_date: null, status: 'pending', picks: [],
        h1: { status: 'pending', exit_date: null }, h5: { status: 'pending', exit_date: null } },
    ] })
    expect(await screen.findByText('+0.30%')).toBeInTheDocument()
    expect(screen.getByText('55%')).toBeInTheDocument()
    expect(screen.getByText('20 天 / 800 檔次')).toBeInTheDocument()
    expect(screen.getByText('等待隔日開盤')).toBeInTheDocument()
  })
})

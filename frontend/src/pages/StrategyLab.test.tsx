import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { api, type StrategyLabHorizon, type StrategyLabIdentity, type StrategyLabOverview } from '@/lib/api'
import { StrategyLab } from './StrategyLab'

vi.mock('@/lib/api', () => ({ api: { strategyLab: vi.fn(), strategyLabObservations: vi.fn() } }))

const horizon: StrategyLabHorizon = {
  matured: 5, pending: 2, unavailable: 1, hit_count: 3, hit_rate_denominator: 5,
  hit_rate_pct: 60, average_return_pct: 1.2, benchmark_n: 5, excess_n: 5,
  average_benchmark_return_pct: 0.2, average_excess_pct: 1,
  sample_sufficient: true, excess_sample_sufficient: true,
}
const ident = (i: number): StrategyLabIdentity => ({
  key: `strategy-${i}`, strategy_id: `formal-${i}`, strategy_name: `策略 ${i}`, version: 'v1',
  source: i === 1 ? 'A13 Buy Point' : i === 2 ? 'Daily recommendation' : 'Selection',
  definition_digest: null, entry_basis: i === 1 ? 'reference_close' : 'next_open',
  price_semantics: 'raw', cost_assumption: 'no_cost',
})
const overview = (n = 5): StrategyLabOverview => ({
  evidence_label: 'Forward / OOS observed', historical_pit_status: '未完全可用', hit_definition: 'return > 0',
  unit: 'pct', minimum_sample: 5, sample_count: n * 8,
  strategies: Array.from({ length: n }, (_, i) => ({ identity: ident(i), sample_count: 8, snapshot_count: 2,
    horizons: { '1D': horizon, '5D': horizon, '20D': horizon } })),
  strategy_options: Array.from({ length: n }, (_, i) => ident(i)),
  filter_options: { source: ['Selection', 'A13 Buy Point', 'Daily recommendation'], exchange: ['TWSE', 'TPEX'], risk_status: ['clear', 'unknown'], industry: [], market_regime: [], liquidity_bucket: [] },
  slice_availability: {}, duplicate_snapshots: 1, duplicate_samples: 0, integrity_conflicts: 0, excluded_research_snapshots: 3,
})
function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter><StrategyLab /></MemoryRouter></QueryClientProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.strategyLab).mockResolvedValue(overview())
  vi.mocked(api.strategyLabObservations).mockResolvedValue({ total: 0, offset: 0, limit: 50, observations: [] })
})

describe('Strategy Lab forward experiments', () => {
  it('renders identities, horizon denominators and the historical PIT blocker', async () => {
    mount()
    const table = await screen.findByRole('table', { name: '策略總覽' })
    expect(within(table).getAllByText('5 / 5')).toHaveLength(15)
    expect(within(table).getAllByText('60.00%')).toHaveLength(15)
    expect(screen.getByText(/Historical PIT research：未完全可用/)).toBeInTheDocument()
    expect(screen.getByText(/不是 historical backtest/)).toBeInTheDocument()
    expect(screen.getByText(/pending \/ unavailable 排除於命中率分母/)).toBeInTheDocument()
    expect(within(table).getByText(/A13 Buy Point/)).toBeInTheDocument()
    expect(within(table).getByText(/Daily recommendation/)).toBeInTheDocument()
    expect(screen.queryByText('最佳策略')).not.toBeInTheDocument()
  })

  it('passes filters and minimum sample to the server and disables missing persisted context', async () => {
    mount()
    await screen.findByRole('table', { name: '策略總覽' })
    expect(screen.getByLabelText('產業')).toBeDisabled()
    expect(screen.getByLabelText('市場環境')).toBeDisabled()
    expect(screen.getByLabelText('流動性分組')).toBeDisabled()
    fireEvent.change(screen.getByLabelText('TWSE / TPEX'), { target: { value: 'TPEX' } })
    fireEvent.change(screen.getByLabelText('Minimum sample'), { target: { value: '10' } })
    fireEvent.change(screen.getByLabelText('Selection source'), { target: { value: 'Selection' } })
    fireEvent.change(screen.getByLabelText('風險 gate'), { target: { value: 'clear' } })
    fireEvent.change(screen.getByLabelText('策略篩選'), { target: { value: 'strategy-0' } })
    await waitFor(() => expect(api.strategyLab).toHaveBeenLastCalledWith({ minimum_sample: 10, exchange: 'TPEX', source: 'Selection', risk_status: 'clear', strategy_key: 'strategy-0' }))
  })

  it('compares 2 to 4 identities without ranking and limits the fifth selection', async () => {
    mount()
    await screen.findByRole('table', { name: '策略總覽' })
    const boxes = screen.getAllByRole('checkbox')
    fireEvent.click(boxes[0]); fireEvent.click(boxes[1])
    expect(within(screen.getByRole('region', { name: '策略比較' })).getByText('策略並排比較（2 / 4）')).toBeInTheDocument()
    fireEvent.click(boxes[2]); fireEvent.click(boxes[3])
    expect(boxes[4]).toBeDisabled()
    fireEvent.click(boxes[0])
    expect(boxes[4]).toBeEnabled()
  })

  it('drills down with provenance, stock detail and precise Selection Review link', async () => {
    vi.mocked(api.strategyLabObservations).mockResolvedValue({ total: 1, offset: 0, limit: 50, observations: [{
      observation_id: 'observation', snapshot_id: 'forward_formal_20260803', symbol: '2330.TWSE', name: '台積電',
      signal_date: '2026-08-03', as_of: '2026-08-03T15:00:00+08:00', outcome_date: '2026-08-04', entry_date: '2026-08-04', entry_price: 100,
      horizon: 1, status: 'matured', return_pct: 2, benchmark_symbol: '0050.TWSE', benchmark_return_pct: 1, excess_pct: 1,
      reason: null, benchmark_reason: null, identity: ident(0), evidence_label: 'Forward / OOS observed', provenance: { snapshot_digest: 'proof', source: 'Screener' },
    }] })
    mount()
    fireEvent.click(await screen.findByRole('button', { name: '策略 0' }))
    const section = await screen.findByRole('region', { name: '策略觀察明細' })
    expect(await within(section).findByText('2330.TWSE')).toBeInTheDocument()
    expect(within(section).getByRole('link', { name: 'Selection Review' })).toHaveAttribute('href', '/selection-review?tab=forward&batch_id=forward_formal_20260803&strategy_id=formal-0')
    expect(within(section).getByRole('link', { name: '台積電' })).toHaveAttribute('href', '/stocks/2330.TWSE')
    expect(within(section).getByText('forward_formal_20260803')).toBeInTheDocument()
    expect(within(section).getByText(/snapshot_digest/)).toHaveTextContent('proof')
    expect(api.strategyLabObservations).toHaveBeenCalledWith({ minimum_sample: 5, strategy_key: 'strategy-0' }, 0)
    fireEvent.click(screen.getByRole('button', { name: '關閉明細' }))
    expect(screen.queryByRole('region', { name: '策略觀察明細' })).not.toBeInTheDocument()
  })

  it('shows empty and pending counts without fabricated zero performance', async () => {
    const value = overview(1)
    value.strategies[0].horizons = Object.fromEntries(['1D', '5D', '20D'].map(h => [h, {
      ...horizon, matured: 0, hit_rate_denominator: 0, pending: 8, unavailable: 0,
      hit_rate_pct: null, average_return_pct: null, average_benchmark_return_pct: null, average_excess_pct: null,
    }]))
    vi.mocked(api.strategyLab).mockResolvedValue(value)
    const view = mount()
    expect((await screen.findAllByText('8 / 0')).length).toBe(3)
    expect(screen.queryByText('0.00%')).not.toBeInTheDocument()
    view.unmount()
    vi.mocked(api.strategyLab).mockResolvedValue(overview(0))
    mount()
    expect(await screen.findByText(/尚無符合條件的前瞻觀察/)).toBeInTheDocument()
  })

  it('shows loading and source errors without stale statistics', async () => {
    vi.mocked(api.strategyLab).mockRejectedValue(new Error('corrupt'))
    mount()
    expect(screen.getByRole('status')).toHaveTextContent('載入策略觀察')
    expect(await screen.findByRole('alert')).toHaveTextContent('策略觀察目前無法讀取')
    expect(screen.queryByRole('table', { name: '策略總覽' })).not.toBeInTheDocument()
  })
})

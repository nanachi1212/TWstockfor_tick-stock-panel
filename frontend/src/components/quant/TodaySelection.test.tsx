import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { api } from '@/lib/api'
import { TodaySelection } from './TodaySelection'

vi.mock('@/lib/api', () => ({ api: {
  taiwanQuantLiveModels: vi.fn(), taiwanQuantLiveRuns: vi.fn(), taiwanQuantLiveRun: vi.fn(),
  taiwanQuotes: vi.fn(), watchlistList: vi.fn(), watchlistAdd: vi.fn(),
} }))

const signals = Array.from({ length: 12 }, (_, index) => ({
  symbol: `${2330 + index}.TWSE`, score: 0.9 - index / 100, rank: index + 1,
  selected: true, reference_close: 100 + index,
  feature_percentiles: { momentum_5d: 0.9, momentum_20d: 0.8, momentum_60d: 0.7 },
}))

function setup() {
  vi.mocked(api.taiwanQuantLiveModels).mockResolvedValue({ configured_model: { model_key: 'live-model', top_n: 10 }, latest_operation: null } as any)
  vi.mocked(api.taiwanQuantLiveRuns).mockResolvedValue({ runs: [{ model_key: 'live-model', session: '2026-09-23', snapshot_hash: 'abc', frozen_at: '2026-09-23T16:00:00+08:00', signal_count: 12 }] } as any)
  vi.mocked(api.taiwanQuantLiveRun).mockResolvedValue({
    model_key: 'live-model', session: '2026-09-23', snapshot_hash: 'abc', frozen_at: '2026-09-23T16:00:00+08:00',
    snapshot: { signal_session: '2026-09-23', usage_scope: 'experimental_live', validation_state: 'unvalidated', model: { model_key: 'live-model', top_n: 10, validation_state: 'unvalidated' }, signals, features: signals.map(signal => ({ symbol: signal.symbol, momentum_5d: 0.1, momentum_20d: 0.2, momentum_60d: 0.3, volatility_20d: 0.02, adv20_twd: 20_000_000, relative_volume: 1.4 })) },
  } as any)
  vi.mocked(api.taiwanQuotes).mockResolvedValue({ quotes: [{ symbol: signals[0].symbol, name: '台積電', last_price: 105, change_pct: 0.025 } as any], count: 1 })
  vi.mocked(api.watchlistList).mockResolvedValue({ symbols: [] } as any)
  vi.mocked(api.watchlistAdd).mockResolvedValue({ ok: true } as any)
}

function Location() { return <span data-testid="location">{useLocation().pathname}</span> }

function renderSelection(initialPath = '/') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[initialPath]}><Routes>
    <Route path="/" element={<><TodaySelection /><Location /></>} />
    <Route path="/stocks/:symbol" element={<><TodaySelection symbol="2330.TWSE" /><Location /></>} />
  </Routes></MemoryRouter></QueryClientProvider>)
}

afterEach(() => { vi.clearAllMocks() })

describe('TodaySelection', () => {
  it('uses the frozen live Top 10, preserves score and explains its factor percentiles', async () => {
    setup()
    renderSelection()
    expect(await screen.findByText('台積電')).toBeInTheDocument()
    expect(screen.getAllByText('105.00')).toHaveLength(2)
    expect(screen.getByText('90.0%')).toBeInTheDocument()
    expect(screen.getAllByText('短期動能排名前段、中期動能排名前段、長期動能排名前段').length).toBeGreaterThan(0)
    expect(screen.getAllByRole('row')).toHaveLength(11)
    expect(api.taiwanQuantEvaluation).toBeUndefined()
  })

  it('navigates to detail and shows the same live score and factor breakdown there', async () => {
    setup()
    renderSelection()
    fireEvent.click(await screen.findByRole('link', { name: /台積電/ }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/stocks/2330.TWSE'))
    expect(await screen.findByRole('region', { name: 'Live Quant 摘要' })).toHaveTextContent('排名 #1')
    expect(screen.getByText(/分數 90.0%/)).toBeInTheDocument()
    expect(api.taiwanQuantLiveRun).toHaveBeenCalledTimes(1)
  })

  it('adds a ranked symbol through the existing watchlist API', async () => {
    setup()
    renderSelection()
    fireEvent.click(await screen.findByRole('button', { name: '將 2330.TWSE 加入自選' }))
    await waitFor(() => expect(api.watchlistAdd).toHaveBeenCalledWith('2330.TWSE'))
  })

  it('keeps the ranking visible and explains partial quote availability', async () => {
    setup()
    vi.mocked(api.taiwanQuotes).mockResolvedValue({ quotes: [], count: 0 })
    renderSelection()
    expect(await screen.findByText('部分標的報價不可用，對應列以凍結排名參考收盤價顯示。')).toBeInTheDocument()
    expect(screen.getByText('100.00')).toBeInTheDocument()
  })

  it('shows a clear empty state when no live run exists', async () => {
    setup()
    vi.mocked(api.taiwanQuantLiveRuns).mockResolvedValue({ runs: [] })
    renderSelection()
    expect(await screen.findByText('目前尚無已完成的 live 排名；資料不足或非交易日不會以歷史 OOS 代替。')).toBeInTheDocument()
  })

  it('shows a retryable API error instead of hiding the dashboard', async () => {
    setup()
    vi.mocked(api.taiwanQuantLiveModels).mockRejectedValue(new Error('offline'))
    renderSelection()
    expect(await screen.findByRole('alert')).toHaveTextContent('Live Quant 或行情資料目前無法完整讀取')
    expect(screen.getByRole('button', { name: '重試' })).toBeInTheDocument()
  })
})

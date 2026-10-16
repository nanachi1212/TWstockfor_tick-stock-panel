import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api, type TaiwanPortfolioAnalytics } from '@/lib/api'
import { storage } from '@/lib/storage'
import { PORTFOLIO_CHANGED } from '@/lib/portfolio'
import { DATA_HEALTH_INVALIDATE_PREFIXES, QK } from '@/lib/queryKeys'
import { Holdings } from '@/pages/Holdings'
import { StaleDataBanner } from '@/components/StaleDataBanner'

vi.mock('@/lib/api', () => ({ api: { taiwanPortfolioAnalytics: vi.fn(), taiwanDataStatus: vi.fn(), taiwanUpdateLatest: vi.fn() } }))
vi.mock('./Portfolio', () => ({
  PortfolioPanel: ({ allocation }: { allocation?: TaiwanPortfolioAnalytics['allocation'] }) => (
    <div data-testid="table-allocation">{allocation?.positions.map(p => `${p.symbol}:${p.weight}`).join(',') ?? 'unavailable'}</div>
  ),
}))

const response: TaiwanPortfolioAnalytics = {
  benchmark_symbol: '0050.TWSE', benchmark_name: '元大台灣50',
  allocation: {
    status: 'available', as_of: '2026-10-14', total_market_value: 1000,
    sectors: [{ industry: '半導體業', market_value: 600, weight: 0.6 }, { industry: 'ETF', market_value: 400, weight: 0.4 }],
    positions: [{ symbol: '2330.TWSE', market_value: 600, weight: 0.6 }, { symbol: '0050.TWSE', market_value: 400, weight: 0.4 }],
    concentration: { largest_position: { symbol: '2330.TWSE', market_value: 600, weight: 0.6 }, top3_weight: 1,
      largest_industry: { industry: '半導體業', market_value: 600, weight: 0.6 } },
    unpriced_symbols: [], stale_symbols: [],
  },
  performance: { status: 'unavailable', reason: 'corporate_action_coverage_unavailable', window_start: null,
    window_end: null, sessions: 0, metrics: null, series: [], excluded_symbols: [] },
}

function seed(shares = 10) {
  storage.portfolioTransactions.set([
    { id: 'stock', symbol: '2330.TWSE', name: '台積電', side: 'buy', shares, price: 100, fee: 0, date: '2026-10-14', createdAt: '2026-10-14T00:00:00Z' },
    { id: 'etf', symbol: '0050.TWSE', name: '元大台灣50', side: 'buy', shares: 10, price: 100, fee: 0, date: '2026-10-14', createdAt: '2026-10-14T00:00:00Z' },
  ])
}

function renderHoldings() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return { ...render(<QueryClientProvider client={client}><Holdings /></QueryClientProvider>), client }
}

beforeEach(() => {
  seed()
  vi.mocked(api.taiwanPortfolioAnalytics).mockResolvedValue(structuredClone(response))
})
afterEach(() => { vi.clearAllMocks() })

describe('holdings concentration', () => {
  it('shows session gaps without a fabricated risk metric', async () => {
    vi.mocked(api.taiwanPortfolioAnalytics).mockResolvedValue({ ...response, performance: {
      ...response.performance, reason: 'incomplete_sessions', missing_sessions: [{ symbol: '2330.TWSE', date: '2026-06-08' }],
    } })
    renderHoldings()
    expect(await screen.findByText(/共同期間有交易日缺價/)).toBeInTheDocument()
    expect(screen.getByText(/缺口：2330.TWSE 2026-06-08/)).toBeInTheDocument()
    expect(screen.queryByText('最大回撤')).not.toBeInTheDocument()
  })

  it('keeps verified stock metrics visible while explaining the unavailable ETF benchmark', async () => {
    vi.mocked(api.taiwanPortfolioAnalytics).mockResolvedValue({ ...response, performance: {
      ...response.performance, status: 'partial', reason: 'benchmark_distribution_unverified',
      window_start: '2026-06-01', window_end: '2026-06-30', sessions: 22,
      metrics: { total_return: 0.1, volatility: 0.2, max_drawdown: -0.5, benchmark_return: null, benchmark_volatility: null, beta: null },
    } })
    renderHoldings()
    expect(await screen.findByText(/0050 配息涵蓋尚未驗證/)).toBeInTheDocument()
    expect(screen.getByText('最大回撤').parentElement).toHaveTextContent('-50.0%')
    expect(screen.getByText('Beta').parentElement).toHaveTextContent('—')
  })
  it('refreshes concentration after the existing update-latest action', async () => {
    vi.mocked(api.taiwanDataStatus).mockResolvedValue({ daily_status: 'stale', daily_as_of: '2026-10-13',
      daily_days_behind: 1, target_latest_trading_date: '2026-10-14' } as Awaited<ReturnType<typeof api.taiwanDataStatus>>)
    vi.mocked(api.taiwanUpdateLatest).mockResolvedValue({ ok: true, already_current: false,
      dates_fetched: 1, message: 'updated' } as Awaited<ReturnType<typeof api.taiwanUpdateLatest>>)
    const { client } = renderHoldings()
    await screen.findByText('最大持股占比')
    render(<QueryClientProvider client={client}><StaleDataBanner /></QueryClientProvider>)
    const button = await screen.findByRole('button', { name: '更新到最新' })
    act(() => button.click())
    await waitFor(() => expect(api.taiwanPortfolioAnalytics).toHaveBeenCalledTimes(2))
  })

  it('shares one response between the table and objective concentration summary', async () => {
    renderHoldings()
    const label = await screen.findByText('最大持股占比')
    expect(label.parentElement).toHaveTextContent('60.0%')
    expect(screen.getByText('前三大持股合計').parentElement).toHaveTextContent('100.0%')
    expect(screen.getByText('最大產業占比').parentElement).toHaveTextContent('半導體業')
    expect(screen.getByTestId('table-allocation')).toHaveTextContent('2330.TWSE:0.6')
    expect(screen.getByText(/尚未拆解成分股產業/)).toBeInTheDocument()
    expect(api.taiwanPortfolioAnalytics).toHaveBeenCalledTimes(1)
    expect(api.taiwanPortfolioAnalytics).toHaveBeenCalledWith([
      { symbol: '0050.TWSE', shares: 10 }, { symbol: '2330.TWSE', shares: 10 },
    ])
  })

  it('keeps the analytics query key stable and makes no request when holdings are unchanged', async () => {
    const { client } = renderHoldings()
    await screen.findByText('最大持股占比')
    const firstKey = client.getQueryCache().find({ queryKey: QK.portfolioAnalytics('0050.TWSE:10,2330.TWSE:10') })?.queryKey

    act(() => window.dispatchEvent(new Event(PORTFOLIO_CHANGED)))
    await waitFor(() => expect(client.getQueryCache().find({ queryKey: QK.portfolioAnalytics('0050.TWSE:10,2330.TWSE:10') })?.queryKey).toEqual(firstKey))
    expect(api.taiwanPortfolioAnalytics).toHaveBeenCalledTimes(1)
  })

  it('shows the API performance window and reports missing window dates explicitly', async () => {
    vi.mocked(api.taiwanPortfolioAnalytics).mockResolvedValue({ ...response, performance: {
      status: 'available', reason: null, window_start: '2026-05-04', window_end: '2026-10-14', sessions: 100,
      metrics: { total_return: 0.1, benchmark_return: 0.08, volatility: 0.2, benchmark_volatility: 0.15, beta: 1.1, max_drawdown: -0.05 },
      series: [], excluded_symbols: [],
    } })
    const { client } = renderHoldings()
    expect(await screen.findByText(/2026-05-04 ～ 2026-10-14/)).toBeInTheDocument()
    expect(screen.getByText(/期間受已驗證除權息資料覆蓋範圍限制/)).toBeInTheDocument()
    expect(screen.queryByText(/一年/)).not.toBeInTheDocument()

    vi.mocked(api.taiwanPortfolioAnalytics).mockResolvedValue({ ...response, performance: {
      status: 'available', reason: null, window_start: null, window_end: null, sessions: 100,
      metrics: { total_return: 0.1, benchmark_return: 0.08, volatility: 0.2, benchmark_volatility: 0.15, beta: 1.1, max_drawdown: -0.05 },
      series: [], excluded_symbols: [],
    } })
    await act(async () => { await client.invalidateQueries({ queryKey: QK.portfolioAnalytics('0050.TWSE:10,2330.TWSE:10') }) })
    expect(await screen.findByText(/績效期間日期不可用/)).toBeInTheDocument()
  })

  it('suppresses all weights for missing or out-of-date prices', async () => {
    vi.mocked(api.taiwanPortfolioAnalytics).mockResolvedValue({ ...response, allocation: {
      ...response.allocation, status: 'partial', total_market_value: null, positions: [], sectors: [], concentration: null,
      unpriced_symbols: ['0050.TWSE'], stale_symbols: ['2330.TWSE'],
    } })
    renderHoldings()
    expect(await screen.findByText(/暫不顯示整體權重與集中度/)).toBeInTheDocument()
    expect(screen.getByText(/缺收盤價/)).toHaveTextContent('0050')
    expect(screen.getByText(/收盤價日期落後/)).toHaveTextContent('2330')
    expect(screen.queryByText('最大持股占比')).not.toBeInTheDocument()
    expect(screen.getByTestId('table-allocation')).not.toHaveTextContent('0.6')
  })

  it('hides retained concentration and table weights after a failed refresh', async () => {
    const { client } = renderHoldings()
    await screen.findByText('最大持股占比')
    vi.mocked(api.taiwanPortfolioAnalytics).mockRejectedValue(new Error('offline'))
    await act(async () => { await client.invalidateQueries({ queryKey: ['portfolio-analytics'] }) })
    expect(await screen.findByRole('alert')).toHaveTextContent('組合分析暫時無法取得')
    expect(screen.queryByText('最大持股占比')).not.toBeInTheDocument()
    expect(screen.getByTestId('table-allocation')).toHaveTextContent('unavailable')
  })

  it('recalculates after a ledger change and invalidates on daily-data updates', async () => {
    const { client } = renderHoldings()
    await screen.findByText('最大持股占比')
    act(() => { seed(20); window.dispatchEvent(new Event(PORTFOLIO_CHANGED)) })
    await waitFor(() => expect(api.taiwanPortfolioAnalytics).toHaveBeenLastCalledWith([
      { symbol: '0050.TWSE', shares: 10 }, { symbol: '2330.TWSE', shares: 20 },
    ]))
    expect(DATA_HEALTH_INVALIDATE_PREFIXES.daily).toContain(QK.portfolioAnalytics('')[0])
    act(() => { storage.portfolioTransactions.set([]); window.dispatchEvent(new Event(PORTFOLIO_CHANGED)) })
    await waitFor(() => expect(screen.queryByRole('region', { name: '組合分析' })).not.toBeInTheDocument())
    client.clear()
  })
})

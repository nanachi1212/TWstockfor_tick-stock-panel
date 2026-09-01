// Phase 7K — TaiwanScreener 異常訊號面板導航正確性回歸測試
// 涵蓋：異常訊號面板列連結指向真實已註冊路由 /stocks/:symbol（保留交易所後綴），
// 不再指向不存在的 /taiwan/stocks/:symbol；主篩選表格既有正確連結行為不受影響。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import { TaiwanScreener } from './TaiwanScreener'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: {
    taiwanScreenerRun: vi.fn(),
    taiwanDataStatus: vi.fn(),
    taiwanMarketIntelligence: vi.fn(),
    taiwanIndustryIntelligence: vi.fn(),
    taiwanAbnormalDiagnostics: vi.fn(),
  },
}))

function buildScreenerResponse() {
  return {
    total: 1,
    page: 1,
    page_size: 50,
    data_dates: { daily_as_of: '2026-08-28', institutional_as_of: '2026-08-28', margin_as_of: '2026-08-28' },
    items: [
      {
        symbol: '2330.TWSE',
        name: '台積電',
        exchange: 'TWSE',
        instrument_type: 'stock',
        close: 1000,
        change_pct: 0.01,
        volume: 10000,
        amount: 100000000,
        foreign_net: 1000,
        investment_trust_net: 0,
        dealer_net: 0,
        margin_balance_change: 0,
        short_balance: 0,
        short_margin_ratio: 5,
        is_no_limit: false,
        price_limit_pct: 0.1,
        ma5: 990,
        ma20: 980,
        rsi_14: 55,
        momentum_5d: 0.02,
      },
    ],
  }
}

function buildDataStatus() {
  return {
    daily_as_of: '2026-08-28',
    institutional_as_of: '2026-08-28',
    margin_as_of: '2026-08-28',
    target_latest_trading_date: '2026-08-28',
    is_fully_current: true,
    daily_status: 'current',
    institutional_status: 'current',
    margin_status: 'current',
    daily_days_behind: 0,
    institutional_days_behind: 0,
    margin_days_behind: 0,
    scheduler_enabled: true,
    scheduled_update_time: '16:30',
    scheduled_timezone: 'Asia/Taipei',
  }
}

function buildAbnormalSignal(overrides: Partial<any> = {}) {
  return {
    symbol: '2330.TWSE',
    code: '2330',
    name: '台積電',
    industry: '半導體',
    close: 1000,
    change_pct: 0.06,
    amount: 100000000,
    volume_ratio_5d: 3.2,
    foreign_net: 1000000,
    margin_balance_change: 0,
    signal_count: 1,
    signals: [{ type: 'PRICE_MOVE', subtype: 'UP', formula: 'abs(chg)>=5%', observed: 0.06, baseline: 0.05 }],
    ...overrides,
  }
}

function buildAbnormalDiagnostics(items: any[]) {
  return { items, universe_count: items.length, generated_at: '2026-08-28T16:00:00+08:00' }
}

function renderScreener() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/taiwan-screener']}>
        <Routes>
          <Route path="/taiwan-screener" element={<TaiwanScreener />} />
          <Route
            path="/stocks/:symbol"
            element={<StockDetailMock />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function StockDetailMock() {
  // Reads the matched :symbol via window.location since useParams needs the route context;
  // simplest robust signal for these tests is just a stable marker element.
  return <div data-testid="detail-mock">stock-detail-page</div>
}

beforeEach(() => {
  vi.mocked(api.taiwanScreenerRun).mockResolvedValue(buildScreenerResponse() as any)
  vi.mocked(api.taiwanDataStatus).mockResolvedValue(buildDataStatus() as any)
  vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(null as any)
  vi.mocked(api.taiwanIndustryIntelligence).mockResolvedValue(null as any)
  vi.mocked(api.taiwanAbnormalDiagnostics).mockResolvedValue(
    buildAbnormalDiagnostics([
      buildAbnormalSignal({ symbol: '2330.TWSE', code: '2330', name: '台積電' }),
      buildAbnormalSignal({ symbol: '8069.TPEX', code: '8069', name: '元太' }),
    ]) as any,
  )
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('Abnormal diagnostics panel navigation (Phase 7K)', () => {
  it('renders the TWSE row link as exactly /stocks/2330.TWSE', async () => {
    renderScreener()
    const link = await screen.findByRole('link', { name: /台積電/ })
    expect(link).toHaveAttribute('href', '/stocks/2330.TWSE')
  })

  it('renders the TPEx row link as exactly /stocks/8069.TPEX (suffix preserved)', async () => {
    renderScreener()
    const link = await screen.findByRole('link', { name: /元太/ })
    expect(link).toHaveAttribute('href', '/stocks/8069.TPEX')
  })

  it('clicking the TWSE abnormal-diagnostics link navigates to the mocked /stocks/:symbol route', async () => {
    renderScreener()
    const link = await screen.findByRole('link', { name: /台積電/ })
    fireEvent.click(link)
    await waitFor(() => expect(screen.getByTestId('detail-mock')).toBeInTheDocument())
  })

  it('clicking the TPEx abnormal-diagnostics link navigates to the mocked /stocks/:symbol route', async () => {
    renderScreener()
    const link = await screen.findByRole('link', { name: /元太/ })
    fireEvent.click(link)
    await waitFor(() => expect(screen.getByTestId('detail-mock')).toBeInTheDocument())
  })

  it('main screener table stock link still points to /stocks/2330.TWSE (regression, unrelated to the fix)', async () => {
    renderScreener()
    await waitFor(() => expect(screen.getAllByText('2330.TWSE').length).toBeGreaterThan(0))
    const symbolLink = screen.getAllByRole('link', { name: '2330.TWSE' })[0]
    expect(symbolLink).toHaveAttribute('href', '/stocks/2330.TWSE')
  })
})

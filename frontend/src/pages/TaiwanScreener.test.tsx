// Phase 7K — TaiwanScreener 異常訊號面板導航正確性回歸測試
// 涵蓋：異常訊號面板列連結指向真實已註冊路由 /stocks/:symbol（保留交易所後綴），
// 不再指向不存在的 /taiwan/stocks/:symbol；主篩選表格既有正確連結行為不受影響。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'
import { MemoryRouter, Routes, Route, useSearchParams } from 'react-router-dom'
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
    // Phase 8C-A: 結果 row 直接操作 (自選/比較/監控)
    watchlistList: vi.fn(),
    watchlistAdd: vi.fn(),
    watchlistRemove: vi.fn(),
    watchlistGroups: vi.fn(),
    taiwanSearch: vi.fn(),
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
  return queryClient
}

function StockDetailMock() {
  // Reads the matched :symbol via window.location since useParams needs the route context;
  // simplest robust signal for these tests is just a stable marker element.
  return <div data-testid="detail-mock">stock-detail-page</div>
}

// Phase 8C-A: 驗證「加入比較」handoff 帶著 symbols query param 進入 /stocks/compare
// (MemoryRouter 不會同步真實 window.location, 需透過 useSearchParams 讀取)
function CompareMock() {
  const [params] = useSearchParams()
  return <div data-testid="compare-mock">{params.get('symbols')}</div>
}

function renderScreenerWithCompareRoute() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/taiwan-screener']}>
        <Routes>
          <Route path="/taiwan-screener" element={<TaiwanScreener />} />
          <Route path="/stocks/compare" element={<CompareMock />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return queryClient
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
  vi.mocked(api.watchlistList).mockResolvedValue({ symbols: [] } as any)
  vi.mocked(api.watchlistAdd).mockResolvedValue({ symbols: [] } as any)
  vi.mocked(api.watchlistRemove).mockResolvedValue({ symbols: [] } as any)
  vi.mocked(api.watchlistGroups).mockResolvedValue({ groups: [] } as any)
  vi.mocked(api.taiwanSearch).mockResolvedValue({ results: [] } as any)
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

describe('Main results row direct actions (Phase 8C-A)', () => {
  it('offers 加入自選/加入比較/新增監控/研究 without leaving the page first', async () => {
    renderScreener()
    await waitFor(() => expect(screen.getAllByText('2330.TWSE').length).toBeGreaterThan(0))

    expect(await screen.findByRole('button', { name: '將 2330.TWSE 加入自選' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '將 2330.TWSE 加入比較' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '為 2330.TWSE 新增監控' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '查看 2330.TWSE 詳細研究頁' })).toHaveAttribute('href', '/stocks/2330.TWSE')
  })

  it('加入自選 is a genuine 1-click quick-add — no group picker in the way (Phase 8C-A fix)', async () => {
    renderScreener()
    const addButton = await screen.findByRole('button', { name: '將 2330.TWSE 加入自選' })
    fireEvent.click(addButton)
    // 直接呼叫 watchlistAdd(symbol, '', undefined) → api.ts 正規化為 group_id=null
    // (與既有「未分組」選項相同 backend 語意), 不彈任何選單, 单次 click 即完成。
    await waitFor(() => expect(api.watchlistAdd).toHaveBeenCalledWith('2330.TWSE', '', undefined))
    expect(screen.queryByRole('menuitem', { name: /未分組/ })).not.toBeInTheDocument()
    // 仍停留在 Screener, 沒有被導去別的頁面
    expect(screen.queryByTestId('detail-mock')).not.toBeInTheDocument()
  })

  it('選擇分組加入自選 chevron still opens the group picker (specific-group path preserved)', async () => {
    vi.mocked(api.watchlistGroups).mockResolvedValue({
      groups: [{ id: 'g1', name: '半導體', color: 'blue' }],
    } as any)
    renderScreener()
    const chevron = await screen.findByRole('button', { name: '選擇分組將 2330.TWSE 加入自選' })
    fireEvent.click(chevron)
    const groupOption = await screen.findByRole('menuitem', { name: /半導體/ })
    fireEvent.click(groupOption)
    await waitFor(() => expect(api.watchlistAdd).toHaveBeenCalledWith('2330.TWSE', '', 'g1'))
  })

  it('加入比較 navigates straight to /stocks/compare with the symbol pre-populated', async () => {
    renderScreenerWithCompareRoute()
    const compareButton = await screen.findByRole('button', { name: '將 2330.TWSE 加入比較' })
    fireEvent.click(compareButton)
    await waitFor(() => expect(screen.getByTestId('compare-mock')).toHaveTextContent('2330.TWSE'))
  })

  it('監控 opens the rule editor scoped to the row symbol, without a second Monitor form', async () => {
    renderScreener()
    const monitorButton = await screen.findByRole('button', { name: '為 2330.TWSE 新增監控' })
    fireEvent.click(monitorButton)
    expect(await screen.findByText('監控標的 (Security Master)')).toBeInTheDocument()
    expect(screen.getByDisplayValue('2330.TWSE')).toBeInTheDocument()
  })
})

describe('Abnormal diagnostics panel state UX (Phase 7L)', () => {
  it('shows a loading row when the query is pending with no cached data', async () => {
    vi.mocked(api.taiwanAbnormalDiagnostics).mockReturnValue(new Promise(() => {}) as any)
    renderScreener()
    expect(await screen.findByText('正在載入異常診斷…')).toBeInTheDocument()
  })

  it('shows an error row when the query rejects with no cached data', async () => {
    vi.mocked(api.taiwanAbnormalDiagnostics).mockRejectedValue(new Error('network error'))
    renderScreener()
    expect(await screen.findByText('異常診斷載入失敗，請稍後再試')).toBeInTheDocument()
  })

  it('error state exposes an accessible alert, with no stack trace/raw exception text', async () => {
    vi.mocked(api.taiwanAbnormalDiagnostics).mockRejectedValue(new Error('some internal detail'))
    renderScreener()
    const alertEl = await screen.findByRole('alert')
    expect(alertEl).toHaveTextContent('異常診斷載入失敗，請稍後再試')
    expect(screen.queryByText(/some internal detail/)).not.toBeInTheDocument()
  })

  it('shows an explicit empty-success row when items is []', async () => {
    vi.mocked(api.taiwanAbnormalDiagnostics).mockResolvedValue(buildAbnormalDiagnostics([]) as any)
    renderScreener()
    expect(await screen.findByText('目前沒有偵測到異常訊號')).toBeInTheDocument()
  })

  it('empty-success state is not an alert', async () => {
    vi.mocked(api.taiwanAbnormalDiagnostics).mockResolvedValue(buildAbnormalDiagnostics([]) as any)
    renderScreener()
    await screen.findByText('目前沒有偵測到異常訊號')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('empty-success state does not use red error styling', async () => {
    vi.mocked(api.taiwanAbnormalDiagnostics).mockResolvedValue(buildAbnormalDiagnostics([]) as any)
    renderScreener()
    const cell = await screen.findByText('目前沒有偵測到異常訊號')
    expect(cell.className).not.toMatch(/text-red-400/)
  })

  it('renders populated abnormal-diagnostics rows on a successful non-empty response', async () => {
    renderScreener()
    expect(await screen.findByRole('link', { name: /台積電/ })).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: /元太/ })).toBeInTheDocument()
  })

  it('preserves already-loaded populated rows during a background refetch that subsequently errors (cached-data priority)', async () => {
    vi.mocked(api.taiwanAbnormalDiagnostics).mockResolvedValueOnce(
      buildAbnormalDiagnostics([buildAbnormalSignal({ symbol: '2330.TWSE', code: '2330', name: '台積電' })]) as any,
    )
    const queryClient = renderScreener()
    await screen.findByRole('link', { name: /台積電/ })

    // Simulate a background refetch (same query key, still 'ALL' filter — no user action needed
    // to change the key) that fails; cached data must remain visible, not be replaced by an error row.
    vi.mocked(api.taiwanAbnormalDiagnostics).mockRejectedValueOnce(new Error('refetch failed'))
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ['taiwanAbnormalDiagnostics', 'ALL'] })
    })

    expect(screen.getByRole('link', { name: /台積電/ })).toBeInTheDocument()
    expect(screen.queryByText('異常診斷載入失敗，請稍後再試')).not.toBeInTheDocument()
    expect(screen.queryByText('正在載入異常診斷…')).not.toBeInTheDocument()
  })
})

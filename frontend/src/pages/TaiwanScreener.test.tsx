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

// Phase 8C-C — Screener & Watchlist Refinement 回歸測試
function buildIndustryMetric(overrides: Partial<any> = {}) {
  return {
    industry: '半導體業', supported_symbol_count: 10, snapshot_symbol_count: 10, traded_symbol_count: 10,
    comparable_symbol_count: 10, turnover: 1_000_000, turnover_share: 0.1,
    advance_count: 5, decline_count: 3, flat_count: 2, uncompared_count: 0,
    advance_ratio: 0.5, decline_ratio: 0.3, average_change_pct: 0.01, median_change_pct: 0.01,
    foreign_net: null, investment_trust_net: null, dealer_net: null,
    margin_balance_change: null, short_balance_change: null,
    relative_strength_5d: null, relative_strength_20d: null,
    relative_strength_5d_comparable_count: 0, relative_strength_20d_comparable_count: 0,
    top_gainers: [], top_losers: [], top_turnover: [],
    ...overrides,
  }
}

function buildIndustryIntelligence(industries: ReturnType<typeof buildIndustryMetric>[]) {
  return {
    trade_date: '2026-08-28', generated_at: '2026-08-28T16:00:00+08:00',
    market_reference: { trade_date: '2026-08-28', total_stock_turnover: 1, market_equal_weight_return_5d: null, market_equal_weight_return_20d: null, comparable_stocks_5d_count: 0, comparable_stocks_20d_count: 0 },
    industries,
    data_quality: {
      target_trade_date: '2026-08-28', previous_trade_date: '2026-08-27', base_date_5d: null, base_date_20d: null,
      supported_stock_count: industries.length, classified_stock_count: industries.length, unclassified_stock_count: 0,
      etfs_excluded_count: 0, industry_count: industries.length, classification_coverage_pct: 100,
      daily_status: 'current', institutional_status: 'current', margin_status: 'current', overall_status: 'complete',
    },
  }
}

describe('Filter hierarchy — basic vs. advanced (Phase 8C-C)', () => {
  it('basic filters render by default: market/instrument/change-pct/price/volume/amount', async () => {
    renderScreener()
    expect(await screen.findByText('市場交易所')).toBeInTheDocument()
    expect(screen.getByText('標的型態')).toBeInTheDocument()
    expect(screen.getByText('漲跌幅區間 (%)')).toBeInTheDocument()
    expect(screen.getByText('價格區間 (TWD)')).toBeInTheDocument()
    expect(screen.getByText('最低成交量 (張)')).toBeInTheDocument()
    expect(screen.getByText('最低成交金額 (百萬 TWD)')).toBeInTheDocument()
  })

  it('advanced filters are collapsed by default (RSI/institutional/margin fields not shown)', async () => {
    renderScreener()
    await screen.findByText('市場交易所')
    expect(screen.queryByText('RSI (14) 區間')).not.toBeInTheDocument()
    expect(screen.queryByText('外資買賣超區間 (張)')).not.toBeInTheDocument()
    expect(screen.queryByText('券資比最低 (%)')).not.toBeInTheDocument()
    const toggle = screen.getByRole('button', { name: /進階條件/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
  })

  it('expanding advanced filters reveals technical/institutional/margin groups, original controls still work', async () => {
    renderScreener()
    const toggle = await screen.findByRole('button', { name: /進階條件/ })
    fireEvent.click(toggle)

    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('技術面')).toBeInTheDocument()
    expect(screen.getByText('法人')).toBeInTheDocument()
    expect(screen.getByText('籌碼與融資券')).toBeInTheDocument()
    expect(screen.getByText('RSI (14) 區間')).toBeInTheDocument()
    expect(screen.getByText('外資買賣超區間 (張)')).toBeInTheDocument()
    expect(screen.getByText('券資比最低 (%)')).toBeInTheDocument()

    // 原本站上 MA20 按鈕行為未被破壞
    const ma20Button = screen.getByRole('button', { name: '站上 MA20 (月線)' })
    fireEvent.click(ma20Button)
    await waitFor(() => {
      const lastCall = vi.mocked(api.taiwanScreenerRun).mock.calls.at(-1)![0] as any
      expect(lastCall.above_ma20).toBe(true)
    })
  })
})

describe('Quick presets (Phase 8C-C)', () => {
  it('clicking 強勢股 preset updates existing filter state (above_ma20 + momentum_5d_min), not a second screener logic', async () => {
    renderScreener()
    const preset = await screen.findByRole('button', { name: '強勢股' })
    fireEvent.click(preset)

    await waitFor(() => {
      const lastCall = vi.mocked(api.taiwanScreenerRun).mock.calls.at(-1)![0] as any
      expect(lastCall.above_ma20).toBe(true)
      expect(lastCall.momentum_5d_min).toBeCloseTo(0.02)
    })
    expect(await screen.findByRole('button', { name: '✓ 強勢股', pressed: true })).toBeInTheDocument()
  })

  it('clicking an already-active preset clears just its own fields', async () => {
    renderScreener()
    const preset = await screen.findByRole('button', { name: '放量股' })
    fireEvent.click(preset)
    const active = await screen.findByRole('button', { name: '✓ 放量股' })
    expect(active).toHaveAttribute('aria-pressed', 'true')

    fireEvent.click(active)
    // 已清除: 按鈕回到未 active 的原始標籤與 aria-pressed=false
    // (以 UI 狀態驗證, 不看 network mock 呼叫次數 — react-query 對「回到與初次
    // 掛載時完全相同的預設 payload」可能命中既有快取而不重新呼叫 queryFn)。
    expect(await screen.findByRole('button', { name: '放量股' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.queryByRole('button', { name: '✓ 放量股' })).not.toBeInTheDocument()
  })

  it('重置所有篩選 clears an active preset and returns to default filter state', async () => {
    renderScreener()
    const preset = await screen.findByRole('button', { name: '法人買超' })
    fireEvent.click(preset)
    await screen.findByRole('button', { name: '✓ 法人買超' })

    fireEvent.click(screen.getByRole('button', { name: '重置所有篩選' }))
    const reverted = await screen.findByRole('button', { name: '法人買超' })
    expect(reverted).toHaveAttribute('aria-pressed', 'false')
    expect(screen.queryByRole('button', { name: '✓ 法人買超' })).not.toBeInTheDocument()
  })

  it('row actions (自選/比較/監控/研究) remain available after applying a preset', async () => {
    renderScreener()
    fireEvent.click(await screen.findByRole('button', { name: '突破轉強' }))
    await waitFor(() => expect(screen.getAllByText('2330.TWSE').length).toBeGreaterThan(0))
    expect(screen.getByRole('button', { name: '將 2330.TWSE 加入自選' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '將 2330.TWSE 加入比較' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '為 2330.TWSE 新增監控' })).toBeInTheDocument()
  })
})

describe('Industry name display (Phase 8C-C)', () => {
  it('renders the readable industry name from taiwan-industry-intelligence as-is (no numeric code)', async () => {
    vi.mocked(api.taiwanIndustryIntelligence).mockResolvedValue(
      buildIndustryIntelligence([buildIndustryMetric({ industry: '半導體業' })]) as any,
    )
    renderScreener()
    // 產業名稱按鈕的可存取名稱直接就是可讀中文名稱, 而不是像 "24"/"91" 這種
    // 數字代碼 — 確認 UI 消費既有 industry-intelligence API 回傳的 industry
    // 欄位時原樣顯示, 沒有在前端另外做代碼轉換或截斷。
    expect(await screen.findByRole('button', { name: '半導體業' })).toBeInTheDocument()
  })

  it('industry intelligence table defaults to top 10 rows with an expand control for the rest', async () => {
    const many = Array.from({ length: 15 }, (_, i) => buildIndustryMetric({ industry: `產業${i + 1}` }))
    vi.mocked(api.taiwanIndustryIntelligence).mockResolvedValue(buildIndustryIntelligence(many) as any)
    renderScreener()

    await screen.findByRole('button', { name: '產業1' })
    expect(screen.queryByRole('button', { name: '產業15' })).not.toBeInTheDocument()
    const expandButton = screen.getByRole('button', { name: /顯示全部 15 檔/ })
    expect(expandButton).toHaveAttribute('aria-expanded', 'false')

    fireEvent.click(expandButton)
    expect(await screen.findByRole('button', { name: '產業15' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '收合 ▴' })).toHaveAttribute('aria-expanded', 'true')
  })
})

describe('Results table density (Phase 8C-C)', () => {
  it('default view hides secondary technical/institutional columns behind 更多欄位', async () => {
    renderScreener()
    await waitFor(() => expect(screen.getAllByText('2330.TWSE').length).toBeGreaterThan(0))
    expect(screen.queryByText('投信買賣超')).not.toBeInTheDocument()
    expect(screen.queryByText('RSI (14)')).not.toBeInTheDocument()

    const toggle = screen.getByRole('button', { name: /更多欄位/ })
    fireEvent.click(toggle)
    expect(await screen.findByText('投信買賣超')).toBeInTheDocument()
    expect(screen.getByText('RSI (14)')).toBeInTheDocument()
  })
})

// Data Freshness & Source Labels batch (Post-8C follow-up)：Market Intelligence
// 的 trade_date 是「應有最新交易日」(resolve_target_latest_trading_date, 依當下
// 時間推算)，不是本地實際持有資料的日期。當該日尚無完整市場資料時，下方統計
// 全部是 0 —— 若沒有明確標示，容易被誤讀為「今日零成交」而非「無資料」。
function buildMarketIntelligence(overrides: Partial<Record<string, any>> = {}) {
  return {
    trade_date: '2026-09-08',
    generated_at: '2026-09-08T16:00:00+08:00',
    market_totals: {
      supported_count: 2375, snapshot_row_count: 0, traded_count: 0,
      advance_count: 0, decline_count: 0, flat_count: 0, uncompared_count: 0,
      upper_limit_count: 0, lower_limit_count: 0, turnover: 0,
    },
    by_exchange: { twse: { turnover: 0 } as any, tpex: { turnover: 0 } as any },
    by_instrument: { stock: {} as any, etf: {} as any },
    institutional: { trade_date: '2026-09-08', row_count: 0, foreign_net: null, investment_trust_net: null, dealer_net: null, total_net: null, status: 'unavailable' },
    margin: { trade_date: '2026-09-08', row_count: 0, margin_balance: null, margin_balance_change: null, short_balance: null, short_balance_change: null, aggregate_short_margin_ratio: null, status: 'unavailable' },
    indexes: { taiex: null, tpex_index: null },
    data_quality: { target_trade_date: '2026-09-08', previous_trade_date: '2026-09-03', overall_status: 'unavailable', universe_supported_symbols: 2375, daily_snapshot_symbols: 0, missing_symbols_count: 2375 },
    ...overrides,
  }
}

describe('Market Intelligence date honesty (Data Freshness & Source Labels batch)', () => {
  it('shows an explicit "no complete data" notice when data_quality.overall_status is not complete, instead of silently presenting all-zero stats as today\'s real market', async () => {
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(buildMarketIntelligence() as any)
    renderScreener()

    await screen.findByText('交易日: 2026-09-08')
    expect(screen.getByText('尚無完整市場資料，以下統計非今日實際行情')).toBeInTheDocument()
  })

  it('does not show the notice when data_quality.overall_status is complete (genuine zero-activity day stays unflagged)', async () => {
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(
      buildMarketIntelligence({ data_quality: { target_trade_date: '2026-09-08', previous_trade_date: '2026-09-05', overall_status: 'complete', universe_supported_symbols: 2375, daily_snapshot_symbols: 2375, missing_symbols_count: 0 } }) as any,
    )
    renderScreener()

    await screen.findByText('交易日: 2026-09-08')
    expect(screen.queryByText('尚無完整市場資料，以下統計非今日實際行情')).not.toBeInTheDocument()
  })
})

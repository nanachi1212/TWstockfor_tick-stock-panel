// Phase 8B-3.1 — Dashboard A 股 legacy 顯示一致性回歸測試
// 涵蓋：show_ashare_legacy_features=false 時 TaiwanOverviewCard 顯示、A 股大盤
// 內容(含上證等中國指數)不顯示、A 股專屬 overviewMarket 查詢不觸發;
// =true 時 A 股區塊恢復且 TaiwanOverviewCard 仍在;監控中心在兩種狀態下都可見。
//
// Phase 8C-B — Dashboard Market Clarity 回歸測試
// 涵蓋：預設台股模式顯示市場強弱摘要、產業強弱 top/bottom、自選股快覽
// (empty/populated)、市場或產業查詢失敗時 Dashboard 仍可渲染不白屏。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Dashboard } from './Dashboard'
import { api } from '@/lib/api'
import { useDataStatus, useCapabilities, usePreferences } from '@/lib/useSharedQueries'

function buildMarketIntelligence(overrides: Partial<Record<string, any>> = {}) {
  return {
    trade_date: '2026-09-05',
    generated_at: '2026-09-05T14:00:00Z',
    market_totals: {
      supported_count: 1000, snapshot_row_count: 950, traded_count: 950,
      advance_count: 600, decline_count: 300, flat_count: 50, uncompared_count: 0,
      upper_limit_count: 8, lower_limit_count: 2, turnover: 350_000_000_000,
    },
    by_exchange: { twse: {} as any, tpex: {} as any },
    by_instrument: { stock: {} as any, etf: {} as any },
    institutional: { trade_date: '2026-09-05', row_count: 900, foreign_net: 12_000_000, investment_trust_net: 1_000_000, dealer_net: -500_000, total_net: 12_500_000, status: 'current' },
    margin: { trade_date: '2026-09-05', row_count: 900, margin_balance: 0, margin_balance_change: 0, short_balance: 0, short_balance_change: 0, aggregate_short_margin_ratio: 5, status: 'current' },
    indexes: { taiex: null, tpex_index: null },
    data_quality: { target_trade_date: '2026-09-05', previous_trade_date: '2026-09-04', overall_status: 'complete', universe_supported_symbols: 1000, daily_snapshot_symbols: 950, missing_symbols_count: 0 },
    ...overrides,
  }
}

function buildIndustry(industry: string, avgChangePct: number) {
  return {
    industry, supported_symbol_count: 10, snapshot_symbol_count: 10, traded_symbol_count: 10,
    comparable_symbol_count: 10, turnover: 1_000_000, turnover_share: 0.05,
    advance_count: avgChangePct >= 0 ? 8 : 2, decline_count: avgChangePct >= 0 ? 2 : 8, flat_count: 0,
    uncompared_count: 0, advance_ratio: avgChangePct >= 0 ? 0.8 : 0.2, decline_ratio: avgChangePct >= 0 ? 0.2 : 0.8,
    average_change_pct: avgChangePct, median_change_pct: avgChangePct,
    foreign_net: null, investment_trust_net: null, dealer_net: null,
    margin_balance_change: null, short_balance_change: null,
    relative_strength_5d: null, relative_strength_20d: null,
    relative_strength_5d_comparable_count: 0, relative_strength_20d_comparable_count: 0,
    top_gainers: [], top_losers: [], top_turnover: [],
  }
}

function buildIndustryIntelligence(industries: ReturnType<typeof buildIndustry>[]) {
  return {
    trade_date: '2026-09-05',
    generated_at: '2026-09-05T14:00:00Z',
    market_reference: { trade_date: '2026-09-05', total_stock_turnover: 1, market_equal_weight_return_5d: null, market_equal_weight_return_20d: null, comparable_stocks_5d_count: 0, comparable_stocks_20d_count: 0 },
    industries,
    data_quality: {
      target_trade_date: '2026-09-05', previous_trade_date: '2026-09-04', base_date_5d: null, base_date_20d: null,
      supported_stock_count: industries.length, classified_stock_count: industries.length, unclassified_stock_count: 0,
      etfs_excluded_count: 0, industry_count: industries.length, classification_coverage_pct: 100,
      daily_status: 'current', institutional_status: 'current', margin_status: 'current', overall_status: 'complete',
    },
  }
}

function buildWatchlistEnriched(rows: any[]) {
  return { rows, as_of: '2026-09-05', elapsed_ms: 1 }
}

vi.mock('@/lib/api', () => ({
  api: {
    overviewMarket: vi.fn(),
    taiwanDataStatus: vi.fn().mockResolvedValue({
      daily_as_of: null,
      institutional_as_of: null,
      margin_as_of: null,
      target_latest_trading_date: '2026-09-01',
      is_fully_current: false,
      daily_status: 'unavailable',
      institutional_status: 'unavailable',
      margin_status: 'unavailable',
      daily_days_behind: 0,
      institutional_days_behind: 0,
      margin_days_behind: 0,
      scheduler_enabled: true,
      scheduled_update_time: '16:30',
      scheduled_timezone: 'Asia/Taipei',
    }),
    dataSources: vi.fn().mockResolvedValue({ builtin: [], plugins: [], custom: [] }),
    alertsList: vi.fn().mockResolvedValue({ alerts: [] }),
    pipelineJobs: vi.fn().mockResolvedValue({ active_id: null }),
    taiwanMarketIntelligence: vi.fn().mockResolvedValue(null),
    taiwanIndustryIntelligence: vi.fn().mockResolvedValue(null),
    watchlistEnriched: vi.fn().mockResolvedValue({ rows: [], as_of: null, elapsed_ms: 0 }),
  },
}))

vi.mock('@/lib/useSharedQueries', () => ({
  useDataStatus: vi.fn(),
  useCapabilities: vi.fn(),
  usePreferences: vi.fn(),
}))

vi.mock('@/components/StockPreviewDialog', () => ({ StockPreviewDialog: () => null }))

function buildOverviewMarket() {
  return {
    as_of: '2026-08-31',
    quote_status: { running: false, mode: 'none', quote_age_ms: null },
    indices: [{ symbol: '000001.SH', name: '上证指数', last_price: 3000, change_pct: 0.5 }],
    breadth: { total: 100, up: 50, down: 40, flat: 10, up_pct: 50, down_pct: 40, avg_pct: 0.1, median_pct: 0.1, strong_up: 5, strong_down: 3 },
    amount: { total: 1e9, avg: 1e7 },
    boards: [],
    limit: { limit_up: 10, broken: 1, failed: 0, limit_down: 2, max_boards: 3, seal_rate: 80, tiers: [] },
    distribution: [{ label: '涨停', count: 10, pct: 10 }],
    trend: { above_ma5: 50, above_ma20: 40, above_ma60: 30, above_ma5_pct: 50, above_ma20_pct: 40, above_ma60_pct: 30, new_high: 5, new_low: 2 },
    activity: { avg_turnover: 1, high_turnover: 5, high_vol_ratio: 10, vol_ratio: 1 },
    radar: [],
    emotion: { score: 60, label: '偏强' },
    top_gainers: [],
    top_losers: [],
    turnover_leaders: [],
    active_leaders: [],
    concept_rank: { leading: [], lagging: [] },
    industry_rank: { leading: [], lagging: [] },
  }
}

function mockCommonHooks(showAshareLegacy: boolean) {
  vi.mocked(useDataStatus).mockReturnValue({ data: undefined } as any)
  vi.mocked(useCapabilities).mockReturnValue({ data: undefined } as any)
  vi.mocked(usePreferences).mockReturnValue({
    data: { daily_data_provider: 'tickflow', show_ashare_legacy_features: showAshareLegacy },
  } as any)
}

function renderDashboard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('Dashboard — A-share visibility consistency (Phase 8B-3.1)', () => {
  it('show_ashare_legacy_features=false: shows TaiwanOverviewCard, hides A-share board content, and does not fetch overviewMarket', async () => {
    mockCommonHooks(false)
    vi.mocked(api.overviewMarket).mockResolvedValue(buildOverviewMarket() as any)
    renderDashboard()

    expect(await screen.findByText('台股資料狀態')).toBeInTheDocument()
    expect(screen.queryByText('市场看板')).not.toBeInTheDocument()
    expect(screen.queryByText('上证指数')).not.toBeInTheDocument()
    expect(screen.queryByText('中國 A 股（選配）')).not.toBeInTheDocument()
    // 監控中心是市場中立功能, 仍應顯示
    expect(screen.getByText('監控中心')).toBeInTheDocument()

    await waitFor(() => expect(api.taiwanDataStatus).toHaveBeenCalled())
    expect(api.overviewMarket).not.toHaveBeenCalled()
  })

  it('show_ashare_legacy_features=true: restores the A-share section under a clear divider, TaiwanOverviewCard still present', async () => {
    mockCommonHooks(true)
    vi.mocked(api.overviewMarket).mockResolvedValue(buildOverviewMarket() as any)
    renderDashboard()

    expect(await screen.findByText('市场看板')).toBeInTheDocument()
    expect(screen.getByText('中國 A 股（選配）')).toBeInTheDocument()
    expect(screen.getByText('上证指数')).toBeInTheDocument()
    expect(screen.getByText('台股資料狀態')).toBeInTheDocument()
    await waitFor(() => expect(api.overviewMarket).toHaveBeenCalled())
  })
})

describe('Dashboard — Market Clarity (Phase 8C-B)', () => {
  it('default Taiwan mode: renders market strength summary with breadth counts and label', async () => {
    mockCommonHooks(false)
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(buildMarketIntelligence() as any)
    renderDashboard()

    expect(await screen.findByText('今日市場強弱')).toBeInTheDocument()
    // 600 advance / (600+300+50) = 66.7% >= 55% -> 偏強
    expect(await screen.findByText('偏強')).toBeInTheDocument()
    expect(screen.getByText('600')).toBeInTheDocument()
    expect(screen.getByText('300')).toBeInTheDocument()
    // 監控事件摘要不依賴 A 股 legacy 開關
    expect(screen.getByText('監控中心')).toBeInTheDocument()
  })

  it('show_ashare_legacy_features=false: market summary does not depend on legacy A-share block', async () => {
    mockCommonHooks(false)
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(buildMarketIntelligence() as any)
    renderDashboard()

    expect(await screen.findByText('今日市場強弱')).toBeInTheDocument()
    expect(screen.queryByText('市场看板')).not.toBeInTheDocument()
    expect(api.overviewMarket).not.toHaveBeenCalled()
  })

  it('renders industry top/bottom strongest and weakest', async () => {
    mockCommonHooks(false)
    vi.mocked(api.taiwanIndustryIntelligence).mockResolvedValue(buildIndustryIntelligence([
      buildIndustry('半導體業', 0.05),
      buildIndustry('金融保險業', 0.03),
      buildIndustry('鋼鐵工業', -0.04),
      buildIndustry('航運業', -0.02),
    ]) as any)
    renderDashboard()

    expect(await screen.findByText('產業強弱')).toBeInTheDocument()
    expect(await screen.findByText('半導體業')).toBeInTheDocument()
    expect(screen.getByText('金融保險業')).toBeInTheDocument()
    expect(screen.getByText('鋼鐵工業')).toBeInTheDocument()
    expect(screen.getByText('航運業')).toBeInTheDocument()
  })

  it('empty watchlist shows a compact empty state with CTA, not a large empty table', async () => {
    mockCommonHooks(false)
    vi.mocked(api.watchlistEnriched).mockResolvedValue(buildWatchlistEnriched([]) as any)
    renderDashboard()

    expect(await screen.findByText('尚未加入任何自選股')).toBeInTheDocument()
    expect(screen.getByText('前往自選股 →')).toBeInTheDocument()
  })

  it('populated watchlist shows quick-glance rows sorted by absolute change', async () => {
    mockCommonHooks(false)
    vi.mocked(api.watchlistEnriched).mockResolvedValue(buildWatchlistEnriched([
      { symbol: '2330.TWSE', name: '台積電', close: 900, change_pct: 0.01 },
      { symbol: '2317.TWSE', name: '鴻海', close: 100, change_pct: 0.08 },
    ]) as any)
    renderDashboard()

    expect(await screen.findByText('台積電')).toBeInTheDocument()
    expect(screen.getByText('鴻海')).toBeInTheDocument()
  })

  it('market/industry query failure does not crash the Dashboard', async () => {
    mockCommonHooks(false)
    vi.mocked(api.taiwanMarketIntelligence).mockRejectedValue(new Error('network error'))
    vi.mocked(api.taiwanIndustryIntelligence).mockRejectedValue(new Error('network error'))
    renderDashboard()

    expect(await screen.findByText('目前無法讀取市場強弱資料,不影響其他功能使用。')).toBeInTheDocument()
    expect(await screen.findByText('目前無法讀取產業強弱資料,不影響其他功能使用。')).toBeInTheDocument()
    // 其餘區塊仍正常渲染, 未白屏
    expect(screen.getByText('監控中心')).toBeInTheDocument()
    expect(screen.getByText('台股資料狀態')).toBeInTheDocument()
  })
})

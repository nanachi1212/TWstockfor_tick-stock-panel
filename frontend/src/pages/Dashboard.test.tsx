// Phase 8C-D — Legacy Product Removal: 中國 A 股 legacy 大盤看板整段已隨產品
// 介面移除, show_ashare_legacy_features 開關本身也已移除, Dashboard 全站僅
// 剩台股內容, 不再有任何 legacy 開關/查詢殘留。
//
// Phase 8C-B — Dashboard Market Clarity 回歸測試
// 涵蓋：市場強弱摘要、產業強弱 top/bottom、自選股快覽 (empty/populated)、
// 市場或產業查詢失敗時 Dashboard 仍可渲染不白屏。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Dashboard } from './Dashboard'
import { api } from '@/lib/api'

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
    taiwanQuantEvaluation: vi.fn().mockResolvedValue({
      status: 'processing',
      generated_at: '2026-09-24T15:00:00+08:00',
      evaluation_status: 'waiting_for_data_health',
      evaluation_timestamp: null,
      evaluation_provenance: null,
      available_horizons: [5, 20],
      data_health: { status: 'blocked', primary_oos_ready: false, highest_level: 'ready_for_training', blocked_reasons: {} },
      a2b: { completed: 194, pending: 284, failed: 0, total: 478, worker_status: 'running' },
      evaluation: null,
      blocking_reasons: ['A2b classification is processing'],
      ranking_modes: { live_current: '/api/taiwan/quant/live/models', historical_oos: '/api/taiwan/quant/evaluation' },
    }),
    taiwanQuantA2bStatus: vi.fn().mockResolvedValue({ completed: 232, pending: 246, failed: 0, total: 478, worker_status: 'running' }),
    taiwanQuantLiveModels: vi.fn().mockResolvedValue({ configured_model: { model_key: 'live-model', top_n: 10 }, latest_operation: null, expected_session: null, current_run_valid: false, current_run_audit_status: null, current_run_reason: 'session_unavailable' }),
    taiwanQuantLiveRuns: vi.fn().mockResolvedValue({ runs: [] }),
    taiwanQuantLiveRun: vi.fn(),
    taiwanQuantEvaluateAlerts: vi.fn().mockResolvedValue({ ok: true, status: 'available', alerts: [] }),
    taiwanQuotes: vi.fn().mockResolvedValue({ quotes: [], count: 0 }),
    watchlistList: vi.fn().mockResolvedValue({ symbols: [] }),
    watchlistAdd: vi.fn().mockResolvedValue({ ok: true }),
    watchlistRemove: vi.fn().mockResolvedValue({ symbols: [] }),
    alertsList: vi.fn().mockResolvedValue({ alerts: [] }),
    alertsMarkRead: vi.fn().mockResolvedValue({ ok: true }),
    alertsMarkAllRead: vi.fn().mockResolvedValue({ ok: true, updated: 1 }),
    alertDeleteById: vi.fn().mockResolvedValue({ ok: true }),
    taiwanMarketIntelligence: vi.fn().mockResolvedValue(null),
    taiwanIndustryIntelligence: vi.fn().mockResolvedValue(null),
    watchlistEnriched: vi.fn().mockResolvedValue({ rows: [], as_of: null, elapsed_ms: 0 }),
  },
}))

vi.mock('@/components/StockPreviewDialog', () => ({ StockPreviewDialog: () => null }))

function renderDashboard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  function CurrentPath() {
    return <output data-testid="current-path">{useLocation().pathname}</output>
  }
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <><CurrentPath /><Dashboard /></>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('Dashboard — Legacy A-share removal (Phase 8C-D)', () => {
  it('renders Taiwan content only: no legacy A-share board, no legacy divider, no legacy API calls', async () => {
    renderDashboard()

    expect(await screen.findByText('台股資料狀態')).toBeInTheDocument()
    expect(screen.queryByText('市场看板')).not.toBeInTheDocument()
    expect(screen.queryByText('中國 A 股（選配）')).not.toBeInTheDocument()
    expect(screen.queryByText('上证指数')).not.toBeInTheDocument()
    // 提醒中心是市場中立功能, 不受 legacy 移除影響
    expect(screen.getByText('提醒')).toBeInTheDocument()
    // legacy A 股 API 不應存在於 api mock 上, 更不會被呼叫
    expect((api as any).overviewMarket).toBeUndefined()
  })
})

describe('Dashboard — Market Clarity (Phase 8C-B)', () => {
  it('renders market strength summary with breadth counts and label', async () => {
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(buildMarketIntelligence() as any)
    renderDashboard()

    expect(await screen.findByText('今日市場強弱')).toBeInTheDocument()
    // 600 advance / (600+300+50) = 66.7% >= 55% -> 偏強
    expect(await screen.findByText('偏強')).toBeInTheDocument()
    expect(screen.getByText('600')).toBeInTheDocument()
    expect(screen.getByText('300')).toBeInTheDocument()
    expect(screen.getByText('提醒')).toBeInTheDocument()
  })

  it('renders industry top/bottom strongest and weakest', async () => {
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
    vi.mocked(api.watchlistEnriched).mockResolvedValue(buildWatchlistEnriched([]) as any)
    renderDashboard()

    expect(await screen.findByText('尚未加入任何自選股')).toBeInTheDocument()
    expect(screen.getByText('前往自選股 →')).toBeInTheDocument()
  })

  it('populated watchlist shows quick-glance rows sorted by absolute change', async () => {
    vi.mocked(api.watchlistEnriched).mockResolvedValue(buildWatchlistEnriched([
      { symbol: '2330.TWSE', name: '台積電', close: 900, change_pct: 0.01 },
      { symbol: '2317.TWSE', name: '鴻海', close: 100, change_pct: 0.08 },
    ]) as any)
    renderDashboard()

    expect(await screen.findByText('台積電')).toBeInTheDocument()
    expect(screen.getByText('鴻海')).toBeInTheDocument()
  })

  it('my observations keep unranked symbols visible and support detail/remove actions', async () => {
    vi.mocked(api.watchlistList).mockResolvedValue({ symbols: [
      { symbol: '2330.TWSE', name: '台積電', added_at: '2026-09-05T09:00:00Z' },
      { symbol: '2317.TWSE', name: '鴻海', added_at: '2026-09-05T09:01:00Z' },
    ] } as any)
    vi.mocked(api.watchlistEnriched).mockResolvedValue(buildWatchlistEnriched([
      { symbol: '2330.TWSE', name: '台積電', close: 900, change_pct: 0.01 },
      { symbol: '2317.TWSE', name: '鴻海', close: null, change_pct: null },
    ]) as any)
    vi.mocked(api.taiwanQuantLiveModels).mockResolvedValue({
      configured_model: { model_key: 'live-model', top_n: 10 },
      latest_operation: null,
      expected_session: '2026-09-05',
      current_run_valid: true,
      current_run_audit_status: 'ok',
      current_run_reason: 'current',
    } as any)
    vi.mocked(api.taiwanQuantLiveRuns).mockResolvedValue({ runs: [{
      model_key: 'live-model', session: '2026-09-05', snapshot_hash: 'hash',
      frozen_at: '2026-09-05T16:00:00Z', signal_count: 1,
    }] } as any)
    vi.mocked(api.taiwanQuantLiveRun).mockResolvedValue({
      model_key: 'live-model', session: '2026-09-05', snapshot_hash: 'hash',
      frozen_at: '2026-09-05T16:00:00Z', audit_status: 'ok',
      snapshot: {
        signal_session: '2026-09-05', usage_scope: 'live', validation_state: 'validated',
        model: { model_key: 'live-model', top_n: 10, validation_state: 'validated' },
        signals: [{ symbol: '2330.TWSE', score: 0.9, rank: 1, selected: true, reference_close: 900, feature_percentiles: { momentum_5d: 0.8 } }],
        features: [],
      },
    } as any)
    renderDashboard()

    expect(await screen.findByText('我的觀察')).toBeInTheDocument()
    expect(await screen.findByText(/#1 · Quant \+90\.00%/)).toBeInTheDocument()
    expect(screen.getByText('今日未進入 Top 10')).toBeInTheDocument()
    expect(screen.getByLabelText('查看 2330.TWSE 詳情')).toHaveAttribute('href', '/stocks/2330.TWSE')

    fireEvent.click(screen.getByLabelText('移除 2317.TWSE 觀察'))
    await waitFor(() => expect(api.watchlistRemove).toHaveBeenCalledWith('2317.TWSE'))
  })

  it('market/industry query failure does not crash the Dashboard', async () => {
    vi.mocked(api.taiwanMarketIntelligence).mockRejectedValue(new Error('network error'))
    vi.mocked(api.taiwanIndustryIntelligence).mockRejectedValue(new Error('network error'))
    renderDashboard()

    expect(await screen.findByText('目前無法讀取市場強弱資料,不影響其他功能使用。')).toBeInTheDocument()
    expect(await screen.findByText('目前無法讀取產業強弱資料,不影響其他功能使用。')).toBeInTheDocument()
    // 其餘區塊仍正常渲染, 未白屏
    expect(screen.getByText('提醒')).toBeInTheDocument()
    expect(screen.getByText('台股資料狀態')).toBeInTheDocument()
  })
})

describe('Dashboard — stock reminders', () => {
  it('opens the stock detail route from an alert and supports read state', async () => {
    vi.mocked(api.alertsList).mockResolvedValue({ alerts: [{
      ts: Date.now(), alert_id: 'alert-1', is_read: false, rule_id: 'rule-1',
      source: 'price', type: 'price_above', symbol: '2330.TWSE', name: '台積電',
      message: '股價高於 1000 元', price: 1001, severity: 'info',
    }], total: 1 } as any)
    renderDashboard()

    fireEvent.click(await screen.findByTitle('查看 2330.TWSE 個股詳情'))
    expect(screen.getByTestId('current-path')).toHaveTextContent('/stocks/2330.TWSE')

    fireEvent.click(screen.getByRole('button', { name: '標記 2330.TWSE 已讀' }))
    await waitFor(() => expect(api.alertsMarkRead).toHaveBeenCalled())
    expect(vi.mocked(api.alertsMarkRead).mock.calls[0][0]).toBe('alert-1')
  })
})

// DAILY_USE_CORE_UX_FIXES (P1-1) — 「今日市場強弱」不可把 data_quality
// 不完整時的 0/0/0 當成正式市場結果呈現; 完整資料時(即使真的全 0)則照實顯示。
describe('Dashboard — Market data honesty (DAILY_USE_CORE_UX_FIXES P1-1)', () => {
  it('A. complete data renders normally, without the incomplete-data notice', async () => {
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(buildMarketIntelligence() as any)
    renderDashboard()

    expect(await screen.findByText('今日市場強弱')).toBeInTheDocument()
    expect(await screen.findByText('600')).toBeInTheDocument()
    expect(screen.queryByText(/今日市場資料尚未完整/)).not.toBeInTheDocument()
  })

  it('B. partial data shows a clear incomplete-data notice', async () => {
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(buildMarketIntelligence({
      data_quality: { target_trade_date: '2026-09-09', previous_trade_date: '2026-09-03', overall_status: 'partial', universe_supported_symbols: 2375, daily_snapshot_symbols: 0, missing_symbols_count: 2375 },
    }) as any)
    renderDashboard()

    expect(await screen.findByText(/今日市場資料尚未完整/)).toBeInTheDocument()
    expect(screen.getByText(/前一交易日：2026-09-03/)).toBeInTheDocument()
    expect(screen.queryByText(/已知最新資料|最新資料日期|非今日實際行情/)).not.toBeInTheDocument()
  })

  it('C. partial data with all-zero totals still shows the notice, not a bare 0/0/0 result', async () => {
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(buildMarketIntelligence({
      market_totals: {
        supported_count: 2375, snapshot_row_count: 0, traded_count: 0,
        advance_count: 0, decline_count: 0, flat_count: 0, uncompared_count: 0,
        upper_limit_count: 0, lower_limit_count: 0, turnover: 0,
      },
      data_quality: { target_trade_date: '2026-09-09', previous_trade_date: '2026-09-03', overall_status: 'partial', universe_supported_symbols: 2375, daily_snapshot_symbols: 0, missing_symbols_count: 2375 },
    }) as any)
    renderDashboard()

    expect(await screen.findByText(/今日市場資料尚未完整/)).toBeInTheDocument()
    // 0/0/0 本身仍可顯示(誠實反映 API 回傳值), 但必須伴隨明確提示, 而非唯一線索
    expect(screen.getAllByText('0').length).toBeGreaterThan(0)
  })

  it('D. strength label (偏強/偏弱/中性) is suppressed while data is incomplete, even if the ratio would otherwise qualify', async () => {
    // advance 600 / (600+300+50) ≈ 66.7% -> 若資料完整會判為偏強, 但這裡
    // overall_status = partial, 不應顯示任何 deterministic 強弱結論。
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(buildMarketIntelligence({
      data_quality: { target_trade_date: '2026-09-09', previous_trade_date: '2026-09-03', overall_status: 'partial', universe_supported_symbols: 2375, daily_snapshot_symbols: 950, missing_symbols_count: 1425 },
    }) as any)
    renderDashboard()

    expect(await screen.findByText(/今日市場資料尚未完整/)).toBeInTheDocument()
    expect(screen.queryByText('偏強')).not.toBeInTheDocument()
    expect(screen.queryByText('偏弱')).not.toBeInTheDocument()
    expect(screen.queryByText('中性')).not.toBeInTheDocument()
  })
})

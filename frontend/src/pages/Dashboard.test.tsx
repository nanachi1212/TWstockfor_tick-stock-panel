// Phase 8B-3.1 — Dashboard A 股 legacy 顯示一致性回歸測試
// 涵蓋：show_ashare_legacy_features=false 時 TaiwanOverviewCard 顯示、A 股大盤
// 內容(含上證等中國指數)不顯示、A 股專屬 overviewMarket 查詢不觸發;
// =true 時 A 股區塊恢復且 TaiwanOverviewCard 仍在;監控中心在兩種狀態下都可見。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Dashboard } from './Dashboard'
import { api } from '@/lib/api'
import { useDataStatus, useCapabilities, usePreferences } from '@/lib/useSharedQueries'

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

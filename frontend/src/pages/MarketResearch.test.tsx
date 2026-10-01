import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { MarketResearch } from './MarketResearch'
import { api } from '@/lib/api'
import { rotationPoints, type ResearchMetric, type InvestorStatistics, type IndustryRotationRow } from '@/lib/marketResearch'

vi.mock('@/lib/api', () => ({ api: { taiwanInstitutionalStatistics: vi.fn(), taiwanIndustryRotation: vi.fn() } }))
vi.mock('echarts-for-react', () => ({ default: () => <div data-testid="rotation-chart" /> }))

function metric(value: number | null, status: ResearchMetric['status'] = 'available'): ResearchMetric {
  return { value, unit: 'shares', status, source: ['TWSE:T86'], date: '2026-09-30', as_of: '2026-09-30', coverage: { expected_days: 5, coverage_days: status === 'partial' ? 4 : 5, expected_observations: 5, observed_observations: 5, missing_dates: status === 'partial' ? ['2026-09-28'] : [] } }
}
function stats(value: number): InvestorStatistics {
  return { net_shares: metric(value), net_lots: metric(value / 1000), net_volume_ratio: metric(0.15), buy_streak: metric(5), sell_streak: metric(0), streak_capped: true }
}
function snapshot() {
  const investors = { foreign: stats(7500), investment_trust: stats(-2500), dealer: stats(0) }
  return { date: '2026-09-30', window: 5 as const, sessions: [], universe: 'active_supported_stocks_and_etfs',
    aggregates: [{ symbol: 'ALL', name: 'ALL', exchange: 'ALL', investors }],
    securities: [{ symbol: '2330.TWSE', name: '台積電', exchange: 'TWSE', investors }] }
}
function industry(status: ResearchMetric['status'] = 'available'): IndustryRotationRow {
  return { industry: '半導體業', relative_strength_5d: metric(0.01, status), relative_strength_20d: metric(0.02, status), turnover_share: metric(0.4, status), average_turnover_share_20d: metric(0.35, status), turnover_share_delta_pp: metric(5, status) }
}
function mount(url = '/market-research') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[url]}><MarketResearch /></MemoryRouter></QueryClientProvider>)
}
beforeEach(() => {
  vi.mocked(api.taiwanInstitutionalStatistics).mockResolvedValue(snapshot())
  vi.mocked(api.taiwanIndustryRotation).mockResolvedValue({ date: '2026-09-30', price_semantics: 'raw_close', benchmark: 'active_supported_stocks_equal_weight', delta_definition: 'current-minus-20d-mean', industries: [industry()] })
})
afterEach(() => { vi.clearAllMocks() })

describe('Market research product UI', () => {
  it('shows units, metadata, stock navigation and investor switching', async () => {
    mount()
    expect(await screen.findByRole('link', { name: /台積電/ })).toHaveAttribute('href', '/stocks/2330.TWSE')
    expect(screen.getAllByText('買賣超（張）')).toHaveLength(2)
    fireEvent.change(screen.getByLabelText('數量單位'), { target: { value: 'shares' } })
    expect(screen.getAllByText('買賣超（股）')).toHaveLength(2)
    expect(screen.getAllByText('+7,500')).toHaveLength(2)
    expect(screen.getAllByText('來源：TWSE:T86').length).toBeGreaterThan(0)
    fireEvent.change(screen.getByLabelText('法人'), { target: { value: 'investment_trust' } })
    expect(screen.getAllByText('-2,500')).toHaveLength(2)
    fireEvent.change(screen.getByLabelText('搜尋標的'), { target: { value: '不存在' } })
    expect(screen.getByText('沒有符合條件的標的。')).toBeInTheDocument()
  })
  it('uses date and all five windows as independent query contexts', async () => {
    mount('/market-research?date=2026-09-30&window=5')
    await screen.findByRole('link', { name: /台積電/ })
    for (const window of [10, 20, 45, 60]) {
      fireEvent.change(screen.getByLabelText('交易日窗口'), { target: { value: String(window) } })
      await waitFor(() => expect(api.taiwanInstitutionalStatistics).toHaveBeenLastCalledWith('2026-09-30', window))
    }
    fireEvent.change(screen.getByLabelText('交易日'), { target: { value: '2026-09-29' } })
    await waitFor(() => expect(api.taiwanInstitutionalStatistics).toHaveBeenLastCalledWith('2026-09-29', 60))
  })
  it('shows partial and unavailable values without converting them to zero', async () => {
    const data = snapshot()
    data.securities[0].investors.foreign.net_lots = metric(null, 'unavailable')
    data.securities[0].investors.foreign.buy_streak = metric(2, 'partial')
    vi.mocked(api.taiwanInstitutionalStatistics).mockResolvedValue(data)
    mount()
    await screen.findByRole('link', { name: /台積電/ })
    expect(screen.getAllByText('不可用 · 5/5 日').length).toBeGreaterThan(0)
    expect(screen.getAllByText('部分資料 · 4/5 日').length).toBeGreaterThan(0)
    expect(screen.getAllByText('—').length).toBeGreaterThan(0)
  })
  it('shows loading, request failure and retry', async () => {
    vi.mocked(api.taiwanInstitutionalStatistics).mockRejectedValueOnce(new Error('offline'))
    mount()
    expect(screen.getByRole('status')).toHaveTextContent('載入資料中')
    expect(await screen.findByRole('alert')).toHaveTextContent('offline')
    fireEvent.click(screen.getByText('重試'))
    await screen.findByRole('link', { name: /台積電/ })
  })
  it('provides rotation and reserved URL tabs without making unrelated requests', async () => {
    mount('/market-research?tab=rotation')
    expect(await screen.findByTestId('rotation-chart')).toBeInTheDocument()
    expect(api.taiwanInstitutionalStatistics).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('tab', { name: /大盤寬度/ }))
    expect(screen.getByText('大盤寬度功能尚未提供，Phase 1 保留此分頁入口。')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: /估值/ }))
    expect(screen.getByText('估值功能尚未提供，Phase 1 保留此分頁入口。')).toBeInTheDocument()
  })
  it('omits incomplete points and keeps RS and percentage-point units separate', () => {
    expect(rotationPoints([industry(), industry('partial'), industry('unavailable')])).toEqual([{ name: '半導體業', value: [2, 5] }])
  })
})

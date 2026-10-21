import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { MarketResearch } from './MarketResearch'
import { api } from '@/lib/api'
import { rotationPoints, type BigHolderRow, type GroupRankingRow, type GroupRankingSnapshot, type HeatmapIndustry, type MarketHeatmapSnapshot, type ResearchMetric, type InvestorStatistics, type IndustryRotationRow } from '@/lib/marketResearch'

vi.mock('@/lib/api', () => ({ api: { taiwanInstitutionalStatistics: vi.fn(), taiwanIndustryRotation: vi.fn(), marketBreadthValuation: vi.fn(), refreshMarketValuation: vi.fn(), taiwanBigHolderRanking: vi.fn(), taiwanMarketHeatmap: vi.fn(), taiwanGroupRanking: vi.fn() } }))
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
  vi.mocked(api.marketBreadthValuation).mockResolvedValue({ contract_version: 1, generated_at: '', requested_as_of: '2026-09-30', as_of: null, market: 'composite', sections: 'all', status: 'unavailable', stale: false, history: [], latest: null, valuation: null, warnings: [], usage_scope: 'descriptive_history', strategy_lab_eligible: false })
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
  it('mounts breadth and valuation in the existing shell with independent sections', async () => {
    mount('/market-research?tab=rotation')
    expect(await screen.findByTestId('rotation-chart')).toBeInTheDocument()
    expect(api.taiwanInstitutionalStatistics).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('tab', { name: /大盤寬度/ }))
    expect(await screen.findByText(/尚無可觀測日 K/)).toBeInTheDocument()
    expect(api.marketBreadthValuation).toHaveBeenCalledWith(undefined, 'composite', 20, 'breadth')
    expect(screen.queryByText('更新官方估值')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: /估值/ }))
    expect(screen.getByText('更新官方估值')).toBeInTheDocument()
    await waitFor(() => expect(api.marketBreadthValuation).toHaveBeenLastCalledWith(undefined, 'composite', 20, 'valuation'))
    expect(api.taiwanInstitutionalStatistics).not.toHaveBeenCalled()
  })
  it('preserves URL date and market across research tabs and resets to latest', async () => {
    mount('/market-research?tab=breadth&date=2026-09-29&market=TWSE')
    await waitFor(() => expect(api.marketBreadthValuation).toHaveBeenCalledWith('2026-09-29', 'TWSE', 20, 'breadth'))
    fireEvent.change(screen.getByLabelText('研究市場'), { target: { value: 'TPEX' } })
    await waitFor(() => expect(api.marketBreadthValuation).toHaveBeenLastCalledWith('2026-09-29', 'TPEX', 20, 'breadth'))
    fireEvent.click(screen.getByRole('tab', { name: '估值' }))
    expect(screen.getByLabelText('研究市場')).toHaveValue('TPEX')
    expect(screen.getByLabelText('研究日期')).toHaveValue('2026-09-29')
    fireEvent.click(screen.getByText('最新可觀測交易日'))
    await waitFor(() => expect(api.marketBreadthValuation).toHaveBeenLastCalledWith(undefined, 'TPEX', 20, 'valuation'))
    expect(api.taiwanInstitutionalStatistics).not.toHaveBeenCalled()
    expect(api.taiwanIndustryRotation).not.toHaveBeenCalled()
  })
  it('shows big-holder rankings while the 4-week base is still accumulating', async () => {
    const row = (symbol: string, name: string, change: number): BigHolderRow => ({ symbol, name, exchange: 'TWSE', big_holder_pct: 63.4, total_holders: 219588,
      change_1w_pp: change, holders_change_1w_pct: 1.2, change_4w_pp: null, holders_change_4w_pct: null, tone: 'insufficient', in_watchlist: false })
    vi.mocked(api.taiwanBigHolderRanking).mockResolvedValue({ date: '2026-10-16', previous_1w_date: '2026-10-08', previous_4w_date: null,
      min_holders: 1000, threshold_pp: 0.5, universe_count: 1940,
      concentrating: [row('6669.TWSE', '緯穎', 10.5)], dispersing: [row('3227.TPEX', '原相', -6.77)], watchlist: [] })
    mount('/market-research?tab=big-holders')
    expect(await screen.findByRole('link', { name: /緯穎/ })).toHaveAttribute('href', '/stocks/6669.TWSE')
    expect(screen.getByText('+10.50')).toBeInTheDocument()
    expect(screen.getByText(/目前依 1 週變化排序/)).toHaveTextContent('前 4 週 尚未累積')
    expect(screen.getAllByText('資料累積中')).toHaveLength(1)
    fireEvent.click(screen.getByRole('tab', { name: /籌碼渙散/ }))
    expect(screen.getByRole('link', { name: /原相/ })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /緯穎/ })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: /自選/ }))
    expect(screen.getByText('自選股中沒有在範圍內的股票。')).toBeInTheDocument()
    expect(api.taiwanInstitutionalStatistics).not.toHaveBeenCalled()
  })
  it('shows the money-flow heatmap totals and industry table for a past date', async () => {
    const sector = (industry: string, amount: number, change_pct: number | null): HeatmapIndustry => ({ industry, amount, share: amount / 123_456_000_000, change_pct,
      advance: 1, decline: 1, flat: 0, count: 2, stocks: [{ symbol: '2330.TWSE', name: '台積電', close: 1030, change_pct: 0.03, amount, volume: 1000 }] })
    const snapshot: MarketHeatmapSnapshot = { date: '2026-10-20', previous_date: '2026-10-19', status: 'available', reason: null, price_semantics: 'raw_close',
      total_amount: 123_456_000_000, previous_total_amount: 102_880_000_000, advance: 2, decline: 2, flat: 0, uncompared: 2,
      industries: [sector('半導體業', 100_000_000_000, 0.0175), sector('ETF', 20_000_000_000, 0.01), sector('UNCLASSIFIED', 3_456_000_000, null)] }
    vi.mocked(api.taiwanMarketHeatmap).mockResolvedValue(snapshot)
    mount('/market-research?tab=heatmap&date=2026-10-20')
    expect(await screen.findByText('1,234.56 億')).toBeInTheDocument()
    expect(screen.getByText('+20.00%')).toBeInTheDocument()
    expect(screen.getByText('目標日期：2026-10-20')).toBeInTheDocument()
    const rows = screen.getAllByRole('row').slice(1)
    expect(rows).toHaveLength(3)
    expect(within(rows[0]).getByText('半導體業')).toBeInTheDocument()
    expect(within(rows[0]).getByText('+1.75%')).toHaveClass('text-bull')
    expect(within(rows[2]).getByText('未分類')).toBeInTheDocument()
    expect(within(rows[2]).getByText('—')).toBeInTheDocument()
    expect(screen.getByTestId('rotation-chart')).toBeInTheDocument()
    expect(api.taiwanMarketHeatmap).toHaveBeenCalledWith('2026-10-20')
    expect(api.taiwanInstitutionalStatistics).not.toHaveBeenCalled()
  })
  it('shows a notice instead of an error when the heatmap date has no daily data', async () => {
    vi.mocked(api.taiwanMarketHeatmap).mockResolvedValue({ date: '2026-10-21', previous_date: null, status: 'unavailable', reason: 'no_daily_data', price_semantics: 'raw_close',
      total_amount: 0, previous_total_amount: null, advance: 0, decline: 0, flat: 0, uncompared: 0, industries: [] })
    mount('/market-research?tab=heatmap')
    expect(await screen.findByText('2026-10-21 尚無日資料')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByTestId('rotation-chart')).not.toBeInTheDocument()
  })
  it('ranks watchlist groups with rank moves and low-turnover exclusions', async () => {
    const group = (id: string, name: string, rank: number, rank_change: number, items: GroupRankingRow['items']): GroupRankingRow => ({ id, name, color: 'rose', rank, score: 0.02 / rank,
      weighted_avg_change: 0.015, average_change: 0.01, median_change: 0.01, members: items.length + 1, quoted: items.length, used: items.filter(i => !i.excluded).length,
      excluded: items.filter(i => i.excluded).length, advance: 1, decline: 0, flat: 0, breadth: 1, amount: 4e9, top_symbol: items[0].symbol, items, previous_rank: rank + rank_change, rank_change })
    const item = (symbol: string, name: string, excluded: boolean) => ({ symbol, name, close: 100, change_pct: 0.02, amount: excluded ? 5e6 : 4e9, share: excluded ? 0.001 : 0.999, weight: excluded ? 0 : 1, excluded })
    const snapshot: GroupRankingSnapshot = { date: '2026-10-21', previous_date: '2026-10-20', status: 'available', reason: null, price_semantics: 'raw_close',
      rules: { min_share: 0.005, min_amount: 1e7 },
      groups: [group('g1', 'AI 伺服器', 1, 1, [item('2382.TWSE', '廣達', false), item('6669.TWSE', '緯穎', true)]), group('g2', '散熱', 2, -1, [item('3017.TWSE', '奇鋐', false)])] }
    vi.mocked(api.taiwanGroupRanking).mockResolvedValue(snapshot)
    mount('/market-research?tab=groups&date=2026-10-21')
    expect(await screen.findByText('▼1')).toHaveClass('text-bear')
    expect(screen.getAllByText(/^(AI 伺服器|散熱)$/).map(e => e.textContent)).toEqual(['AI 伺服器', '散熱'])
    expect(screen.getByText('▲1')).toHaveClass('text-bull')
    expect(screen.getAllByText('低量排除')).toHaveLength(1)
    expect(screen.getByRole('link', { name: /緯穎/ })).toHaveAttribute('href', '/stocks/6669.TWSE')
    expect(screen.getByText('目標日期：2026-10-21')).toBeInTheDocument()
    expect(api.taiwanGroupRanking).toHaveBeenCalledWith('2026-10-21')
    expect(api.taiwanInstitutionalStatistics).not.toHaveBeenCalled()
  })
  it('points to the watchlist when no group exists yet', async () => {
    vi.mocked(api.taiwanGroupRanking).mockResolvedValue({ date: '2026-10-21', previous_date: '2026-10-20', status: 'available', reason: null, price_semantics: 'raw_close',
      rules: { min_share: 0.005, min_amount: 1e7 }, groups: [] })
    mount('/market-research?tab=groups')
    expect(await screen.findByText(/尚未建立自選分組/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '前往自選' })).toHaveAttribute('href', '/watchlist')
  })
  it('omits incomplete points and keeps RS and percentage-point units separate', () => {
    expect(rotationPoints([industry(), industry('partial'), industry('unavailable')])).toEqual([{ name: '半導體業', value: [2, 5] }])
  })
})

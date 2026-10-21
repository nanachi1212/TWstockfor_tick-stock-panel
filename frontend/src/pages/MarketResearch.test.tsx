import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { MarketResearch } from './MarketResearch'
import { api } from '@/lib/api'
import { rotationPoints, type BigHolderRow, type DayCoverage, type GroupRankingRow, type GroupRankingSnapshot, type HeatmapIndustry, type MarketHeatmapSnapshot, type PatternScanSnapshot, type ResearchMetric, type InvestorStatistics, type IndustryRotationRow } from '@/lib/marketResearch'

vi.mock('@/lib/api', () => ({ api: { taiwanInstitutionalStatistics: vi.fn(), taiwanIndustryRotation: vi.fn(), marketBreadthValuation: vi.fn(), refreshMarketValuation: vi.fn(), taiwanBigHolderRanking: vi.fn(), taiwanMarketHeatmap: vi.fn(), taiwanGroupRanking: vi.fn(), taiwanPatternScan: vi.fn() } }))
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
const complete: DayCoverage = { status: 'complete', missing_symbols: [], missing_count: 0, previous_status: 'complete',
  exchanges: { TWSE: { census: 'observed', expected: 1334, observed: 1334, missing: 0 }, TPEX: { census: 'observed', expected: 1009, observed: 1009, missing: 0 } } }

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
      coverage: complete, total_amount: 123_456_000_000, previous_total_amount: 102_880_000_000, advance: 2, decline: 2, flat: 0, uncompared: 2,
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
    expect(screen.queryByText(/日資料不完整/)).not.toBeInTheDocument()
  })
  it('warns that a partial heatmap day does not represent the whole market', async () => {
    vi.mocked(api.taiwanMarketHeatmap).mockResolvedValue({ date: '2026-10-16', previous_date: '2026-10-15', status: 'partial', reason: 'incomplete_daily_coverage', price_semantics: 'raw_close',
      coverage: { ...complete, status: 'partial', missing_symbols: ['8908.TWSE', '6488.TPEX'], missing_count: 2, previous_status: 'partial',
        exchanges: { TWSE: { census: 'observed', expected: 1333, observed: 1332, missing: 1 }, TPEX: { census: 'observed', expected: 1004, observed: 1003, missing: 1 } } },
      total_amount: 1e11, previous_total_amount: null, advance: 1, decline: 0, flat: 0, uncompared: 0,
      industries: [{ industry: '半導體業', amount: 1e11, share: 1, change_pct: 0.01, advance: 1, decline: 0, flat: 0, count: 1, stocks: [] }] })
    mount('/market-research?tab=heatmap&date=2026-10-16')
    const notice = await screen.findByText('2026-10-16 日資料不完整：缺 2 檔（TWSE 1／TPEX 1），總成交金額與占比不代表全市場')
    expect(notice).toHaveAttribute('title', '8908.TWSE、6488.TPEX')
    expect(screen.getByText('較前日（前日資料不完整）')).toBeInTheDocument()
    expect(screen.getByText('1,000.00 億')).toBeInTheDocument()
  })
  it('shows a notice instead of an error when the heatmap date has no daily data', async () => {
    vi.mocked(api.taiwanMarketHeatmap).mockResolvedValue({ date: '2026-10-21', previous_date: null, status: 'unavailable', reason: 'no_daily_data', price_semantics: 'raw_close',
      coverage: null, total_amount: 0, previous_total_amount: null, advance: 0, decline: 0, flat: 0, uncompared: 0, industries: [] })
    mount('/market-research?tab=heatmap')
    expect(await screen.findByText('2026-10-21 尚無日資料')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByTestId('rotation-chart')).not.toBeInTheDocument()
  })
  it('ranks watchlist groups with rank moves and low-turnover exclusions', async () => {
    const group = (id: string, name: string, rank: number, rank_change: number, items: GroupRankingRow['items']): GroupRankingRow => ({ id, name, color: 'rose', rank, score: 0.02 / rank,
      weighted_avg_change: 0.015, average_change: 0.01, median_change: 0.01, members: items.length + 1, quoted: items.length, used: items.filter(i => !i.excluded).length,
      excluded: items.filter(i => i.excluded).length, advance: 1, decline: 0, flat: 0, breadth: 1, amount: 4e9, top_symbol: items[0].symbol,
      missing_symbols: [], complete: true, items, previous_rank: rank + rank_change, rank_change })
    const item = (symbol: string, name: string, excluded: boolean) => ({ symbol, name, close: 100, change_pct: 0.02, amount: excluded ? 5e6 : 4e9, share: excluded ? 0.001 : 0.999, weight: excluded ? 0 : 1, excluded })
    const snapshot: GroupRankingSnapshot = { date: '2026-10-21', previous_date: '2026-10-20', status: 'available', reason: null, price_semantics: 'raw_close',
      rules: { min_share: 0.005, min_amount: 1e7 }, coverage: complete,
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
    expect(screen.queryByText(/缺 \d+ 檔/)).not.toBeInTheDocument()

    const partial = { ...snapshot, status: 'partial' as const, reason: 'incomplete_daily_coverage' as const,
      coverage: { ...complete, status: 'partial' as const, missing_symbols: ['2359.TWSE'], missing_count: 1, exchanges: { TWSE: { census: 'observed' as const, expected: 3, observed: 2, missing: 1 } } },
      groups: [{ ...snapshot.groups[0], missing_symbols: ['2359.TWSE'], complete: false }, snapshot.groups[1]] }
    vi.mocked(api.taiwanGroupRanking).mockResolvedValue(partial)
    cleanup()
    mount('/market-research?tab=groups&date=2026-10-21')
    expect(await screen.findByText('缺 1 檔')).toHaveAttribute('title', '2359.TWSE')
    expect(screen.getByText('2026-10-21 日資料不完整：缺 1 檔（TWSE 1），名次可能因缺資料而失真')).toBeInTheDocument()
  })
  it('points to the watchlist when no group exists yet', async () => {
    vi.mocked(api.taiwanGroupRanking).mockResolvedValue({ date: '2026-10-21', previous_date: '2026-10-20', status: 'available', reason: null, price_semantics: 'raw_close',
      rules: { min_share: 0.005, min_amount: 1e7 }, coverage: null, groups: [] })
    mount('/market-research?tab=groups')
    expect(await screen.findByText(/尚未建立自選分組/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '前往自選' })).toHaveAttribute('href', '/watchlist')
  })
  it('lists pattern candidates with tick-grid plan levels and a plan-wide reward-to-risk', async () => {
    const plan = { entry: 94.7, entry_max: 94.7, stop: 78.4, target: 107, rr: 0.7, risk_percent: 17.2, chased: false, action: '報酬風險比不足，暫不進場',
      confirmation: '收盤高於 93.4', defense: '跌回頸線下方 0.35 ATR', timing: '確認後分段', target_basis: '量度位', missing: '未核對法說' }
    const snapshot: PatternScanSnapshot = { date: '2026-10-21', from: '2026-06-10', status: 'available', reason: null,
      price_semantics: 'adjusted_for_pattern_raw_for_levels', rules: { window: 90, lot_size: 1000, min_avg_volume_lots: 300, excluded_industries: ['金融'], rr_threshold: 1.5, cost_rate: 0.006 },
      universe: 1983, eligible: 819, matched: 1, pattern_count: 1, excluded: { low_liquidity: 720 },
      items: [{ symbol: '2330.TWSE', code: '2330', name: '台積電', industry: '半導體業', price: 90, avg_volume_lots: 1000, pattern: {
        id: 'double', name: '雙底', category: '反轉型', score: 64, status: '接近突破', start: '2026-06-30', end: '2026-10-14', breakout_date: null,
        description: '兩個低點接近', anchors: [], resistance: 93, support: 79, atr: 2.7, volume_ratio: 1, volume_status: 'verified', score_parts: { geometry: 38.9, confirmation: 8, volume: 6.7, trend: 10 },
        plan, warnings: ['型態由數值規則辨識，請覆核圖形'] } }] }
    vi.mocked(api.taiwanPatternScan).mockResolvedValue(snapshot)
    mount('/market-research?tab=patterns&date=2026-10-21')
    expect(await screen.findByRole('link', { name: /2330 台積電/ })).toHaveAttribute('href', '/stocks/2330.TWSE')
    expect(screen.getByText('雙底')).toBeInTheDocument()
    expect(screen.getByText('接近突破')).toBeInTheDocument()
    expect(screen.getByText('報酬風險比不足，暫不進場')).toBeInTheDocument()
    expect(screen.getByText('0.70')).toHaveClass('text-muted')
    expect(screen.getByText('64')).toBeInTheDocument()
    expect(screen.getByText(/五日均量不足 300 張 720/)).toBeInTheDocument()
    expect(api.taiwanPatternScan).toHaveBeenCalledWith('2026-10-21')
  })
  it('explains why no pattern is shown when corporate actions cannot be verified', async () => {
    vi.mocked(api.taiwanPatternScan).mockResolvedValue({ date: '2026-10-21', from: null, status: 'unavailable', reason: 'corporate_action_coverage_unavailable',
      price_semantics: 'adjusted_for_pattern_raw_for_levels', rules: { window: 90, lot_size: 1000, min_avg_volume_lots: 300, excluded_industries: [], rr_threshold: 1.5, cost_rate: 0.006 },
      universe: 0, eligible: 0, matched: 0, pattern_count: 0, excluded: {}, items: [] })
    mount('/market-research?tab=patterns')
    expect(await screen.findByText(/除權息資料未驗證，無法還原價格/)).toBeInTheDocument()
  })
  it('omits incomplete points and keeps RS and percentage-point units separate', () => {
    expect(rotationPoints([industry(), industry('partial'), industry('unavailable')])).toEqual([{ name: '半導體業', value: [2, 5] }])
  })
})

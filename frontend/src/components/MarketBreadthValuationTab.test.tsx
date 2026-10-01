import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { BreadthValuationResponse, ResearchMetric, ValuationMetric } from '@/lib/marketBreadthTypes'
import { MarketBreadthValuationTab } from './MarketBreadthValuationTab'

vi.mock('@/lib/api', () => ({ api: { marketBreadthValuation: vi.fn(), refreshMarketValuation: vi.fn() } }))

const metric: ResearchMetric = { value: 0.5, included_count: 10, excluded_count: 2,
  excluded_reason_counts: { insufficient_history: 2 }, coverage: 10 / 12, status: 'partial', unit: 'ratio', numerator: 5 }
const valuation: ValuationMetric = { ...metric, value: 20, percentile: null,
  percentile_sample_count: 3, percentile_start: '2026-09-25', percentile_end: '2026-09-29', percentile_status: 'insufficient_history' }

function response(): BreadthValuationResponse {
  const latest: BreadthValuationResponse['latest'] = {
    as_of: '2026-09-30', market: 'composite', source: ['taiwan_daily_store'], retrieved_at: null,
    available_at: null, universe_label: '依可觀測日 K 計算的研究統計', universe_status: 'observed',
    historical_eligibility_status: 'unverified', eligibility_verified_count: 10, eligibility_unknown_count: 2,
    price_retrieved_at_status: 'unavailable_in_legacy_daily_store', eligibility_retrieved_at: null, missing_markets: [],
    universe_complete: false, coverage_denominator: 'observable_bars_and_verified_observed_stocks',
    usage_scope: 'descriptive_history', strategy_lab_eligible: false,
    metrics: { ma20: metric, ma60: metric, ma240: metric, new_high_52w: { ...metric, unit: 'stocks', value: 5 },
      new_low_52w: { ...metric, unit: 'stocks', value: 1 }, ad_net: { ...metric, unit: 'stocks', value: 3 },
      ad_line: { ...metric, unit: 'stocks', value: 10 } },
    advances: 6, declines: 3, unchanged: 1, ad_segment_start: '2026-09-01',
  }
  return { contract_version: 1, generated_at: '2026-10-01T10:00:00+08:00', requested_as_of: '2026-09-30',
    as_of: '2026-09-30', market: 'composite', sections: 'all', status: 'partial', stale: false, history: [latest], latest,
    valuation: { market: 'composite', as_of: '2026-09-30', source: ['twse:valuation', 'tpex:valuation'],
      source_urls: [], retrieved_at: '2026-10-01T10:00:00+08:00', available_at: null,
      publication_time_status: 'unverified', usage_scope: 'descriptive_history', strategy_lab_eligible: false,
      methodology: 'individual_stock_median', composite_method: 'pooled_same_session_individual_records',
      coverage_denominator: 'observed_valuation_records_union_observed_bars', universe_complete: false,
      status: 'partial', metrics: { pe: valuation, pb: valuation, dividend_yield: valuation } },
    warnings: ['無可驗證首次發布時間，僅供描述性歷史研究，不可接 Strategy Lab。'],
    usage_scope: 'descriptive_history', strategy_lab_eligible: false }
}

function mount(props = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><MarketBreadthValuationTab {...props} /></QueryClientProvider>)
}

afterEach(() => { vi.resetAllMocks() })

describe('MarketBreadthValuationTab', () => {
  it('keeps breadth and valuation content in their respective shell tabs', async () => {
    vi.mocked(api.marketBreadthValuation).mockResolvedValue(response())
    const breadthView = mount({ view: 'breadth', asOf: '', market: 'composite' })
    await screen.findByText('高於 MA20')
    expect(screen.queryByText('個股本益比中位數')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('研究日期')).not.toBeInTheDocument()
    expect(api.marketBreadthValuation).toHaveBeenCalledWith(undefined, 'composite', 20, 'breadth')
    breadthView.unmount()
    mount({ view: 'valuation' })
    await screen.findByText('個股本益比中位數')
    expect(screen.queryByText('高於 MA20')).not.toBeInTheDocument()
    expect(screen.queryByText('檢視逐日寬度與 A/D Line')).not.toBeInTheDocument()
  })

  it('loads valuation while breadth is pending and labels historical revisions', async () => {
    vi.mocked(api.marketBreadthValuation).mockImplementation((_date, _market, _days, sections) =>
      sections === 'breadth' ? new Promise(() => {}) : Promise.resolve({ ...response(), sections: 'valuation', latest: null, history: [] }))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const view = render(<QueryClientProvider client={client}><MarketBreadthValuationTab asOf="2026-09-29" view="breadth" /></QueryClientProvider>)
    expect(screen.getByText('載入大盤研究資料中…')).toBeInTheDocument()
    view.rerender(<QueryClientProvider client={client}><MarketBreadthValuationTab asOf="2026-09-29" view="valuation" /></QueryClientProvider>)
    await screen.findByText('個股本益比中位數')
    expect(screen.getByText('歷史日期估值使用目前保存的最新 revision，非 Historical PIT。')).toBeInTheDocument()
    expect(screen.queryByText(/尚無可觀測日 K/)).not.toBeInTheDocument()
  })

  it('shows honest sample coverage, exclusions, descriptive scope, null rank and provenance', async () => {
    vi.mocked(api.marketBreadthValuation).mockResolvedValue(response())
    mount()
    expect(await screen.findByText('高於 MA20')).toBeInTheDocument()
    expect(within(screen.getAllByRole('article')[0]).getByText('50.0%')).toBeInTheDocument()
    expect(screen.getAllByText('新上市或歷史不足：2').length).toBeGreaterThan(0)
    expect(screen.getByText(/完整歷史普通股 universe 涵蓋率未知/)).toBeInTheDocument()
    expect(screen.getByText(/不可接 Strategy Lab/)).toBeInTheDocument()
    expect(screen.getAllByText(/歷史 percentile：歷史不足/)).toHaveLength(3)
    expect(screen.queryByText('0.0%')).not.toBeInTheDocument()
    expect(screen.getByText(/首次發布時間尚未驗證/)).toBeInTheDocument()
  })

  it('changes context keys and forwards the shell date and market', async () => {
    vi.mocked(api.marketBreadthValuation).mockResolvedValue(response())
    const view = mount({ asOf: '2026-09-29', market: 'TWSE' })
    await waitFor(() => expect(api.marketBreadthValuation).toHaveBeenCalledWith('2026-09-29', 'TWSE', 20, 'all'))
    expect(screen.queryByLabelText('研究市場')).not.toBeInTheDocument()
    view.unmount()
    mount()
    fireEvent.change(screen.getByLabelText('研究市場'), { target: { value: 'TPEX' } })
    await waitFor(() => expect(api.marketBreadthValuation).toHaveBeenCalledWith(undefined, 'TPEX', 20, 'all'))
    fireEvent.change(screen.getByLabelText('研究日期'), { target: { value: '2026-09-28' } })
    await waitFor(() => expect(api.marketBreadthValuation).toHaveBeenCalledWith('2026-09-28', 'TPEX', 20, 'all'))
  })

  it('shows loading and unavailable data without inventing zeros', async () => {
    let resolve!: (data: BreadthValuationResponse) => void
    vi.mocked(api.marketBreadthValuation).mockReturnValue(new Promise(r => { resolve = r }))
    mount()
    expect(screen.getByText('載入大盤研究資料中…')).toBeInTheDocument()
    resolve({ ...response(), status: 'unavailable', latest: null, valuation: null, history: [], as_of: null })
    expect(await screen.findByText(/尚無可觀測日 K/)).toBeInTheDocument()
    expect(screen.queryByText('高於 MA20')).not.toBeInTheDocument()
  })

  it('shows error and retry plus a stale snapshot warning', async () => {
    vi.mocked(api.marketBreadthValuation).mockRejectedValueOnce(new Error('unavailable'))
    vi.mocked(api.marketBreadthValuation).mockResolvedValueOnce({ ...response(), stale: true, status: 'partial', requested_as_of: '2026-10-01' })
    mount()
    expect(await screen.findByRole('alert')).toHaveTextContent('大盤研究資料讀取失敗')
    fireEvent.click(screen.getByText('重試'))
    expect(await screen.findByText(/此為較舊快照/)).toBeInTheDocument()
  })

  it('disables refresh while pending, invalidates data and reports isolated source failure', async () => {
    vi.mocked(api.marketBreadthValuation).mockResolvedValue(response())
    let finish!: (result: Awaited<ReturnType<typeof api.refreshMarketValuation>>) => void
    vi.mocked(api.refreshMarketValuation).mockReturnValue(new Promise(resolve => { finish = resolve }))
    mount()
    await screen.findByText('高於 MA20')
    fireEvent.click(screen.getByText('更新官方估值'))
    await waitFor(() => expect(screen.getByText('更新估值中…')).toBeDisabled())
    finish({ status: 'partial', records_saved: 1083, failed: [{ market: 'TPEX', reason: 'failed' }],
      as_of: ['2026-09-30'], usage_scope: 'descriptive_history', available_at: null })
    expect(await screen.findByText(/保留原有資料/)).toBeInTheDocument()
    await waitFor(() => expect(api.marketBreadthValuation).toHaveBeenCalledTimes(2))
  })
})

// DAILY_USE_CORE_UX_FIXES (P1-2) — TaiwanStockDetail 返回按鈕回歸測試。
// 通用 idx-based 判斷邏輯已在 useSafeBack.test.tsx 完整驗證; 這裡只驗證本頁
// 真的接上了它 —— 不再寫死「返回即時監控」文案/目的地, 且能在有可信 in-app
// 來源(如監控中心)時正確返回該來源, 直接網址進入時安全落到台股選股 fallback。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { TaiwanStockDetail } from './TaiwanStockDetail'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

vi.mock('@/lib/api', () => ({
  api: {
    taiwanSearch: vi.fn().mockResolvedValue({ results: [] }),
    watchlistList: vi.fn().mockResolvedValue({ symbols: [] }),
    watchlistAdd: vi.fn(),
    watchlistRemove: vi.fn(),
    watchlistGroups: vi.fn().mockResolvedValue({ groups: [] }),
    taiwanStockDetail: vi.fn().mockResolvedValue({
      symbol: '2330.TWSE',
      identity: { symbol: '2330.TWSE', code: '2330', name: '台積電', exchange: 'TWSE', industry: null, is_supported: true, etf_category: null },
      price_limit: { rule_type: '10%', is_no_limit: false, limit_up: 110, limit_down: 90 },
      realtime: { last_price: 100, prev_close: 100, open: 100, high: 100, low: 100, change: 0, change_pct: 0, volume: 0, quote_time: null, bids: [], asks: [], meta: null },
      daily_history: { rows: [], meta: null },
      institutional: { status: 'unavailable', foreign_net: null, investment_trust_net: null, dealer_net: null, total_net: null, meta: null },
      margin: { status: 'unavailable', margin_balance: null, margin_change: null, short_balance: null, short_change: null, short_margin_ratio: null, meta: null },
      factors: { status: 'unavailable' },
      market_context: { benchmark_name: '加權指數', benchmark_symbol: 'TAIEX', close: null, change: null, change_pct: null },
      industry_context: { industry: null, turnover_share: null, relative_strength_5d: null },
      monitor_summary: { rule_count: 0, rules: [] },
      recent_alerts: [],
      overall_data_quality: 'partial',
      data_quality: { overall_status: 'partial' },
      evidence_summary: { known_fields_count: 0, missing_fields_count: 0, derived_fields_count: 0 },
    }),
    taiwanCurrentData: vi.fn().mockResolvedValue(null),
    taiwanStockResearchContext: vi.fn().mockResolvedValue(null),
    taiwanStockAIResearch: vi.fn(),
    alertsList: vi.fn().mockResolvedValue({ alerts: [], total: 0 }),
    taiwanQuantLiveModels: vi.fn().mockResolvedValue({
      configured_model: { model_key: 'test', top_n: 10 }, latest_operation: null, expected_session: null,
      current_run_valid: false, current_run_audit_status: null, current_run_reason: 'none',
    }),
    taiwanQuantLiveRuns: vi.fn().mockResolvedValue({ runs: [] }),
  },
}))

function renderAt(entries: (string | { pathname: string; state?: unknown })[], initialIndex: number) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={entries} initialIndex={initialIndex}>
        <Routes>
          <Route path="/stocks/:symbol" element={<><TaiwanStockDetail /><LocationState /></>} />
          <Route path="/monitor" element={<div>MONITOR PAGE</div>} />
          <Route path="/taiwan-screener" element={<div>SCREENER PAGE</div>} />
          <Route path="/settings" element={<div>AI SETTINGS</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return queryClient
}

function LocationState() {
  const location = useLocation()
  return <output data-testid="location-state">{JSON.stringify(location.state)}</output>
}

beforeEach(() => {
  window.history.replaceState(null, '')
})

afterEach(() => {
  vi.clearAllMocks()
  window.history.replaceState(null, '')
})

describe('TaiwanStockDetail — back navigation (DAILY_USE_CORE_UX_FIXES P1-2)', () => {
  it('never shows the old hardcoded "返回即時監控" label', async () => {
    renderAt(['/stocks/2330.TWSE'], 0)

    expect(await screen.findByText('返回')).toBeInTheDocument()
    expect(screen.queryByText('返回即時監控')).not.toBeInTheDocument()
  })

  it('D. returns to the known source (Monitor) when entered from there', async () => {
    window.history.replaceState({ idx: 1 }, '')
    renderAt(['/monitor', '/stocks/2330.TWSE'], 1)

    fireEvent.click(await screen.findByText('返回'))

    expect(await screen.findByText('MONITOR PAGE')).toBeInTheDocument()
  })

  it('E. direct URL falls back to 台股選股 instead of leaving the app', async () => {
    // window.history.state 為 null(beforeEach 已重置) 模擬直接輸入網址
    renderAt(['/stocks/2330.TWSE'], 0)

    fireEvent.click(await screen.findByText('返回'))

    expect(await screen.findByText('SCREENER PAGE')).toBeInTheDocument()
  })
})

describe('TaiwanStockDetail — AI Research', () => {
  it('sends only this symbol context after the user clicks AI 分析 and renders the brief', async () => {
    vi.mocked(api.taiwanStockAIResearch).mockResolvedValue({
      status: 'success',
      provider: 'Custom',
      prompt_version: 'taiwan_stock_research_v1',
      generated_at: '2026-09-25T10:00:00+08:00',
      evidence_registry_keys: [],
      report: {
        symbol: '2330.TWSE', code: '2330', name: '台積電', industry: null, instrument_type: 'stock',
        evidence_as_of: '2026-09-24', generated_at: '2026-09-25T10:00:00+08:00', prompt_version: 'taiwan_stock_research_v1',
        overview: 'AI 測試摘要', key_observations: [], risk_factors: [], watch_next: [{ text: '觀察下次已完成交易日資料', evidence_refs: ['price_context.close'] }],
        missing_information: [], disclaimer: '僅供資料解讀',
      },
    })
    renderAt(['/stocks/2330.TWSE'], 0)

    const analyze = await screen.findByRole('button', { name: 'AI 分析' })
    await waitFor(() => expect(analyze).toBeEnabled())
    fireEvent.click(analyze)

    expect(await screen.findByText('AI 測試摘要')).toBeInTheDocument()
    expect(await screen.findByText('觀察下次已完成交易日資料')).toBeInTheDocument()
    expect(screen.getByText('本次使用：Custom')).toBeInTheDocument()
    expect(vi.mocked(api.taiwanStockAIResearch)).toHaveBeenCalledWith(
      '2330.TWSE', undefined, expect.objectContaining({ watchlist: { included: false } }),
    )
    expect(vi.mocked(api.taiwanStockAIResearch).mock.calls[0][2]?.quant).toEqual({ status: 'no_valid_run', selected: false })
    fireEvent.click(screen.getByRole('button', { name: '重新分析' }))
    await waitFor(() => expect(vi.mocked(api.taiwanStockAIResearch)).toHaveBeenCalledTimes(2))
  })

  it('does not call AI merely because the stock detail URL is opened', async () => {
    renderAt(['/stocks/2330.TWSE'], 0)
    await screen.findByRole('button', { name: 'AI 分析' })
    expect(vi.mocked(api.taiwanStockAIResearch)).not.toHaveBeenCalled()
  })

  it('keeps the displayed report and provider label together when reanalysis fails', async () => {
    vi.mocked(api.taiwanStockAIResearch)
      .mockResolvedValueOnce({
        status: 'success', provider: 'Provider A', prompt_version: 'taiwan_stock_research_v1',
        generated_at: '2026-09-25T10:00:00+08:00', evidence_registry_keys: [],
        report: {
          symbol: '2330.TWSE', code: '2330', name: '台積電', industry: null, instrument_type: 'stock',
          evidence_as_of: '2026-09-24', generated_at: '2026-09-25T10:00:00+08:00', prompt_version: 'taiwan_stock_research_v1',
          overview: 'Provider A 摘要', key_observations: [], risk_factors: [], watch_next: [],
          missing_information: [], disclaimer: '僅供資料解讀',
        },
      })
      .mockResolvedValueOnce({
        status: 'unavailable', provider: 'Provider B', error_message: 'Provider B 暫時無法使用。',
        prompt_version: 'taiwan_stock_research_v1', generated_at: '2026-09-25T10:01:00+08:00', evidence_registry_keys: [],
      })
    renderAt(['/stocks/2330.TWSE'], 0)
    const analyze = await screen.findByRole('button', { name: 'AI 分析' })
    await waitFor(() => expect(analyze).toBeEnabled())
    fireEvent.click(analyze)
    expect(await screen.findByText('Provider A 摘要')).toBeInTheDocument()
    expect(screen.getByText('本次使用：Provider A')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '重新分析' }))

    expect(await screen.findByText(/Provider B 暫時無法使用/)).toBeInTheDocument()
    expect(screen.getByText('Provider A 摘要')).toBeInTheDocument()
    expect(screen.getByText('本次使用：Provider A')).toBeInTheDocument()
  })

  it('sends explicit unavailable Quant status when the live Quant query fails', async () => {
    vi.mocked(api.taiwanQuantLiveRuns).mockRejectedValue(new Error('Quant unavailable'))
    vi.mocked(api.taiwanStockAIResearch).mockResolvedValue({
      status: 'unavailable', error_message: 'AI 分析目前無法使用。', provider: 'Custom',
      prompt_version: 'taiwan_stock_research_v1', generated_at: '2026-09-25T10:00:00+08:00', evidence_registry_keys: [],
    })
    renderAt(['/stocks/2330.TWSE'], 0)
    const analyze = await screen.findByRole('button', { name: 'AI 分析' })
    await waitFor(() => expect(analyze).toBeEnabled())
    expect(await screen.findByText('Live Quant 摘要目前無法讀取。')).toBeInTheDocument()
    fireEvent.click(analyze)
    await waitFor(() => expect(vi.mocked(api.taiwanStockAIResearch)).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.taiwanStockAIResearch).mock.calls[0][2]?.quant).toEqual({ status: 'unavailable', selected: false })
  })

  it('keeps stock detail usable and links to AI settings when the provider is unavailable', async () => {
    vi.mocked(api.taiwanStockAIResearch).mockResolvedValue({
      status: 'unavailable', error_message: 'AI 分析目前無法使用，請檢查 AI 設定或稍後重試。',
      provider: 'Custom', prompt_version: 'taiwan_stock_research_v1', generated_at: '2026-09-25T10:00:00+08:00', evidence_registry_keys: [],
    })
    renderAt(['/stocks/2330.TWSE'], 0)
    const analyze = await screen.findByRole('button', { name: 'AI 分析' })
    await waitFor(() => expect(analyze).toBeEnabled())
    fireEvent.click(analyze)
    expect(await screen.findByText(/AI 分析目前無法使用/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'AI 設定' }))
    expect(await screen.findByText('AI SETTINGS')).toBeInTheDocument()
  })

  it('includes the one selected alert and local position in a user-requested interpretation', async () => {
    window.localStorage.setItem('portfolio_transactions', JSON.stringify([{
      id: 'trade-1', symbol: '2330.TWSE', name: '台積電', side: 'buy', shares: 10,
      price: 900, fee: 0, tax: 0, date: '2026-09-01', tradeTime: '10:00', createdAt: '2026-09-01T10:00:00+08:00',
    }]))
    vi.mocked(api.alertsList).mockResolvedValue({ alerts: [{
      ts: 1, alert_id: 'alert-1', source: 'twse:mis', type: 'price_below', symbol: '2330.TWSE', name: '台積電',
      message: '價格跌破 900', price: 899, trigger_value: 899, threshold: 900, severity: 'warning',
    }], total: 1 })
    vi.mocked(api.taiwanStockAIResearch).mockResolvedValue({
      status: 'success', provider: 'Custom', prompt_version: 'taiwan_stock_research_v1', generated_at: '2026-09-25T10:00:00+08:00', evidence_registry_keys: [],
      report: {
        symbol: '2330.TWSE', code: '2330', name: '台積電', industry: null, instrument_type: 'stock', evidence_as_of: '2026-09-24',
        personal_context_as_of: '2026-09-25T10:00:00+08:00',
        generated_at: '2026-09-25T10:00:00+08:00', prompt_version: 'taiwan_stock_research_v1', overview: '提醒測試摘要',
        portfolio_interpretation: '目前資料包含本機持倉成本。', alert_interpretation: '觸發價格提醒。',
        key_observations: [], risk_factors: [], watch_next: [], missing_information: [], disclaimer: '僅供資料解讀',
      },
    })
    renderAt([{ pathname: '/stocks/2330.TWSE', state: { aiResearchRequested: true, alertId: 'alert-1' } }], 0)

    expect(await screen.findByText('提醒測試摘要')).toBeInTheDocument()
    expect(screen.getByText((_, element) => (
      element?.tagName === 'P'
      && element.textContent?.replace(/\s+/g, ' ').includes('市場證據截至 2026-09-24；持倉、自選與提醒資料截至 2026-09-25 10:00:00+08:00') === true
    ))).toBeInTheDocument()
    expect(screen.getByText('我的部位')).toBeInTheDocument()
    expect(screen.getByText('提醒解讀')).toBeInTheDocument()
    expect(vi.mocked(api.taiwanStockAIResearch)).toHaveBeenCalledWith('2330.TWSE', undefined, expect.objectContaining({
      portfolio: expect.objectContaining({ shares: 10, average_cost: 900 }),
      alert: expect.objectContaining({ alert_id: 'alert-1', trigger_value: 899, message: '價格跌破 900' }),
    }))
    const sentContext = vi.mocked(api.taiwanStockAIResearch).mock.calls[0][2]
    expect(sentContext).not.toHaveProperty('quote')
    expect(sentContext?.portfolio).not.toHaveProperty('current_price')
    expect(sentContext?.portfolio).not.toHaveProperty('unrealized_pnl')
    expect(sentContext?.portfolio).not.toHaveProperty('change_pct')
  })

  it('keeps an alert analysis request retryable when the alert lookup fails', async () => {
    vi.mocked(api.alertsList)
      .mockRejectedValueOnce(new Error('temporary alert lookup failure'))
      .mockResolvedValueOnce({ alerts: [{
        ts: 1, alert_id: 'alert-retry', source: 'twse:mis', type: 'price_below', symbol: '2330.TWSE', name: '台積電',
        message: '價格跌破 900', price: 899, trigger_value: 899, threshold: 900, severity: 'warning',
      }], total: 1 })
    vi.mocked(api.taiwanStockAIResearch).mockResolvedValue({
      status: 'unavailable', error_message: 'AI 分析目前無法使用。', provider: 'Custom',
      prompt_version: 'taiwan_stock_research_v1', generated_at: '2026-09-25T10:00:00+08:00', evidence_registry_keys: [],
    })
    renderAt([{ pathname: '/stocks/2330.TWSE', state: { aiResearchRequested: true, alertId: 'alert-retry' } }], 0)

    expect(await screen.findByText(/提醒資料讀取失敗/)).toBeInTheDocument()
    expect(screen.getByTestId('location-state')).toHaveTextContent('aiResearchRequested')
    fireEvent.click(screen.getByRole('button', { name: '重試提醒讀取' }))

    await waitFor(() => expect(vi.mocked(api.taiwanStockAIResearch)).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.taiwanStockAIResearch).mock.calls[0][2]?.alert).toEqual(expect.objectContaining({
      alert_id: 'alert-retry', trigger_value: 899,
    }))
  })

  it('refreshes local holdings after a portfolio trade event before sending AI context', async () => {
    vi.mocked(api.taiwanStockAIResearch).mockResolvedValue({
      status: 'unavailable', error_message: 'AI 分析目前無法使用。', provider: 'Custom',
      prompt_version: 'taiwan_stock_research_v1', generated_at: '2026-09-25T10:00:00+08:00', evidence_registry_keys: [],
    })
    renderAt(['/stocks/2330.TWSE'], 0)
    const analyze = await screen.findByRole('button', { name: 'AI 分析' })
    await waitFor(() => expect(analyze).toBeEnabled())

    window.localStorage.setItem('portfolio_transactions', JSON.stringify([{
      id: 'trade-after-load', symbol: '2330.TWSE', name: '台積電', side: 'buy', shares: 4,
      price: 950, fee: 0, tax: 0, date: '2026-09-25', tradeTime: '10:00', createdAt: '2026-09-25T10:00:00+08:00',
    }]))
    act(() => window.dispatchEvent(new Event('portfolio-transactions-changed')))
    fireEvent.click(analyze)

    await waitFor(() => expect(vi.mocked(api.taiwanStockAIResearch)).toHaveBeenCalledWith(
      '2330.TWSE', undefined, expect.objectContaining({ portfolio: expect.objectContaining({ shares: 4, average_cost: 950 }) }),
    ))
  })

  it('sends verified portfolio returns as decimal fractions and omits realtime percentage points', async () => {
    const detailLoader = vi.mocked(api.taiwanStockDetail).getMockImplementation()
    expect(detailLoader).toBeDefined()
    const detail = await detailLoader!('2330.TWSE')
    vi.mocked(api.taiwanStockDetail).mockResolvedValue({
      ...detail,
      realtime: {
        ...detail.realtime, last_price: 105, change: 5, change_pct: 5, market_status: 'open',
        meta: { source: 'twse:mis', trade_date: '2026-09-25', status: 'available', is_stale: false },
      },
    })
    window.localStorage.setItem('portfolio_transactions', JSON.stringify([{
      id: 'trade-verified', symbol: '2330.TWSE', name: '台積電', side: 'buy', shares: 10,
      price: 100, fee: 0, tax: 0, date: '2026-09-24', tradeTime: '10:00', createdAt: '2026-09-24T10:00:00+08:00',
    }]))
    vi.mocked(api.taiwanStockAIResearch).mockResolvedValue({
      status: 'unavailable', error_message: 'AI 分析目前無法使用。', provider: 'Custom',
      prompt_version: 'taiwan_stock_research_v1', generated_at: '2026-09-25T10:00:00+08:00', evidence_registry_keys: [],
    })
    renderAt(['/stocks/2330.TWSE'], 0)
    const analyze = await screen.findByRole('button', { name: 'AI 分析' })
    await waitFor(() => expect(analyze).toBeEnabled())
    fireEvent.click(analyze)

    await waitFor(() => expect(vi.mocked(api.taiwanStockAIResearch)).toHaveBeenCalledTimes(1))
    const sentContext = vi.mocked(api.taiwanStockAIResearch).mock.calls[0][2]
    expect(sentContext?.quote).not.toHaveProperty('change_pct')
    expect(sentContext?.portfolio?.return_pct).toBeCloseTo(0.05)
    expect(sentContext?.portfolio).not.toHaveProperty('change_pct')
  })

  it('omits a retained quote after the detail refresh fails', async () => {
    const detailLoader = vi.mocked(api.taiwanStockDetail).getMockImplementation()
    expect(detailLoader).toBeDefined()
    const detail = await detailLoader!('2330.TWSE')
    vi.mocked(api.taiwanStockDetail)
      .mockResolvedValueOnce({
        ...detail,
        realtime: {
          ...detail.realtime, last_price: 105, change: 5, change_pct: 5,
          meta: { source: 'twse:mis', trade_date: '2026-09-25', status: 'available', is_stale: false },
        },
      })
      .mockRejectedValueOnce(new Error('detail refresh failed'))
    vi.mocked(api.taiwanStockAIResearch).mockResolvedValue({
      status: 'unavailable', error_message: 'AI 分析目前無法使用。', provider: 'Custom',
      prompt_version: 'taiwan_stock_research_v1', generated_at: '2026-09-25T10:00:00+08:00', evidence_registry_keys: [],
    })
    const queryClient = renderAt(['/stocks/2330.TWSE'], 0)
    const analyze = await screen.findByRole('button', { name: 'AI 分析' })
    await waitFor(() => expect(analyze).toBeEnabled())
    await queryClient.refetchQueries({ queryKey: QK.taiwanStockDetail('2330.TWSE', 180), type: 'active' })
    await waitFor(() => expect(vi.mocked(api.taiwanStockDetail)).toHaveBeenCalledTimes(2))
    fireEvent.click(analyze)

    await waitFor(() => expect(vi.mocked(api.taiwanStockAIResearch)).toHaveBeenCalledTimes(1))
    const sentContext = vi.mocked(api.taiwanStockAIResearch).mock.calls[0][2]
    expect(sentContext).not.toHaveProperty('quote')
    expect(sentContext).not.toHaveProperty('portfolio')
  })

  it('waits for the watchlist mutation and refresh before sending AI context', async () => {
    vi.mocked(api.watchlistList)
      .mockResolvedValueOnce({ symbols: [] })
      .mockResolvedValueOnce({ symbols: [{ symbol: '2330.TWSE', added_at: '2026-09-25T10:00:00+08:00' }] })
    let finishAdd: (() => void) | undefined
    vi.mocked(api.watchlistAdd).mockImplementation(() => new Promise(resolve => {
      finishAdd = () => resolve({ symbols: [] })
    }))
    vi.mocked(api.taiwanStockAIResearch).mockResolvedValue({
      status: 'unavailable', error_message: 'AI 分析目前無法使用。', provider: 'Custom',
      prompt_version: 'taiwan_stock_research_v1', generated_at: '2026-09-25T10:00:00+08:00', evidence_registry_keys: [],
    })
    renderAt(['/stocks/2330.TWSE'], 0)
    const analyze = await screen.findByRole('button', { name: 'AI 分析' })
    await waitFor(() => expect(analyze).toBeEnabled())

    fireEvent.click(screen.getByRole('button', { name: '將 2330.TWSE 加入自選' }))
    await waitFor(() => expect(vi.mocked(api.watchlistAdd)).toHaveBeenCalledTimes(1))
    expect(analyze).toBeDisabled()
    finishAdd?.()

    await screen.findByRole('button', { name: '將 2330.TWSE 移出自選' })
    await waitFor(() => expect(analyze).toBeEnabled())
    fireEvent.click(analyze)
    await waitFor(() => expect(vi.mocked(api.taiwanStockAIResearch)).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.taiwanStockAIResearch).mock.calls[0][2]?.watchlist).toEqual({ included: true })
  })

  it('consumes the AI navigation request so reload does not automatically re-run analysis', async () => {
    vi.mocked(api.taiwanStockAIResearch).mockResolvedValue({
      status: 'unavailable', error_message: 'AI 分析目前無法使用。', provider: 'Custom',
      prompt_version: 'taiwan_stock_research_v1', generated_at: '2026-09-25T10:00:00+08:00', evidence_registry_keys: [],
    })
    renderAt([{ pathname: '/stocks/2330.TWSE', state: { aiResearchRequested: true, returnTo: '/monitor' } }], 0)

    await waitFor(() => expect(vi.mocked(api.taiwanStockAIResearch)).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.getByTestId('location-state')).toHaveTextContent('"returnTo":"/monitor"'))
    expect(screen.getByTestId('location-state')).not.toHaveTextContent('aiResearchRequested')
  })
})

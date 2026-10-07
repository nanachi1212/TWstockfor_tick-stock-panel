import { QK } from '@/lib/queryKeys'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { BeginnerPicks } from '@/pages/BeginnerPicks'
import { beginnerCandidate, beginnerRadar, beginnerSelection } from '@/test/beginnerFixtures'
import { BeginnerDashboardWidget, BeginnerStockView, PickCard } from './BeginnerPicker'

vi.mock('@/lib/api', () => ({
  api: {
    beginnerSelection: vi.fn(),
    beginnerRadar: vi.fn(),
    beginnerSelectionSymbol: vi.fn(),
    taiwanExternalContext: vi.fn(),
    watchlistList: vi.fn(),
    watchlistAdd: vi.fn(),
    syncPlanRules: vi.fn(),
  },
}))

const unavailableExternal = {
  source: 'test', status: 'unavailable' as const, as_of: null, retrieved_at: null,
  freshness: 'unavailable', data: null, error_reason: 'not_queried',
}
const emptyExternalContext = {
  intraday_context: unavailableExternal,
  fx_context: unavailableExternal,
  macro_context: unavailableExternal,
  secondary_cross_checks: unavailableExternal,
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}|{JSON.stringify(location.state)}</output>
}

function renderWith(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const rendered = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="*" element={<>{ui}<LocationProbe /></>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { ...rendered, client }
}

describe('Beginner Stock Picker', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.taiwanExternalContext).mockResolvedValue(emptyExternalContext)
    vi.mocked(api.watchlistList).mockResolvedValue({ symbols: [] })
    vi.mocked(api.watchlistAdd).mockResolvedValue({ symbols: [] } as any)
    vi.mocked(api.syncPlanRules).mockResolvedValue({ created: 0, removed: 0, skipped: [] })
  })

  it('dashboard shows market summary, at most 5 cards, view-all link and the strength disclaimer', async () => {
    vi.mocked(api.beginnerSelection).mockResolvedValue(beginnerSelection(7))
    renderWith(<BeginnerDashboardWidget />)
    expect(await screen.findByText('今天市場怎麼看？')).toBeInTheDocument()
    expect(screen.getByText('適合找機會')).toBeInTheDocument()
    expect(screen.getAllByRole('article')).toHaveLength(5)
    expect(screen.getByRole('link', { name: '查看全部' })).toHaveAttribute('href', '/picks')
    expect(screen.getByText('訊號強度代表目前條件符合程度，不代表上漲機率。')).toBeInTheDocument()
    expect(screen.queryByText(/%\s*機率|勝率|上漲機率\s*\d/)).not.toBeInTheDocument()
  })

  it('card answers why, what to do, and when the reason fails — without raw metrics', () => {
    renderWith(<PickCard candidate={beginnerCandidate('2330.TWSE')} />)
    const card = screen.getByRole('article')
    expect(within(card).getByText('等待回檔')).toBeInTheDocument()
    expect(within(card).getByText('為什麼被選中')).toBeInTheDocument()
    expect(within(card).getByText(/觀察區：94～97/)).toBeInTheDocument()
    expect(within(card).getByText(/失效條件：/)).toBeInTheDocument()
    expect(within(card).getByText('資金：法人資料目前不可用。')).toBeInTheDocument()
    expect(card.textContent).not.toMatch(/ma20|rsi|flow_ratio|\/100/i)
  })

  it('AI button opens the stock page and requests research without re-ranking', () => {
    renderWith(<PickCard candidate={beginnerCandidate('2330.TWSE')} />)
    fireEvent.click(screen.getByRole('button', { name: /AI 深入分析/ }))
    expect(screen.getByTestId('location')).toHaveTextContent('/stocks/2330.TWSE|{"aiResearchRequested":true}')
  })

  it('full page lists radar items and explains why others were not selected', async () => {
    const live = { status: 'waiting' as const, label: '尚未到位', price: 99, quote_time: null, note: null }
    vi.mocked(api.beginnerRadar).mockResolvedValue(beginnerRadar(
      beginnerSelection(12).candidates.map(candidate => ({ candidate, sources: ['pick' as const], live })),
      { data_gaps: ['Dcard 來源目前不可用'] },
    ))
    renderWith(<BeginnerPicks />)
    expect(screen.getByRole('heading', { name: '每日承接雷達' })).toBeInTheDocument()
    expect(await screen.findAllByRole('article')).toHaveLength(12)
    expect(screen.getByRole('heading', { name: '等拉回（12）' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /為什麼沒選/ }))
    expect(screen.getByText('行情停在 2026-09-29，不是最新交易日，資料過舊。')).toBeInTheDocument()
    expect(screen.getByText(/Dcard 來源目前不可用/)).toBeInTheDocument()
  })

  it('radar groups by state, shows live status, plan levels and own-stock source', async () => {
    localStorage.setItem('portfolio_transactions', JSON.stringify([{
      id: 't1', symbol: '2317.TWSE', name: '鴻海', side: 'buy', shares: 1000, price: 100, fee: 0,
      date: '2026-09-01', tradeTime: '10:00', createdAt: '2026-09-01T02:00:00.000Z',
    }]))
    vi.mocked(api.beginnerRadar).mockResolvedValue(beginnerRadar([
      {
        candidate: beginnerCandidate('2330.TWSE', { rank: 1 }), sources: ['pick'],
        live: { status: 'in_zone', label: '已進承接區', price: 96, quote_time: '2026-10-07T10:15:00+08:00', note: null },
      },
      {
        candidate: beginnerCandidate('2317.TWSE', {
          name: '鴻海', rank: null, selection_state: 'skip', reasons: [], trade_plan: null,
          exclusion_reasons: [{ reason_code: 'trend_not_ready', evidence_key: 'trend', direction: 'negative', display_text: '趨勢偏弱，暫時略過。' }],
        }),
        sources: ['holding'],
        live: { status: 'unavailable', label: '無盤中判斷', price: 101, quote_time: null, note: '這檔目前不提供計畫價位。' },
      },
    ]))
    renderWith(<BeginnerPicks />)
    const pullback = await screen.findByRole('region', { name: '等拉回' })
    expect(within(pullback).getByText('已進承接區')).toBeInTheDocument()
    expect(within(pullback).getByText('承接區')).toBeInTheDocument()
    expect(within(pullback).getByText('失效位')).toBeInTheDocument()
    const own = screen.getByRole('region', { name: '你的股票：暫不操作' })
    expect(within(own).getByText('持股')).toBeInTheDocument()
    expect(within(own).getByText('這檔目前不提供計畫價位。')).toBeInTheDocument()
    expect(api.beginnerRadar).toHaveBeenCalledWith(['2317.TWSE'])
    localStorage.removeItem('portfolio_transactions')
  })

  it('empty selection is honest instead of padding the list', async () => {
    vi.mocked(api.beginnerSelection).mockResolvedValue(beginnerSelection(0, {
      status: 'degraded', data_gaps: ['事件風險資料不可用'],
      market: { ...beginnerSelection().market, state: 'unavailable', headline: '資料不足' },
    }))
    renderWith(<BeginnerDashboardWidget />)
    expect(await screen.findByText('今天沒有符合條件的股票。')).toBeInTheDocument()
    expect(screen.getByText(/事件風險資料不可用/)).toBeInTheDocument()
    expect(screen.getAllByText('資料不足').length).toBeGreaterThan(0)
  })

  it('stock view shows all eight beginner sections and reveals raw evidence only when advanced', async () => {
    vi.mocked(api.beginnerSelectionSymbol).mockResolvedValue({
      version: 'beginner-selection-v1', generated_at: '', market: beginnerSelection().market,
      candidate: beginnerCandidate('2330.TWSE'), disclaimer: '',
    })
    const onToggle = vi.fn()
    const { rerender } = renderWith(<BeginnerStockView symbol="2330.TWSE" advanced={false} onToggleAdvanced={onToggle} />)
    expect(await screen.findByText('結論')).toBeInTheDocument()
    for (const label of ['現在怎麼做', '價格位置', '趨勢與均線', '買賣力道', '資金籌碼', '相對強弱', '基本面', '主要風險']) {
      expect(screen.getByRole('region', { name: label })).toBeInTheDocument()
    }
    for (const label of ['結論', '理由', '下一步']) expect(screen.getByText(label)).toBeInTheDocument()
    expect(screen.getByText('支撐區')).toBeInTheDocument()
    expect(screen.getByText('最近收盤價')).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: '價格位置' })).getByText(/資料日 2026-09-30/)).toBeInTheDocument()
    expect(screen.getByText('壓力位')).toBeInTheDocument()
    expect(screen.getByText('目前沒有可靠的即時內外盤資料。')).toBeInTheDocument()
    expect(screen.getByText('個股 20 日')).toBeInTheDocument()
    expect(screen.queryByText('MA5')).not.toBeInTheDocument()
    expect(screen.queryByText(/RSI|MACD|KD|Bollinger|DMI|ADX|OBV|Fibonacci|Elliott/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/screener\.trend_pit/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /展開進階資料/ }))
    expect(onToggle).toHaveBeenCalled()
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter><BeginnerStockView symbol="2330.TWSE" advanced onToggleAdvanced={onToggle} /></MemoryRouter>
      </QueryClientProvider>,
    )
    expect(await screen.findByText(/screener\.trend_pit/)).toBeInTheDocument()
    expect(screen.getByText('MA5')).toBeInTheDocument()
    expect(screen.getByText('市場討論')).toBeInTheDocument()
    expect(screen.getByText('來源目前不可用。')).toBeInTheDocument()
  })

  it('skipped stock explains why it was not selected', async () => {
    vi.mocked(api.beginnerSelectionSymbol).mockResolvedValue({
      version: 'beginner-selection-v1', generated_at: '', market: beginnerSelection().market,
      candidate: beginnerSelection().not_selected[0], disclaimer: '',
    })
    renderWith(<BeginnerStockView symbol="2330.TWSE" advanced={false} onToggleAdvanced={() => {}} />)
    expect(await screen.findByText('為什麼沒被選？')).toBeInTheDocument()
    expect(screen.getAllByText('暫時略過').length).toBeGreaterThan(0)
    expect(screen.queryByText(/觀察區/)).not.toBeInTheDocument()
  })

  it('shows required FRED attribution with macro data on stock detail', async () => {
    vi.mocked(api.beginnerSelectionSymbol).mockResolvedValue({
      version: 'beginner-selection-v1', generated_at: '', market: beginnerSelection().market,
      candidate: beginnerCandidate('2330.TWSE'), disclaimer: '',
    })
    vi.mocked(api.taiwanExternalContext).mockResolvedValue({
      ...emptyExternalContext,
      macro_context: {
        ...unavailableExternal, status: 'available', freshness: 'daily_cache',
        data: { summary: '全球環境：測試。' }, error_reason: null,
      },
    })
    renderWith(<BeginnerStockView symbol="2330.TWSE" advanced={false} onToggleAdvanced={() => {}} />)
    expect(await screen.findByText('全球環境：測試。')).toBeInTheDocument()
    expect(screen.getByText(/This product uses the FRED® API/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'FRED API Terms of Use' })).toHaveAttribute(
      'href', 'https://fred.stlouisfed.org/docs/api/terms_of_use.html',
    )
  })

  it('loads optional FX context independently on stock detail', async () => {
    vi.mocked(api.beginnerSelectionSymbol).mockResolvedValue({
      version: 'beginner-selection-v1', generated_at: '', market: beginnerSelection().market,
      candidate: beginnerCandidate('2330.TWSE'), disclaimer: '',
    })
    vi.mocked(api.taiwanExternalContext).mockResolvedValue({
      ...emptyExternalContext,
      fx_context: {
        ...unavailableExternal, status: 'available', freshness: 'daily',
        data: { summary: '匯率環境：獨立載入。' }, error_reason: null,
      },
    })
    renderWith(<BeginnerStockView symbol="2330.TWSE" advanced={false} onToggleAdvanced={() => {}} />)
    expect(await screen.findByText('匯率環境：獨立載入。')).toBeInTheDocument()
    expect(api.taiwanExternalContext).toHaveBeenCalledWith('2330.TWSE')
  })

  it('shows Fugle inner/outer ratios and timestamp, and hides stale ratios', async () => {
    const liveInnerOuter = {
      status: 'available', outer_pct: 63, inner_pct: 37,
      last_price: 1200, trade_volume: 1000, trade_value: 1_200_000,
      bids: [[1195, 10]], asks: [[1200, 20]],
      explanation: '內盤 37.0%、外盤 63.0%，外盤較強。',
      source: 'fugle_marketdata:websocket:aggregates',
      as_of: '2026-10-05T13:28:42+08:00', freshness: 'realtime',
      disclaimer: '內外盤反映成交主動性，不等於真正買方／賣方人數，不能單獨作為買賣依據。',
    }
    vi.mocked(api.beginnerSelectionSymbol).mockResolvedValue({
      version: 'beginner-selection-v1', generated_at: '', market: beginnerSelection().market,
      candidate: beginnerCandidate('2330.TWSE'), disclaimer: '',
    })
    vi.mocked(api.taiwanExternalContext).mockResolvedValue({
      ...emptyExternalContext,
      intraday_context: {
        ...unavailableExternal, status: 'available', freshness: 'realtime',
        data: { inner_outer: liveInnerOuter }, error_reason: null,
      },
    })
    const { unmount } = renderWith(<BeginnerStockView symbol="2330.TWSE" advanced={false} onToggleAdvanced={() => {}} />)
    expect(await screen.findByText('63.0%')).toBeInTheDocument()
    expect(screen.getByText('37.0%')).toBeInTheDocument()
    expect(screen.getByText('資料時間：13:28:42')).toBeInTheDocument()
    expect(screen.getByText('盤中即時價')).toBeInTheDocument()
    unmount()

    vi.mocked(api.taiwanExternalContext).mockResolvedValue({
      ...emptyExternalContext,
      intraday_context: {
        ...unavailableExternal, status: 'stale', freshness: 'stale',
        data: { inner_outer: {
          ...liveInnerOuter, status: 'data_insufficient', freshness: 'stale',
          explanation: 'Fugle aggregates 資料已過期，暫不顯示內外盤比例。',
        } }, error_reason: 'stale',
      },
    })
    renderWith(<BeginnerStockView symbol="2330.TWSE" advanced={false} onToggleAdvanced={() => {}} />)
    expect(await screen.findByText('即時買賣力道資料已過期')).toBeInTheDocument()
    expect(screen.queryByText('63.0%')).not.toBeInTheDocument()
  })

  it.each([390, 1440])('renders the complete first screen at %ipx', async width => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: width })
    fireEvent(window, new Event('resize'))
    vi.mocked(api.beginnerSelectionSymbol).mockResolvedValue({
      version: 'beginner-selection-v1', generated_at: '', market: beginnerSelection().market,
      candidate: beginnerCandidate('2330.TWSE'), disclaimer: '',
    })
    renderWith(<BeginnerStockView symbol="2330.TWSE" advanced={false} onToggleAdvanced={() => {}} />)
    expect(await screen.findByRole('region', { name: '現在怎麼做' })).toBeInTheDocument()
    expect(screen.getAllByRole('region')).toHaveLength(9)
    expect(screen.getByRole('region', { name: '這檔股票現在怎麼看' }).className).not.toMatch(/min-w-\[/)
  })

  it('radar card for a pick not in watchlist shows button which calls watchlistAdd and syncPlanRules', async () => {
    vi.mocked(api.watchlistList).mockResolvedValue({ symbols: [] })
    vi.mocked(api.watchlistAdd).mockResolvedValue({ symbols: [] } as any)
    vi.mocked(api.syncPlanRules).mockResolvedValue({ created: 2, removed: 0, skipped: [] })

    const candidate = beginnerCandidate('2330.TWSE')
    const live = { status: 'in_zone' as const, label: '已進承接區', price: 96, quote_time: null, note: null }
    const { client } = renderWith(<PickCard candidate={candidate} radar={{ live, sources: ['pick'] }} />)
    const invalidate = vi.spyOn(client, 'invalidateQueries')

    const addButton = screen.getByRole('button', { name: /加入自選並自動監控/ })
    expect(addButton).toBeInTheDocument()
    expect(screen.queryByText('自動監控中')).not.toBeInTheDocument()

    fireEvent.click(addButton)

    await waitFor(() => {
      expect(api.watchlistAdd).toHaveBeenCalledWith('2330.TWSE', '')
      expect(api.syncPlanRules).toHaveBeenCalledTimes(1)
      expect(vi.mocked(api.watchlistAdd).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(api.syncPlanRules).mock.invocationCallOrder[0])
      expect(invalidate).toHaveBeenCalledWith({ queryKey: QK.beginnerRadarRoot })
      expect(invalidate).toHaveBeenCalledWith({ queryKey: QK.taiwanRules })
    })
    expect(await screen.findByText('自動監控中')).toBeInTheDocument()
  })

  it('chip 自動監控中 shows only for watchlist stocks with a plan', async () => {
    const live = { status: 'waiting' as const, label: '尚未到位', price: 100, quote_time: null, note: null }

    // Case 1: Watchlist stock with plan -> shows chip
    const withPlan = beginnerCandidate('2330.TWSE', {
      trade_plan: {
        rule_version: 'v1', entry_semantics: 'pullback_limit', entry_zone_low: 94, entry_zone_high: 97,
        reference_high: 100, breakout_trigger: 100, stop_price: 88, evidence_as_of: '2026-10-07', plan_identity: 'id1',
      },
    })
    const { unmount: unmount1 } = renderWith(
      <PickCard candidate={withPlan} radar={{ live, sources: ['watchlist'] }} />
    )
    expect(screen.getByText('自動監控中')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /加入自選並自動監控/ })).not.toBeInTheDocument()
    unmount1()

    // Case 2: Watchlist stock WITHOUT plan (trade_plan: null) -> does NOT show chip
    const withoutPlan = beginnerCandidate('2317.TWSE', { trade_plan: null })
    const { unmount: unmount2 } = renderWith(
      <PickCard candidate={withoutPlan} radar={{ live, sources: ['watchlist'] }} />
    )
    expect(screen.queryByText('自動監控中')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /加入自選並自動監控/ })).not.toBeInTheDocument()
    unmount2()

    // Case 3: Pick not in watchlist with plan -> does NOT show chip, shows button
    const pickNotWatchlist = beginnerCandidate('2454.TWSE', {
      trade_plan: {
        rule_version: 'v1', entry_semantics: 'pullback_limit', entry_zone_low: 1000, entry_zone_high: 1050,
        reference_high: 1100, breakout_trigger: 1100, stop_price: 950, evidence_as_of: '2026-10-07', plan_identity: 'id2',
      },
    })
    renderWith(
      <PickCard candidate={pickNotWatchlist} radar={{ live, sources: ['pick'] }} />
    )
    expect(screen.queryByText('自動監控中')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /加入自選並自動監控/ })).toBeInTheDocument()
  })
  it('retries only sync after watchlist add succeeded but sync failed', async () => {
    vi.mocked(api.syncPlanRules).mockRejectedValueOnce(new Error('PRICE_BELOW'))
    const live = { status: 'waiting' as const, label: '尚未到位', price: 100, quote_time: null, note: null }
    renderWith(<PickCard candidate={beginnerCandidate('2330.TWSE')} radar={{ live, sources: ['pick'] }} />)
    fireEvent.click(screen.getByRole('button', { name: '加入自選並自動監控' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('已加入自選，但自動監控同步失敗')
    expect(screen.queryByText('自動監控中')).not.toBeInTheDocument()
    expect(document.body.textContent).not.toContain('PRICE_BELOW')
    fireEvent.click(screen.getByRole('button', { name: '重試自動監控' }))
    expect(await screen.findByText('自動監控中')).toBeInTheDocument()
    expect(api.watchlistAdd).toHaveBeenCalledOnce()
    expect(api.syncPlanRules).toHaveBeenCalledTimes(2)
  })

  it('does not sync if watchlist add fails', async () => {
    vi.mocked(api.watchlistAdd).mockRejectedValueOnce(new Error('network error'))
    const live = { status: 'waiting' as const, label: '尚未到位', price: 100, quote_time: null, note: null }
    renderWith(<PickCard candidate={beginnerCandidate('2330.TWSE')} radar={{ live, sources: ['pick'] }} />)
    fireEvent.click(screen.getByRole('button', { name: '加入自選並自動監控' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('加入自選失敗')
    expect(api.syncPlanRules).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '加入自選並自動監控' })).toBeEnabled()
  })

  it('does not claim auto watch is active when sync skips the selected stock', async () => {
    vi.mocked(api.syncPlanRules).mockResolvedValueOnce({
      created: 0, removed: 0, skipped: [{ symbol: '2330.TWSE', reason: 'trade_plan_unavailable' }],
    })
    const live = { status: 'waiting' as const, label: '尚未到位', price: 100, quote_time: null, note: null }
    renderWith(<PickCard candidate={beginnerCandidate('2330.TWSE')} radar={{ live, sources: ['pick'] }} />)
    fireEvent.click(screen.getByRole('button', { name: '加入自選並自動監控' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('目前沒有可用的承接計畫')
    expect(screen.queryByText('自動監控中')).not.toBeInTheDocument()
  })

})

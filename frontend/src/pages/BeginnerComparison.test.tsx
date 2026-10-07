import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api, type BeginnerComparisonResponse } from '@/lib/api'
import { beginnerCandidate, beginnerRadar, beginnerSelection } from '@/test/beginnerFixtures'
import { BeginnerComparison } from './BeginnerComparison'
import { BeginnerPicks } from './BeginnerPicks'
import { BeginnerDashboardWidget } from '@/components/beginner/BeginnerPicker'

vi.mock('@/lib/api', () => ({ api: { beginnerComparison: vi.fn(), beginnerSelection: vi.fn(), beginnerRadar: vi.fn() } }))

function comparison(): BeginnerComparisonResponse {
  const watch = beginnerCandidate('1101.TWSE', { name: '觀察股', rank: 2, selection_state: 'watch', signal_strength: 'medium' })
  const chase = beginnerCandidate('2330.TWSE', { name: '強訊號不追股', rank: 1, selection_state: 'no_chase', signal_strength: 'strong' })
  const absent = beginnerCandidate('9999.TWSE', { name: '缺資料股', rank: null, selection_state: 'skip', evidence_status: 'insufficient', technical_panel: null, close: null, as_of: null })
  return {
    version: 'beginner-comparator-v1', generated_at: '', as_of: '2026-09-30',
    market: { ...beginnerSelection().market, state: 'cautious', guidance: '先觀察，不追價。' },
    candidates: [chase, watch, absent],
    groups: [
      { key: 'observe', label: '優先觀察', symbols: [watch.symbol] },
      { key: 'wait', label: '等待', symbols: [] },
      { key: 'avoid', label: '暫時不要追／略過', symbols: [chase.symbol] },
      { key: 'insufficient', label: '資料不足', symbols: [absent.symbol] },
    ],
    differences: [{ higher_symbol: watch.symbol, lower_symbol: chase.symbol, reasons: ['觀察股已到可觀察的價格條件；強訊號不追股短線漲多，暫時不要追。'] }],
    disclaimer: '沿用今日選股原排序。',
  }
}

function Probe() {
  const location = useLocation()
  const navigate = useNavigate()
  return <><output data-testid="location">{location.pathname}{location.search}</output><button onClick={() => navigate(-1)}>測試返回</button></>
}

function renderRoute(url: string, dashboard = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[url]}>
    <Routes>
      <Route path="/picks" element={<BeginnerPicks />} />
      <Route path="/picks/compare" element={<BeginnerComparison />} />
      <Route path="/" element={dashboard ? <BeginnerDashboardWidget /> : <BeginnerPicks />} />
    </Routes><Probe />
  </MemoryRouter></QueryClientProvider>)
}

describe('beginner comparator', () => {
  beforeEach(() => {
    vi.mocked(api.beginnerComparison).mockReset().mockResolvedValue(comparison())
    vi.mocked(api.beginnerSelection).mockReset().mockResolvedValue(beginnerSelection())
    vi.mocked(api.beginnerRadar).mockReset().mockResolvedValue(beginnerRadar(beginnerSelection().candidates.map(candidate => ({
      candidate, sources: ['pick'], live: { status: 'waiting', label: '尚未到位', price: null, quote_time: null, note: null, source: null, source_status: null, freshness_class: null },
    }))))
  })

  it.each([
    '/picks/compare', '/picks/compare?symbols=2330.TWSE',
    '/picks/compare?symbols=2330.TWSE,2330.TWSE', '/picks/compare?symbols=invalid,1101.TWSE',
    '/picks/compare?symbols=1000.TWSE,1001.TWSE,1002.TWSE,1003.TWSE,1004.TWSE,1005.TWSE',
  ])('fails closed for invalid selection %s without requesting data', url => {
    renderRoute(url)
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(api.beginnerComparison).not.toHaveBeenCalled()
  })

  it('uses action groups ahead of strength, keeps original rank and marks missing data explicitly', async () => {
    renderRoute('/picks/compare?symbols=2330.TWSE,1101.TWSE,9999.TWSE')
    expect(await screen.findByText('這幾檔怎麼選？')).toBeInTheDocument()
    expect(screen.getByText('① 觀察股')).toBeInTheDocument()
    const cards = screen.getAllByRole('article')
    expect(cards[0]).toHaveAccessibleName('觀察股 1101.TWSE 比較')
    expect(within(cards[0]).getByText(/原排名 #2/)).toBeInTheDocument()
    expect(within(cards[1]).getByText(/原排名 #1/)).toBeInTheDocument()
    expect(within(cards[1]).getByText('不宜追價')).toBeInTheDocument()
    expect(within(cards[2]).getByText('資料不足 · 暫不評價')).toBeInTheDocument()
    expect(within(cards[2]).queryByText('中性')).not.toBeInTheDocument()
    expect(screen.getByText('市場偏保守，先觀察與等待，不積極追買。')).toBeInTheDocument()
    expect(screen.getAllByText('即時力道不可用')).toHaveLength(3)
    expect(api.beginnerComparison).toHaveBeenCalledWith(['1101.TWSE', '2330.TWSE', '9999.TWSE'])
  })

  it('shows price summary and distances, keeping exact metrics inside advanced details', async () => {
    const data = comparison()
    const panel = data.candidates[1].technical_panel!
    panel.support.support_distance_low_pct = -3
    panel.support.support_distance_high_pct = 0.1
    vi.mocked(api.beginnerComparison).mockResolvedValue(data)
    renderRoute('/picks/compare?symbols=1101.TWSE,2330.TWSE,9999.TWSE')
    const card = await screen.findByRole('article', { name: '觀察股 1101.TWSE 比較' })
    const prices = within(card).getByRole('region', { name: '價格摘要' })
    expect(within(prices).getByText('最後完整收盤')).toBeInTheDocument()
    expect(within(prices).getByText('支撐區內')).toBeInTheDocument()
    expect(within(prices).getByText('約 1.0% · 近壓力')).toBeInTheDocument()
    const details = card.querySelector('details')!
    expect(details.open).toBe(false)
    for (const label of ['MA5', 'MA20', 'MA60', 'ATR14', '量比', '融資增減（股）', '融券增減（股）']) {
      expect(within(details).getByText(label)).toBeInTheDocument()
    }
    expect(within(details).getByText('營收年增（%）')).toBeInTheDocument()
    expect(within(details).getByText('營收月增（%）')).toBeInTheDocument()
    expect(within(details).getAllByText(/source: pit_adjusted_daily · as of:/)).toHaveLength(3)
    expect(screen.queryByRole('button', { name: /AI/ })).not.toBeInTheDocument()
  })

  it.each([false, true])('supports min2, max5, no double navigation, back and shared query cache (dashboard=%s)', async dashboard => {
    renderRoute(dashboard ? '/' : '/picks', dashboard)
    const checkboxes = await screen.findAllByRole('checkbox')
    const button = screen.getByRole('button', { name: '比較這些股票' })
    expect(button).toBeDisabled()
    fireEvent.click(checkboxes[0])
    expect(button).toBeDisabled()
    fireEvent.click(checkboxes[1])
    expect(button).toBeEnabled()
    checkboxes.slice(2, 5).forEach(input => fireEvent.click(input))
    if (!dashboard) expect(checkboxes[5]).toBeDisabled()
    expect(screen.getByText(/已選 5.*已達上限/)).toBeInTheDocument()
    fireEvent.click(button)
    await screen.findByText('這幾檔怎麼選？')
    expect(api.beginnerComparison).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: '測試返回' }))
    await screen.findByRole('button', { name: '比較這些股票' })
    expect(screen.getAllByRole('checkbox').filter(input => (input as HTMLInputElement).checked)).toHaveLength(5)
    expect(dashboard ? api.beginnerSelection : api.beginnerRadar).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: '比較這些股票' }))
    await screen.findByText('這幾檔怎麼選？')
    expect(api.beginnerComparison).toHaveBeenCalledTimes(1)
  })

  it('preserves the last result during refresh and prevents double submit', async () => {
    renderRoute('/picks/compare?symbols=1101.TWSE,2330.TWSE,9999.TWSE')
    await screen.findByText('這幾檔怎麼選？')
    let finish!: (value: BeginnerComparisonResponse) => void
    vi.mocked(api.beginnerComparison).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const refresh = screen.getByRole('button', { name: '更新比較' })
    act(() => { fireEvent.click(refresh); fireEvent.click(refresh) })
    await waitFor(() => expect(refresh).toBeDisabled())
    fireEvent.click(refresh)
    expect(api.beginnerComparison).toHaveBeenCalledTimes(2)
    expect(screen.getByText('這幾檔怎麼選？')).toBeInTheDocument()
    await act(async () => finish(comparison()))
    await waitFor(() => expect(refresh).toBeEnabled())
  })

  it('renders error and unsupported difference states honestly', async () => {
    vi.mocked(api.beginnerComparison).mockRejectedValueOnce(new Error('unavailable'))
    renderRoute('/picks/compare?symbols=1101.TWSE,2330.TWSE')
    expect(await screen.findByRole('alert')).toHaveTextContent('比較資料暫時無法讀取')
    vi.mocked(api.beginnerComparison).mockResolvedValueOnce({ ...comparison(), differences: [] })
    fireEvent.click(screen.getByRole('button', { name: '更新比較' }))
    expect(await screen.findByText('資料不足或沒有可支持的條件差異，不追加優先理由。')).toBeInTheDocument()
  })

  it.each(['available', 'waiting', 'stale'] as const)('does not periodically reload full comparison for %s Fugle context; refresh stays manual', async state => {
    const data = comparison()
    data.candidates[0].intraday_context = {
      source: 'fugle_marketdata:websocket:aggregates', status: state === 'waiting' ? 'unavailable' : state,
      as_of: '2026-09-30T13:20:00+08:00', retrieved_at: null, freshness: state, data: null,
      error_reason: state === 'available' ? null : state,
    }
    vi.mocked(api.beginnerComparison).mockResolvedValue(data)
    // Keep notification/waitFor timeouts real; only simulate periodic work.
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    const view = renderRoute('/picks/compare?symbols=1101.TWSE,2330.TWSE,9999.TWSE')
    try {
      await screen.findByText('這幾檔怎麼選？')
      await act(async () => { await vi.advanceTimersByTimeAsync(20_000) })
      expect(api.beginnerComparison).toHaveBeenCalledTimes(1)
      fireEvent.click(screen.getByRole('button', { name: '更新比較' }))
      await waitFor(() => expect(api.beginnerComparison).toHaveBeenCalledTimes(2))
      expect(screen.getByText('① 觀察股')).toBeInTheDocument()
    } finally {
      view.unmount()
      vi.useRealTimers()
    }
  })

  it('keeps eligibility failures visible alongside deduplicated technical risks when a panel exists', async () => {
    const data = comparison()
    const failures = ['行情資料過舊，請先更新。', '除權息資料無法驗證。', '無法確認處置與暫停交易風險。']
    const absent = data.candidates[2]
    absent.exclusion_reasons = failures.map((text, i) => ({
      reason_code: ['quote_stale', 'corporate_action_unverified', 'risk_unverified'][i],
      evidence_key: 'eligibility', direction: 'negative', display_text: text,
    }))
    absent.technical_panel = structuredClone(data.candidates[1].technical_panel)
    absent.technical_panel!.key_risks = [
      { code: 'duplicate_failure', text: failures[0], source: 'fixture' },
      { code: 'near_resistance', text: '現價已接近上方壓力，追價空間有限。', source: 'trade_plan' },
    ]
    vi.mocked(api.beginnerComparison).mockResolvedValue(data)
    renderRoute('/picks/compare?symbols=1101.TWSE,2330.TWSE,9999.TWSE')
    const card = await screen.findByRole('article', { name: '缺資料股 9999.TWSE 比較' })
    const risks = within(card).getByRole('region', { name: '主要風險' })
    failures.forEach(text => expect(within(risks).getAllByText(`• ${text}`)).toHaveLength(1))
    expect(within(risks).getByText('• 現價已接近上方壓力，追價空間有限。')).toBeInTheDocument()
    expect(within(card).getByText('資料不足 · 暫不評價')).toBeInTheDocument()
    expect(within(card).getByText(/未列入原排名/)).toBeInTheDocument()
  })
})

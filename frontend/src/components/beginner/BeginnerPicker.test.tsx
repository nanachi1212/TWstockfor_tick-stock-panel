import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { BeginnerPicks } from '@/pages/BeginnerPicks'
import { beginnerCandidate, beginnerSelection } from '@/test/beginnerFixtures'
import { BeginnerDashboardWidget, BeginnerStockView, PickCard } from './BeginnerPicker'

vi.mock('@/lib/api', () => ({
  api: {
    beginnerSelection: vi.fn(),
    beginnerSelectionSymbol: vi.fn(),
  },
}))

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}|{JSON.stringify(location.state)}</output>
}

function renderWith(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="*" element={<>{ui}<LocationProbe /></>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Beginner Stock Picker', () => {
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

  it('full page lists picks and explains why others were not selected', async () => {
    vi.mocked(api.beginnerSelection).mockResolvedValue(beginnerSelection(12))
    renderWith(<BeginnerPicks />)
    expect(screen.getByRole('heading', { name: '今日選股' })).toBeInTheDocument()
    expect(await screen.findAllByRole('article')).toHaveLength(12)
    fireEvent.click(screen.getByRole('button', { name: /為什麼沒選/ }))
    expect(screen.getByText('行情停在 2026-09-29，不是最新交易日，資料過舊。')).toBeInTheDocument()
    expect(screen.getByText(/Dcard 來源目前不可用/)).toBeInTheDocument()
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

  it('stock view is beginner-first and reveals evidence sources only when advanced', async () => {
    vi.mocked(api.beginnerSelectionSymbol).mockResolvedValue({
      version: 'beginner-selection-v1', generated_at: '', market: beginnerSelection().market,
      candidate: beginnerCandidate('2330.TWSE'), disclaimer: '',
    })
    const onToggle = vi.fn()
    const { rerender } = renderWith(<BeginnerStockView symbol="2330.TWSE" advanced={false} onToggleAdvanced={onToggle} />)
    expect(await screen.findByText('結論')).toBeInTheDocument()
    for (const label of ['結論', '理由', '主要風險', '下一步']) expect(screen.getByText(label)).toBeInTheDocument()
    expect(screen.queryByText(/screener\.trend_pit/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /展開進階資料/ }))
    expect(onToggle).toHaveBeenCalled()
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter><BeginnerStockView symbol="2330.TWSE" advanced onToggleAdvanced={onToggle} /></MemoryRouter>
      </QueryClientProvider>,
    )
    expect(await screen.findByText(/screener\.trend_pit/)).toBeInTheDocument()
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
})

import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { api } from '@/lib/api'
import { storage } from '@/lib/storage'
import { PortfolioPanel } from './Portfolio'

vi.mock('@/lib/api', () => ({ api: { taiwanQuotes: vi.fn() } }))
vi.mock('@/components/quant/TodaySelection', () => ({ useTodayQuantSelection: () => ({ signals: [] }) }))

function renderPortfolio() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter><PortfolioPanel /></MemoryRouter></QueryClientProvider>)
}

function fillTrade({ shares, price }: { shares: string; price: string }) {
  fireEvent.change(screen.getByLabelText('股票代碼'), { target: { value: '2330.TWSE' } })
  const nameInput = screen.queryByLabelText('股票名稱（選填）')
  if (nameInput) fireEvent.change(nameInput, { target: { value: '台積電' } })
  fireEvent.change(screen.getByLabelText('股數'), { target: { value: shares } })
  fireEvent.change(screen.getByLabelText('成交價'), { target: { value: price } })
  fireEvent.click(screen.getByRole('button', { name: '保存成交' }))
}

afterEach(() => {
  localStorage.removeItem('portfolio_transactions')
  vi.clearAllMocks()
})

describe('Portfolio UI', () => {
  it('records a buy, displays quote-based portfolio totals, and restores the holding after remount', async () => {
    vi.mocked(api.taiwanQuotes).mockResolvedValue({ quotes: [{
      symbol: '2330.TWSE', name: '台積電', last_price: 110, prev_close: 108, change: 2,
      change_pct: 1.85, source_meta: { is_stale: false },
    } as any], count: 1 })
    const first = renderPortfolio()
    fireEvent.click(screen.getByRole('button', { name: '買入' }))
    fillTrade({ shares: '10', price: '100' })

    expect(await screen.findByText('台積電')).toBeInTheDocument()
    expect((await screen.findAllByText('NT$1,100.00')).length).toBeGreaterThan(0)
    expect(screen.getAllByText('NT$100.00').length).toBeGreaterThan(0)
    first.unmount()

    renderPortfolio()
    expect(await screen.findByText('台積電')).toBeInTheDocument()
    expect(screen.getByText('10')).toBeInTheDocument()
  })

  it('rejects an oversell with a clear message and keeps the holding unchanged', async () => {
    storage.portfolioTransactions.set([{
      id: 'seed', symbol: '2330.TWSE', name: '台積電', side: 'buy', shares: 5,
      price: 100, fee: 0, date: '2026-09-24', createdAt: '2026-09-24T00:00:00.000Z',
    }])
    vi.mocked(api.taiwanQuotes).mockResolvedValue({ quotes: [], count: 0 })
    renderPortfolio()
    fireEvent.click(await screen.findByRole('button', { name: '賣出 2330.TWSE' }))
    fillTrade({ shares: '6', price: '110' })

    expect(await screen.findByRole('alert')).toHaveTextContent('最多可賣出 5 股')
    expect(screen.getByText('5')).toBeInTheDocument()
  })

  it('shows an explicit unavailable quote state and an empty state without inventing values', async () => {
    vi.mocked(api.taiwanQuotes).mockResolvedValue({ quotes: [], count: 0 })
    const empty = renderPortfolio()
    expect(screen.getByText(/目前沒有持股/)).toBeInTheDocument()
    empty.unmount()

    storage.portfolioTransactions.set([{
      id: 'seed', symbol: '2330.TWSE', name: '台積電', side: 'buy', shares: 5,
      price: 100, fee: 0, date: '2026-09-24', createdAt: '2026-09-24T00:00:00.000Z',
    }])
    renderPortfolio()
    expect(await screen.findAllByText('目前無法取得報價')).toHaveLength(1)
    expect(screen.getAllByText('報價不完整').length).toBeGreaterThan(0)
    await waitFor(() => expect(screen.queryByText('NT$0.00')).not.toBeInTheDocument())
  })
})

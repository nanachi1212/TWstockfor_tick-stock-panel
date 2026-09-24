import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { api } from '@/lib/api'
import { storage } from '@/lib/storage'
import { PortfolioPanel } from './Portfolio'

vi.mock('@/lib/api', () => ({ api: { taiwanQuotes: vi.fn(), taiwanTransactionTax: vi.fn(), taiwanPortfolioInstrument: vi.fn() } }))
vi.mock('@/components/quant/TodaySelection', () => ({ useTodayQuantSelection: () => ({ signals: [] }) }))

function renderPortfolio(props: Parameters<typeof PortfolioPanel>[0] = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return { ...render(<QueryClientProvider client={client}><MemoryRouter><PortfolioPanel {...props} /></MemoryRouter></QueryClientProvider>), client }
}

async function fillTrade({ shares, price }: { shares: string; price: string }) {
  fireEvent.change(screen.getByLabelText('股票代碼'), { target: { value: '2330.TWSE' } })
  const nameInput = screen.queryByLabelText('股票名稱（選填）')
  if (nameInput) fireEvent.change(nameInput, { target: { value: '台積電' } })
  fireEvent.change(screen.getByLabelText('股數'), { target: { value: shares } })
  fireEvent.change(screen.getByLabelText('成交價'), { target: { value: price } })
  fireEvent.change(screen.getByLabelText('日期'), { target: { value: '2026-09-24' } })
  fireEvent.change(screen.getByLabelText('成交時間（台北）'), { target: { value: '10:00' } })
  await waitFor(() => expect(screen.getByRole('button', { name: '保存成交' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: '保存成交' }))
}

beforeEach(() => {
  Object.defineProperty(navigator, 'locks', {
    configurable: true,
    value: { request: async (_name: string, callback: (lock: Lock) => unknown) => callback({} as Lock) },
  })
  vi.mocked(api.taiwanPortfolioInstrument).mockResolvedValue({
    symbol: '2330.TWSE', instrument_type: 'stock', tax_class: 'ordinary_stock',
    trading_day_status: 'verified', is_supported: true,
  } as any)
})

afterEach(() => {
  localStorage.removeItem('portfolio_transactions')
  Reflect.deleteProperty(navigator, 'locks')
  vi.clearAllMocks()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('Portfolio UI', () => {
  it('records a buy, displays quote-based portfolio totals, and restores the holding after remount', async () => {
    vi.mocked(api.taiwanQuotes).mockResolvedValue({ quotes: [{
      symbol: '2330.TWSE', name: '台積電', last_price: 110, prev_close: 108, change: 2,
      change_pct: 1.85, source_meta: { is_stale: false },
    } as any], count: 1 })
    const first = renderPortfolio()
    fireEvent.click(screen.getByRole('button', { name: '買入' }))
    await fillTrade({ shares: '10', price: '100' })

    expect(await screen.findByText('台積電')).toBeInTheDocument()
    expect((await screen.findAllByText('NT$1,100.00')).length).toBeGreaterThan(0)
    expect(screen.getAllByText('NT$100.00').length).toBeGreaterThan(0)
    first.unmount()

    renderPortfolio()
    expect(await screen.findByText('台積電')).toBeInTheDocument()
    expect(screen.getByText('10')).toBeInTheDocument()
  })

  it('prefills a reminder from the quote shown on the stock detail page', async () => {
    storage.portfolioTransactions.set([{
      id: 'seed', symbol: '2330.TWSE', name: '台積電', side: 'buy', shares: 5,
      price: 100, fee: 0, date: '2026-09-24', createdAt: '2026-09-24T00:00:00.000Z',
    }])
    renderPortfolio({ symbol: '2330.TWSE', name: '台積電', quote: 110 })

    fireEvent.click(await screen.findByRole('button', { name: '設定 2330.TWSE 提醒' }))

    expect(await screen.findByDisplayValue('台積電 提醒')).toBeInTheDocument()
    expect(screen.getByDisplayValue('112.2')).toBeInTheDocument()
    expect(api.taiwanQuotes).not.toHaveBeenCalled()
  })

  it('saves through the IndexedDB lock when Web Locks are unavailable', async () => {
    Reflect.deleteProperty(navigator, 'locks')
    const database: any = {
      createObjectStore: vi.fn(),
      close: vi.fn(),
      transaction: vi.fn(() => {
        const transaction: any = {
          objectStore: () => store,
          abort: () => queueMicrotask(() => transaction.onabort?.()),
        }
        const store: any = {
          get: () => {
            const request: any = {}
            queueMicrotask(() => request.onsuccess?.())
            return request
          },
          put: () => queueMicrotask(() => transaction.oncomplete?.()),
        }
        return transaction
      }),
    }
    vi.stubGlobal('indexedDB', {
      open: () => {
        const request: any = { result: database }
        queueMicrotask(() => {
          request.onupgradeneeded?.()
          request.onsuccess?.()
        })
        return request
      },
    })
    vi.mocked(api.taiwanQuotes).mockResolvedValue({ quotes: [], count: 0 })
    renderPortfolio()
    fireEvent.click(screen.getByRole('button', { name: '買入' }))
    await fillTrade({ shares: '1', price: '100' })

    expect(await screen.findByText('台積電')).toBeInTheDocument()
    expect(storage.portfolioTransactions.get([])).toHaveLength(1)
  })

  it('rejects an oversell with a clear message and keeps the holding unchanged', async () => {
    vi.mocked(api.taiwanTransactionTax).mockResolvedValue({ symbol: '2330.TWSE', tax_class: 'ordinary_stock', tax_rate: 0.003, tax_amount: 19.8 })
    storage.portfolioTransactions.set([{
      id: 'seed', symbol: '2330.TWSE', name: '台積電', side: 'buy', shares: 5,
      price: 100, fee: 0, date: '2026-09-24', createdAt: '2026-09-24T00:00:00.000Z',
    }])
    vi.mocked(api.taiwanQuotes).mockResolvedValue({ quotes: [], count: 0 })
    renderPortfolio()
    fireEvent.click(await screen.findByRole('button', { name: '賣出 2330.TWSE' }))
    fireEvent.change(screen.getByLabelText('股票代碼'), { target: { value: '2330.TWSE' } })
    fireEvent.change(screen.getByLabelText('股數'), { target: { value: '6' } })
    fireEvent.change(screen.getByLabelText('成交價'), { target: { value: '110' } })
    expect(await screen.findByText(/預估證交稅/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '保存成交' }))

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

  it('marks daily change unavailable when a quote has no daily change value', async () => {
    storage.portfolioTransactions.set([{
      id: 'seed', symbol: '2330.TWSE', name: '台積電', side: 'buy', shares: 5,
      price: 100, fee: 0, date: '2026-09-22', createdAt: '2026-09-22T00:00:00.000Z',
    }])
    vi.mocked(api.taiwanQuotes).mockResolvedValue({ quotes: [{
      symbol: '2330.TWSE', name: '台積電', last_price: 110, prev_close: null, change: null,
      change_pct: null, source_meta: { is_stale: false },
    } as any], count: 1 })
    renderPortfolio()

    expect(await screen.findByText('報價不完整')).toBeInTheDocument()
    expect(screen.queryByText('NT$0.00')).not.toBeInTheDocument()
  })

  it('fails closed on a malformed persisted ledger without offering writes', () => {
    localStorage.setItem('portfolio_transactions', JSON.stringify([{
      id: 'bad', symbol: '2330.TWSE', side: 'sell', shares: 100, price: 100,
    }]))
    vi.mocked(api.taiwanQuotes).mockResolvedValue({ quotes: [], count: 0 })
    renderPortfolio()

    expect(screen.getByRole('alert')).toHaveTextContent('成交紀錄格式錯誤')
    expect(screen.queryByRole('button', { name: '買入' })).not.toBeInTheDocument()
  })

  it('keeps realized profit visible after closing the position', () => {
    storage.portfolioTransactions.set([
      { id: 'buy', symbol: '2330.TWSE', name: '台積電', side: 'buy', shares: 10, price: 100, fee: 0, date: '2026-09-22', createdAt: '2026-09-22T00:00:00.000Z' },
      { id: 'sell', symbol: '2330.TWSE', name: '台積電', side: 'sell', shares: 10, price: 110, fee: 0, tax: 0, date: '2026-09-23', createdAt: '2026-09-23T00:00:00.000Z' },
    ])
    renderPortfolio()

    expect(screen.getByText('目前沒有持股，從觀察清單或個股頁記錄第一筆買入。')).toBeInTheDocument()
    expect(screen.getByText('已實現損益（平均成本法）：NT$100.00')).toBeInTheDocument()
  })

  it('migrates legacy sell tax through market rules and persists the same P/L after reload', async () => {
    storage.portfolioTransactions.set([
      { id: 'buy', symbol: '2330.TWSE', name: '台積電', side: 'buy', shares: 10, price: 100, fee: 0, date: '2026-09-22', createdAt: '2026-09-22T00:00:00.000Z' },
      { id: 'legacy-sell', symbol: '2330.TWSE', name: '台積電', side: 'sell', shares: 10, price: 110, fee: 0, date: '2026-09-23', createdAt: '2026-09-23T00:00:00.000Z' },
    ])
    vi.mocked(api.taiwanTransactionTax).mockResolvedValue({
      symbol: '2330.TWSE', tax_class: 'ordinary_stock', tax_rate: 0.003, tax_amount: 3.3,
    })
    const first = renderPortfolio()
    expect(await screen.findByText('已實現損益（平均成本法）：NT$96.70')).toBeInTheDocument()
    await waitFor(() => expect(storage.portfolioTransactions.get([])).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: 'legacy-sell', tax: 3.3 }),
    ])))
    first.unmount()

    vi.mocked(api.taiwanTransactionTax).mockClear()
    renderPortfolio()
    expect(screen.getByText('已實現損益（平均成本法）：NT$96.70')).toBeInTheDocument()
    expect(api.taiwanTransactionTax).not.toHaveBeenCalled()
  })

  it('shows legacy realized P/L as incomplete when market rules cannot resolve tax', async () => {
    storage.portfolioTransactions.set([
      { id: 'buy', symbol: '2330.TWSE', name: '台積電', side: 'buy', shares: 10, price: 100, fee: 0, date: '2026-09-22', createdAt: '2026-09-22T00:00:00.000Z' },
      { id: 'legacy-sell', symbol: '2330.TWSE', name: '台積電', side: 'sell', shares: 10, price: 110, fee: 0, date: '2026-09-23', createdAt: '2026-09-23T00:00:00.000Z' },
    ])
    vi.mocked(api.taiwanTransactionTax).mockRejectedValue(new Error('rule unavailable'))
    renderPortfolio()

    expect(screen.getByText('已實現損益（平均成本法）：不完整，部分舊賣出缺少可驗證的證交稅')).toBeInTheDocument()
    await waitFor(() => expect(api.taiwanTransactionTax).toHaveBeenCalled())
    const savedTransactions = storage.portfolioTransactions.get([]) as Array<Record<string, unknown>>
    expect(savedTransactions.find(transaction => transaction.id === 'legacy-sell')).not.toHaveProperty('tax')
  })

  it('marks delayed quotes and the resulting portfolio valuation as non-realtime', async () => {
    storage.portfolioTransactions.set([{
      id: 'seed', symbol: '2330.TWSE', name: '台積電', side: 'buy', shares: 5,
      price: 100, fee: 0, date: '2026-09-22', createdAt: '2026-09-22T00:00:00.000Z',
    }])
    vi.mocked(api.taiwanQuotes).mockResolvedValue({ quotes: [{
      symbol: '2330.TWSE', name: '台積電', last_price: 110, prev_close: 108, change: 2,
      change_pct: 1.85, source_meta: { is_stale: false, freshness_class: 'delayed_15m', source: 'yahoo:chart' },
    } as any], count: 1 })
    renderPortfolio()

    expect(await screen.findByText('延遲 15m')).toBeInTheDocument()
    expect(screen.getByText('總市值（含非即時報價）')).toBeInTheDocument()
  })

  it('marks retained quotes stale and hides aggregate valuations after a refresh fails', async () => {
    storage.portfolioTransactions.set([{
      id: 'seed', symbol: '2330.TWSE', name: '台積電', side: 'buy', shares: 5,
      price: 100, fee: 0, date: '2026-09-22', createdAt: '2026-09-22T00:00:00.000Z',
    }])
    vi.mocked(api.taiwanQuotes)
      .mockResolvedValueOnce({ quotes: [{
        symbol: '2330.TWSE', name: '台積電', last_price: 110, prev_close: 108, change: 2,
        change_pct: 1.85, source_meta: { is_stale: false, freshness_class: 'best_effort_near_realtime' },
      } as any], count: 1 })
      .mockRejectedValueOnce(new Error('offline'))
    const { client } = renderPortfolio()
    expect(await screen.findByText('總市值')).toBeInTheDocument()

    await act(async () => {
      await client.invalidateQueries({ queryKey: ['portfolio-quotes', '2330.TWSE'] })
    })

    expect(await screen.findByText(/報價載入失敗/)).toBeInTheDocument()
    expect(screen.getByText('總市值（含非即時報價）')).toBeInTheDocument()
    expect(screen.getAllByText('行情更新失敗')).toHaveLength(4)
    expect(screen.getByText('資料過期')).toBeInTheDocument()
    expect(screen.getByText('NT$550.00')).toBeInTheDocument()
  })

  it('marks retained detail-page quotes stale after a detail refresh fails', async () => {
    storage.portfolioTransactions.set([{
      id: 'seed', symbol: '2330.TWSE', name: '台積電', side: 'buy', shares: 5,
      price: 100, fee: 0, date: '2026-09-22', createdAt: '2026-09-22T00:00:00.000Z',
    }])
    renderPortfolio({
      symbol: '2330.TWSE', name: '台積電', quote: 110, quoteMeta: {
        is_stale: false, is_realtime: true, freshness_class: 'best_effort_near_realtime',
      }, quoteUnavailable: true,
    })

    expect(await screen.findByText('資料過期')).toBeInTheDocument()
    expect(screen.getByText('NT$550.00')).toBeInTheDocument()
  })

  it('preserves delayed quote freshness on the detail-page portfolio', async () => {
    storage.portfolioTransactions.set([{
      id: 'seed', symbol: '2330.TWSE', name: '台積電', side: 'buy', shares: 5,
      price: 100, fee: 0, date: '2026-09-22', createdAt: '2026-09-22T00:00:00.000Z',
    }])
    renderPortfolio({
      symbol: '2330.TWSE', name: '台積電', quote: 110, quoteMeta: {
        is_stale: false, is_realtime: false, source_type: 'third_party_aggregator',
        freshness_class: 'delayed_15m',
      },
    })

    expect(await screen.findByText('延遲 15m')).toBeInTheDocument()
  })

  it('keeps the trade dialog open and reports a persistence failure', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota exceeded') })
    vi.mocked(api.taiwanQuotes).mockResolvedValue({ quotes: [], count: 0 })
    renderPortfolio()
    fireEvent.click(screen.getByRole('button', { name: '買入' }))
    await fillTrade({ shares: '1', price: '100' })

    expect(await screen.findByRole('alert')).toHaveTextContent('quota exceeded')
    expect(screen.getByRole('form', { name: 'Portfolio 成交紀錄' })).toBeInTheDocument()
  })
})

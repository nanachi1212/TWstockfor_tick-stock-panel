import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { BuyPointStrategies } from './BuyPointStrategies'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: {
    buyPointStrategies: vi.fn(),
    buyPointSignals: vi.fn(),
    buyPointSummary: vi.fn(),
    buyPointStats: vi.fn(),
    buyPointAssign: vi.fn(),
    buyPointClone: vi.fn(),
    buyPointUpdate: vi.fn(),
    buyPointCreate: vi.fn(),
    buyPointSnapshot: vi.fn(),
    watchlistList: vi.fn(),
  },
}))

vi.mock('@/components/Toast', () => ({ toast: vi.fn() }))

const baseStrategy = {
  description: '', category: 'test', enabled: true, preset: false,
  conditions: {}, risk_filters: {}, alert_channels: ['app'],
  created_at: '2026-09-29T00:00:00Z', updated_at: '2026-09-29T00:00:00Z',
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.buyPointStrategies).mockResolvedValue({
    strategies: [
      { ...baseStrategy, id: 'selected', name: '選定策略', assigned_symbols: ['2330.TWSE'] },
      { ...baseStrategy, id: 'other', name: '其他策略', assigned_symbols: ['2317.TWSE'] },
    ],
  } as never)
  vi.mocked(api.buyPointSignals).mockResolvedValue({ signals: [], as_of: '2026-09-29' } as never)
  vi.mocked(api.buyPointSummary).mockResolvedValue({ counts: {}, total: 0 } as never)
  vi.mocked(api.buyPointStats).mockResolvedValue({ stats: [], disclaimer: '' } as never)
  vi.mocked(api.watchlistList).mockResolvedValue({
    symbols: [
      { symbol: '2330.TWSE', name: '台積電' },
      { symbol: '2317.TWSE', name: '鴻海' },
    ],
  } as never)
  vi.mocked(api.buyPointAssign).mockResolvedValue({ symbol: '2317.TWSE', strategy_ids: ['other', 'selected'] } as never)
})

describe('BuyPointStrategies watchlist assignment', () => {
  it('updates each symbol with the backend symbol-first contract and preserves other strategies', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <BuyPointStrategies />
      </QueryClientProvider>,
    )

    const selectedCard = (await screen.findByText('選定策略')).closest('article')
    expect(selectedCard).not.toBeNull()
    fireEvent.click(within(selectedCard!).getByRole('button', { name: '套用自選' }))
    fireEvent.click(screen.getByLabelText(/2317\.TWSE/))
    fireEvent.click(screen.getByRole('button', { name: '儲存套用' }))

    await waitFor(() => {
      expect(api.buyPointAssign).toHaveBeenCalledTimes(1)
      expect(api.buyPointAssign).toHaveBeenCalledWith('2317.TWSE', ['other', 'selected'])
    })
  })
})

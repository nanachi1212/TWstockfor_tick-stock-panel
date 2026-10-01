import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api, type TaiwanEventsResponse } from '@/lib/api'
import { TaiwanEventCenter } from './TaiwanEventCenter'

vi.mock('@/lib/api', () => ({ api: {
  taiwanEvents: vi.fn(), watchlistList: vi.fn().mockResolvedValue({ symbols: [] }),
  taiwanEventCandidates: vi.fn().mockResolvedValue({ candidates: [] }), checkTaiwanEventAlerts: vi.fn(),
} }))

function renderPage() {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><TaiwanEventCenter /></MemoryRouter>
  </QueryClientProvider>)
}

const response: TaiwanEventsResponse = { events: [{
  id: 'test', symbol: '2330.TWSE', code: '2330', name: '測試公司', exchange: 'TWSE',
  event_date: '2026-10-01', event_type: 'insider_transfer_declaration', event_type_label: '內部人持股轉讓申報',
  severity: 'info', title: '申報事件', summary: '事前申報資料', source: 'mops:transfer:TWSE',
  retrieved_at: '2026-10-01T08:00:00+08:00', status: 'data_insufficient', freshness: 'fresh',
}], total: 1, as_of_date: '2026-10-01', status: 'partial' }

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.taiwanEvents).mockResolvedValue(response)
})

describe('Event Center MOPS integration', () => {
  it('exposes all three types in the existing page and filters on the server before limit', async () => {
    renderPage()
    expect(await screen.findByText('申報事件')).toBeInTheDocument()
    expect(screen.getByRole('option', { name: '重大訊息' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: '法人說明會' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: '內部人持股轉讓申報' })).toBeInTheDocument()
    expect(screen.getByTestId('mops-evidence')).toHaveTextContent('不代表實際成交或已賣出')
    expect(screen.getByRole('link', { name: '前往個股分析' })).toHaveAttribute('href', '/stocks/2330.TWSE')
    fireEvent.change(screen.getByRole('combobox', { name: '事件類型' }), { target: { value: 'investor_conference' } })
    await waitFor(() => expect(api.taiwanEvents).toHaveBeenLastCalledWith(expect.objectContaining({ event_types: ['investor_conference'] })))
  })

  it('shows upstream outages alongside an empty list, without claiming no events exist', async () => {
    vi.mocked(api.taiwanEvents).mockResolvedValue({ ...response, events: [], total: 0, status: 'unavailable' })
    renderPage()
    expect(await screen.findByText(/官方事件來源連線中斷/)).toBeInTheDocument()
  })

  it('shows request failure and retains a retry action', async () => {
    vi.mocked(api.taiwanEvents).mockRejectedValue(new Error('offline'))
    renderPage()
    expect(await screen.findByText(/事件載入發生異常/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重新整理' })).toBeEnabled()
  })

  it('explicit refresh reaches the source refresh API', async () => {
    renderPage()
    await screen.findByText('申報事件')
    fireEvent.click(screen.getByRole('button', { name: '重新整理' }))
    await waitFor(() => expect(api.taiwanEvents).toHaveBeenCalledWith(expect.objectContaining({ refresh: true })))
  })
})

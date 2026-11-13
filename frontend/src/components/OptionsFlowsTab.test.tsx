import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { OptionsFlowsSnapshot } from '@/lib/marketResearch'
import { OptionsFlowsTab } from './OptionsFlowsTab'

vi.mock('@/lib/api', () => ({ api: { taiwanOptionsFlows: vi.fn() } }))

const snapshot: OptionsFlowsSnapshot = {
  date: '2026-11-12', status: 'available', source: 'taifex', message: null, previous_date: '2026-11-11',
  default_contract: '202611',
  contracts: [
    { contract: '202611', call_peak: 47500, put_peak: 40000, total_oi: 3000, strikes: [
      { strike: 30000, call: 1, put: 2, call_change: null, put_change: null },
      { strike: 40000, call: 100, put: 1500, call_change: 10, put_change: -20 },
      { strike: 47500, call: 1400, put: null, call_change: null, put_change: null },
    ] },
    { contract: '202611W4', call_peak: 41000, put_peak: 39000, total_oi: 50, strikes: [
      { strike: 41000, call: 50, put: 0, call_change: null, put_change: null },
    ] },
  ],
  foreign: { futures: { date: '2026-11-12', long_oi: 10000, short_oi: 40000, net_oi: -30000, net_oi_change: 1200 },
    futures_status: 'available', futures_message: null, options: null, options_status: 'rate_limited', options_message: '選擇權資料查詢已達頻率上限' },
}

function renderTab(data: OptionsFlowsSnapshot) {
  vi.mocked(api.taiwanOptionsFlows).mockResolvedValue(data)
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><OptionsFlowsTab /></QueryClientProvider>)
}

describe('OptionsFlowsTab', () => {
  it('shows peaks, hides negligible strikes, keeps missing values as dashes and switches contracts', async () => {
    renderTab(snapshot)
    expect(await screen.findByText('-30,000 口')).toBeInTheDocument()
    expect(screen.getByText('選擇權資料查詢已達頻率上限')).toBeInTheDocument()
    const table = screen.getByRole('table')
    expect(within(table).queryByText('30,000')).not.toBeInTheDocument() // under 5% of the peak
    const row = within(table).getByText('47,500').closest('tr')!
    expect(row).toHaveTextContent('1,400')
    expect(row).toHaveTextContent('—')
    fireEvent.change(screen.getByLabelText('選擇權到期'), { target: { value: '202611W4' } })
    expect(within(screen.getByRole('table')).getByText('41,000')).toBeInTheDocument()
  })

  it('explains when no day has been stored yet', async () => {
    renderTab({ ...snapshot, status: 'unavailable', contracts: [], default_contract: null, foreign: null, message: '尚無這一天的選擇權未平倉資料' })
    expect(await screen.findByText('尚無這一天的選擇權未平倉資料')).toBeInTheDocument()
  })
})

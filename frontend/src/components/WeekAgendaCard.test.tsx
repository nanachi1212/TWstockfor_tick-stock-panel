import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api, type WeekAgendaResponse } from '@/lib/api'
import { WeekAgendaCard } from './WeekAgendaCard'

vi.mock('@/lib/api', () => ({ api: { taiwanWeekAgenda: vi.fn() } }))

const agenda: WeekAgendaResponse = {
  start: '2026-11-09', end: '2026-11-15', fetched_at: '2026-11-13T09:00:00+08:00', sources: [],
  errors: ['財報行事曆（Nasdaq）暫時無法連線，顯示上次資料'],
  macro: [
    { time: '2026-11-10T21:30:00+08:00', date: '2026-11-10', country: 'USD', country_name: '美國', impact: 'High', holiday: false,
      title: '核心 CPI（月增）', title_en: 'Core CPI m/m', forecast: '0.2%', previous: '0.3%' },
    { time: '2026-11-11T13:00:00+08:00', date: '2026-11-11', country: 'USD', country_name: '美國', impact: 'Holiday', holiday: true,
      title: '金融市場休假', title_en: 'Bank Holiday', forecast: '', previous: '' },
  ],
  earnings: [{ date: '2026-11-12', symbol: 'CSCO', name: 'Cisco', zh: '思科', tech: true, market_cap: 2e11, timing: '盤後', eps_forecast: '$1.15' }],
}

describe('WeekAgendaCard', () => {
  it('groups by day and shows forecasts, holidays, earnings timing and source errors', async () => {
    vi.mocked(api.taiwanWeekAgenda).mockResolvedValue(agenda)
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><WeekAgendaCard /></QueryClientProvider>)
    expect(await screen.findByText('核心 CPI（月增）')).toBeInTheDocument()
    expect(screen.getByText('預估 0.2%／前值 0.3%')).toBeInTheDocument()
    expect(screen.getByText('金融市場休假').closest('li')).toHaveTextContent('全日')
    expect(screen.getByText('思科').closest('li')).toHaveTextContent('盤後')
    expect(screen.getByText('財報行事曆（Nasdaq）暫時無法連線，顯示上次資料')).toBeInTheDocument()
  })
})

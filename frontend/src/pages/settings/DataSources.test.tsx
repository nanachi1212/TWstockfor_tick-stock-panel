import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { SettingsDataSourcesPanel } from './DataSources'

vi.mock('@/lib/useSharedQueries', () => ({
  usePreferences: () => ({ data: { daily_data_provider: 'taiwan', adj_factor_provider: 'same_as_daily', minute_data_provider: 'taiwan', realtime_data_provider: 'taiwan' } }),
}))
vi.mock('@/lib/api', () => ({ api: {
  dataSources: vi.fn().mockResolvedValue({
    builtin: [{ name: 'taiwan', display_name: '台灣官方資料源 (TWSE/TPEx)', datasets: ['daily', 'realtime', 'financial'] }],
    custom: [{ name: 'custom_feed', display_name: '自訂分鐘來源', datasets: ['minute'] }],
    errors: [],
  }),
} }))
vi.mock('./TaiwanHistoryDataCard', () => ({ TaiwanHistoryDataCard: () => null }))
vi.mock('./FinMindSettingsCard', () => ({ FinMindSettingsCard: () => null }))
vi.mock('./ExternalKeysCard', () => ({ ExternalKeysCard: () => null }))

it('only offers declared providers for minute and adjustment data while retaining custom routing', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><SettingsDataSourcesPanel /></QueryClientProvider>)
  await screen.findByRole('button', { name: '自訂分鐘來源' })
  const minute = screen.getByText('分鐘K').closest('.rounded-lg')!
  expect(within(minute as HTMLElement).getByRole('button', { name: '自訂分鐘來源' })).toBeInTheDocument()
  expect(within(minute as HTMLElement).queryByRole('button', { name: /台灣官方/ })).not.toBeInTheDocument()
  const adjustment = screen.getByText('除權因子').closest('.rounded-lg')!
  expect(within(adjustment as HTMLElement).getByText('尚未設定此資料集來源')).toBeInTheDocument()
  expect(within(adjustment as HTMLElement).queryByRole('button', { name: /台灣官方/ })).not.toBeInTheDocument()
  expect(screen.getByText('新增資料來源')).toBeInTheDocument()
})

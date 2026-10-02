import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { api } from '@/lib/api'
import { Research } from './Research'

vi.mock('@/lib/api', () => ({ api: {
  taiwanAIResearchHistory: vi.fn(),
  taiwanAIResearchHistoryDetail: vi.fn(),
  taiwanAIResearchHistoryCompare: vi.fn(),
} }))

function mount(url = '/research') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[url]}><Research /></MemoryRouter></QueryClientProvider>)
}

afterEach(() => { vi.clearAllMocks() })

describe('Research history page', () => {
  it('handles loading and malformed legacy records as empty safely', async () => {
    vi.mocked(api.taiwanAIResearchHistory).mockResolvedValue({ items: [{ id: 42 as unknown as string }, { id: 'ok', symbol: '2330.TWSE' }] })
    vi.mocked(api.taiwanAIResearchHistoryDetail).mockResolvedValue({ id: 'ok', symbol: '2330.TWSE' })
    mount()
    expect(screen.getByRole('status')).toHaveTextContent('載入研究歷史')
    expect(await screen.findByText('2330.TWSE')).toBeInTheDocument()
    expect(screen.getAllByText(/provider 未提供/).length).toBeGreaterThan(0)
  })

  it('shows empty state and deterministic three-section comparison', async () => {
    vi.mocked(api.taiwanAIResearchHistory).mockResolvedValue({ items: [
      { id: 'a', symbol: '2330.TWSE', provider: 'p1' },
      { id: 'b', symbol: '2330.TWSE', provider: 'p2' },
    ] })
    vi.mocked(api.taiwanAIResearchHistoryDetail).mockResolvedValue({ id: 'a', symbol: '2330.TWSE', report: { overview: 'ok' } })
    vi.mocked(api.taiwanAIResearchHistoryCompare).mockResolvedValue({ data_changes: { close: [1, 2] }, model_prompt_changes: { model: true }, interpretation_changes: { overview: true } })
    mount()
    await screen.findByRole('heading', { name: /研究詳情：2330\.TWSE/ })
    fireEvent.click(screen.getAllByText('選為比較 A')[1])
    fireEvent.click(screen.getAllByText('選為比較 B')[0])
    expect(await screen.findByText('資料變化')).toBeInTheDocument()
    expect(screen.getByText('模型／提示詞變化')).toBeInTheDocument()
    expect(screen.getByText('解讀變化')).toBeInTheDocument()
    await waitFor(() => expect(api.taiwanAIResearchHistoryCompare).toHaveBeenCalled())
  })

  it('shows a useful empty state when history has no records', async () => {
    vi.mocked(api.taiwanAIResearchHistory).mockResolvedValue({ items: [] })
    mount('/research?symbol=2330.TWSE')
    expect(await screen.findByText('沒有符合篩選條件的研究紀錄。')).toBeInTheDocument()
  })
})

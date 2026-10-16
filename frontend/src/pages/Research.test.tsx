import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { api } from '@/lib/api'
import { Research } from './Research'

vi.mock('@/lib/api', () => ({ api: {
  taiwanAIResearchHistory: vi.fn(),
  taiwanAIResearchHistoryDetail: vi.fn(),
  taiwanAIResearchHistoryCompare: vi.fn(),
}, researchPromptVersion: (record: { prompt_versions?: unknown; prompt_version?: unknown }) => {
  const versions = record.prompt_versions
  if (versions && typeof versions === 'object' && !Array.isArray(versions) && typeof (versions as Record<string, unknown>).research === 'string') return (versions as Record<string, string>).research
  return typeof record.prompt_version === 'string' ? record.prompt_version : null
} }))

function mount(url = '/research') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[url]}><Research /></MemoryRouter></QueryClientProvider>)
}

afterEach(() => { vi.clearAllMocks() })

describe('Research history page', () => {
  it('opens an older record outside the list and links its frozen candidate to tracking', async () => {
    vi.mocked(api.taiwanAIResearchHistory).mockResolvedValue({ items: [] })
    vi.mocked(api.taiwanAIResearchHistoryDetail).mockResolvedValue({
      id: 'old', symbol: '2330.TWSE', evidence_as_of: '2026-09-01',
      evidence_payload: { personal_context: {
        quant: { status: 'available', selected: true, model_key: 'baseline_v1', session: '2026-09-01', rank: 3 },
        portfolio: { shares: 100 }, alert: { alert_id: 'alert-1', message: '進入候選', triggered_at: '2026-09-01T10:00:00+08:00' },
      } },
    })
    mount('/research?id=old')
    const workflow = await screen.findByRole('region', { name: '研究來源與後續追蹤' })
    expect(workflow).toHaveTextContent('當時持股：100 股')
    expect(workflow).toHaveTextContent('alert-1')
    expect(within(workflow).getByRole('link', { name: '原候選快照與事後追蹤' })).toHaveAttribute('href', '/selection-review?tab=live&model_key=baseline_v1&session=2026-09-01')
    expect(within(workflow).getByRole('link', { name: '檢視／登錄持股' })).toHaveAttribute('href', '/stocks/2330.TWSE#portfolio')
    expect(within(workflow).getByRole('link', { name: '檢視／設定提醒' })).toHaveAttribute('href', '/stocks/2330.TWSE#monitor')
    expect(api.taiwanAIResearchHistoryDetail).toHaveBeenCalledWith('old')
  })

  it('does not invent sources or holdings for legacy records', async () => {
    vi.mocked(api.taiwanAIResearchHistory).mockResolvedValue([{ id: 'legacy', symbol: '2330.TWSE' }])
    vi.mocked(api.taiwanAIResearchHistoryDetail).mockResolvedValue({ id: 'legacy', symbol: '2330.TWSE', evidence_payload: 'broken' })
    mount()
    expect(await screen.findByText(/未記錄，不能視為零持股/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '原候選快照與事後追蹤' })).not.toBeInTheDocument()
  })
  it('handles loading and malformed legacy records as empty safely', async () => {
    vi.mocked(api.taiwanAIResearchHistory).mockResolvedValue([{ id: 42 as unknown as string }, { id: 'link', kind: 'link', symbol: '9999.TWSE' }, { id: 'ok', symbol: '2330.TWSE' }])
    vi.mocked(api.taiwanAIResearchHistoryDetail).mockResolvedValue({ id: 'ok', symbol: '2330.TWSE' })
    mount()
    expect(screen.getByRole('status')).toHaveTextContent('載入研究歷史')
    expect(await screen.findByText('2330.TWSE')).toBeInTheDocument()
    expect(screen.getAllByText(/provider 未提供/).length).toBeGreaterThan(0)
  })

  it('shows a readable old -> new comparison', async () => {
    vi.mocked(api.taiwanAIResearchHistory).mockResolvedValue({ items: [
      { id: 'a', symbol: '2330.TWSE', provider: 'p1' },
      { id: 'b', symbol: '2330.TWSE', provider: 'p2' },
    ] })
    vi.mocked(api.taiwanAIResearchHistoryDetail).mockResolvedValue({ id: 'a', symbol: '2330.TWSE', report: { overview: 'ok' } })
    vi.mocked(api.taiwanAIResearchHistoryCompare).mockResolvedValue({
      evidence_data_changes: { field_changes: [{ path: 'price_context.close', before: 2500, after: 2540 }] },
      model_prompt_config_changes: { field_changes: [] },
      interpretation_report_changes: { field_changes: [{ path: 'overview', before: '舊總覽', after: '新總覽' }] },
    })
    mount()
    await screen.findByRole('heading', { name: /研究詳情：2330\.TWSE/ })
    fireEvent.click(screen.getAllByText('選為比較 A')[1])
    fireEvent.click(screen.getAllByText('選為比較 B')[0])
    expect(await screen.findByRole('region', { name: '重點欄位變化' })).toHaveTextContent('收盤價')
    expect(screen.getByText('新：新總覽')).toBeInTheDocument()
    expect(screen.getByText(/模型與提示詞相同/)).toBeInTheDocument()
    await waitFor(() => expect(api.taiwanAIResearchHistoryCompare).toHaveBeenCalled())
  })

  it('shows a useful empty state when history has no records', async () => {
    vi.mocked(api.taiwanAIResearchHistory).mockResolvedValue({ items: [] })
    mount('/research?symbol=2330.TWSE')
    expect(await screen.findByText('沒有符合篩選條件的研究紀錄。')).toBeInTheDocument()
  })

  it('renders a saved record whose report is nested under response.report as readable text', async () => {
    vi.mocked(api.taiwanAIResearchHistory).mockResolvedValue({ items: [{ id: 'n1', symbol: '6147.TPEX' }] })
    vi.mocked(api.taiwanAIResearchHistoryDetail).mockResolvedValue({
      id: 'n1', symbol: '6147.TPEX',
      response: { status: 'success', report: { overview: '白話總覽文字', key_observations: [{ text: '外資近 20 日淨賣超', evidence_refs: ['institutional_context.foreign_net_20d'] }], risk_factors: [], missing_information: [], disclaimer: '' } },
    })
    mount()
    expect(await screen.findByText('白話總覽文字')).toBeInTheDocument()
    expect(screen.getByText('重點觀察')).toBeInTheDocument()
    expect(screen.getByText('外資近 20 日淨賣超')).toBeInTheDocument()
  })
})

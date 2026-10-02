import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { formatSocialSentimentPrompt } from '@/lib/copy-formatters'
import { SocialSentiment } from './SocialSentiment'

const response = {
  schema_version: 1,
  status: 'partial' as const,
  generated_at: new Date().toISOString(),
  started_at: new Date().toISOString(),
  finished_at: new Date().toISOString(),
  as_of: '2026-09-29',
  trigger: 'after_close' as const,
  snapshot_slot: 'after_close' as const,
  snapshot_id: '2026-09-29-after_close.json',
  window_hours: 24,
  sources: {
    ptt: { status: 'available' as const, posts: 26, comments: 5894, pages: 3, errors: [] },
    dcard: { status: 'unavailable' as const, posts: 0, comments: 0, pages: 0, errors: ['HTTP 403'] },
  },
  identified_symbols: 2,
  ai: { status: 'degraded' as const, batches: 1, analyzed_symbols: 1, errors: [] },
  rankings: [
    { rank: 1, symbol: '2330.TWSE', code: '2330', company_name: '台積電', ptt_mentions: 10, dcard_mentions: 0, total_mentions: 10, unique_posts: 3, engagement: 20, volume_change_24h: 0.5, bullish_count: 2, neutral_count: 1, bearish_count: 0, sentiment: 'bullish' as const, sentiment_status: 'available' as const, sentiment_score: 0.8, sentiment_confidence: 0.9, sentiment_reason: '偏多', social_heat_score: 60 },
    { rank: 2, symbol: '2317.TWSE', code: '2317', company_name: '鴻海', ptt_mentions: 20, dcard_mentions: 0, total_mentions: 20, unique_posts: 5, engagement: 30, volume_change_24h: null, bullish_count: 0, neutral_count: 0, bearish_count: 0, sentiment: 'unavailable' as const, sentiment_status: 'unavailable' as const, sentiment_score: null, sentiment_confidence: null, sentiment_reason: null, social_heat_score: 50 },
  ],
  discussions: [],
}

vi.mock('@/lib/api', () => ({
  api: {
    taiwanSocialSentiment: vi.fn(),
    taiwanSocialSentimentHistory: vi.fn(),
    watchlistList: vi.fn(),
  },
}))

function renderPage() {
  vi.mocked(api.taiwanSocialSentiment).mockResolvedValue(response)
  vi.mocked(api.taiwanSocialSentimentHistory).mockResolvedValue({ items: [] })
  vi.mocked(api.watchlistList).mockResolvedValue({ symbols: [] })
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter><SocialSentiment /></MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('SocialSentiment', () => {
  it('renders a partial ranking and never presents blocked Dcard as zero', async () => {
    renderPage()
    expect(await screen.findByText('部分來源可用')).toBeInTheDocument()
    expect(screen.getByText('Dcard：目前來源不可用')).toBeInTheDocument()
    const dcardColumn = screen.getAllByRole('cell').filter(cell => cell.textContent === '不可用')
    expect(dcardColumn.length).toBeGreaterThan(0)
    expect(screen.getByText('台積電')).toBeInTheDocument()
    expect(screen.getByText(/來源：盤後排程/)).toBeInTheDocument()
  })

  it('sorts by mentions, filters AI rows, and opens row detail', async () => {
    renderPage()
    await screen.findByText('台積電')
    fireEvent.click(screen.getByRole('button', { name: '聲量排序' }))
    const table = screen.getByRole('table')
    const stockButtons = within(table).getAllByRole('button', { name: /查看 .* 社群明細/ })
    expect(stockButtons[0]).toHaveAccessibleName('查看 鴻海 社群明細')
    fireEvent.click(screen.getByLabelText('只看有 AI 情緒'))
    expect(screen.queryByText('鴻海')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '查看 台積電 社群明細' }))
    expect(screen.getByText('台積電 社群明細')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /前往個股分析/ })).toHaveAttribute('href', '/stocks/2330.TWSE')
  })

  it('formats an external-AI prompt with source coverage and bias warning', () => {
    const text = formatSocialSentimentPrompt({
      as_of: response.as_of,
      generated_at: response.generated_at,
      source_statuses: { ptt: 'available', dcard: 'unavailable' },
      rankings: [{ rank: 1, symbol: '2330.TWSE', name: '台積電', mentions: 10, heat: 60, sentiment: '偏多', score: 0.8, confidence: 0.9 }],
    })
    expect(text).toContain('dcard=unavailable')
    expect(text).toContain('社群討論可能有抽樣與群體偏誤')
    expect(text).toContain('不得把缺失資料當成 0')
  })
})

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { SocialFetch } from './SocialFetch'

vi.mock('@/lib/api', () => ({
  api: {
    taiwanSocialSentimentRun: vi.fn(),
    taiwanSocialSentimentJob: vi.fn(),
  },
}))

const partialJob = {
  job_id: 'manual-1',
  status: 'partial' as const,
  started_at: '2026-09-30T09:38:12+08:00',
  finished_at: '2026-09-30T09:42:00+08:00',
  ptt_status: 'available' as const,
  dcard_status: 'unavailable' as const,
  ai_status: 'degraded' as const,
  posts: 41,
  comments: 6875,
  symbols_identified: 2,
  error_summary: 'Dcard unavailable',
  snapshot_id: '2026-09-30-093812-123456-manual.json',
  rankings: [
    { rank: 1, symbol: '2330.TWSE', code: '2330', company_name: '台積電', ptt_mentions: 10, dcard_mentions: 0, total_mentions: 10, unique_posts: 3, engagement: 20, volume_change_24h: null, bullish_count: 2, neutral_count: 1, bearish_count: 0, sentiment: 'bullish' as const, sentiment_status: 'available' as const, sentiment_score: 0.8, sentiment_confidence: 0.9, sentiment_reason: '偏多', social_heat_score: 60 },
  ],
  discussions: {
    items: [{ id: 'ptt:M.1', source: 'ptt' as const, published_at: '2026-09-30T09:30:00+08:00', symbols: ['2330.TWSE'], stock_names: ['台積電'], title: '台積電討論', url: 'https://www.ptt.cc/bbs/Stock/M.1.html', excerpt: '看好後續表現', representative_comments: ['代表留言'], comments_count: 12, engagement: 20 }],
    total: 51,
    offset: 0,
    limit: 50,
    has_more: true,
  },
}

function renderPage() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter><SocialFetch /></MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('SocialFetch', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.taiwanSocialSentimentJob).mockResolvedValue(partialJob)
  })

  it('starts a manual run and disables the button while the request is pending', async () => {
    let resolveRun!: (value: { job_id: string; status: 'running' }) => void
    vi.mocked(api.taiwanSocialSentimentRun).mockReturnValue(new Promise(resolve => { resolveRun = resolve }))
    renderPage()

    const button = screen.getByRole('button', { name: '立即撈取 PTT / Dcard' })
    fireEvent.click(button)

    expect(button).toBeDisabled()
    expect(screen.getByText('正在啟動社群資料更新…')).toBeInTheDocument()
    resolveRun({ job_id: 'manual-1', status: 'running' })
    await waitFor(() => expect(api.taiwanSocialSentimentRun).toHaveBeenCalledWith({ mode: 'manual' }))
  })

  it('renders partial source status, ranking, discussion filters, and pagination', async () => {
    vi.mocked(api.taiwanSocialSentimentRun).mockResolvedValue({ job_id: 'manual-1', status: 'running' })
    vi.mocked(api.taiwanSocialSentimentJob).mockResolvedValue(partialJob)
    renderPage()

    fireEvent.click(screen.getByRole('button', { name: '立即撈取 PTT / Dcard' }))

    expect(await screen.findByText('Dcard：目前來源不可用')).toBeInTheDocument()
    expect(screen.getAllByText('台積電').length).toBeGreaterThan(0)
    expect(screen.getByText('台積電討論')).toBeInTheDocument()
    expect(screen.getByText('代表留言')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('來源篩選'), { target: { value: 'ptt' } })
    fireEvent.change(screen.getByLabelText('股票篩選'), { target: { value: '2330.TWSE' } })
    fireEvent.change(screen.getByLabelText('關鍵字搜尋'), { target: { value: '看好' } })
    await waitFor(() => expect(api.taiwanSocialSentimentJob).toHaveBeenLastCalledWith(
      'manual-1',
      expect.objectContaining({ source: 'ptt', symbol: '2330.TWSE', q: '看好', offset: 0, limit: 50 }),
    ))

    fireEvent.click(screen.getByRole('button', { name: '載入更多討論' }))
    await waitFor(() => expect(api.taiwanSocialSentimentJob).toHaveBeenLastCalledWith(
      'manual-1',
      expect.objectContaining({ offset: 50, limit: 50 }),
    ))
  })

  it('shows already-running state honestly', async () => {
    vi.mocked(api.taiwanSocialSentimentRun).mockResolvedValue({ job_id: null, status: 'already_running' })
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: '立即撈取 PTT / Dcard' }))
    expect(await screen.findByText('社群資料正在更新中')).toBeInTheDocument()
  })

  it('shows completed status and result content', async () => {
    vi.mocked(api.taiwanSocialSentimentRun).mockResolvedValue({ job_id: 'manual-1', status: 'running' })
    vi.mocked(api.taiwanSocialSentimentJob).mockResolvedValue({
      ...partialJob,
      status: 'completed',
      dcard_status: 'available',
      ai_status: 'available',
      error_summary: null,
    })
    renderPage()

    fireEvent.click(screen.getByRole('button', { name: '立即撈取 PTT / Dcard' }))

    expect(await screen.findByText('完成')).toBeInTheDocument()
    expect(screen.getByText('本次結果排行榜')).toBeInTheDocument()
  })

  it('shows failed status and its bounded error summary', async () => {
    vi.mocked(api.taiwanSocialSentimentRun).mockResolvedValue({ job_id: 'manual-1', status: 'running' })
    vi.mocked(api.taiwanSocialSentimentJob).mockResolvedValue({
      ...partialJob,
      status: 'failed',
      error_summary: 'collector timeout',
      rankings: [],
      discussions: { ...partialJob.discussions, items: [], total: 0, has_more: false },
    })
    renderPage()

    fireEvent.click(screen.getByRole('button', { name: '立即撈取 PTT / Dcard' }))

    expect(await screen.findByText('失敗')).toBeInTheDocument()
    expect(screen.getByText('本次撈取失敗：collector timeout')).toBeInTheDocument()
  })
})

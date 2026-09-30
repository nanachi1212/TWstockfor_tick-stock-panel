import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DataHealth } from './DataHealth'
import { DataHealthSummary } from '@/components/dashboard/DataHealthSummary'
import { api, type DataHealthReport, type DataHealthJob } from '@/lib/api'
import { CORE_NAV } from '@/lib/navigation'
import { useDataHealthJobs } from '@/lib/useDataHealth'

vi.mock('@/lib/api', () => ({ api: { dataHealth: vi.fn(), dataHealthJobs: vi.fn(), dataHealthAction: vi.fn() } }))

const fixture: DataHealthReport = {
  generated_at: '2026-09-30T10:00:00+08:00', current_count: 1, total_count: 3,
  datasets: [
    { id: 'daily', name: 'Daily OHLC', status: 'current', source: 'Taiwan daily store', data_date: '2026-09-29', freshness: '目標交易日 2026-09-29', reason: '符合交易日規則', last_attempt: null, last_success: null, actions: ['update', 'validate'] },
    { id: 'margin', name: 'Margin / Short', status: 'stale', source: '官方', data_date: '2026-09-28', freshness: '目標交易日 2026-09-29', reason: '今日官方資料尚未發布', last_attempt: '2026-09-30T09:00:00+08:00', last_success: null, actions: ['retry', 'validate'] },
    { id: 'dcard', name: 'Dcard', status: 'unavailable', source: 'Dcard', data_date: null, freshness: '未知', reason: 'HTTP 403', last_attempt: null, last_success: null, actions: [] },
  ],
}
const job: DataHealthJob = { job_id: 'job1', dataset: 'margin', action: 'retry', affected_datasets: ['daily', 'margin'], status: 'queued', queued_at: '2026-09-30T10:00:00+08:00', started_at: null, finished_at: null, reason: null }

function HealthShell() {
  useDataHealthJobs()
  return <DataHealth />
}

function mount(component = <HealthShell />) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter>{component}</MemoryRouter></QueryClientProvider>)
  return client
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.dataHealth).mockResolvedValue(structuredClone(fixture))
  vi.mocked(api.dataHealthJobs).mockResolvedValue([])
})

describe('資料健康中心', () => {
  it('renders dates, causes and missing metadata without zero or unsafe Dcard actions', async () => {
    mount()
    const dcard = (await screen.findByRole('rowheader', { name: 'Dcard' })).closest('tr')!
    expect(within(dcard).getByText('HTTP 403')).toBeInTheDocument()
    expect(within(dcard).queryAllByRole('button')).toHaveLength(0)
    expect(within(dcard).getAllByText('未知')).toHaveLength(4)
    expect(screen.getByText('今日官方資料尚未發布')).toBeInTheDocument()
    expect(screen.getByText('1 / 3 正常')).toBeInTheDocument()
  })

  it('filters by status and source/reason text and shows empty results', async () => {
    mount()
    await screen.findByRole('rowheader', { name: 'Daily OHLC' })
    fireEvent.change(screen.getByLabelText('狀態篩選'), { target: { value: 'stale' } })
    expect(screen.queryByRole('rowheader', { name: 'Dcard' })).not.toBeInTheDocument()
    expect(screen.getByRole('rowheader', { name: 'Margin / Short' })).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('搜尋資料集'), { target: { value: 'HTTP 403' } })
    expect(screen.getByText('沒有符合篩選條件的資料集。')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('狀態篩選'), { target: { value: '' } })
    expect(screen.getByRole('rowheader', { name: 'Dcard' })).toBeInTheDocument()
  })

  it('queues an existing updater action and prevents duplicate clicks across affected rows', async () => {
    let resolve!: (value: DataHealthJob) => void
    vi.mocked(api.dataHealthAction).mockImplementation(() => new Promise(r => { resolve = r }))
    mount()
    const retry = await screen.findByRole('button', { name: 'Margin / Short 重試' })
    await waitFor(() => expect(retry).toBeEnabled())
    fireEvent.click(retry)
    fireEvent.click(retry)
    await waitFor(() => expect(api.dataHealthAction).toHaveBeenCalledTimes(1))
    expect(api.dataHealthAction).toHaveBeenCalledWith('margin', 'retry')
    expect(screen.getByRole('button', { name: 'Daily OHLC 立即更新' })).toBeDisabled()
    vi.mocked(api.dataHealthJobs).mockResolvedValue([job])
    resolve(job)
    await screen.findByText(/排隊中 \(queued\)/)
    expect(retry).toBeDisabled()
  })

  it('shows partial/failed/running jobs and invalidates consumers on completion', async () => {
    vi.mocked(api.dataHealthJobs).mockResolvedValue([{ ...job, status: 'partial', reason: '部分來源仍缺漏' }])
    const client = mount()
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    await screen.findByText(/部分完成 \(partial\)/)
    expect(screen.getByText('部分來源仍缺漏')).toBeInTheDocument()
    await waitFor(() => expect(invalidate).toHaveBeenCalled())
  })

  it('shows loading and health read failures without a fake normal count', async () => {
    vi.mocked(api.dataHealth).mockRejectedValue(new Error('offline'))
    mount()
    expect(screen.getByText('正在讀取資料健康…')).toBeInTheDocument()
    expect(await screen.findByRole('alert')).toHaveTextContent('資料健康讀取失敗')
    expect(screen.queryByText('1 / 3 正常')).not.toBeInTheDocument()
  })

  it('disables actions when job tracking fails', async () => {
    vi.mocked(api.dataHealthJobs).mockRejectedValue(new Error('offline'))
    mount()
    await screen.findByText('無法讀取背景任務，暫停操作以避免重複更新。')
    expect(screen.getByRole('button', { name: 'Daily OHLC 立即更新' })).toBeDisabled()
  })

  it('Dashboard displays only the health summary link and Sidebar registers the route', async () => {
    mount(<DataHealthSummary />)
    expect(await screen.findByRole('link', { name: '資料健康 1 / 3 正常' })).toHaveAttribute('href', '/data-health')
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(CORE_NAV.find(item => item.to === '/data-health')?.label).toBe('資料健康')
  })
})

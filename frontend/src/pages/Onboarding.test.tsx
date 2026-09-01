// Phase 8B-1 — Taiwan-first Onboarding 回歸測試
// 涵蓋：新 4 步流程（使用須知 → 歡迎 → 台股資料狀態 → 完成）、無 API Key / AI
// 也能完成引導、台股資料狀態步驟在無資料時不阻擋完成、完成後寫入 onboarding_completed、
// 舊 A 股 onboarding 文案不再出現。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Onboarding } from './Onboarding'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: {
    completeOnboarding: vi.fn(),
    taiwanDataStatus: vi.fn(),
  },
}))

function LocationSpy() {
  const location = useLocation()
  return <div data-testid="location">{location.pathname}</div>
}

function renderOnboarding() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/onboarding']}>
        <Routes>
          <Route path="/onboarding" element={<Onboarding />} />
          <Route path="/" element={<LocationSpy />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function mockDataStatus(overrides: Partial<Record<string, unknown>> = {}) {
  vi.mocked(api.taiwanDataStatus).mockResolvedValue({
    daily_as_of: null,
    institutional_as_of: null,
    margin_as_of: null,
    target_latest_trading_date: '2026-09-01',
    is_fully_current: false,
    daily_status: 'unavailable',
    institutional_status: 'unavailable',
    margin_status: 'unavailable',
    daily_days_behind: 0,
    institutional_days_behind: 0,
    margin_days_behind: 0,
    scheduler_enabled: true,
    scheduled_update_time: '16:30',
    scheduled_timezone: 'Asia/Taipei',
    ...overrides,
  } as any)
}

beforeEach(() => {
  mockDataStatus()
  vi.mocked(api.completeOnboarding).mockResolvedValue({ ok: true, onboarding_completed: true } as any)
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('Onboarding — Taiwan-first flow', () => {
  it('welcome step introduces the tool as Taiwan-market-first, not an A-share panel', async () => {
    renderOnboarding()
    fireEvent.click(await screen.findByText('我已了解,繼續'))

    expect(await screen.findByText('歡迎使用台股分析工具')).toBeInTheDocument()
    // 旧 A 股 onboarding 定位文案不应再出现
    expect(screen.queryByText(/A 股/)).not.toBeInTheDocument()
    expect(screen.queryByText(/连板梯队/)).not.toBeInTheDocument()
    // 只展示已完成的台股功能, 不宣传能力探测 / 数据源配置步骤
    expect(screen.queryByText('配置数据源')).not.toBeInTheDocument()
    expect(screen.queryByText('能力探测结果')).not.toBeInTheDocument()
  })

  it('can complete onboarding with no API key and no local Taiwan data — shows a clear non-blocking hint', async () => {
    renderOnboarding()
    fireEvent.click(await screen.findByText('我已了解,繼續'))
    fireEvent.click(await screen.findByText('開始使用'))

    // 台股资料状态步骤: 无资料时显示提示, 但仍可继续
    expect(await screen.findByText(/目前尚未下載台股資料/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('下一步'))

    // 完成步骤
    expect(await screen.findByText('一切就緒!')).toBeInTheDocument()
    fireEvent.click(screen.getByText('進入面板'))

    await waitFor(() => expect(api.completeOnboarding).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/'))
  })

  it('shows Taiwan data freshness when local data already exists', async () => {
    mockDataStatus({
      daily_as_of: '2026-08-31',
      institutional_as_of: '2026-08-31',
      margin_as_of: '2026-08-31',
      is_fully_current: true,
      daily_status: 'current',
      institutional_status: 'current',
      margin_status: 'current',
    })
    renderOnboarding()
    fireEvent.click(await screen.findByText('我已了解,繼續'))
    fireEvent.click(await screen.findByText('開始使用'))

    expect((await screen.findAllByText('2026-08-31')).length).toBeGreaterThan(0)
    expect(screen.getAllByText('最新').length).toBeGreaterThan(0)
    expect(screen.queryByText(/目前尚未下載台股資料/)).not.toBeInTheDocument()
  })

  it('skip on the welcome step completes onboarding immediately without requiring any key', async () => {
    renderOnboarding()
    fireEvent.click(await screen.findByText('我已了解,繼續'))
    fireEvent.click(await screen.findByText('稍後再說'))

    await waitFor(() => expect(api.completeOnboarding).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/'))
  })
})

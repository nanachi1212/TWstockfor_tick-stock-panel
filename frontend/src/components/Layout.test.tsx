// Phase 8B-2 — Taiwan-first Navigation 回歸測試
// 涵蓋：主導覽預設顯示台股功能、預設不顯示 A 股 legacy 功能、
// show_ashare_legacy_features 開啟後「中國 A 股（選配）」區塊出現且不與台股導航混排。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Layout } from './Layout'
import { usePreferences, useCapabilities, useSettings, useQuoteStatus, useVersion } from '@/lib/useSharedQueries'
import { useToggleRealtimeQuotes } from '@/lib/useSharedMutations'
import { useQuoteStream, useQuoteStreamStatus } from '@/lib/useQuoteStream'
import { getFrontendExtensionNavigation } from '@/extensions/registry'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: {
    dataSources: vi.fn().mockResolvedValue({ builtin: [], plugins: [], custom: [] }),
    analysisMenus: vi.fn().mockResolvedValue({ items: [] }),
    watchlistGroups: vi.fn().mockResolvedValue({ groups: [] }),
    watchlistList: vi.fn().mockResolvedValue({ items: [] }),
    watchlistEnriched: vi.fn().mockResolvedValue({ items: [] }),
    indexQuotes: vi.fn().mockResolvedValue({ rows: [] }),
    alertsList: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    intradayRefresh: vi.fn().mockResolvedValue({}),
    pipelineJobs: vi.fn().mockResolvedValue({ active_id: null }),
  },
}))

vi.mock('@/lib/useSharedQueries', () => ({
  useCapabilities: vi.fn(),
  useSettings: vi.fn(),
  usePreferences: vi.fn(),
  useQuoteStatus: vi.fn(),
  useVersion: vi.fn(),
}))

vi.mock('@/lib/useSharedMutations', () => ({
  useToggleRealtimeQuotes: vi.fn(),
}))

vi.mock('@/lib/useQuoteStream', () => ({
  useQuoteStream: vi.fn(),
  useQuoteStreamStatus: vi.fn(),
}))

vi.mock('@/extensions/registry', () => ({
  getFrontendExtensionNavigation: vi.fn(() => []),
}))

vi.mock('@/extensions/ExtensionSlot', () => ({
  ExtensionSlot: () => null,
}))

vi.mock('@/components/Toast', () => ({
  ToastContainer: () => null,
  toast: vi.fn(),
}))
vi.mock('@/components/AlertToast', () => ({ AlertToastContainer: () => null }))
vi.mock('@/components/stock-analysis/StockAnalysisHost', () => ({ StockAnalysisHost: () => null }))
vi.mock('@/components/stock-analysis/StockAnalysisBubble', () => ({ StockAnalysisBubble: () => null }))

function mockCommonHooks() {
  vi.mocked(useCapabilities).mockReturnValue({ data: undefined } as any)
  vi.mocked(useSettings).mockReturnValue({ data: undefined } as any)
  vi.mocked(useQuoteStatus).mockReturnValue({ data: undefined } as any)
  vi.mocked(useVersion).mockReturnValue({ data: undefined } as any)
  vi.mocked(useToggleRealtimeQuotes).mockReturnValue({ mutateAsync: vi.fn() } as any)
  vi.mocked(useQuoteStream).mockReturnValue(undefined as any)
  vi.mocked(useQuoteStreamStatus).mockReturnValue({ connected: false } as any)
  vi.mocked(getFrontendExtensionNavigation).mockReturnValue([])
}

function renderLayout() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/']}>
        <Layout />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  mockCommonHooks()
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('Layout — Taiwan-first navigation (Phase 8B-2)', () => {
  it('shows Taiwan-first nav items by default (台股選股 / 多股比較 / 自選股)', async () => {
    vi.mocked(usePreferences).mockReturnValue({ data: {} } as any)
    renderLayout()

    expect(await screen.findByText('台股選股')).toBeInTheDocument()
    expect(screen.getByText('多股比較')).toBeInTheDocument()
    expect(screen.getByText('自選股')).toBeInTheDocument()
  })

  it('hides A-share legacy nav items by default', async () => {
    vi.mocked(usePreferences).mockReturnValue({ data: {} } as any)
    renderLayout()

    await screen.findByText('台股選股')
    expect(screen.queryByText('連板梯隊')).not.toBeInTheDocument()
    expect(screen.queryByText('概念分析')).not.toBeInTheDocument()
    expect(screen.queryByText('行業分析')).not.toBeInTheDocument()
    expect(screen.queryByText('中國 A 股（選配）')).not.toBeInTheDocument()
  })

  it('Phase 8B-4.2.1: /backtest 與 /data 預設不在核心導航中顯示', async () => {
    vi.mocked(usePreferences).mockReturnValue({ data: {} } as any)
    renderLayout()

    await screen.findByText('台股選股')
    expect(screen.queryByText('A 股回測')).not.toBeInTheDocument()
    expect(screen.queryByText('A 股資料管理')).not.toBeInTheDocument()
  })

  it('reveals the "中國 A 股（選配）" section with legacy routes intact when the preference is on', async () => {
    vi.mocked(usePreferences).mockReturnValue({ data: { show_ashare_legacy_features: true } } as any)
    renderLayout()

    await screen.findByText('台股選股')
    expect(screen.getByText('中國 A 股（選配）')).toBeInTheDocument()
    expect(screen.getByText('連板梯隊')).toBeInTheDocument()
    expect(screen.getByText('概念分析')).toBeInTheDocument()
    expect(screen.getByText('行業分析')).toBeInTheDocument()
    // 台股核心導航仍在, 不被 A 股區塊取代或混排
    expect(screen.getByText('台股選股')).toBeInTheDocument()
  })

  it('Phase 8B-4.2.1: 開啟 A 股 legacy 後,「中國 A 股（選配）」區塊顯示 A 股回測 / A 股資料管理', async () => {
    vi.mocked(usePreferences).mockReturnValue({ data: { show_ashare_legacy_features: true } } as any)
    renderLayout()

    await screen.findByText('台股選股')
    expect(screen.getByText('A 股回測')).toBeInTheDocument()
    expect(screen.getByText('A 股資料管理')).toBeInTheDocument()
  })

  it('Phase 8B-3.2: does not fetch A-share sidebar index quotes when the preference is off', async () => {
    vi.mocked(usePreferences).mockReturnValue({ data: {} } as any)
    renderLayout()

    await screen.findByText('台股選股')
    // 给足够时间让 disabled 之外的其他查询完成, 确认 indexQuotes 全程未被调用
    await waitFor(() => expect(api.dataSources).toHaveBeenCalled())
    expect(api.indexQuotes).not.toHaveBeenCalled()
  })

  it('Phase 8B-3.2: fetches A-share sidebar index quotes once the preference is on', async () => {
    vi.mocked(usePreferences).mockReturnValue({ data: { show_ashare_legacy_features: true } } as any)
    renderLayout()

    await screen.findByText('台股選股')
    await waitFor(() => expect(api.indexQuotes).toHaveBeenCalled())
  })
})

// Phase 8B-2 — Taiwan-first Navigation 回歸測試
// Phase 8C-D — Legacy Product Removal: 「中國 A 股（選配）」區塊、
// show_ashare_legacy_features 開關、A 股側邊欄指數卡片(SidebarIndexQuotes)
// 已隨產品介面正式移除, 不再有任何 legacy 開關可以「開啟」——CORE_NAV 是
// 主導覽唯一的清單。
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
    watchlistGroups: vi.fn().mockResolvedValue({ groups: [] }),
    watchlistList: vi.fn().mockResolvedValue({ items: [] }),
    watchlistEnriched: vi.fn().mockResolvedValue({ items: [] }),
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
    expect(screen.queryByText('策略選股')).not.toBeInTheDocument()
    expect(screen.queryByText('中國 A 股（選配）')).not.toBeInTheDocument()
  })

  it('Phase 8B-4.2.1: /backtest 預設不在核心導航中顯示', async () => {
    vi.mocked(usePreferences).mockReturnValue({ data: {} } as any)
    renderLayout()

    await screen.findByText('台股選股')
    expect(screen.queryByText('A 股回測')).not.toBeInTheDocument()
  })
})

describe('Layout — Legacy A-share removal (Phase 8C-D)', () => {
  it('legacy section stays absent even if a stale show_ashare_legacy_features field is present in cached preferences', async () => {
    // 偏好型別已移除該欄位, 但既有使用者 localStorage/cache 可能仍殘留舊值 —
    // 確保 Layout 不再讀取它、也不會意外恢復 legacy 區塊。
    vi.mocked(usePreferences).mockReturnValue({ data: { show_ashare_legacy_features: true } as any } as any)
    renderLayout()

    await screen.findByText('台股選股')
    expect(screen.queryByText('中國 A 股（選配）')).not.toBeInTheDocument()
    expect(screen.queryByText('策略選股')).not.toBeInTheDocument()
    expect(screen.queryByText('A 股回測')).not.toBeInTheDocument()
    expect(screen.queryByText('因子挖掘')).not.toBeInTheDocument()
  })

  it('never fetches A-share sidebar index quotes (feature removed entirely, not just gated)', async () => {
    vi.mocked(usePreferences).mockReturnValue({ data: {} } as any)
    renderLayout()

    await waitFor(() => expect(api.dataSources).toHaveBeenCalled())
    expect((api as any).indexQuotes).toBeUndefined()
  })
})

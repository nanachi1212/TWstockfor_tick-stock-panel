// Phase 8B-2 — Taiwan-first Navigation 回歸測試
// Phase 8C-D — Legacy Product Removal: 「中國 A 股（選配）」區塊、
// show_ashare_legacy_features 開關、A 股側邊欄指數卡片(SidebarIndexQuotes)
// 已隨產品介面正式移除, 不再有任何 legacy 開關可以「開啟」——CORE_NAV 是
// 主導覽唯一的清單。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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
    dataHealthJobs: vi.fn().mockResolvedValue([]),
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

describe('Layout — 窄螢幕抽屜導覽', () => {
  const originalMatchMedia = window.matchMedia
  const mockViewport = (compact: boolean) => {
    window.matchMedia = ((query: string) => ({
      ...originalMatchMedia(query),
      matches: compact && query === '(max-width: 767px)',
    })) as typeof window.matchMedia
  }

  beforeEach(() => {
    vi.mocked(usePreferences).mockReturnValue({ data: {} } as any)
  })

  afterEach(() => {
    window.matchMedia = originalMatchMedia
  })

  it('手機: 不渲染側欄, 由「開啟選單」按鈕開抽屜', async () => {
    mockViewport(true)
    renderLayout()

    const menuButton = await screen.findByRole('button', { name: '開啟選單' })
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(menuButton).toHaveAttribute('aria-expanded', 'false')

    fireEvent.click(menuButton)
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(menuButton).toHaveAttribute('aria-expanded', 'true')
    expect(within(dialog).getByRole('link', { name: '自選股' })).toBeInTheDocument()
    expect(dialog).toContainElement(document.activeElement as HTMLElement)
    expect(document.body.style.overflow).toBe('hidden')
  })

  it('手機: 點導覽連結後關閉抽屜, 焦點回到選單按鈕', async () => {
    mockViewport(true)
    renderLayout()

    const menuButton = await screen.findByRole('button', { name: '開啟選單' })
    fireEvent.click(menuButton)
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('link', { name: '自選股' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(menuButton).toHaveAttribute('aria-expanded', 'false')
    expect(menuButton).toHaveFocus()
    expect(document.body.style.overflow).toBe('')
  })

  it('手機: Escape 與點遮罩都會關閉抽屜', async () => {
    mockViewport(true)
    renderLayout()

    const menuButton = await screen.findByRole('button', { name: '開啟選單' })
    fireEvent.click(menuButton)
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(menuButton).toHaveFocus()

    fireEvent.click(menuButton)
    const overlay = screen.getByRole('dialog').previousElementSibling as HTMLElement
    fireEvent.click(overlay)
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('桌面: 保留側欄, 沒有「開啟選單」按鈕', async () => {
    mockViewport(false)
    renderLayout()

    expect(await screen.findByRole('complementary')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '自選股' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '開啟選單' })).not.toBeInTheDocument()
  })

  it('桌面: 收起狀態寫入 localStorage, 重新載入後沿用', async () => {
    mockViewport(false)
    const { unmount } = renderLayout()

    fireEvent.click(await screen.findByRole('button', { name: '收起選單' }))
    expect(localStorage.getItem('tf-nav-collapsed')).toBe('1')
    expect(screen.queryByText('自選股')).not.toBeInTheDocument()
    unmount()

    renderLayout()
    expect(await screen.findByRole('button', { name: '展開選單' })).toBeInTheDocument()
    expect(screen.getByRole('complementary')).toBeInTheDocument()
  })
})

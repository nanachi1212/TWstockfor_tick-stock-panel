// Phase 8B-2.1 — Menu Settings Consistency 回歸測試
// 涵蓋：台股核心功能清單改由 @/lib/navigation.ts 產生後仍正常顯示、
// 中國 A 股功能預設關閉時個別項目不出現、開啟總開關後個別項目才出現、
// 操作 A 股區塊不影響台股核心項目。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { SettingsMenuSettingsPanel } from './MenuSettings'
import { api } from '@/lib/api'
import { usePreferences } from '@/lib/useSharedQueries'

vi.mock('@/lib/api', () => ({
  api: {
    analysisMenus: vi.fn().mockResolvedValue({ items: [] }),
    saveNavOrder: vi.fn().mockResolvedValue({ nav_order: [] }),
    saveNavHidden: vi.fn().mockResolvedValue({ nav_hidden: [] }),
    updateShowAshareLegacyFeatures: vi.fn().mockResolvedValue({ show_ashare_legacy_features: true }),
  },
}))

vi.mock('@/lib/useSharedQueries', () => ({
  usePreferences: vi.fn(),
}))

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <SettingsMenuSettingsPanel />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('MenuSettings — Taiwan-first consistency (Phase 8B-2.1)', () => {
  it('shows Taiwan core nav items and no individual A-share rows when the master toggle is off', async () => {
    vi.mocked(usePreferences).mockReturnValue({ data: { nav_order: [], nav_hidden: [], show_ashare_legacy_features: false } } as any)
    renderPanel()

    expect(await screen.findByText('台股選股')).toBeInTheDocument()
    expect(screen.getByText('多股比較')).toBeInTheDocument()
    expect(screen.getByText('顯示中國 A 股功能')).toBeInTheDocument()
    // 总开关关闭时, 个别 A 股项目不应出现 (不产生第二套不一致的显示)
    expect(screen.queryByText('連板梯隊')).not.toBeInTheDocument()
    expect(screen.queryByText('概念分析')).not.toBeInTheDocument()
    // Phase 8B-4.2.1: /backtest 與 /data 已移出 CORE_NAV, 总开关关闭时也不应出现
    expect(screen.queryByText('A 股回測')).not.toBeInTheDocument()
    expect(screen.queryByText('A 股資料管理')).not.toBeInTheDocument()
  })

  it('reveals individual A-share rows once the master toggle is on, without affecting Taiwan core items', async () => {
    vi.mocked(usePreferences).mockReturnValue({ data: { nav_order: [], nav_hidden: [], show_ashare_legacy_features: true } } as any)
    renderPanel()

    expect(await screen.findByText('連板梯隊')).toBeInTheDocument()
    expect(screen.getByText('概念分析')).toBeInTheDocument()
    expect(screen.getByText('行業分析')).toBeInTheDocument()
    // Phase 8B-4.2.1: 开启后 A 股回測 / A 股資料管理 应出现在 A 股区块中(与 sidebar 同步)
    expect(screen.getByText('A 股回測')).toBeInTheDocument()
    expect(screen.getByText('A 股資料管理')).toBeInTheDocument()
    // 台股核心项目不受影响
    expect(screen.getByText('台股選股')).toBeInTheDocument()
    expect(screen.getByText('自選股')).toBeInTheDocument()
  })

  it('toggling the master switch calls the same preference endpoint used by the sidebar', async () => {
    vi.mocked(usePreferences).mockReturnValue({ data: { nav_order: [], nav_hidden: [], show_ashare_legacy_features: false } } as any)
    renderPanel()

    await screen.findByText('顯示中國 A 股功能')
    fireEvent.click(screen.getByRole('button', { name: '顯示中國 A 股功能' }))

    await waitFor(() => expect(api.updateShowAshareLegacyFeatures).toHaveBeenCalledWith(true))
  })

  it('individually hiding an A-share item calls saveNavHidden (same mechanism as Taiwan core items)', async () => {
    vi.mocked(usePreferences).mockReturnValue({ data: { nav_order: [], nav_hidden: [], show_ashare_legacy_features: true } } as any)
    renderPanel()

    const row = await screen.findByText('連板梯隊')
    const hideBtn = row.closest('div')!.parentElement!.querySelector('button[title="隱藏"]')!
    fireEvent.click(hideBtn)

    await waitFor(() => expect(api.saveNavHidden).toHaveBeenCalledWith(['/limit-ladder']))
  })
})

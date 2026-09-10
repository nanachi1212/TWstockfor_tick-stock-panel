// Phase 8C-D — Legacy Product Removal: 「顯示中國 A 股功能」總開關與個別
// 策略選股/A 股回測/因子挖掘項目已隨產品介面正式移除, MenuSettings 只剩台股
// 核心導覽的拖曳排序/顯示管理。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { SettingsMenuSettingsPanel } from './MenuSettings'
import { api } from '@/lib/api'
import { usePreferences } from '@/lib/useSharedQueries'

vi.mock('@/lib/api', () => ({
  api: {
    saveNavOrder: vi.fn().mockResolvedValue({ nav_order: [] }),
    saveNavHidden: vi.fn().mockResolvedValue({ nav_hidden: [] }),
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
  it('shows exactly the 5 Taiwan core nav items', async () => {
    vi.mocked(usePreferences).mockReturnValue({ data: { nav_order: [], nav_hidden: [] } } as any)
    renderPanel()

    expect(await screen.findByText('台股選股')).toBeInTheDocument()
    expect(screen.getByText('多股比較')).toBeInTheDocument()
    expect(screen.getByText('自選股')).toBeInTheDocument()
    expect(screen.getByText('看板')).toBeInTheDocument()
    expect(screen.getByText('監控中心')).toBeInTheDocument()
  })

  it('individually hiding a Taiwan core item calls saveNavHidden', async () => {
    vi.mocked(usePreferences).mockReturnValue({ data: { nav_order: [], nav_hidden: [] } } as any)
    renderPanel()

    const row = await screen.findByText('台股選股')
    const hideBtn = row.closest('div')!.parentElement!.querySelector('button[title="隱藏"]')!
    fireEvent.click(hideBtn)

    await waitFor(() => expect(api.saveNavHidden).toHaveBeenCalledWith(['/taiwan-screener']))
  })
})

describe('MenuSettings — Legacy A-share toggle removal (Phase 8C-D)', () => {
  it('no longer renders the master "顯示中國 A 股功能" toggle or section', async () => {
    vi.mocked(usePreferences).mockReturnValue({ data: { nav_order: [], nav_hidden: [] } } as any)
    renderPanel()

    await screen.findByText('台股選股')
    expect(screen.queryByText('顯示中國 A 股功能')).not.toBeInTheDocument()
    expect(screen.queryByText('中國 A 股功能')).not.toBeInTheDocument()
  })

  it('no longer renders individual legacy items (策略選股/A 股回測/因子挖掘) in any state', async () => {
    vi.mocked(usePreferences).mockReturnValue({ data: { nav_order: [], nav_hidden: [] } } as any)
    renderPanel()

    await screen.findByText('台股選股')
    expect(screen.queryByText('策略選股')).not.toBeInTheDocument()
    expect(screen.queryByText('A 股回測')).not.toBeInTheDocument()
    expect(screen.queryByText('因子挖掘')).not.toBeInTheDocument()
  })
})

// DAILY_USE_CORE_UX_FIXES (P1-2) — TaiwanStockDetail 返回按鈕回歸測試。
// 通用 idx-based 判斷邏輯已在 useSafeBack.test.tsx 完整驗證; 這裡只驗證本頁
// 真的接上了它 —— 不再寫死「返回即時監控」文案/目的地, 且能在有可信 in-app
// 來源(如監控中心)時正確返回該來源, 直接網址進入時安全落到台股選股 fallback。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { TaiwanStockDetail } from './TaiwanStockDetail'

vi.mock('@/lib/api', () => ({
  api: {
    taiwanSearch: vi.fn().mockResolvedValue({ results: [] }),
    watchlistList: vi.fn().mockResolvedValue({ symbols: [] }),
    watchlistAdd: vi.fn(),
    watchlistRemove: vi.fn(),
    watchlistGroups: vi.fn().mockResolvedValue({ groups: [] }),
    taiwanStockDetail: vi.fn().mockResolvedValue({
      identity: { name: '台積電', exchange: 'TWSE' },
      realtime: { close: 100, change: 0, change_pct: 0 },
      daily_history: { rows: [] },
    }),
    taiwanCurrentData: vi.fn().mockResolvedValue(null),
    taiwanStockResearchContext: vi.fn().mockResolvedValue(null),
    taiwanStockAIResearch: vi.fn(),
  },
}))

function renderAt(entries: string[], initialIndex: number) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={entries} initialIndex={initialIndex}>
        <Routes>
          <Route path="/stocks/:symbol" element={<TaiwanStockDetail />} />
          <Route path="/monitor" element={<div>MONITOR PAGE</div>} />
          <Route path="/taiwan-screener" element={<div>SCREENER PAGE</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  window.history.replaceState(null, '')
})

afterEach(() => {
  vi.clearAllMocks()
  window.history.replaceState(null, '')
})

describe('TaiwanStockDetail — back navigation (DAILY_USE_CORE_UX_FIXES P1-2)', () => {
  it('never shows the old hardcoded "返回即時監控" label', async () => {
    renderAt(['/stocks/2330.TWSE'], 0)

    expect(await screen.findByText('返回')).toBeInTheDocument()
    expect(screen.queryByText('返回即時監控')).not.toBeInTheDocument()
  })

  it('D. returns to the known source (Monitor) when entered from there', async () => {
    window.history.replaceState({ idx: 1 }, '')
    renderAt(['/monitor', '/stocks/2330.TWSE'], 1)

    fireEvent.click(await screen.findByText('返回'))

    expect(await screen.findByText('MONITOR PAGE')).toBeInTheDocument()
  })

  it('E. direct URL falls back to 台股選股 instead of leaving the app', async () => {
    // window.history.state 為 null(beforeEach 已重置) 模擬直接輸入網址
    renderAt(['/stocks/2330.TWSE'], 0)

    fireEvent.click(await screen.findByText('返回'))

    expect(await screen.findByText('SCREENER PAGE')).toBeInTheDocument()
  })
})

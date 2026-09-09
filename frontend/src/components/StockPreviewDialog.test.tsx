// DAILY_USE_CORE_UX_FIXES (P1-3) — StockPreviewDialog「查看完整個股」出口
// 回歸測試。重的子元件(K線/分時圖/監控編輯器/擴充插槽)與本測試要驗證的
// 「有明確路徑離開空白預覽、進到完整個股頁」無關, 全部 mock 掉。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StockPreviewDialog } from './StockPreviewDialog'

vi.mock('@/lib/api', () => ({
  api: {
    watchlistList: vi.fn().mockResolvedValue({ symbols: [] }),
    monitorRulesList: vi.fn().mockResolvedValue({ rules: [] }),
    preferences: vi.fn().mockResolvedValue({}),
    quoteStatus: vi.fn().mockResolvedValue({ running: false }),
  },
}))

vi.mock('@/components/StockPanel', () => ({
  StockPanel: () => <div data-testid="stock-panel" />,
  getDefaultRange: () => ({ start: '2026-03-09', end: '2026-09-09' }),
}))
vi.mock('@/components/StockMultiDayIntradayChart', () => ({
  StockMultiDayIntradayChart: () => <div data-testid="intraday-chart" />,
}))
vi.mock('@/components/monitor/RuleEditor', () => ({ RuleEditor: () => null }))
vi.mock('@/components/stock-analysis/PriceAlertDialog', () => ({ PriceAlertDialog: () => null }))
vi.mock('@/extensions/ExtensionSlot', () => ({ ExtensionSlot: () => null }))
vi.mock('@/components/WatchlistAddMenu', () => ({ WatchlistAddMenu: () => null }))

function renderDialog(symbol = '2454.TWSE') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/watchlist']}>
        <Routes>
          <Route path="/watchlist" element={<div>WATCHLIST PAGE (dialog host)</div>} />
          <Route path="/stocks/:symbol" element={<div>STOCK DETAIL PAGE</div>} />
        </Routes>
        <StockPreviewDialog symbol={symbol} name="聯發科" onClose={() => {}} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('StockPreviewDialog — 完整個股入口 (DAILY_USE_CORE_UX_FIXES P1-3)', () => {
  it('E. 提供「查看完整個股」action, 導向正確的台股個股詳細頁 route', async () => {
    renderDialog('2454.TWSE')

    const link = await screen.findByRole('button', { name: /查看 2454\.TWSE 完整個股頁/ })
    fireEvent.click(link)

    expect(await screen.findByText('STOCK DETAIL PAGE')).toBeInTheDocument()
  })

  it('不對非台股代碼顯示「查看完整個股」(該 route 僅支援台股, 與既有「加入比較」同一限制)', async () => {
    renderDialog('600000.SH')

    // 給 watchlist/monitorRules 查詢一點時間解析, 確認並非還沒 render 完
    await screen.findByTitle('關閉')
    expect(screen.queryByRole('button', { name: /查看.*完整個股頁/ })).not.toBeInTheDocument()
  })
})

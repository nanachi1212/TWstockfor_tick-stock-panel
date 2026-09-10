// DAILY_USE_CORE_UX_FIXES (P1-3) — StockDailyKChart 空資料 empty state 回歸測試。
// 涵蓋 loading / error / 有效 rows 既有分支不受影響, 並新增 rows 為空陣列(HTTP
// 成功但本機無此標的日 K 資料)時必須顯示明確 empty state, 而不是沉默留白。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StockDailyKChart } from './StockDailyKChart'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: { klineDaily: vi.fn() },
}))

// EChartsCandlestick 本身牽涉 ECharts canvas 渲染, 與本檔案要驗證的「rows 為
// 空陣列時要顯示明確 empty state」無關, mock 掉只驗證有沒有被呼叫到。
vi.mock('@/components/EChartsCandlestick', () => ({
  EChartsCandlestick: () => <div data-testid="candlestick-chart" />,
  OVERLAY_INDICATORS: [],
  SUB_CHARTS: [],
}))

function renderChart(symbol = '2454.TWSE') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <StockDailyKChart symbol={symbol} />
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('StockDailyKChart — empty/loading/error states (DAILY_USE_CORE_UX_FIXES P1-3)', () => {
  it('A. rows = [] (HTTP 成功但本機無此標的資料) shows a clear empty state, not silence', async () => {
    vi.mocked(api.klineDaily).mockResolvedValue({ symbol: '2454.TWSE', rows: [] } as any)
    renderChart()

    expect(await screen.findByText('目前沒有可顯示的日 K 資料')).toBeInTheDocument()
    expect(screen.queryByTestId('candlestick-chart')).not.toBeInTheDocument()
    // 不可誤報成「載入失敗」——那是給真正的 HTTP/查詢錯誤用的
    expect(screen.queryByText('日K載入失敗')).not.toBeInTheDocument()
  })

  it('B. loading state is preserved', () => {
    vi.mocked(api.klineDaily).mockReturnValue(new Promise(() => {})) // never resolves
    renderChart()

    expect(screen.getByText('載入中…')).toBeInTheDocument()
    expect(screen.queryByText('目前沒有可顯示的日 K 資料')).not.toBeInTheDocument()
  })

  it('C. error state is preserved', async () => {
    vi.mocked(api.klineDaily).mockRejectedValue(new Error('network error'))
    renderChart()

    expect(await screen.findByText('日K載入失敗')).toBeInTheDocument()
    expect(screen.queryByText('目前沒有可顯示的日 K 資料')).not.toBeInTheDocument()
  })

  it('D. valid rows still render the chart', async () => {
    vi.mocked(api.klineDaily).mockResolvedValue({
      symbol: '2330.TWSE',
      rows: [{ date: '2026-09-08', open: 100, high: 105, low: 99, close: 103, volume: 1000 }],
    } as any)
    renderChart('2330.TWSE')

    expect(await screen.findByTestId('candlestick-chart')).toBeInTheDocument()
    expect(screen.queryByText('目前沒有可顯示的日 K 資料')).not.toBeInTheDocument()
  })

  it('malformed rows (present but all fail parsing) still show the distinct format-error message', async () => {
    vi.mocked(api.klineDaily).mockResolvedValue({
      symbol: '2330.TWSE',
      rows: [{ date: null, open: null, close: null }],
    } as any)
    renderChart('2330.TWSE')

    expect(await screen.findByText('資料格式異常，請重新整理頁面')).toBeInTheDocument()
    expect(screen.queryByText('目前沒有可顯示的日 K 資料')).not.toBeInTheDocument()
  })
})

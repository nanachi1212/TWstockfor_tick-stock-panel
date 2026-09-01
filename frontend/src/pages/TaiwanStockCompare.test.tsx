// Phase 7H — TaiwanStockCompare 頁面行為測試
// 涵蓋：URL 還原/去重、新增移除同步 URL、重新整理保留選取、AI 絕不自動觸發、
// 過期 AI 回應防護 (race condition guard)。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { TaiwanStockCompare } from './TaiwanStockCompare'
import { api } from '@/lib/api'
import { loadLastCompareSymbols, mergeSymbolIntoCompare } from '@/lib/taiwanCompareSymbols'

// createMemoryRouter/RouterProvider (data router) constructs internal fetch
// Request/AbortSignal objects that are incompatible with jsdom's AbortSignal
// realm in this Node/undici version — use the plain declarative router
// (<MemoryRouter>/<Routes>) instead, which this app doesn't otherwise need
// data-router features (loaders/actions) for. A tiny sibling component
// exposes the current location.search via a test id so URL-sync assertions
// don't need the data router's `router.state`.
function LocationSpy() {
  const location = useLocation()
  return <div data-testid="location-search">{location.search}</div>
}

vi.mock('@/lib/api', () => ({
  api: {
    taiwanSearch: vi.fn(),
    taiwanStockCompare: vi.fn(),
    taiwanStockCompareAIResearch: vi.fn(),
  },
}))

function buildContext(symbol: string, name: string, opts: Partial<any> = {}) {
  return {
    symbol,
    identity: { name, instrument_type: opts.instrument_type ?? 'stock' },
    price_context: { close: opts.close ?? 100, return_5d: opts.return_5d ?? 0.01, return_20d: 0.02 },
    technical_context: { rsi14: 55 },
    institutional_context: { foreign_net_1d: opts.foreign_net_1d ?? 0 },
    fundamentals_context: { status: opts.fundamentals_status ?? 'available', pe: 18 },
    etf_context: { leverage_multiplier: opts.leverage_multiplier ?? null },
  }
}

function buildComparisonResponse(symbols: string[]) {
  return {
    symbols_requested: symbols,
    comparison_date: '2026-08-28',
    generated_at: '2026-08-28T16:00:00+08:00',
    instruments: symbols.map(s => ({ symbol: s, context: buildContext(s, s), diagnostic_item: { signal_count: 0 } })),
    unsupported_symbols: [],
  }
}

function renderAt(initialPath: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialPath]}>
        <LocationSpy />
        <Routes>
          <Route path="/stocks/compare" element={<TaiwanStockCompare />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function currentSearch(): string {
  return screen.getByTestId('location-search').textContent || ''
}

async function waitForComparisonLoaded() {
  await waitFor(() => expect(screen.getByText(/比較基準日/)).toBeInTheDocument())
}

beforeEach(() => {
  vi.mocked(api.taiwanStockCompare).mockResolvedValue(buildComparisonResponse(['2330.TWSE', '2881.TWSE']) as any)
  vi.mocked(api.taiwanSearch).mockResolvedValue({ results: [] } as any)
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('URL restore', () => {
  it('restores 2 symbols from the URL on mount', async () => {
    renderAt('/stocks/compare?symbols=2330.TWSE,2881.TWSE')
    await waitFor(() => expect(screen.getByText('2330.TWSE')).toBeInTheDocument())
    expect(screen.getByText('2881.TWSE')).toBeInTheDocument()
  })

  it('restores 5 symbols from the URL on mount', async () => {
    const five = ['A.TWSE', 'B.TWSE', 'C.TWSE', 'D.TWSE', 'E.TWSE']
    vi.mocked(api.taiwanStockCompare).mockResolvedValue(buildComparisonResponse(five) as any)
    renderAt(`/stocks/compare?symbols=${five.join(',')}`)
    // 用 aria-label 而非文字比對，避免與比較表格內同名文字重複命中
    for (const sym of five) {
      await waitFor(() => expect(screen.getByLabelText(`移除 ${sym}`)).toBeInTheDocument())
    }
  })

  it('dedupes duplicate symbols in the URL', async () => {
    renderAt('/stocks/compare?symbols=2330.TWSE,2330.TWSE,2881.TWSE')
    await waitFor(() => expect(screen.getAllByText('2330.TWSE').length).toBeGreaterThan(0))
    // exactly one chip for 2330.TWSE (table also renders it, so count chip occurrences via aria-label instead)
    expect(screen.getByLabelText('移除 2330.TWSE')).toBeInTheDocument()
    expect(screen.queryAllByLabelText('移除 2330.TWSE').length).toBe(1)
  })

  it('does not crash on an invalid/unsupported symbol in the URL', async () => {
    vi.mocked(api.taiwanStockCompare).mockResolvedValue({
      ...buildComparisonResponse(['2330.TWSE']),
      instruments: buildComparisonResponse(['2330.TWSE']).instruments,
      unsupported_symbols: ['NOT_REAL'],
    } as any)
    renderAt('/stocks/compare?symbols=2330.TWSE,NOT_REAL')
    await waitFor(() => expect(screen.getByText(/無法解析之代碼/)).toBeInTheDocument())
    // NOT_REAL 同時出現在已選 chip 與不支援代碼提示中，用 getAllByText 避免重複命中報錯
    expect(screen.getAllByText(/NOT_REAL/).length).toBeGreaterThan(0)
  })
})

describe('selection edits sync to URL', () => {
  it('adding a symbol updates the URL', async () => {
    vi.mocked(api.taiwanSearch).mockResolvedValue({
      results: [{ symbol: '8069.TPEX', name: '元太', exchange: 'TPEX' }],
    } as any)
    renderAt('/stocks/compare?symbols=2330.TWSE,2881.TWSE')
    await waitFor(() => expect(screen.getByText('2330.TWSE')).toBeInTheDocument())

    fireEvent.change(screen.getByPlaceholderText('搜尋台股代號或名稱以加入比較...'), {
      target: { value: '元太' },
    })
    const addBtn = await screen.findByText('元太')
    fireEvent.click(addBtn)

    await waitFor(() => expect(currentSearch()).toContain('8069.TPEX'))
    expect(currentSearch()).toContain('2330.TWSE')
  })

  it('removing a symbol updates the URL', async () => {
    renderAt('/stocks/compare?symbols=2330.TWSE,2881.TWSE')
    await waitFor(() => expect(screen.getByText('2330.TWSE')).toBeInTheDocument())

    fireEvent.click(screen.getByLabelText('移除 2881.TWSE'))

    await waitFor(() => expect(currentSearch()).not.toContain('2881.TWSE'))
    expect(currentSearch()).toContain('2330.TWSE')
  })
})

describe('refresh persistence', () => {
  it('re-mounting at the same URL reproduces the same selection (simulated refresh)', async () => {
    renderAt('/stocks/compare?symbols=2330.TWSE,2881.TWSE')
    await waitFor(() => expect(screen.getByText('2330.TWSE')).toBeInTheDocument())

    // simulate a hard refresh by rendering a brand-new instance at the same URL
    renderAt('/stocks/compare?symbols=2330.TWSE,2881.TWSE')
    await waitFor(() => expect(screen.getAllByText('2330.TWSE').length).toBeGreaterThan(0))
  })

  it('mirrors the URL-derived selection to localStorage on mount, not just on explicit edits', async () => {
    // 情境：使用者透過分享連結直接開啟比較頁 (未點擊任何新增/移除按鈕)，之後從
    // 另一個個股頁點「加入比較」時，仍必須讀到這次透過連結帶入的既有選取。
    renderAt('/stocks/compare?symbols=2330.TWSE,2881.TWSE')
    await waitFor(() => expect(screen.getByText('2330.TWSE')).toBeInTheDocument())

    await waitFor(() => expect(loadLastCompareSymbols()).toEqual(['2330.TWSE', '2881.TWSE']))

    // 模拟「加入比較」的合併邏輯 (TaiwanStockDetail.tsx 僅呼叫這兩個純函式)
    const merged = mergeSymbolIntoCompare(loadLastCompareSymbols(), '8069.TPEX')
    expect(merged).toEqual(['2330.TWSE', '2881.TWSE', '8069.TPEX'])
  })

  it('clearing the comparison selection clears localStorage too, so a later 加入比較 does not resurrect it (Phase 7H.1)', async () => {
    // 1. store A+B
    renderAt('/stocks/compare?symbols=2330.TWSE,2881.TWSE')
    await waitFor(() => expect(loadLastCompareSymbols()).toEqual(['2330.TWSE', '2881.TWSE']))

    // 2. clear the comparison selection (remove both chips)
    fireEvent.click(screen.getByLabelText('移除 2330.TWSE'))
    await waitFor(() => expect(currentSearch()).not.toContain('2330.TWSE'))
    fireEvent.click(screen.getByLabelText('移除 2881.TWSE'))
    await waitFor(() => expect(currentSearch()).not.toContain('2881.TWSE'))

    // 3. persisted comparison symbols become empty
    await waitFor(() => expect(loadLastCompareSymbols()).toEqual([]))
    expect(localStorage.getItem('tw_compare:last_symbols')).toBeNull()

    // 4. later 加入比較 from a stock detail page does not resurrect A+B
    const merged = mergeSymbolIntoCompare(loadLastCompareSymbols(), '8069.TPEX')
    expect(merged).toEqual(['8069.TPEX']) // NOT ['2330.TWSE', '2881.TWSE', '8069.TPEX']
  })
})

describe('AI never auto-triggers', () => {
  it('does not call taiwanStockCompareAIResearch on mount or after deterministic data loads', async () => {
    renderAt('/stocks/compare?symbols=2330.TWSE,2881.TWSE')
    await waitFor(() => expect(api.taiwanStockCompare).toHaveBeenCalled())
    // give any accidental effects a tick to fire
    await new Promise(r => setTimeout(r, 20))
    expect(api.taiwanStockCompareAIResearch).not.toHaveBeenCalled()
  })
})

describe('stale AI response race guard', () => {
  it('discards a late AI response that no longer matches the current selection', async () => {
    let resolveAi: (v: any) => void
    const deferred = new Promise(resolve => {
      resolveAi = resolve
    })
    vi.mocked(api.taiwanStockCompareAIResearch).mockReturnValue(deferred as any)

    // 3 個標的起始，之後只移除其中 1 個，讓比較仍維持 >= 2 檔 (canCompare 仍為 true)，
    // 以驗證 AI 卡片在選取變更後「保留可見、回到未生成狀態」而非整個消失。
    vi.mocked(api.taiwanStockCompare).mockResolvedValue(
      buildComparisonResponse(['2330.TWSE', '2881.TWSE', '8069.TPEX']) as any,
    )
    renderAt('/stocks/compare?symbols=2330.TWSE,2881.TWSE,8069.TPEX')
    await waitForComparisonLoaded()

    // 1. start AI generation for [2330.TWSE, 2881.TWSE, 8069.TPEX]
    fireEvent.click(screen.getByText('生成 AI 客觀比較'))
    await waitFor(() => expect(api.taiwanStockCompareAIResearch).toHaveBeenCalledWith(
      ['2330.TWSE', '2881.TWSE', '8069.TPEX'],
    ))
    expect(screen.getByText('正在客觀比較中...')).toBeInTheDocument()

    // 2. change selection (remove one symbol, 2 remain) while the request is still in flight
    fireEvent.click(screen.getByLabelText('移除 8069.TPEX'))

    // AI loading state must reset immediately even though the old request hasn't resolved
    await waitFor(() => expect(screen.queryByText('正在客觀比較中...')).not.toBeInTheDocument())
    // the deterministic query re-fetches for the new 2-symbol selection; wait for it, then
    // confirm the AI card is back to its "not yet generated" placeholder, not the stale report
    await waitForComparisonLoaded()
    await waitFor(() => expect(screen.getByText('尚未生成 AI 客觀比較')).toBeInTheDocument())

    // 3. the stale [2330.TWSE, 2881.TWSE, 8069.TPEX] response now resolves
    resolveAi!({
      status: 'success',
      report: {
        symbols: ['2330.TWSE', '2881.TWSE', '8069.TPEX'],
        comparison_overview: 'STALE RESPONSE — must never be shown',
        key_observations: [],
        risk_factors: [],
        missing_information: [],
        disclaimer: '',
      },
      prompt_version: 'v1',
      generated_at: '2026-08-28T16:00:00+08:00',
      evidence_registry_keys: [],
    })

    // give the discarded promise's .then a tick to (not) apply
    await new Promise(r => setTimeout(r, 20))
    expect(screen.queryByText('STALE RESPONSE — must never be shown')).not.toBeInTheDocument()
    expect(screen.getByText('尚未生成 AI 客觀比較')).toBeInTheDocument()
  })
})

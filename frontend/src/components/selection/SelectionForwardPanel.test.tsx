import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SelectionForwardPanel, type SelectionForwardBatchReviewView, type SelectionForwardPreviewView } from './SelectionForwardPanel'

// Explicit test fixture; production data comes from the backend contract.
const previewFixture: SelectionForwardPreviewView = {
  dataDate: '2026-09-25',
  ruleVersion: 'trend_liquidity_v1',
  rankingBasis: '5 日動能由高至低，同分依成交金額排序',
  status: 'available',
  targetTradeDate: '2026-09-28',
  targetTradeDateStatus: 'confirmed',
  lockAllowed: true,
  candidates: Array.from({ length: 22 }, (_, index) => ({
    symbol: `${2330 + index}.TWSE`,
    name: `測試標的 ${index + 1}`,
    referencePrice: index === 0 ? 1000 : null,
    knownRisks: index === 0 ? ['高波動'] : [],
    missingData: index === 1 ? ['法人資料'] : [],
  })),
  knownRisks: ['產業集中'],
  missingData: ['部分標的缺少法人資料'],
}

const batchReviewFixture: SelectionForwardBatchReviewView = {
  statistics: {
    '1D': { referenceCloseReturnPct: -1.1, referenceCloseEvaluableCount: 8, paperReturnPct: 1.25, benchmark0050ReturnPct: 0.75, benchmarkEvaluableCount: 8, excessReturnPct: 0.5, excessEvaluableCount: 8, evaluableCount: 8, trackingCount: 2, missingCount: 1 },
    '5D': { referenceCloseReturnPct: null, referenceCloseEvaluableCount: 0, paperReturnPct: null, benchmark0050ReturnPct: null, benchmarkEvaluableCount: 0, excessReturnPct: null, excessEvaluableCount: 0, evaluableCount: 0, trackingCount: 8, missingCount: 3 },
    '20D': { referenceCloseReturnPct: -0.4, referenceCloseEvaluableCount: 2, paperReturnPct: 0, benchmark0050ReturnPct: 0.1, benchmarkEvaluableCount: 2, excessReturnPct: -0.6, excessEvaluableCount: 2, evaluableCount: 2, trackingCount: 4, missingCount: 5 },
  },
  items: [{
    symbol: '2330.TWSE',
    name: '測試標的 台積電',
    referencePrice: 1000,
    paperEntryPrice: 1010,
    paperEntryPriceStatus: 'completed',
    referenceCloseReturns: {
      '1D': { status: 'completed', returnPct: -1.1 },
      '5D': { status: 'tracking', returnPct: null },
      '20D': { status: 'missing', returnPct: null, reason: 'missing_horizon_close' },
    },
    paperEntryReturns: {
      '1D': { status: 'completed', returnPct: 1.1 },
      '5D': { status: 'tracking', returnPct: null },
      '20D': { status: 'completed', returnPct: -0.5 },
    },
    benchmarkReturns: {
      '1D': { status: 'completed', returnPct: 0.75 },
      '5D': { status: 'tracking', returnPct: null },
      '20D': { status: 'missing', returnPct: null },
    },
    excessReturns: {
      '1D': { status: 'completed', returnPct: 0.35 },
      '5D': { status: 'tracking', returnPct: null },
      '20D': { status: 'missing', returnPct: null },
    },
  }],
  metadata: { lockedAt: '2026-09-25T08:00:00+08:00', targetTradeDate: '2026-09-28', targetTradeDateStatus: 'scheduled_unverified', ruleVersion: 'trend_liquidity_v1', selectedCount: 20, priceAdjustment: 'split_adjusted_price', costAssumption: '未扣成本與滑價；紙上開盤價不保證成交' },
}

describe('SelectionForwardPanel', () => {
  it('shows the first 10 fixture rows then expands to at most 20', () => {
    render(<SelectionForwardPanel preview={previewFixture} onDryRun={vi.fn()} onLockOfficialBatch={vi.fn()} />)

    expect(screen.getByText('資料日期').parentElement).toHaveTextContent('2026-09-25')
    expect(screen.getByText('規則版本').parentElement).toHaveTextContent('trend_liquidity_v1')
    expect(screen.getByText('排名依據').parentElement).toHaveTextContent('5 日動能由高至低，同分依成交金額排序')
    expect(screen.getByText('候選 Top 10')).toBeInTheDocument()
    expect(screen.getByText('測試標的 10')).toBeInTheDocument()
    expect(screen.queryByText('測試標的 11')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '展開至 Top20' }))
    expect(screen.getByText('候選 Top 20')).toBeInTheDocument()
    expect(screen.getByText('測試標的 20')).toBeInTheDocument()
    expect(screen.queryByText('測試標的 21')).not.toBeInTheDocument()
    expect(screen.getAllByText('資料不足').some(element => element.tagName === 'TD')).toBe(true)
    expect(screen.getByText('產業集中')).toBeInTheDocument()
  })

  it('does not infer a lock window from browser time and marks unverified scheduled dates', () => {
    render(<SelectionForwardPanel preview={{ ...previewFixture, targetTradeDateStatus: 'scheduled_unverified' }} onDryRun={vi.fn()} onLockOfficialBatch={vi.fn()} />)

    expect(screen.getByText('預定進場日').parentElement).toHaveTextContent('未確認')
    expect(screen.getByRole('button', { name: '鎖定正式測試名單' })).toBeEnabled()
    expect(screen.getByText(/鎖定時點與進場日期以後端核驗為準/)).toBeInTheDocument()
  })

  it('renders backend-supplied horizons, 0050 and paper-entry results without turning null into zero', () => {
    render(<SelectionForwardPanel preview={previewFixture} batchReview={batchReviewFixture} />)

    expect(screen.getByText('+1.25%')).toBeInTheDocument()
    expect(screen.getAllByText('+0.75%')).toHaveLength(1)
    expect(screen.getByText(/\+0\.50%/)).toBeInTheDocument()
    expect(screen.getByText('+0.35%')).toBeInTheDocument()
    expect(screen.getByText('0.00%')).toBeInTheDocument()
    expect(screen.getByText('-0.50%')).toBeInTheDocument()
    expect(screen.getAllByText(/-1\.10%/)).toHaveLength(2)
    expect(screen.getByText(/鎖定時間/)).toBeInTheDocument()
    expect(screen.getByText(/未扣成本與滑價/)).toBeInTheDocument()
    expect(screen.getAllByText('可評估筆數').map(label => label.nextElementSibling?.textContent)).toContain('8')
    expect(screen.getAllByText('追蹤中', { selector: 'td' }).length).toBeGreaterThan(0)
    expect(screen.getAllByText('不可評估', { selector: 'td' }).length).toBeGreaterThan(0)
    expect(screen.getAllByText('1000')).toHaveLength(2)
    expect(screen.getAllByText('尚無可評估結果').length).toBeGreaterThan(0)
  })

  it('adds visible candidates to the watchlist and compares the top five', () => {
    const add = vi.fn()
    const compare = vi.fn()
    const first = previewFixture.candidates[0].symbol
    render(<SelectionForwardPanel preview={previewFixture} watchlistSymbols={new Set([first])} onAddToWatchlist={add} onCompare={compare} />)

    expect(screen.getAllByText('已在自選')).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: /全部加入自選（9）/ }))
    expect(add).toHaveBeenCalledWith(previewFixture.candidates.slice(1, 10).map(item => item.symbol))
    fireEvent.click(screen.getAllByRole('button', { name: '加自選' })[0])
    expect(add).toHaveBeenLastCalledWith([previewFixture.candidates[1].symbol])
    fireEvent.click(screen.getByRole('button', { name: /比較前 5 檔/ }))
    expect(compare).toHaveBeenCalledWith(previewFixture.candidates.slice(0, 5).map(item => item.symbol))
  })

  it('hides row actions when the host page provides none', () => {
    render(<SelectionForwardPanel preview={previewFixture} />)
    expect(screen.queryByRole('button', { name: '加自選' })).not.toBeInTheDocument()
  })

  it('does not show fabricated candidates and allows an explicit dry-run request before a preview', () => {
    render(<SelectionForwardPanel onDryRun={vi.fn()} />)

    expect(screen.getByText('尚未載入候選資料')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '乾跑預覽' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '鎖定正式測試名單' })).toBeDisabled()
  })

  it('distinguishes a genuine zero-candidate preview from unavailable screening data', () => {
    render(<SelectionForwardPanel preview={{ ...previewFixture, candidates: [], status: 'available' }} onDryRun={vi.fn()} onLockOfficialBatch={vi.fn()} />)
    expect(screen.getByText('目前沒有可展示的候選標的，資料尚未準備完成。')).toBeInTheDocument()
    expect(screen.getByText('資料日期').parentElement).toHaveTextContent('2026-09-25')
    expect(screen.queryByText('目前無法提供正式候選資料。')).not.toBeInTheDocument()
  })

  it('states that complete data can still yield zero candidates', () => {
    render(<SelectionForwardPanel preview={{ ...previewFixture, candidates: [], strategyReadiness: 'ready' }} onDryRun={vi.fn()} />)
    expect(screen.getByText('資料完整，但本期沒有符合條件標的。')).toBeInTheDocument()
    expect(screen.queryByText('目前沒有可展示的候選標的，資料尚未準備完成。')).not.toBeInTheDocument()
  })
})

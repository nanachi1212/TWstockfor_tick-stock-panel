import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SelectionForwardPanel, type SelectionForwardBatchReviewView, type SelectionForwardPreviewView } from './SelectionForwardPanel'

// Layout-only fixture. Production remains empty until the backend contract is integrated.
const previewFixture: SelectionForwardPreviewView = {
  dataDate: '2026-09-25',
  ruleVersion: 'trend-liquidity-v1',
  rankingBasis: '趨勢強度與成交金額',
  status: 'pre_open',
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
    '1D': { paperReturnPct: 1.25, benchmark0050ReturnPct: 0.75, excessReturnPct: 0.5, evaluableCount: 8, trackingCount: 2, missingCount: 1 },
    '5D': { paperReturnPct: null, benchmark0050ReturnPct: null, excessReturnPct: null, evaluableCount: 0, trackingCount: 8, missingCount: 3 },
    '20D': { paperReturnPct: -0.5, benchmark0050ReturnPct: 0.1, excessReturnPct: -0.6, evaluableCount: 2, trackingCount: 4, missingCount: 5 },
  },
  items: [{
    symbol: '2330.TWSE',
    name: '測試標的 台積電',
    referencePriceChangePct: 2.4,
    paperEntryReturns: {
      '1D': { status: 'completed', returnPct: 1.1 },
      '5D': { status: 'tracking', returnPct: null },
      '20D': { status: 'missing', returnPct: null },
    },
  }],
}

describe('SelectionForwardPanel', () => {
  it('shows the first 10 fixture rows then expands to at most 20', () => {
    render(<SelectionForwardPanel preview={previewFixture} onDryRun={vi.fn()} onLockOfficialBatch={vi.fn()} />)

    expect(screen.getByText('資料日期').parentElement).toHaveTextContent('2026-09-25')
    expect(screen.getByText('規則版本').parentElement).toHaveTextContent('trend-liquidity-v1')
    expect(screen.getByText('排名依據').parentElement).toHaveTextContent('趨勢強度與成交金額')
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

  it('disables official locking after market open and clearly marks the preview', () => {
    render(<SelectionForwardPanel preview={{ ...previewFixture, status: 'market_open' }} onDryRun={vi.fn()} onLockOfficialBatch={vi.fn()} />)

    expect(screen.getByRole('status')).toHaveTextContent('已開盤')
    expect(screen.getByRole('button', { name: '鎖定正式測試名單' })).toBeDisabled()
  })

  it('renders backend-supplied horizons, 0050 and paper-entry results without turning null into zero', () => {
    render(<SelectionForwardPanel preview={previewFixture} batchReview={batchReviewFixture} />)

    expect(screen.getByText('+1.25%')).toBeInTheDocument()
    expect(screen.getByText('+0.75%')).toBeInTheDocument()
    expect(screen.getByText('+0.50%')).toBeInTheDocument()
    expect(screen.getAllByText('可評估筆數').map(label => label.nextElementSibling?.textContent)).toContain('8')
    expect(screen.getByText('追蹤中', { selector: 'td' })).toBeInTheDocument()
    expect(screen.getByText('缺資料', { selector: 'td' })).toBeInTheDocument()
    expect(screen.getByText('+2.40%')).toBeInTheDocument()
    expect(screen.getAllByText('尚無可評估結果').length).toBeGreaterThan(0)
    expect(screen.queryByText('0.00%')).not.toBeInTheDocument()
  })

  it('does not show fabricated candidates or enable operations without an API result', () => {
    render(<SelectionForwardPanel />)

    expect(screen.getByText('等待核心 API 契約')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '乾跑預覽' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '鎖定正式測試名單' })).toBeDisabled()
  })
})

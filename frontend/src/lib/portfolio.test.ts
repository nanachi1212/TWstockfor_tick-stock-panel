import { describe, expect, it } from 'vitest'
import {
  buildPortfolioPositions,
  createPortfolioTransaction,
  type PortfolioTransaction,
} from './portfolio'

function transaction(side: 'buy' | 'sell', shares: number, price: number, fee = 0, date = '2026-09-24'): PortfolioTransaction {
  return { id: `${side}-${shares}-${price}-${fee}-${date}`, symbol: '2330.TWSE', name: '台積電', side, shares, price, fee, date, createdAt: `${date}T00:00:00.000Z` }
}

describe('portfolio accounting', () => {
  it('computes first and repeated buys with weighted average cost including fees', () => {
    const position = buildPortfolioPositions([
      transaction('buy', 10, 100, 10, '2026-09-22'),
      transaction('buy', 10, 120, 10, '2026-09-23'),
    ])[0]
    expect(position.shares).toBe(20)
    expect(position.costBasis).toBe(2220)
    expect(position.averageCost).toBe(111)
  })

  it('supports partial and complete sells and tracks realized profit by average cost', () => {
    const transactions = [
      transaction('buy', 10, 100),
      transaction('sell', 4, 130, 5, '2026-09-25'),
    ]
    const partial = buildPortfolioPositions(transactions)[0]
    expect(partial.shares).toBe(6)
    expect(partial.costBasis).toBe(600)
    expect(partial.realizedPnl).toBe(115)
    expect(buildPortfolioPositions([...transactions, transaction('sell', 6, 90, 0, '2026-09-26')])).toEqual([
      expect.objectContaining({ shares: 0, costBasis: 0, realizedPnl: 55 }),
    ])
  })

  it('rejects oversells, nonpositive prices, fractional shares, and invalid fees', () => {
    const buys = [transaction('buy', 2, 100)]
    expect(() => createPortfolioTransaction({ symbol: '2330.TWSE', side: 'sell', shares: 3, price: 101, date: '2026-09-24' }, buys)).toThrow('最多可賣出 2 股')
    expect(() => createPortfolioTransaction({ symbol: '2330.TWSE', side: 'buy', shares: 1.5, price: 101, date: '2026-09-24' }, buys)).toThrow('整數')
    expect(() => createPortfolioTransaction({ symbol: '2330.TWSE', side: 'buy', shares: 1, price: 0, date: '2026-09-24' }, buys)).toThrow('成交價')
    expect(() => createPortfolioTransaction({ symbol: '2330.TWSE', side: 'buy', shares: 1, price: 101, fee: -1, date: '2026-09-24' }, buys)).toThrow('手續費')
    expect(() => createPortfolioTransaction({ symbol: '2330.TWSE', side: 'sell', shares: 1, price: 101, date: '2026-09-23' }, buys)).toThrow('當時持有股數')
    expect(() => createPortfolioTransaction({ symbol: '2330.TWSE', side: 'buy', shares: 1, price: 101, date: '2026-02-31' }, buys)).toThrow('有效成交日期')
  })
})

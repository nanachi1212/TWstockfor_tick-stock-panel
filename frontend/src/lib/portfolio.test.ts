import { describe, expect, it } from 'vitest'
import {
  buildPortfolioPositions,
  createPortfolioTransaction,
  isSupportedPortfolioInstrument,
  type PortfolioTransaction,
} from './portfolio'

function transaction(side: 'buy' | 'sell', shares: number, price: number, fee = 0, date = '2026-09-24', tax = 0, tradeTime = side === 'buy' ? '09:00' : '10:00'): PortfolioTransaction {
  return { id: `${side}-${shares}-${price}-${fee}-${tax}-${date}-${tradeTime}`, symbol: '2330.TWSE', name: '台積電', side, shares, price, fee, tax, date, tradeTime, createdAt: `${date}T00:00:00.000Z` }
}

describe('portfolio accounting', () => {
  it('allows quick trade only for supported canonical Taiwan stocks and ETFs', () => {
    expect(isSupportedPortfolioInstrument({ symbol: '2330.TWSE', instrument_type: 'stock', is_supported: true })).toBe(true)
    expect(isSupportedPortfolioInstrument({ symbol: '0050.TWSE', instrument_type: 'etf', is_supported: true })).toBe(true)
    expect(isSupportedPortfolioInstrument({ symbol: 'BABA.SZ', instrument_type: 'stock', is_supported: true })).toBe(false)
    expect(isSupportedPortfolioInstrument({ symbol: 'TAIEX.TWSE', instrument_type: 'index', is_supported: false })).toBe(false)
  })

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
      transaction('sell', 4, 130, 5, '2026-09-24', 10),
    ]
    const partial = buildPortfolioPositions(transactions)[0]
    expect(partial.shares).toBe(6)
    expect(partial.costBasis).toBe(600)
    expect(partial.realizedPnl).toBe(105)
    expect(buildPortfolioPositions([...transactions, transaction('sell', 6, 90, 0, '2026-09-24', 2)])).toEqual([
      expect.objectContaining({ shares: 0, costBasis: 0, realizedPnl: 43 }),
    ])
  })

  it('uses execution time for same-day buy and sell accounting', () => {
    const opening = transaction('buy', 10, 100, 0, '2026-09-24', 0, '09:00')
    const sell = transaction('sell', 10, 110, 0, '2026-09-24', 0, '09:30')
    const laterBuy = transaction('buy', 10, 200, 0, '2026-09-24', 0, '10:00')

    expect(buildPortfolioPositions([opening, laterBuy, sell])[0]).toEqual(expect.objectContaining({
      shares: 10, averageCost: 200, realizedPnl: 100,
    }))
  })

  it('rejects ambiguous legacy same-day mixed trades without execution times', () => {
    const buy = { ...transaction('buy', 10, 100), tradeTime: undefined }
    const sell = { ...transaction('sell', 10, 110), tradeTime: undefined }
    expect(() => buildPortfolioPositions([buy, sell])).toThrow('同日買賣需填寫不同的成交時間')
  })

  it('rejects oversells, nonpositive prices, fractional shares, and invalid fees', () => {
    const buys = [transaction('buy', 2, 100)]
    expect(() => createPortfolioTransaction({ symbol: '2330.TWSE', side: 'sell', shares: 3, price: 101, date: '2026-09-24', tradeTime: '11:00' }, buys)).toThrow('最多可賣出 2 股')
    expect(() => createPortfolioTransaction({ symbol: '2330.TWSE', side: 'buy', shares: 1.5, price: 101, date: '2026-09-24', tradeTime: '11:00' }, buys)).toThrow('整數')
    expect(() => createPortfolioTransaction({ symbol: '2330.TWSE', side: 'buy', shares: 1, price: 0, date: '2026-09-24', tradeTime: '11:00' }, buys)).toThrow('成交價')
    expect(() => createPortfolioTransaction({ symbol: '2330.TWSE', side: 'buy', shares: 1, price: 101, fee: -1, date: '2026-09-24', tradeTime: '11:00' }, buys)).toThrow('手續費')
    expect(() => createPortfolioTransaction({ symbol: '2330.TWSE', side: 'sell', shares: 1, price: 101, date: '2026-09-23', tradeTime: '11:00' }, buys)).toThrow('當時持有股數')
    expect(() => createPortfolioTransaction({ symbol: '2330.TWSE', side: 'buy', shares: 1, price: 101, date: '2026-02-31', tradeTime: '11:00' }, buys)).toThrow('有效成交日期')
    expect(() => createPortfolioTransaction({ symbol: '2330.TWSE', side: 'buy', shares: 1, price: 101, date: '2099-01-01', tradeTime: '11:00' }, buys)).toThrow('晚於今日')
  })
})

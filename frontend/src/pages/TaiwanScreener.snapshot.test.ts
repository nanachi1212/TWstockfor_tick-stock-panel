import { describe, expect, it } from 'vitest'
import type { ScreenerResultItem } from '@/lib/api'
import { snapshotItems } from './TaiwanScreener'

const row = (symbol: string, close: number | null) => ({ symbol, name: symbol, close }) as ScreenerResultItem

describe('screener snapshot rows', () => {
  it('ranks across the whole result and never stores a missing price as 0', () => {
    const items = snapshotItems([row('2330.TWSE', 1000), row('2317.TWSE', null), row('2454.TWSE', 0), row('6488.TPEX', 500)], 'breakout_v1', 'v1')
    expect(items.map(item => [item.symbol, item.rank, item.price])).toEqual([
      ['2330.TWSE', 1, 1000],
      ['6488.TPEX', 4, 500],
    ])
    expect(items[0].strategy_conditions.preset).toBe('breakout_v1')
  })
})

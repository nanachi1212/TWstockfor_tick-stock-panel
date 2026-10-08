// 側欄導覽結構回歸測試: 核心流程 6 頁常駐, 其餘研究/進階頁收在「進階功能」群組。
import { describe, expect, it } from 'vitest'
import { ADVANCED_NAV, ALL_NAV, CORE_NAV, isAdvancedNavPath } from './navigation'

describe('navigation', () => {
  it('keeps the beginner core flow as the only always-visible items', () => {
    expect(CORE_NAV.map(item => item.to)).toEqual([
      '/',
      '/picks',
      '/taiwan-screener',
      '/watchlist',
      '/monitor',
      '/stocks/compare',
    ])
  })

  it('moves research pages into the advanced group without dropping any route', () => {
    const all = ALL_NAV.map(item => item.to)
    expect(new Set(all).size).toBe(all.length)
    expect(new Set(all)).toEqual(new Set([
      '/', '/picks', '/taiwan-screener', '/watchlist', '/monitor', '/stocks/compare',
      '/daily-brief', '/market-research', '/research', '/events', '/buy-points',
      '/selection-review', '/strategy-lab', '/social-sentiment', '/social-fetch',
      '/model', '/data-health',
    ]))
    expect(ADVANCED_NAV.map(item => item.to)).toContain('/data-health')
  })

  it('detects advanced paths including nested routes', () => {
    expect(isAdvancedNavPath('/data-health')).toBe(true)
    expect(isAdvancedNavPath('/research/abc')).toBe(true)
    expect(isAdvancedNavPath('/watchlist')).toBe(false)
    expect(isAdvancedNavPath('/')).toBe(false)
  })
})

// Phase 8C-A — CORE_NAV 順序回歸測試
//
// 防止未來意外改回「自選股先於台股選股」的舊順序。Phase 8C-0 產品審查發現
// 現有順序(看板/自選股/台股選股/多股比較/監控中心)與「看市場→找股票→
// 加自選→比較→監控」的核心使用流程不符, 本 Phase 只調整陣列順序(route path
// 不變), 這裡鎖定新順序、並確認 route path 集合本身完全未變。
import { describe, expect, it } from 'vitest'
import { CORE_NAV, ASHARE_LEGACY_NAV } from './navigation'

describe('CORE_NAV', () => {
  it('orders pages to match 看市場→找股票→加自選→比較→監控', () => {
    expect(CORE_NAV.map(item => item.to)).toEqual([
      '/',
      '/taiwan-screener',
      '/watchlist',
      '/stocks/compare',
      '/monitor',
    ])
  })

  it('keeps the same 5 route paths as before the reorder (no route added/removed)', () => {
    const paths = new Set(CORE_NAV.map(item => item.to))
    expect(paths).toEqual(new Set(['/', '/watchlist', '/taiwan-screener', '/stocks/compare', '/monitor']))
  })
})

describe('ASHARE_LEGACY_NAV', () => {
  it('is untouched by the Phase 8C-A core-nav reorder', () => {
    expect(ASHARE_LEGACY_NAV.map(item => item.to)).toEqual(['/screener', '/backtest', '/mining'])
  })
})

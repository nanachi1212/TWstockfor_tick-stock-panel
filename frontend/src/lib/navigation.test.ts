// Phase 8C-A — CORE_NAV 順序回歸測試
// Phase 8C-D — Legacy Product Removal: ASHARE_LEGACY_NAV 已隨 /screener,
// /backtest, /mining 產品介面正式移除, 不再匯出; CORE_NAV 是最終且唯一的
// 導覽陣列。
//
// 防止未來意外改回「自選股先於台股選股」的舊順序。Phase 8C-0 產品審查發現
// 現有順序(看板/自選股/台股選股/多股比較/監控中心)與「看市場→找股票→
// 加自選→比較→監控」的核心使用流程不符, 本 Phase 只調整陣列順序(route path
// 不變), 這裡鎖定新順序、並確認 route path 集合本身完全未變。
import { describe, expect, it } from 'vitest'
import * as navigationModule from './navigation'
import { CORE_NAV } from './navigation'

describe('CORE_NAV', () => {
  it('orders pages to match 看市場→每日摘要→事件中心→找股票→選股復盤→加自選→比較→監控', () => {
    expect(CORE_NAV.map(item => item.to)).toEqual([
      '/',
      '/daily-brief',
      '/events',
      '/taiwan-screener',
      '/selection-review',
      '/watchlist',
      '/stocks/compare',
      '/monitor',
    ])
  })

  it('keeps the 8 Taiwan core route paths including daily brief and selection review', () => {
    const paths = new Set(CORE_NAV.map(item => item.to))
    expect(paths).toEqual(
      new Set([
        '/',
        '/daily-brief',
        '/events',
        '/taiwan-screener',
        '/selection-review',
        '/watchlist',
        '/stocks/compare',
        '/monitor',
      ]),
    )
  })

  it('is the only exported nav array — CORE_NAV has exactly 8 Taiwan core items', () => {
    expect(CORE_NAV).toHaveLength(8)
  })
})

describe('ASHARE_LEGACY_NAV removal (Phase 8C-D)', () => {
  it('is no longer exported from navigation.ts', () => {
    expect('ASHARE_LEGACY_NAV' in navigationModule).toBe(false)
  })
})

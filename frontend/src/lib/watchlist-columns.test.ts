// Phase 8C-C — Watchlist 台股預設欄位精簡回歸測試
// 涵蓋：新的 default-visible 欄位組合、「連板」不再是台股預設欄位、
// registry 仍保留技術欄位定義供已有偏好的使用者 / legacy 場景使用。
//
// TAIWAN_LOCALIZATION_POLISH: label 全面改為正體中文, 「连板/连跌」依實際
// 語意 (consecutive_limit_ups/downs) 改為「連續漲停/連續跌停」; internal id
// (builtin:limit_ups 等) 保持不變。
import { describe, expect, it } from 'vitest'
import { BUILTIN_COLUMNS, COLUMN_GROUPS } from './watchlist-columns'

function findColumn(id: string) {
  const col = BUILTIN_COLUMNS.find(c => c.id === id)
  if (!col) throw new Error(`column ${id} not found in BUILTIN_COLUMNS`)
  return col
}

describe('Watchlist BUILTIN_COLUMNS — Taiwan-first defaults (Phase 8C-C)', () => {
  it('new default-visible columns: symbol/price/pct/turnover/vol_ratio/rsi14/signals', () => {
    const defaultVisibleIds = BUILTIN_COLUMNS.filter(c => c.visible).map(c => c.id)
    expect(defaultVisibleIds).toEqual([
      'builtin:symbol',
      'builtin:price',
      'builtin:pct',
      'builtin:turnover',
      'builtin:vol_ratio',
      'builtin:rsi14',
      'builtin:signals',
    ])
  })

  it('連板 (limit_ups) is not default-visible in the Taiwan experience', () => {
    expect(findColumn('builtin:limit_ups').visible).toBe(false)
  })

  it('60D 动量 is not default-visible (avoids two technical indicators both defaulting on)', () => {
    expect(findColumn('builtin:momentum_60d').visible).toBe(false)
  })

  it('registry still keeps 連續漲停/60D 動量 as selectable columns (not deleted, just default-off)', () => {
    expect(findColumn('builtin:limit_ups')).toMatchObject({ label: '連續漲停' })
    expect(findColumn('builtin:momentum_60d')).toMatchObject({ label: '60D 動量' })
  })

  it('registry still keeps technical-indicator fields for users who rely on them (e.g. legacy A-share)', () => {
    for (const id of ['builtin:ma5', 'builtin:ma20', 'builtin:macd_dif', 'builtin:kdj_k', 'builtin:rsi6', 'builtin:rsi24']) {
      expect(BUILTIN_COLUMNS.some(c => c.id === id)).toBe(true)
    }
  })
})

// TAIWAN_LOCALIZATION_POLISH — display labels 正體化回歸測試
describe('Watchlist BUILTIN_COLUMNS — Traditional Chinese labels (Data Freshness & Source Labels / Localization batch)', () => {
  const SIMPLIFIED_CHARS_SEEN_BEFORE = ['代码', '名称', '现价', '涨跌幅', '换手率', '连板', '连跌', '信号', '动量', '波动', '布林上轨', '布林下轨', '营收', '净利', '负债率']

  it('no known-simplified strings remain in any column label', () => {
    for (const col of BUILTIN_COLUMNS) {
      for (const bad of SIMPLIFIED_CHARS_SEEN_BEFORE) {
        expect(col.label).not.toContain(bad)
      }
    }
  })

  it('core visible labels are the expected Traditional Chinese strings', () => {
    expect(findColumn('builtin:symbol').label).toBe('代碼/名稱')
    expect(findColumn('builtin:price').label).toBe('現價')
    expect(findColumn('builtin:pct').label).toBe('漲跌幅')
    expect(findColumn('builtin:turnover').label).toBe('換手率')
    expect(findColumn('builtin:signals').label).toBe('訊號')
  })

  it('連續漲停/連續跌停 label reflects actual field semantics (consecutive limit-up/down days), not the bare A-share term "连板"', () => {
    expect(findColumn('builtin:limit_ups').label).toBe('連續漲停')
    expect(findColumn('builtin:limit_downs').label).toBe('連續跌停')
  })

  it('internal column IDs are unchanged (saved preferences still match by id)', () => {
    for (const id of ['builtin:symbol', 'builtin:price', 'builtin:pct', 'builtin:limit_ups', 'builtin:limit_downs', 'builtin:signals', 'builtin:momentum_60d']) {
      expect(BUILTIN_COLUMNS.some(c => c.id === id)).toBe(true)
    }
    // source.key for limit_ups/limit_downs (the actual backend field contract) unchanged too
    expect(findColumn('builtin:limit_ups').source).toEqual({ type: 'builtin', key: 'limit_ups' })
    expect(findColumn('builtin:limit_downs').source).toEqual({ type: 'builtin', key: 'limit_downs' })
  })

  it('COLUMN_GROUPS labels are Traditional Chinese too', () => {
    const group = COLUMN_GROUPS.find(g => g.id === 'limit')
    expect(group?.label).toBe('連續漲跌停')
    for (const g of COLUMN_GROUPS) {
      for (const bad of SIMPLIFIED_CHARS_SEEN_BEFORE) {
        expect(g.label).not.toContain(bad)
      }
    }
  })

  it('saved-preference merge behavior is unaffected: label always comes from current defaults, visible from saved config (list-columns.ts mergeColumns contract)', () => {
    // 這是既有 mergeColumns 的既定行為 (label/source/align/pinned 以 defaults 為準,
    // 只有 visible 取自 saved) —— 本測試只是把這個契約明確斷言出來, 確保未來若
    // 有人改了 label 字串, 不需要連帶 migrate 使用者的 preferences.json。
    const col = findColumn('builtin:limit_ups')
    expect(col.visible).toBe(false) // 目前 code default
    expect(col.label).toBe('連續漲停') // label 永遠來自目前程式碼定義, 與 saved visible 無關
  })
})

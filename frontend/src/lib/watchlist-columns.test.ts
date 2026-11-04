import { describe, expect, it } from 'vitest'
import { BUILTIN_COLUMNS, COLUMN_GROUPS } from './watchlist-columns'
import { mergeColumns } from './list-columns'

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

  it('removes mainland-only consecutive-limit columns and groups', () => {
    expect(BUILTIN_COLUMNS.some(c => ['builtin:limit_ups', 'builtin:limit_downs'].includes(c.id))).toBe(false)
    expect(COLUMN_GROUPS.some(g => g.id === 'limit')).toBe(false)
  })

  it('60D 動量 is not default-visible (avoids two technical indicators both defaulting on)', () => {
    expect(findColumn('builtin:momentum_60d').visible).toBe(false)
  })

  it('registry still keeps technical-indicator fields for users who rely on them', () => {
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

  it('internal column IDs are unchanged (saved preferences still match by id)', () => {
    for (const id of ['builtin:symbol', 'builtin:price', 'builtin:pct', 'builtin:signals', 'builtin:momentum_60d']) {
      expect(BUILTIN_COLUMNS.some(c => c.id === id)).toBe(true)
    }
  })

  it('COLUMN_GROUPS labels are Traditional Chinese too', () => {
    for (const g of COLUMN_GROUPS) {
      for (const bad of SIMPLIFIED_CHARS_SEEN_BEFORE) {
        expect(g.label).not.toContain(bad)
      }
    }
  })

  it('drops retired builtin preferences while preserving Taiwan and custom columns', () => {
    const columns = mergeColumns([
      { id: 'builtin:limit_ups', source: { type: 'builtin', key: 'limit_ups' }, label: '連板', visible: true },
      { ...findColumn('builtin:rsi14'), visible: false },
      { id: 'ext:custom:score', source: { type: 'ext', configId: 'custom', fieldName: 'score' }, label: '自訂分數', visible: true },
    ], BUILTIN_COLUMNS)
    expect(columns.some(c => c.id === 'builtin:limit_ups')).toBe(false)
    expect(columns.find(c => c.id === 'builtin:rsi14')?.visible).toBe(false)
    expect(columns.find(c => c.id === 'ext:custom:score')?.visible).toBe(true)
  })
})

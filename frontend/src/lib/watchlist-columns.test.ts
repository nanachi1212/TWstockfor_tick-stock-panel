// Phase 8C-C — Watchlist 台股預設欄位精簡回歸測試
// 涵蓋：新的 default-visible 欄位組合、「連板」不再是台股預設欄位、
// registry 仍保留技術欄位定義供已有偏好的使用者 / legacy 場景使用。
import { describe, expect, it } from 'vitest'
import { BUILTIN_COLUMNS } from './watchlist-columns'

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

  it('registry still keeps 連板/60D 动量 as selectable columns (not deleted, just default-off)', () => {
    expect(findColumn('builtin:limit_ups')).toMatchObject({ label: '连板' })
    expect(findColumn('builtin:momentum_60d')).toMatchObject({ label: '60D 动量' })
  })

  it('registry still keeps technical-indicator fields for users who rely on them (e.g. legacy A-share)', () => {
    for (const id of ['builtin:ma5', 'builtin:ma20', 'builtin:macd_dif', 'builtin:kdj_k', 'builtin:rsi6', 'builtin:rsi24']) {
      expect(BUILTIN_COLUMNS.some(c => c.id === id)).toBe(true)
    }
  })
})

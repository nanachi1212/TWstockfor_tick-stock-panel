// Phase 7H / 7I — 純邏輯 / localStorage 輔助函式測試
import { describe, expect, it } from 'vitest'
import {
  MAX_COMPARE_SYMBOLS,
  canonicalizeSymbols,
  loadLastCompareSymbols,
  mergeSymbolIntoCompare,
  parseCompareDate,
  saveLastCompareSymbols,
} from './taiwanCompareSymbols'

describe('canonicalizeSymbols', () => {
  it('uppercases, trims, and filters empty entries', () => {
    expect(canonicalizeSymbols([' 2330.twse ', '', '  ', '2881.twse'])).toEqual([
      '2330.TWSE',
      '2881.TWSE',
    ])
  })

  it('dedupes case-insensitively while preserving first-seen order', () => {
    expect(canonicalizeSymbols(['2330.TWSE', '2330.twse', '2881.TWSE', '2330.TWSE'])).toEqual([
      '2330.TWSE',
      '2881.TWSE',
    ])
  })

  it('caps at MAX_COMPARE_SYMBOLS distinct symbols', () => {
    const many = Array.from({ length: 8 }, (_, i) => `S${i}.TWSE`)
    const result = canonicalizeSymbols(many)
    expect(result.length).toBe(MAX_COMPARE_SYMBOLS)
    expect(result).toEqual(many.slice(0, MAX_COMPARE_SYMBOLS))
  })

  it('dedupes before capping so duplicates never waste a slot', () => {
    const withDupes = ['A.TWSE', 'A.TWSE', 'B.TWSE', 'C.TWSE', 'D.TWSE', 'E.TWSE', 'F.TWSE']
    const result = canonicalizeSymbols(withDupes)
    expect(result).toEqual(['A.TWSE', 'B.TWSE', 'C.TWSE', 'D.TWSE', 'E.TWSE'])
  })

  it('handles null/undefined entries safely', () => {
    expect(canonicalizeSymbols([null, undefined, 'A.TWSE'])).toEqual(['A.TWSE'])
  })
})

describe('mergeSymbolIntoCompare', () => {
  it('appends a new symbol to an existing list', () => {
    expect(mergeSymbolIntoCompare(['2330.TWSE'], '2881.TWSE')).toEqual([
      '2330.TWSE',
      '2881.TWSE',
    ])
  })

  it('is a no-op when the symbol is already present', () => {
    expect(mergeSymbolIntoCompare(['2330.TWSE', '2881.TWSE'], '2330.twse')).toEqual([
      '2330.TWSE',
      '2881.TWSE',
    ])
  })

  it('does not add a 6th symbol when already at the cap', () => {
    const atCap = ['A.TWSE', 'B.TWSE', 'C.TWSE', 'D.TWSE', 'E.TWSE']
    expect(mergeSymbolIntoCompare(atCap, 'F.TWSE')).toEqual(atCap)
  })
})

describe('loadLastCompareSymbols / saveLastCompareSymbols', () => {
  it('round-trips a saved symbol list', () => {
    saveLastCompareSymbols(['2330.TWSE', '2881.TWSE'])
    expect(loadLastCompareSymbols()).toEqual(['2330.TWSE', '2881.TWSE'])
  })

  it('canonicalizes on save (dedupe/cap/case)', () => {
    saveLastCompareSymbols(['a.twse', 'A.TWSE', 'b.twse'])
    expect(loadLastCompareSymbols()).toEqual(['A.TWSE', 'B.TWSE'])
  })

  it('returns [] when nothing has been saved', () => {
    expect(loadLastCompareSymbols()).toEqual([])
  })

  it('saving an empty list clears any previously stored key (Phase 7H.1)', () => {
    saveLastCompareSymbols(['2330.TWSE', '2881.TWSE'])
    expect(loadLastCompareSymbols()).toEqual(['2330.TWSE', '2881.TWSE'])

    saveLastCompareSymbols([])

    expect(localStorage.getItem('tw_compare:last_symbols')).toBeNull()
    expect(loadLastCompareSymbols()).toEqual([])
  })

  it('falls back to [] on corrupt JSON instead of throwing', () => {
    localStorage.setItem('tw_compare:last_symbols', '{not valid json')
    expect(() => loadLastCompareSymbols()).not.toThrow()
    expect(loadLastCompareSymbols()).toEqual([])
  })

  it('falls back to [] when stored value is not an array', () => {
    localStorage.setItem('tw_compare:last_symbols', JSON.stringify({ foo: 'bar' }))
    expect(loadLastCompareSymbols()).toEqual([])
  })

  it('save() never throws even if localStorage is unavailable', () => {
    const original = globalThis.localStorage
    // @ts-expect-error — simulate storage being unavailable (e.g. private browsing)
    delete globalThis.localStorage
    expect(() => saveLastCompareSymbols(['2330.TWSE'])).not.toThrow()
    globalThis.localStorage = original
  })
})

describe('parseCompareDate (Phase 7I)', () => {
  it('accepts a well-formed real calendar date', () => {
    expect(parseCompareDate('2026-08-28')).toBe('2026-08-28')
  })

  it('returns null for null/undefined/empty input', () => {
    expect(parseCompareDate(null)).toBeNull()
    expect(parseCompareDate(undefined)).toBeNull()
    expect(parseCompareDate('')).toBeNull()
  })

  it('returns null for malformed shape (not YYYY-MM-DD)', () => {
    expect(parseCompareDate('2026/08/28')).toBeNull()
    expect(parseCompareDate('26-08-28')).toBeNull()
    expect(parseCompareDate('2026-8-28')).toBeNull()
    expect(parseCompareDate('not-a-date')).toBeNull()
  })

  it('rejects a real-calendar-invalid month (2026-13-01)', () => {
    expect(parseCompareDate('2026-13-01')).toBeNull()
  })

  it('rejects a real-calendar-invalid day-of-month (2026-02-30)', () => {
    expect(parseCompareDate('2026-02-30')).toBeNull()
  })

  it('rejects a zero month (2026-00-10)', () => {
    expect(parseCompareDate('2026-00-10')).toBeNull()
  })

  it('rejects a zero day', () => {
    expect(parseCompareDate('2026-08-00')).toBeNull()
  })

  it('accepts a real leap-day date and rejects it on a non-leap year', () => {
    expect(parseCompareDate('2024-02-29')).toBe('2024-02-29') // 2024 is a leap year
    expect(parseCompareDate('2026-02-29')).toBeNull() // 2026 is not
  })
})

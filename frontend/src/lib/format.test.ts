import { describe, expect, it } from 'vitest'
import {
  fmtBigNum, fmtLots, fmtPct, fmtPctPoint, fmtPrice, fmtTaipeiDateTime, priceColorClass,
  taipeiDate, taipeiDateTime, taipeiTime,
} from './format'

describe('percent formatting', () => {
  it('fmtPct takes ratios and signs gains', () => {
    expect(fmtPct(0.0366)).toBe('+3.66%')
    expect(fmtPct(-0.0125)).toBe('-1.25%')
    expect(fmtPct(0)).toBe('0.00%')
    expect(fmtPct(0.5, 1, false)).toBe('50.0%')
    expect(fmtPct(null)).toBe('—')
    expect(fmtPct(Number.NaN)).toBe('—')
  })

  it('fmtPctPoint takes percent points', () => {
    expect(fmtPctPoint(3.66)).toBe('+3.66%')
    expect(fmtPctPoint(-1.2, 1)).toBe('-1.2%')
    expect(fmtPctPoint(55, 1, false)).toBe('55.0%')
    expect(fmtPctPoint(undefined)).toBe('—')
  })
})

describe('price, lots and amount formatting', () => {
  it('formats prices and treats missing as a dash', () => {
    expect(fmtPrice(12.345)).toBe('12.35')
    expect(fmtPrice(1050, 0)).toBe('1050')
    expect(fmtPrice(null)).toBe('—')
  })

  it('converts shares to whole lots', () => {
    expect(fmtLots(1_234_567)).toBe('1,235 張')
    expect(fmtLots(1_500, true)).toBe('+2 張')
    expect(fmtLots(-3_000, true)).toBe('-3 張')
    expect(fmtLots(2_000, false, false)).toBe('2')
    expect(fmtLots(null)).toBe('—')
  })

  it('shows odd lots under 1000 shares as shares, never as 0 or 1 lot', () => {
    expect(fmtLots(300)).toBe('300 股')
    expect(fmtLots(500)).toBe('500 股')
    expect(fmtLots(999, true)).toBe('+999 股')
    expect(fmtLots(-300, true)).toBe('-300 股')
    expect(fmtLots(0)).toBe('0 張')
    expect(fmtLots(1000)).toBe('1 張')
    expect(fmtLots(300, false, false)).toBe('0')
  })

  it('abbreviates large amounts with a single rule', () => {
    expect(fmtBigNum(2_500_000_000_000)).toBe('2.50兆')
    expect(fmtBigNum(123_456_789)).toBe('1.23億')
    expect(fmtBigNum(12_345)).toBe('1萬')
    expect(fmtBigNum(12_345, 2)).toBe('1.23萬')
    expect(fmtBigNum(999)).toBe('999')
    expect(fmtBigNum(undefined)).toBe('—')
  })

  it('colours flat and missing values neutrally', () => {
    expect(priceColorClass(1)).toBe('text-bull')
    expect(priceColorClass(-1)).toBe('text-bear')
    expect(priceColorClass(0)).toBe('text-muted')
    expect(priceColorClass(null)).toBe('text-muted')
  })
})

describe('Asia/Taipei time', () => {
  it('renders the same instant in Taipei regardless of the machine zone', () => {
    expect(taipeiDate('2026-09-09T17:00:00Z')).toBe('2026-09-10')
    expect(taipeiTime('2026-09-09T17:00:00Z')).toBe('01:00:00')
    expect(taipeiTime('2026-09-09T17:00:00Z', false)).toBe('01:00')
    expect(taipeiDateTime('2026-09-09T01:53:58Z', true)).toBe('2026-09-09 09:53:58')
  })

  it('returns null or the fallback instead of inventing a time', () => {
    expect(taipeiDate(null)).toBeNull()
    expect(taipeiDate(undefined)).toBeNull()
    expect(taipeiTime('not-a-date')).toBeNull()
    expect(fmtTaipeiDateTime('', '未知')).toBe('未知')
  })
})

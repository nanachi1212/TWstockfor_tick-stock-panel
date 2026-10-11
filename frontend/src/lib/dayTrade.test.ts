import { describe, expect, it } from 'vitest'
import { dayTradeFit } from './dayTrade'

describe('dayTradeFit', () => {
  const liquid = { close: 120, change_pct: -0.035, amount: 1_200_000_000, vol_ratio_5d: 2.1, risk_status: 'clear' as const }

  it('marks liquid, volatile stocks with expanding volume (up or down)', () => {
    expect(dayTradeFit(liquid).fit).toBe(true)
  })

  it('fails closed on missing data, unknown risk, or thin trading', () => {
    expect(dayTradeFit({ ...liquid, risk_status: 'unknown' }).fit).toBe(false)
    expect(dayTradeFit({ ...liquid, amount: null }).fit).toBe(false)
    expect(dayTradeFit({ ...liquid, amount: 300_000_000 }).fit).toBe(false)
    expect(dayTradeFit({ ...liquid, vol_ratio_5d: 1.2 }).fit).toBe(false)
    expect(dayTradeFit({ ...liquid, change_pct: 0.01 }).fit).toBe(false)
    expect(dayTradeFit({ ...liquid, close: 8 }).fit).toBe(false)
  })
})

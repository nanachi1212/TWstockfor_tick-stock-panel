import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { MinuteKlineRow } from '@/lib/api'
import { RADIX } from '@/lib/radix'
import { MiniIntraday } from './MiniIntraday'

const rows = [{ open: 100, close: 100 }, { open: 100, close: 100 }] as MinuteKlineRow[]
const lineColor = (changePct: number | null) => {
  const { container } = render(<MiniIntraday rows={rows} prevClose={100} changePct={changePct} />)
  return container.querySelectorAll('polyline')[1].getAttribute('stroke')
}

describe('MiniIntraday colour', () => {
  it('draws a flat day in the neutral colour, not the up colour', () => {
    expect(lineColor(0)).toBe(RADIX.neutral)
    expect(lineColor(null)).toBe(RADIX.neutral)
    expect(lineColor(0.01)).toBe(RADIX.danger)
    expect(lineColor(-0.01)).toBe(RADIX.success)
  })
})

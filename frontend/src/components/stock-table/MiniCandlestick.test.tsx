import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { DailyKlineMeta } from '@/lib/api'
import { MiniCandlestick } from './MiniCandlestick'

const meta: DailyKlineMeta = {
  source: 'taiwan_daily_store', price_semantics: 'raw', status: 'available', as_of: '2026-08-03',
}

describe('MiniCandlestick history metadata', () => {
  it('identifies raw prices and incomplete history on the active chart', () => {
    render(<MiniCandlestick rows={[{ date: '2026-08-03', open: 100, high: 103, low: 99, close: 102 }]} meta={{ ...meta, status: 'partial' }} />)
    expect(screen.getByRole('img', { name: '日 K（未復權原始價格），歷史資料不完整' })).toBeInTheDocument()
  })

  it('distinguishes confirmed missing history from a pending request', () => {
    const { rerender } = render(<MiniCandlestick rows={[]} />)
    expect(screen.getByRole('img', { name: '載入中' })).toBeInTheDocument()
    rerender(<MiniCandlestick rows={[]} meta={{ ...meta, status: 'unavailable', as_of: null }} />)
    expect(screen.getByRole('img', { name: '日 K 資料不足' })).toBeInTheDocument()
    expect(screen.getByText('無資料')).toBeInTheDocument()
  })
})

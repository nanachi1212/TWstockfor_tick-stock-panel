import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'
import { QK } from './queryKeys'
import { boardTag } from '@/components/stock-table/primitives'

afterEach(() => { vi.unstubAllGlobals() })

describe('Taiwan market contract', () => {
  it('classifies canonical Taiwan symbols without mainland numeric-prefix rules', () => {
    expect(boardTag('3010.TWSE')?.label).toBe('上市')
    expect(boardTag('6881.TPEX')?.label).toBe('上櫃')
    expect(boardTag('0050.TWSE')?.label).toBe('上市')
    for (const symbol of ['300001.SZ', '688001.SH', '830001.BJ', '2330']) {
      expect(boardTag(symbol)).toBeNull()
    }
  })

  it('always searches Taiwan instruments and isolates the Taiwan cache', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ results: [] }) })
    vi.stubGlobal('fetch', fetchMock)
    await api.instrumentSearch('台積電', 12, 'stock,etf,index')
    const url = new URL(fetchMock.mock.calls[0][0], 'http://localhost')
    expect(url.pathname).toBe('/api/kline/instruments/search')
    expect(url.searchParams.get('market')).toBe('taiwan')
    expect(url.searchParams.get('q')).toBe('台積電')
    expect(url.searchParams.get('asset_types')).toBe('stock,etf,index')
    expect(QK.instrumentSearch('2330', 'stock')).toEqual(['instrument-search', '2330', 'stock', 'taiwan'])
  })
})

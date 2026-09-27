import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'

describe('selection forward API contract', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('uses the canonical screener preset for dry-run preview', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) })
    vi.stubGlobal('fetch', fetchMock)

    await api.taiwanScreenerRun({ preset: 'trend_liquidity_v1', page: 1, page_size: 20 })

    expect(fetchMock).toHaveBeenCalledWith('/api/taiwan/screener/run', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ preset: 'trend_liquidity_v1', page: 1, page_size: 20 }),
    }))
  })

  it('locks via an explicit no-body POST and reads only formal forward batch endpoints', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) })
    vi.stubGlobal('fetch', fetchMock)

    await api.selectionReview.lockForwardBatch()
    await api.selectionReview.listForwardBatches()
    await api.selectionReview.getForwardBatchDetail('batch /1')
    await api.selectionReview.getForwardBatchStats()

    expect(fetchMock.mock.calls.map(call => call[0])).toEqual([
      '/api/taiwan/selection-review/forward-batches',
      '/api/taiwan/selection-review/forward-batches',
      '/api/taiwan/selection-review/forward-batches/batch%20%2F1',
      '/api/taiwan/selection-review/forward-batches/stats',
    ])
    expect(fetchMock.mock.calls[0][1]).toEqual(expect.objectContaining({ method: 'POST' }))
    expect(fetchMock.mock.calls[0][1].body).toBeUndefined()
  })
})

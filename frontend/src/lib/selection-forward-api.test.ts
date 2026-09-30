import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'

describe('selection forward API contract', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('serializes Strategy Lab filters and drilldown pagination through the shared client', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) })
    vi.stubGlobal('fetch', fetchMock)
    const filters = { minimum_sample: 10, source: 'A13 Buy Point', strategy_key: 'identity / 1', exchange: 'TPEX' }
    await api.strategyLab(filters)
    await api.strategyLabObservations(filters, 50)
    expect(fetchMock.mock.calls.map(call => call[0])).toEqual([
      '/api/taiwan/strategy-lab?minimum_sample=10&source=A13+Buy+Point&strategy_key=identity+%2F+1&exchange=TPEX',
      '/api/taiwan/strategy-lab/observations?minimum_sample=10&source=A13+Buy+Point&strategy_key=identity+%2F+1&exchange=TPEX&offset=50',
    ])
  })

  it('uses the canonical screener preset for dry-run preview', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) })
    vi.stubGlobal('fetch', fetchMock)

    await api.taiwanScreenerRun({ preset: 'trend_liquidity_v1' })

    expect(fetchMock).toHaveBeenCalledWith('/api/taiwan/screener/run', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ preset: 'trend_liquidity_v1' }),
    }))
  })

  it('locks with the explicit default strategy identity and reads formal forward endpoints', async () => {
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
    expect(fetchMock.mock.calls[0][1].body).toBe(JSON.stringify({ strategy_id: 'trend_liquidity_v1' }))
  })

  it('preserves the selected non-default strategy when locking and reading statistics', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) })
    vi.stubGlobal('fetch', fetchMock)
    await api.selectionReview.lockForwardBatch('breakout_v1')
    await api.selectionReview.getForwardBatchStats('breakout_v1')
    expect(fetchMock.mock.calls[0][1].body).toBe(JSON.stringify({ strategy_id: 'breakout_v1' }))
    expect(fetchMock.mock.calls[1][0]).toBe('/api/taiwan/selection-review/forward-batches/stats?strategy_id=breakout_v1')
  })
})

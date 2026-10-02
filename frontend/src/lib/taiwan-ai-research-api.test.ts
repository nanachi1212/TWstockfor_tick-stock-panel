import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'

describe('Taiwan AI research route contract', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('keeps research as the backward-compatible default purpose', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) })
    vi.stubGlobal('fetch', fetchMock)

    await api.taiwanStockAIResearch('2330.TWSE', undefined, { watchlist: { included: true } })

    expect(fetchMock).toHaveBeenCalledWith('/api/taiwan/stocks/2330.TWSE/ai-research', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ date: null, purpose: 'research', personal_context: { watchlist: { included: true } } }),
    }))
  })

  it('sends advice controls without accepting client-supplied plans or prices', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) })
    vi.stubGlobal('fetch', fetchMock)

    await api.taiwanStockAIResearch('2330.TWSE', '2026-10-01', undefined, {
      purpose: 'advice', review: true, strategyId: 'pullback / v1', refresh: true,
    })

    const body = JSON.parse(fetchMock.mock.calls[0][1].body)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/taiwan/stocks/2330.TWSE/ai-research')
    expect(body).toEqual({
      date: '2026-10-01', purpose: 'advice', review: true, strategy_id: 'pullback / v1', refresh: true,
    })
    expect(body).not.toHaveProperty('selected_trade_plan')
    expect(body).not.toHaveProperty('price')
  })
})

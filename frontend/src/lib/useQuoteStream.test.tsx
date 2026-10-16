import { renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { useQuoteStream } from './useQuoteStream'

class FakeEventSource {
  static last: FakeEventSource
  listeners: Record<string, () => void> = {}
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  constructor() { FakeEventSource.last = this }
  addEventListener(name: string, fn: () => void) { this.listeners[name] = fn }
  close() {}
}

afterEach(() => { vi.unstubAllGlobals() })

it('daily_data_updated invalidates portfolio analytics but not unrelated queries', () => {
  vi.stubGlobal('EventSource', FakeEventSource)
  const qc = new QueryClient()
  qc.setQueryData(['portfolio-analytics', '2330.TWSE:1000'], {})
  qc.setQueryData(['settings'], {})
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  renderHook(() => useQuoteStream(true, undefined), { wrapper })

  FakeEventSource.last.listeners.daily_data_updated()

  expect(qc.getQueryState(['portfolio-analytics', '2330.TWSE:1000'])?.isInvalidated).toBe(true)
  expect(qc.getQueryState(['settings'])?.isInvalidated).toBe(false)
})

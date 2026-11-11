import { readFileSync } from 'node:fs'
import { describe, expect, it, vi } from 'vitest'

const source = readFileSync('public/sw.js', 'utf8')

function loadWorker(fetchImpl: (request: unknown) => Promise<unknown>) {
  const store = new Map<string, unknown>()
  const cache = {
    keys: async () => [...store.keys()].map(url => ({ url })),
    delete: async (key: { url: string }) => store.delete(key.url),
    put: async (key: string | { url: string }, value: unknown) => {
      const url = typeof key === 'string' ? `https://app.test${key}` : key.url
      store.delete(url)
      store.set(url, value)
    },
  }
  const caches = { open: async () => cache, match: async () => undefined }
  const self = { addEventListener: () => {}, location: { origin: 'https://app.test' } }
  const worker = new Function('self', 'caches', 'fetch', `${source}; return { trim, navigate }`)(self, caches, fetchImpl) as {
    trim: (c: typeof cache) => Promise<void>
    navigate: (event: { request: unknown; waitUntil: (p: Promise<unknown>) => void }) => Promise<unknown>
  }
  return { worker, cache, store }
}

describe('service worker cache', () => {
  it('drops the oldest assets past the limit but keeps the offline shell', async () => {
    const { worker, cache, store } = loadWorker(async () => new Response(''))
    store.set('https://app.test/index.html', 1)
    for (let i = 0; i < 260; i += 1) store.set(`https://app.test/assets/chunk-${i}.js`, 1)
    await worker.trim(cache)
    expect(store.has('https://app.test/index.html')).toBe(true)
    expect(store.has('https://app.test/assets/chunk-0.js')).toBe(false)
    expect(store.has('https://app.test/assets/chunk-259.js')).toBe(true)
    expect(store.size).toBeLessThanOrEqual(201)
  })

  it('stores the navigation response as the shell without a second request', async () => {
    const fetchImpl = vi.fn(async () => new Response('<div id="root"></div>'))
    const { worker, store } = loadWorker(fetchImpl)
    const pending: Promise<unknown>[] = []
    await worker.navigate({ request: 'https://app.test/stocks/2330.TWSE', waitUntil: p => { pending.push(p) } })
    await Promise.all(pending)
    expect(fetchImpl).toHaveBeenCalledTimes(1)
    expect(store.has('https://app.test/index.html')).toBe(true)
  })

  it('does not overwrite the shell with a page that is not the app', async () => {
    const { worker, store } = loadWorker(async () => new Response('<html>Swagger UI</html>'))
    const pending: Promise<unknown>[] = []
    await worker.navigate({ request: 'https://app.test/docs', waitUntil: p => { pending.push(p) } })
    await Promise.all(pending)
    expect(store.size).toBe(0)
  })
})

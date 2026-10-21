const CACHE = 'twstock-static-v1'
const SHELL = '/index.html'
const PRECACHE = [
  SHELL,
  '/manifest.webmanifest',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/icon-maskable-512.png',
]

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE)
      .then(cache => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', event => {
  const { request } = event
  if (request.method !== 'GET') return
  const url = new URL(request.url)
  // API 與健康檢查一律走網路且不快取: 行情與狀態不能用舊資料冒充最新。
  if (url.origin !== self.location.origin || url.pathname.startsWith('/api/') || url.pathname === '/health') return

  if (url.pathname.startsWith('/assets/')) {
    event.respondWith(cacheFirst(request))
  } else if (request.mode === 'navigate') {
    event.respondWith(navigate(event))
  } else {
    event.respondWith(networkFirst(request))
  }
})

async function cacheFirst(request) {
  return (await caches.match(request)) ?? fetchAndCache(request)
}

async function networkFirst(request) {
  try {
    return await fetchAndCache(request)
  } catch (error) {
    const cached = await caches.match(request)
    if (cached) return cached
    throw error
  }
}

async function navigate(event) {
  try {
    const response = await fetch(event.request)
    // sw.js 內容不變就不會重跑 install; 每次連線導航刷新離線殼, 離線時才拿得到引用現行 assets 檔名的 index.html。
    event.waitUntil(caches.open(CACHE).then(cache => cache.add(SHELL)))
    return response
  } catch {
    return (await caches.match(SHELL)) ?? Response.error()
  }
}

async function fetchAndCache(request) {
  const response = await fetch(request)
  if (response.ok) {
    const cache = await caches.open(CACHE)
    await cache.put(request, response.clone())
  }
  return response
}

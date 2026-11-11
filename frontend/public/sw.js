const CACHE = 'twstock-static-v1'
const SHELL = '/index.html'
const MAX_ENTRIES = 200
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
    // sw.js 內容不變就不會重跑 install; 每次連線導航重新整理離線殼, 離線時才拿得到引用現行 assets 檔名的 index.html。
    // 導航回應本身就是殼 (SPA fallback), 直接存這份, 不再多抓一次 index.html。
    event.waitUntil(refreshShell(response.clone()).catch(() => {}))
    return response
  } catch {
    return (await caches.match(SHELL)) ?? Response.error()
  }
}

async function refreshShell(response) {
  if (!response.ok) return
  const html = await response.text()
  // /docs 之類不是 SPA 殼的頁面不能蓋掉離線殼。
  if (!html.includes('id="root"')) return
  const cache = await caches.open(CACHE)
  await cache.put(SHELL, new Response(html, { headers: { 'content-type': 'text/html; charset=utf-8' } }))
}

async function fetchAndCache(request) {
  const response = await fetch(request)
  if (response.ok) {
    // 快取寫入只是離線備援; 空間滿或被拒時仍回傳已取得的回應。
    try {
      const cache = await caches.open(CACHE)
      await cache.put(request, response.clone())
      await trim(cache)
    } catch {}
  }
  return response
}

// 一次建置約 70 個檔; 舊版帶 hash 的 assets 不會再被請求, 超過上限就從最舊的刪起 (keys() 依寫入順序)。
// ponytail: 以寫入順序近似「最舊」, 跨版本沒變的檔可能被刪後重抓一次; 要精確就改成比對 index.html 引用清單。
async function trim(cache) {
  const keys = await cache.keys()
  for (const key of keys.slice(0, Math.max(0, keys.length - MAX_ENTRIES))) {
    if (!PRECACHE.includes(new URL(key.url).pathname)) await cache.delete(key)
  }
}

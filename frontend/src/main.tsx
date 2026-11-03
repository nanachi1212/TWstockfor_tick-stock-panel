import React from 'react'
import ReactDOM from 'react-dom/client'
import { RouterProvider } from 'react-router-dom'
import { QueryClient, QueryClientProvider, QueryCache } from '@tanstack/react-query'
import { initializeFrontendExtensions } from './extensions/bootstrap'
import './index.css'
import { applyFontScale } from './lib/theme'

applyFontScale()

// 前端重新 build 後，已開著的視窗仍引用舊的 chunk 檔名；動態載入失敗時重新載入一次取得新版。
// sessionStorage 旗標避免新版本身壞掉時無限重新整理。
window.addEventListener('vite:preloadError', event => {
  try {
    if (sessionStorage.getItem('tf-chunk-reload') === '1') return
    sessionStorage.setItem('tf-chunk-reload', '1')
  } catch { /* storage blocked: still try one reload */ }
  event.preventDefault()
  window.location.reload()
})
window.addEventListener('load', () => {
  try { sessionStorage.removeItem('tf-chunk-reload') } catch { /* ignore */ }
})

if (import.meta.env.PROD && window.isSecureContext && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(err => console.warn('Service Worker 註冊失敗', err))
  })
}

// 全局認證攔截: 任何 query/mutation 收到 401 (未登錄/會話過期) → 跳登錄頁。
// api.ts 的 request() 已對 401 靜默 (不彈 toast), 這裡統一負責跳轉。
// 排除 /login 自身的請求, 避免登錄頁請求失敗又跳登錄形成死循環。
const _redirectToLogin = (() => {
  let redirecting = false
  return (err: unknown) => {
    if (redirecting) return
    if (!(err instanceof Error)) return
    const msg = err.message || ''
    // 401 (未登錄/會話過期) → 跳登錄頁
    // 403 未初始化 (面板未設密碼, 公網訪問) → 也跳登錄頁(顯示設密碼提示)
    const is401 = msg.includes('未登录') || msg.includes('会话已过期') || msg.includes('401')
    const isNotInit = msg.includes('尚未初始化访问密码') || msg.includes('NOT_INITIALIZED')
    if (!is401 && !isNotInit) return
    // 已在登錄頁則不跳(避免死循環)
    if (window.location.pathname === '/login') return
    redirecting = true
    const redirect = encodeURIComponent(window.location.pathname + window.location.search)
    window.location.href = `/login?redirect=${redirect}`
  }
})()

const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: (err) => _redirectToLogin(err),
  }),
  defaultOptions: {
    queries: {
      staleTime: 5_000,           // 5s 內複用,與 §4.2 Repository 不變量一致
      refetchOnWindowFocus: false,
    },
    mutations: {
      onError: (err) => _redirectToLogin(err),
    },
  },
})

async function bootstrap() {
  await initializeFrontendExtensions()
  const { router } = await import('./router')
  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </React.StrictMode>,
  )
}

void bootstrap()

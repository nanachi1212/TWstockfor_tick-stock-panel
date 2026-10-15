// Vitest 全域測試設定 (Phase 7H 新增，供 taiwanCompareSymbols / useCompareSymbols 測試使用)
import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { cleanup, configure } from '@testing-library/react'

// findBy*/waitFor default to 1s; heavy pages (Dashboard) take ~0.8s alone and time out when the full suite loads the CPU.
// Only failing waits get slower; passing ones return as soon as the element appears.
configure({ asyncUtilTimeout: 5000 })

afterEach(() => {
  cleanup()
  localStorage.clear()
})

// jsdom 未实现 matchMedia (Phase 8B-2 新增，供 Layout.tsx 的响应式/主题相关逻辑测试使用)
if (typeof window !== 'undefined' && !window.matchMedia) {
  window.matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }) as unknown as MediaQueryList
}

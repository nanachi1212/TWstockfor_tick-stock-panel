// Vitest 全域測試設定 (Phase 7H 新增，供 taiwanCompareSymbols / useCompareSymbols 測試使用)
import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

afterEach(() => {
  cleanup()
  localStorage.clear()
})

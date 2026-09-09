// DAILY_USE_CORE_UX_FIXES (P1-2) — useSafeBack 回歸測試。
//
// 測試技巧: MemoryRouter 完全不碰真實 window.history(它自己維護一份記憶體
// stack), 所以 idx 判斷需要對「真實」window.history.state 直接讀寫來模擬
// 「有/無可信站內來源」, navigate(-1) 的實際落點則由 MemoryRouter 自己的
// initialEntries/initialIndex 決定 —— 兩者互不干擾, 剛好對應真實瀏覽器下
// BrowserRouter 用 window.history、component 邏輯用 react-router navigate
// 的分工。
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { useSafeBack } from './useSafeBack'

function Harness({ fallbackPath }: { fallbackPath: string }) {
  const goBack = useSafeBack(fallbackPath)
  return <button onClick={goBack}>返回</button>
}

function renderAt(entries: string[], initialIndex: number, fallbackPath = '/fallback') {
  return render(
    <MemoryRouter initialEntries={entries} initialIndex={initialIndex}>
      <Routes>
        <Route path="/source" element={<div>SOURCE PAGE</div>} />
        <Route path="/fallback" element={<div>FALLBACK PAGE</div>} />
        <Route path="/current" element={<Harness fallbackPath={fallbackPath} />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  window.history.replaceState(null, '')
})

afterEach(() => {
  window.history.replaceState(null, '')
})

describe('useSafeBack', () => {
  it('navigates back (history back) when the tab has a trustworthy in-app history entry (idx > 0)', () => {
    window.history.replaceState({ idx: 1 }, '')
    renderAt(['/source', '/current'], 1)

    fireEvent.click(screen.getByText('返回'))

    expect(screen.getByText('SOURCE PAGE')).toBeInTheDocument()
    expect(screen.queryByText('FALLBACK PAGE')).not.toBeInTheDocument()
  })

  it('falls back to the given path when there is no trustworthy in-app history (idx missing, e.g. direct URL / new tab)', () => {
    // window.history.state 保持 beforeEach 設的 null -> 沒有 idx
    renderAt(['/current'], 0)

    fireEvent.click(screen.getByText('返回'))

    expect(screen.getByText('FALLBACK PAGE')).toBeInTheDocument()
    expect(screen.queryByText('SOURCE PAGE')).not.toBeInTheDocument()
  })

  it('falls back to the given path when idx is exactly 0 (first entry in the tab)', () => {
    window.history.replaceState({ idx: 0 }, '')
    renderAt(['/current'], 0)

    fireEvent.click(screen.getByText('返回'))

    expect(screen.getByText('FALLBACK PAGE')).toBeInTheDocument()
  })

  it('uses the caller-provided fallback path, not a hardcoded one', () => {
    renderAt(['/current'], 0, '/source')

    fireEvent.click(screen.getByText('返回'))

    // fallbackPath 這裡故意傳 '/source', 驗證真的是呼叫端決定, 不是寫死
    expect(screen.getByText('SOURCE PAGE')).toBeInTheDocument()
  })
})

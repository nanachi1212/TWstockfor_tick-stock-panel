import { afterEach, describe, expect, it } from 'vitest'
import { storage } from './storage'
import { initialWatchlistView } from './watchlist-view'

const originalMatchMedia = window.matchMedia
const mockViewport = (compact: boolean) => {
  window.matchMedia = ((query: string) => ({
    ...originalMatchMedia(query),
    matches: compact && query === '(max-width: 767px)',
  })) as typeof window.matchMedia
}

afterEach(() => {
  window.matchMedia = originalMatchMedia
})

describe('initialWatchlistView', () => {
  it('手機寬度且從未切換: 預設卡片檢視', () => {
    mockViewport(true)
    expect(initialWatchlistView()).toBe('card')
  })

  it('桌面寬度且從未切換: 維持表格檢視', () => {
    mockViewport(false)
    expect(initialWatchlistView()).toBe('table')
  })

  it('手動切換過的檢視優先於視窗寬度', () => {
    storage.watchlistView.set('table')
    mockViewport(true)
    expect(initialWatchlistView()).toBe('table')

    storage.watchlistView.set('card')
    mockViewport(false)
    expect(initialWatchlistView()).toBe('card')
  })

  it('儲存值無法辨識時依視窗寬度決定', () => {
    storage.watchlistView.set('grid')
    mockViewport(true)
    expect(initialWatchlistView()).toBe('card')
  })
})

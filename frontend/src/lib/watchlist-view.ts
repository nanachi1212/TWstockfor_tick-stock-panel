import { storage } from '@/lib/storage'

export type WatchlistView = 'table' | 'card'

// 與 Layout 切換頂列/抽屜的斷點相同
const COMPACT_QUERY = '(max-width: 767px)'

/** 使用者手動切換過就沿用；從未切換時，窄螢幕預設卡片（表格至少 900px 寬），桌面預設表格。 */
export function initialWatchlistView(): WatchlistView {
  const stored = storage.watchlistView.get('')
  if (stored === 'table' || stored === 'card') return stored
  return window.matchMedia(COMPACT_QUERY).matches ? 'card' : 'table'
}

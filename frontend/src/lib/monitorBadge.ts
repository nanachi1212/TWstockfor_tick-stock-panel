import { useSyncExternalStore } from 'react'

/**
 * 監控中心未讀觸發記錄徽標 — 全局 store + localStorage 持久化。
 *
 * 核心邏輯:
 *   - 在監控中心頁面時, 每次收到新推送都同步更新 lastSeen (看到=已讀)
 *   - 離開監控中心後, 新推送才計入未讀
 *   - 刷新頁面從 localStorage 恢復 lastSeen, 未讀 = 期間新增
 */

const STORAGE_KEY = 'monitor_last_seen_total'

let currentTotal = 0
let lastSeenTotal = readSeen()
let onMonitorPage = false    // 當前是否在監控中心頁面
let pendingSeen = false      // Monitor mount 請求 markSeen, 等 currentTotal 就緒
const listeners = new Set<() => void>()

function readSeen(): number {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (v === null) return -1  // 從未設置過 → 未初始化
    return parseInt(v, 10) || 0
  } catch {
    return -1
  }
}

function writeSeen(v: number) {
  try { localStorage.setItem(STORAGE_KEY, String(v)) } catch { /* ignore */ }
}

function syncSeen() {
  if (lastSeenTotal !== currentTotal) {
    lastSeenTotal = currentTotal
    writeSeen(currentTotal)
  }
}

function emit() {
  listeners.forEach(fn => fn())
}

function subscribe(fn: () => void) {
  listeners.add(fn)
  return () => { listeners.delete(fn) }
}

function getSnapshot() {
  return Math.max(0, currentTotal - Math.max(0, lastSeenTotal))
}

/** 輪詢更新最新總數 (Layout 層調用)。 */
export function setCurrentTotal(total: number): void {
  if (total < 0) return

  // 首次初始化: lastSeen < 0 (從未設置) → 把已讀基線設為當前總數
  // 否則 lastSeen=0 + total=1 會被誤算成"1條未讀" (首次進入就顯示徽標的 bug)
  if (lastSeenTotal < 0) {
    lastSeenTotal = total
    writeSeen(total)
  }
  // 總數減少 (清空) → 同步重置
  if (total < lastSeenTotal) {
    lastSeenTotal = total
    writeSeen(total)
  }

  const changed = total !== currentTotal
  currentTotal = total

  // 消費 pending markSeen
  if (pendingSeen) {
    pendingSeen = false
    syncSeen()
  }
  // ★ 在監控中心頁面期間: 收到新推送立即同步 (看到=已讀, 不計入未讀)
  else if (onMonitorPage && changed) {
    syncSeen()
  }

  emit()
}

/** 進入監控頁時調用。 */
export function markSeen(): void {
  onMonitorPage = true
  if (currentTotal > 0) {
    syncSeen()
    emit()
  } else {
    pendingSeen = true
  }
}

/** 離開監控頁時調用 (停止同步, 之後新增才計入未讀)。 */
export function leaveMonitorPage(): void {
  onMonitorPage = false
  pendingSeen = false
  // 不在此處 syncSeen — lastSeen 保持頁面期間最後一次同步的值即可
  // (避免 currentTotal 此刻還沒刷新到最新, 寫入偏小的值)
}

/** 記錄被清空時調用。 */
export function resetBadge(): void {
  currentTotal = 0
  lastSeenTotal = 0
  pendingSeen = false
  writeSeen(0)
  emit()
}

/** 讀取當前未讀數。 */
export function useUnreadAlerts(): number {
  return useSyncExternalStore(subscribe, getSnapshot, () => 0)
}

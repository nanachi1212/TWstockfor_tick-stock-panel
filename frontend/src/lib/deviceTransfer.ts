/**
 * 備份與轉移 — 前端部分。
 *
 * Portfolio 成交紀錄與介面偏好存在 localStorage, 後端讀不到;
 * 匯出時把「允許清單內」的 key 交給後端打包, 還原時由後端回傳再寫回。
 * 允許清單以後端 /api/device-transfer/options 為準, 前端不另維護一份。
 */
import { api, type DeviceTransferOptions, type RestoreResult } from '@/lib/api'

/** key → 原值 (null = 原本不存在) */
export type BrowserSnapshot = Record<string, string | null>

function keysOf(options: DeviceTransferOptions, categories: string[]): string[] {
  return options.categories.filter(c => categories.includes(c.id)).flatMap(c => c.browser_keys)
}

export function collectBrowserStorage(options: DeviceTransferOptions, categories: string[]) {
  const out: Record<string, Record<string, string>> = {}
  for (const cat of options.categories) {
    if (!categories.includes(cat.id) || cat.browser_keys.length === 0) continue
    const values: Record<string, string> = {}
    for (const key of cat.browser_keys) {
      const v = localStorage.getItem(key)
      if (v !== null) values[key] = v
    }
    out[cat.id] = values
  }
  return out
}

export function restoreSnapshot(snapshot: BrowserSnapshot) {
  for (const [key, value] of Object.entries(snapshot)) {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  }
}

/** 以備份內容取代所選類別的 localStorage; 失敗時恢復原值並拋錯。回傳還原前快照。 */
export function applyBrowserStorage(
  options: DeviceTransferOptions,
  storage: Record<string, Record<string, string>>,
): BrowserSnapshot {
  const snapshot: BrowserSnapshot = {}
  for (const key of keysOf(options, Object.keys(storage))) snapshot[key] = localStorage.getItem(key)
  try {
    for (const [cat, values] of Object.entries(storage)) {
      for (const key of keysOf(options, [cat])) {
        if (key in values) localStorage.setItem(key, values[key])
        else localStorage.removeItem(key)
      }
    }
  } catch (error) {
    restoreSnapshot(snapshot)
    throw error
  }
  return snapshot
}

/** 後端寫檔 → 前端寫 localStorage; 任一段失敗都整體復原。 */
export async function restoreBackup(
  options: DeviceTransferOptions,
  file: File,
  categories: string[],
  password?: string,
): Promise<{ result: RestoreResult; browserSnapshot: BrowserSnapshot }> {
  const result = await api.deviceTransferRestore(file, categories, password)
  try {
    return { result, browserSnapshot: applyBrowserStorage(options, result.browser_storage) }
  } catch {
    await api.deviceTransferRollback(result.restore_point)
    throw new Error('寫入瀏覽器設定失敗，已復原到還原前的狀態')
  }
}

export async function undoRestore(restorePointId: string, browserSnapshot: BrowserSnapshot) {
  await api.deviceTransferRollback(restorePointId)
  restoreSnapshot(browserSnapshot)
}

export interface ExportBackupRequest {
  preset?: string
  categories?: string[]
  include_secrets: boolean
  password?: string
  browser_storage: Record<string, Record<string, string>>
}

/** 下載 .twstock-backup (二進位, 不走 JSON request helper)。 */
export async function exportBackup(body: ExportBackupRequest): Promise<{ blob: Blob; filename: string }> {
  const res = await fetch('/api/device-transfer/export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const j = await res.json()
      if (typeof j.detail === 'string') detail = j.detail
    } catch { /* 非 JSON 錯誤保留狀態碼 */ }
    throw new Error(detail)
  }
  const match = /filename="([^"]+)"/.exec(res.headers.get('Content-Disposition') ?? '')
  return { blob: await res.blob(), filename: match?.[1] ?? 'twstock.twstock-backup' }
}

export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

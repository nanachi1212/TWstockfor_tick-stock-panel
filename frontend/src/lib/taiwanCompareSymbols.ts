// 台股多標的比較 — 純邏輯 / localStorage 輔助函式 (Phase 7H)
//
// 不依賴 React / react-router，可安全地同時被比較頁 (useCompareSymbols.ts)
// 與個股詳情頁 (TaiwanStockDetail.tsx 的「加入比較」) 呼叫，避免重複實作
// 正規化/去重/上限邏輯，也避免詳情頁被迫依賴比較頁專用的 useSearchParams hook。
//
// 僅持久化「標的代碼清單」，絕不持久化 AI 生成內容 (符合 Phase 7H 邊界)。

export const MIN_COMPARE_SYMBOLS = 2
export const MAX_COMPARE_SYMBOLS = 5

const LAST_COMPARE_SYMBOLS_KEY = 'tw_compare:last_symbols'

/**
 * 正規化一組標的代碼：大寫化、去除空白、過濾空字串、保序去重、裁切至上限。
 * 唯一實作 — URL 解析與「加入比較」合併皆呼叫此函式，避免邏輯分岔。
 */
export function canonicalizeSymbols(raw: (string | null | undefined)[]): string[] {
  const seen = new Set<string>()
  const result: string[] = []
  for (const item of raw) {
    const sym = (item || '').trim().toUpperCase()
    if (!sym || seen.has(sym)) continue
    seen.add(sym)
    result.push(sym)
    if (result.length >= MAX_COMPARE_SYMBOLS) break
  }
  return result
}

/** 將單一標的代碼合併進現有比較清單（正規化、去重、裁切至上限）。 */
export function mergeSymbolIntoCompare(existing: (string | null | undefined)[], symbol: string): string[] {
  return canonicalizeSymbols([...existing, symbol])
}

/** 讀取上次比較頁所選之標的清單（localStorage，失敗時安全回退為空陣列）。 */
export function loadLastCompareSymbols(): string[] {
  try {
    const raw = localStorage.getItem(LAST_COMPARE_SYMBOLS_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (Array.isArray(parsed)) return canonicalizeSymbols(parsed)
  } catch {
    /* ignore — private browsing / corrupt JSON / storage unavailable */
  }
  return []
}

/** 寫入本次比較頁所選之標的清單（僅代碼字串，絕不含 AI 生成內容）。 */
export function saveLastCompareSymbols(symbols: string[]): void {
  try {
    localStorage.setItem(LAST_COMPARE_SYMBOLS_KEY, JSON.stringify(canonicalizeSymbols(symbols)))
  } catch {
    /* ignore — private browsing / storage unavailable */
  }
}

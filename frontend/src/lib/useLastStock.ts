import { useCallback, useState } from 'react'

/**
 * 記憶"上次查看的個股"(按頁面維度,localStorage 持久化)。
 *
 * 兩個分析頁(財務 / 個股)各自獨立記憶,key 區分:
 *   - financials: 最後查看的財務分析個股
 *   - stock-analysis: 最後查看的個股分析個股
 *
 * 用法:
 *   const { last, remember } = useLastStock('stock-analysis')
 *   remember('000001.SZ', '平安銀行')   // 選中股票時調用
 *   <LastStockChip stock={last} ... />  // 渲染在 PageHeader 右側
 */

export interface StockRef { symbol: string; name: string }

const PREFIX = 'last_stock:'

export function useLastStock(scope: string) {
  const [last, setLast] = useState<StockRef | null>(() => load(scope))

  const remember = useCallback((symbol: string, name: string) => {
    const ref = { symbol, name }
    setLast(ref)
    save(scope, ref)
  }, [scope])

  const clear = useCallback(() => {
    setLast(null)
    save(scope, null)
  }, [scope])

  return { last, remember, clear }
}

function load(scope: string): StockRef | null {
  try {
    const v = localStorage.getItem(PREFIX + scope)
    if (!v) return null
    const p = JSON.parse(v)
    if (p && typeof p.symbol === 'string' && typeof p.name === 'string') return p
  } catch { /* ignore */ }
  return null
}

function save(scope: string, ref: StockRef | null) {
  try {
    if (ref) localStorage.setItem(PREFIX + scope, JSON.stringify(ref))
    else localStorage.removeItem(PREFIX + scope)
  } catch { /* ignore */ }
}

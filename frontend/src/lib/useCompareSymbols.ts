// 台股多標的比較頁 — URL 查詢參數同步 hook (Phase 7H)
//
// 唯一使用 useSearchParams() 的地方；僅供 TaiwanStockCompare.tsx 使用。
// URL 為選取狀態之唯一真實來源 (single source of truth)：selected 直接由
// searchParams 衍生，不另存一份會與 URL 走散的 useState。每次新增/移除皆以
// replace:true 寫回 URL (視為狀態編輯，而非個別導覽事件)。
//
// localStorage 鏡射（供「加入比較」跨頁合併使用）在 selected 每次變動時同步寫入
// ——不僅限於使用者主動新增/移除，也包含「直接開啟分享連結」等僅由 URL 衍生選取
// 的情形；否則透過分享連結進入比較頁後、未點擊任何按鈕就切去別的個股頁，再從
// 該頁點「加入比較」時，將讀不到分享連結帶入的既有選取，重演「跨頁遺失」問題。
// 僅在選取非空時才寫入，避免造訪空白比較頁（如導覽列連結）時，把先前有意義的
// 記憶覆蓋成空陣列。
import { useCallback, useEffect, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  MAX_COMPARE_SYMBOLS,
  MIN_COMPARE_SYMBOLS,
  canonicalizeSymbols,
  saveLastCompareSymbols,
} from './taiwanCompareSymbols'

export { MAX_COMPARE_SYMBOLS, MIN_COMPARE_SYMBOLS }

export function useCompareSymbols() {
  const [searchParams, setSearchParams] = useSearchParams()

  const selected = useMemo(
    () => canonicalizeSymbols((searchParams.get('symbols') || '').split(',')),
    [searchParams],
  )
  const selectedKey = selected.join(',')

  useEffect(() => {
    if (selected.length > 0) saveLastCompareSymbols(selected)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- selectedKey is the canonical dep
  }, [selectedKey])

  const writeSymbols = useCallback(
    (next: string[]) => {
      const canonical = canonicalizeSymbols(next)
      setSearchParams(
        canonical.length ? { symbols: canonical.join(',') } : {},
        { replace: true },
      )
    },
    [setSearchParams],
  )

  const addSymbol = useCallback(
    (symbol: string) => {
      writeSymbols([...selected, symbol])
    },
    [selected, writeSymbols],
  )

  const removeSymbol = useCallback(
    (symbol: string) => {
      const sym = symbol.toUpperCase()
      writeSymbols(selected.filter(s => s !== sym))
    },
    [selected, writeSymbols],
  )

  return { selected, addSymbol, removeSymbol }
}

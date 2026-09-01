// 台股多標的比較頁 — URL 查詢參數同步 hook (Phase 7H / 7I)
//
// 唯一使用 useSearchParams() 的地方；僅供 TaiwanStockCompare.tsx 使用。
// URL 為選取狀態之唯一真實來源 (single source of truth)：selected/date 皆直接由
// searchParams 衍生，不另存一份會與 URL 走散的 useState。每次新增/移除/換日皆以
// replace:true 寫回 URL (視為狀態編輯，而非個別導覽事件)。
//
// localStorage 鏡射（供「加入比較」跨頁合併使用）僅適用於 symbols，在 selected 每次
// 變動時同步寫入——不僅限於使用者主動新增/移除，也包含「直接開啟分享連結」等僅由
// URL 衍生選取的情形；否則透過分享連結進入比較頁後、未點擊任何按鈕就切去別的個股
// 頁，再從該頁點「加入比較」時，將讀不到分享連結帶入的既有選取，重演「跨頁遺失」
// 問題。無條件鏡射（包含清空至 0 檔）：localStorage 必須隨時精確代表比較頁「當下」
// 的選取狀態，使用者移除完所有標的後，儲存的清單也必須一併清空。
//
// date（比較日期）刻意不比照 symbols 做 localStorage 鏡射——Phase 7I 規格明確要求
// 日期僅存在於 URL，且「無 date 參數」本身即代表「最新模式」這個有意義的狀態，不應
// 被跨頁記憶覆蓋或汙染。
import { useCallback, useEffect, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  MAX_COMPARE_SYMBOLS,
  MIN_COMPARE_SYMBOLS,
  canonicalizeSymbols,
  parseCompareDate,
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

  // null = 未指定日期 ("最新模式")；非 null = 使用者明確指定之歷史比較日期。
  // 無效的日期字串 (格式錯誤或非真實存在之西曆日期) 一律視同未指定。
  const date = useMemo(() => parseCompareDate(searchParams.get('date')), [searchParams])

  useEffect(() => {
    saveLastCompareSymbols(selected)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- selectedKey is the canonical dep
  }, [selectedKey])

  const writeSymbols = useCallback(
    (next: string[]) => {
      const canonical = canonicalizeSymbols(next)
      setSearchParams(
        (prev) => {
          const params = new URLSearchParams(prev)
          if (canonical.length) params.set('symbols', canonical.join(','))
          else params.delete('symbols')
          return params
        },
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

  // 設定/清除比較日期——null 或無效輸入皆移除 date 參數 (回到「最新模式」)，
  // 絕不寫入 localStorage。
  const setDate = useCallback(
    (next: string | null) => {
      const canonical = parseCompareDate(next)
      setSearchParams(
        (prev) => {
          const params = new URLSearchParams(prev)
          if (canonical) params.set('date', canonical)
          else params.delete('date')
          return params
        },
        { replace: true },
      )
    },
    [setSearchParams],
  )

  return { selected, addSymbol, removeSymbol, date, setDate }
}

/**
 * 表格排序 hook（三態：無 → 升序 → 降序 → 無）。
 *
 * 從自選頁提煉，自選/策略列表共享。調用方提供 getSortValue 決定每列的排序標量。
 */
import { useCallback, useState } from 'react'
import type { ColumnConfig } from '@/lib/list-columns'
import { getSortValue as defaultGetSortValue } from '@/lib/stock-table'

export interface SortState {
  key: string   // 列 id
  dir: 'asc' | 'desc'
}

export function useTableSort<T>(getSortValue: (r: T, col: ColumnConfig) => any = defaultGetSortValue) {
  const [sort, setSort] = useState<SortState | null>(null)

  /** 點擊表頭：同列輪換 asc→desc→清除；不同列重置為 asc */
  const toggle = useCallback((colId: string) => {
    setSort(prev => {
      if (!prev || prev.key !== colId) return { key: colId, dir: 'asc' }
      if (prev.dir === 'asc') return { key: colId, dir: 'desc' }
      return null
    })
  }, [])

  /** 對行集合按當前 sort 排序（返回新數組）。無 sort 或列不存在時原樣返回；
   *  取值為 null 的行排在最後。表頭是否可點由 StockDataTable 控制。 */
  const sortRows = useCallback((rows: T[], columns: ColumnConfig[]): T[] => {
    if (!sort) return rows
    const col = columns.find(c => c.id === sort.key)
    if (!col) return rows
    const { dir } = sort
    return [...rows].sort((a, b) => {
      const va = getSortValue(a, col)
      const vb = getSortValue(b, col)
      if (va == null && vb == null) return 0
      if (va == null) return 1
      if (vb == null) return -1
      const na = typeof va === 'number' ? va : Number(va)
      const nb = typeof vb === 'number' ? vb : Number(vb)
      if (!Number.isNaN(na) && !Number.isNaN(nb)) {
        return dir === 'asc' ? na - nb : nb - na
      }
      const sa = String(va), sb = String(vb)
      return dir === 'asc' ? sa.localeCompare(sb) : sb.localeCompare(sa)
    })
  }, [sort, getSortValue])

  return { sort, toggle, sortRows }
}

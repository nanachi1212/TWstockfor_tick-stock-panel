/**
 * 股票列表表格骨架（自選/策略頁共享）。
 *
 * 職責：表頭渲染（讀列配置 label/align + 可排序三態指示器）、表體遍歷、sticky 表頭。
 * 不內置任何業務邏輯：單元格內容（含 symbol 列交互、操作列、ext 列）由調用方通過
 * renderCell / renderExtraCol 注入。這樣兩個頁面的特有交互得以保留，同時表頭能力一致。
 */
import { cloneElement, isValidElement, useRef, type ReactElement, type ReactNode } from 'react'
import { useVirtualizer, type VirtualItem } from '@tanstack/react-virtual'
import type { ColumnConfig } from '@/lib/list-columns'
import { UNSORTABLE_KEYS } from '@/lib/stock-table'
import { VIRTUAL_LIST_THRESHOLD, useParentScroll } from '@/components/virtual-list/useParentScroll'
import type { SortState } from './useTableSort'

export type { SortState }

export interface StockDataTableProps {
  columns: ColumnConfig[]
  rows: any[]
  /** 單元格渲染回調。返回 null 時回退到內置純數據列渲染。 */
  renderCell: (r: any, col: ColumnConfig) => ReactNode
  /** 行 key（默認取 r.symbol） */
  rowKey?: (r: any) => string | number
  /** 行 className（默認含 hover） */
  rowClassName?: (r: any) => string
  /** 表頭是否 sticky（自選頁需要，策略頁不需要） */
  headerSticky?: boolean
  /** 最小表格寬度，默認按列數計算 */
  minWidth?: number
  /** 排序：外部受控時傳入（含當前 sort 與 toggle）；不傳則表頭不可排序 */
  sort?: SortState | null
  onSortToggle?: (colId: string) => void
  /** 實例級放行: 讓 UNSORTABLE_KEYS 中的 builtin 列在本表也可排序 (如自選頁分時列) */
  extraSortableKeys?: ReadonlySet<string>
  /** 追加在每行末尾的額外單元格（如自選頁的操作列） */
  renderExtraCol?: (r: any) => ReactNode
  /** 追加的表頭單元格（對應 renderExtraCol） */
  extraHeader?: ReactNode
  /** 自定義表頭單元格內容覆蓋（如日k眼睛按鈕）。返回 undefined 則用 col.label */
  renderHeaderContent?: (col: ColumnConfig) => ReactNode | undefined
  /** 外層容器 className */
  className?: string
}

function alignThClass(align: ColumnConfig['align']): string {
  // 表頭一律不換行: 窄列(如收起的圖表列)中標籤/排序箭頭折行會把整行表頭頂高
  if (align === 'right') return 'px-3 py-2.5 font-medium text-right whitespace-nowrap'
  if (align === 'center') return 'px-3 py-2.5 font-medium text-center whitespace-nowrap'
  return 'px-3 py-2.5 font-medium whitespace-nowrap'
}

export function StockDataTable({
  columns, rows, renderCell,
  rowKey = (r: any) => r.symbol,
  rowClassName = () => 'border-t border-border hover:bg-elevated/50',
  headerSticky = false,
  minWidth,
  sort,
  onSortToggle,
  extraSortableKeys,
  renderExtraCol,
  extraHeader,
  renderHeaderContent,
  className = 'rounded-card border border-border overflow-x-auto',
}: StockDataTableProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const visibleColumns = columns.filter(c => c.visible)
  const computedMinWidth = minWidth ?? Math.max(900, visibleColumns.length * 110)
  const virtualized = rows.length > VIRTUAL_LIST_THRESHOLD
  const { getScrollElement, scrollMargin } = useParentScroll(containerRef, virtualized)
  const rowVirtualizer = useVirtualizer({
    count: virtualized ? rows.length : 0,
    getScrollElement,
    estimateSize: () => 56,
    getItemKey: index => rowKey(rows[index]),
    overscan: 10,
    scrollMargin,
  })
  const virtualRows = virtualized ? rowVirtualizer.getVirtualItems() : []
  const totalSize = virtualized ? rowVirtualizer.getTotalSize() : 0
  const firstVirtualRow = virtualRows[0]
  const lastVirtualRow = virtualRows[virtualRows.length - 1]
  const topPadding = firstVirtualRow ? firstVirtualRow.start - scrollMargin : 0
  const bottomPadding = lastVirtualRow
    ? totalSize - (lastVirtualRow.end - scrollMargin)
    : totalSize
  const columnCount = visibleColumns.length + (renderExtraCol || extraHeader ? 1 : 0)

  const isColSortable = (col: ColumnConfig): boolean => {
    // 排序能力由調用方是否提供 onSortToggle 決定；sort 是否為 null 隻影響當前指示器
    if (!onSortToggle) return false
    if (col.source.type === 'builtin' && UNSORTABLE_KEYS.has(col.source.key) && !extraSortableKeys?.has(col.source.key)) return false
    return true
  }

  const theadClass = headerSticky
    ? 'sticky top-0 z-10 bg-surface after:absolute after:inset-x-0 after:bottom-0 after:h-px after:bg-border'
    : 'bg-elevated'

  const renderRow = (r: any, virtualRow?: VirtualItem) => (
    <tr
      key={rowKey(r)}
      ref={virtualRow ? rowVirtualizer.measureElement : undefined}
      data-index={virtualRow?.index}
      className={`transition-colors duration-150 ease-smooth group ${rowClassName(r)}`}
    >
      {visibleColumns.map(col => {
        // renderCell 返回的 <td> 無 key, 這裡補上避免 React key 警告
        const cell = renderCell(r, col)
        return isValidElement(cell)
          ? cloneElement(cell as ReactElement, { key: col.id })
          : cell
      })}
      {renderExtraCol && renderExtraCol(r)}
    </tr>
  )

  return (
    <div ref={containerRef} className={className}>
      <table className="w-full text-sm" style={{ minWidth: computedMinWidth }}>
        <thead className={theadClass}>
          <tr className="text-left text-secondary">
            {visibleColumns.map(col => {
              const sortable = isColSortable(col)
              const isSorted = sort?.key === col.id
              const dir = isSorted ? sort!.dir : null
              const contentOverride = renderHeaderContent?.(col)
              return (
                <th
                  key={col.id}
                  className={`${alignThClass(col.align)} ${sortable ? 'cursor-pointer select-none group' : ''}`}
                  onClick={sortable ? () => onSortToggle!(col.id) : undefined}
                >
                  {contentOverride !== undefined ? contentOverride : col.label}
                  {sortable && (
                    <span className="inline-block ml-1 text-[10px] opacity-30 group-hover:opacity-60 transition-opacity">
                      {isSorted ? (dir === 'asc' ? '↑' : '↓') : '↕'}
                    </span>
                  )}
                </th>
              )
            })}
            {extraHeader && (
              <th className="px-3 py-2.5 font-medium text-right">{extraHeader}</th>
            )}
          </tr>
        </thead>
        <tbody>
          {virtualized && topPadding > 0 && (
            <tr aria-hidden="true">
              <td colSpan={columnCount} className="p-0 border-0" style={{ height: topPadding }} />
            </tr>
          )}
          {virtualized
            ? virtualRows.map(virtualRow => renderRow(rows[virtualRow.index], virtualRow))
            : rows.map((r: any) => renderRow(r))}
          {virtualized && bottomPadding > 0 && (
            <tr aria-hidden="true">
              <td colSpan={columnCount} className="p-0 border-0" style={{ height: bottomPadding }} />
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

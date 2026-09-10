import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Check, ChevronDown, ChevronUp, FolderCog, FolderInput, Pencil, Plus, Trash2, X, Eraser } from 'lucide-react'
import { Modal } from '@/components/Modal'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { usePreferences } from '@/lib/useSharedQueries'
import { fmtPct } from '@/lib/format'
import type { WatchlistGroup, WatchlistGroupColor } from '@/lib/api'
import {
  groupPctColor,
  groupPctTitle,
  type GroupPctMap,
} from '@/lib/watchlistGroupStats'
import {
  DEFAULT_WATCHLIST_GROUP_COLOR,
  WATCHLIST_GROUP_COLORS,
  resolveWatchlistGroupColor,
} from '@/lib/watchlist-group-colors'

export type WatchlistGroupFilter = 'all' | 'ungrouped' | string

interface GroupBarProps {
  groups: WatchlistGroup[]
  counts: Record<string, number>
  selected: WatchlistGroupFilter
  total: number
  /** 分組等權平均漲跌幅 (key: 'all' | 'ungrouped' | 分組id); 缺省不顯示 */
  pcts?: GroupPctMap
  onSelect: (group: WatchlistGroupFilter) => void
  onCreate: (name: string, color: WatchlistGroupColor) => Promise<void>
  onRename: (groupId: string, name: string, color: WatchlistGroupColor) => Promise<void>
  onDelete: (groupId: string) => Promise<void>
  onClearGroup?: (groupId: string) => Promise<void>
  /** 手動調整分組前後順序 (持久化到後端) */
  onReorder?: (orderedIds: string[]) => Promise<void>
}

export function WatchlistGroupBar({
  groups,
  counts,
  selected,
  total,
  pcts,
  onSelect,
  onCreate,
  onRename,
  onDelete,
  onClearGroup,
  onReorder,
}: GroupBarProps) {
  const [managerOpen, setManagerOpen] = useState(false)
  const [confirmClear, setConfirmClear] = useState(false)
  const tabs = [
    { id: 'all', name: '全部', count: total, color: null },
    { id: 'ungrouped', name: '未分組', count: counts.ungrouped ?? 0, color: null },
    ...groups.map(group => ({ id: group.id, name: group.name, count: counts[group.id] ?? 0, color: group.color })),
  ]
  // 拖拽排序狀態: dragIndex = 拖動中的分組下標, dropIndex = 插入位置 (均相對 groups 數組)
  const [dragIndex, setDragIndex] = useState<number | null>(null)
  const [dropIndex, setDropIndex] = useState<number | null>(null)
  const reorderable = !!onReorder && groups.length > 1
  const tablistRef = useRef<HTMLDivElement>(null)

  const clearDrag = () => { setDragIndex(null); setDropIndex(null) }

  // 分組很多時標籤欄橫向滾動, 拖到邊緣附近自動滾動, 保證能拖到視野外的位置
  const autoScroll = (clientX: number) => {
    const el = tablistRef.current
    if (!el || el.scrollWidth <= el.clientWidth) return
    const rect = el.getBoundingClientRect()
    const edge = 48
    if (clientX < rect.left + edge) el.scrollLeft -= 16
    else if (clientX > rect.right - edge) el.scrollLeft += 16
  }

  const handleDrop = () => {
    if (dragIndex == null || dropIndex == null) { clearDrag(); return }
    const ids = groups.map(group => group.id)
    const [moved] = ids.splice(dragIndex, 1)
    if (moved) {
      ids.splice(dropIndex > dragIndex ? dropIndex - 1 : dropIndex, 0, moved)
      if (ids.join(',') !== groups.map(group => group.id).join(',')) {
        void onReorder?.(ids)
      }
    }
    clearDrag()
  }

  return (
    <>
      <div className="flex h-10 items-stretch border-b border-border bg-surface/40 px-5">
        <div
          ref={tablistRef}
          role="tablist"
          aria-label="自選分組"
          onDragOver={dragIndex != null ? e => autoScroll(e.clientX) : undefined}
          className="flex min-w-0 flex-1 items-stretch gap-1 overflow-x-auto"
        >
          {tabs.map((tab, tabIndex) => {
            const active = selected === tab.id
            const color = tab.color ? resolveWatchlistGroupColor(tab.color) : null
            // 前兩個為固定標籤 (全部/未分組), 其後對應 groups 數組 — 可拖拽排序
            const groupIndex = tabIndex - 2
            const draggable = reorderable && tabIndex >= 2
            const dragging = draggable && dragIndex === groupIndex
            return (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={active}
                draggable={draggable}
                onDragStart={draggable ? e => {
                  setDragIndex(groupIndex)
                  e.dataTransfer.effectAllowed = 'move'
                  e.dataTransfer.setData('text/plain', tab.id)
                } : undefined}
                onDragOver={draggable ? e => {
                  e.preventDefault()
                  e.dataTransfer.dropEffect = 'move'
                  autoScroll(e.clientX)
                  const rect = e.currentTarget.getBoundingClientRect()
                  setDropIndex(groupIndex + (e.clientX > rect.left + rect.width / 2 ? 1 : 0))
                } : undefined}
                onDrop={draggable ? e => { e.preventDefault(); handleDrop() } : undefined}
                onDragEnd={draggable ? clearDrag : undefined}
                onClick={() => onSelect(tab.id)}
                title={draggable ? `${tab.name} — 可拖拽調整分組順序` : undefined}
                className={`relative my-1.5 inline-flex shrink-0 items-center gap-1.5 rounded-btn border px-3 text-xs transition-colors ${
                  draggable ? 'cursor-grab active:cursor-grabbing' : ''
                } ${
                  dragging ? 'opacity-40' : ''
                } ${
                  active
                    ? color
                      ? `${color.text} ${color.border} ${color.background}`
                      : 'border-accent/40 bg-accent/10 text-accent'
                    : color
                      ? `border-transparent ${color.text} hover:bg-elevated`
                      : 'border-transparent text-secondary hover:bg-elevated hover:text-foreground'
                }`}
              >
                {draggable && dragIndex != null && dropIndex === groupIndex && (
                  <span className="absolute -left-0.5 top-1 bottom-1 w-0.5 rounded-full bg-accent" />
                )}
                {draggable && dragIndex != null && dropIndex === groupIndex + 1 && groupIndex === groups.length - 1 && (
                  <span className="absolute -right-0.5 top-1 bottom-1 w-0.5 rounded-full bg-accent" />
                )}
                {color && <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${color.dot}`} />}
                <span>{tab.name}</span>
                <span className={`font-mono text-[10px] tabular-nums ${active && !color ? 'text-accent/80' : 'text-muted'}`}>
                  {tab.count}
                </span>
                {pcts && (() => {
                  const info = pcts[tab.id]
                  if (!info || info.pct == null || info.sampled === 0) return null
                  return (
                    <span
                      className={`font-mono text-[10px] tabular-nums ${groupPctColor(info.pct)}`}
                      title={groupPctTitle(info)}
                    >
                      {fmtPct(info.pct)}
                    </span>
                  )
                })()}
              </button>
            )
          })}
        </div>
        <button
          type="button"
          onClick={() => setManagerOpen(true)}
          className="ml-2 inline-flex w-8 shrink-0 items-center justify-center text-muted hover:text-accent"
          title="管理自選分組"
          aria-label="管理自選分組"
        >
          <FolderCog className="h-4 w-4" />
        </button>
        {/* 清空當前分組 — 僅選中具體分組時顯示 */}
        {onClearGroup && selected !== 'all' && selected !== 'ungrouped' && (
          <button
            type="button"
            onClick={() => setConfirmClear(true)}
            className="inline-flex w-8 shrink-0 items-center justify-center text-muted hover:text-warning"
            title="清空當前分組"
            aria-label="清空當前分組"
          >
            <Eraser className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* 清空分組確認彈窗 */}
      {confirmClear && selected !== 'all' && selected !== 'ungrouped' && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={() => setConfirmClear(false)}
          />
          <div className="relative w-[90vw] max-w-[380px] rounded-card border border-border bg-base shadow-2xl p-6">
            <h3 className="text-sm font-medium text-foreground mb-2">清空分組</h3>
            <p className="text-xs text-secondary mb-5">
              確認清空「{tabs.find(t => t.id === selected)?.name}」分組? 分組內所有股票將轉為未分組(不從自選中刪除)。
            </p>
            <div className="flex items-center justify-end gap-2">
              <button
                onClick={() => setConfirmClear(false)}
                className="px-3 py-1.5 rounded-btn bg-elevated text-secondary hover:bg-elevated/80 text-sm transition-colors"
              >
                取消
              </button>
              <button
                onClick={() => { setConfirmClear(false); void onClearGroup?.(selected) }}
                className="px-3 py-1.5 rounded-btn bg-warning/15 text-warning hover:bg-warning/25 text-sm font-medium transition-colors"
              >
                確認清空
              </button>
            </div>
          </div>
        </div>
      )}

      {managerOpen && (
        <GroupManagerDialog
          groups={groups}
          counts={counts}
          onClose={() => setManagerOpen(false)}
          onCreate={onCreate}
          onRename={onRename}
          onDelete={onDelete}
          onReorder={onReorder}
        />
      )}
    </>
  )
}

function GroupColorPicker({
  value,
  onChange,
}: {
  value: WatchlistGroupColor
  onChange: (color: WatchlistGroupColor) => void
}) {
  return (
    <div role="radiogroup" aria-label="分組顏色" className="flex flex-wrap items-center gap-1.5">
      {WATCHLIST_GROUP_COLORS.map(option => {
        const selected = value === option.id
        return (
          <button
            key={option.id}
            type="button"
            role="radio"
            aria-checked={selected}
            aria-label={option.label}
            title={option.label}
            onClick={() => onChange(option.id)}
            className={`inline-flex h-6 w-6 items-center justify-center rounded-full border transition-transform hover:scale-110 ${
              selected
                ? `${option.border} ${option.background} ring-2 ${option.ring}`
                : 'border-transparent hover:bg-elevated'
            }`}
          >
            <span className={`h-3 w-3 rounded-full ${option.dot}`} />
          </button>
        )
      })}
    </div>
  )
}

function GroupManagerDialog({
  groups,
  counts,
  onClose,
  onCreate,
  onRename,
  onDelete,
  onReorder,
}: Omit<GroupBarProps, 'selected' | 'total' | 'onSelect' | 'onClearGroup'> & { onClose: () => void }) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [newName, setNewName] = useState('')
  const [newColor, setNewColor] = useState<WatchlistGroupColor>(DEFAULT_WATCHLIST_GROUP_COLOR)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingName, setEditingName] = useState('')
  const [editingColor, setEditingColor] = useState<WatchlistGroupColor>(DEFAULT_WATCHLIST_GROUP_COLOR)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')

  // 「顯示在側邊欄」偏好開關
  const qc = useQueryClient()
  const prefs = usePreferences()
  const groupsInNav = prefs.data?.watchlist_groups_in_nav ?? false
  const [navTogglePending, setNavTogglePending] = useState(false)
  const toggleGroupsInNav = async (enabled: boolean) => {
    setNavTogglePending(true)
    try {
      await api.updateWatchlistGroupsInNav(enabled)
      await qc.invalidateQueries({ queryKey: QK.preferences })
    } finally {
      setNavTogglePending(false)
    }
  }

  const validate = (name: string) => {
    const value = name.trim()
    if (!value) return '請輸入分組名稱'
    if (value.length > 24) return '分組名稱不能超過 24 個字元'
    return ''
  }

  const run = async (action: () => Promise<void>) => {
    setPending(true)
    setError('')
    try {
      await action()
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失敗')
    } finally {
      setPending(false)
    }
  }

  const create = async () => {
    const message = validate(newName)
    if (message) {
      setError(message)
      return
    }
    await run(async () => {
      await onCreate(newName.trim(), newColor)
      setNewName('')
      setNewColor(DEFAULT_WATCHLIST_GROUP_COLOR)
      inputRef.current?.focus()
    })
  }

  const rename = async (groupId: string) => {
    const message = validate(editingName)
    if (message) {
      setError(message)
      return
    }
    await run(async () => {
      await onRename(groupId, editingName.trim(), editingColor)
      setEditingId(null)
    })
  }

  // 上移/下移: 與相鄰分組交換位置, 新順序由後端持久化 (json 數組順序即定義順序)
  const move = async (groupId: string, dir: -1 | 1) => {
    if (!onReorder) return
    const ids = groups.map(group => group.id)
    const index = ids.indexOf(groupId)
    const target = index + dir
    if (index < 0 || target < 0 || target >= ids.length) return
    ;[ids[index], ids[target]] = [ids[target], ids[index]]
    await run(async () => { await onReorder(ids) })
  }

  return (
    <Modal
      onClose={onClose}
      labelledBy="watchlist-groups-title"
      initialFocusRef={inputRef}
      panelClassName="w-[92vw] max-w-md bg-surface border border-border rounded-card shadow-xl"
    >
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div>
          <h2 id="watchlist-groups-title" className="text-sm font-semibold text-foreground">管理自選分組</h2>
          <p className="mt-0.5 text-[11px] text-muted">刪除分組不會刪除其中的股票</p>
        </div>
        <button type="button" onClick={onClose} className="h-8 w-8 inline-flex items-center justify-center text-muted hover:text-foreground" aria-label="關閉">
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* 顯示在側邊欄 開關 */}
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <div className="min-w-0">
          <div className="text-xs font-medium text-foreground">顯示在側邊欄</div>
          <div className="mt-0.5 text-[10px] text-muted">開啟後可在左側選單展開分組子選單</div>
        </div>
        <button
          type="button"
          onClick={() => void toggleGroupsInNav(!groupsInNav)}
          disabled={navTogglePending}
          className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors duration-200 disabled:opacity-50 ${
            groupsInNav ? 'bg-accent' : 'bg-elevated'
          }`}
          title={groupsInNav ? '已開啟 — 點擊關閉' : '已關閉 — 點擊開啟'}
        >
          <span className={`inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform duration-200 ${
            groupsInNav ? 'translate-x-[18px]' : 'translate-x-0.5'
          }`} />
        </button>
      </div>

      <div className="px-4 py-3">
        <div className="flex gap-2">
          <input
            ref={inputRef}
            value={newName}
            maxLength={24}
            onChange={event => setNewName(event.target.value)}
            onKeyDown={event => { if (event.key === 'Enter') void create() }}
            placeholder="新分組名稱"
            className="h-8 min-w-0 flex-1 rounded-btn border border-border bg-elevated px-3 text-xs text-foreground outline-none focus:border-accent/50"
          />
          <button
            type="button"
            onClick={() => void create()}
            disabled={pending}
            className="inline-flex h-8 items-center gap-1.5 rounded-btn bg-accent px-3 text-xs text-white disabled:opacity-50"
          >
            <Plus className="h-3.5 w-3.5" />
            新建
          </button>
        </div>
        <div className="mt-2 flex items-start gap-2">
          <span className="mt-1 shrink-0 text-[11px] text-muted">分組顏色</span>
          <GroupColorPicker value={newColor} onChange={setNewColor} />
        </div>
        {error && <p className="mt-2 text-xs text-danger">{error}</p>}
      </div>

      <div className="max-h-[360px] overflow-y-auto border-t border-border px-4">
        {groups.length === 0 ? (
          <div className="py-10 text-center text-xs text-muted">暫無自訂分組</div>
        ) : groups.map((group, index) => {
          const color = resolveWatchlistGroupColor(group.color)
          return (
          <div key={group.id} className="flex min-h-12 items-center gap-2 border-b border-border/60 last:border-0">
            <span className={`h-6 w-1 shrink-0 rounded-full ${color.dot}`} />
            {editingId === group.id ? (
              <div className="min-w-0 flex-1 py-2">
                <div className="flex items-center gap-1.5">
                  <input
                    value={editingName}
                    maxLength={24}
                    onChange={event => setEditingName(event.target.value)}
                    onKeyDown={event => { if (event.key === 'Enter') void rename(group.id) }}
                    className={`h-7 min-w-0 flex-1 rounded-btn border bg-elevated px-2 text-xs text-foreground outline-none ${resolveWatchlistGroupColor(editingColor).border}`}
                    autoFocus
                  />
                  <button type="button" disabled={pending} onClick={() => void rename(group.id)} className={`p-1 ${resolveWatchlistGroupColor(editingColor).text}`} title="儲存分組">
                    <Check className="h-3.5 w-3.5" />
                  </button>
                  <button type="button" onClick={() => setEditingId(null)} className="p-1 text-muted hover:text-foreground" title="取消">
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
                <div className="mt-1.5">
                  <GroupColorPicker value={editingColor} onChange={setEditingColor} />
                </div>
              </div>
            ) : deletingId === group.id ? (
              <>
                <span className="min-w-0 flex-1 text-xs text-secondary">
                  刪除「{group.name}」？{(counts[group.id] ?? 0) > 0 ? ` ${counts[group.id]} 檔股票將回到未分組。` : ''}
                </span>
                <button
                  type="button"
                  disabled={pending}
                  onClick={() => void run(async () => { await onDelete(group.id); setDeletingId(null) })}
                  className="rounded px-2 py-1 text-[11px] text-danger bg-danger/10 hover:bg-danger/20 disabled:opacity-50"
                >
                  確認
                </button>
                <button type="button" onClick={() => setDeletingId(null)} className="p-1 text-muted hover:text-foreground" aria-label="取消刪除">
                  <X className="h-3.5 w-3.5" />
                </button>
              </>
            ) : (
              <>
                <span className={`min-w-0 flex-1 truncate text-xs ${color.text}`}>{group.name}</span>
                <span className="font-mono text-[10px] text-muted tabular-nums">{counts[group.id] ?? 0} 檔</span>
                {onReorder && groups.length > 1 && (
                  <>
                    <button
                      type="button"
                      disabled={pending || index === 0}
                      onClick={() => void move(group.id, -1)}
                      className="p-1 text-muted hover:text-accent disabled:opacity-30 disabled:hover:text-muted"
                      title="上移"
                      aria-label={`上移分組 ${group.name}`}
                    >
                      <ChevronUp className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      disabled={pending || index === groups.length - 1}
                      onClick={() => void move(group.id, 1)}
                      className="p-1 text-muted hover:text-accent disabled:opacity-30 disabled:hover:text-muted"
                      title="下移"
                      aria-label={`下移分組 ${group.name}`}
                    >
                      <ChevronDown className="h-3.5 w-3.5" />
                    </button>
                  </>
                )}
                <button
                  type="button"
                  onClick={() => {
                    setEditingId(group.id)
                    setEditingName(group.name)
                    setEditingColor(group.color)
                    setDeletingId(null)
                    setError('')
                  }}
                  className="p-1 text-muted hover:text-accent"
                  title="編輯分組"
                >
                  <Pencil className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  onClick={() => { setDeletingId(group.id); setEditingId(null); setError('') }}
                  className="p-1 text-muted hover:text-danger"
                  title="刪除分組"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </>
            )}
          </div>
          )
        })}
      </div>
    </Modal>
  )
}

interface GroupPickerProps {
  groups: WatchlistGroup[]
  /** 該標的當前所屬分組 id 列表 */
  groupIds: string[]
  symbol: string
  disabled?: boolean
  /** 勾選=加入該分組, 取消勾選=僅移出該分組 (標的保留在自選中) */
  onToggleMember: (symbol: string, groupId: string, member: boolean) => void
}

export function WatchlistGroupPicker({ groups, groupIds, symbol, disabled, onToggleMember }: GroupPickerProps) {
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState({ top: 0, left: 0, flipUp: false })
  const btnRef = useRef<HTMLButtonElement>(null)
  const popRef = useRef<HTMLDivElement>(null)

  const POP_WIDTH = 176
  const openMenu = () => {
    const rect = btnRef.current?.getBoundingClientRect()
    if (rect) {
      // 面板右對齊按鈕 (視口邊界保護); 底部放不下時翻轉到按鈕上方
      const estHeight = groups.length * 28 + 44
      const flipUp = rect.bottom + estHeight > window.innerHeight && rect.top > estHeight
      setPos({
        top: flipUp ? rect.top - 4 : rect.bottom + 4,
        left: Math.max(8, Math.min(rect.right - POP_WIDTH, window.innerWidth - POP_WIDTH - 8)),
        flipUp,
      })
    }
    setOpen(true)
  }

  // 外部點擊 / Esc 關閉 (按鈕與面板自身除外)
  useEffect(() => {
    if (!open) return
    const onDocMouseDown = (e: MouseEvent) => {
      const target = e.target as Node
      if (btnRef.current?.contains(target) || popRef.current?.contains(target)) return
      setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDocMouseDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocMouseDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const memberSet = new Set(groupIds)
  const memberGroups = groupIds
    .map(gid => groups.find(g => g.id === gid))
    .filter((g): g is WatchlistGroup => !!g)
  const dots = memberGroups.slice(0, 3)
  const titleNames = memberGroups.map(g => g.name).join('、')

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        disabled={disabled}
        onClick={event => {
          event.stopPropagation()
          if (open) {
            setOpen(false)
          } else {
            openMenu()
          }
        }}
        className={`relative inline-flex h-5 items-center justify-center gap-0.5 rounded border border-transparent px-1 transition-colors ${
          memberGroups.length > 0
            ? 'hover:border-accent/30'
            : 'text-muted hover:border-accent/30 hover:text-accent'
        } ${disabled ? 'opacity-40' : ''}`}
        title={memberGroups.length === 0 ? '未分組 — 點擊設定分組' : `分組：${titleNames}`}
        aria-label={`${symbol} 的分組`}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        {dots.length === 0 ? (
          <FolderInput className="h-3.5 w-3.5" />
        ) : (
          // 疊瓦式圓點: 先加的分組在最上層完整顯示, 後加的從其右側露出半圓, 緊湊不撐寬
          <span className="flex items-center">
            {dots.map((g, i) => (
              <span
                key={g.id}
                style={{ zIndex: dots.length - i }}
                className={`relative h-2 w-2 rounded-full ring-1 ring-border/50 ${resolveWatchlistGroupColor(g.color).dot} ${i > 0 ? '-ml-1' : ''}`}
              />
            ))}
            {memberGroups.length > 3 && (
              <span className="ml-0.5 font-mono text-[9px] leading-none text-muted">+{memberGroups.length - 3}</span>
            )}
          </span>
        )}
      </button>
      {open && createPortal(
        <div
          ref={popRef}
          role="menu"
          aria-label={`${symbol} 的分組`}
          data-watchlist-group-menu
          style={{
            position: 'fixed',
            top: pos.flipUp ? undefined : pos.top,
            bottom: pos.flipUp ? window.innerHeight - pos.top : undefined,
            left: pos.left,
            width: POP_WIDTH,
          }}
          className="z-50 rounded-card border border-border bg-base p-1 shadow-xl"
          onClick={event => event.stopPropagation()}
        >
          <div className="px-2 pb-1 pt-1.5 text-[10px] text-muted">加入分組（可多選）</div>
          {groups.length === 0 ? (
            <div className="px-2 py-2 text-xs text-muted">暫無分組，請先新建</div>
          ) : groups.map(group => {
            const color = resolveWatchlistGroupColor(group.color)
            const member = memberSet.has(group.id)
            return (
              <button
                key={group.id}
                type="button"
                role="menuitemcheckbox"
                aria-checked={member}
                onClick={() => onToggleMember(symbol, group.id, !member)}
                className={`flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs transition-colors hover:bg-elevated ${
                  member ? color.text : 'text-secondary'
                }`}
              >
                <span className={`inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-sm border ${
                  member ? `${color.border} ${color.background}` : 'border-border'
                }`}>
                  {member && <Check className="h-2.5 w-2.5" />}
                </span>
                <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${color.dot}`} />
                <span className="min-w-0 flex-1 truncate">{group.name}</span>
              </button>
            )
          })}
          {groups.length > 0 && groupIds.length === 0 && (
            <div className="border-t border-border/60 px-2 pb-1 pt-1.5 text-[10px] text-muted">
              未加入任何分組 — 標的仍在自選中
            </div>
          )}
        </div>,
        document.body,
      )}
    </>
  )
}

import { useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { Eye, EyeOff, ExternalLink, GripVertical, Bell } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { usePreferences } from '@/lib/useSharedQueries'
import { CORE_NAV } from '@/lib/navigation'

interface NavEntry {
  id: string
  label: string
  type: 'builtin'
  visible: boolean
}

// Phase 8B-2.1 — 台股核心功能清單改由 @/lib/navigation.ts 的 CORE_NAV 產生,
// 與 Layout.tsx 的 sidebar 共用同一份 metadata(不再各自維護一份易漂移的清單)。
// 中國 A 股 legacy 功能(ASHARE_LEGACY_NAV)不在這裡 —— 它們不是獨立的核心
// menu item, 改用下方「中國 A 股功能」小節的總開關 + 個別顯示管理。
const BUILTIN_PAGES: NavEntry[] = CORE_NAV.map(n => ({
  id: n.to, label: n.label, type: 'builtin' as const, visible: true,
}))

// ── Sortable row ──

function SortableItem({ entry, hidden, onToggleHidden, badgeEnabled, onToggleBadge }: {
  entry: NavEntry
  hidden: boolean
  onToggleHidden: (id: string) => void
  badgeEnabled?: boolean
  onToggleBadge?: (id: string) => void
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: entry.id })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.6 : 1,
    zIndex: isDragging ? 10 : undefined,
  }

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`grid grid-cols-[2.5rem_1fr_4.5rem_3rem_3rem_3rem] items-center border-b border-border/70 px-4 py-3 last:border-b-0 ${
        isDragging ? 'bg-elevated rounded-lg shadow-lg' : ''
      } ${hidden ? 'opacity-50' : ''}`}
    >
      <div
        {...attributes}
        {...listeners}
        className="cursor-grab active:cursor-grabbing text-muted hover:text-foreground transition-colors"
      >
        <GripVertical className="h-4 w-4" />
      </div>
      <div className="min-w-0 flex items-center gap-2">
        <span className={`truncate text-sm font-medium ${!hidden ? 'text-foreground' : 'text-muted line-through'}`}>
          {entry.label}
        </span>
        {hidden && (
          <span className="rounded bg-elevated px-1.5 py-0.5 text-[10px] text-muted shrink-0">已隱藏</span>
        )}
        <span className="truncate text-[11px] text-muted font-mono">{entry.id}</span>
      </div>
      <div>
        <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] bg-elevated text-muted">
          內建
        </span>
      </div>
      <div className="flex justify-center">
        <button
          onClick={() => onToggleHidden(entry.id)}
          className={`rounded p-1 transition-colors ${
            hidden
              ? 'text-muted hover:text-accent hover:bg-accent/10'
              : 'text-accent hover:bg-accent/10'
          }`}
          title={hidden ? '顯示' : '隱藏'}
        >
          {hidden ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
        </button>
      </div>
      <div className="flex justify-center">
        <Link
          to={entry.id}
          className="rounded p-1 text-muted hover:text-accent hover:bg-accent/10 transition-colors"
          title="開啟頁面"
        >
          <ExternalLink className="h-3.5 w-3.5" />
        </Link>
      </div>
      {/* 第 6 列: 徽標開關 (僅監控中心) */}
      <div className="flex justify-center">
        {onToggleBadge && (
          <button
            onClick={() => onToggleBadge(entry.id)}
            className={`rounded p-1 transition-colors ${
              badgeEnabled
                ? 'text-accent hover:bg-accent/10'
                : 'text-muted hover:text-accent hover:bg-accent/10'
            }`}
            title={badgeEnabled ? '關閉數字提示' : '開啟數字提示'}
          >
            <Bell className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
    </div>
  )
}

// ── Main panel ──

export function SettingsMenuSettingsPanel() {
  const qc = useQueryClient()
  const { data: prefs } = usePreferences()

  const allEntries = useMemo(() => {
    const saved = prefs?.nav_order ?? []
    const entryMap = new Map<string, NavEntry>()
    for (const e of BUILTIN_PAGES) entryMap.set(e.id, e)

    if (saved.length === 0) return [...BUILTIN_PAGES]

    const ordered: NavEntry[] = []
    const seen = new Set<string>()
    for (const id of saved) {
      const entry = entryMap.get(id)
      if (entry) {
        ordered.push(entry)
        seen.add(id)
      }
    }
    for (const e of BUILTIN_PAGES) {
      if (seen.has(e.id)) continue
      // 未保存過排序的新條目: 內置頁插回默認位置
      const defaultIndex = BUILTIN_PAGES.findIndex(p => p.id === e.id)
      let anchor = -1
      if (defaultIndex > 0) {
        for (let i = defaultIndex - 1; i >= 0 && anchor < 0; i -= 1) {
          anchor = ordered.findIndex(o => o.id === BUILTIN_PAGES[i].id)
        }
      }
      if (anchor >= 0) ordered.splice(anchor + 1, 0, e)
      else if (defaultIndex >= 0) ordered.unshift(e)
      else ordered.push(e)
    }
    return ordered
  }, [prefs?.nav_order])

  const hiddenSet = useMemo(() => new Set(prefs?.nav_hidden ?? []), [prefs?.nav_hidden])

  // Local order state for optimistic drag updates
  const [localOrder, setLocalOrder] = useState<string[] | null>(null)
  const orderedEntries = useMemo(() => {
    const order = localOrder ?? prefs?.nav_order ?? []
    if (!order.length) return allEntries
    const byId = new Map(allEntries.map(e => [e.id, e]))
    const result: NavEntry[] = []
    const seen = new Set<string>()
    for (const id of order) {
      const e = byId.get(id)
      if (e) { result.push(e); seen.add(id) }
    }
    for (const e of allEntries) {
      if (seen.has(e.id)) continue
      // 與 allEntries 同一語義: 未保存的新內置頁插回默認位置而非追加到末尾
      const defaultIndex = BUILTIN_PAGES.findIndex(p => p.id === e.id)
      let anchor = -1
      if (defaultIndex > 0) {
        for (let i = defaultIndex - 1; i >= 0 && anchor < 0; i -= 1) {
          anchor = result.findIndex(o => o.id === BUILTIN_PAGES[i].id)
        }
      }
      if (anchor >= 0) result.splice(anchor + 1, 0, e)
      else if (defaultIndex >= 0) result.unshift(e)
      else result.push(e)
    }
    return result
  }, [localOrder, prefs?.nav_order, allEntries])

  const saveNavOrder = useMutation({
    mutationFn: (order: string[]) => api.saveNavOrder(order),
    onSuccess: () => {
      setLocalOrder(null)
      qc.invalidateQueries({ queryKey: QK.preferences })
    },
  })

  const saveNavHidden = useMutation({
    mutationFn: (hidden: string[]) => api.saveNavHidden(hidden),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.preferences }),
  })

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event
    if (!over || active.id === over.id) return

    const ids = orderedEntries.map(e => e.id)
    const oldIdx = ids.indexOf(active.id as string)
    const newIdx = ids.indexOf(over.id as string)
    const reordered = arrayMove(ids, oldIdx, newIdx)
    setLocalOrder(reordered)
    saveNavOrder.mutate(reordered)
  }

  const toggleHidden = (id: string) => {
    const next = new Set(hiddenSet)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    saveNavHidden.mutate([...next])
  }

  // 監控中心徽標開關 (localStorage)
  const [badgeEnabled, setBadgeEnabled] = useState(() => {
    try { return localStorage.getItem('monitor_badge_enabled') !== '0' } catch { return true }
  })
  const toggleBadge = (id: string) => {
    if (id !== '/monitor') return
    const next = !badgeEnabled
    setBadgeEnabled(next)
    try { localStorage.setItem('monitor_badge_enabled', next ? '1' : '0') } catch { /* ignore */ }
  }

  return (
    <div className="max-w-5xl space-y-6">
      <section className="rounded-2xl border border-border bg-surface p-6 bg-[radial-gradient(circle_at_top_right,rgba(59,130,246,0.12),transparent_38%)]">
        <div className="text-[11px] uppercase tracking-[0.2em] text-accent/80">選單設定</div>
        <h2 className="mt-2 text-2xl font-semibold tracking-tight text-foreground">調整左側選單順序</h2>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-secondary">
          拖動左側把手調整選單排列順序,點擊眼睛圖示控制選單在側邊欄中的顯示或隱藏。
        </p>
      </section>

      <section className="rounded-card border border-border bg-surface overflow-hidden">
        <div className="grid grid-cols-[2.5rem_1fr_4.5rem_3rem_3rem_3rem] items-center border-b border-border px-4 py-2 text-[11px] text-muted">
          <div />
          <div>選單</div>
          <div>類型</div>
          <div className="text-center">顯示</div>
          <div className="text-center">設定</div>
          <div className="text-center">數字</div>
        </div>

        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={handleDragEnd}
        >
          <SortableContext
            items={orderedEntries.map(e => e.id)}
            strategy={verticalListSortingStrategy}
          >
            {orderedEntries.map((entry) => (
              <SortableItem
                key={entry.id}
                entry={entry}
                hidden={hiddenSet.has(entry.id)}
                onToggleHidden={toggleHidden}
                badgeEnabled={entry.id === '/monitor' ? badgeEnabled : undefined}
                onToggleBadge={entry.id === '/monitor' ? toggleBadge : undefined}
              />
            ))}
          </SortableContext>
        </DndContext>
      </section>
    </div>
  )
}

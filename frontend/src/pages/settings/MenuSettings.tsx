import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
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
import { Eye, EyeOff, ExternalLink, GripVertical, Settings, Bell, Globe2 } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { usePreferences } from '@/lib/useSharedQueries'
import { CORE_NAV, ASHARE_LEGACY_NAV } from '@/lib/navigation'

interface NavEntry {
  id: string
  label: string
  type: 'builtin' | 'analysis'
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
        <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] ${
          entry.type === 'analysis' ? 'bg-accent/10 text-accent' : 'bg-elevated text-muted'
        }`}>
          {entry.type === 'builtin' ? '內建' : '擴展'}
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
        {entry.type === 'builtin' ? (
          <Link
            to={entry.id}
            className="rounded p-1 text-muted hover:text-accent hover:bg-accent/10 transition-colors"
            title="開啟頁面"
          >
            <ExternalLink className="h-3.5 w-3.5" />
          </Link>
        ) : (
          <Link
            to={`/settings?tab=ext-pages`}
            className="rounded p-1 text-muted hover:text-accent hover:bg-accent/10 transition-colors"
            title="編輯擴展頁面"
          >
            <Settings className="h-3.5 w-3.5" />
          </Link>
        )}
      </div>
      {/* 第 6 列: 徽标开关 (仅监控中心) */}
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
  const menus = useQuery({ queryKey: QK.analysisMenus, queryFn: api.analysisMenus })

  const analysisEntries: NavEntry[] = (menus.data?.items ?? []).map(m => ({
    id: m.id,
    label: m.label,
    type: 'analysis' as const,
    visible: m.visible,
  }))

  const allEntries = useMemo(() => {
    const saved = prefs?.nav_order ?? []
    const entryMap = new Map<string, NavEntry>()
    for (const e of BUILTIN_PAGES) entryMap.set(e.id, e)
    for (const e of analysisEntries) entryMap.set(e.id, e)

    if (saved.length === 0) return [...BUILTIN_PAGES, ...analysisEntries]

    const ordered: NavEntry[] = []
    const seen = new Set<string>()
    for (const id of saved) {
      const entry = entryMap.get(id)
      if (entry) {
        ordered.push(entry)
        seen.add(id)
      }
    }
    for (const e of [...BUILTIN_PAGES, ...analysisEntries]) {
      if (seen.has(e.id)) continue
      // 未保存过排序的新条目: 内置页插回默认位置, 分析菜单追加到末尾
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
  }, [prefs?.nav_order, analysisEntries])

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
      // 与 allEntries 同一语义: 未保存的新内置页插回默认位置而非追加到末尾
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

  // Phase 8B-2.1 — 「中國 A 股功能」總開關。與 Layout.tsx sidebar 讀同一個
  // show_ashare_legacy_features 偏好, 不是第二套顯示邏輯。
  const showAshareLegacy = prefs?.show_ashare_legacy_features ?? false
  const saveShowAshareLegacy = useMutation({
    mutationFn: (enabled: boolean) => api.updateShowAshareLegacyFeatures(enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.preferences }),
  })

  // 监控中心徽标开关 (localStorage)
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

        {menus.isLoading && (
          <div className="px-5 py-10 text-center text-sm text-muted">正在載入選單…</div>
        )}
      </section>

      {/* Phase 8B-2.1 — 中國 A 股功能: 單一總開關 + 開啟後可個別隱藏,
          與 Layout.tsx sidebar 的「中國 A 股（選配）」區塊同步同一組偏好
          (show_ashare_legacy_features + nav_hidden), 不是獨立的顯示邏輯。 */}
      <section className="rounded-card border border-border bg-surface p-5">
        <div className="flex items-center gap-2 mb-1">
          <Globe2 className="h-4 w-4 text-accent" />
          <h3 className="text-sm font-medium text-foreground">中國 A 股功能</h3>
        </div>
        <p className="text-[11px] text-muted mb-4">
          原專案保留的中國 A 股分析功能，預設不顯示在側邊欄，開啟後才會出現「中國 A 股（選配）」區塊。台股功能不受影響。
        </p>

        <div className="flex items-center justify-between gap-4 py-2 border-b border-border/60">
          <div className="min-w-0">
            <div className="text-sm text-foreground">顯示中國 A 股功能</div>
            <div className="text-[11px] text-muted truncate">開啟後會顯示原專案保留的中國 A 股分析功能。台股功能不受影響。</div>
          </div>
          <button
            aria-label="顯示中國 A 股功能"
            onClick={() => saveShowAshareLegacy.mutate(!showAshareLegacy)}
            disabled={saveShowAshareLegacy.isPending}
            className={`relative inline-flex h-5 w-9 items-center rounded-full shrink-0 transition-colors duration-200 disabled:opacity-50 ${
              showAshareLegacy ? 'bg-accent' : 'bg-elevated'
            }`}
          >
            <span
              className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform duration-200 ${
                showAshareLegacy ? 'translate-x-[18px]' : 'translate-x-[3px]'
              }`}
            />
          </button>
        </div>

        {showAshareLegacy && (
          <div className="mt-1 divide-y divide-border/60">
            {ASHARE_LEGACY_NAV.map(item => {
              const hidden = hiddenSet.has(item.to)
              return (
                <div key={item.to} className="flex items-center justify-between gap-4 py-2.5">
                  <div className="flex items-center gap-2 min-w-0">
                    <item.icon className="h-3.5 w-3.5 text-muted shrink-0" />
                    <span className={`text-sm truncate ${hidden ? 'text-muted line-through' : 'text-foreground'}`}>
                      {item.label}
                    </span>
                    {hidden && (
                      <span className="rounded bg-elevated px-1.5 py-0.5 text-[10px] text-muted shrink-0">已隱藏</span>
                    )}
                  </div>
                  <button
                    onClick={() => toggleHidden(item.to)}
                    className={`rounded p-1 transition-colors shrink-0 ${
                      hidden
                        ? 'text-muted hover:text-accent hover:bg-accent/10'
                        : 'text-accent hover:bg-accent/10'
                    }`}
                    title={hidden ? '顯示' : '隱藏'}
                  >
                    {hidden ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
              )
            })}
          </div>
        )}
      </section>
    </div>
  )
}

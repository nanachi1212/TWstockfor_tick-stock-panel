import React, { useState, useCallback, useRef, useEffect, useMemo } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useVirtualizer } from '@tanstack/react-virtual'
import { motion, AnimatePresence } from 'framer-motion'
import { Trash2, RefreshCw, Star, X, Search, LayoutGrid, List, Rows3, BarChart3, Settings2, Plus, Check, Filter, Eye, EyeOff, Minus, ChevronsUp, Clock, RotateCcw, ImagePlus, FolderOpen, FolderMinus, FolderPlus, Scale } from 'lucide-react'
import { api, type KlineRow, type MinuteKlineRow, type WatchlistGroup, type WatchlistGroupColor } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { storage } from '@/lib/storage'
import { fmtPrice, fmtPct, fmtBigNum, priceColorClass, formatExtNumber } from '@/lib/format'
import { computeGroupPcts, loadGroupStatsConfig, type GroupStatsConfigPatch } from '@/lib/watchlistGroupStats'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState } from '@/components/EmptyState'
import { StockPreviewDialog } from '@/components/StockPreviewDialog'
import {
  DimensionMembersDialog,
  dimensionKindForSourceField,
  type DimensionMembersTarget,
} from '@/components/DimensionMembersDialog'
import { WatchlistImportDialog } from '@/components/WatchlistImportDialog'
import { WatchlistAddMenu } from '@/components/WatchlistAddMenu'
import {
  WatchlistGroupBar,
  WatchlistGroupPicker,
  type WatchlistGroupFilter,
} from '@/components/WatchlistGroups'
import { WatchlistGroupCards } from '@/components/WatchlistGroupCards'
import { WatchlistGroupStatsBar } from '@/components/WatchlistGroupStatsBar'
import { ExtensionSlot } from '@/extensions/ExtensionSlot'
import { MIN_COMPARE_SYMBOLS, MAX_COMPARE_SYMBOLS } from '@/lib/taiwanCompareSymbols'
import { DataQualityBadge } from '@/components/taiwan/TaiwanDataQuality'

// 分時列開放排序 (StockDataTable 實例級白名單; 表頭眼睛/刷新按鈕已 stopPropagation)
const INTRADAY_SORTABLE_KEYS = new Set(['intraday'])
import { getOcrInstallHint } from '@/lib/ocrInstallHint'
import { ColumnCustomizer } from '@/components/ColumnCustomizer'
import { StockDataTable } from '@/components/stock-table/StockDataTable'
import { VIRTUAL_LIST_THRESHOLD, useParentScroll } from '@/components/virtual-list/useParentScroll'
import { useTableSort } from '@/components/stock-table/useTableSort'
import { MiniCandlestick } from '@/components/stock-table/MiniCandlestick'
import { MiniIntraday } from '@/components/stock-table/MiniIntraday'
import { boardTag, renderBuiltinDataCell } from '@/components/stock-table/primitives'
import { getSignals, signalCls, getSortValue, getIntradaySortValue, UNSORTABLE_KEYS } from '@/lib/stock-table'
import { resolveCandleConfig, resolveIntradayConfig } from '@/lib/list-columns'
import { useQuoteStatus, useCapabilities, usePreferences } from '@/lib/useSharedQueries'
import {
  type ColumnConfig,
  BUILTIN_COLUMNS,
  COLUMN_GROUPS,
  loadColumnConfig,
  saveColumnConfig,
  buildExtColumnsParam,
} from '@/lib/watchlist-columns'

// ===== 板塊標識（篩選/卡片用） =====
// 注: boardTag（創/科/北 標籤）已移至共享 @/components/stock-table/primitives

const BOARDS = ['滬主板', '深主板', '創業板', '科創板', '北交所'] as const
type BoardType = typeof BOARDS[number]

function getBoardType(symbol: string): BoardType | null {
  if (/^(300|301)/.test(symbol)) return '創業板'
  if (/^688/.test(symbol))       return '科創板'
  if (/\.BJ$/.test(symbol))      return '北交所'
  if (/^60[0135]/.test(symbol))  return '滬主板'
  if (/^00[012]/.test(symbol))   return '深主板'
  return null
}

// ===== 換手率分檔色（卡片/表格用） =====

function turnoverColor(rate: number | null | undefined): string {
  if (rate == null || Number.isNaN(rate)) return 'text-[#888]'
  if (rate < 5)   return 'text-[#888]'
  if (rate < 10)  return 'text-[#d4a800]'
  if (rate < 20)  return 'text-[#f97316]'
  if (rate < 35)  return 'text-[#d94a3d]'
  return 'text-[#b84a8a]'
}

// ===== 動態列渲染 =====
// 表頭/單元格渲染已共享化：純數據列由 @/components/stock-table/primitives 的
// renderBuiltinDataCell 處理；symbol/signals/candle/ext 等需上下文的列由下方
// 表格 renderCell 回調處理。表格骨架使用 StockDataTable。

/** 渲染擴展數據列的值（含分隔/標籤/展開配置） */
function renderExtValue(
  val: any,
  col: ColumnConfig,
  expanded: boolean,
  onToggle: () => void,
  inline?: boolean,
  onTagClick?: (tag: string) => void,
): React.ReactNode {
  if (val == null || Number.isNaN(val)) return <span className="text-muted">—</span>
  if (typeof val === 'number') {
    // 數字格式化: 千分位 + 單位換算 + 小數位(由列配置控制)
    const cfg = col.extDisplay
    const hasNumFmt = cfg?.thousandSeparator || (cfg?.unitConvert && cfg.unitConvert !== 'none')
    const displayVal = hasNumFmt
      ? formatExtNumber(val, { thousandSeparator: cfg?.thousandSeparator, unitConvert: cfg?.unitConvert, unitDecimals: cfg?.unitDecimals })
      : (Number.isInteger(val) ? fmtPrice(val, 0) : fmtPrice(val))
    return <span className="tabular-nums">{displayVal}</span>
  }
  if (typeof val === 'boolean') {
    return <span className={val ? 'text-bull' : 'text-muted'}>{val ? '是' : '否'}</span>
  }

  // String — 按 extDisplay 配置渲染
  const cfg = col.extDisplay
  const str = String(val)

  // 純文本模式
  if (cfg?.displayMode === 'text') {
    return <span className="text-foreground">{str}</span>
  }

  // 標籤模式（默認）
  const separator = cfg?.separator?.trim() || null
  const tags = separator
    ? str.split(separator).map(s => s.trim()).filter(Boolean)
    : str.split(/[、,，;；\-]/).map(s => s.trim()).filter(Boolean)

  if (tags.length === 0) return <span className="text-muted">—</span>

  const maxTags = cfg?.maxTags ?? 0
  const showAll = maxTags <= 0 || expanded || tags.length <= maxTags
  const sliced = showAll ? tags : tags.slice(0, maxTags)
  const hiddenIndices = maxTags > 0 ? cfg?.hiddenIndices : undefined
  const visibleTags = hiddenIndices?.length
    ? sliced.filter((_, i) => !hiddenIndices.includes(i))
    : sliced
  const hiddenCount = tags.length - visibleTags.length

  // 豎向排列：僅在表格視圖、收起狀態、設定了顯示上限時生效
  const isVertical = !inline && cfg?.tagLayout === 'vertical' && !expanded

  const tagEls = (
    <>
      {visibleTags.map((tag, i) => onTagClick ? (
        <button
          key={i}
          type="button"
          onClick={event => { event.stopPropagation(); onTagClick(tag) }}
          className="inline-block px-1.5 py-px rounded text-[10px] font-medium leading-tight text-yellow-500 bg-yellow-500/10 hover:brightness-95"
        >
          {tag}
        </button>
      ) : (
        <span key={i} className="inline-block px-1.5 py-px rounded text-[10px] font-medium leading-tight text-yellow-500 bg-yellow-500/10">
          {tag}
        </span>
      ))}
      {!showAll && hiddenCount > 0 && (
        <button
          onClick={onToggle}
          className="inline-block px-1.5 py-px rounded text-[10px] font-medium leading-tight text-accent bg-accent/10 hover:bg-accent/20 transition-colors"
        >
          +{hiddenCount}
        </button>
      )}
      {showAll && maxTags > 0 && tags.length > maxTags && (
        <button
          onClick={onToggle}
          className="inline-block px-1.5 py-px rounded text-[10px] font-medium leading-tight text-muted hover:text-foreground transition-colors"
        >
          收起
        </button>
      )}
    </>
  )

  if (inline) {
    // 卡片視圖：返回 inline 片段
    return tagEls
  }
  // 表格視圖：用 <div> 包裹
  return <div className={isVertical ? 'flex flex-col items-start gap-0.5' : 'flex flex-wrap gap-0.5'}>{tagEls}</div>
}

/** 渲染擴展數據列的 <td> */
function renderExtCell(
  r: any,
  col: ColumnConfig,
  expandedCells: Set<string>,
  onToggleExpand: (key: string) => void,
  onDimensionClick: (target: DimensionMembersTarget) => void,
): React.ReactNode {
  if (col.source.type !== 'ext') return null
  const { configId, fieldName } = col.source
  const val = r[`${configId}__${fieldName}`]
  const cellKey = `${r.symbol}::${col.id}`
  const expanded = expandedCells.has(cellKey)
  const sourceField = `${configId}.${fieldName}`
  const dimensionKind = dimensionKindForSourceField(sourceField)

  const style: React.CSSProperties = {}
  if (col.extDisplay?.maxWidth) {
    style.maxWidth = col.extDisplay.maxWidth
  }

  // 根據值類型決定 td class
  const tdClass = val == null || Number.isNaN(val)
    ? 'px-2 py-1.5 text-right num tabular-nums text-muted'
    : typeof val === 'number'
      ? 'px-2 py-1.5 text-right num tabular-nums'
      : typeof val === 'boolean'
        ? 'px-2 py-1.5 text-right'
        : 'px-2 py-1.5'

  return (
    <td className={tdClass} style={style}>
      {renderExtValue(
        val,
        col,
        expanded,
        () => onToggleExpand(cellKey),
        false,
        dimensionKind ? value => onDimensionClick({ kind: dimensionKind, value, sourceField }) : undefined,
      )}
    </td>
  )
}

// ===== 搜索框組件（緊湊內聯式）=====

function StockSearchBox({
  onPreview,
  existingBySymbol,
  groups,
  onAdd,
  onToggleMember,
  preferredGroupId,
  addPending,
  memberPending,
}: {
  onPreview: (symbol: string, name: string) => void
  /** symbol -> 該標的當前所屬分組 id 列表; 不在 Map 中 = 未加自選 */
  existingBySymbol: Map<string, string[]>
  groups: WatchlistGroup[]
  onAdd: (symbol: string, groupId: string | null) => void
  onToggleMember: (symbol: string, groupId: string, member: boolean) => void
  preferredGroupId: string | null
  addPending: boolean
  memberPending: boolean
}) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const [activeIdx, setActiveIdx] = useState(-1)

  const search = useQuery({
    // Taiwan-only 產品方向: 自選搜索只查台股證券主檔(TaiwanSecurityMaster),
    // 不應再出現 A 股 .SH/.SZ/.BJ 結果。2330.TWSE / 6488.TPEX 這類標的可被搜到並加入自選。
    queryKey: QK.instrumentSearch(query, 'stock,etf,index', 'taiwan'),
    queryFn: () => api.instrumentSearch(query, 20, 'stock,etf,index', 'taiwan'),
    enabled: query.trim().length > 0,
    staleTime: 30_000,
  })

  const results = search.data?.results ?? []

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (e.target instanceof Element && e.target.closest('[data-watchlist-group-menu]')) return
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Escape') { setOpen(false); return }
    if (!open || results.length === 0) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIdx(i => Math.min(i + 1, results.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIdx(i => Math.max(i - 1, -1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (activeIdx >= 0) handleSelect(results[activeIdx])
      else if (results.length > 0) handleSelect(results[0])
    }
  }

  function handleSelect(r: { symbol: string; name: string }) {
    onPreview(r.symbol, r.name)
    setQuery('')
    setOpen(false)
    setActiveIdx(-1)
  }

  return (
    <div ref={containerRef} className="relative">
      <div className="relative flex items-center">
        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted pointer-events-none" />
        <input
          ref={inputRef}
          type="text"
          placeholder="搜尋…"
          value={query}
          onChange={(e) => { setQuery(e.target.value); setOpen(true); setActiveIdx(-1) }}
          onFocus={() => { if (query.trim()) setOpen(true) }}
          onKeyDown={handleKeyDown}
          className="w-44 h-8 pl-8 pr-2.5 rounded-btn bg-elevated border border-border text-xs text-foreground placeholder:text-muted focus:outline-none focus:border-accent/50 focus:w-56 transition-all duration-200"
        />
      </div>

      <AnimatePresence>
        {open && results.length > 0 && (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.12, ease: [0.16, 1, 0.3, 1] }}
            className="absolute right-0 top-full mt-1 z-50 w-72 max-h-[320px] overflow-y-auto rounded-card border border-border bg-base shadow-xl"
          >
            {results.map((r, i) => {
              const entryGids = existingBySymbol.get(r.symbol)
              const inWatchlist = entryGids !== undefined
              return (
                <div
                  key={r.symbol}
                  className={`flex items-center gap-2.5 px-3 py-2 text-xs transition-colors duration-100 ${
                    i === activeIdx ? 'bg-accent/10 text-accent' : 'hover:bg-elevated text-foreground'
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => handleSelect(r)}
                    className="flex items-center gap-2.5 flex-1 min-w-0 text-left"
                  >
                    <span className="font-mono shrink-0 w-[80px]">{r.symbol}</span>
                    {/* 名稱+標籤組: 標籤緊貼名稱文字, 而不是被 flex-1 推到行尾 */}
                    <span className="flex min-w-0 flex-1 items-center gap-1">
                      <span className="truncate text-secondary">{r.name}</span>
                      {r.asset_type === 'etf' && (
                        <span className="shrink-0 px-1 py-0.5 rounded text-[10px] leading-none bg-accent/10 text-accent">ETF</span>
                      )}
                      {r.asset_type === 'index' && (
                        <span className="shrink-0 px-1 py-0.5 rounded text-[10px] leading-none bg-sky-500/10 text-sky-400">指數</span>
                      )}
                      {(() => {
                        const b = boardTag(r.symbol)
                        return b && (
                          <span className={`shrink-0 px-1 py-0.5 rounded text-[10px] leading-none border ${b.color}`}>{b.label}</span>
                        )
                      })()}
                    </span>
                  </button>
                  {inWatchlist ? (
                    // 已加自選: 對勾標識 + 分組勾選面板, 可繼續加入/移出其他分組
                    // (走 members 端點, 不重排列表、不覆蓋備註)
                    <span className="flex shrink-0 items-center gap-1">
                      <span
                        className="inline-flex p-1 text-accent/70"
                        title="已加自選"
                        aria-label="已加自選"
                      >
                        <Check className="h-3.5 w-3.5" />
                      </span>
                      <WatchlistGroupPicker
                        groups={groups}
                        groupIds={entryGids}
                        symbol={r.symbol}
                        disabled={memberPending}
                        onToggleMember={onToggleMember}
                      />
                    </span>
                  ) : (
                    // 未加自選: + 一鍵加入當前分組頁籤 (全部/未分組頁簽下加為未分組);
                    // 文件夾圖標展開分組菜單, 顯式選擇目標分組
                    <span className="flex shrink-0 items-center gap-0.5">
                      <button
                        type="button"
                        onClick={event => { event.stopPropagation(); onAdd(r.symbol, preferredGroupId ?? null) }}
                        disabled={addPending}
                        className="shrink-0 rounded p-1 text-muted transition-colors hover:bg-accent/10 hover:text-accent disabled:opacity-50 cursor-pointer"
                        title={
                          preferredGroupId
                            ? `加入自選 · 目前分組「${groups.find(g => g.id === preferredGroupId)?.name ?? ''}」`
                            : '加入自選 (未分組)'
                        }
                        aria-label={`快速加入自選 ${r.symbol}`}
                      >
                        <Plus className="h-3.5 w-3.5" />
                      </button>
                      <WatchlistAddMenu
                        onSelect={groupId => onAdd(r.symbol, groupId)}
                        preferredGroupId={preferredGroupId}
                        disabled={addPending}
                        triggerClassName="shrink-0 rounded p-1 text-muted transition-colors hover:bg-accent/10 hover:text-accent disabled:opacity-50"
                        title="展開分組,選擇要加入的自選分組"
                      >
                        <FolderPlus className="h-3.5 w-3.5" />
                      </WatchlistAddMenu>
                    </span>
                  )}
                </div>
              )
            })}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

// ===== 實時監控圓點 =====
// 自選頁 symbol 列代碼後的小圓點, 標識該標的正在被實時行情監控 (Free/低檔按自選監控模式)。
// 視覺: 內圈實心點 + 外圈 animate-ping 擴散暈, 語義=「在線/活動」。
// 配色用 accent (電光藍) 而非綠/紅: 項目設計規範規定紅綠僅用於價格/K線,
// UI 狀態用 accent, 避免與 A 股漲跌色混淆。
// 全市場模式不顯示 —— 全部都在監控, 標記無信息量。
function RealtimeDot({ title = '即時監控中' }: { title?: string }) {
  return (
    <span
      title={title}
      className="relative inline-flex h-2 w-2 shrink-0"
      aria-label={title}
    >
      {/* 外圈: 擴散暈 (ping 動畫) */}
      <span className="absolute inline-flex h-full w-full rounded-full bg-accent/60 animate-ping motion-reduce:hidden" />
      {/* 內圈: 實心點 + 微輝光 */}
      <span className="relative inline-flex rounded-full h-2 w-2 bg-accent shadow-[0_0_5px_rgba(61,214,140,0.6)]" />
    </span>
  )
}

// ===== 卡片組件 =====

// 共享的空 K 線數組常量 — 避免每次渲染傳入新的 [] 破壞 StockCard 的 memo
const EMPTY_KLINE: KlineRow[] = []

function cardColumnCount(viewportWidth: number): number {
  if (viewportWidth >= 1536) return 6
  if (viewportWidth >= 1280) return 5
  if (viewportWidth >= 768) return 4
  if (viewportWidth >= 640) return 3
  return 2
}

function useCardColumnCount(): number {
  const [count, setCount] = useState(() => cardColumnCount(window.innerWidth))

  useEffect(() => {
    const update = () => setCount(cardColumnCount(window.innerWidth))
    window.addEventListener('resize', update)
    return () => window.removeEventListener('resize', update)
  }, [])

  return count
}

const StockCard = React.memo(function StockCard({
  r,
  candleRows,
  showCandle,
  onPreview,
  onConfirmRemove,
  onCancelRemove,
  onRequestRemove,
  isConfirming,
  extCols,
  expandedCells,
  onToggleExpand,
  onDimensionClick,
  isMonitored,
  groups,
  onToggleMember,
  groupChangePending,
}: {
  r: any
  candleRows: KlineRow[]
  showCandle: boolean
  onPreview: (symbol: string, name: string) => void
  onConfirmRemove: (symbol: string) => void
  onCancelRemove: () => void
  onRequestRemove: (symbol: string) => void
  isConfirming: boolean
  extCols: ColumnConfig[]
  expandedCells: Set<string>
  onToggleExpand: (key: string) => void
  onDimensionClick: (target: DimensionMembersTarget) => void
  isMonitored?: boolean
  groups: WatchlistGroup[]
  onToggleMember: (symbol: string, groupId: string, member: boolean) => void
  groupChangePending: boolean
}) {
  const board = boardTag(r.symbol)
  const price = r.rt_price ?? r.close
  const pct = r.rt_pct ?? r.change_pct
  const name = r.rt_name ?? r.name
  const signals = getSignals(r)
  const isUp = (pct ?? 0) > 0
  const isDown = (pct ?? 0) < 0

  // 動態背景漸變: 漲=紅底, 跌=綠底, 平=無色
  const bgGlow = isUp
    ? 'bg-gradient-to-br from-bull/[0.06] via-transparent to-bull/[0.02]'
    : isDown
      ? 'bg-gradient-to-br from-bear/[0.06] via-transparent to-bear/[0.02]'
      : ''
  // 左側指示條顏色
  const barColor = isUp ? 'bg-bull/70' : isDown ? 'bg-bear/70' : 'bg-muted/30'
  // 漲跌幅標籤背景
  const pctBg = isUp ? 'bg-bull/12 text-bull' : isDown ? 'bg-bear/12 text-bear' : 'bg-elevated text-secondary'

  return (
    <div
      className={`relative rounded-lg border border-border bg-surface hover:border-border/80 transition-all duration-200 group cursor-pointer overflow-hidden ${bgGlow}`}
      onClick={() => onPreview(r.symbol, name ?? '')}
    >
      {/* 左側彩色指示條 */}
      <div className={`absolute left-0 top-0 bottom-0 w-[3px] rounded-l-lg ${barColor}`} />

      {/* 分組與刪除入口 */}
      <div className="absolute top-1.5 right-1.5 z-10">
        {isConfirming ? (
          <div className="flex items-center gap-1" onClick={e => e.stopPropagation()}>
            <button
              onClick={() => onConfirmRemove(r.symbol)}
              className="px-1.5 py-0.5 rounded text-[10px] text-danger bg-danger/10 hover:bg-danger/20 transition-colors"
            >
              確認
            </button>
            <button onClick={() => onCancelRemove()} className="p-0.5 text-muted hover:text-foreground transition-colors">
              <X className="h-3 w-3" />
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-0.5" onClick={e => e.stopPropagation()}>
            <WatchlistGroupPicker
              groups={groups}
              groupIds={r.group_ids ?? []}
              symbol={r.symbol}
              disabled={groupChangePending}
              onToggleMember={onToggleMember}
            />
            <button
              onClick={() => onRequestRemove(r.symbol)}
              className="opacity-0 group-hover:opacity-100 text-muted hover:text-danger transition-all duration-150 p-0.5 rounded hover:bg-elevated"
              aria-label="移除"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        )}
      </div>

      {/* 卡片內容 */}
      <div className="pl-4 pr-2.5 pt-2.5 pb-0">
        {/* 第一行: 代碼 + 名稱 + 板塊標識 */}
        <div className="flex items-center gap-1.5 min-w-0 mb-2 pr-8">
          <span className="shrink-0 font-mono text-foreground text-xs tracking-wide">
            {r.symbol}
          </span>
          {name && (
            <span className="text-xs text-secondary truncate">{name}</span>
          )}
          {board && (
            <span className={`shrink-0 inline-flex items-center justify-center px-1 h-[16px] rounded text-[9px] font-bold leading-none ${board.color}`}>
              {board.label}
            </span>
          )}
          {r.consecutive_limit_ups > 0 && (
            <span className="shrink-0 inline-flex items-center justify-center px-1 h-[16px] rounded bg-danger/15 text-danger text-[9px] font-bold tabular-nums">
              {r.consecutive_limit_ups === 1 ? '首板' : `${r.consecutive_limit_ups}連`}
            </span>
          )}
          {isMonitored && <span className="ml-auto"><RealtimeDot /></span>}
        </div>

        {/* 第二行: 大價格 + 漲跌幅膠囊 */}
        <div className="flex items-end justify-between gap-2 mb-2">
          <span className={`text-xl tabular-nums tracking-tighter leading-none ${priceColorClass(pct)}`}>
            {fmtPrice(price)}
          </span>
          {pct != null && (
            <span className={`shrink-0 inline-flex items-center px-1.5 py-[2px] rounded text-[11px] tabular-nums ${pctBg}`}>
              {fmtPct(pct)}
            </span>
          )}
        </div>

        {/* 第三行: 指標 */}
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[10px] text-muted leading-relaxed">
          <span title="換手率">換手<span className={`font-mono ml-0.5 ${turnoverColor(r.turnover_rate)}`}>{r.turnover_rate != null ? `${r.turnover_rate.toFixed(2)}%` : '—'}</span></span>
          <span title="量比">量比<span className="font-mono ml-0.5">{fmtPrice(r.vol_ratio_5d)}</span></span>
          <span title="RSI14">RSI<span className="font-mono ml-0.5">{r.rsi_14 != null ? r.rsi_14.toFixed(1) : '—'}</span></span>
          {/* 擴展數據列展示在卡片中 */}
          {extCols.map(col => {
            if (col.source.type !== 'ext') return null
            const { configId, fieldName } = col.source
            const val = r[`${configId}__${fieldName}`]
            if (val == null) return null

            const cellKey = `${r.symbol}::${col.id}`
            const expanded = expandedCells.has(cellKey)
            const sourceField = `${configId}.${fieldName}`
            const dimensionKind = dimensionKindForSourceField(sourceField)

            return (
              <span key={col.id} title={col.label}>
                <span className="text-secondary">{col.label}</span>
                <span className="font-mono ml-0.5">
                  {renderExtValue(
                    val,
                    col,
                    expanded,
                    () => onToggleExpand(cellKey),
                    true,
                    dimensionKind ? value => onDimensionClick({ kind: dimensionKind, value, sourceField }) : undefined,
                  )}
                </span>
              </span>
            )
          })}
        </div>
      </div>

      {/* 信號標籤區 */}
      {signals.length > 0 && (
        <div className="pl-4 pr-2.5 pt-1.5 pb-2 flex flex-wrap gap-1">
          {signals.slice(0, 3).map(s => (
            <span key={s.label} className={`inline-block px-1.5 py-[1px] rounded text-[9px] font-medium leading-tight ${signalCls(s.type)}`}>
              {s.label}
            </span>
          ))}
          {signals.length > 3 && (
            <span className="inline-block px-1 py-[1px] rounded text-[9px] text-muted bg-elevated leading-tight">
              +{signals.length - 3}
            </span>
          )}
        </div>
      )}

      {/* 迷你蠟燭圖 */}
      {showCandle && candleRows.length > 0 && (
        <div className="border-t border-border/40 px-3 py-1.5">
          <MiniCandlestick rows={candleRows} height={32} />
        </div>
      )}
    </div>
  )
})

// ===== 主頁面 =====

export function Watchlist() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [viewMode, setViewMode] = useState<'table' | 'card'>(() => {
    return (storage.watchlistView.get('table') as 'table' | 'card')
  })
  // Phase 8C-A: 選 2–5 檔直接比較 — 會話內選取狀態, 不持久化(離開頁面即清空,
  // 與比較頁本身的 URL-as-truth 選取狀態是兩件事; 這裡只負責「把選好的帶過去」)。
  const [selectedForCompare, setSelectedForCompare] = useState<Set<string>>(new Set())
  const toggleCompareSelection = useCallback((symbol: string) => {
    setSelectedForCompare(prev => {
      const next = new Set(prev)
      if (next.has(symbol)) {
        next.delete(symbol)
      } else if (next.size < MAX_COMPARE_SYMBOLS) {
        next.add(symbol)
      }
      return next
    })
  }, [])
  const handleCompareSelected = () => {
    if (selectedForCompare.size < MIN_COMPARE_SYMBOLS) return
    navigate(`/stocks/compare?symbols=${encodeURIComponent(Array.from(selectedForCompare).join(','))}`)
  }
  // 分組卡片總覽: 臨時整頁模式, 不持久化; 關閉(含刷新)後回到原視圖設置
  const [groupCardsOpen, setGroupCardsOpen] = useState(false)
  // 分組統計條: 頂部圖形化分組漲跌概覽, 會話內開關, 不影響個股視圖設置
  const [groupStatsOpen, setGroupStatsOpen] = useState(false)
  const [dailyKChartVisible, setDailyKChartVisible] = useState(() => {
    return storage.watchlistCandle.get(true)
  })
  const [intradayChartVisible, setIntradayChartVisible] = useState(() => {
    return storage.watchlistIntraday.get(true)
  })

  // 列配置 — 從後端/localStorage 異步加載
  const [columns, setColumns] = useState<ColumnConfig[]>([...BUILTIN_COLUMNS])
  const [customizerOpen, setCustomizerOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [searchParams] = useSearchParams()
  const initialGroup = (searchParams.get('group') as WatchlistGroupFilter | null) ?? 'all'
  const [selectedGroup, setSelectedGroup] = useState<WatchlistGroupFilter>(initialGroup)
  // URL ?group= 變化時同步選中分組 (側邊欄二級菜單切換分組時觸發)
  useEffect(() => {
    const g = (searchParams.get('group') as WatchlistGroupFilter | null) ?? 'all'
    setSelectedGroup(g)
  }, [searchParams])
  const [ocrAvailable, setOcrAvailable] = useState<boolean | null>(null)
  const [ocrInstallHint, setOcrInstallHint] = useState('')
  const columnsLoaded = useRef(false)

  useEffect(() => {
    if (columnsLoaded.current) return
    columnsLoaded.current = true
    loadColumnConfig().then(setColumns)
  }, [])

  useEffect(() => {
    let cancelled = false
    void api.watchlistOcrStatus().then(
      res => {
        if (cancelled) return
        setOcrAvailable(res.available)
        if (!res.available) setOcrInstallHint(getOcrInstallHint())
      },
      () => {
        if (cancelled) return
        setOcrAvailable(false)
        setOcrInstallHint(getOcrInstallHint())
      },
    )
    return () => {
      cancelled = true
    }
  }, [])

  const handleColumnsChange = useCallback((next: ColumnConfig[]) => {
    setColumns(next)
    saveColumnConfig(next)
  }, [])

  const candleColumn = useMemo(() =>
    columns.find(c => c.source.type === 'builtin' && c.source.key === 'candle' && c.visible),
    [columns],
  )
  const candleColumnEnabled = !!candleColumn
  // 日k列渲染配置（來自列定製，已鉗制邊界）
  const candleResolved = useMemo(() => resolveCandleConfig(candleColumn?.candleConfig), [candleColumn])
  const candleDays = candleResolved.days
  const candleSize = dailyKChartVisible
    ? { width: candleResolved.enabledWidth, height: candleResolved.enabledHeight }
    : { width: candleResolved.disabledWidth, height: candleResolved.disabledHeight }

  const dailyKVisible = candleColumnEnabled && dailyKChartVisible

  // 分時列檢測: 用戶開啟且列可見時才拉數據
  const intradayColumn = useMemo(() =>
    columns.find(c => c.source.type === 'builtin' && c.source.key === 'intraday' && c.visible),
    [columns],
  )
  // 分時列渲染配置（寬高, 來自列定製, 已鉗制邊界）
  const intradayResolved = useMemo(() => resolveIntradayConfig(intradayColumn?.intradayConfig), [intradayColumn])
  // 分時圖依賴分鐘K批量數據 (kline.minute.batch), 無數據時開了列也不拉
  const caps = useCapabilities()
  const hasMinuteBatch = !!caps.data?.capabilities?.['kline.minute.batch']
  const intradayVisible = !!intradayColumn && hasMinuteBatch && intradayChartVisible

  // 計算可見列（列是否出現只由自定義列配置決定）
  const visibleColumns = useMemo(() => {
    return columns.filter(c => c.visible)
  }, [columns])

  // 計算 ext 列參數
  const extColumnsParam = useMemo(() => buildExtColumnsParam(columns), [columns])

  const toggleView = useCallback(() => {
    setGroupCardsOpen(false)
    setViewMode(v => {
      const next = v === 'table' ? 'card' : 'table'
      storage.watchlistView.set(next)
      return next
    })
  }, [])
  // 分組卡片: 整頁臨時展示, 開關不觸碰個股視圖設置
  const toggleGroupView = useCallback(() => {
    setGroupCardsOpen(open => !open)
  }, [])
  const toggleGroupStats = useCallback(() => {
    setGroupStatsOpen(open => !open)
  }, [])
  const toggleDailyKChart = useCallback(() => {
    setDailyKChartVisible(v => {
      const next = !v
      storage.watchlistCandle.set(next)
      return next
    })
  }, [])
  const toggleIntradayChart = useCallback(() => {
    setIntradayChartVisible(v => {
      const next = !v
      storage.watchlistIntraday.set(next)
      return next
    })
  }, [])
  const [previewSymbol, setPreviewSymbol] = useState<string | null>(null)
  const [previewName, setPreviewName] = useState<string>('')
  const [dimensionTarget, setDimensionTarget] = useState<DimensionMembersTarget | null>(null)
  const [expandedCells, setExpandedCells] = useState<Set<string>>(new Set())
  const closePreview = useCallback(() => {
    setPreviewSymbol(null)
    setPreviewName('')
  }, [])

  const handleToggleExpand = useCallback((cellKey: string) => {
    setExpandedCells(prev => {
      const next = new Set(prev)
      if (next.has(cellKey)) next.delete(cellKey)
      else next.add(cellKey)
      return next
    })
  }, [])

  const list = useQuery({
    queryKey: QK.watchlist,
    queryFn: api.watchlistList,
  })

  const groupList = useQuery({
    queryKey: QK.watchlistGroups,
    queryFn: api.watchlistGroups,
  })
  const groups = groupList.data?.groups ?? []
  const activeGroupId = selectedGroup === 'all' || selectedGroup === 'ungrouped'
    ? null
    : selectedGroup

  useEffect(() => {
    if (
      selectedGroup !== 'all'
      && selectedGroup !== 'ungrouped'
      && groupList.isSuccess
      && !groups.some(group => group.id === selectedGroup)
    ) {
      setSelectedGroup('all')
    }
  }, [groupList.isSuccess, groups, selectedGroup])

  // enriched 數據 — 傳入 ext_columns 參數
  const enriched = useQuery({
    queryKey: QK.watchlistEnriched(extColumnsParam),
    queryFn: () => api.watchlistEnriched(extColumnsParam || undefined),
    enabled: (list.data?.symbols.length ?? 0) > 0,
  })

  const symbols = enriched.data?.rows?.map((r: any) => r.symbol) ?? []
  const symbolsKey = symbols.join(',')

  // 指數無本地分鐘K數據, 分時批量請求剔除指數 symbol (省請求, 避免逐只 404)
  const minuteSymbols = useMemo(
    () => symbols.filter((s: string) => (enriched.data?.rows ?? []).find((r: any) => r.symbol === s)?.asset_type !== 'index'),
    [symbols, enriched.data],
  )
  const minuteSymbolsKey = minuteSymbols.join(',')

  // 實時行情狀態 (提前到此處: 分時輪詢判斷需要 realtimeRunning)
  const quoteStatus = useQuoteStatus()
  const realtimeRunning = quoteStatus.data?.running ?? false

  // 批量日k數據 (天數由列配置決定; 分組卡片視圖不展示蠟燭, 掛起請求)
  const klineBatch = useQuery({
    queryKey: QK.watchlistKlineBatch(`${symbolsKey}|${candleDays}`),
    queryFn: () => api.klineDailyBatch(symbols, candleDays),
    enabled: dailyKVisible && symbols.length > 0 && !groupCardsOpen,
    staleTime: 5 * 60_000,  // 5 分鐘內不重請求
  })

  // 當日蠟燭實時修補: 歷史 K 線按 staleTime 週期拉取 (見 queryKeys 註釋), 最後一根
  // 蠟燭用每 tick 刷新的 enriched 當日 OHLC 前端覆蓋/追加, 蠟燭隨實時行情跳動, 零額外請求。
  const klineData = useMemo(() => {
    const base = dailyKVisible ? (klineBatch.data?.data ?? {}) : {}
    const liveRows = enriched.data?.rows
    const asOf = enriched.data?.as_of
    if (!dailyKVisible || !liveRows?.length || !asOf) return base
    const liveBySymbol = new Map<string, any>(liveRows.map((r: any) => [r.symbol, r]))
    const patched: Record<string, KlineRow[]> = {}
    for (const sym of Object.keys(base)) {
      const arr = base[sym]
      if (!Array.isArray(arr) || arr.length === 0) { patched[sym] = arr; continue }
      const live = liveBySymbol.get(sym)
      const { open, high, low, close } = live ?? {}
      if (open == null || high == null || low == null || close == null) { patched[sym] = arr; continue }
      const last = arr[arr.length - 1]
      if (last.date === asOf) {
        patched[sym] = [...arr.slice(0, -1), { ...last, open, high, low, close }]
      } else if (last.date < asOf) {
        patched[sym] = [...arr, { date: asOf, open, high, low, close }]
      } else {
        patched[sym] = arr
      }
    }
    return patched
  }, [dailyKVisible, klineBatch.data, enriched.data])

  // 批量分時數據 (有分鐘K批量能力時, 列可見才拉)
  // 刷新策略: 僅當實時行情運行 且 用戶在實時監控設置裡開啟 minute_intraday_refresh 時
  // 按用戶設定的間隔輪詢 (不接 SSE 高頻, 避免每秒拉 TickFlow 觸限流); 與 Screener / 設置卡片描述一致。
  const { data: prefsData } = usePreferences()
  const intradayRefreshEnabled = prefsData?.minute_intraday_refresh ?? false
  const intradayRefreshInterval = prefsData?.minute_intraday_refresh_interval ?? 6
  const minuteBatch = useQuery({
    queryKey: QK.minuteBatch(minuteSymbolsKey),
    queryFn: () => api.klineMinuteBatch(minuteSymbols),
    enabled: intradayVisible && minuteSymbols.length > 0 && !groupCardsOpen,
    staleTime: 10_000,
    refetchInterval: (intradayRefreshEnabled && realtimeRunning) ? intradayRefreshInterval * 1000 : false,
  })
  const minuteData = intradayVisible ? (minuteBatch.data?.data ?? {}) : {}

  const addMutation = useMutation({
    mutationFn: ({ symbol, groupId }: { symbol: string; groupId: string | null }) =>
      api.watchlistAdd(symbol, '', groupId),
    onSuccess: (data) => {
      qc.setQueryData(QK.watchlist, data)
      qc.invalidateQueries({ queryKey: QK.watchlist })
      qc.invalidateQueries({ queryKey: ['watchlist-enriched'] })
      qc.invalidateQueries({ queryKey: ['kline-batch'] })
    },
  })

  const remove = useMutation({
    mutationFn: (sym: string) => api.watchlistRemove(sym),
    onSuccess: (_data, sym) => {
      // 1. 立即從 enriched 緩存中移除該股票，UI 即時更新
      qc.setQueryData(['watchlist-enriched', extColumnsParam], (old: any) => {
        if (!old?.rows) return old
        return { ...old, rows: old.rows.filter((r: any) => r.symbol !== sym) }
      })
      // 2. 清除 list 緩存，觸發後台 refetch
      qc.invalidateQueries({ queryKey: QK.watchlist })
      qc.invalidateQueries({ queryKey: ['watchlist-enriched'] })
      qc.invalidateQueries({ queryKey: ['kline-batch'] })
    },
  })

  const moveToTop = useMutation({
    mutationFn: (sym: string) => api.watchlistMoveToTop(sym),
    onSuccess: (data) => {
      qc.setQueryData(QK.watchlist, data)
      qc.invalidateQueries({ queryKey: QK.watchlist })
      qc.invalidateQueries({ queryKey: ['watchlist-enriched'] })
      qc.invalidateQueries({ queryKey: ['kline-batch'] })
      qc.invalidateQueries({ queryKey: QK.preferences })
      qc.invalidateQueries({ queryKey: QK.quoteStatus })
    },
  })

  const clearAll = useMutation({
    mutationFn: () => api.watchlistClear(),
    onSuccess: () => {
      setConfirmClear(false)
      // 立即清空 enriched 緩存
      qc.setQueryData(['watchlist-enriched', extColumnsParam], { rows: [], as_of: null, elapsed_ms: 0 })
      qc.invalidateQueries({ queryKey: QK.watchlist })
      qc.invalidateQueries({ queryKey: ['watchlist-enriched'] })
      qc.invalidateQueries({ queryKey: ['kline-batch'] })
    },
  })

  const createGroup = useMutation({
    mutationFn: ({ name, color }: { name: string; color: WatchlistGroupColor }) =>
      api.watchlistGroupCreate(name, color),
    onSuccess: data => {
      qc.setQueryData(QK.watchlistGroups, { groups: data.groups })
      setSelectedGroup(data.group.id)
    },
  })

  const renameGroup = useMutation({
    mutationFn: ({ groupId, name, color }: { groupId: string; name: string; color: WatchlistGroupColor }) =>
      api.watchlistGroupRename(groupId, name, color),
    onSuccess: data => qc.setQueryData(QK.watchlistGroups, data),
  })

  const reorderGroup = useMutation({
    mutationFn: (orderedIds: string[]) => api.watchlistGroupReorder(orderedIds),
    onSuccess: data => qc.setQueryData(QK.watchlistGroups, data),
  })

  const deleteGroup = useMutation({
    mutationFn: (groupId: string) => api.watchlistGroupDelete(groupId),
    onSuccess: (data, groupId) => {
      qc.setQueryData(QK.watchlistGroups, { groups: data.groups })
      qc.setQueryData(QK.watchlist, { symbols: data.symbols })
      if (selectedGroup === groupId) setSelectedGroup('all')
    },
  })

  const clearGroup = useMutation({
    mutationFn: (groupId: string) => api.watchlistGroupClear(groupId),
    onSuccess: data => qc.setQueryData(QK.watchlist, data),
  })

  // 多組並存: 勾選加入 / 取消移出 (僅影響該分組, 標的保留在自選中)
  const addGroupMember = useMutation({
    mutationFn: ({ symbol, groupId }: { symbol: string; groupId: string }) =>
      api.watchlistGroupAddMember(groupId, symbol),
    onSuccess: data => qc.setQueryData(QK.watchlist, data),
  })
  const removeGroupMember = useMutation({
    mutationFn: ({ symbol, groupId }: { symbol: string; groupId: string }) =>
      api.watchlistGroupRemoveMember(groupId, symbol),
    onSuccess: data => qc.setQueryData(QK.watchlist, data),
  })

  // 二次確認狀態
  const [confirmClear, setConfirmClear] = useState(false)
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null)

  // 穩定的 per-symbol 回調 (供 memo 化的 StockCard 使用, 避免每次渲染都傳新引用)
  const handleCardPreview = useCallback((sym: string, name: string) => {
    setPreviewSymbol(sym); setPreviewName(name)
  }, [])
  const handleCardConfirmRemove = useCallback((sym: string) => {
    remove.mutate(sym); setConfirmRemove(null)
  }, [remove])
  const handleCardCancelRemove = useCallback(() => setConfirmRemove(null), [])
  const handleCardRequestRemove = useCallback((sym: string) => setConfirmRemove(sym), [])
  const handleToggleMember = useCallback((symbol: string, groupId: string, member: boolean) => {
    if (member) addGroupMember.mutate({ symbol, groupId })
    else removeGroupMember.mutate({ symbol, groupId })
  }, [addGroupMember, removeGroupMember])
  // 分組卡片總覽下點擊分組 tab / 卡片頭 = 鑽取該分組: 關閉總覽並選中分組,
  // 個股視圖設置(table/card)保持用戶原選擇
  const handleGroupSelect = useCallback((group: WatchlistGroupFilter) => {
    setSelectedGroup(group)
    setGroupCardsOpen(false)
  }, [])

  const listEntries = list.data?.symbols ?? []
  const allSymbols = listEntries.map(s => s.symbol)
  const rows = enriched.data?.rows ?? []
  const groupBySymbol = useMemo(
    () => new Map(listEntries.map(entry => [entry.symbol, entry.group_ids ?? []])),
    [listEntries],
  )
  // 分組等權平均漲跌幅 (實時優先 rt_pct, 收盤兜底 change_pct; 與表格同源)
  const groupPcts = useMemo(
    () => computeGroupPcts(
      listEntries,
      new Map(rows.map((r: any) => [r.symbol as string, r])),
    ),
    [listEntries, rows],
  )
  // 分組「指標 + 排序 + 卡片顯示項」配置: 分組統計條與分組卡片共享同一份持久化設置
  const [groupStatsConfig, setGroupStatsConfig] = useState(loadGroupStatsConfig)
  const updateGroupStatsConfig = useCallback((patch: GroupStatsConfigPatch) => {
    setGroupStatsConfig(prev => {
      const next = { ...prev, ...patch }
      storage.watchlistGroupStats.set(next)
      return next
    })
  }, [])
  const groupCounts = useMemo(() => {
    // 多組並存: 一股計入每個所屬分組的計數; 不屬於任何分組才計未分組
    const counts: Record<string, number> = { ungrouped: 0 }
    for (const entry of listEntries) {
      const gids = entry.group_ids ?? []
      if (gids.length === 0) counts.ungrouped += 1
      else for (const gid of gids) counts[gid] = (counts[gid] ?? 0) + 1
    }
    return counts
  }, [listEntries])
  const rowsInSelectedGroup = useMemo(() => {
    const rowsWithGroup = rows.map(row => ({ ...row, group_ids: groupBySymbol.get(row.symbol) ?? [] }))
    if (selectedGroup === 'all') return rowsWithGroup
    if (selectedGroup === 'ungrouped') return rowsWithGroup.filter(row => row.group_ids.length === 0)
    return rowsWithGroup.filter(row => row.group_ids.includes(selectedGroup))
  }, [groupBySymbol, rows, selectedGroup])
  const activeGroup = activeGroupId
    ? groups.find(group => group.id === activeGroupId)
    : undefined
  const watchlistContentLoading = list.isLoading || (allSymbols.length > 0 && enriched.isLoading)

  // 實時監控圓點: 僅 Free/低檔 "按自選股實時監控" 模式 (mode === 'watchlist') 下顯示;
  // 全市場模式 (mode === 'full_market') 全部標的都在監控, 標圓點無意義, 故不顯示。
  // 後端自選實時模式實際只監控自選頁前 N 個 (N = watchlist_symbol_count), 順序與 allSymbols 一致。
  const realtimeMode = quoteStatus.data?.mode
  const watchlistMonitoredCount = quoteStatus.data?.watchlist_symbol_count ?? 0
  const showRealtimeDot = realtimeRunning && realtimeMode === 'watchlist'
  // 真正被監控的標的集合 (自選列表前 watchlistMonitoredCount 個)
  const monitoredSymbols = useMemo(
    () => showRealtimeDot ? new Set(allSymbols.slice(0, watchlistMonitoredCount)) : new Set<string>(),
    [showRealtimeDot, allSymbols, watchlistMonitoredCount],
  )

  // ===== 篩選 =====
  const [filterOpen, setFilterOpen] = useState(false)
  const [filters, setFilters] = useState<Record<string, { min?: string; max?: string; text?: string }>>({})

  // 板塊篩選（持久化）
  const [boardFilter, setBoardFilter] = useState<Set<string>>(() => {
    const saved = storage.watchlistBoardFilter.get([])
    return saved.length > 0 ? new Set(saved) : new Set(BOARDS) // 預設全選
  })
  const persistBoardFilter = useCallback((next: Set<string>) => {
    setBoardFilter(next)
    storage.watchlistBoardFilter.set([...next])
  }, [])

  const toggleBoard = useCallback((board: string) => {
    setBoardFilter(prev => {
      const next = new Set(prev)
      if (next.has(board)) next.delete(board)
      else next.add(board)
      persistBoardFilter(next)
      return next
    })
  }, [persistBoardFilter])

  // 排除 ST (含 *ST/S*ST 等變體, 按簡稱含 "ST" 判定), 默認關閉並持久化
  const [excludeST, setExcludeST] = useState(() => storage.watchlistExcludeST.get(false))
  const toggleExcludeST = useCallback(() => {
    setExcludeST(prev => {
      storage.watchlistExcludeST.set(!prev)
      return !prev
    })
  }, [])

  const updateFilter = useCallback((colId: string, patch: { min?: string; max?: string; text?: string }) => {
    setFilters(prev => {
      const next = { ...prev }
      const existing = next[colId] || {}
      const merged = { ...existing, ...patch }
      if (!merged.min && !merged.max && !merged.text) {
        delete next[colId]
      } else {
        next[colId] = merged
      }
      return next
    })
  }, [])

  const resetAllFilters = useCallback(() => {
    setFilters({})
    persistBoardFilter(new Set(BOARDS))
    setExcludeST(false)
    storage.watchlistExcludeST.set(false)
  }, [persistBoardFilter])

  // 可篩選的內置列
  const filterableBuiltinCols = useMemo(
    () => columns.filter(c => c.source.type === 'builtin' && !UNSORTABLE_KEYS.has(c.source.key) && c.id !== 'builtin:symbol'),
    [columns],
  )

  // 按類別索引（複用列配置的分組定義）
  const colsByCategory = useMemo(() => {
    const map: Record<string, { id: string; label: string; col: typeof filterableBuiltinCols[number] }[]> = {}
    for (const cat of COLUMN_GROUPS) {
      map[cat.label] = []
      for (const key of cat.keys) {
        const col = filterableBuiltinCols.find(c => c.source.type === 'builtin' && c.source.key === key)
        if (col) map[cat.label].push({ id: col.id, label: col.label, col })
      }
    }
    return map
  }, [filterableBuiltinCols])

  // 篩選 + 排序
  const filteredRows = useMemo(() => {
    // 板塊篩選（全選時跳過）
    let result = rowsInSelectedGroup
    if (boardFilter.size > 0 && boardFilter.size < BOARDS.length) {
      result = result.filter(r => {
        // 非股票 (指數/ETF) 無板塊語義, 不受板塊篩選影響 (順帶修復 ETF 行被誤過濾)
        if (r.asset_type && r.asset_type !== 'stock') return true
        const board = getBoardType(r.symbol)
        return board != null && boardFilter.has(board)
      })
    }
    // 排除 ST: 按簡稱判定 (ST/*ST/S*ST 均含 "ST"); 非股票名稱不含該標記, 天然不受影響
    if (excludeST) {
      result = result.filter(r => !((r.rt_name ?? r.name ?? '').toUpperCase().includes('ST')))
    }
    // 數值/文本篩選
    const activeFilters = Object.entries(filters).filter(([, v]) => v.min || v.max || v.text)
    if (activeFilters.length > 0) {
      result = result.filter(r => {
        for (const [colId, f] of activeFilters) {
          const col = columns.find(c => c.id === colId)
          if (!col) continue
          const val = getSortValue(r, col)
          if (val == null) return false
          if (typeof val === 'number') {
            if (f.min && val < Number(f.min)) return false
            if (f.max && val > Number(f.max)) return false
          } else {
            if (f.text && !String(val).includes(f.text)) return false
          }
        }
        return true
      })
    }
    return result
  }, [rowsInSelectedGroup, filters, columns, boardFilter, excludeST])

  const activeFilterCount = Object.values(filters).filter(v => v.min || v.max || v.text).length
  const hasBoardFilter = boardFilter.size > 0 && boardFilter.size < BOARDS.length
  const hasActiveFilters = activeFilterCount > 0 || hasBoardFilter || excludeST

  // 排序（複用共享三態排序 hook）。分時列按「最新分鐘收盤 vs 昨收」排序（分時圖最後一點同口徑），
  // 其餘列走共享取值；眼睛關閉時不拉分鐘數據，取值為 null → 保持原序。
  const getWatchlistSortValue = useCallback((r: any, col: ColumnConfig) => {
    if (col.source.type === 'builtin' && col.source.key === 'intraday') {
      return getIntradaySortValue(r, minuteData[r.symbol])
    }
    return getSortValue(r, col)
  }, [minuteData])
  const { sort, toggle: handleSortToggle, sortRows } = useTableSort(getWatchlistSortValue)

  const sortedRows = useMemo(
    () => sortRows(filteredRows, columns),
    [filteredRows, sortRows, columns],
  )

  const cardColumns = useCardColumnCount()
  const cardGridRef = useRef<HTMLDivElement>(null)
  const virtualizeCards = viewMode === 'card' && !groupCardsOpen && sortedRows.length > VIRTUAL_LIST_THRESHOLD
  const cardRowCount = Math.ceil(sortedRows.length / cardColumns)
  const { getScrollElement: getCardScrollElement, scrollMargin: cardScrollMargin } = useParentScroll(
    cardGridRef,
    virtualizeCards,
  )
  const cardRowVirtualizer = useVirtualizer({
    count: virtualizeCards ? cardRowCount : 0,
    getScrollElement: getCardScrollElement,
    estimateSize: () => dailyKVisible ? 180 : 140,
    getItemKey: index => `${cardColumns}:${(sortedRows[index * cardColumns] as any)?.symbol ?? index}`,
    gap: 12,
    overscan: 3,
    scrollMargin: cardScrollMargin,
  })

  // 可見的 ext 列（卡片視圖使用）
  const visibleExtCols = useMemo(
    () => visibleColumns.filter(c => c.source.type === 'ext'),
    [visibleColumns]
  )

  // "數據未就緒" 的個股數: 後端 LEFT JOIN 保證返回所有自選行,
  // 指標全為 null 的行屬於 enriched 緩存未覆蓋 (新股/冷門/新用戶未同步), 非篩選導致.
  // 用 close 是否為 null/undefined 判斷 "整行指標缺失" (close 是 enriched 最基礎字段).
  const pendingCount = useMemo(
    () => sortedRows.filter((r: any) => r.close == null).length,
    [sortedRows],
  )

  // "被篩選條件隱藏" 的個股數: 後端返回的行數 vs 經過前端篩選後的行數.
  // 分組切換不計入篩選隱藏，只比較當前分組內的數據。
  const hiddenCount = Math.max(0, rowsInSelectedGroup.length - sortedRows.length)

  const renderStockCard = (r: any) => (
    <StockCard
      key={r.symbol}
      r={r}
      candleRows={klineData[r.symbol] ?? EMPTY_KLINE}
      showCandle={dailyKVisible}
      onPreview={handleCardPreview}
      onConfirmRemove={handleCardConfirmRemove}
      onCancelRemove={handleCardCancelRemove}
      onRequestRemove={handleCardRequestRemove}
      isConfirming={confirmRemove === r.symbol}
      extCols={visibleExtCols}
      expandedCells={expandedCells}
      onToggleExpand={handleToggleExpand}
      onDimensionClick={setDimensionTarget}
      isMonitored={monitoredSymbols.has(r.symbol)}
      groups={groups}
      onToggleMember={handleToggleMember}
      groupChangePending={addGroupMember.isPending || removeGroupMember.isPending}
    />
  )

  return (
    <div className="flex flex-col h-full">
      <PageHeader
        title="自選股"
        titleExtra={
          <span className="inline-flex items-center gap-1.5">
            {/* 計數膠囊: 顯示數/總數, mono 字體突出數字 */}
            <span className="inline-flex items-baseline gap-0.5 px-2 py-0.5 rounded-md bg-elevated/70 text-[11px]">
              <span className="font-mono font-semibold text-secondary tabular-nums">{sortedRows.length}</span>
              <span className="text-muted/50">/</span>
              <span className="font-mono text-muted tabular-nums">{rowsInSelectedGroup.length}</span>
              <span className="text-muted/60 ml-0.5">檔</span>
            </span>
            {/* 數據未就緒提示: 自選了但 enriched 緩存未覆蓋 (新股/冷門/新用戶未同步), 指標全為 null */}
            {pendingCount > 0 && (
              <span
                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[10px] font-medium bg-muted/15 text-muted border border-border/50 whitespace-nowrap"
                title={`目前有 ${pendingCount} 檔指標暫未就緒 (新股/冷門股或資料尚未同步),等待每日資料更新後自動補全`}
              >
                <Clock className="h-2.5 w-2.5" />
                待資料 {pendingCount}
              </span>
            )}
            {/* 過濾提示: 僅在有篩選隱藏時出現, 柔和橙色融入整體 */}
            {hiddenCount > 0 && (
              <span
                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[10px] font-medium bg-warning/12 text-warning/90 border border-warning/25 whitespace-nowrap"
                title={`目前有 ${hiddenCount} 檔被篩選條件隱藏,清除篩選可查看全部`}
              >
                <Filter className="h-2.5 w-2.5" />
                已篩選 {hiddenCount}
              </span>
            )}
          </span>
        }
        right={
          <div className="flex items-center gap-2">
            {/* 篩選 / 重置 / 搜索 */}
            <button
              onClick={() => setFilterOpen(v => !v)}
              className={`inline-flex items-center justify-center h-8 w-8 rounded-btn transition-colors duration-150 ease-smooth ${
                filterOpen || hasActiveFilters
                  ? 'bg-accent/15 text-accent hover:bg-accent/25'
                  : 'bg-elevated text-secondary hover:bg-elevated/80'
              }`}
              title={`篩選${activeFilterCount > 0 ? ` (${activeFilterCount})` : ''}`}
            >
              <Filter className="h-4 w-4" />
            </button>
            {hasActiveFilters && (
              <button
                onClick={resetAllFilters}
                className="inline-flex items-center justify-center h-8 w-8 rounded-btn bg-elevated text-secondary hover:bg-danger/10 hover:text-danger transition-colors duration-150 ease-smooth"
                title="重置全部篩選"
                aria-label="重置全部篩選"
              >
                <RotateCcw className="h-4 w-4" />
              </button>
            )}
            <StockSearchBox
              onPreview={(sym, name) => { setPreviewSymbol(sym); setPreviewName(name) }}
              existingBySymbol={groupBySymbol}
              groups={groups}
              onAdd={(symbol, groupId) => addMutation.mutate({ symbol, groupId })}
              onToggleMember={handleToggleMember}
              preferredGroupId={activeGroupId}
              addPending={addMutation.isPending}
              memberPending={addGroupMember.isPending || removeGroupMember.isPending}
            />
            <button
              onClick={() => {
                if (ocrAvailable === false) return
                setImportOpen(true)
              }}
              disabled={ocrAvailable === false}
              className="inline-flex items-center justify-center h-8 w-8 rounded-btn bg-elevated hover:bg-elevated/80 text-secondary hover:text-foreground transition-colors duration-150 ease-smooth disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-elevated disabled:hover:text-secondary"
              title={
                ocrAvailable === false
                  ? ocrInstallHint || getOcrInstallHint()
                  : '從截圖匯入自選'
              }
            >
              <ImagePlus className="h-4 w-4" />
            </button>
            <div className="w-px h-5 bg-border" />
            {/* 視圖 */}
            <button
              onClick={toggleView}
              className="inline-flex items-center justify-center h-8 w-8 rounded-btn bg-elevated hover:bg-elevated/80 text-secondary hover:text-foreground transition-colors duration-150 ease-smooth"
              title={viewMode === 'table' ? '卡片檢視' : '列表檢視'}
            >
              {viewMode === 'table' ? <LayoutGrid className="h-4 w-4" /> : <List className="h-4 w-4" />}
            </button>
            {/* 分組卡片視圖 */}
            <button
              onClick={toggleGroupView}
              aria-pressed={groupCardsOpen}
              className={`inline-flex items-center justify-center h-8 w-8 rounded-btn transition-colors duration-150 ease-smooth ${
                groupCardsOpen
                  ? 'bg-accent/15 text-accent hover:bg-accent/25'
                  : 'bg-elevated text-secondary hover:bg-elevated/80 hover:text-foreground'
              }`}
              title={groupCardsOpen ? '退出分組卡片' : '分組卡片檢視'}
              aria-label={groupCardsOpen ? '退出分組卡片' : '分組卡片檢視'}
            >
              <Rows3 className="h-4 w-4" />
            </button>
            {/* 分組統計條 */}
            <button
              onClick={toggleGroupStats}
              aria-pressed={groupStatsOpen}
              className={`inline-flex items-center justify-center h-8 w-8 rounded-btn transition-colors duration-150 ease-smooth ${
                groupStatsOpen
                  ? 'bg-accent/15 text-accent hover:bg-accent/25'
                  : 'bg-elevated text-secondary hover:bg-elevated/80 hover:text-foreground'
              }`}
              title={groupStatsOpen ? '收起分組統計' : '分組統計'}
              aria-label={groupStatsOpen ? '收起分組統計' : '分組統計'}
            >
              <BarChart3 className="h-4 w-4" />
            </button>
            <div className="w-px h-5 bg-border" />
            {/* 自定義列 / 刷新 */}
            <button
              onClick={() => setCustomizerOpen(true)}
              className="inline-flex items-center justify-center h-8 w-8 rounded-btn bg-elevated hover:bg-elevated/80 text-secondary hover:text-foreground transition-colors duration-150 ease-smooth"
              title="自訂欄位"
            >
              <Settings2 className="h-4 w-4" />
            </button>
            <button
              onClick={() => enriched.refetch()}
              disabled={enriched.isFetching}
              className="inline-flex items-center justify-center h-8 w-8 rounded-btn bg-elevated hover:bg-elevated/80 text-secondary hover:text-foreground transition-colors duration-150 ease-smooth disabled:opacity-50"
              title="重新整理"
            >
              <RefreshCw className={`h-4 w-4 ${enriched.isFetching ? 'animate-spin' : ''}`} />
            </button>
            {allSymbols.length > 0 && (
              <>
                <div className="w-px h-5 bg-border" />
                <button
                  onClick={() => setConfirmClear(true)}
                  className="inline-flex items-center justify-center h-8 w-8 rounded-btn bg-danger/10 text-danger hover:bg-danger/20 transition-colors duration-150 ease-smooth"
                  title="清空自選"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </>
            )}
            {/* 擴展插槽: 自選頁工具欄二開區 (無註冊時不渲染) */}
            <ExtensionSlot
              name="watchlist.toolbar"
              context={{
                symbols: sortedRows.map((row: any) => row.symbol),
                viewMode,
                selectedGroup,
                refresh: () => enriched.refetch(),
              }}
            />
          </div>
        }
      />

      {groupStatsOpen && (
        <WatchlistGroupStatsBar
          groups={groups}
          counts={groupCounts}
          pcts={groupPcts}
          selected={selectedGroup}
          onSelect={handleGroupSelect}
          config={groupStatsConfig}
          onConfigChange={updateGroupStatsConfig}
        />
      )}

      <WatchlistGroupBar
        groups={groups}
        counts={groupCounts}
        selected={selectedGroup}
        total={allSymbols.length}
        pcts={groupPcts}
        onSelect={handleGroupSelect}
        onCreate={(name, color) => createGroup.mutateAsync({ name, color }).then(() => undefined)}
        onRename={(groupId, name, color) => renameGroup.mutateAsync({ groupId, name, color }).then(() => undefined)}
        onDelete={groupId => deleteGroup.mutateAsync(groupId).then(() => undefined)}
        onClearGroup={groupId => clearGroup.mutateAsync(groupId).then(() => undefined)}
        onReorder={orderedIds => reorderGroup.mutateAsync(orderedIds).then(() => undefined)}
      />

      {/* Phase 8C-A: 選 2–5 檔直接比較 — 有勾選時才出現的 compact action bar */}
      {selectedForCompare.size > 0 && (
        <div className="flex items-center gap-2 px-5 py-1.5 border-b border-border bg-accent/5 text-xs">
          <span className="text-secondary">
            已選 <span className="font-mono font-semibold text-foreground">{selectedForCompare.size}</span> / {MAX_COMPARE_SYMBOLS} 檔
          </span>
          <button
            type="button"
            onClick={handleCompareSelected}
            disabled={selectedForCompare.size < MIN_COMPARE_SYMBOLS}
            title={selectedForCompare.size < MIN_COMPARE_SYMBOLS ? `至少選 ${MIN_COMPARE_SYMBOLS} 檔才能比較` : '比較已選股票'}
            className="inline-flex items-center gap-1.5 rounded-btn bg-accent px-2.5 py-1 text-white font-medium hover:bg-accent/90 transition-colors disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
          >
            <Scale className="h-3.5 w-3.5" />
            比較已選股票
          </button>
          {selectedForCompare.size < MIN_COMPARE_SYMBOLS && (
            <span className="text-muted">再選 {MIN_COMPARE_SYMBOLS - selectedForCompare.size} 檔即可比較</span>
          )}
          <button
            type="button"
            onClick={() => setSelectedForCompare(new Set())}
            className="ml-auto text-muted hover:text-foreground transition-colors cursor-pointer"
          >
            清除選取
          </button>
        </div>
      )}

      {/* 篩選欄 */}
      {filterOpen && (
        <div className="px-5 py-2 border-b border-border bg-surface/50 max-h-[184px] overflow-y-auto">
          {/* 板塊篩選 */}
          <div className="mb-2">
            <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">板塊</div>
            <div className="flex flex-wrap gap-1">
              {BOARDS.map(board => {
                const active = boardFilter.has(board)
                return (
                  <button
                    key={board}
                    onClick={() => toggleBoard(board)}
                    className={`px-2 py-0.5 rounded text-[11px] transition-colors ${
                      active
                        ? 'bg-accent/15 text-accent'
                        : 'bg-elevated text-secondary hover:text-foreground hover:bg-elevated/80'
                    }`}
                  >
                    {board}
                  </button>
                )
              })}
            </div>
          </div>
          {/* 排除 ST */}
          <div className="mb-2">
            <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">風險警示</div>
            <div className="flex flex-wrap gap-1">
              <button
                onClick={toggleExcludeST}
                className={`px-2 py-0.5 rounded text-[11px] transition-colors ${
                  excludeST
                    ? 'bg-accent/15 text-accent'
                    : 'bg-elevated text-secondary hover:text-foreground hover:bg-elevated/80'
                }`}
                title="勾選後隱藏簡稱含 ST 標記的標的 (ST/*ST/S*ST)"
              >
                排除ST
              </button>
            </div>
          </div>
          {COLUMN_GROUPS.map(cat => {
            const items = colsByCategory[cat.label]?.filter(i => i.col)
            if (!items?.length) return null
            return (
              <div key={cat.label} className="mb-1.5 last:mb-0">
                <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">{cat.label}</div>
                <div className="flex flex-wrap gap-x-2 gap-y-1">
                  {items.map(item => {
                    const f = filters[item.id] || {}
                    const hasFilter = !!f.min || !!f.max || !!f.text
                    return (
                      <div key={item.id} className="flex items-center gap-0.5 text-[11px]">
                        <span className={`whitespace-nowrap ${hasFilter ? 'text-accent' : 'text-secondary'}`}>{item.label}</span>
                        <input
                          type="number"
                          value={f.min ?? ''}
                          onChange={e => updateFilter(item.id, { min: e.target.value })}
                          placeholder="min"
                          className={`w-12 h-5 rounded border text-[10px] px-1 placeholder:text-muted focus:outline-none ${
                            hasFilter ? 'border-accent/30 bg-accent/5' : 'border-border bg-elevated'
                          } text-foreground focus:border-accent/50`}
                        />
                        <span className="text-muted">~</span>
                        <input
                          type="number"
                          value={f.max ?? ''}
                          onChange={e => updateFilter(item.id, { max: e.target.value })}
                          placeholder="max"
                          className={`w-12 h-5 rounded border text-[10px] px-1 placeholder:text-muted focus:outline-none ${
                            hasFilter ? 'border-accent/30 bg-accent/5' : 'border-border bg-elevated'
                          } text-foreground focus:border-accent/50`}
                        />
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })}
          {hasActiveFilters && (
            <button onClick={resetAllFilters} className="mt-1 text-[10px] text-danger hover:text-danger/80 transition-colors">
              重置全部篩選
            </button>
          )}
        </div>
      )}

      {/* 可滾動列表區 — 佔滿剩餘高度，內部獨立滾動，表頭 sticky 固定 */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        <div className="px-5 py-3">
          {/* 列表 */}
          {watchlistContentLoading ? (
            <div className="text-sm text-muted">載入中…</div>
          ) : list.isError ? (
            <div className="text-sm text-danger">讀取自選失敗</div>
          ) : enriched.isError ? (
            <div className="text-sm text-danger">讀取自選行情失敗</div>
          ) : allSymbols.length === 0 ? (
            <EmptyState
              icon={Star}
              title="自選股為空"
              hint="點擊右上角搜尋新增標的,或點擊圖片圖示從券商自選截圖批次匯入。"
            />
          ) : rowsInSelectedGroup.length === 0 ? (
            <EmptyState
              icon={FolderOpen}
              title="該分組尚無標的"
              hint="使用右上角搜尋新增,或透過股票旁的分組按鈕移入目前分組。"
            />
          ) : groupCardsOpen ? (
            <WatchlistGroupCards
              groups={groups}
              rows={rows}
              groupBySymbol={groupBySymbol}
              pcts={groupPcts}
              onPreview={handleCardPreview}
              onOpenGroup={handleGroupSelect}
              config={groupStatsConfig}
              onConfigChange={updateGroupStatsConfig}
            />
          ) : viewMode === 'table' ? (
            <StockDataTable
              columns={visibleColumns}
              rows={sortedRows}
              headerSticky
              sort={sort}
              onSortToggle={handleSortToggle}
              extraSortableKeys={INTRADAY_SORTABLE_KEYS}
              rowKey={(r: any) => r.symbol}
              rowClassName={() => 'border-t border-border hover:bg-elevated/50 transition-colors duration-150 ease-smooth'}
              // 日k列表頭：標籤 + 顯示/隱藏眼睛按鈕
              renderHeaderContent={(col) => {
                if (col.source.type === 'builtin' && col.source.key === 'candle') {
                  return (
                    <span className="inline-flex items-center justify-center gap-1.5">
                      <span className="shrink-0 whitespace-nowrap">{col.label}</span>
                      <button
                        type="button"
                        onClick={(event) => { event.stopPropagation(); toggleDailyKChart() }}
                        className={`inline-flex items-center justify-center w-5 h-5 rounded transition-colors ${
                          dailyKChartVisible
                            ? 'text-accent bg-accent/10 hover:bg-accent/20'
                            : 'text-muted hover:text-foreground hover:bg-elevated'
                        }`}
                        title={dailyKChartVisible ? '隱藏日K蠟燭' : '顯示日K蠟燭'}
                        aria-label={dailyKChartVisible ? '隱藏日K蠟燭' : '顯示日K蠟燭'}
                      >
                        {dailyKChartVisible ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}
                      </button>
                    </span>
                  )
                }
                if (col.source.type === 'builtin' && col.source.key === 'intraday') {
                  const intradayAutoRefresh = intradayRefreshEnabled && realtimeRunning
                  return (
                    <span className="inline-flex items-center justify-center gap-1.5">
                      <span className="shrink-0 whitespace-nowrap">{col.label}</span>
                      <button
                        type="button"
                        onClick={(event) => { event.stopPropagation(); toggleIntradayChart() }}
                        className={`inline-flex items-center justify-center w-5 h-5 rounded transition-colors ${
                          intradayChartVisible
                            ? 'text-accent bg-accent/10 hover:bg-accent/20'
                            : 'text-muted hover:text-foreground hover:bg-elevated'
                        }`}
                        title={intradayChartVisible ? '隱藏分時圖' : '顯示分時圖'}
                        aria-label={intradayChartVisible ? '隱藏分時圖' : '顯示分時圖'}
                      >
                        {intradayChartVisible ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}
                      </button>
                      {/* 分時圖顯示 且 未開自動輪詢時, 提供手動刷新按鈕 */}
                      {intradayChartVisible && !intradayAutoRefresh && (
                        <button
                          type="button"
                          onClick={(event) => { event.stopPropagation(); minuteBatch.refetch() }}
                          disabled={minuteBatch.isFetching}
                          className="inline-flex items-center justify-center w-5 h-5 rounded text-muted hover:text-accent hover:bg-accent/10 transition-colors disabled:opacity-40"
                          title="重新整理分時資料"
                          aria-label="重新整理分時資料"
                        >
                          <RefreshCw className={`h-3.5 w-3.5 ${minuteBatch.isFetching ? 'animate-spin' : ''}`} />
                        </button>
                      )}
                      {/* 自動輪詢中: 顯示旋轉圖標提示正在實時刷新 */}
                      {intradayChartVisible && intradayAutoRefresh && (
                        <RefreshCw className="h-3 w-3 text-accent/60 animate-spin" aria-label="即時重新整理中" />
                      )}
                    </span>
                  )
                }
                return undefined
              }}
              renderCell={(r: any, col: ColumnConfig) => {
                // ext 列
                if (col.source.type === 'ext') {
                  return renderExtCell(r, col, expandedCells, handleToggleExpand, setDimensionTarget)
                }
                const key = col.source.key
                const price = r.rt_price ?? r.close
                const pct = r.rt_pct ?? r.change_pct
                const name = r.rt_name ?? r.name
                // 自選頁 symbol 列：預覽 + 內嵌刪除（減號圖標，二次確認）
                if (key === 'symbol') {
                  const board = boardTag(r.symbol)
                  return (
                    <td className="px-1.5 py-1.5">
                      <div className="flex items-center gap-1 w-full">
                        {/* Phase 8C-A: 選 2–5 檔直接比較 — 勾選框, 與預覽按鈕互不干擾 */}
                        <input
                          type="checkbox"
                          checked={selectedForCompare.has(r.symbol)}
                          onChange={() => toggleCompareSelection(r.symbol)}
                          onClick={e => e.stopPropagation()}
                          disabled={!selectedForCompare.has(r.symbol) && selectedForCompare.size >= MAX_COMPARE_SYMBOLS}
                          title={selectedForCompare.has(r.symbol) ? '取消選取以比較' : `選取以比較（最多 ${MAX_COMPARE_SYMBOLS} 檔）`}
                          aria-label={`選取 ${r.symbol} 加入比較`}
                          className="shrink-0 h-3.5 w-3.5 rounded border-border accent-accent cursor-pointer disabled:cursor-not-allowed disabled:opacity-40"
                        />
                        <button
                          type="button"
                          onClick={() => { setPreviewSymbol(r.symbol); setPreviewName(name ?? '') }}
                          className="flex items-center gap-1 text-left min-w-0"
                        >
                          <span className="font-mono text-foreground text-xs group-hover:text-accent transition-colors duration-150">
                            {r.symbol}
                          </span>
                          {name && (
                            <span className="text-xs text-secondary truncate group-hover:text-foreground transition-colors duration-150">
                              {name}
                            </span>
                          )}
                          {board ? (
                            <span className={`shrink-0 inline-flex items-center justify-center w-[18px] h-[18px] rounded text-[9px] font-bold leading-none border ${board.color}`}>
                              {board.label}
                            </span>
                          ) : null}
                          {monitoredSymbols.has(r.symbol) && <span className="ml-2"><RealtimeDot /></span>}
                        </button>
                        {/* 刪除入口：從分組移除 + 從自選移除(二次確認) + 移到頂部 */}
                        <div className="ml-auto pl-1 shrink-0">
                          {confirmRemove === r.symbol ? (
                            <div className="flex items-center gap-1">
                              <button
                                onClick={() => { remove.mutate(r.symbol); setConfirmRemove(null) }}
                                className="px-1.5 py-0.5 rounded text-[10px] text-danger bg-danger/10 hover:bg-danger/20 transition-colors"
                              >
                                確認
                              </button>
                              <button
                                onClick={() => setConfirmRemove(null)}
                                className="p-0.5 text-muted hover:text-foreground transition-colors"
                              >
                                <X className="h-3 w-3" />
                              </button>
                            </div>
                          ) : (
                            <div className="flex items-center gap-1">
                              <WatchlistGroupPicker
                                groups={groups}
                                groupIds={r.group_ids ?? []}
                                symbol={r.symbol}
                                disabled={addGroupMember.isPending || removeGroupMember.isPending}
                                onToggleMember={handleToggleMember}
                              />
                              {selectedGroup !== 'all' && selectedGroup !== 'ungrouped' && r.group_ids?.includes(selectedGroup) && (
                                <button
                                  onClick={() => handleToggleMember(r.symbol, selectedGroup, false)}
                                  disabled={addGroupMember.isPending || removeGroupMember.isPending}
                                  className="p-0.5 text-muted hover:text-warning transition-colors duration-150 ease-smooth disabled:opacity-50"
                                  aria-label="移出目前分組"
                                  title="移出目前分組（仍保留在自選中）"
                                >
                                  <FolderMinus className="h-3.5 w-3.5" />
                                </button>
                              )}
                              <button
                                onClick={() => setConfirmRemove(r.symbol)}
                                className="p-0.5 text-muted hover:text-danger transition-colors duration-150 ease-smooth"
                                aria-label="移除"
                                title="從自選移除"
                              >
                                <Minus className="h-3.5 w-3.5" />
                              </button>
                              <button
                                onClick={() => moveToTop.mutate(r.symbol)}
                                disabled={moveToTop.isPending || allSymbols[0] === r.symbol}
                                className="p-0.5 text-muted hover:text-accent transition-colors duration-150 ease-smooth disabled:opacity-30 disabled:hover:text-muted"
                                aria-label="移到頂部"
                                title="移到頂部"
                              >
                                <ChevronsUp className="h-3.5 w-3.5" />
                              </button>
                            </div>
                          )}
                        </div>
                      </div>
                    </td>
                  )
                }
                // 實時行情列：price/pct/amount 使用 rt_ 回退（自選頁有實時推送）
                const numCls = 'px-2 py-1.5 text-right num tabular-nums'
                if (key === 'price') {
                  // Data Freshness & Source Labels batch: 現價旁補上精簡新鮮度徽章
                  // (即時/延遲/快照/過期), 只有 Taiwan symbol 才有 source_meta —
                  // legacy A 股 row 沒有這個欄位, DataQualityBadge 會直接不渲染。
                  return (
                    <td className={`${numCls} ${priceColorClass(pct)}`}>
                      <span className="inline-flex items-center justify-end gap-1 w-full">
                        {r.source_meta && <DataQualityBadge meta={r.source_meta} quoteTime={r.quote_time} />}
                        <span>{fmtPrice(price)}</span>
                      </span>
                    </td>
                  )
                }
                if (key === 'pct') {
                  return <td className={`${numCls} ${priceColorClass(pct)}`}>{fmtPct(pct)}</td>
                }
                if (key === 'amount') {
                  return <td className={`${numCls} text-secondary`}>{fmtBigNum(r.rt_amount ?? r.amount)}</td>
                }
                if (key === 'turnover') {
                  return <td className={`${numCls} ${turnoverColor(r.turnover_rate)}`}>{r.turnover_rate != null ? `${r.turnover_rate.toFixed(2)}%` : '—'}</td>
                }
                // 信號列
                if (key === 'signals') {
                  const signals = getSignals(r)
                  return (
                    <td className="px-2 py-1.5">
                      {signals.length > 0 && (
                        <div className="flex flex-wrap gap-0.5">
                          {signals.slice(0, 3).map((s) => (
                            <span key={s.label} className={`inline-block px-1.5 py-px rounded text-[10px] font-medium leading-tight ${signalCls(s.type)}`}>
                              {s.label}
                            </span>
                          ))}
                          {signals.length > 3 && (
                            <span className="text-[10px] text-muted">+{signals.length - 3}</span>
                          )}
                        </div>
                      )}
                    </td>
                  )
                }
                // 日k列
                if (key === 'candle') {
                  return (
                    <td
                      className="pl-2 pr-3 py-1.5"
                      style={{ width: candleSize.width + 4, minWidth: candleSize.width + 4, maxWidth: candleSize.width + 4, height: candleSize.height }}
                    >
                      <MiniCandlestick rows={klineData[r.symbol] ?? []} width={candleSize.width} height={candleSize.height} />
                    </td>
                  )
                }
                // 分時列
                if (key === 'intraday') {
                  // 指數無本地分鐘K數據, 分時列降級為佔位符
                  if (r.asset_type === 'index') {
                    const iw = intradayChartVisible ? intradayResolved.width : 40
                    const ih = intradayChartVisible ? intradayResolved.height : 40
                    return (
                      <td className="pl-3 pr-2 py-1.5 border-l border-border/30" style={{ width: iw + 4, minWidth: iw + 4, maxWidth: iw + 4, height: ih }}>
                        <div className="flex items-center justify-center">
                          <span className="text-[10px] text-muted">—</span>
                        </div>
                      </td>
                    )
                  }
                  const rows: MinuteKlineRow[] = minuteData[r.symbol] ?? []
                  // 眼睛關閉(收起)時用小尺寸 (和日k收起態一致 40x40); 開啟時用配置值
                  const iw = intradayChartVisible ? intradayResolved.width : 40
                  const ih = intradayChartVisible ? intradayResolved.height : 40
                  return (
                    <td className="pl-3 pr-2 py-1.5 border-l border-border/30" style={{ width: iw + 4, minWidth: iw + 4, maxWidth: iw + 4, height: ih }}>
                      <div className="flex items-center justify-center">
                        {intradayChartVisible
                          ? <MiniIntraday rows={rows} prevClose={r.prev_close} changePct={r.change_pct} width={iw - 4} height={ih} />
                          : <span className="text-[10px] text-muted">分時</span>}
                      </div>
                    </td>
                  )
                }
                // 其餘純數據列 → 共享原語
                return renderBuiltinDataCell(r, col)
              }}
              className="rounded-card overflow-x-auto"
            />
          ) : !virtualizeCards ? (
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-3">
              {sortedRows.map(renderStockCard)}
            </div>
          ) : (
            <div
              ref={cardGridRef}
              className="relative"
              style={{ height: cardRowVirtualizer.getTotalSize() }}
            >
              {cardRowVirtualizer.getVirtualItems().map(virtualRow => {
                const start = virtualRow.index * cardColumns
                const row = sortedRows.slice(start, start + cardColumns)
                return (
                  <div
                    key={virtualRow.key}
                    ref={cardRowVirtualizer.measureElement}
                    data-index={virtualRow.index}
                    className="absolute left-0 top-0 w-full grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-3"
                    style={{ transform: `translateY(${virtualRow.start - cardScrollMargin}px)` }}
                  >
                    {row.map(renderStockCard)}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      {/* 清空確認彈窗 */}
      <AnimatePresence>
        {confirmClear && (
          <div className="fixed inset-0 z-50 flex items-center justify-center">
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.15 }}
              className="absolute inset-0 bg-black/60 backdrop-blur-sm"
              onClick={() => setConfirmClear(false)}
            />
            <motion.div
              initial={{ opacity: 0, scale: 0.95, y: 12 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.97, y: 8 }}
              transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
              className="relative w-[90vw] max-w-[380px] rounded-card border border-border bg-base shadow-2xl p-6"
            >
              <h3 className="text-sm font-medium text-foreground mb-2">確認清空自選</h3>
              <p className="text-xs text-secondary mb-5">
                將移除全部 {allSymbols.length} 檔自選股,此操作無法復原。
              </p>
              <div className="flex items-center justify-end gap-2">
                <button
                  onClick={() => setConfirmClear(false)}
                  className="px-3 py-1.5 rounded-btn bg-elevated text-secondary hover:bg-elevated/80 text-sm transition-colors"
                >
                  取消
                </button>
                <button
                  onClick={() => clearAll.mutate()}
                  disabled={clearAll.isPending}
                  className="px-3 py-1.5 rounded-btn bg-danger/15 text-danger hover:bg-danger/25 text-sm font-medium transition-colors disabled:opacity-50"
                >
                  {clearAll.isPending ? '清除中…' : '確認清空'}
                </button>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      {/* 列自定義側欄 */}
      <ColumnCustomizer
        columns={columns}
        onChange={handleColumnsChange}
        open={customizerOpen}
        onClose={() => setCustomizerOpen(false)}
      />

      <StockPreviewDialog
        symbol={previewSymbol}
        name={previewName}
        onClose={closePreview}
      />

      <DimensionMembersDialog
        target={dimensionTarget}
        onClose={() => setDimensionTarget(null)}
        onStockClick={(symbol, name) => {
          setDimensionTarget(null)
          setPreviewSymbol(symbol)
          setPreviewName(name ?? '')
        }}
      />

      <WatchlistImportDialog
        open={importOpen}
        onClose={() => setImportOpen(false)}
        groupId={activeGroupId}
        groupName={activeGroup?.name}
        groupColor={activeGroup?.color}
      />
    </div>
  )
}

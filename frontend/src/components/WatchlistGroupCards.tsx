import React, { useCallback, useMemo, useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import type { WatchlistGroup } from '@/lib/api'
import { fmtPrice, fmtPct, priceColorClass } from '@/lib/format'
import {
  rowPct,
  groupPctColor,
  groupMetricTitle,
  groupMetricValue,
  sortGroupKeys,
  GROUP_METRICS,
  type GroupPctMap,
  type GroupPctInfo,
  type GroupStatsConfig,
  type GroupStatsConfigPatch,
  type GroupMetric,
} from '@/lib/watchlistGroupStats'
import {
  resolveWatchlistGroupColor,
  type WatchlistGroupColorOption,
} from '@/lib/watchlist-group-colors'
import { boardTag } from '@/components/stock-table/primitives'
import { GroupStatsSettings } from '@/components/GroupStatsSettings'

/**
 * 自選「分組卡片」視圖 — 每個分組一張卡, 組內按漲跌幅降序排列,
 * 默認展示前 N 名 (N 在設置彈層可調), 可展開查看全部。卡片自身順序、
 * 頭部數值與顯示條數跟隨「指標 + 排序」配置 (與分組統計條共享,
 * 由自選頁統一持有並持久化)。數據全部來自自選頁既有查詢
 * (enriched 行 + 分組歸屬), 不產生額外請求。
 */

interface GroupCardData {
  /** 'ungrouped' 或分組 id */
  key: string
  name: string
  color: WatchlistGroupColorOption | null
  /** 組內成員 (已按漲跌幅降序) */
  rows: any[]
}

const GroupCard = React.memo(function GroupCard({
  data,
  pctInfo,
  metric,
  topN,
  showColorBar,
  showRank,
  expanded,
  onToggle,
  onPreview,
  onOpen,
}: {
  data: GroupCardData
  pctInfo?: GroupPctInfo
  metric: GroupMetric
  /** 默認展示的成員條數 (來自持久化配置) */
  topN: number
  /** 頭部是否顯示分組顏色底條 (來自持久化配置) */
  showColorBar: boolean
  /** 成員行是否顯示序號 (來自持久化配置) */
  showRank: boolean
  expanded: boolean
  onToggle: (key: string) => void
  onPreview: (symbol: string, name: string) => void
  onOpen: (key: string) => void
}) {
  const visible = expanded ? data.rows : data.rows.slice(0, topN)
  const hasMore = data.rows.length > topN
  const color = data.color
  // 頭部數值跟隨所選指標; 上漲佔比以 0.5 為強弱軸染色
  const v = groupMetricValue(pctInfo, metric)
  const signed = metric === 'up_ratio' ? (v == null ? null : v - 0.5) : v
  const valueLabel = v == null
    ? '—'
    : metric === 'up_ratio'
      ? `${(v * 100).toFixed(0)}%`
      : fmtPct(v)

  return (
    <div className="flex flex-col self-start w-full overflow-hidden rounded-lg border border-border bg-surface">
      {/* 頭部: 色點 + 名稱 + 指標數值居左, 總數 + 漲跌家數居右; 點擊鑽取該分組 */}
      <button
        type="button"
        onClick={() => onOpen(data.key)}
        className={`group flex w-full items-center gap-1.5 border-b border-border/60 px-3 py-2 text-left transition-colors hover:bg-elevated/60 ${showColorBar && color ? color.background : ''}`}
        title={`查看「${data.name}」分組清單`}
      >
        <span className={`h-2 w-2 shrink-0 rounded-full ${color ? color.dot : 'bg-muted/60'}`} />
        <span className={`truncate text-xs font-medium ${color ? color.text : 'text-foreground'}`}>
          {data.name}
        </span>
        {pctInfo && pctInfo.sampled > 0 && (
          <span
            className={`shrink-0 font-mono text-xs font-semibold tabular-nums ${groupPctColor(signed)}`}
            title={groupMetricTitle(pctInfo, metric)}
          >
            {valueLabel}
          </span>
        )}
        <span className="ml-auto flex shrink-0 items-center gap-1.5" title={groupMetricTitle(pctInfo, metric)}>
          <span className="font-mono text-[10px] tabular-nums text-muted">{data.rows.length}</span>
          {pctInfo && pctInfo.sampled > 0 && (
            <span className="text-[10px] tabular-nums">
              <span className="text-bull">{pctInfo.up}漲</span>
              <span className="mx-0.5 text-muted/40">/</span>
              <span className="text-bear">{pctInfo.down}跌</span>
            </span>
          )}
        </span>
        <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted/60 transition-transform group-hover:translate-x-0.5" />
      </button>

      {/* 組內榜單: 按漲跌幅降序 */}
      {data.rows.length === 0 ? (
        <div className="px-3 py-4 text-center text-[11px] text-muted">尚無標的</div>
      ) : (
        <div className="flex flex-col">
          {visible.map((r: any, i: number) => {
            const pct = rowPct(r)
            const price = r.rt_price ?? r.close
            const cls = priceColorClass(pct)
            const board = boardTag(r.symbol)
            return (
              <button
                key={r.symbol}
                type="button"
                onClick={() => onPreview(r.symbol, r.rt_name ?? r.name ?? '')}
                className="flex w-full items-center gap-2 px-3 py-[5px] text-left transition-colors hover:bg-elevated/50"
                title={`${r.symbol} ${r.rt_name ?? r.name ?? ''}`}
              >
                {showRank && (
                  <span className="w-4 shrink-0 text-right font-mono text-[10px] leading-none tabular-nums text-muted/70">
                    {i + 1}
                  </span>
                )}
                <span className="shrink-0 font-mono text-xs text-foreground">{r.symbol}</span>
                <span className="flex min-w-0 flex-1 items-center gap-1">
                  <span className="min-w-0 truncate text-xs text-secondary">{r.rt_name ?? r.name}</span>
                  {board && (
                    <span className={`shrink-0 inline-flex items-center justify-center px-1 h-[16px] rounded text-[9px] font-bold leading-none ${board.color}`}>
                      {board.label}
                    </span>
                  )}
                </span>
                <span className={`shrink-0 font-mono text-xs tabular-nums ${cls}`}>{fmtPrice(price)}</span>
                <span className={`w-[52px] shrink-0 text-right font-mono text-xs font-medium tabular-nums ${cls}`}>
                  {fmtPct(pct)}
                </span>
              </button>
            )
          })}
        </div>
      )}

      {/* 展開/收起 */}
      {hasMore && (
        <button
          type="button"
          onClick={() => onToggle(data.key)}
          className="flex items-center justify-center gap-1 border-t border-border/60 px-3 py-1.5 text-[10px] text-muted transition-colors hover:bg-elevated/60 hover:text-foreground"
        >
          {expanded ? '收起' : `顯示全部 ${data.rows.length} 檔`}
          <ChevronDown className={`h-3 w-3 transition-transform ${expanded ? '' : 'rotate-180'}`} />
        </button>
      )}
    </div>
  )
})

interface WatchlistGroupCardsProps {
  groups: WatchlistGroup[]
  /** enriched 全量行 (未經過分組/板塊篩選) */
  rows: any[]
  /** symbol -> 所屬分組 id 列表 (空數組 = 未分組), 來自自選列表查詢 */
  groupBySymbol: Map<string, string[]>
  /** 分組等權漲跌幅統計 */
  pcts: GroupPctMap
  onPreview: (symbol: string, name: string) => void
  /** 鑽取分組 (切換到該分組的卡片列表) */
  onOpenGroup: (groupId: string) => void
  config: GroupStatsConfig
  onConfigChange: (patch: GroupStatsConfigPatch) => void
}

export function WatchlistGroupCards({
  groups,
  rows,
  groupBySymbol,
  pcts,
  onPreview,
  onOpenGroup,
  config,
  onConfigChange,
}: WatchlistGroupCardsProps) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())

  const toggleExpanded = useCallback((key: string) => {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])

  // 分桶 + 組內按漲跌幅降序: 僅在 enriched 行或分組歸屬變化時重算,
  // 每組數組引用穩定, 配合 GroupCard memo 避免無關卡片重渲染。
  const cards = useMemo<GroupCardData[]>(() => {
    const buckets = new Map<string, any[]>()
    for (const group of groups) buckets.set(group.id, [])
    buckets.set('ungrouped', [])
    for (const r of rows) {
      // 多組並存: 一股可同時出現在多個分組卡片中
      const gids = groupBySymbol.get(r.symbol)
      if (!gids || gids.length === 0) {
        buckets.get('ungrouped')?.push(r)
      } else {
        for (const gid of gids) buckets.get(gid)?.push(r)
      }
    }
    const sorted = new Map<string, any[]>()
    for (const [key, list] of buckets) {
      sorted.set(key, [...list].sort((a, b) => {
        const pa = rowPct(a)
        const pb = rowPct(b)
        if (pa == null && pb == null) return 0
        if (pa == null) return 1
        if (pb == null) return -1
        return pb - pa
      }))
    }
    const result: GroupCardData[] = groups.map(group => ({
      key: group.id,
      name: group.name,
      color: resolveWatchlistGroupColor(group.color),
      rows: sorted.get(group.id) ?? [],
    }))
    // 未分組僅在非空時展示
    const ungrouped = sorted.get('ungrouped') ?? []
    if (ungrouped.length > 0) {
      result.push({ key: 'ungrouped', name: '未分組', color: null, rows: ungrouped })
    }
    return result
  }, [groups, rows, groupBySymbol])

  // 卡片順序跟隨「指標 + 排序」配置 (與分組統計條同源)
  const ordered = useMemo(
    () => sortGroupKeys(cards, c => c.key, pcts, config),
    [cards, pcts, config],
  )

  if (cards.length === 0) return null

  const metricLabel = GROUP_METRICS.find(m => m.id === config.metric)?.label ?? ''

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <div className="text-[10px] uppercase tracking-wider text-muted">分組卡片 · {metricLabel}</div>
        <GroupStatsSettings config={config} onChange={onConfigChange} ariaLabel="分組卡片設定" showCardLimit />
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
        {ordered.map(card => (
          <GroupCard
            key={card.key}
            data={card}
            pctInfo={pcts[card.key]}
            metric={config.metric}
            topN={config.cardTopN}
            showColorBar={config.cardColorBar}
            showRank={config.cardRank}
            expanded={expanded.has(card.key)}
            onToggle={toggleExpanded}
            onPreview={onPreview}
            onOpen={onOpenGroup}
          />
        ))}
      </div>
    </div>
  )
}

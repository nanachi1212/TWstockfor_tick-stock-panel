import type { WatchlistGroup } from '@/lib/api'
import { fmtPct } from '@/lib/format'
import {
  groupPctColor,
  groupMetricTitle,
  groupMetricValue,
  GROUP_METRICS,
  sortGroupKeys,
  type GroupPctMap,
  type GroupStatsConfig,
  type GroupStatsConfigPatch,
} from '@/lib/watchlistGroupStats'
import { resolveWatchlistGroupColor } from '@/lib/watchlist-group-colors'
import { GroupStatsSettings } from '@/components/GroupStatsSettings'
import type { WatchlistGroupFilter } from '@/components/WatchlistGroups'

/**
 * 自選「分組統計條」— 頁面頂部的圖形化分組漲跌概覽。
 *
 * 每個分組一行: 中軸分叉條形圖直觀對比各組強弱 (紅=強 向右, 綠=弱 向左,
 * 長度按各組最大絕對值歸一化)。指標與排序可配置 (與分組卡片視圖共享,
 * 由自選頁統一持有並持久化):
 * - 指標: 等權平均 / 中位數 / 上漲佔比(50%強弱軸) / 組內最強 / 組內最弱
 * - 排序: 分組定義順序 / 按指標降序 / 升序
 * 數據來自自選頁既有的分組漲跌統計 (groupPcts), 零額外請求; 點擊行鑽取分組列表。
 */

interface Row {
  key: string
  name: string
  dot: string
  text: string
  count: number
  selected: boolean
}

export function WatchlistGroupStatsBar({
  groups,
  counts,
  pcts,
  selected,
  onSelect,
  config,
  onConfigChange,
}: {
  groups: WatchlistGroup[]
  counts: Record<string, number>
  pcts: GroupPctMap
  selected?: WatchlistGroupFilter
  onSelect: (group: WatchlistGroupFilter) => void
  config: GroupStatsConfig
  onConfigChange: (patch: GroupStatsConfigPatch) => void
}) {
  const rows: Row[] = groups.map(group => {
    const color = resolveWatchlistGroupColor(group.color)
    return {
      key: group.id,
      name: group.name,
      dot: color.dot,
      text: color.text,
      count: counts[group.id] ?? 0,
      selected: selected === group.id,
    }
  })
  if ((counts.ungrouped ?? 0) > 0) {
    rows.push({
      key: 'ungrouped',
      name: '未分組',
      dot: 'bg-muted/60',
      text: 'text-foreground',
      count: counts.ungrouped ?? 0,
      selected: selected === 'ungrouped',
    })
  }

  const metricLabel = GROUP_METRICS.find(m => m.id === config.metric)?.label ?? ''
  const valueOf = (key: string) => groupMetricValue(pcts[key], config.metric)

  if (rows.length === 0) return null

  const ordered = sortGroupKeys(rows, r => r.key, pcts, config)

  // 條長歸一化基準: 上漲佔比以 0.5 強弱軸的偏離量為幅值, 其餘指標取絕對值
  const magnitude = (v: number) => config.metric === 'up_ratio' ? Math.abs(v - 0.5) : Math.abs(v)
  const maxMag = Math.max(...ordered.reduce<number[]>((acc, r) => {
    const v = valueOf(r.key)
    if (v != null) acc.push(magnitude(v))
    return acc
  }, [0.0001]))

  return (
    <div className="border-b border-border bg-surface/40 px-5 py-2">
      <div className="mb-1 flex items-center justify-between">
        <div className="text-[10px] uppercase tracking-wider text-muted">分組漲跌 · {metricLabel}</div>
        <GroupStatsSettings config={config} onChange={onConfigChange} />
      </div>
      <div className="flex flex-col gap-px">
        {ordered.map(r => {
          const info = pcts[r.key]
          const v = valueOf(r.key)
          // 條形方向: 上漲佔比以 0.5 為軸, 其餘以 0 為軸; 幅值按組間最大值歸一化
          const signed = config.metric === 'up_ratio' ? (v == null ? null : v - 0.5) : v
          const half = v == null ? 0 : Math.min(50, (magnitude(v) / maxMag) * 50)
          const isUp = signed != null && signed > 0
          const isDown = signed != null && signed < 0
          const label = v == null
            ? '—'
            : config.metric === 'up_ratio'
              ? `${(v * 100).toFixed(0)}%`
              : fmtPct(v)
          return (
            <button
              key={r.key}
              type="button"
              onClick={() => onSelect(r.key)}
              title={groupMetricTitle(info, config.metric)}
              className={`grid grid-cols-[minmax(72px,auto)_1fr_auto_auto] items-center gap-2.5 rounded px-1.5 py-1 text-left transition-colors ${
                r.selected ? 'bg-accent/10' : 'hover:bg-elevated/50'
              }`}
            >
              <span className="flex min-w-0 items-center gap-1.5">
                <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${r.dot}`} />
                <span className={`truncate text-xs ${r.selected ? 'text-foreground' : r.text}`}>{r.name}</span>
                <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted">{r.count}</span>
              </span>
              <span className="relative h-3.5 rounded bg-elevated/40">
                <span className="absolute inset-y-0 left-1/2 w-px bg-border" />
                {isUp && (
                  <span
                    className="absolute inset-y-[3px] left-1/2 rounded-r bg-bull/75 transition-[width] duration-300"
                    style={{ width: `${half}%` }}
                  />
                )}
                {isDown && (
                  <span
                    className="absolute inset-y-[3px] right-1/2 rounded-l bg-bear/75 transition-[width] duration-300"
                    style={{ width: `${half}%` }}
                  />
                )}
              </span>
              <span className={`w-16 text-right font-mono text-xs font-semibold tabular-nums ${groupPctColor(signed)}`}>
                {label}
              </span>
              <span className="w-[72px] shrink-0 text-right text-[10px] tabular-nums">
                {info && info.sampled > 0 ? (
                  <>
                    <span className="text-bull">{info.up}漲</span>
                    <span className="mx-0.5 text-muted/40">/</span>
                    <span className="text-bear">{info.down}跌</span>
                  </>
                ) : (
                  <span className="text-muted">—</span>
                )}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
